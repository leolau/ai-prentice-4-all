"""api_call_log — the per-attempt ledger behind Models ▸ Performance.

Contract: every provider call attempt records one row (success AND each
failed retry); writes are fire-and-forget so telemetry can never break the
call it measures; rows past retention prune cleanly.
"""
import time
from types import SimpleNamespace

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


class _FakeResp:
    model = "gpt-5-mini"

    class usage:
        input_tokens = 120
        output_tokens = 30


class _FakeCompletions:
    def __init__(self, fn):
        self._fn = fn

    def create(self, **kw):
        return self._fn(**kw)


class _FakeClient:
    def __init__(self, fn):
        self.chat = SimpleNamespace(completions=_FakeCompletions(fn))


def test_logged_create_shim_records(tmp_path, monkeypatch):
    """_ensure_logged_create wraps chat.completions.create — the row lands
    with caller 'aux:<ctx task>' or bare 'aux'."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()

    import agent.auxiliary_client as aux

    client = _FakeClient(lambda **kw: _FakeResp())
    aux._ensure_logged_create(client, provider_hint="openai",
                              model_hint="gpt-5-mini")
    aux._ensure_logged_create(client)  # idempotent — no double record

    client.chat.completions.create(messages=[])  # ctx unset → passthrough
    token = aux._AUX_TASK_CTX.set("compression")
    try:
        client.chat.completions.create(messages=[])
    finally:
        aux._AUX_TASK_CTX.reset(token)

    db = api_metrics_db()
    rows = db._conn.execute("SELECT * FROM api_call_log ORDER BY id").fetchall()
    assert len(rows) == 1  # unscoped calls record nothing (main-path guard)
    assert rows[0]["caller"] == "aux:compression"
    assert rows[0]["status"] == "ok" and rows[0]["input_tokens"] == 120
    assert rows[0]["model"] == "gpt-5-mini" and rows[0]["provider"] == "openai"
    hermes_state._METRICS_DBS.clear()


def test_logged_create_error_and_stream(tmp_path, monkeypatch):
    """Errors record at raise time; streams record when consumed."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()
    import agent.auxiliary_client as aux

    def create(**kw):
        if kw.get("stream"):
            return iter([SimpleNamespace(usage=None),
                         SimpleNamespace(usage={"input_tokens": 7})])
        raise RuntimeError("provider down")

    client = _FakeClient(create)
    aux._ensure_logged_create(client, provider_hint="openai")

    token = aux._AUX_TASK_CTX.set("compression")
    try:
        with pytest.raises(RuntimeError):
            client.chat.completions.create(messages=[])
        stream = client.chat.completions.create(messages=[], stream=True)
        list(stream)  # consume → record fires on exhaustion
    finally:
        aux._AUX_TASK_CTX.reset(token)

    rows = api_metrics_db()._conn.execute(
        "SELECT * FROM api_call_log ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["status"] == "error" and rows[0]["error_type"] == "RuntimeError"
    assert rows[1]["status"] == "ok" and rows[1]["input_tokens"] == 7
    hermes_state._METRICS_DBS.clear()


def test_task_scoped_client_labels_calls(tmp_path, monkeypatch):
    """Getter-level proxy: a shared underlying client records the proxy's
    task label at call time — not the label it was built with."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()
    import agent.auxiliary_client as aux

    inner = _FakeClient(lambda **kw: _FakeResp())
    scoped = aux._task_scoped_client(inner, "triage_specifier", "openai", "m")
    assert scoped.chat.completions.create(messages=[]) is not None

    rows = api_metrics_db()._conn.execute(
        "SELECT caller FROM api_call_log").fetchall()
    assert [r["caller"] for r in rows] == ["aux:triage_specifier"]
    hermes_state._METRICS_DBS.clear()


def test_call_llm_sets_task_context(tmp_path, monkeypatch):
    """call_llm scopes _AUX_TASK_CTX around the impl so create() calls it
    makes are labelled — and produce exactly one ledger row."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()
    import agent.auxiliary_client as aux

    def fake_impl(task, **kw):
        assert aux._AUX_TASK_CTX.get() == "compression"
        client = _FakeClient(lambda **k: _FakeResp())
        aux._ensure_logged_create(client, provider_hint="openai")
        client.chat.completions.create(messages=[])
        return _FakeResp()

    monkeypatch.setattr(aux, "_call_llm_impl", fake_impl)
    aux.call_llm("compression", messages=[{"role": "user", "content": "x"}])

    rows = api_metrics_db()._conn.execute(
        "SELECT caller, status FROM api_call_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["caller"] == "aux:compression" and rows[0]["status"] == "ok"
    # ContextVar must not leak out of the call
    assert aux._AUX_TASK_CTX.get() is None
    hermes_state._METRICS_DBS.clear()


def test_logged_create_shim_records_async(tmp_path, monkeypatch):
    """Async clients: _to_async_client mints a FRESH AsyncOpenAI/adapter —
    the conversion decorator must carry the log-wrap across, and coroutine
    creates record exactly like sync ones."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()
    import agent.auxiliary_client as aux

    class _AsyncCompletions:
        async def create(self, **kw):
            return _FakeResp()

    class _AsyncClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_AsyncCompletions())

    # Simulate the _to_async_client output path: conversion result is wrapped.
    async_client = _AsyncClient()
    aux._ensure_logged_create(async_client, provider_hint="openai",
                              model_hint="gpt-5-mini")

    async def _run():
        token = aux._AUX_TASK_CTX.set("compression")
        try:
            return await async_client.chat.completions.create(messages=[])
        finally:
            aux._AUX_TASK_CTX.reset(token)

    import asyncio
    resp = asyncio.new_event_loop().run_until_complete(_run())
    assert resp is not None

    rows = api_metrics_db()._conn.execute(
        "SELECT caller, status, input_tokens, provider FROM api_call_log"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["caller"] == "aux:compression"
    assert rows[0]["status"] == "ok" and rows[0]["input_tokens"] == 120
    hermes_state._METRICS_DBS.clear()


def test_logged_create_unscoped_is_passthrough(tmp_path, monkeypatch):
    """A wrapped client invoked with no aux task in context records nothing —
    main-path code that shares a client shape must not double-log."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    hermes_state._METRICS_DBS.clear()
    import agent.auxiliary_client as aux

    calls = []
    client = _FakeClient(lambda **kw: calls.append(kw) or _FakeResp())
    aux._ensure_logged_create(client, provider_hint="openai")

    client.chat.completions.create(messages=[])
    assert len(calls) == 1  # underlying create ran
    rows = api_metrics_db()._conn.execute("SELECT * FROM api_call_log").fetchall()
    assert rows == []
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
