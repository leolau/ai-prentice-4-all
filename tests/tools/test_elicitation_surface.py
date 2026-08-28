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


class TestScopePersistence:
    """With a persist_key, the user's scope choice must be remembered.

    Live symptom (agent-home): clicking "Always approve" on an
    ``approvals.tools`` gate asked again on the very next call — the choice
    was collapsed to a bare accept and dropped. These pin the contract the
    tool-approval plugin now relies on.
    """

    @pytest.fixture(autouse=True)
    def _hermes_home(self, tmp_path, monkeypatch):
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        from hermes_cli import config as cfgmod

        getattr(cfgmod, "_CONFIG_CACHE", {}).clear()
        yield
        getattr(cfgmod, "_CONFIG_CACHE", {}).clear()

    def _ask_with_scope(self, tool: str, choice: str, scope_key: str):
        def notify(_data):
            threading.Timer(
                0.05, resolve_gateway_approval, args=(SESSION, choice)
            ).start()

        register_gateway_notify(SESSION, notify)
        return request_elicitation_consent_detailed(
            tool,
            "needs approval",
            timeout_seconds=5,
            surface="tool-approval",
            persist_key=tool,
            persist_session_key=scope_key,
        )

    def test_always_is_permanent(self, gateway_session):
        out = self._ask_with_scope(
            "mcp_canva_read_design", "always", "chat-session-1"
        )
        assert out == ("accept", "approved")
        assert approval_mod.is_approved("chat-session-1", "mcp_canva_read_design")
        # Permanent means it survives a fresh process, via config.
        import yaml

        from hermes_constants import get_hermes_home

        cfg = yaml.safe_load((get_hermes_home() / "config.yaml").read_text()) or {}
        assert "mcp_canva_read_design" in (cfg.get("command_allowlist") or [])

    def test_session_is_remembered_for_the_stable_key_only(self, gateway_session):
        out = self._ask_with_scope(
            "mcp_canva_list_designs", "session", "chat-session-1"
        )
        assert out == ("accept", "approved")
        assert approval_mod.is_approved("chat-session-1", "mcp_canva_list_designs")
        assert not approval_mod.is_approved("another-session", "mcp_canva_list_designs")

    def test_once_persists_nothing(self, gateway_session):
        out = self._ask_with_scope(
            "mcp_canva_export_design", "once", "chat-session-1"
        )
        assert out == ("accept", "approved")
        assert not approval_mod.is_approved(
            "chat-session-1", "mcp_canva_export_design"
        )

    def test_no_persist_key_keeps_per_call_semantics(self, gateway_session):
        def notify(_data):
            threading.Timer(
                0.05, resolve_gateway_approval, args=(SESSION, "always")
            ).start()

        register_gateway_notify(SESSION, notify)
        out = request_elicitation_consent_detailed(
            "mcp_canva_share_design",
            "needs approval",
            timeout_seconds=5,
            surface="tool-approval",
        )
        assert out == ("accept", "approved")
        # Server-driven elicitation stays per-call: nothing remembered.
        assert not approval_mod.is_approved(
            "chat-session-1", "mcp_canva_share_design"
        )
        assert not approval_mod.is_approved(SESSION, "mcp_canva_share_design")


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


class TestSurfaceDetection:
    """Why the live prompts went to the terminal instead of Telegram.

    ``HERMES_SESSION_PLATFORM`` and the approval session key are bound by
    different mechanisms, so a call can hold the session key (its notify
    callback live and answering other prompts in the same turn) while the
    platform flag reads empty. The gate then concluded "not a gateway
    session", prompted the server's stdin, and denied itself — observed on
    hermes-systest, where a Telegram button resolved a terminal-command
    approval at 01:25:08 and 18s later a gated MCP call in that same session
    printed its prompt into the gateway's log file and auto-denied.
    """

    def test_registered_surface_beats_a_missing_platform_flag(self, monkeypatch):
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
        monkeypatch.setattr(approval_mod, "_get_session_platform", lambda: "")
        token = set_current_session_key(SESSION)
        try:
            assert approval_mod._is_gateway_approval_context() is False
            register_gateway_notify(SESSION, lambda _data: None)
            assert approval_mod._is_gateway_approval_context() is True
        finally:
            unregister_gateway_notify(SESSION)
            reset_current_session_key(token)

    def test_another_sessions_surface_does_not_count(self, monkeypatch):
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
        monkeypatch.setattr(approval_mod, "_get_session_platform", lambda: "")
        register_gateway_notify("agent:main:telegram:dm:12345:9999", lambda _d: None)
        token = set_current_session_key(SESSION)
        try:
            assert approval_mod._is_gateway_approval_context() is False
        finally:
            unregister_gateway_notify("agent:main:telegram:dm:12345:9999")
            reset_current_session_key(token)

    def test_cron_is_still_never_a_gateway_context(self, monkeypatch):
        """Cron must keep failing to the config-governed path.

        A cron job has a delivery route but nobody watching it; treating its
        callback as an approval surface would block the job for the full
        timeout on every gated call.
        """
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        monkeypatch.setattr(approval_mod, "_get_session_platform", lambda: "telegram")
        token = set_current_session_key(SESSION)
        register_gateway_notify(SESSION, lambda _data: None)
        try:
            assert approval_mod._is_gateway_approval_context() is False
        finally:
            unregister_gateway_notify(SESSION)
            reset_current_session_key(token)

    def test_no_session_no_surface(self, monkeypatch):
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
        monkeypatch.setattr(approval_mod, "_get_session_platform", lambda: "")
        token = set_current_session_key("")
        try:
            assert approval_mod._is_gateway_approval_context() is False
        finally:
            reset_current_session_key(token)

    def test_the_prompt_reaches_the_user_without_the_platform_flag(
        self, monkeypatch,
    ):
        """End to end: the live failure, with the platform flag missing."""
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
        monkeypatch.setattr(approval_mod, "_get_session_platform", lambda: "")
        monkeypatch.setattr(
            approval_mod,
            "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail("prompted the server's terminal"),
        )
        prompted: list[str] = []

        def notify(data):
            prompted.append(data["command"])
            threading.Timer(
                0.05, resolve_gateway_approval, args=(SESSION, "once")
            ).start()

        token = set_current_session_key(SESSION)
        register_gateway_notify(SESSION, notify)
        try:
            assert _ask() == ("accept", "approved")
        finally:
            unregister_gateway_notify(SESSION)
            reset_current_session_key(token)
        assert prompted and "list_calendars" in prompted[0]


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
