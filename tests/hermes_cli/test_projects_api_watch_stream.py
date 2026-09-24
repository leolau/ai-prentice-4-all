"""`projects_api._watch_stream` — the shared tick-and-diff loop behind the
run/card/project-cursor live streams (§12 live updates, push edition).

Exercised directly against the generator, not through an HTTP client: the
project-events stream never reaches a terminal state on its own, so a real
request would only stop on a client disconnect — fine for a browser, not
something a test should wait out on a fixed real-time tick. A fake
``Request`` with a scriptable ``is_disconnected()`` gets the same coverage
in milliseconds.
"""

from __future__ import annotations

import asyncio
import json

from hermes_cli import projects_api


class _FakeRequest:
    """Reports disconnected after ``disconnect_after`` liveness checks —
    the generator calls ``is_disconnected()`` once per tick, after any
    frame for that tick has already been yielded."""

    def __init__(self, disconnect_after: int = 10_000):
        self.checks = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > self.disconnect_after


async def _collect(response, limit: int = 50) -> list[tuple[str, dict]]:
    """Drain a `_watch_stream` `StreamingResponse` into (event, data) pairs."""
    frames: list[tuple[str, dict]] = []
    async for chunk in response.body_iterator:
        raw = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else chunk
        if raw.startswith(":"):
            continue  # a keep-alive comment, not a frame
        event, data = "message", None
        for line in raw.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if data is not None:
            frames.append((event, data))
        if len(frames) >= limit:
            break
    return frames


def test_emits_one_update_per_actual_change_and_ends_on_terminal():
    rows = iter(
        [
            {"status": "running", "n": 1},
            {"status": "running", "n": 1},  # unchanged — must not re-emit
            {"status": "running", "n": 2},
            {"status": "done", "n": 2},
        ]
    )

    async def run():
        response = await projects_api._watch_stream(
            _FakeRequest(),
            lambda: next(rows, None),
            is_terminal=lambda row: row["status"] == "done",
            tick_seconds=0,
        )
        return await _collect(response)

    frames = asyncio.run(run())
    kinds = [f[0] for f in frames]
    assert kinds == ["update", "update", "update", "end"]
    assert [f[1].get("n") for f in frames[:3]] == [1, 2, 2]
    assert frames[2][1]["status"] == "done"


def test_ends_with_gone_when_the_row_disappears():
    rows = iter([{"status": "running"}, None])

    async def run():
        response = await projects_api._watch_stream(
            _FakeRequest(),
            lambda: next(rows, None),
            is_terminal=lambda row: False,
            tick_seconds=0,
        )
        return await _collect(response)

    frames = asyncio.run(run())
    assert [f[0] for f in frames] == ["update", "gone"]


def test_run_is_terminal_stays_live_past_terminal_status_while_a_card_runs():
    """Mirrors `useRunLive.ts`'s `isRunLive(status, hasActiveCard)` exactly
    (PR #403): a terminal run row (e.g. auto-failed by the stale-run sweep)
    can still have a card actively working, and the stream must not close
    on the row alone."""
    live_card = {"status": "running"}
    done_card = {"status": "done"}

    assert projects_api._run_is_terminal({"status": "done", "cards": [done_card]})
    assert not projects_api._run_is_terminal(
        {"status": "done", "cards": [done_card, live_card]}
    )
    assert not projects_api._run_is_terminal({"status": "running", "cards": []})
    assert projects_api._run_is_terminal({"status": "failed", "cards": []})
    assert projects_api._run_is_terminal({"status": "cancelled", "cards": None})


def test_stops_on_client_disconnect_without_a_terminal_row():
    """The project-events cursor has no terminal state at all — a
    disconnect is the only thing that ever ends it."""
    calls = {"n": 0}

    def produce():
        calls["n"] += 1
        return {"latest_event_id": calls["n"]}

    async def run():
        response = await projects_api._watch_stream(
            _FakeRequest(disconnect_after=3),
            produce,
            is_terminal=lambda row: False,
            tick_seconds=0,
        )
        return await _collect(response, limit=100)

    frames = asyncio.run(run())
    # One update per distinct value, then the loop stops once the fake
    # request reports disconnected — never an "end" frame (there is none
    # for this stream) and never an unbounded read.
    assert all(f[0] == "update" for f in frames)
    assert 1 <= len(frames) <= 4
