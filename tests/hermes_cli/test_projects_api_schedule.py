"""The Projects HTTP surface — schedule, health, doctor (design §3.2,
§9.2, §12 / §17 step 5).

Behaviour contracts:

- ``PUT /schedule`` wires one ``hermes cron`` job in the host profile's
  store; §3.1 preconditions (no playbook, non-repeatable) are 409s that
  name what is missing, an invalid schedule string is a 422 (§3.1);
- ``DELETE /schedule`` removes the job and both halves of the link; a
  cadence change leaving ``repeatable`` pauses and detaches instead —
  never deletes silently — and records who changed it (§3.1);
- ``next_run_at`` rides on the detail read, refreshed from the cron
  store (§3.2 — a display cache, never a scheduling authority);
- health is the full §9.2 ladder computed on read — a broken cron link
  reads ``stalled``, because silence looks identical to success on a
  list page (§15 failure mode 1); ``GET /doctor`` names the break.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import projects_api, projects_db, projects_run, projects_schedule
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    cron_dir = tmp_path / "host-cron"
    monkeypatch.setattr(
        projects_schedule, "_host_profile_cron_dir", lambda profile: cron_dir
    )
    # Deterministic host seams for the §4.1 intersection (detail reads).
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
    return TestClient(app), state, cron_dir


def _active_project(env, **body_overrides) -> dict:
    """A fully-mandatory, ACTIVE project: leo=lead, ada=member, vic=viewer,
    host profile 'default'."""
    client, _state, _cron = env
    payload = {
        "goal": "Ship the Monday digest — to every subscriber",
        "description": "A weekly digest compiled and emailed each Monday.",
        "host_profile": "default",
        "outputs": [{"title": "The Monday digest email"}],
    }
    payload.update(body_overrides)
    resp = client.post("/api/registry/projects", json=payload)
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
    client, _state, _cron = env
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


def _jobs(cron_dir):
    path = cron_dir / "jobs.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["jobs"]


# ---------------------------------------------------------------------------
# PUT /schedule — the wiring (§3.2)
# ---------------------------------------------------------------------------


def test_put_schedule_wires_the_cron_job_and_detail_shows_next_run(env):
    project = _active_project(env, cadence="repeatable")
    _save_and_activate_playbook(env, project)
    client, _state, cron_dir = env

    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schedule"] == "every 60m"
    assert body["cron_job_id"]
    assert isinstance(body["next_run_at"], int)

    jobs = _jobs(cron_dir)
    assert len(jobs) == 1
    assert (
        jobs[0]["prompt"]
        == f"hermes projects run {project['slug']} --trigger schedule"
    )
    assert jobs[0]["origin"]["project_id"] == project["id"]

    # The detail read refreshes the display cache and shows full health.
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert detail["schedule"] == "every 60m"
    assert detail["next_run_at"] == body["next_run_at"]
    assert detail["health"] == "ok"
    assert detail["runs"] == []


def test_put_schedule_refuses_preconditions_with_409(env):
    client, _state, _cron = env
    # A schedule with no method is a timer that produces nothing (§3.1).
    project = _active_project(env, cadence="repeatable")
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 409
    assert "playbook" in resp.json()["detail"]

    # A one-off project never carries a schedule.
    one_off = _active_project(env, slug="one-off-lift")
    resp = client.put(
        f"/api/registry/projects/{one_off['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 409
    assert "repeatable" in resp.json()["detail"]


def test_put_schedule_invalid_string_is_422(env):
    project = _active_project(env, cadence="repeatable")
    _save_and_activate_playbook(env, project)
    client, _state, _cron = env
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "whenever it suits me"},
    )
    assert resp.status_code == 422
    # A one-shot on a repeatable project is refused too.
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "30m"},
    )
    assert resp.status_code == 422
    assert "recurring" in resp.json()["detail"]


def test_schedule_routes_are_lead_only(env):
    project = _active_project(env, cadence="repeatable")
    _save_and_activate_playbook(env, project)
    client, state, _cron = env
    for actor in (MEMBER_P, VIEWER_P):
        state["actor"] = actor
        resp = client.put(
            f"/api/registry/projects/{project['slug']}/schedule",
            json={"schedule": "every 60m"},
        )
        assert resp.status_code == 403
        resp = client.delete(f"/api/registry/projects/{project['slug']}/schedule")
        assert resp.status_code == 403
    state["actor"] = STRANGER
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 404


def test_delete_schedule_removes_job_and_link(env):
    project = _active_project(env, cadence="repeatable")
    _save_and_activate_playbook(env, project)
    client, _state, cron_dir = env
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 200

    resp = client.delete(f"/api/registry/projects/{project['slug']}/schedule")
    assert resp.status_code == 200
    assert resp.json() == {"scheduled": False, "removed": True}
    assert _jobs(cron_dir) == []
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert detail["schedule"] is None
    assert detail["next_run_at"] is None

    # Nothing left to remove.
    resp = client.delete(f"/api/registry/projects/{project['slug']}/schedule")
    assert resp.status_code == 200
    assert resp.json() == {"scheduled": False, "removed": False}


def test_cadence_change_pauses_and_detaches_never_deletes(env):
    """§3.1: repeatable → one_off pauses and detaches the cron job, keeps
    the schedule text, and records who changed it."""
    project = _active_project(env, cadence="repeatable")
    _save_and_activate_playbook(env, project)
    client, _state, cron_dir = env
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 200

    resp = client.patch(
        f"/api/registry/projects/{project['slug']}", json={"cadence": "one_off"}
    )
    assert resp.status_code == 200, resp.text

    jobs = _jobs(cron_dir)
    assert len(jobs) == 1  # paused, not deleted
    assert jobs[0]["enabled"] is False
    assert jobs[0]["state"] == "paused"

    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert detail["cadence"] == "one_off"
    assert detail["cron_job_id"] is None
    assert detail["schedule"] == "every 60m"  # one PUT away from re-scheduling
    with projects_db.connect_closing() as conn:
        directives = projects_db.list_project_directives(conn, project["id"])
    assert any(
        "paused and detached" in d["body"] and "leo" in d["author_user_id"]
        for d in directives
    )


# ---------------------------------------------------------------------------
# Health + doctor (§9.2, §15 failure mode 1)
# ---------------------------------------------------------------------------


def _break_the_cron_link(project):
    """Point ``cron_job_id`` at a job that does not exist — the silent
    automation the doctor exists to name."""
    with projects_db.connect_closing() as conn:
        projects_db.update_project_fields(
            conn, project["id"], {"cron_job_id": "ghost1234"}
        )


def test_broken_cron_link_reads_stalled_and_doctor_names_it(env):
    project = _active_project(env, cadence="repeatable")
    _save_and_activate_playbook(env, project)
    client, _state, _cron = env
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 200
    _break_the_cron_link(project)

    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert detail["health"] == "stalled"

    resp = client.get(f"/api/registry/projects/{project['slug']}/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["health"] == "stalled"
    assert body["clean"] is False
    assert {f["code"] for f in body["findings"]} == {"cron_job_missing"}

    # The box-wide doctor sees it too...
    resp = client.get("/api/registry/projects/doctor")
    assert resp.status_code == 200
    slugs = [item["slug"] for item in resp.json()["items"]]
    assert project["slug"] in slugs
    # ...filtered by slug...
    resp = client.get(
        f"/api/registry/projects/doctor?slug={project['slug']}"
    )
    assert [i["slug"] for i in resp.json()["items"]] == [project["slug"]]
    # ...but never to somebody without access.
    client_state = env[1]
    client_state["actor"] = STRANGER
    resp = client.get("/api/registry/projects/doctor")
    assert resp.json()["items"] == []


def test_doctor_is_clean_for_a_wired_project(env):
    project = _active_project(env, cadence="repeatable")
    _save_and_activate_playbook(env, project)
    client, _state, _cron = env
    resp = client.put(
        f"/api/registry/projects/{project['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 200
    resp = client.get(f"/api/registry/projects/{project['slug']}/doctor")
    body = resp.json()
    assert body["clean"] is True
    assert body["findings"] == []
    assert body["health"] == "ok"
    # A stranger's doctor visit is a 404, like every other read.
    env[1]["actor"] = STRANGER
    assert (
        client.get(f"/api/registry/projects/{project['slug']}/doctor").status_code
        == 404
    )


def test_list_supports_the_health_filter(env):
    healthy = _active_project(env, cadence="repeatable", slug="healthy-digest")
    _save_and_activate_playbook(env, healthy)
    client, _state, _cron = env
    resp = client.put(
        f"/api/registry/projects/{healthy['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 200

    broken = _active_project(env, cadence="repeatable", slug="broken-digest")
    _save_and_activate_playbook(env, broken)
    resp = client.put(
        f"/api/registry/projects/{broken['slug']}/schedule",
        json={"schedule": "every 60m"},
    )
    assert resp.status_code == 200
    _break_the_cron_link(broken)

    resp = client.get("/api/registry/projects?health=stalled")
    slugs = [item["slug"] for item in resp.json()["items"]]
    assert slugs == ["broken-digest"]

    resp = client.get("/api/registry/projects?health=ok")
    slugs = [item["slug"] for item in resp.json()["items"]]
    assert "healthy-digest" in slugs and "broken-digest" not in slugs

    # F2: ``stalled`` outranks ``attention`` (§9.2), so the chip named for
    # needing a human must surface it too — the server expands the filter.
    resp = client.get("/api/registry/projects?health=attention")
    slugs = [item["slug"] for item in resp.json()["items"]]
    assert slugs == ["broken-digest"]
