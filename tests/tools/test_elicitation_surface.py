"""A prompt nobody could receive must not be reported as a refusal.

Live symptom this pins (hermes-systest, Telegram): every gated
``mcp_google_workspace_*`` call came back in 0.00s as "the user declined".
The user had not declined — nothing had been sent to them. The terminal
prompt path was reached on a thread with no session context and no tty, so
``input()`` read EOF and resolved to a denial instantly and silently. The
agent then apologised for a refusal that never happened, the user insisted
they had approved, and the real fault — a lost approval surface — left no log
line at all.

So the contract under test is two-part: the *decision* still fails closed
(never accept without a human), and the *reason* distinguishes "they said no"
from "we never asked".
"""

from __future__ import annotations

import logging
import threading

import pytest

from tools import approval as approval_mod
from tools.approval import (
    register_gateway_notify,
    request_elicitation_consent,
    request_elicitation_consent_detailed,
    reset_current_session_key,
    resolve_gateway_approval,
    set_current_session_key,
    unregister_gateway_notify,
)

SESSION = "agent:main:telegram:dm:12345:2139"


@pytest.fixture
def gateway_session(monkeypatch):
    """A gateway session with an approval surface attached, as the real
    gateway sets it up around ``run_conversation``."""
    monkeypatch.setattr(approval_mod, "_is_gateway_approval_context", lambda: True)
    token = set_current_session_key(SESSION)
    yield SESSION
    unregister_gateway_notify(SESSION)
    reset_current_session_key(token)


def _ask(timeout=5):
    return request_elicitation_consent_detailed(
        "mcp_google_workspace_list_calendars",
        "needs approval",
        timeout_seconds=timeout,
        surface="tool-approval",
    )


class TestUserDecisions:
    def test_button_press_approves(self, gateway_session):
        def notify(_data):
            threading.Timer(
                0.05, resolve_gateway_approval, args=(SESSION, "once")
            ).start()

        register_gateway_notify(SESSION, notify)
        assert _ask() == ("accept", "approved")

    def test_deny_is_reported_as_the_users_decision(self, gateway_session):
        def notify(_data):
            threading.Timer(
                0.05, resolve_gateway_approval, args=(SESSION, "deny")
            ).start()

        register_gateway_notify(SESSION, notify)
        assert _ask() == ("decline", "user_denied")

    def test_no_answer_is_a_timeout_not_a_refusal(self, gateway_session):
        register_gateway_notify(SESSION, lambda _data: None)
        assert _ask(timeout=1) == ("cancel", "timeout")


class TestOurFailures:
    def test_undelivered_prompt_is_not_a_refusal(self, gateway_session):
        def notify(_data):
            raise RuntimeError("telegram send failed")

        register_gateway_notify(SESSION, notify)
        assert _ask() == ("decline", "undeliverable")

    def test_missing_surface_on_a_gateway_session(self, gateway_session):
        # No notify_cb registered: the session exists but nothing can prompt.
        assert _ask() == ("decline", "no_surface")

    def test_context_loss_reads_as_no_surface_not_user_denied(
        self, monkeypatch, caplog,
    ):
        """The production symptom, reproduced.

        A worker thread that starts without the agent turn's ContextVars sees
        no gateway platform and no session key. Before, that landed on the
        terminal prompt, hit EOF on stdin, and returned "the user declined" in
        microseconds with nothing logged.
        """
        monkeypatch.setattr(approval_mod, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_mod, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval_mod, "_stdin_is_a_tty", lambda: False)
        monkeypatch.setattr(
            approval_mod,
            "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail(
                "must not prompt a terminal nobody is watching"
            ),
        )
        out: list[tuple[str, str]] = []
        with caplog.at_level(logging.ERROR, logger="tools.approval"):
            worker = threading.Thread(target=lambda: out.append(_ask()))
            worker.start()
            worker.join(timeout=5)

        assert out == [("decline", "no_surface")]
        # The fault has to be findable afterwards: the old path was silent,
        # which is why this took a log-archaeology session to explain.
        assert any(
            "no approval surface" in record.message for record in caplog.records
        )

    def test_interactive_terminal_still_prompts(self, monkeypatch):
        """Guard the narrowing: a real CLI session must still be asked."""
        monkeypatch.setattr(approval_mod, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_mod, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(
            approval_mod, "prompt_dangerous_approval", lambda *a, **k: "once"
        )
        assert _ask() == ("accept", "approved")


class TestBackCompat:
    def test_plain_entry_point_still_returns_a_bare_decision(self, gateway_session):
        """MCP elicitation callers keep the 3-value contract."""
        assert request_elicitation_consent(
            "tool", "desc", timeout_seconds=1,
        ) == "decline"


class TestAgentFacingMessage:
    """What the model is told decides what the user is told."""

    @staticmethod
    def _message(reason: str) -> str:
        import importlib.util
        import pathlib
        import sys

        path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "plugins"
            / "tool-approval"
            / "__init__.py"
        )
        spec = importlib.util.spec_from_file_location(
            "hermes_plugins.tool_approval_msg", path,
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod._block_message("mcp_google_workspace_get_events", reason)

    def test_user_denial_says_so(self):
        assert "declined" in self._message("user_denied")

    @pytest.mark.parametrize("reason", ["no_surface", "undeliverable", "error"])
    def test_our_failures_do_not_blame_the_user(self, reason):
        message = self._message(reason)
        assert "did NOT decline" in message
        assert "the user declined" not in message
        assert "NOT executed" in message

    def test_timeout_keeps_its_own_wording(self):
        assert "did not respond in time" in self._message("timeout")
