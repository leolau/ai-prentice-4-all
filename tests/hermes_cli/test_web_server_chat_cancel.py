"""Explicit Stop interrupts the server-side turn; a closed tab does not.

A turn deliberately outlives its SSE client (so it survives a closed browser),
which means the Stop button cannot work by closing the stream — it needs
``POST /api/sessions/{sid}/chat/stream/cancel``, which calls
``AIAgent.interrupt()`` on the run's agent. These tests drive that endpoint
against the in-memory run registry with a stand-in agent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except (ImportError, AttributeError):
        pass
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    return tmp_path


@pytest.fixture
def client(monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    person = SimpleNamespace(user_id="leo", display="leo", role="owner", is_owner=True)

    async def _fake_principal(request, *, allow_as=False):
        return person

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


class _FakeAgent:
    def __init__(self):
        self.interrupts = 0

    def interrupt(self, message=None):
        self.interrupts += 1


def _run(session_id: str, *, agent=None, done=False) -> dict:
    return {
        "session_id": session_id,
        "buffer": [],
        "subs": [],
        "done": done,
        "agent": agent,
        "cancel_requested": False,
    }


def test_cancel_interrupts_the_runs_agent(client):
    from hermes_cli import web_server

    agent = _FakeAgent()
    web_server._CHAT_RUNS["run-c1"] = _run("sess-a", agent=agent)
    try:
        res = client.post(
            "/api/sessions/sess-a/chat/stream/cancel", json={"run_id": "run-c1"}
        )
        assert res.status_code == 200
        assert res.json() == {"run_id": "run-c1", "cancelled": True, "done": False}
        assert agent.interrupts == 1
        assert web_server._CHAT_RUNS["run-c1"]["cancel_requested"] is True
    finally:
        web_server._CHAT_RUNS.pop("run-c1", None)


def test_cancel_before_the_agent_exists_is_honoured_when_it_appears(client):
    """Stop can land while the executor is still building the agent."""
    from hermes_cli import web_server

    web_server._CHAT_RUNS["run-c2"] = _run("sess-a")
    try:
        res = client.post(
            "/api/sessions/sess-a/chat/stream/cancel", json={"run_id": "run-c2"}
        )
        assert res.status_code == 200
        assert res.json()["cancelled"] is True
        assert web_server._CHAT_RUNS["run-c2"]["cancel_requested"] is True
    finally:
        web_server._CHAT_RUNS.pop("run-c2", None)


def test_cancel_of_a_finished_turn_is_a_noop(client):
    from hermes_cli import web_server

    agent = _FakeAgent()
    web_server._CHAT_RUNS["run-c3"] = _run("sess-a", agent=agent, done=True)
    try:
        res = client.post(
            "/api/sessions/sess-a/chat/stream/cancel", json={"run_id": "run-c3"}
        )
        assert res.status_code == 200
        assert res.json() == {"run_id": "run-c3", "cancelled": False, "done": True}
        assert agent.interrupts == 0
    finally:
        web_server._CHAT_RUNS.pop("run-c3", None)


def test_cancel_refuses_another_conversations_run(client):
    from hermes_cli import web_server

    agent = _FakeAgent()
    web_server._CHAT_RUNS["run-c4"] = _run("somebody-elses-session", agent=agent)
    try:
        res = client.post(
            "/api/sessions/sess-a/chat/stream/cancel", json={"run_id": "run-c4"}
        )
        assert res.status_code == 404
        assert agent.interrupts == 0
    finally:
        web_server._CHAT_RUNS.pop("run-c4", None)


def test_cancel_unknown_run_is_404(client):
    res = client.post(
        "/api/sessions/sess-a/chat/stream/cancel", json={"run_id": "nope"}
    )
    assert res.status_code == 404
