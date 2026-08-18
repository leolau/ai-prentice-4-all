"""The score routes (design §8, §17 step 9b).

Behaviour contracts: ``score_user`` is human-only (a session-less caller
is a 403), 1–5, editable — re-scoring overwrites; ``score_self`` rides
with the retro, the run's own claim; the project's score is derived from
the last five ``score_user`` values; and the runs brief carries
``score_self`` only when the ≥2 divergence makes it a learning signal.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import projects_api, projects_db
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]

_PREFIX = "/api/registry/projects"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))

    state = {"actor": OWNER, "subject": ""}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set()

    async def _subject(request):
        return state["subject"]

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)
    monkeypatch.setattr(projects_api, "_interactive_subject", _subject)

    app = FastAPI()
    app.include_router(projects_api.router)
    return TestClient(app), state


def _project_with_runs(env, runs: int = 1) -> dict:
    client, _state = env
    resp = client.post(
        f"{_PREFIX}/",
        json={
            "goal": "Ship the Monday digest — to every subscriber",
            "description": "A weekly digest compiled and emailed each Monday.",
            "host_profile": "default",
            "outputs": [{"title": "The Monday digest email"}],
        },
    )
    assert resp.status_code == 200, resp.text
    project = resp.json()
    with projects_db.connect_closing() as conn:
        for _ in range(runs):
            projects_db.open_project_run(
                conn, project_id=project["id"], trigger="manual",
                profile="default",
            )
    return project


# ---------------------------------------------------------------------------
# POST /{slug}/runs/{n}/score — the human-only write
# ---------------------------------------------------------------------------


def test_score_without_a_session_is_403(env):
    client, state = env
    project = _project_with_runs(env)
    state["subject"] = ""  # an agent turn / service caller
    resp = client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/score", json={"score": 4}
    )
    assert resp.status_code == 403
    assert "human act" in resp.json()["detail"]


def test_score_happy_path_records_the_judgement(env):
    client, state = env
    project = _project_with_runs(env)
    state["subject"] = "leo"
    resp = client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/score",
        json={"score": 3, "note": "too formal"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "scored": 1, "score_user": 3, "score_note": "too formal", "by": "leo"
    }
    with projects_db.connect_closing() as conn:
        run = projects_db.get_project_run(conn, project["id"], 1)
    assert run["score_user"] == 3
    assert run["score_note"] == "too formal"
    assert run["scored_by"] == "leo"
    assert run["scored_at"] is not None


def test_score_is_editable_rescoring_overwrites(env):
    client, state = env
    project = _project_with_runs(env)
    state["subject"] = "leo"
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/score",
        json={"score": 2, "note": "first take"},
    ).status_code == 200
    resp = client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/score", json={"score": 4}
    )
    assert resp.status_code == 200
    assert resp.json()["score_user"] == 4
    with projects_db.connect_closing() as conn:
        run = projects_db.get_project_run(conn, project["id"], 1)
    assert run["score_user"] == 4
    assert run["score_note"] is None  # the note clears with the re-score


def test_score_validates_the_ladder(env):
    client, state = env
    project = _project_with_runs(env)
    state["subject"] = "leo"
    for bad in (0, 6, "many", None):
        resp = client.post(
            f"{_PREFIX}/{project['slug']}/runs/1/score", json={"score": bad}
        )
        assert resp.status_code == 422, (bad, resp.text)


def test_score_unknown_run_is_404(env):
    client, state = env
    project = _project_with_runs(env)
    state["subject"] = "leo"
    resp = client.post(
        f"{_PREFIX}/{project['slug']}/runs/99/score", json={"score": 3}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# score_self rides with the retro (§8.1)
# ---------------------------------------------------------------------------


def test_retro_carries_score_self(env):
    client, _state = env
    project = _project_with_runs(env)
    resp = client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/retro",
        json={"retro": "Compiled cleanly; the email went out late.",
              "score_self": 4},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["score_self"] == 4
    with projects_db.connect_closing() as conn:
        run = projects_db.get_project_run(conn, project["id"], 1)
    assert run["score_self"] == 4 and run["retro"].startswith("Compiled")


def test_retro_refuses_a_score_self_off_the_ladder(env):
    client, _state = env
    project = _project_with_runs(env)
    resp = client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/retro",
        json={"retro": "Some text.", "score_self": 7},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Derived score + the ≥2 divergence signal
# ---------------------------------------------------------------------------


def test_project_score_is_the_mean_of_the_last_five(env):
    client, state = env
    project = _project_with_runs(env, runs=2)
    state["subject"] = "leo"
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/score", json={"score": 5}
    ).status_code == 200
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/2/score", json={"score": 4}
    ).status_code == 200

    detail = client.get(f"{_PREFIX}/{project['slug']}").json()
    assert detail["score"] == {"mean": 4.5, "runs": 2}


def test_project_score_windows_scores_not_runs(env):
    """E5: the window is the last five *scores* — twenty-five unscored
    runs after them must not make the score disappear, and a re-score of
    an early run still moves it."""
    client, state = env
    project = _project_with_runs(env, runs=40)
    state["subject"] = "leo"
    for run_no, score in ((1, 2), (2, 4), (3, 4), (4, 4), (5, 5)):
        assert client.post(
            f"{_PREFIX}/{project['slug']}/runs/{run_no}/score",
            json={"score": score},
        ).status_code == 200

    detail = client.get(f"{_PREFIX}/{project['slug']}").json()
    # Runs 6–40 are unscored: under the old "scores within the last 25
    # runs" window this read was None.
    assert detail["score"] == {"mean": 3.8, "runs": 5}

    # Re-scoring run 1 still moves the window.
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/score", json={"score": 5}
    ).status_code == 200
    detail = client.get(f"{_PREFIX}/{project['slug']}").json()
    assert detail["score"]["runs"] == 5
    assert abs(detail["score"]["mean"] - 4.4) < 1e-9


def test_project_score_absent_until_somebody_scores(env):
    client, _state = env
    project = _project_with_runs(env)
    detail = client.get(f"{_PREFIX}/{project['slug']}").json()
    assert detail["score"] is None


def test_brief_carries_score_self_only_when_divergent(env):
    client, state = env
    project = _project_with_runs(env, runs=2)
    state["subject"] = "leo"
    # Run 1: the run claimed a 5, the human said 2 — a divergence of 3.
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/retro",
        json={"retro": "Went fine.", "score_self": 5},
    ).status_code == 200
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/1/score", json={"score": 2}
    ).status_code == 200
    # Run 2: self 4 vs user 3 — within 1, no signal.
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/2/retro",
        json={"retro": "Went fine too.", "score_self": 4},
    ).status_code == 200
    assert client.post(
        f"{_PREFIX}/{project['slug']}/runs/2/score", json={"score": 3}
    ).status_code == 200

    runs = client.get(f"{_PREFIX}/{project['slug']}").json()["runs"]
    by_no = {r["run_no"]: r for r in runs}
    assert by_no[1]["score_self"] == 5  # presence IS the ≥2 signal
    assert "score_self" not in by_no[2]
