"""A board-dispatched card's live reasoning (the cross-process relay).

The worker runs in another process, so the relay is a small SQLite store
next to the board's ``kanban.db``. Contracts, in the order the data
travels: the store appends, bounds, expires and replays from a cursor; two
writers at once never collide on a sequence number; the worker relay tees
the agent's callbacks without letting a tool's arguments or result in; and
the SSE endpoint serves the same frames as ``/runs/{n}/activity``.
"""

from __future__ import annotations

import inspect
import multiprocessing
import threading
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import card_activity, kanban_db, projects_api, projects_live_api
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]
STRANGER = Principal(user_id="eve", display="Eve", role="member")  # type: ignore[arg-type]

_PREFIX = "/api/registry/projects"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "card_activity.db"
    monkeypatch.setenv("HERMES_CARD_ACTIVITY_DB", str(path))
    return path


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_a_card_no_worker_ever_opened_is_unknown_not_silent(store):
    assert card_activity.read("t_none") == ([], True, False)
    card_activity.begin("t_other")
    assert card_activity.read("t_none") == ([], True, False)


def test_append_and_resume_after_a_cursor(store):
    card_activity.begin("t1", note="Worker started")
    card_activity.publish_reasoning("t1", "Reading the template")
    card_activity.publish_tool("t1", "start", "tc-1", "read_file")
    card_activity.publish_tool("t1", "complete", "tc-1", "read_file")

    events, finished, known = card_activity.read("t1")
    assert known and not finished
    assert [e["kind"] for e in events] == [
        "status", "reasoning", "tool.start", "tool.complete",
    ]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert events[1]["text"] == "Reading the template"
    assert events[2]["name"] == "read_file" and events[2]["tool_id"] == "tc-1"

    rest, _f, _k = card_activity.read("t1", after=2)
    assert [e["seq"] for e in rest] == [3, 4]

    card_activity.finish("t1", "Worker finished")
    tail, finished, known = card_activity.read("t1", after=4)
    assert finished and known
    assert [e["text"] for e in tail] == ["Worker finished"]


def test_a_new_attempt_clears_the_old_one_but_never_rewinds_the_cursor(store):
    card_activity.begin("t1")
    card_activity.publish_reasoning("t1", "attempt one")
    card_activity.finish("t1")
    card_activity.begin("t1")
    card_activity.publish_reasoning("t1", "attempt two")
    events, finished, _known = card_activity.read("t1")
    assert not finished
    assert [e["text"] for e in events] == ["attempt two"]
    # A reader still holding attempt one's cursor sees attempt two.
    assert [e["text"] for e in card_activity.read("t1", after=1)[0]] == ["attempt two"]


def test_retention_is_bounded(store, monkeypatch):
    monkeypatch.setattr(card_activity, "MAX_EVENTS", 5)
    card_activity.begin("t1")
    for i in range(12):
        card_activity.publish_reasoning("t1", f"line {i}")
    events, _f, _k = card_activity.read("t1")
    assert [e["text"] for e in events] == [f"line {i}" for i in range(7, 12)]
    assert events[-1]["seq"] == 12


def test_long_text_is_split_not_stored_whole(store, monkeypatch):
    monkeypatch.setattr(card_activity, "MAX_TEXT", 10)
    card_activity.begin("t1")
    card_activity.publish_reasoning("t1", "x" * 25)
    events, _f, _k = card_activity.read("t1")
    assert [len(e["text"]) for e in events] == [10, 10, 5]


def test_a_stream_nobody_wrote_to_expires(store, monkeypatch):
    card_activity.begin("old")
    card_activity.publish_reasoning("old", "long ago")
    real = time.time
    monkeypatch.setattr(
        card_activity.time, "time", lambda: real() + card_activity.TTL_SECONDS + 5
    )
    # Expired streams read as unknown immediately…
    assert card_activity.read("old") == ([], True, False)
    # …and are physically pruned on the next attempt anyone opens.
    card_activity.begin("fresh")
    import sqlite3

    conn = sqlite3.connect(str(store))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM card_events WHERE task_id='old'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM card_streams WHERE task_id='old'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_two_threads_writing_at_once_never_share_a_sequence(store):
    card_activity.begin("t1")

    def _write(tag: str) -> None:
        for i in range(40):
            card_activity.publish_reasoning("t1", f"{tag}{i}")

    threads = [threading.Thread(target=_write, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events, _f, _k = card_activity.read("t1")
    assert [e["seq"] for e in events] == list(range(1, 81))
    assert {e["text"] for e in events} == {f"{t}{i}" for t in "ab" for i in range(40)}


def _child_writer(path: str, tag: str) -> None:
    import os

    os.environ["HERMES_CARD_ACTIVITY_DB"] = path
    from hermes_cli import card_activity as ca

    for i in range(25):
        ca.publish_reasoning("t1", f"{tag}{i}")
        ca.publish_tool("t1", "start", f"{tag}-tc{i}", "terminal")


def test_two_processes_writing_at_once_never_share_a_sequence(store):
    card_activity.begin("t1")
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_child_writer, args=(str(store), t)) for t in ("p", "q")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    events, _f, _k = card_activity.read("t1")
    assert len(events) == 100
    assert [e["seq"] for e in events] == list(range(1, 101))


def test_the_store_never_accepts_tool_arguments_or_results():
    """A value that cannot enter the store cannot leak out of it."""
    assert list(inspect.signature(card_activity.publish_tool).parameters) == [
        "task_id", "phase", "tool_id", "name",
    ]


# ---------------------------------------------------------------------------
# The worker relay
# ---------------------------------------------------------------------------


def _fake_agent():
    seen: list = []
    agent = types.SimpleNamespace(
        reasoning_callback=None,
        tool_start_callback=lambda *a: seen.append(("start", a)),
        tool_complete_callback=None,
    )
    return agent, seen


def test_relay_publishes_reasoning_lines_and_tool_names_only(store):
    agent, seen = _fake_agent()
    relay = card_activity.attach_worker_relay(agent, "t1")
    assert relay is not None
    # Idempotent: a second attach (goal-loop turn) reuses the same relay.
    assert card_activity.attach_worker_relay(agent, "t1") is relay

    agent.reasoning_callback("Reading the ")
    agent.reasoning_callback("template.\nNext I")
    secret_args = {"path": "/etc/secret", "token": "sk-SECRET"}
    agent.tool_start_callback("tc-1", "read_file", secret_args)
    agent.tool_complete_callback("tc-1", "read_file", secret_args, "RESULT-BODY sk-SECRET")
    relay.close("Worker finished")

    events, finished, known = card_activity.read("t1")
    assert known and finished
    kinds = [(e["kind"], e.get("text") or e.get("name")) for e in events]
    assert kinds == [
        ("status", "Worker started"),
        ("reasoning", "Reading the template."),
        ("reasoning", "Next I"),
        ("tool.start", "read_file"),
        ("tool.complete", "read_file"),
        ("status", "Worker finished"),
    ]
    blob = repr(events)
    assert "sk-SECRET" not in blob and "/etc/secret" not in blob
    assert "RESULT-BODY" not in blob
    # The callback that was already there still ran, with its own args.
    assert seen == [("start", ("tc-1", "read_file", secret_args))]


def test_relay_drops_the_non_streaming_duplicate_of_streamed_reasoning(store):
    agent, _seen = _fake_agent()
    relay = card_activity.attach_worker_relay(agent, "t1")
    text = "I will read the template first, then draft the clauses."
    for word in text.split(" "):
        agent.reasoning_callback(word + " ")
    agent.reasoning_callback(text)
    relay.close()
    events, _f, _k = card_activity.read("t1")
    assert [e["text"] for e in events if e["kind"] == "reasoning"] == [text]


def test_relay_never_raises_into_the_agent_when_the_store_breaks(store, monkeypatch):
    agent, _seen = _fake_agent()
    relay = card_activity.attach_worker_relay(agent, "t1")

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(card_activity, "_append", _boom)
    agent.reasoning_callback("still fine\n")
    agent.tool_start_callback("tc-1", "terminal", {})
    relay.close()


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.delenv("HERMES_CARD_ACTIVITY_DB", raising=False)
    monkeypatch.setattr(projects_live_api, "_ACTIVITY_TICK_SECONDS", 0.01)
    state = {"actor": OWNER}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set()

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)

    app = FastAPI()
    app.include_router(projects_api.router)
    app.include_router(projects_live_api.router)
    return TestClient(app), state


def _project_with_card(client, status: str = "running") -> tuple[str, str]:
    resp = client.post(
        f"{_PREFIX}/",
        json={
            "goal": "Ship the weekly digest",
            "description": "The weekly digest, compiled and sent each Monday.",
            "host_profile": "default",
            "outputs": [{"title": "The Monday digest email"}],
        },
    )
    assert resp.status_code == 200, resp.text
    project = resp.json()
    with kanban_db.connect_closing() as bconn:
        tid = kanban_db.create_task(
            bconn, title="Draft the digest", project_id=project["id"]
        )
        bconn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, tid))
        bconn.commit()
    return project["slug"], tid


def _frames(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        out.append((event, data))
    return out


def test_the_store_sits_beside_the_board_db(env, tmp_path):
    assert card_activity.store_path() == tmp_path / "card_activity.db"


def test_activity_404s_for_an_unknown_card(env):
    client, _state = env
    slug, _tid = _project_with_card(client)
    assert client.get(f"{_PREFIX}/{slug}/cards/t_nope/activity").status_code == 404


def test_activity_404s_for_a_caller_who_cannot_read_the_project(env):
    client, state = env
    slug, tid = _project_with_card(client)
    state["actor"] = STRANGER
    assert client.get(f"{_PREFIX}/{slug}/cards/{tid}/activity").status_code == 404


def test_activity_says_unavailable_when_no_worker_is_publishing(env):
    client, _state = env
    slug, tid = _project_with_card(client)
    resp = client.get(f"{_PREFIX}/{slug}/cards/{tid}/activity")
    assert resp.status_code == 200
    assert [name for name, _ in _frames(resp.text)] == ["unavailable"]


def test_activity_replays_reasoning_and_tool_names_then_ends(env):
    client, _state = env
    slug, tid = _project_with_card(client)
    card_activity.begin(tid)
    card_activity.publish_reasoning(tid, "Reading last week's digest")
    card_activity.publish_tool(tid, "start", "tc-1", "read_file")
    card_activity.publish_tool(tid, "complete", "tc-1", "read_file")
    card_activity.finish(tid, "Worker finished")

    resp = client.get(f"{_PREFIX}/{slug}/cards/{tid}/activity")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-accel-buffering"] == "no"
    assert [name for name, _ in _frames(resp.text)] == [
        "reasoning", "tool.start", "tool.complete", "status", "end",
    ]
    assert "Reading last week's digest" in resp.text
    assert "args" not in resp.text and "result" not in resp.text


def test_activity_ends_when_the_card_leaves_running(env):
    client, _state = env
    slug, tid = _project_with_card(client, status="blocked")
    card_activity.begin(tid)
    card_activity.publish_reasoning(tid, "half way")
    resp = client.get(f"{_PREFIX}/{slug}/cards/{tid}/activity")
    assert [name for name, _ in _frames(resp.text)] == ["reasoning", "end"]


def test_activity_resumes_after_a_cursor(env):
    client, _state = env
    slug, tid = _project_with_card(client)
    card_activity.begin(tid)
    card_activity.publish_reasoning(tid, "one")
    card_activity.publish_reasoning(tid, "two")
    card_activity.finish(tid)
    resp = client.get(f"{_PREFIX}/{slug}/cards/{tid}/activity?after=1")
    assert "one" not in resp.text
    assert "two" in resp.text


def test_activity_rejects_a_nonsense_cursor(env):
    client, _state = env
    slug, tid = _project_with_card(client)
    base = f"{_PREFIX}/{slug}/cards/{tid}/activity"
    assert client.get(f"{base}?after=nope").status_code == 400
    assert client.get(f"{base}?after=-2").status_code == 400
