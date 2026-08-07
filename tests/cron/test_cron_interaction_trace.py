"""FG-16/C8: a scheduled run is one traced interaction.

Cron drives the same agent and tools as a chat turn but has no inbound channel
message, so nothing upstream mints a trace for it — calendar/reminder jobs were
invisible in the Activity ledger. These tests pin the helper contract: mint +
bind under the enrolled owner, emit the job's events onto one ``trace_id``,
flush from the (loop-less) scheduler worker thread, unbind, and fail open when
the application datastore isn't there.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cron import scheduler
from hermes_cli import access, datastore, interactions
from hermes_cli.interactions import InteractionTrace, current_trace


@pytest.fixture
def traced(monkeypatch):
    state: dict = {"flushed": [], "mint": []}

    class _Ledger:
        async def flush(self, trace):
            state["flushed"].append(tuple(trace.events))

    class _Principals:
        def __init__(self, store):
            state["store"] = store

        async def get_owner(self):
            return SimpleNamespace(user_id="usr_owner", role="owner")

    def _fake_create_trace(
        *, config, actor_user_id, session_key, platform, source=None, mode=None,
    ):
        state["mint"].append(
            {
                "actor_user_id": actor_user_id,
                "session_key": session_key,
                "platform": platform,
                "mode": mode,
            }
        )
        trace = InteractionTrace(
            actor_user_id=actor_user_id,
            session_key=session_key,
            platform=platform,
            mode=mode or "prod",
        )
        state["trace"] = trace
        return trace, _Ledger()

    monkeypatch.setattr(
        datastore, "get_store", lambda *a, **k: SimpleNamespace(dsn="postgresql://x")
    )
    monkeypatch.setattr(access, "PrincipalStore", _Principals)
    monkeypatch.setattr(interactions, "create_trace", _fake_create_trace)
    return state


def test_scheduled_run_is_one_owner_attributed_trace(traced):
    trace, ledger, context = scheduler._start_cron_trace(
        "job_brief", "Morning brief", "cron_job_brief_20260101_070000"
    )
    assert current_trace() is trace  # tool spans join this trace
    scheduler._cron_trace_event("outbound", "job_brief", "Cron response (12 chars)")
    scheduler._finish_cron_trace(trace, ledger, context, "job_brief")

    assert current_trace() is None  # not leaked to the next job on this thread
    assert traced["mint"] == [
        {
            "actor_user_id": "usr_owner",
            "session_key": "cron_job_brief_20260101_070000",
            "platform": "cron",
            "mode": "prod",
        }
    ]
    (rows,) = traced["flushed"]
    assert [r.kind for r in rows] == ["inbound", "outbound"]
    assert {r.trace_id for r in rows} == {trace.trace_id}
    assert {r.platform for r in rows} == {"cron"}
    assert rows[0].summary == "Scheduled job: Morning brief"
    assert rows[1].parent_id == rows[0].id


def test_failed_run_records_the_error(traced):
    trace, ledger, context = scheduler._start_cron_trace("job_x", "Job X", "cron_job_x")
    scheduler._cron_trace_event("error", "job_x", "Cron job failed: RuntimeError: boom")
    scheduler._finish_cron_trace(trace, ledger, context, "job_x")

    (rows,) = traced["flushed"]
    assert [r.kind for r in rows] == ["inbound", "error"]
    assert "boom" in rows[1].summary


def test_unconfigured_datastore_leaves_the_job_untraced(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("supabase-app store is not configured")

    monkeypatch.setattr(datastore, "get_store", _boom)

    assert scheduler._start_cron_trace("job_y", "Job Y", "cron_job_y") == (
        None,
        None,
        None,
    )
    assert current_trace() is None
    # Emitting/finishing without a trace is a no-op, not an error.
    scheduler._cron_trace_event("outbound", "job_y", "Cron response (0 chars)")
    scheduler._finish_cron_trace(None, None, None, "job_y")
