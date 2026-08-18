"""The Projects schedule engine (design §3.2, §9.2, §15 / §17 step 5).

Behaviour contracts, not change detectors:

- a repeatable project's schedule IS a ``hermes cron`` job in the host
  profile's store — this module writes no scheduler of its own (§3.2);
- the wiring refuses loudly, naming what is missing: no active playbook,
  no host profile, a non-repeatable cadence, a one-shot schedule (§3.1);
- ``cron_job_id`` on the project and the job's origin metadata are two
  halves of one link; DELETE removes, a cadence change pauses and
  detaches — never deletes silently (§3.1);
- ``next_run_at`` is a display cache refreshed on read — the cron store
  stays authoritative (§3.2);
- health is derived on read: ``stalled`` (broken cron link, host row
  gone, two-period silence) outranks ``attention`` (§9.2), because the
  failure mode of an automated project is silence;
- ``doctor`` names every broken link (§15 failure mode 1).
"""

from __future__ import annotations

import json
import time

import pytest

from hermes_cli import kanban_db, projects_db, projects_schedule

NOW = 1_800_000_000  # fixed clock for the health ladder


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Isolated projects + kanban stores and a temp host-profile cron dir."""
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    cron_dir = tmp_path / "host-cron"
    monkeypatch.setattr(
        projects_schedule, "_host_profile_cron_dir", lambda profile: cron_dir
    )
    return tmp_path, cron_dir


def _repeatable_project(cadence="repeatable", board_slug=None, **updates):
    with projects_db.connect_closing() as conn:
        pid = projects_db.create_full_project(
            conn,
            goal="Ship the Monday digest — to every subscriber",
            description="A weekly digest compiled and emailed each Monday.",
            owner_user_id="leo",
            cadence=cadence,
            board_slug=board_slug,
        )
        projects_db.add_project_profile(
            conn, project_id=pid, profile="research", role="host"
        )
        projects_db.add_project_member(
            conn, project_id=pid, user_id="leo", role="lead"
        )
        projects_db.add_project_output(
            conn, project_id=pid, title="The Monday digest email", required=True
        )
        if updates:
            projects_db.update_project_fields(conn, pid, updates)
        projects_db.set_project_status(conn, pid, "active")
        return projects_db.get_project(conn, pid)


def _activate(pid):
    steps = [
        {"key": "gather", "title": "Collect arrivals", "assignee": "research"}
    ]
    with projects_db.connect_closing() as conn:
        rev = projects_db.save_playbook_rev(
            conn, project_id=pid, body="The weekly method", steps=steps,
            created_by="leo",
        )
        assert projects_db.activate_playbook_rev(conn, pid, rev)


def _profiles(pid):
    with projects_db.connect_closing() as conn:
        return projects_db.get_project_profiles(conn, pid)


def _set_schedule(project, schedule="every 60m"):
    with projects_db.connect_closing() as conn:
        return projects_schedule.set_project_schedule(
            conn, project=project, schedule=schedule,
            profiles=_profiles(project.id), changed_by="leo",
        )


def _jobs(cron_dir):
    path = cron_dir / "jobs.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["jobs"]


# ---------------------------------------------------------------------------
# Wiring — one cron job in the host profile's store (§3.2)
# ---------------------------------------------------------------------------


def test_set_schedule_creates_the_host_profiles_cron_job(stores):
    _tmp, cron_dir = stores
    project = _repeatable_project()
    _activate(project.id)

    result = _set_schedule(project)
    assert result["cron_job_id"]
    assert result["schedule"] == "every 60m"
    assert isinstance(result["next_run_at"], int)

    jobs = _jobs(cron_dir)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["id"] == result["cron_job_id"]
    # The job IS the project's run verb — this is what fires.
    assert job["prompt"] == f"hermes projects run {project.slug} --trigger schedule"
    assert job["origin"]["kind"] == "project"
    assert job["origin"]["project_id"] == project.id
    assert job["deliver"] == "local"

    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
    assert fresh.schedule == "every 60m"
    assert fresh.cron_job_id == result["cron_job_id"]
    assert fresh.next_run_at == result["next_run_at"]


def test_set_schedule_refusals_name_what_is_missing(stores):
    project = _repeatable_project()
    # No active playbook yet.
    with projects_db.connect_closing() as conn:
        with pytest.raises(
            projects_schedule.SchedulePreconditionError, match="playbook"
        ):
            projects_schedule.set_project_schedule(
                conn, project=project, schedule="every 60m",
                profiles=_profiles(project.id), changed_by="leo",
            )
    _activate(project.id)

    # A one-shot schedule on a repeatable project.
    with projects_db.connect_closing() as conn:
        with pytest.raises(ValueError, match="recurring"):
            projects_schedule.set_project_schedule(
                conn, project=project, schedule="30m",
                profiles=_profiles(project.id), changed_by="leo",
            )
        # An unparseable schedule.
        with pytest.raises(ValueError):
            projects_schedule.set_project_schedule(
                conn, project=project, schedule="whenever it suits me",
                profiles=_profiles(project.id), changed_by="leo",
            )

    # A non-repeatable cadence never carries a schedule.
    one_off = _repeatable_project(cadence="one_off")
    _activate(one_off.id)
    with projects_db.connect_closing() as conn:
        with pytest.raises(
            projects_schedule.SchedulePreconditionError, match="repeatable"
        ):
            projects_schedule.set_project_schedule(
                conn, project=one_off, schedule="every 60m",
                profiles=_profiles(one_off.id), changed_by="leo",
            )

    # No host profile row.
    with projects_db.connect_closing() as conn:
        with pytest.raises(
            projects_schedule.SchedulePreconditionError, match="host profile"
        ):
            projects_schedule.set_project_schedule(
                conn, project=project, schedule="every 60m",
                profiles=[], changed_by="leo",
            )


def test_update_reuses_the_existing_job(stores):
    _tmp, cron_dir = stores
    project = _repeatable_project()
    _activate(project.id)
    first = _set_schedule(project, "every 60m")
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
    second = _set_schedule(fresh, "every 30m")
    assert second["cron_job_id"] == first["cron_job_id"]
    jobs = _jobs(cron_dir)
    assert len(jobs) == 1
    assert jobs[0]["schedule"]["minutes"] == 30


def test_clear_removes_job_and_both_halves_of_the_link(stores):
    _tmp, cron_dir = stores
    project = _repeatable_project()
    _activate(project.id)
    _set_schedule(project)
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
        assert projects_schedule.clear_project_schedule(conn, project=fresh) is True
        assert _jobs(cron_dir) == []
        fresh = projects_db.get_project(conn, project.id)
        assert fresh.schedule is None
        assert fresh.cron_job_id is None
        assert fresh.next_run_at is None
        # Nothing left to remove.
        assert projects_schedule.clear_project_schedule(conn, project=fresh) is False


def test_detach_pauses_the_job_and_keeps_the_record(stores):
    """§3.1: cadence leaving repeatable pauses and detaches — never
    deletes — keeps the schedule text, and records who changed it."""
    _tmp, cron_dir = stores
    project = _repeatable_project()
    _activate(project.id)
    _set_schedule(project)
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
        projects_schedule.detach_project_schedule(
            conn, project=fresh,
            reason="cadence changed from repeatable to 'one_off'",
            changed_by="leo",
        )
    jobs = _jobs(cron_dir)
    assert len(jobs) == 1
    assert jobs[0]["enabled"] is False
    assert jobs[0]["state"] == "paused"
    assert "cadence changed" in (jobs[0]["paused_reason"] or "")
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
        assert fresh.cron_job_id is None
        assert fresh.schedule == "every 60m"  # kept: one PUT away
        directives = projects_db.list_project_directives(conn, project.id)
    assert any("paused and detached" in d["body"] for d in directives)


def test_refresh_next_run_is_a_display_cache(stores):
    project = _repeatable_project()
    _activate(project.id)
    result = _set_schedule(project)
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
        value = projects_schedule.refresh_next_run(conn, fresh)
    assert value == result["next_run_at"]

    # The job vanishes from the store → the cache clears (doctor names why).
    from cron import jobs as cron_jobs

    with projects_schedule._cron_in_profile("research"):
        assert cron_jobs.remove_job(result["cron_job_id"])
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
        assert projects_schedule.refresh_next_run(conn, fresh) is None
        assert projects_db.get_project(conn, project.id).next_run_at is None


# ---------------------------------------------------------------------------
# Health (§9.2) — stalled outranks attention
# ---------------------------------------------------------------------------

_ROLLUP = {"total": 3, "done": 1, "running": 1, "blocked": 0}


def _health(project, *, profiles=None, runs=None, cron_job=None,
            card_rollup=None, now=NOW):
    return projects_schedule.derive_health(
        project,
        card_rollup=card_rollup if card_rollup is not None else dict(_ROLLUP),
        profiles=profiles if profiles is not None else _profiles(project.id),
        runs=runs or [],
        cron_job=cron_job,
        now=now,
    )


def _run_row(*, started_at, status="done", outcome=None, score=None):
    return {
        "started_at": started_at, "status": status, "outcome": outcome,
        "score_user": score,
    }


def test_health_ok_when_the_link_round_trips(stores):
    project = _repeatable_project()
    _activate(project.id)
    result = _set_schedule(project)
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
    assert _health(
        fresh, cron_job={"id": result["cron_job_id"]},
        runs=[_run_row(started_at=NOW - 3600)],
    ) == "ok"


def test_health_stalled_when_the_cron_link_is_broken(stores):
    project = _repeatable_project(cron_job_id="ghost1234")
    assert _health(project, cron_job=None) == "stalled"


def test_health_stalled_when_the_host_row_left(stores):
    project = _repeatable_project()
    demoted = [{"profile": "research", "role": "member"}]
    assert _health(project, profiles=demoted, cron_job={"id": "x"}) == "stalled"


def test_health_stalled_after_two_silent_periods(stores):
    # 'every 60m' → period 3600s; four hours of silence is two periods.
    project = _repeatable_project(schedule="every 60m", cron_job_id="real")
    runs = [_run_row(started_at=NOW - 4 * 3600)]
    assert _health(project, runs=runs, cron_job={"id": "real"}) == "stalled"
    # One hour of silence is not.
    runs = [_run_row(started_at=NOW - 3600)]
    assert _health(project, runs=runs, cron_job={"id": "real"}) == "ok"


def test_health_stalled_when_a_wired_schedule_has_never_fired(stores):
    """M1: a repeatable project that has never run IS measurable — the
    silence counts from when the schedule was wired (the cron job's own
    creation). Two silent periods since then is ``stalled``; silence
    inside the window is still grace."""
    _tmp, cron_dir = stores
    project = _repeatable_project()
    _activate(project.id)
    result = _set_schedule(project)  # 'every 60m'
    # The cron store timestamps are ISO strings; health measures epochs.
    wired_at = projects_schedule._epoch_from_timestamp(
        _jobs(cron_dir)[0]["created_at"]
    )
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
    job = {"id": result["cron_job_id"], "created_at": wired_at}
    assert _health(
        fresh, cron_job=job, runs=[], now=wired_at + 3 * 3600
    ) == "stalled"
    assert _health(
        fresh, cron_job=job, runs=[], now=wired_at + 3600
    ) == "ok"


def test_health_attention_signals(stores):
    project = _repeatable_project(cron_job_id="real")
    # A freshly wired schedule under the fixed clock, so the never-run
    # anchor (M1) stays out of these attention assertions.
    job = {"id": "real", "created_at": NOW}
    blocked = dict(_ROLLUP, blocked=1)
    assert _health(project, card_rollup=blocked, cron_job=job) == "attention"
    waiting = [_run_row(started_at=NOW, status="waiting")]
    assert _health(project, runs=waiting, cron_job=job) == "attention"
    no_output = [_run_row(started_at=NOW, outcome="no_output")]
    assert _health(project, runs=no_output, cron_job=job) == "attention"
    low_score = [_run_row(started_at=NOW, score=2)]
    assert _health(project, runs=low_score, cron_job=job) == "attention"
    good = [_run_row(started_at=NOW, outcome="all delivered", score=4)]
    assert _health(project, runs=good, cron_job=job) == "ok"


def test_health_one_off_and_standing_overdue(stores):
    overdue = _repeatable_project(cadence="one_off", due_at=NOW - 86400)
    assert _health(overdue) == "attention"
    done = _repeatable_project(cadence="one_off", due_at=NOW - 86400)
    with projects_db.connect_closing() as conn:
        projects_db.set_project_status(conn, done.id, "done")
        done = projects_db.get_project(conn, done.id)
    assert _health(done) == "ok"

    standing = _repeatable_project(cadence="standing", review_every="30d")
    with projects_db.connect_closing() as conn:
        conn.execute(
            "UPDATE projects SET last_reviewed_at = ? WHERE id = ?",
            (NOW - 40 * 86400, standing.id),
        )
        standing = projects_db.get_project(conn, standing.id)
    assert _health(standing) == "attention"


# ---------------------------------------------------------------------------
# Doctor (§15 failure mode 1) — name the silence
# ---------------------------------------------------------------------------


def _findings(project, *, profiles=None, runs=None, cron_job=None):
    with projects_db.connect_closing() as conn:
        return projects_schedule.doctor_findings(
            conn, project,
            profiles=profiles if profiles is not None else _profiles(project.id),
            runs=runs or [],
            cron_job=cron_job,
            now=NOW,
        )


def test_doctor_clean_for_a_wired_project(stores):
    project = _repeatable_project()
    _activate(project.id)
    result = _set_schedule(project)
    with projects_db.connect_closing() as conn:
        fresh = projects_db.get_project(conn, project.id)
    assert _findings(fresh, cron_job={"id": result["cron_job_id"]},
                     runs=[_run_row(started_at=NOW - 3600)]) == []


def test_doctor_names_the_breaks(stores):
    project = _repeatable_project(cron_job_id="ghost1234")
    _activate(project.id)
    codes = {f["code"] for f in _findings(project, cron_job=None)}
    assert codes == {"cron_job_missing"}

    no_method = _repeatable_project()
    codes = {f["code"] for f in _findings(no_method, cron_job=None)}
    assert {"no_active_playbook", "no_schedule"} <= codes

    silent = _repeatable_project(schedule="every 60m", cron_job_id="real")
    _activate(silent.id)
    findings = _findings(
        silent, cron_job={"id": "real"},
        runs=[_run_row(started_at=NOW - 4 * 3600)],
    )
    assert {f["code"] for f in findings} == {"stalled_repeatable"}

    hostless = [{"profile": "research", "role": "member"}]
    codes = {
        f["code"] for f in _findings(project, profiles=hostless, cron_job=None)
    }
    assert "host_profile_missing" in codes


def test_doctor_cadence_overdues_and_broken_board(stores, monkeypatch):
    standing = _repeatable_project(cadence="standing", review_every="7d")
    with projects_db.connect_closing() as conn:
        conn.execute(
            "UPDATE projects SET last_reviewed_at = ? WHERE id = ?",
            (NOW - 8 * 86400, standing.id),
        )
        standing = projects_db.get_project(conn, standing.id)
    assert {f["code"] for f in _findings(standing)} == {"standing_overdue"}

    overdue = _repeatable_project(cadence="one_off", due_at=NOW - 86400)
    assert {f["code"] for f in _findings(overdue)} == {"one_off_overdue"}

    def _boom(board=None):
        raise RuntimeError("board store unreadable")

    monkeypatch.setattr(kanban_db, "connect_closing", _boom)
    boarded = _repeatable_project(board_slug="ghost-board")
    assert "board_missing" in {f["code"] for f in _findings(boarded)}


# ---------------------------------------------------------------------------
# Period estimation — sizes the staleness window, never load-bearing
# ---------------------------------------------------------------------------


def test_schedule_period_seconds(stores):
    assert projects_schedule.schedule_period_seconds("every 30m") == 1800
    assert projects_schedule.schedule_period_seconds("0 9 * * *") == 86400
    monthly = projects_schedule.schedule_period_seconds("0 9 1 * *")
    assert monthly > 86400  # the gap between monthly fires
    assert projects_schedule.schedule_period_seconds("garbage") == 7 * 86400
    assert projects_schedule.schedule_period_seconds(None) == 7 * 86400
