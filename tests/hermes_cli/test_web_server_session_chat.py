"""Endpoint tests for the dashboard session-chat + create-session routes.

Real ``SessionDB`` (SQLite under a throwaway ``HERMES_HOME``) exercises the DB
boundary; the C1 principal resolver and the one-brain turn are stubbed so the
tests stay hermetic and focus on the endpoint's contract:

* ``POST /api/sessions`` creates an owner-attributed session row (and rejects
  unsafe ids / conflicts);
* ``POST /api/sessions/{id}/chat`` loads the persisted history and forwards it
  **verbatim** to the shared one-brain runner (no synthetic message, no
  ephemeral system prompt), returns the assistant reply + usage, and 404s an
  unknown session / 400s an empty message.
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

    # Resolve to the enrolled owner without a Postgres principal store.
    owner = SimpleNamespace(user_id="root", display="Root Owner", role="owner", is_owner=True)

    async def _fake_principal(request, *, allow_as=False):
        return owner

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


@pytest.fixture
def capture_turn(monkeypatch):
    """Stub the one-brain runner, capturing what the endpoint forwards."""
    import gateway.session_chat as session_chat

    captured: dict = {}

    def _fake_turn(*, session_db, user_message, conversation_history, session_id=None, **kwargs):
        captured["session_db"] = session_db
        captured["user_message"] = user_message
        captured["conversation_history"] = conversation_history
        captured["session_id"] = session_id
        captured["kwargs"] = kwargs
        # Observe the approval surface as the agent thread would see it: the
        # streaming path binds a per-run approval session key and registers a
        # notify callback under it, which is exactly what the non-streaming
        # path omitted (→ the `no_surface` calendar bug).
        from tools import approval as _ap

        key = _ap.get_current_session_key(default="")
        captured["approval_session_key"] = key
        captured["surface_registered"] = key in _ap._gateway_notify_cbs
        captured["gateway_ctx"] = _ap._is_gateway_approval_context()
        return (
            {"final_response": f"echo:{user_message}", "session_id": session_id},
            {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        )

    monkeypatch.setattr(session_chat, "run_session_turn_sync", _fake_turn)
    return captured


def test_create_session_returns_id(client):
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"].startswith("home_")
    assert data["source"] == "agent_home"


def test_create_session_rejects_unsafe_id(client):
    resp = client.post("/api/sessions", json={"id": "../etc/passwd"})
    assert resp.status_code == 400


def test_create_session_conflict(client):
    resp = client.post("/api/sessions", json={"id": "dup-1"})
    assert resp.status_code == 200
    again = client.post("/api/sessions", json={"id": "dup-1"})
    assert again.status_code == 409


def test_chat_empty_message_rejected(client, capture_turn):
    client.post("/api/sessions", json={"id": "s-empty"})
    resp = client.post("/api/sessions/s-empty/chat", json={"message": "   "})
    assert resp.status_code == 400
    assert "message" not in capture_turn  # runner never invoked


def test_chat_unknown_session_404(client, capture_turn):
    resp = client.post("/api/sessions/does-not-exist/chat", json={"message": "hi"})
    assert resp.status_code == 404
    assert "user_message" not in capture_turn


def test_chat_roundtrip_forwards_history_verbatim(client, capture_turn):
    import hermes_state

    # Seed a real alternating transcript in the throwaway SessionDB.
    db = hermes_state.SessionDB()
    try:
        db.ensure_session("s-hist", source="agent_home")
        db.append_message("s-hist", "user", "first")
        db.append_message("s-hist", "assistant", "second")
        expected = db.get_messages_as_conversation("s-hist")
    finally:
        db.close()

    resp = client.post("/api/sessions/s-hist/chat", json={"message": "third"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == {"role": "assistant", "content": "echo:third"}
    assert data["usage"] == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert data["session_id"] == "s-hist"

    # The endpoint forwarded the persisted history unchanged (cache/alternation
    # safety: no synthetic user turn appended, message passed separately).
    assert capture_turn["conversation_history"] == expected
    assert capture_turn["user_message"] == "third"
    assert capture_turn["session_id"] == "s-hist"
    # No ephemeral system prompt is smuggled through kwargs.
    assert "ephemeral_system_prompt" not in capture_turn["kwargs"]


def test_chat_stream_roundtrip_and_attaches_approval_surface(client, capture_turn):
    """The streamed turn returns SSE and — the fix — attaches a per-run approval
    surface so a gated tool can prompt instead of failing closed (`no_surface`).
    """
    import hermes_state

    db = hermes_state.SessionDB()
    try:
        db.ensure_session("s-stream", source="agent_home")
        db.append_message("s-stream", "user", "first")
        expected = db.get_messages_as_conversation("s-stream")
    finally:
        db.close()

    resp = client.post("/api/sessions/s-stream/chat/stream", json={"message": "hi"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-hermes-session-id"] == "s-stream"

    body = resp.text
    assert "event: assistant.completed" in body
    assert "echo:hi" in body
    assert "event: run.completed" in body
    assert "event: done" in body

    # History forwarded verbatim (cache/alternation safety), and the message
    # passed separately — same contract as the non-streaming endpoint.
    assert capture_turn["conversation_history"] == expected
    assert capture_turn["user_message"] == "hi"
    # The surface: a per-run key was bound AND a notify_cb registered under it
    # while the turn ran. This is the exact state whose absence returned
    # `no_surface` for the calendar tool in agent-home.
    assert capture_turn["approval_session_key"].startswith("run_")
    assert capture_turn["surface_registered"] is True
    assert capture_turn["gateway_ctx"] is True
    # Provider deltas are wired through to the browser.
    assert callable(capture_turn["kwargs"].get("stream_delta_callback"))

    # …and it is torn down after the turn (no leaked global callback).
    from tools import approval as _ap

    assert capture_turn["approval_session_key"] not in _ap._gateway_notify_cbs


def test_chat_stream_emits_every_delta_in_order(client, monkeypatch):
    """Regression for "only shows the first couple of characters then stopped":
    every provider delta the runner streams must appear as its own
    `assistant.delta` frame, in order, followed by the full completed content.
    (The truncation was a browser-side identity bug, but this pins the server
    contract the client depends on: one frame per delta, not just the first.)
    """
    import hermes_state
    import gateway.session_chat as session_chat

    db = hermes_state.SessionDB()
    try:
        db.ensure_session("s-multi", source="agent_home")
    finally:
        db.close()

    parts = ["I'm", " powered", " by", " GLM-5.2", "."]

    def _streaming_turn(*, session_db, user_message, conversation_history,
                        session_id=None, stream_delta_callback=None, **kwargs):
        for p in parts:
            stream_delta_callback(p)
        return (
            {"final_response": "".join(parts), "session_id": session_id},
            {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        )

    monkeypatch.setattr(session_chat, "run_session_turn_sync", _streaming_turn)

    resp = client.post("/api/sessions/s-multi/chat/stream", json={"message": "hi"})
    assert resp.status_code == 200
    body = resp.text

    # One delta frame per part, and they arrive in order.
    assert body.count("event: assistant.delta") == len(parts)
    positions = [body.index(f'"delta": "{p}"') for p in parts]
    assert positions == sorted(positions)
    # The completed frame carries the full concatenation.
    assert 'event: assistant.completed' in body
    assert "I'm powered by GLM-5.2." in body
    assert body.index("event: assistant.completed") > positions[-1]


def test_chat_stream_unknown_session_404(client, capture_turn):
    resp = client.post("/api/sessions/nope/chat/stream", json={"message": "hi"})
    assert resp.status_code == 404
    assert "user_message" not in capture_turn


def test_run_approval_normalizes_choice_and_resolves(client, monkeypatch):
    """The resolve route forwards the user's explicit choice to the existing
    gateway approval mechanism (mapping the UI 'approve' alias to 'once')."""
    from tools import approval as _ap

    calls: dict = {}

    def _fake_resolve(session_key, choice, resolve_all=False):
        calls["session_key"] = session_key
        calls["choice"] = choice
        calls["resolve_all"] = resolve_all
        return 1

    monkeypatch.setattr(_ap, "resolve_gateway_approval", _fake_resolve)

    resp = client.post("/v1/runs/run_abc/approval", json={"choice": "approve"})
    assert resp.status_code == 200
    assert resp.json() == {
        "object": "hermes.run.approval",
        "run_id": "run_abc",
        "choice": "once",
        "resolved": 1,
    }
    assert calls == {"session_key": "run_abc", "choice": "once", "resolve_all": False}


def test_run_approval_rejects_invalid_choice(client, monkeypatch):
    from tools import approval as _ap

    def _boom(*a, **k):  # must never be reached
        raise AssertionError("resolve_gateway_approval called for invalid choice")

    monkeypatch.setattr(_ap, "resolve_gateway_approval", _boom)
    resp = client.post("/v1/runs/run_abc/approval", json={"choice": "maybe"})
    assert resp.status_code == 400
