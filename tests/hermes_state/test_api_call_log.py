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
