"""What a project run is thinking, while it thinks it.

A run page that only shows card statuses can tell you a run is `running`
for eleven minutes and nothing else — which is indistinguishable, from the
outside, from a run that is stuck. Chat already solves this: the agent's
reasoning and its tool calls are streamed to the browser as they happen.
This is the same idea for a run's **inline** steps, which execute inside
the web server's own process and therefore have the same callbacks
available.

Two decisions worth stating, because both are easy to get wrong later:

- **Only safe metadata is kept.** Reasoning text and a tool's id and name
  go in the buffer; a tool's *arguments and results never do* — they carry
  file contents, credentials and API responses, and this buffer is read by
  a browser. The publish helpers do not accept them.
- **Cursor, not fan-out.** The producer is an agent thread with no event
  loop; the consumer is an SSE coroutine. Rather than bridge the two with
  ``call_soon_threadsafe`` (which needs the loop the run was started from,
  and a run outlives its request), each event gets a sequence number and a
  reader asks for "everything after N". Replay and live tail are then the
  same operation, and a reader that arrives late or reconnects gets the
  whole run rather than the remainder.

Board-dispatched cards run in a *separate process* and are not visible
here; the run page says so rather than implying the reasoning covers every
card.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Tuple

#: Events kept per run. A long run's early reasoning is dropped before its
#: memory is: this is a live view, not the record (the record is the run
#: row, its cards and its deliveries).
MAX_EVENTS = 400

#: How long a finished run stays readable, so a page opened just after the
#: end still shows what happened rather than an empty panel.
GRACE_SECONDS = 300.0


class _Run:
    __slots__ = ("events", "seq", "done", "ended_at")

    def __init__(self) -> None:
        self.events: List[dict] = []
        self.seq = 0
        self.done = False
        self.ended_at: Optional[float] = None


_LOCK = threading.Lock()
_RUNS: Dict[str, _Run] = {}


def run_key(project_id: str, run_no: int) -> str:
    """The registry key for a run. Project-scoped, so two projects' run 3
    are two different runs."""
    return f"{project_id}:{run_no}"


def _prune_locked() -> None:
    now = time.time()
    for key, run in list(_RUNS.items()):
        if run.done and run.ended_at is not None and now - run.ended_at > GRACE_SECONDS:
            _RUNS.pop(key, None)


def begin(key: str) -> None:
    """Open a buffer for a run. A restart clears whatever was there: the
    live view belongs to the attempt that is running now."""
    with _LOCK:
        _prune_locked()
        _RUNS[key] = _Run()


def finish(key: str, note: str = "") -> None:
    """Mark the run's inline work over. Readers stop tailing when they see
    this, instead of holding a connection open forever."""
    with _LOCK:
        run = _RUNS.get(key)
        if run is None:
            return
        if note:
            _append_locked(run, {"kind": "status", "text": note})
        run.done = True
        run.ended_at = time.time()


def _append_locked(run: _Run, event: dict) -> None:
    run.seq += 1
    event["seq"] = run.seq
    event["at"] = time.time()
    run.events.append(event)
    if len(run.events) > MAX_EVENTS:
        del run.events[: len(run.events) - MAX_EVENTS]


def publish_reasoning(key: str, text: str) -> None:
    """The agent's reasoning, verbatim. Safe for the browser — it is the
    model's own words about what it is doing, not tool I/O."""
    if not text:
        return
    with _LOCK:
        run = _RUNS.get(key)
        if run is not None:
            _append_locked(run, {"kind": "reasoning", "text": str(text)})


def publish_tool(key: str, phase: str, tool_id: str, name: str) -> None:
    """A tool's id and name only. Arguments and results are deliberately
    not parameters of this function: they can carry secrets, and a value
    that never enters the buffer cannot leak out of it."""
    with _LOCK:
        run = _RUNS.get(key)
        if run is not None:
            _append_locked(
                run,
                {
                    "kind": "tool.start" if phase == "start" else "tool.complete",
                    "tool_id": str(tool_id or ""),
                    "name": str(name or "tool"),
                },
            )


def read(key: str, after: int = 0) -> Tuple[List[dict], bool, bool]:
    """Everything after sequence ``after``.

    Returns ``(events, done, known)``. ``known`` is False when this process
    has no buffer for the run — which is the ordinary case for a
    board-dispatched run (another process runs it) and for a run that ended
    long enough ago to be pruned. The caller says which; it does not
    pretend the run had nothing to say.
    """
    with _LOCK:
        run = _RUNS.get(key)
        if run is None:
            return [], True, False
        return [dict(e) for e in run.events if e["seq"] > after], run.done, True
