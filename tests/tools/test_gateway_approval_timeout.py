"""The gateway approval wait must honour a caller-supplied timeout.

``request_elicitation_consent(timeout_seconds=...)`` is documented as the
per-call deadline (``approvals.tools_timeout`` on the tool-approval plugin),
but the gateway branch used to drop it and always wait
``approvals.gateway_timeout`` — so the knob was a silent no-op on exactly the
surface it is documented for (Telegram/Slack/Discord).
"""

import time

import pytest

from tools import approval as approval_mod


@pytest.fixture(autouse=True)
def _clean_queues():
    with approval_mod._lock:
        approval_mod._gateway_queues.clear()
    yield
    with approval_mod._lock:
        approval_mod._gateway_queues.clear()


def test_await_gateway_decision_prefers_explicit_timeout(monkeypatch):
    monkeypatch.setattr(
        approval_mod, "_get_approval_config", lambda: {"gateway_timeout": 300}
    )

    started = time.monotonic()
    decision = approval_mod._await_gateway_decision(
        "sess-1", lambda data: None, {"command": "x", "description": "y"},
        timeout_seconds=1,
    )
    elapsed = time.monotonic() - started

    assert decision == {"resolved": False, "choice": None}
    assert elapsed < 10


def test_await_gateway_decision_falls_back_to_config(monkeypatch):
    monkeypatch.setattr(
        approval_mod, "_get_approval_config", lambda: {"gateway_timeout": 1}
    )

    started = time.monotonic()
    decision = approval_mod._await_gateway_decision(
        "sess-2", lambda data: None, {"command": "x", "description": "y"},
    )
    elapsed = time.monotonic() - started

    assert decision == {"resolved": False, "choice": None}
    assert elapsed < 10


def test_elicitation_consent_forwards_timeout_to_gateway(monkeypatch):
    captured = {}

    def _fake_await(session_key, notify_cb, approval_data, *, surface="gateway",
                    timeout_seconds=None):
        captured["timeout_seconds"] = timeout_seconds
        captured["surface"] = surface
        return {"resolved": True, "choice": "once"}

    monkeypatch.setattr(approval_mod, "get_current_session_key", lambda: "sess-3")
    monkeypatch.setattr(approval_mod, "_is_gateway_approval_context", lambda: True)
    monkeypatch.setattr(approval_mod, "_await_gateway_decision", _fake_await)
    with approval_mod._lock:
        approval_mod._gateway_notify_cbs["sess-3"] = lambda data: None

    try:
        decision = approval_mod.request_elicitation_consent(
            "aws sts get-caller-identity",
            "needs approval",
            timeout_seconds=42,
            surface="tool-approval",
        )
    finally:
        with approval_mod._lock:
            approval_mod._gateway_notify_cbs.pop("sess-3", None)

    assert decision == "accept"
    assert captured == {"timeout_seconds": 42, "surface": "tool-approval"}
