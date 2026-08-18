"""The retro learning write-back (design §8.2, §17 step 10).

Behaviour contracts: a retro may carry at most three concrete proposals,
one per §8.2 destination, and every crossing lands **inactive** — a
playbook revision a lead/admin activates, a directive any member
activates, and a skill candidate that records the project and run it
came from. Nothing on the learning path is automatic.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import projects_api, projects_db, projects_run
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]

_PREFIX = "/api/registry/projects"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))

    async def _resolve(request, *, allow_as=True):
        return OWNER

    async def _enrolled(user_id):
        return set()

    async def _subject(request):
        # The learning surface under test is crossed by a verified human
        # session; the session-less refusal is tested on the main surface.
        return OWNER.user_id

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)
    monkeypatch.setattr(projects_api, "_interactive_subject", _subject)

    app = FastAPI()
    app.include_router(projects_api.router)
    return TestClient(app)


def _project_with_run(client) -> dict:
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
        projects_db.open_project_run(
            conn, project_id=project["id"], trigger="manual", profile="default",
        )
    return project


def _retro(client, slug, proposals):
    return client.post(
        f"{_PREFIX}/{slug}/runs/1/retro",
        json={"retro": "Compiled and sent; the tone was too formal.",
              "proposals": proposals},
    )


# ---------------------------------------------------------------------------
# The three §8.2 destinations, all inactive
# ---------------------------------------------------------------------------


def test_proposals_materialize_inactive(env):
    project = _project_with_run(env)
    slug = project["slug"]

    resp = _retro(env, slug, [
        {"kind": "playbook", "body": "Draft, then read it aloud before sending."},
        {"kind": "directive", "body": "Never email before 9am"},
        {"kind": "skill", "name": "digest-tone", "body": "Keep the digest conversational."},
    ])
    assert resp.status_code == 200, resp.text
    landed = resp.json()["proposals"]
    assert [p["kind"] for p in landed] == ["playbook", "directive", "skill"]

    with projects_db.connect_closing() as conn:
        # The playbook proposal is a revision, inactive, naming the run.
        rev = projects_db.get_playbook(conn, project["id"], landed[0]["rev"])
        assert rev["active"] == 0
        assert rev["note"] == "proposed by run 1"
        # The directive proposal is not in the active set…
        assert all(d["body"] != "Never email before 9am"
                   for d in projects_db.list_project_directives(conn, project["id"]))
        # …but it is proposed.
        proposed = projects_db.list_proposed_directives(conn, project["id"])
        assert [d["body"] for d in proposed] == ["Never email before 9am"]
        # The skill candidate records its provenance — project and run.
        candidates = projects_db.list_skill_candidates(conn, project["id"])
        assert len(candidates) == 1
        assert candidates[0]["name"] == "digest-tone"
        assert candidates[0]["run_no"] == 1


def test_retro_without_proposals_still_writes(env):
    project = _project_with_run(env)
    resp = env.post(
        f"{_PREFIX}/{project['slug']}/runs/1/retro",
        json={"retro": "Compiled and sent."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["proposals"] == []


def test_directive_proposal_appears_on_the_list_route(env):
    project = _project_with_run(env)
    slug = project["slug"]
    assert _retro(env, slug, [
        {"kind": "directive", "body": "Keep subject lines under 40 chars"},
    ]).status_code == 200

    resp = env.get(f"{_PREFIX}/{slug}/directives")
    assert resp.status_code == 200
    body = resp.json()
    assert body["directives"] == []
    assert [d["body"] for d in body["proposed"]] == [
        "Keep subject lines under 40 chars"
    ]


# ---------------------------------------------------------------------------
# Validation — the cap is three, the kinds are fixed
# ---------------------------------------------------------------------------


def test_more_than_three_proposals_is_422(env):
    project = _project_with_run(env)
    bad = [{"kind": "directive", "body": f"rule {i}"} for i in range(4)]
    resp = _retro(env, project["slug"], bad)
    assert resp.status_code == 422
    assert "at most three" in resp.json()["detail"]


def test_unknown_proposal_kind_is_422(env):
    project = _project_with_run(env)
    resp = _retro(env, project["slug"], [{"kind": "memory", "body": "x"}])
    assert resp.status_code == 422
    assert "kind must be one of" in resp.json()["detail"]


def test_empty_proposal_body_is_422(env):
    project = _project_with_run(env)
    resp = _retro(env, project["slug"], [{"kind": "directive", "body": "  "}])
    assert resp.status_code == 422
    assert "body must not be empty" in resp.json()["detail"]


def test_proposals_must_be_a_list(env):
    project = _project_with_run(env)
    resp = env.post(
        f"{_PREFIX}/{project['slug']}/runs/1/retro",
        json={"retro": "done", "proposals": {"kind": "directive", "body": "x"}},
    )
    assert resp.status_code == 422
    assert "proposals must be a list" in resp.json()["detail"]


def test_playbook_proposal_with_a_bad_assignee_is_422(env):
    project = _project_with_run(env)
    resp = _retro(env, project["slug"], [{
        "kind": "playbook",
        "body": "New method",
        "steps": [{"key": "draft", "title": "Draft", "assignee": "nobody"}],
    }])
    assert resp.status_code == 422
    assert "assignee" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /{slug}/directives/{id}/activate — the member crossing
# ---------------------------------------------------------------------------


def _propose_directive(env, slug, body_text) -> str:
    resp = _retro(env, slug, [{"kind": "directive", "body": body_text}])
    assert resp.status_code == 200, resp.text
    landed = [p for p in resp.json()["proposals"] if p["kind"] == "directive"]
    return landed[0]["id"]


def test_activate_a_proposed_directive(env):
    project = _project_with_run(env)
    slug = project["slug"]
    did = _propose_directive(env, slug, "Never email before 9am")

    resp = env.post(f"{_PREFIX}/{slug}/directives/{did}/activate")
    assert resp.status_code == 200
    assert resp.json() == {
        "id": did, "active": True, "applies_from": "next run",
        "by": OWNER.user_id,
    }

    listing = env.get(f"{_PREFIX}/{slug}/directives").json()
    assert [d["body"] for d in listing["directives"]] == ["Never email before 9am"]
    assert listing["proposed"] == []


def test_activate_twice_or_unknown_is_404(env):
    project = _project_with_run(env)
    slug = project["slug"]
    did = _propose_directive(env, slug, "Never email before 9am")

    assert env.post(f"{_PREFIX}/{slug}/directives/{did}/activate").status_code == 200
    # Already active — nothing left to cross.
    assert env.post(f"{_PREFIX}/{slug}/directives/{did}/activate").status_code == 404
    assert env.post(f"{_PREFIX}/{slug}/directives/dir_missing/activate").status_code == 404


def test_activate_respects_the_active_cap(env, monkeypatch):
    orig = projects_run.projects_runtime_config
    monkeypatch.setattr(
        projects_run, "projects_runtime_config",
        lambda: {**orig(), "guidance_max_directives": 1},
    )
    project = _project_with_run(env)
    slug = project["slug"]

    # One active directive fills the cap…
    assert env.post(
        f"{_PREFIX}/{slug}/directives", json={"body": "Existing rule"},
    ).status_code == 200
    # …so the proposed one cannot cross.
    did = _propose_directive(env, slug, "One more rule")
    resp = env.post(f"{_PREFIX}/{slug}/directives/{did}/activate")
    assert resp.status_code == 409
    assert "retire one first" in resp.json()["detail"]
