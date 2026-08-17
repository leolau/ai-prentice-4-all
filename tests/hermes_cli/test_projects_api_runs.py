"""The Projects HTTP surface — runs, playbook, guidance (design §12,
API part 2 / §17 step 4).

Behaviour contracts:

- the playbook is proposed by anyone who may act (``member``+) but only
  a lead/admin may activate it — the crossing is what needs a human (§7.2);
- cycles and unknown assignees are refused loudly at save time (§7.1);
- guidance routes carry the one sentence the UI must never drop —
  *applies from the next run* (§5.1) — and the directive cap refuses
  with *retire one first* (§5.2);
- runs are manual-trigger through the API, 409 without an active
  playbook, cost reads fail-open (§6);
- ``PATCH /tools`` validates names at write time and shows the
  narrowing intersection (§4.1); ``PATCH /autonomy`` is a lead/admin
  route, never a judgement act (§4).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db, projects_api, projects_db, projects_run
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]
MEMBER_P = Principal(user_id="ada", display="Ada", role="member")  # type: ignore[arg-type]
VIEWER_P = Principal(user_id="vic", display="Vic", role="member")  # type: ignore[arg-type]
STRANGER = Principal(user_id="eve", display="Eve", role="member")  # type: ignore[arg-type]

STEPS = [
    {"key": "gather", "title": "Collect arrivals"},
    {"key": "approve", "title": "Owner reviews", "depends_on": ["gather"],
     "checkpoint": True},
    {"key": "send", "title": "Send to the list", "depends_on": ["approve"]},
]


class _FakeApprovalStore:
    """Records the ``NotificationStore.create`` kwargs (the H1 seam)."""

    def __init__(self):
        self.calls: list = []

    async def initialize(self):
        pass

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return object()


APPROVALS = _FakeApprovalStore()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    APPROVALS.calls.clear()
    monkeypatch.setattr(
        "hermes_cli.datastore.get_store", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        projects_run, "_approval_store",
        lambda app_store, *, config: APPROVALS,
    )
    # Deterministic host seams for the §4.1 intersection.
    monkeypatch.setattr(
        projects_run, "_enabled_toolsets_for_profile",
        lambda profile: ["research", "web"],
    )
    monkeypatch.setattr(
        projects_run, "_available_skill_names", lambda profile: ["digest"]
    )

    state = {"actor": OWNER, "enrolled": set(), "subject": OWNER.user_id}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set(state["enrolled"])

    async def _subject(request):
        # Playbook activation is a human act (§16); this file's scenarios
        # run under a verified session, the refusal on the main surface.
        return state["subject"]

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)
    monkeypatch.setattr(projects_api, "_interactive_subject", _subject)

    app = FastAPI()
    app.include_router(projects_api.router)
    return TestClient(app), state


def _active_project(env, **body_overrides) -> dict:
    """A fully-mandatory, ACTIVE project with the test membership matrix:
    leo=lead (owner), ada=member, vic=viewer."""
    client, _state = env
    payload = {
        "goal": "Ship the Monday digest — to every subscriber",
        "description": "A weekly digest compiled and emailed each Monday.",
        "host_profile": "default",
        "outputs": [{"title": "The Monday digest email"}],
    }
    payload.update(body_overrides)
    resp = client.post("/api/registry/projects/", json=payload)
    assert resp.status_code == 200, resp.text
    project = resp.json()
    pid = project["id"]
    with projects_db.connect_closing() as conn:
        projects_db.add_project_member(
            conn, project_id=pid, user_id="ada", role="member"
        )
        projects_db.add_project_member(
            conn, project_id=pid, user_id="vic", role="viewer"
        )
        projects_db.set_project_status(conn, pid, "active")
    resp = client.get(f"/api/registry/projects/{project['slug']}")
    return resp.json()


def _save_and_activate_playbook(env, project, steps=None):
    client, _state = env
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook",
        json={"body": "The weekly method", "steps": steps or STEPS},
    )
    assert resp.status_code == 200, resp.text
    rev = resp.json()["rev"]
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook/{rev}/activate",
        json={"note": "first method"},
    )
    assert resp.status_code == 200, resp.text
    return rev


# ---------------------------------------------------------------------------
# Playbook — propose vs activate (§7.2)
# ---------------------------------------------------------------------------


def test_member_may_propose_but_only_lead_activates(env):
    project = _active_project(env)
    client, state = env

    state["actor"] = MEMBER_P
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook", json={"steps": STEPS}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rev"] == 1 and body["active"] is False

    # A member may never cross the activation line.
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook/1/activate", json={}
    )
    assert resp.status_code == 403

    state["actor"] = OWNER
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook/1/activate",
        json={"note": "approved by leo"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is True

    # A viewer sees the method but changes nothing.
    state["actor"] = VIEWER_P
    resp = client.get(f"/api/registry/projects/{project['slug']}/playbook")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"]["rev"] == 1
    assert [r["rev"] for r in body["revisions"]] == [1]
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook", json={"steps": STEPS}
    )
    assert resp.status_code == 403


def test_playbook_cycle_refused_loudly_at_save(env):
    project = _active_project(env)
    client, _state = env
    cyclic = [
        {"key": "a", "title": "A", "depends_on": ["b"]},
        {"key": "b", "title": "B", "depends_on": ["a"]},
    ]
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook", json={"steps": cyclic}
    )
    assert resp.status_code == 422
    assert "a -> b -> a" in resp.json()["detail"]


def test_playbook_assignee_must_be_a_project_profile(env):
    project = _active_project(env)
    client, _state = env
    steps = [{"key": "a", "title": "A", "assignee": "ghost-profile"}]
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook", json={"steps": steps}
    )
    assert resp.status_code == 422
    assert "ghost-profile" in resp.json()["detail"]


def test_run_pins_its_rev_so_activation_mid_flight_is_safe(env):
    """Rev 2 activates while run 1 is mid-flight: the run keeps rev 1."""
    project = _active_project(env)
    client, state = env
    _save_and_activate_playbook(env, project)
    resp = client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    assert resp.status_code == 200, resp.text
    run1 = resp.json()["run"]
    assert run1["playbook_rev"] == 1 and run1["run_no"] == 1

    state["actor"] = MEMBER_P
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook",
        json={"steps": [{"key": "only", "title": "New method"}]},
    )
    rev2 = resp.json()["rev"]
    state["actor"] = OWNER
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/playbook/{rev2}/activate", json={}
    )
    assert resp.status_code == 200

    resp = client.get(f"/api/registry/projects/{project['slug']}/runs/1")
    assert resp.json()["playbook_rev"] == 1  # untouched by the activation
    # The NEXT run takes the new method.
    resp = client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    assert resp.json()["run"]["playbook_rev"] == 2


# ---------------------------------------------------------------------------
# Directives (§5)
# ---------------------------------------------------------------------------


def test_directive_flow_member_adds_viewer_cannot_retire_keeps_record(env):
    project = _active_project(env)
    client, state = env

    state["actor"] = MEMBER_P
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/directives",
        json={"kind": "directive", "body": "Always cc legal"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applies_from"] == "next run"  # §5.1 copy, always present
    did = body["id"]

    state["actor"] = VIEWER_P
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/directives",
        json={"body": "viewer trying"},
    )
    assert resp.status_code == 403
    resp = client.get(f"/api/registry/projects/{project['slug']}/directives")
    assert resp.status_code == 200
    assert resp.json()["applies_from"] == "next run"
    assert [d["body"] for d in resp.json()["directives"]] == ["Always cc legal"]

    state["actor"] = OWNER
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/directives/{did}/retire"
    )
    assert resp.status_code == 200
    # Retired: gone from the active list, surviving in the record (§5.2).
    resp = client.get(f"/api/registry/projects/{project['slug']}/directives")
    assert resp.json()["directives"] == []
    resp = client.get(
        f"/api/registry/projects/{project['slug']}/directives?include_retired=true"
    )
    rows = resp.json()["directives"]
    assert len(rows) == 1 and rows[0]["active"] == 0


def test_directive_cap_is_a_409_retire_one_first(env, monkeypatch):
    monkeypatch.setattr(
        projects_run, "projects_runtime_config",
        lambda: {
            "max_skills": 5, "guidance_max_directives": 2,
            "guidance_max_chars": 4000, "brief_max_chars": 1200,
        },
    )
    project = _active_project(env)
    client, _state = env
    for i in range(2):
        resp = client.post(
            f"/api/registry/projects/{project['slug']}/directives",
            json={"body": f"instruction {i}"},
        )
        assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/directives",
        json={"body": "one too many"},
    )
    assert resp.status_code == 409
    assert "retire one first" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Runs (§6)
# ---------------------------------------------------------------------------


def test_run_requires_an_active_playbook_then_spawns_cards(env):
    project = _active_project(env)
    client, _state = env
    resp = client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    assert resp.status_code == 409
    assert "no playbook" in resp.json()["detail"]

    _save_and_activate_playbook(env, project)
    resp = client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["run_no"] == 1
    assert body["run"]["trigger"] == "manual"
    assert body["run"]["triggered_by"] == "leo"
    assert set(body["cards"]) == {"gather", "approve", "send"}
    assert "## Project: Ship the Monday digest" in body["guidance"]

    resp = client.get(f"/api/registry/projects/{project['slug']}/runs")
    runs = resp.json()["runs"]
    assert len(runs) == 1 and runs[0]["run_no"] == 1
    assert "duration_seconds" in runs[0]

    # Detail: cards joined with live board state; cost fail-open (§6).
    resp = client.get(f"/api/registry/projects/{project['slug']}/runs/1")
    detail = resp.json()
    assert detail["cost_recorded"] is False and detail["cost"] is None
    assert {c["step_key"] for c in detail["cards"]} == {
        "gather", "approve", "send"
    }

    resp = client.get(f"/api/registry/projects/{project['slug']}/runs/99")
    assert resp.status_code == 404


def test_continue_releases_the_checkpoint_and_cancel_never_kills(env):
    project = _active_project(env)
    client, _state = env
    # Widen the cap: the default (1) is legitimately full once the first
    # step card is ready — the cap counts running + ready, and continue
    # must respect it like every other promotion step (§4).
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}",
        json={"max_in_progress": 3},
    )
    assert resp.status_code == 200, resp.text
    _save_and_activate_playbook(env, project)
    resp = client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    started = resp.json()
    send_id = started["cards"]["send"]

    # `send` succeeds the checkpoint: held in triage…
    resp = client.get(f"/api/registry/projects/{project['slug']}/runs/1")
    status_of = {c["task_id"]: c["status"] for c in resp.json()["cards"]}
    assert status_of[send_id] == "triage"

    # …until the human continues.
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/runs/1/continue", json={}
    )
    assert resp.status_code == 200, resp.text
    assert send_id in resp.json()["promoted"]

    # Cancel archives what has not started; a second cancel is refused.
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/runs/1/cancel", json={}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    with kanban_db.connect_closing() as bconn:
        assert kanban_db.get_task(bconn, send_id).status == "archived"
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/runs/1/cancel", json={}
    )
    assert resp.status_code == 409


def test_retro_writes_the_record(env):
    project = _active_project(env)
    client, _state = env
    _save_and_activate_playbook(env, project)
    client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/runs/1/retro",
        json={"retro": "Sources were slow; pre-fetch next time."},
    )
    assert resp.status_code == 200, resp.text
    resp = client.get(f"/api/registry/projects/{project['slug']}/runs/1")
    assert "pre-fetch" in resp.json()["retro"]
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/runs/1/retro", json={}
    )
    assert resp.status_code == 422


def test_a_viewer_reads_runs_but_cannot_start_one(env):
    project = _active_project(env)
    client, state = env
    _save_and_activate_playbook(env, project)
    client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    state["actor"] = VIEWER_P
    resp = client.get(f"/api/registry/projects/{project['slug']}/runs")
    assert resp.status_code == 200
    resp = client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    assert resp.status_code == 403
    state["actor"] = STRANGER
    resp = client.get(f"/api/registry/projects/{project['slug']}/runs")
    assert resp.status_code == 404  # reads by non-members stay invisible


# ---------------------------------------------------------------------------
# Instruments + autonomy (§4 / §4.1)
# ---------------------------------------------------------------------------


def test_tools_route_validates_names_and_shows_the_intersection(env):
    project = _active_project(env)
    client, state = env
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/tools",
        json={"toolsets": ["bogus-tool"]},
    )
    assert resp.status_code == 422
    assert "bogus-tool" in resp.json()["detail"]

    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/tools",
        json={"toolsets": ["web", "coding"], "skills": ["digest"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Stored as requested…
    assert body["toolsets"] == ["web", "coding"]
    # …but the effective set is the intersection — coding cannot be
    # granted on a host profile that does not enable it (§4.1).
    assert body["effective_toolsets"] == ["web"]
    assert body["dropped_toolsets"] == ["coding"]
    assert body["effective_skills"] == ["digest"]

    # Viewer cannot re-arm the project's instruments.
    state["actor"] = VIEWER_P
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/tools",
        json={"toolsets": ["web"]},
    )
    assert resp.status_code == 403


def test_tools_route_rejects_names_that_would_split_the_csv(env):
    """L1: both columns are stored comma-joined, so a name carrying a
    separator (or any other structural character) is refused at write
    time — before the unknown-name check — rather than silently round-
    tripping as two unknown names."""
    project = _active_project(env)
    client, _state = env
    for payload in (
        {"toolsets": ["web,coding"]},
        {"toolsets": ["web coding"]},
        {"skills": ["di,gest"]},
    ):
        resp = client.patch(
            f"/api/registry/projects/{project['slug']}/tools", json=payload
        )
        assert resp.status_code == 422, (payload, resp.text)
        detail = resp.json()["detail"]
        assert "invalid" in detail


def test_autonomy_is_a_lead_route_not_a_judgement_act(env):
    project = _active_project(env)
    client, state = env

    state["actor"] = MEMBER_P
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/autonomy",
        json={"autonomy": "autonomous"},
    )
    assert resp.status_code == 403  # members judge work, not the guardrails

    state["actor"] = OWNER
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/autonomy",
        json={"autonomy": "weird"},
    )
    assert resp.status_code == 422
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/autonomy",
        json={"autonomy": "manual"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["autonomy"] == "manual"

    # A manual project's run promotes nothing (§4).
    _save_and_activate_playbook(env, project)
    resp = client.post(f"/api/registry/projects/{project['slug']}/runs", json={})
    body = resp.json()
    assert body["promoted"] == []
    with kanban_db.connect_closing() as bconn:
        for tid in body["cards"].values():
            assert kanban_db.get_task(bconn, tid).status == "triage"
