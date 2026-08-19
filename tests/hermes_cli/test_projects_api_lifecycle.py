"""The Projects HTTP surface — lifecycle: archive / restore / hard delete
(design §13, §16 "Lifecycle", decision 17).

Behaviour contracts:

- Archive sets ``archived=1`` **and** ``status='archived'`` in one call,
  detaches the schedule by the same call, records who did it, and answers
  with the updated row — never an ack. A ``needs_completion`` record is
  refused with the missing fields named (L2).
- Restore lands in ``paused`` — never ``active`` — and does not resurrect
  the schedule. An archived project is absent from the default list and
  present under ``archived=true``.
- Hard delete is the narrow exception: human-only, owner or lead,
  ``?confirm=<slug>``, and refused ``409`` naming what it found unless the
  project is already archived and carries no run, no delivered/accepted
  output and no card. A permitted delete leaves nothing behind.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db, projects_api, projects_db, projects_schedule
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]
MEMBER_P = Principal(user_id="ada", display="Ada", role="member")  # type: ignore[arg-type]
VIEWER_P = Principal(user_id="vic", display="Vic", role="member")  # type: ignore[arg-type]

PREFIX = "/api/registry/projects"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    cron_dir = tmp_path / "host-cron"
    monkeypatch.setattr(
        projects_schedule, "_host_profile_cron_dir", lambda profile: cron_dir
    )

    state = {"actor": OWNER, "enrolled": set(), "subject": OWNER.user_id}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set(state["enrolled"])

    async def _subject(request):
        # Delete is a human act (§8.1/§8.2); ``subject=None`` simulates the
        # session-less caller the gate must refuse.
        return state["subject"]

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)
    monkeypatch.setattr(projects_api, "_interactive_subject", _subject)

    app = FastAPI()
    app.include_router(projects_api.router)
    return TestClient(app), state


def _create_active_project(env, **body_overrides) -> dict:
    """A fully-mandatory ACTIVE project: leo=lead, ada=member, vic=viewer,
    host profile 'default'."""
    client, _state = env
    payload = {
        "goal": "Ship the Monday digest — to every subscriber",
        "description": "A weekly digest compiled and emailed each Monday.",
        "host_profile": "default",
        "outputs": [{"title": "The Monday digest email"}],
    }
    payload.update(body_overrides)
    resp = client.post(PREFIX, json=payload)
    assert resp.status_code == 200, resp.text
    project = resp.json()
    with projects_db.connect_closing() as conn:
        projects_db.add_project_member(
            conn, project_id=project["id"], user_id="ada", role="member"
        )
        projects_db.add_project_member(
            conn, project_id=project["id"], user_id="vic", role="viewer"
        )
        projects_db.set_project_status(conn, project["id"], "active")
    return client.get(f"{PREFIX}/{project['slug']}").json()


def _archive(env, project) -> dict:
    client, _state = env
    resp = client.post(f"{PREFIX}/{project['slug']}/archive", json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Archive (§13: the ordinary removal verb)
# ---------------------------------------------------------------------------


def test_archive_sets_both_flags_and_returns_the_row(env):
    project = _create_active_project(env)

    archived = _archive(env, project)
    # One call, both halves of the state, and the answer is the row itself.
    assert archived["slug"] == project["slug"]
    assert archived["archived"] is True
    assert archived["status"] == "archived"

    client, _state = env
    detail = client.get(f"{PREFIX}/{project['slug']}").json()
    assert detail["archived"] is True
    assert detail["status"] == "archived"


def test_archive_records_who_did_it(env):
    project = _create_active_project(env)
    client, _state = env

    resp = client.post(
        f"{PREFIX}/{project['slug']}/archive", json={"reason": "done for term"}
    )
    assert resp.status_code == 200, resp.text

    directives = client.get(f"{PREFIX}/{project['slug']}/directives").json()
    bodies = " ".join(d.get("body", "") for d in directives["directives"])
    assert "archived" in bodies.lower()
    assert "done for term" in bodies


def test_archive_detaches_the_schedule(env):
    project = _create_active_project(env)
    # A live schedule on the record (the cron store itself is irrelevant —
    # the invariant is the project row).
    with projects_db.connect_closing() as conn:
        with projects_db.write_txn(conn):
            conn.execute(
                "UPDATE projects SET cron_job_id = 'job-x', "
                "cadence = 'repeatable' WHERE id = ?",
                (project["id"],),
            )

    archived = _archive(env, project)
    # No archived project keeps a live cron pointer.
    assert archived["cron_job_id"] is None
    with projects_db.connect_closing() as conn:
        row = conn.execute(
            "SELECT cron_job_id, next_run_at FROM projects WHERE id = ?",
            (project["id"],),
        ).fetchone()
    assert row["cron_job_id"] is None
    assert row["next_run_at"] is None


def test_archive_refuses_a_needs_completion_record(env):
    project = _create_active_project(env)
    # The shape a legacy import lands in (L2).
    with projects_db.connect_closing() as conn:
        with projects_db.write_txn(conn):
            conn.execute(
                "UPDATE projects SET status = 'needs_completion', goal = NULL "
                "WHERE id = ?",
                (project["id"],),
            )

    client, _state = env
    resp = client.post(f"{PREFIX}/{project['slug']}/archive", json={})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "needs completion" in detail
    assert "a goal" in detail

    # Nothing moved.
    with projects_db.connect_closing() as conn:
        row = conn.execute(
            "SELECT archived, status FROM projects WHERE id = ?",
            (project["id"],),
        ).fetchone()
    assert row["archived"] == 0
    assert row["status"] == "needs_completion"


def test_archive_is_a_lead_write(env):
    project = _create_active_project(env)
    client, state = env

    state["actor"] = VIEWER_P
    assert client.post(f"{PREFIX}/{project['slug']}/archive").status_code == 403
    state["actor"] = MEMBER_P
    assert client.post(f"{PREFIX}/{project['slug']}/archive").status_code == 403

    state["actor"] = OWNER
    assert client.post(f"{PREFIX}/{project['slug']}/archive").status_code == 200
    # Archiving twice is a conflict, not a silent no-op.
    resp = client.post(f"{PREFIX}/{project['slug']}/archive")
    assert resp.status_code == 409
    assert "already archived" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Restore (§13: re-entry is a decision)
# ---------------------------------------------------------------------------


def test_restore_lands_paused_never_active(env):
    project = _create_active_project(env)
    _archive(env, project)
    client, _state = env

    restored = client.post(f"{PREFIX}/{project['slug']}/restore").json()
    assert restored["archived"] is False
    assert restored["status"] == "paused"
    # The schedule is not resurrected by the same call.
    assert restored["cron_job_id"] is None


def test_restore_refuses_a_live_project(env):
    project = _create_active_project(env)
    client, _state = env
    resp = client.post(f"{PREFIX}/{project['slug']}/restore")
    assert resp.status_code == 409
    assert "not archived" in resp.json()["detail"]


def test_archived_project_list_membership(env):
    project = _create_active_project(env)
    _archive(env, project)
    client, _state = env

    default = client.get(f"{PREFIX}?status=active").json()
    assert all(p["slug"] != project["slug"] for p in default["items"])

    shelved = client.get(f"{PREFIX}?archived=true").json()
    assert any(p["slug"] == project["slug"] for p in shelved["items"])


# ---------------------------------------------------------------------------
# Hard delete (decision 17: the narrow exception)
# ---------------------------------------------------------------------------


def test_delete_refuses_when_not_archived(env):
    project = _create_active_project(env)
    client, _state = env
    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": project["slug"]}
    )
    assert resp.status_code == 409
    assert "not archived" in resp.json()["detail"]


def test_delete_refuses_a_project_with_runs(env):
    project = _create_active_project(env)
    _archive(env, project)
    with projects_db.connect_closing() as conn:
        with projects_db.write_txn(conn):
            conn.execute(
                "INSERT INTO project_runs "
                "(id, project_id, run_no, trigger, profile, status, started_at) "
                "VALUES ('run-1', ?, 1, 'manual', 'default', 'done', 1700000000)",
                (project["id"],),
            )
    client, _state = env
    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": project["slug"]}
    )
    assert resp.status_code == 409
    assert "1 run" in resp.json()["detail"]


def test_delete_refuses_delivered_or_accepted_outputs(env):
    project = _create_active_project(env)
    _archive(env, project)
    with projects_db.connect_closing() as conn:
        with projects_db.write_txn(conn):
            conn.execute(
                "UPDATE project_outputs SET status = 'delivered', "
                "delivered_at = 1700000000 WHERE project_id = ?",
                (project["id"],),
            )
    client, _state = env
    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": project["slug"]}
    )
    assert resp.status_code == 409
    assert "output" in resp.json()["detail"]


def test_delete_refuses_a_project_with_cards(env):
    project = _create_active_project(env)
    client, _state = env
    resp = client.post(
        f"{PREFIX}/{project['slug']}/cards", json={"title": "Draft the summary"}
    )
    assert resp.status_code == 200, resp.text
    _archive(env, project)

    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": project["slug"]}
    )
    assert resp.status_code == 409
    assert "1 card" in resp.json()["detail"]
    # The card survives the refusal — the board is somebody's work.
    with kanban_db.connect_closing() as bconn:
        tasks = kanban_db.list_tasks(bconn, project_id=project["id"])
    assert len(tasks) == 1


def test_delete_needs_the_typed_slug(env):
    project = _create_active_project(env)
    _archive(env, project)
    client, _state = env
    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": "something-else"}
    )
    assert resp.status_code == 422
    assert "confirm" in resp.json()["detail"]


def test_delete_is_a_human_act(env):
    project = _create_active_project(env)
    _archive(env, project)
    client, state = env

    state["subject"] = None  # session-less / agent caller
    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": project["slug"]}
    )
    assert resp.status_code == 403
    assert "human act" in resp.json()["detail"]

    # A member who is not a lead never reaches the human gate.
    state["subject"] = "ada"
    state["actor"] = MEMBER_P
    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": project["slug"]}
    )
    assert resp.status_code == 403


def test_delete_leaves_nothing_behind(env):
    project = _create_active_project(env)
    with projects_db.connect_closing() as conn:
        projects_db.set_active(conn, project["id"])
        assert projects_db.get_active_id(conn) == project["id"]
    _archive(env, project)

    client, _state = env
    resp = client.request(
        "DELETE", f"{PREFIX}/{project['slug']}", params={"confirm": project["slug"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": project["slug"]}

    # The record is gone for everyone…
    assert client.get(f"{PREFIX}/{project['slug']}").status_code == 404
    with projects_db.connect_closing() as conn:
        # …the active pointer no longer names it…
        assert projects_db.get_active_id(conn) != project["id"]
        # …and the cascade stopped at the projects store.
        assert not conn.execute(
            "SELECT 1 FROM project_outputs WHERE project_id = ?",
            (project["id"],),
        ).fetchone()
