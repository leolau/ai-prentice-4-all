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

from hermes_cli import (
    kanban_db,
    projects_api,
    projects_db,
    projects_run,
    projects_schedule,
)
from hermes_cli.access import Principal, private

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]
MEMBER_P = Principal(user_id="ada", display="Ada", role="member")  # type: ignore[arg-type]
VIEWER_P = Principal(user_id="vic", display="Vic", role="member")  # type: ignore[arg-type]
# Instance-"member" with a project-level lead membership (added in the
# U8 test) — the delete gate's "owner or lead" means the PROJECT role.
LEAD_P = Principal(user_id="lea", display="Lea", role="member")  # type: ignore[arg-type]


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


STEPS = [
    {"key": "gather", "title": "Collect arrivals"},
    {"key": "approve", "title": "Owner reviews", "depends_on": ["gather"],
     "checkpoint": True},
    {"key": "send", "title": "Send to the list", "depends_on": ["approve"]},
]


def _save_and_activate_playbook(env, project) -> int:
    client, _state = env
    resp = client.post(
        f"{PREFIX}/{project['slug']}/playbook",
        json={"body": "The weekly method", "steps": STEPS},
    )
    assert resp.status_code == 200, resp.text
    rev = resp.json()["rev"]
    resp = client.post(
        f"{PREFIX}/{project['slug']}/playbook/{rev}/activate",
        json={"note": "first method"},
    )
    assert resp.status_code == 200, resp.text
    return rev


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


# ---------------------------------------------------------------------------
# An archived project stays inert (§13: a shelved project does not run
# and does not learn)
# ---------------------------------------------------------------------------


def test_archived_project_refuses_every_mutating_route(env):
    project = _create_active_project(env)
    client, _state = env
    slug = project["slug"]

    # Seed a FINISHED run so the run-level writes have a real target:
    # archive may shelve a done run (step 1 only blocks open ones).
    _save_and_activate_playbook(env, project)
    resp = client.post(f"{PREFIX}/{slug}/runs", json={})
    assert resp.status_code == 200, resp.text
    run = resp.json()["run"]
    with projects_db.connect_closing() as conn:
        projects_db.update_project_run(conn, run["id"], status="done")
    _archive(env, project)
    baseline_cards = client.get(f"{PREFIX}/{slug}").json()["card_rollup"][
        "total_all_principals"
    ]

    # Every act that grows the record — the full enumeration, so route
    # 13 arriving ungated is what the table catches.
    attempts = [
        ("POST", f"{PREFIX}/{slug}/runs", {}),
        ("POST", f"{PREFIX}/{slug}/outputs/output-1/accept", {}),
        ("POST", f"{PREFIX}/{slug}/directives", {"body": "Keep emails short."}),
        ("PUT", f"{PREFIX}/{slug}/schedule", {"schedule": "0 9 * * 1"}),
        ("PATCH", f"{PREFIX}/{slug}/tools", {"toolsets": []}),
        ("POST", f"{PREFIX}/{slug}/runs/{run['run_no']}/continue", {}),
        ("POST", f"{PREFIX}/{slug}/runs/{run['run_no']}/retro",
         {"retro": "Late learning"}),
        ("POST", f"{PREFIX}/{slug}/runs/{run['run_no']}/score", {"score": 4}),
        ("POST", f"{PREFIX}/{slug}/cards", {"title": "One more thing"}),
        ("POST", f"{PREFIX}/{slug}/playbook", {"steps": STEPS}),
        ("POST", f"{PREFIX}/{slug}/playbook/1/activate", {}),
        ("POST", f"{PREFIX}/{slug}/outputs", {"title": "Late artefact"}),
        ("POST", f"{PREFIX}/{slug}/outputs/output-1/deliver", {}),
        ("PATCH", f"{PREFIX}/{slug}/outputs/output-1", {"status": "delivered"}),
        ("PATCH", f"{PREFIX}/{slug}/autonomy", {"autonomy": "supervised"}),
        ("POST", f"{PREFIX}/{slug}/summarise", {"summary": "Late summary"}),
        ("POST", f"{PREFIX}/{slug}/directives/d-1/activate", {}),
    ]
    for method, url, body in attempts:
        resp = client.request(method, url, json=body)
        assert resp.status_code == 409, (method, url, resp.text)
        detail = resp.json()["detail"]
        assert "archived" in detail and "restore" in detail

    # Nothing slipped through: no NEW run, no guidance, no schedule, no
    # card — and the seeded run's retro/score stayed untouched.
    detail = client.get(f"{PREFIX}/{slug}").json()
    assert len(detail["runs"]) == 1
    assert not detail.get("cron_job_id")
    assert detail["card_rollup"]["total_all_principals"] == baseline_cards
    seeded = client.get(f"{PREFIX}/{slug}/runs/{run['run_no']}").json()
    assert seeded["retro"] is None
    assert seeded["score_user"] is None
    directives = client.get(f"{PREFIX}/{slug}/directives").json()
    # The archive act's own record is the only guidance present.
    bodies = [d["body"] for d in directives["directives"]]
    assert "Keep emails short." not in bodies


def test_archived_project_still_accepts_a_patch_and_restore_unblocks(env):
    project = _create_active_project(env)
    _archive(env, project)
    client, _state = env
    slug = project["slug"]

    # Correcting a typo is not learning — it stays allowed while archived.
    resp = client.patch(f"{PREFIX}/{slug}", json={"goal": "Ship the digest, fixed."})
    assert resp.status_code == 200, resp.text

    # Restore is the unblocking act: the same write now lands.
    resp = client.post(f"{PREFIX}/{slug}/restore", json={})
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"{PREFIX}/{slug}/directives", json={"body": "Keep emails short."}
    )
    assert resp.status_code == 200, resp.text


def test_archived_chip_finds_legacy_rows_shelved_by_flag_alone(env):
    project = _create_active_project(env)
    # A legacy writer shelved the row by flag alone — no status change.
    with projects_db.connect_closing() as conn:
        with projects_db.write_txn(conn):
            conn.execute(
                "UPDATE projects SET archived = 1 WHERE id = ?", (project["id"],)
            )
    client, _state = env

    default = client.get(f"{PREFIX}?status=active").json()
    assert all(p["slug"] != project["slug"] for p in default["items"])
    shelved = client.get(
        f"{PREFIX}?archived=true&status=archived"
    ).json()
    assert any(p["slug"] == project["slug"] for p in shelved["items"])


def test_detail_rollup_counts_archived_cards_for_the_delete_gate(env):
    project = _create_active_project(env)
    client, _state = env
    resp = client.post(
        f"{PREFIX}/{project['slug']}/cards", json={"title": "Draft the summary"}
    )
    assert resp.status_code == 200, resp.text
    with kanban_db.connect_closing() as bconn:
        tasks = kanban_db.list_tasks(bconn, project_id=project["id"])
    assert len(tasks) == 1
    with kanban_db.connect_closing() as bconn:
        assert kanban_db.archive_task(bconn, tasks[0].id)

    detail = client.get(f"{PREFIX}/{project['slug']}").json()
    # The live count is zero…
    assert detail["card_rollup"]["total"] == 0
    # …but the delete gate must still see the archived card.
    assert detail["card_rollup"]["total_with_archived"] == 1


def test_archive_refuses_while_a_run_is_open(env):
    """U7 step 1: the shelf may not trap a resumable run — archive names
    the open run and refuses; cancel is the sanctioned way out."""
    project = _create_active_project(env)
    client, _state = env
    slug = project["slug"]
    _save_and_activate_playbook(env, project)
    resp = client.post(f"{PREFIX}/{slug}/runs", json={})
    assert resp.status_code == 200, resp.text
    run = resp.json()["run"]

    # The fresh run is held at its checkpoint — the shelf must refuse.
    resp = client.post(f"{PREFIX}/{slug}/archive", json={})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "open run" in detail and f"run {run['run_no']}" in detail

    # Cancel is the sanctioned way out…
    resp = client.post(f"{PREFIX}/{slug}/runs/{run['run_no']}/cancel", json={})
    assert resp.status_code == 200, resp.text
    # …and the shelf lands afterwards.
    archived = _archive(env, project)
    assert archived["archived"] is True


def test_cancel_still_works_on_an_archived_project(env):
    """Cancel *reduces* the record — the deliberately-open list keeps it
    working even on a shelved project."""
    project = _create_active_project(env)
    client, _state = env
    slug = project["slug"]
    _save_and_activate_playbook(env, project)
    resp = client.post(f"{PREFIX}/{slug}/runs", json={})
    assert resp.status_code == 200, resp.text
    run = resp.json()["run"]
    # `blocked` is a card-level stall with no resume affordance — archive
    # tolerates it…
    with projects_db.connect_closing() as conn:
        projects_db.update_project_run(conn, run["id"], status="blocked")
    _archive(env, project)
    # …and cancel still lands on top of the shelf.
    resp = client.post(f"{PREFIX}/{slug}/runs/{run['run_no']}/cancel", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


def test_delete_gate_counts_cards_it_cannot_see(env):
    """U8: the delete gate must agree with what the delete ROUTE counts —
    a principal-blind total, not the caller-visible one."""
    project = _create_active_project(env)
    client, _state = env
    slug = project["slug"]
    for title in ("Shared card", "Ada's private card"):
        resp = client.post(f"{PREFIX}/{slug}/cards", json={"title": title})
        assert resp.status_code == 200, resp.text
    # One card becomes private to ada — invisible to leo.
    with kanban_db.connect_closing() as bconn:
        tasks = kanban_db.list_tasks(bconn, project_id=project["id"])
        private_id = next(
            t.id for t in tasks if t.title == "Ada's private card"
        )
        with kanban_db.write_txn(bconn):
            bconn.execute(
                "UPDATE tasks SET visibility = ? WHERE id = ?",
                (private("ada"), private_id),
            )

    # A non-owner lead joins the project — the owner sees every card,
    # so the visibility gap only shows for a lesser principal.
    client, state = env
    with projects_db.connect_closing() as conn:
        projects_db.add_project_member(
            conn, project_id=project["id"], user_id="lea", role="lead"
        )
    state["actor"] = LEAD_P

    detail = client.get(f"{PREFIX}/{slug}").json()
    # The caller-visible total sees one…
    assert detail["card_rollup"]["total_with_archived"] == 1
    # …but the delete gate shows what the route counts: both.
    assert detail["card_rollup"]["total_all_principals"] == 2

    state["actor"] = OWNER
    _archive(env, project)
    state["actor"] = LEAD_P
    resp = client.request("DELETE", f"{PREFIX}/{slug}", params={"confirm": slug})
    assert resp.status_code == 409
    assert "2 cards" in resp.json()["detail"]


def test_archive_refuses_an_old_open_run_beyond_the_page_window(env):
    """U10: the gate must not page — a waiting run 1 buried under 51 newer
    finished runs still blocks the shelf (fails against the limit=50 scan)."""
    project = _create_active_project(env)
    client, _state = env
    slug = project["slug"]
    with projects_db.connect_closing() as conn:
        with projects_db.write_txn(conn):
            # Run 1 is stuck at a checkpoint…
            conn.execute(
                "INSERT INTO project_runs "
                "(id, project_id, run_no, trigger, triggered_by, profile, "
                " playbook_rev, status, started_at, trace_id) "
                "VALUES (?, ?, 1, 'manual', 'leo', 'default', 1, 'waiting', "
                " datetime('now'), NULL)",
                ("run_old", project["id"]),
            )
            # …under 51 finished runs — more than one page of the
            # newest-first listing.
            for n in range(2, 53):
                conn.execute(
                    "INSERT INTO project_runs "
                    "(id, project_id, run_no, trigger, triggered_by, profile, "
                    " playbook_rev, status, started_at, trace_id) "
                    "VALUES (?, ?, ?, 'manual', 'leo', 'default', 1, 'done', "
                    " datetime('now'), NULL)",
                    (f"run_{n}", project["id"], n),
                )

    resp = client.post(f"{PREFIX}/{slug}/archive", json={})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "open run" in detail and "run 1 (waiting)" in detail

    # Still not shelved.
    with projects_db.connect_closing() as conn:
        row = conn.execute(
            "SELECT archived FROM projects WHERE id = ?", (project["id"],)
        ).fetchone()
    assert row["archived"] == 0


def test_run_detail_flags_stalled_run_and_lists_blocked_tree(env):
    """A ``running`` run whose cards have no worker behind them is orphaned:
    the detail read says ``stalled`` and lists the blocked tasks from the
    run's dependency tree (with the failure that blocked them) so the run
    page can offer stop / retry / repeat instead of a lying pill."""
    project = _create_active_project(env)
    client, _state = env
    slug = project["slug"]

    with projects_db.connect_closing() as conn:
        run = projects_db.open_project_run(
            conn,
            project_id=project["id"],
            trigger="manual",
            triggered_by="leo",
            profile="default",
            playbook_rev=1,
        )
    with kanban_db.connect_closing() as bconn:
        # The decomposed subtask is a prerequisite of the run's card — the
        # card waits, todo, while the subtask blocks.
        sub = kanban_db.create_task(
            bconn,
            title="Extract objectives",
            project_id=project["id"],
            owner_user_id="leo",
            initial_status="running",
        )
        card = kanban_db.create_task(
            bconn,
            title="Draft the outline",
            project_id=project["id"],
            owner_user_id="leo",
            initial_status="running",
            parents=[sub],
        )
        assert kanban_db.block_task(bconn, sub, reason="worker died")
        with kanban_db.write_txn(bconn):
            bconn.execute(
                "UPDATE tasks SET last_failure_error = ? WHERE id = ?",
                ("worker exited cleanly (rc=0) — protocol violation", sub),
            )
            # The card waits on its blocked parent — todo, no worker.
            bconn.execute(
                "UPDATE tasks SET status = 'todo' WHERE id = ?", (card,)
            )
    with projects_db.connect_closing() as conn:
        projects_db.link_run_card(conn, run["id"], card, "draft-outline")

    resp = client.get(f"{PREFIX}/{slug}/runs/{run['run_no']}")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["stalled"] is True
    assert [t["task_id"] for t in payload["blocked_tasks"]] == [sub]
    assert "protocol violation" in payload["blocked_tasks"][0]["error"]
    # The run's own card stays a card — not duplicated as blocked work.
    assert [c["task_id"] for c in payload["cards"]] == [card]

    # A worker picking the card up makes the run honest again.
    with kanban_db.connect_closing() as bconn:
        with kanban_db.write_txn(bconn):
            bconn.execute(
                "UPDATE tasks SET status = 'running' WHERE id = ?", (card,)
            )
    payload = client.get(f"{PREFIX}/{slug}/runs/{run['run_no']}").json()
    assert payload["stalled"] is False


def test_run_detail_not_stalled_while_fresh_and_unblocked(env):
    """The stall flag must not cry wolf on a freshly opened run whose cards
    simply have not been claimed yet (the dispatcher needs a tick)."""
    project = _create_active_project(env)
    client, _state = env
    slug = project["slug"]

    with projects_db.connect_closing() as conn:
        run = projects_db.open_project_run(
            conn, project_id=project["id"], trigger="manual",
            triggered_by="leo", profile="default",
        )
    with kanban_db.connect_closing() as bconn:
        card = kanban_db.create_task(
            bconn,
            title="Draft the outline",
            project_id=project["id"],
            owner_user_id="leo",
            initial_status="running",
        )
        with kanban_db.write_txn(bconn):
            bconn.execute(
                "UPDATE tasks SET status = 'todo' WHERE id = ?", (card,)
            )
    with projects_db.connect_closing() as conn:
        projects_db.link_run_card(conn, run["id"], card, "draft-outline")

    payload = client.get(f"{PREFIX}/{slug}/runs/{run['run_no']}").json()
    assert payload["stalled"] is False
    assert payload["blocked_tasks"] == []
