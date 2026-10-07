"""``hermes logs backfill-calls`` — past main-model calls from agent.log.

Contracts: successes keep latency + tokens and failed attempts become
error rows; a worker's calls get its card and the run whose window holds
them; a re-run inserts nothing; nothing at/after the live ledger's first
row is inserted (the live recorder already has it).
"""
from __future__ import annotations

import time

import pytest

from hermes_cli import api_call_backfill, kanban_db
from hermes_state import SessionDB


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) + ",250"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    return home


def _write_log(home, base: float) -> None:
    sid = "20261007_014713_9744af"
    lines = [
        f"{_stamp(base)} INFO [{sid}] agent.turn_context: conversation turn:"
        f" session={sid} model=ds provider=og platform=cli history=0"
        " msg='work kanban task t_d8adf3c1 ...'",
        f"{_stamp(base + 10)} INFO [{sid}] agent.conversation_loop: API call #1:"
        " model=ds provider=og in=38494 out=58 total=38552 latency=2.5s"
        " cache=2048/38494 (5%)",
        f"{_stamp(base + 20)} WARNING [{sid}] agent.conversation_loop: API call"
        " failed (attempt 1/3) error_type=APIConnectionError thread=T"
        " provider=og base_url=https://x/v1 model=ds summary=Connection error.",
        f"{_stamp(base + 30)} INFO [chat_1] agent.conversation_loop: API call #4:"
        " model=glm provider=ali in=100 out=5 total=105 latency=316.4s",
    ]
    text = "\n".join(lines) + "\n"
    # The same record written twice (two handlers) counts once.
    (home / "logs" / "agent.log.1").write_text(lines[0] + "\n" + lines[1] + "\n")
    (home / "logs" / "agent.log").write_text(text)


def _rows(home):
    db = SessionDB(home / "state.db")
    try:
        return [dict(r) for r in db._conn.execute(
            "SELECT * FROM api_call_log ORDER BY ts"
        ).fetchall()]
    finally:
        db.close()


def _run(task_id: str, started: float, ended: float) -> int:
    with kanban_db.connect_closing() as conn:
        with kanban_db.write_txn(conn):
            conn.execute(
                "INSERT INTO tasks (id, title, status, created_at)"
                " VALUES (?, 'Verify', 'done', ?)",
                (task_id, int(started)),
            )
            cur = conn.execute(
                "INSERT INTO task_runs (task_id, status, started_at, ended_at)"
                " VALUES (?, 'done', ?, ?)",
                (task_id, int(started), int(ended)),
            )
            return int(cur.lastrowid)


def test_backfill_inserts_once_and_tags_cards(home):
    base = time.time() - 3 * 86400
    _write_log(home, base)
    run_id = _run("t_d8adf3c1", base - 1, base + 12)

    dry = api_call_backfill.backfill(dry_run=True)
    assert dry.inserted == 3 and _rows(home) == []

    res = api_call_backfill.backfill()
    assert (res.parsed, res.inserted, res.card_calls) == (3, 3, 2)
    ok, err, chat = _rows(home)
    assert ok["status"] == "ok" and ok["duration_ms"] == 2500
    assert ok["cache_read_tokens"] == 2048
    assert ok["input_tokens"] == 38494 - 2048 and ok["output_tokens"] == 58
    assert ok["kanban_task_id"] == "t_d8adf3c1" and ok["kanban_run_id"] == run_id
    assert err["status"] == "error" and err["error_type"] == "APIConnectionError"
    assert err["duration_ms"] is None and err["kanban_run_id"] == run_id
    assert chat["kanban_task_id"] is None and chat["duration_ms"] == 316400

    again = api_call_backfill.backfill()
    assert again.inserted == 0 and again.skipped_existing == 3
    assert len(_rows(home)) == 3


def test_backfill_stops_where_the_live_ledger_starts(home):
    base = time.time() - 3 * 86400
    _write_log(home, base)
    db = SessionDB(home / "state.db")
    try:
        db.record_api_call(request_id="live:1", ts=base + 15, model="ds")
    finally:
        db.close()
    res = api_call_backfill.backfill()
    assert res.inserted == 1 and res.skipped_after_cutoff == 2
