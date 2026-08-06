"""Approval-surface tests for the api_server session-chat path (Option A).

Agent-home chat runs as ``platform=api_server`` and previously bound the
session context but registered no approval notify callback. Any tool gated by
``approvals.tools`` (e.g. Google Workspace calendar) therefore hit the
tool-approval gate, found no surface, and failed closed with ``no_surface`` —
the user was never asked (observed live: "Elicitation requested in gateway
session home_… but no notify_cb is registered — failing closed").

These tests lock in the fix: ``_run_agent`` now registers a per-run approval
surface when given a notify callback, and the streaming chat endpoint forwards
the prompt as an ``approval.request`` SSE event resolvable via
``POST /v1/runs/{run_id}/approval``. They exercise the real approval machinery
(``tools.approval``), not mocks of it, and cover both the happy path and the
fail-closed invariants (missing surface, denial).
"""

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB
from tools import approval as approval_mod


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


@pytest.fixture
def adapter(session_db):
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = session_db
    return adapter


class _ConsentAgent:
    """A fake agent whose turn asks for elicitation consent — i.e. it exercises
    the exact code path an approvals.tools-gated tool takes."""

    session_prompt_tokens = 0
    session_completion_tokens = 0
    session_total_tokens = 0

    def __init__(self, session_id, observed, *, timeout_seconds=5):
        self.session_id = session_id
        self._observed = observed
        self._timeout = timeout_seconds

    def run_conversation(self, user_message, conversation_history, task_id):
        from tools.approval import (
            get_current_session_key,
            request_elicitation_consent_detailed,
        )

        self._observed["session_key_at_tool_time"] = get_current_session_key(default="")
        decision, reason = request_elicitation_consent_detailed(
            "list_calendars",
            "Tool 'list_calendars' requires your approval before it runs.",
            timeout_seconds=self._timeout,
            surface="tool-approval",
        )
        self._observed["decision"] = decision
        self._observed["reason"] = reason
        return {"final_response": f"decision={decision}", "session_id": self.session_id}


@pytest.mark.asyncio
async def test_run_agent_registers_surface_and_resolves_approved(adapter, monkeypatch):
    """With a notify callback, the gated tool prompts the caller and an
    ``once`` decision unblocks the turn as approved — no ``no_surface``."""
    observed = {}
    captured = {}
    run_key = "run_test_approved"

    def fake_create_agent(**kwargs):
        return _ConsentAgent(kwargs.get("session_id"), observed)

    monkeypatch.setattr(adapter, "_create_agent", fake_create_agent)

    def notify_cb(approval_data):
        captured["approval_data"] = approval_data
        # The entry is enqueued before notify fires, so resolving here (in the
        # agent thread) sets the event the wait loop is about to check.
        approval_mod.resolve_gateway_approval(run_key, "once")

    result, _usage = await adapter._run_agent(
        user_message="what's on my calendar?",
        conversation_history=[],
        session_id="home_chat_1",
        gateway_session_key="home_chat_1",
        gateway_notify_cb=notify_cb,
        approval_session_key=run_key,
    )

    assert observed["decision"] == "accept"
    assert observed["reason"] == "approved"
    # The tool saw the per-run approval key, isolating it from other turns.
    assert observed["session_key_at_tool_time"] == run_key
    assert captured["approval_data"]["command"] == "list_calendars"
    assert result["final_response"] == "decision=accept"
    # The surface must not outlive the run.
    with approval_mod._lock:
        assert run_key not in approval_mod._gateway_notify_cbs


@pytest.mark.asyncio
async def test_run_agent_without_surface_fails_closed(adapter, monkeypatch):
    """Reproduces the original bug: no notify callback → the gated tool fails
    closed with ``no_surface`` (the user is never silently bypassed)."""
    observed = {}

    def fake_create_agent(**kwargs):
        return _ConsentAgent(kwargs.get("session_id"), observed, timeout_seconds=2)

    monkeypatch.setattr(adapter, "_create_agent", fake_create_agent)

    result, _usage = await adapter._run_agent(
        user_message="what's on my calendar?",
        conversation_history=[],
        session_id="home_chat_2",
        gateway_session_key="home_chat_2",
    )

    assert observed["decision"] == "decline"
    assert observed["reason"] == "no_surface"
    assert result["final_response"] == "decision=decline"


@pytest.mark.asyncio
async def test_run_agent_surface_denial_is_user_denied(adapter, monkeypatch):
    """A ``deny`` decision is reported as an explicit refusal — distinct from
    ``no_surface`` / ``timeout`` — so the model doesn't mislabel it."""
    observed = {}
    run_key = "run_test_denied"

    def fake_create_agent(**kwargs):
        return _ConsentAgent(kwargs.get("session_id"), observed)

    monkeypatch.setattr(adapter, "_create_agent", fake_create_agent)

    def notify_cb(approval_data):
        approval_mod.resolve_gateway_approval(run_key, "deny")

    await adapter._run_agent(
        user_message="delete my calendar",
        conversation_history=[],
        session_id="home_chat_3",
        gateway_session_key="home_chat_3",
        gateway_notify_cb=notify_cb,
        approval_session_key=run_key,
    )

    assert observed["decision"] == "decline"
    assert observed["reason"] == "user_denied"
    with approval_mod._lock:
        assert run_key not in approval_mod._gateway_notify_cbs


def _stream_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app.router.add_post(
        "/api/sessions/{session_id}/chat/stream", adapter._handle_session_chat_stream
    )
    app.router.add_post("/v1/runs/{run_id}/approval", adapter._handle_run_approval)
    return app


@pytest.mark.asyncio
async def test_session_chat_stream_emits_approval_request_and_resolves(adapter, session_db, monkeypatch):
    """End-to-end: the streamed turn surfaces ``approval.request`` carrying a
    run_id, the client resolves it via ``POST /v1/runs/{run_id}/approval``, and
    the blocked turn resumes and completes. This is the whole Option A path with
    the real approval machinery in the loop."""
    session_id = session_db.create_session("home_stream_session", "api_server")
    observed = {}

    def fake_create_agent(**kwargs):
        return _ConsentAgent(kwargs.get("session_id"), observed, timeout_seconds=10)

    monkeypatch.setattr(adapter, "_create_agent", fake_create_agent)

    app = _stream_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            f"/api/sessions/{session_id}/chat/stream",
            json={"message": "what's on my calendar today?"},
        )
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")

        # Read the stream until the approval prompt arrives, capturing run_id.
        run_id = None
        buffer = ""
        approval_event = None
        while run_id is None:
            chunk = await asyncio.wait_for(resp.content.read(512), timeout=10)
            if not chunk:
                break
            buffer += chunk.decode("utf-8")
            for block in buffer.split("\n\n"):
                if "event: approval.request" in block:
                    for line in block.splitlines():
                        if line.startswith("data: "):
                            approval_event = json.loads(line[len("data: "):])
                            run_id = approval_event["run_id"]
        assert run_id is not None, buffer
        assert approval_event["command"] == "list_calendars"
        assert "once" in approval_event["choices"]

        # Resolve the pending approval — this unblocks the waiting agent turn.
        approve = await cli.post(f"/v1/runs/{run_id}/approval", json={"choice": "once"})
        assert approve.status == 200, await approve.text()
        approve_body = await approve.json()
        assert approve_body["resolved"] == 1

        # The turn resumes and the stream completes.
        rest = buffer
        while True:
            chunk = await asyncio.wait_for(resp.content.read(512), timeout=10)
            if not chunk:
                break
            rest += chunk.decode("utf-8")

    assert observed["decision"] == "accept"
    assert observed["reason"] == "approved"
    assert "event: assistant.completed" in rest
    assert "event: run.completed" in rest
    assert "event: done" in rest
    # The approval session must be cleaned up after the run.
    assert session_id not in adapter._run_approval_sessions
    assert run_id not in adapter._run_approval_sessions
