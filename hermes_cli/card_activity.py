"""What a board-dispatched card is thinking, while it thinks it.

:mod:`hermes_cli.run_activity` streams a run's *inline* steps because they
run inside the web server. A card the dispatcher hands to a worker runs in a
separate ``hermes chat -q`` process, so its reasoning never reached the
browser. This module is the relay: the worker appends events to a small
SQLite file next to the board's ``kanban.db`` and the web server tails it.

The same two rules as the in-process buffer:

- **Only safe metadata is kept.** Reasoning text, short status lines and a
  tool's id and name. A tool's arguments and results never enter the store
  — the publish helpers do not accept them — because this is read by a
  browser.
- **Cursor, not fan-out.** Every event gets a per-card sequence number that
  never goes backwards (not even across worker restarts), and a reader asks
  for "everything after N".

Retention is bounded twice: at most :data:`MAX_EVENTS` per card, and a card
nobody has written to for :data:`TTL_SECONDS` is pruned whole. Writers are
other processes (one per running card), so every write is one short
``BEGIN IMMEDIATE`` transaction under WAL.

The worker side (:class:`WorkerRelay`) never blocks the agent loop: it
queues events and a daemon thread writes them; a failure drops the event.
"""

from __future__ import annotations

import atexit
import os
import queue
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

#: Events kept per card. This is a live view, not the record.
MAX_EVENTS = 400

#: A card stream nobody has written to for this long is dropped whole.
TTL_SECONDS = 6 * 3600.0

#: Longest single reasoning/status event; longer text is split.
MAX_TEXT = 2_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_streams (
    task_id     TEXT PRIMARY KEY,
    next_seq    INTEGER NOT NULL DEFAULT 0,
    started_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS card_events (
    task_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT,
    tool_id    TEXT,
    name       TEXT,
    at         REAL NOT NULL,
    PRIMARY KEY (task_id, seq)
);
"""

_KINDS = ("reasoning", "status", "tool.start", "tool.complete")


def store_path(board: Optional[str] = None) -> Path:
    """The store sits beside the board's ``kanban.db``.

    The dispatcher pins ``HERMES_KANBAN_DB`` in the worker's env, so the
    worker and the web server resolve the same directory even when the
    worker runs under another profile's ``HERMES_HOME``.
    ``HERMES_CARD_ACTIVITY_DB`` pins the file directly.
    """
    override = os.environ.get("HERMES_CARD_ACTIVITY_DB", "").strip()
    if override:
        return Path(override).expanduser()
    from hermes_cli import kanban_db

    return kanban_db.kanban_db_path(board=board).parent / "card_activity.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def _prune(conn: sqlite3.Connection, now: float) -> None:
    cutoff = now - TTL_SECONDS
    stale = [r[0] for r in conn.execute(
        "SELECT task_id FROM card_streams WHERE updated_at < ?", (cutoff,)
    )]
    for task_id in stale:
        conn.execute("DELETE FROM card_events WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM card_streams WHERE task_id = ?", (task_id,))


def _clip(text: str) -> List[str]:
    text = str(text)
    return [text[i : i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)] or [""]


def _append(conn: sqlite3.Connection, task_id: str, events: List[dict]) -> None:
    now = time.time()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT next_seq FROM card_streams WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO card_streams (task_id, next_seq, started_at, updated_at)"
                " VALUES (?, 0, ?, ?)",
                (task_id, now, now),
            )
            seq = 0
        else:
            seq = int(row[0])
        for ev in events:
            seq += 1
            conn.execute(
                "INSERT INTO card_events (task_id, seq, kind, text, tool_id, name, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, seq, ev["kind"], ev.get("text"), ev.get("tool_id"),
                 ev.get("name"), ev.get("at", now)),
            )
        conn.execute(
            "UPDATE card_streams SET next_seq = ?, updated_at = ? WHERE task_id = ?",
            (seq, now, task_id),
        )
        conn.execute(
            "DELETE FROM card_events WHERE task_id = ? AND seq <= ?",
            (task_id, seq - MAX_EVENTS),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _reasoning_event(text: str) -> List[dict]:
    return [{"kind": "reasoning", "text": part} for part in _clip(text) if part]


def _status_event(text: str) -> List[dict]:
    return [{"kind": "status", "text": part} for part in _clip(text) if part]


def _tool_event(phase: str, tool_id: str, name: str) -> dict:
    return {
        "kind": "tool.start" if phase == "start" else "tool.complete",
        "tool_id": str(tool_id or ""),
        "name": str(name or "tool"),
    }


# ---------------------------------------------------------------------------
# Synchronous API (tests, the web server, and the relay's writer thread)
# ---------------------------------------------------------------------------


def begin(task_id: str, *, path: Optional[Path] = None, note: str = "") -> None:
    """Open (or reopen) a card's stream for a new worker attempt.

    Earlier events are dropped — the live view belongs to the attempt that
    is running now — but the sequence keeps counting, so a reader holding
    an old cursor never skips the new attempt's first events.
    """
    now = time.time()
    with closing(_connect(path or store_path())) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _prune(conn, now)
            conn.execute("DELETE FROM card_events WHERE task_id = ?", (task_id,))
            conn.execute(
                "INSERT INTO card_streams (task_id, next_seq, started_at, updated_at)"
                " VALUES (?, 0, ?, ?) ON CONFLICT(task_id) DO UPDATE SET"
                " started_at = excluded.started_at, updated_at = excluded.updated_at,"
                " finished_at = NULL",
                (task_id, now, now),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if note:
            _append(conn, task_id, _status_event(note))


def finish(task_id: str, note: str = "", *, path: Optional[Path] = None) -> None:
    """Mark the worker's attempt over; readers stop tailing on it."""
    with closing(_connect(path or store_path())) as conn:
        if note:
            _append(conn, task_id, _status_event(note))
        conn.execute(
            "UPDATE card_streams SET finished_at = ?, updated_at = ? WHERE task_id = ?",
            (time.time(), time.time(), task_id),
        )


def publish_reasoning(task_id: str, text: str, *, path: Optional[Path] = None) -> None:
    """The agent's reasoning, verbatim."""
    if not text:
        return
    with closing(_connect(path or store_path())) as conn:
        _append(conn, task_id, _reasoning_event(text))


def publish_status(task_id: str, text: str, *, path: Optional[Path] = None) -> None:
    """A short status line ("Worker started", "Worker finished")."""
    if not text:
        return
    with closing(_connect(path or store_path())) as conn:
        _append(conn, task_id, _status_event(text))


def publish_tool(task_id: str, phase: str, tool_id: str, name: str) -> None:
    """A tool's id and name only. Arguments and results are deliberately
    not parameters of this function: a value that never enters the store
    cannot leak out of it."""
    with closing(_connect(store_path())) as conn:
        _append(conn, task_id, [_tool_event(phase, tool_id, name)])


def read(
    task_id: str, after: int = 0, *, path: Optional[Path] = None
) -> Tuple[List[dict], bool, bool]:
    """Everything after sequence ``after``: ``(events, finished, known)``.

    ``known`` is False when no worker has opened a stream for the card (a
    worker that predates the relay, a non-Hermes worker, or one pruned by
    the TTL) — the caller says so instead of showing an idle card.
    """
    p = path or store_path()
    if not p.exists():
        return [], True, False
    with closing(_connect(p)) as conn:
        row = conn.execute(
            "SELECT updated_at, finished_at FROM card_streams WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None or row[0] < time.time() - TTL_SECONDS:
            return [], True, False
        rows = conn.execute(
            "SELECT seq, kind, text, tool_id, name, at FROM card_events"
            " WHERE task_id = ? AND seq > ? ORDER BY seq",
            (task_id, int(after)),
        ).fetchall()
    events: List[dict] = []
    for seq, kind, text, tool_id, name, at in rows:
        ev: dict = {"seq": seq, "kind": kind, "at": at}
        if kind in ("reasoning", "status"):
            ev["text"] = text or ""
        else:
            ev["tool_id"] = tool_id or ""
            ev["name"] = name or "tool"
        events.append(ev)
    return events, row[1] is not None, True


# ---------------------------------------------------------------------------
# The worker side: tee the agent's callbacks into the store, off-thread
# ---------------------------------------------------------------------------


class WorkerRelay:
    """Publishes one worker's reasoning and tool names without blocking it.

    The agent's callbacks run on the agent thread (and tool callbacks on
    executor threads); each one only drops a tuple on a queue. A daemon
    thread batches the queue into the store every :attr:`FLUSH_SECONDS`.
    Streaming reasoning deltas are coalesced into whole lines so the store
    holds sentences, not tokens.
    """

    FLUSH_SECONDS = 0.5
    #: A partial line longer than this is flushed without waiting for "\n".
    MAX_PENDING = 240

    def __init__(self, task_id: str, *, path: Optional[Path] = None) -> None:
        self.task_id = task_id
        self.path = path or store_path()
        self._q: "queue.SimpleQueue[Optional[dict]]" = queue.SimpleQueue()
        self._pending = ""
        self._streamed = ""
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._loop, name=f"card-activity-{task_id}", daemon=True
        )
        self._thread.start()

    # -- producers (agent threads) -----------------------------------------

    def reasoning_delta(self, text: Any) -> None:
        if not isinstance(text, str) or not text or self._closed:
            return
        with self._lock:
            # A non-streaming response hands over the whole reasoning once,
            # after any deltas for it; drop the duplicate.
            if len(text) > 40 and self._streamed.strip().endswith(text.strip()):
                return
            self._streamed = (self._streamed + text)[-8_000:]
            self._pending += text
            *lines, self._pending = self._pending.split("\n")
            if len(self._pending) > self.MAX_PENDING:
                lines.append(self._pending)
                self._pending = ""
        for line in lines:
            if line.strip():
                self._q.put({"kind": "reasoning", "text": line.strip()})

    def tool(self, phase: str, tool_id: Any, name: Any) -> None:
        if self._closed:
            return
        self._flush_pending()
        with self._lock:
            self._streamed = ""
        self._q.put(_tool_event(phase, str(tool_id or ""), str(name or "tool")))

    def status(self, text: str) -> None:
        if text and not self._closed:
            self._q.put({"kind": "status", "text": str(text)})

    def _flush_pending(self) -> None:
        with self._lock:
            line, self._pending = self._pending.strip(), ""
        if line:
            self._q.put({"kind": "reasoning", "text": line})

    # -- consumer (daemon thread) -------------------------------------------

    def _loop(self) -> None:
        stop = False
        while not stop:
            batch: List[dict] = []
            try:
                item = self._q.get(timeout=self.FLUSH_SECONDS)
            except queue.Empty:
                item = {}
            if item is None:
                stop = True
            elif item:
                batch.append(item)
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    stop = True
                    break
                batch.append(item)
            if batch:
                self._write(batch)

    def _write(self, batch: List[dict]) -> None:
        events: List[dict] = []
        for ev in batch:
            if ev["kind"] in ("reasoning", "status"):
                events.extend({"kind": ev["kind"], "text": p} for p in _clip(ev["text"]) if p)
            elif ev["kind"] in _KINDS:
                events.append(ev)
        if not events:
            return
        try:
            with closing(_connect(self.path)) as conn:
                _append(conn, self.task_id, events)
        except Exception:
            pass  # best-effort: a dropped event never fails the worker

    def close(self, note: str = "", timeout: float = 2.0) -> None:
        """Flush what is queued and mark the attempt finished."""
        if self._closed:
            return
        self._flush_pending()
        if note:
            self.status(note)
        self._closed = True
        self._q.put(None)
        self._thread.join(timeout)
        try:
            with closing(_connect(self.path)) as conn:
                conn.execute(
                    "UPDATE card_streams SET finished_at = ?, updated_at = ?"
                    " WHERE task_id = ?",
                    (time.time(), time.time(), self.task_id),
                )
        except Exception:
            pass


def _chain(first: Optional[Callable], second: Callable) -> Callable:
    def _both(*args: Any) -> None:
        try:
            second(*args)
        except Exception:
            pass
        if first is not None:
            first(*args)

    return _both


def attach_worker_relay(agent: Any, task_id: str) -> Optional[WorkerRelay]:
    """Tee ``agent``'s reasoning and tool callbacks into the card's stream.

    Only the callbacks are touched — never the prompt, the history or the
    tools — and any callback already set still runs. Idempotent per agent.
    Tool callbacks receive arguments/results from the agent; the relay
    takes the id and name and drops the rest here, before anything is
    queued.
    """
    task_id = (task_id or "").strip()
    if not task_id or agent is None:
        return None
    existing = getattr(agent, "_card_activity_relay", None)
    if isinstance(existing, WorkerRelay):
        return existing
    try:
        begin(task_id, note="Worker started")
    except Exception:
        return None
    relay = WorkerRelay(task_id)

    agent.reasoning_callback = _chain(
        getattr(agent, "reasoning_callback", None), relay.reasoning_delta
    )
    agent.tool_start_callback = _chain(
        getattr(agent, "tool_start_callback", None),
        lambda tool_id, name, *_ignored: relay.tool("start", tool_id, name),
    )
    agent.tool_complete_callback = _chain(
        getattr(agent, "tool_complete_callback", None),
        lambda tool_id, name, *_ignored: relay.tool("complete", tool_id, name),
    )
    try:
        agent._card_activity_relay = relay
    except Exception:
        pass
    atexit.register(relay.close, "Worker finished")
    return relay
