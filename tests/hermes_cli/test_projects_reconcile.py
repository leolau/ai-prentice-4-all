"""Projects run reconciliation (Gap A completion refill + Gap B sweep).

See ``plans/2026-09-13-project-run-resilience-plan.md`` for the incident and
design. Behaviour contracts, not change detectors:

- a card settling (done/blocked) tops up the owning run's promoted cards
  without a human calling `continue` (Gap A, covered in test_projects_run.py
  end-to-end through kanban_db.complete_task — this file covers the
  reconciler module directly);
- the periodic sweep (`reconcile_all_open_runs`) refills every open run's
  capacity the same way, independent of any single completion event firing
  — this is what catches a run orphaned by a process restart before any
  card ever settled;
- a run with nothing left to promote or in flight, stale past
  `projects.run_stall_seconds`, is failed loudly with an approval raised —
  never left silently claiming `running` forever;
- `run_last_activity_at` fails open to the run's own `started_at` when the
  board can't be read or the run has no linked cards.
"""

from __future__ import annotations

import pytest

from hermes_cli import kanban_db, projects_db, projects_reconcile, projects_run

GUIDE_CFG = {
    "max_skills": 5,
    "guidance_max_directives": 20,
    "guidance_max_chars": 4000,
    "brief_max_chars": 1200,
}


class _FakeApprovalStore:
    def __init__(self):
        self.calls: list = []

    async def initialize(self):
        pass

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return object()


APPROVALS = _FakeApprovalStore()


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Isolated projects + kanban stores with deterministic host seams —
    mirrors test_projects_run.py's fixture (not shared across files by
    convention in this test suite)."""
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
    monkeypatch.setattr(
        projects_run, "_enabled_toolsets_for_profile",
        lambda profile: ["research", "web"],
    )
    monkeypatch.setattr(
        projects_run, "_available_skill_names", lambda profile: ["digest", "email"]
    )
    monkeypatch.setattr(
        projects_run, "projects_runtime_config", lambda: dict(GUIDE_CFG)
    )
    return tmp_path


def _make_project(*, autonomy="supervised", max_in_progress=1):
    with projects_db.connect_closing() as conn:
        pid = projects_db.create_full_project(
            conn,
            goal="Ship the Monday digest — to every subscriber",
            description="A weekly digest compiled and emailed each Monday.",
            owner_user_id="leo",
            cadence="repeatable",
            autonomy=autonomy,
        )
        projects_db.add_project_profile(
            conn, project_id=pid, profile="default", role="host"
        )
        projects_db.add_project_member(
            conn, project_id=pid, user_id="leo", role="lead"
        )
        projects_db.add_project_output(
            conn, project_id=pid, title="The Monday digest email",
            spec="html email to the list", required=True,
        )
        if max_in_progress != 1:
            projects_db.update_project_fields(
                conn, pid, {"max_in_progress": max_in_progress}
            )
        projects_db.set_project_status(conn, pid, "active")
        project = projects_db.get_project(conn, pid)
    return project


def _save_playbook(pid, steps):
    with projects_db.connect_closing() as conn:
        return projects_db.save_playbook_rev(
            conn, project_id=pid, body="The weekly method", steps=steps,
            created_by="leo",
        )


def _start(pid):
    with projects_db.connect_closing() as conn:
        with kanban_db.connect_closing() as bconn:
            project = projects_db.get_project(conn, pid)
            return projects_run.start_run(
                conn, bconn, project=project, triggered_by="leo",
            )


# ---------------------------------------------------------------------------
# Gap B — the periodic sweep
# ---------------------------------------------------------------------------


def test_reconcile_refills_a_run_whose_completion_event_was_lost(stores):
    """Simulates exactly the restart-orphan scenario: a card finishes but
    the process dies before the settle hook (kanban_db's
    `_reconcile_project_run_capacity`) ever runs. The next sweep must
    still top up the run on its own — no completion event required."""
    project = _make_project(autonomy="autonomous", max_in_progress=1)
    steps = [{"key": f"s{i}", "title": f"Step {i}"} for i in range(3)]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    first_id = result["promoted"][0]
    remaining = [tid for tid in result["cards"].values() if tid != first_id]

    with kanban_db.connect_closing() as bconn:
        # Raw status flip — bypasses kanban_db.complete_task() entirely,
        # so no settle hook ever fires. This is the "lost event".
        bconn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (first_id,))

    results = projects_reconcile.reconcile_all_open_runs()
    assert any(r.get("action") == "refilled" for r in results)

    with kanban_db.connect_closing() as bconn:
        statuses = [kanban_db.get_task(bconn, tid).status for tid in remaining]
    assert sorted(statuses) == ["ready", "triage"]

    with projects_db.connect_closing() as conn:
        run = projects_db.get_project_run_by_id(conn, result["run"]["id"])
    assert run["status"] == "running"  # refill alone never closes a run


def test_reconcile_fails_a_stale_run_with_nothing_left_to_promote(stores):
    """`manual` autonomy never promotes anything on its own — the run sits
    with all cards in triage. Once it's stale past the threshold with
    nothing to refill and nothing in flight, the sweep must fail it
    loudly rather than leave it claiming 'running' forever."""
    project = _make_project(autonomy="manual", max_in_progress=1)
    steps = [{"key": "s0", "title": "Step 0"}]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    run = result["run"]
    future = int(run["started_at"]) + 3 * 3600  # past the 2h default threshold

    results = projects_reconcile.reconcile_all_open_runs(now=future)
    assert any(
        r.get("action") == "failed_stale" and r.get("run_no") == run["run_no"]
        for r in results
    )

    with projects_db.connect_closing() as conn:
        closed = projects_db.get_project_run_by_id(conn, run["id"])
    assert closed["status"] == "failed"
    assert closed["outcome"] == "stalled"
    assert closed["error"]
    assert APPROVALS.calls, "a stalled-run approval must be raised, not swallowed"


def test_reconcile_leaves_a_fresh_running_run_alone(stores):
    """Nothing stale, nothing new to refill (start_run already promoted
    everything room allowed) — the sweep must be a true no-op."""
    project = _make_project(autonomy="autonomous", max_in_progress=4)
    steps = [{"key": f"s{i}", "title": f"Step {i}"} for i in range(3)]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)

    results = projects_reconcile.reconcile_all_open_runs()
    assert not any(r.get("action") == "failed_stale" for r in results)

    with projects_db.connect_closing() as conn:
        run = projects_db.get_project_run_by_id(conn, result["run"]["id"])
    assert run["status"] == "running"


def test_reconcile_is_idempotent_against_an_already_closed_run(stores):
    """A run closed between the staleness read and the close write (e.g.
    another profile's gateway sweeping the same shared root store) must
    not be closed a second time — `_reconcile_one_run` re-reads fresh
    status right before writing."""
    project = _make_project(autonomy="manual", max_in_progress=1)
    steps = [{"key": "s0", "title": "Step 0"}]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    run = result["run"]

    with projects_db.connect_closing() as conn:
        projects_db.close_project_run(
            conn, run["id"], status="cancelled", outcome="cancelled by human"
        )

    future = int(run["started_at"]) + 3 * 3600
    results = projects_reconcile.reconcile_all_open_runs(now=future)
    # The run is no longer 'running' so list_project_runs' filter in
    # reconcile_all_open_runs already excludes it — nothing to do.
    assert not any(r.get("run_no") == run["run_no"] for r in results)

    with projects_db.connect_closing() as conn:
        still = projects_db.get_project_run_by_id(conn, run["id"])
    assert still["status"] == "cancelled"


# ---------------------------------------------------------------------------
# run_last_activity_at — the shared liveness read (doctor and the sweep
# must never disagree about what "stale" means)
# ---------------------------------------------------------------------------


def test_run_last_activity_at_falls_back_to_started_at_with_no_cards(stores):
    project = _make_project()
    with projects_db.connect_closing() as conn:
        run = {"id": "run_missing_entirely", "started_at": 1_700_000_000}
        assert (
            projects_reconcile.run_last_activity_at(conn, project, run)
            == 1_700_000_000
        )


def test_run_last_activity_at_reflects_real_card_completion(stores):
    project = _make_project(max_in_progress=4)
    steps = [{"key": "s0", "title": "Step 0"}]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    tid = result["cards"]["s0"]
    with kanban_db.connect_closing() as bconn:
        bconn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (tid,))
        assert kanban_db.complete_task(bconn, tid, result="done")

    with projects_db.connect_closing() as conn:
        run = projects_db.get_project_run_by_id(conn, result["run"]["id"])
        last = projects_reconcile.run_last_activity_at(conn, project, run)
    assert last >= int(run["started_at"])
