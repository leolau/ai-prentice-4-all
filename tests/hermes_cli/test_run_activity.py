"""A run's live reasoning and tool activity (design §12).

Behaviour contracts, in the order the data travels: the buffer replays from
a cursor and says when it has never heard of a run; the seeded session hands
its callbacks to the agent; an inline run publishes through them; and the SSE
endpoint serves reasoning and tool *names* — never a tool's arguments or its
result, which is the whole reason this stream is safe to put in a browser.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import projects_api, run_activity
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]

_PREFIX = "/api/registry/projects"


@pytest.fixture(autouse=True)
def clean_registry():
    run_activity._RUNS.clear()
    yield
    run_activity._RUNS.clear()


# ---------------------------------------------------------------------------
# The buffer
# ---------------------------------------------------------------------------


def test_unknown_run_is_reported_as_unknown_not_as_silence():
    """A run this process never ran must not read as a run that said
    nothing — the page has to be able to tell the two apart."""
    events, done, known = run_activity.read("proj:99")
    assert (events, done, known) == ([], True, False)


def test_a_reader_resumes_after_its_cursor():
    key = run_activity.run_key("proj", 3)
    run_activity.begin(key)
    run_activity.publish_reasoning(key, "first")
    run_activity.publish_tool(key, "start", "tc-1", "read_file")
    run_activity.publish_tool(key, "complete", "tc-1", "read_file")

    events, done, known = run_activity.read(key)
    assert known and not done
    assert [e["kind"] for e in events] == ["reasoning", "tool.start", "tool.complete"]

    # Everything after the second event — a reconnect, not a restart.
    rest, _done, _known = run_activity.read(key, after=events[1]["seq"])
    assert [e["seq"] for e in rest] == [events[2]["seq"]]

    run_activity.finish(key, "Inline steps finished.")
    tail, done, known = run_activity.read(key, after=events[2]["seq"])
    assert done and known
    assert [e["text"] for e in tail] == ["Inline steps finished."]


def test_a_restarted_run_does_not_inherit_the_previous_attempt():
    key = run_activity.run_key("proj", 4)
    run_activity.begin(key)
    run_activity.publish_reasoning(key, "attempt one")
    run_activity.begin(key)
    events, _done, _known = run_activity.read(key)
    assert events == []


def test_the_buffer_never_holds_tool_arguments_or_results():
    """The publish helper takes no arguments/results parameter at all: a
    value that cannot enter the buffer cannot leak out of it."""
    import inspect

    params = list(inspect.signature(run_activity.publish_tool).parameters)
    assert params == ["key", "phase", "tool_id", "name"]

    key = run_activity.run_key("proj", 5)
    run_activity.begin(key)
    run_activity.publish_tool(key, "start", "tc-9", "terminal")
    (event,), _done, _known = run_activity.read(key)
    assert set(event) == {"kind", "tool_id", "name", "seq", "at"}


def test_the_buffer_is_bounded():
    key = run_activity.run_key("proj", 6)
    run_activity.begin(key)
    for i in range(run_activity.MAX_EVENTS + 25):
        run_activity.publish_reasoning(key, f"thought {i}")
    events, _done, _known = run_activity.read(key)
    assert len(events) == run_activity.MAX_EVENTS
    assert events[-1]["text"] == f"thought {run_activity.MAX_EVENTS + 24}"


# ---------------------------------------------------------------------------
# The seeded session hands the callbacks to the agent
# ---------------------------------------------------------------------------


def test_seeded_session_passes_the_callbacks_through_to_the_agent(monkeypatch):
    """The passthrough is the point: without it the run's reasoning never
    leaves ``AIAgent`` and the buffer stays empty."""
    import run_agent
    from agent import seeded_session

    captured: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here — construction is what we assert on")

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)

    def reasoning(text: str) -> None:  # pragma: no cover - never called
        pass

    result = seeded_session.spawn_seeded_session(
        "do the thing",
        origin="test",
        session_id="proj-run-1-abcd",
        config={"model": {"default": "test/model"}},
        runtime={"api_key": "k", "base_url": "http://x", "provider": "p"},
        model="test/model",
        session_db=object(),
        skip_memory=True,
        reasoning_callback=reasoning,
        tool_start_callback=lambda tc_id, name, args: None,
        tool_complete_callback=lambda tc_id, name, args, res: None,
    )
    assert result.error  # the fake agent refused to run; that is expected
    assert captured["reasoning_callback"] is reasoning
    assert captured["tool_start_callback"] is not None
    assert captured["tool_complete_callback"] is not None


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))

    async def _resolve(request, *, allow_as=True):
        return OWNER

    async def _enrolled(user_id):
        return set()

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)

    app = FastAPI()
    app.include_router(projects_api.router)
    return TestClient(app)


def _project_with_run(client) -> tuple[str, str, int]:
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
    slug = project["slug"]

    from hermes_cli import projects_db

    with projects_db.connect_closing() as conn:
        run = projects_db.open_project_run(
            conn, project_id=project["id"], trigger="manual", profile="default"
        )
    return project["id"], slug, run["run_no"]


def _frames(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        out.append((event, data))
    return out


def test_activity_404s_for_a_run_that_does_not_exist(env):
    _pid, slug, _run_no = _project_with_run(env)
    resp = env.get(f"{_PREFIX}/{slug}/runs/97/activity")
    assert resp.status_code == 404


def test_activity_says_unavailable_when_the_work_is_not_in_this_process(env):
    """A board-dispatched run has no buffer here. The stream must say so:
    an empty panel would read as a run doing nothing."""
    _pid, slug, run_no = _project_with_run(env)
    resp = env.get(f"{_PREFIX}/{slug}/runs/{run_no}/activity")
    assert resp.status_code == 200
    events = [name for name, _data in _frames(resp.text)]
    assert events == ["unavailable"]


def test_activity_replays_reasoning_and_tool_names_and_nothing_else(env):
    pid, slug, run_no = _project_with_run(env)
    key = run_activity.run_key(pid, run_no)
    run_activity.begin(key)
    run_activity.publish_reasoning(key, "Reading last week's digest")
    run_activity.publish_tool(key, "start", "tc-1", "read_file")
    run_activity.publish_tool(key, "complete", "tc-1", "read_file")
    run_activity.finish(key, "Inline steps finished.")

    resp = env.get(f"{_PREFIX}/{slug}/runs/{run_no}/activity")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _frames(resp.text)
    assert [name for name, _ in frames] == [
        "reasoning",
        "tool.start",
        "tool.complete",
        "status",
        "end",
    ]
    body = resp.text
    assert "Reading last week's digest" in body
    assert "read_file" in body
    # The tool's own I/O never crosses the boundary.
    assert "args" not in body and "result" not in body


def test_activity_resumes_after_a_cursor(env):
    pid, slug, run_no = _project_with_run(env)
    key = run_activity.run_key(pid, run_no)
    run_activity.begin(key)
    run_activity.publish_reasoning(key, "one")
    run_activity.publish_reasoning(key, "two")
    run_activity.finish(key)

    resp = env.get(f"{_PREFIX}/{slug}/runs/{run_no}/activity?after=1")
    assert resp.status_code == 200
    assert "one" not in resp.text
    assert "two" in resp.text


def test_activity_rejects_a_nonsense_cursor(env):
    _pid, slug, run_no = _project_with_run(env)
    assert env.get(f"{_PREFIX}/{slug}/runs/{run_no}/activity?after=nope").status_code == 400
    assert env.get(f"{_PREFIX}/{slug}/runs/{run_no}/activity?after=-2").status_code == 400
