"""api_call_log — the per-attempt ledger behind Models ▸ Performance.

Contract: every provider call attempt records one row (success AND each
failed retry); writes are fire-and-forget so telemetry can never break the
call it measures; rows past retention prune cleanly.
"""
import time

import pytest

from hermes_state import SessionDB, api_metrics_db, record_api_call


@pytest.fixture
def db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    yield db
    db.close()


def _rows(db):
    return db._conn.execute(
        "SELECT * FROM api_call_log ORDER BY id"
    ).fetchall()


def test_success_and_error_attempts_both_record(db):
    db.record_api_call(
        request_id="turn1:api:0",
        ts=time.time(),
        started_at=time.time() - 1.2,
        session_id="s1",
        caller="main",
        model="claude-sonnet-4-6",
        provider="anthropic",
        duration_ms=1234,
        status="ok",
        finish_reason="stop",
        usage={"input_tokens": 2048, "output_tokens": 512},
    )
    db.record_api_call(
        request_id="turn1:api:1",
        ts=time.time(),
        caller="main",
        model="claude-sonnet-4-6",
        provider="anthropic",
        duration_ms=900,
        status="error",
        error_type="RateLimitError",
        status_code=429,
        retry_count=1,
    )
    rows = _rows(db)
    assert len(rows) == 2
    ok, err = rows
    assert ok["status"] == "ok" and ok["input_tokens"] == 2048
    assert ok["duration_ms"] == 1234
    assert err["status"] == "error" and err["error_type"] == "RateLimitError"
    assert err["status_code"] == 429 and err["retry_count"] == 1


def test_usage_accepts_aux_namespace_shape(db):
    """Aux responses carry prompt/completion token names — record_api_call
    reads the hook-shape keys; the caller normalizes before calling."""
    db.record_api_call(
        caller="aux:compression",
        model="gpt-5-mini",
        usage={"input_tokens": 100, "output_tokens": 40,
               "cache_read_tokens": 60, "reasoning_tokens": 5},
    )
    row = _rows(db)[0]
    assert row["cache_read_tokens"] == 60 and row["reasoning_tokens"] == 5
    assert row["caller"] == "aux:compression"


def test_retention_prunes_old_rows(db):
    old = time.time() - 400 * 86400
    db.record_api_call(ts=old, model="m", status="ok")
    db.record_api_call(ts=time.time(), model="m", status="ok")
    removed = db.prune_api_call_log()
    assert removed == 1
    assert len(_rows(db)) == 1


def test_record_never_raises(db):
    """Telemetry must be fire-and-forget — junk input degrades, not breaks.
    An un-coercible duration drops the row but never raises."""
    db.record_api_call(duration_ms="not-a-number", usage=None)
    assert len(_rows(db)) == 0


def test_module_helper_uses_explicit_db(db):
    record_api_call(db, model="via-explicit", status="ok")
    assert _rows(db)[0]["model"] == "via-explicit"


def test_module_helper_swallows_a_broken_db():
    class BrokenDB:
        def record_api_call(self, **kw):
            raise RuntimeError("locked")

    record_api_call(BrokenDB(), model="m")  # must not raise


def test_aux_call_llm_wrapper_records_attempts(tmp_path, monkeypatch):
    """call_llm is a telemetry shell over _call_llm_impl — aux-role calls
    (vision, compression, …) land in the ledger with their caller role."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()

    import agent.auxiliary_client as aux

    class _Resp:
        model = "gpt-5-mini"

        class usage:
            input_tokens = 120
            output_tokens = 30

    def fake_impl(task, **kw):
        assert task == "compression"
        return _Resp()

    monkeypatch.setattr(aux, "_call_llm_impl", fake_impl)
    monkeypatch.setattr(
        aux, "_resolve_task_provider_model",
        lambda *a, **k: ("openai", "gpt-5-mini", None, None, None),
    )
    out = aux.call_llm("compression", messages=[{"role": "user", "content": "x"}])
    assert out is _Resp or isinstance(out, _Resp)

    db = api_metrics_db()
    rows = db._conn.execute("SELECT * FROM api_call_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["caller"] == "aux:compression"
    assert rows[0]["status"] == "ok"
    assert rows[0]["input_tokens"] == 120

    def boom(task, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(aux, "_call_llm_impl", boom)
    try:
        aux.call_llm("compression", messages=[])
    except RuntimeError:
        pass
    rows = db._conn.execute("SELECT * FROM api_call_log").fetchall()
    assert len(rows) == 2
    assert rows[1]["status"] == "error" and rows[1]["error_type"] == "RuntimeError"
    hermes_state._METRICS_DBS.clear()


def test_api_metrics_db_caches_per_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()
    try:
        a = api_metrics_db()
        b = api_metrics_db()
        assert a is b and a is not None
    finally:
        for db in list(hermes_state._METRICS_DBS.values()):
            try:
                db.close()
            except Exception:
                pass
        hermes_state._METRICS_DBS.clear()


def test_card_worker_calls_are_tagged_with_their_card(db, monkeypatch):
    """A board worker's env names its card and run; every call it records
    carries both, so the card page can total that card's calls."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_abc123")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "218")
    db.record_api_call(model="m", status="ok", duration_ms=10)
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    monkeypatch.delenv("HERMES_KANBAN_RUN_ID")
    db.record_api_call(model="m", status="ok", duration_ms=10)
    tagged, plain = _rows(db)
    assert tagged["kanban_task_id"] == "t_abc123"
    assert tagged["kanban_run_id"] == 218
    assert plain["kanban_task_id"] is None and plain["kanban_run_id"] is None


def test_explicit_empty_card_id_means_no_card(db, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_from_env")
    db.record_api_call(model="m", kanban_task_id="")
    assert _rows(db)[0]["kanban_task_id"] is None


def test_kanban_task_call_stats_totals_main_and_splits_rows(tmp_path):
    from hermes_state import kanban_task_call_stats

    db = SessionDB(tmp_path / "state.db")
    try:
        for ms in (2000, 4000):
            db.record_api_call(model="ds", provider="og", caller="main",
                               duration_ms=ms, kanban_task_id="t_1",
                               kanban_run_id=1)
        db.record_api_call(model="ds", provider="og", caller="main",
                           status="error", duration_ms=None,
                           kanban_task_id="t_1", kanban_run_id=2)
        db.record_api_call(model="ds", provider="og", caller="main",
                           duration_ms=9000, kanban_task_id="t_1",
                           kanban_run_id=2)
        db.record_api_call(model="qwen", provider="og", caller="aux:vision",
                           status="error", duration_ms=120000,
                           kanban_task_id="t_1", kanban_run_id=2)
        db.record_api_call(model="ds", caller="main", duration_ms=1,
                           kanban_task_id="t_other")
    finally:
        db.close()

    stats = kanban_task_call_stats(tmp_path / "state.db", "t_1")
    assert stats["main"] == {"calls": 4, "failures": 1, "min_ms": 2000,
                             "avg_ms": 5000, "max_ms": 9000}
    rows = {(r["run_id"], r["caller"]): r for r in stats["rows"]}
    assert set(rows) == {(1, "main"), (2, "main"), (2, "aux:vision")}
    assert rows[(1, "main")]["calls"] == 2 and rows[(1, "main")]["failures"] == 0
    assert rows[(2, "main")]["failures"] == 1
    assert rows[(2, "aux:vision")]["max_ms"] == 120000
    assert kanban_task_call_stats(tmp_path / "state.db", "t_none") == {
        "main": None, "rows": []
    }
    assert kanban_task_call_stats(tmp_path / "missing.db", "t_1") is None


def test_legacy_ledger_gains_card_columns(tmp_path):
    """A state.db from the build before card tagging upgrades in place."""
    import sqlite3

    path = tmp_path / "state.db"
    SessionDB(path).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX IF EXISTS idx_api_call_log_kanban_task")
    conn.execute("ALTER TABLE api_call_log DROP COLUMN kanban_run_id")
    conn.execute("ALTER TABLE api_call_log DROP COLUMN kanban_task_id")
    conn.commit()
    conn.close()
    db = SessionDB(path)
    try:
        db.record_api_call(model="m", kanban_task_id="t_9", kanban_run_id=3)
        row = _rows(db)[0]
        assert row["kanban_task_id"] == "t_9" and row["kanban_run_id"] == 3
    finally:
        db.close()
