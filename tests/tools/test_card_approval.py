"""A kanban worker's approvals are asked on its card, not refused in-process.

Live symptom this pins (Hetzner, card ``t_4ce5dfdb``): a dispatcher-spawned
worker (stdin on ``/dev/null``, no gateway notifier) hit ``approvals.tools``
for ``mcp_canva_get_design_pages``; the prompt read EOF and the log said "the
user declined". Nobody had been asked, and the card kept running with the
call refused. The contract: the call still fails closed, the ask is recorded
on the card for the project page to answer, and the human's answer is what
the next attempt honours.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from hermes_cli import kanban_db
from tools import approval as approval_mod
from tools.approval import (
    check_all_command_guards,
    request_elicitation_consent_detailed,
)

TOOL = "mcp_canva_create_upload_url"
COMMAND = "chmod 777 /tmp/card-approval-probe"


@pytest.fixture
def card(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    monkeypatch.setattr(approval_mod, "_stdin_is_a_tty", lambda: False)
    monkeypatch.setattr(
        approval_mod,
        "prompt_dangerous_approval",
        lambda *a, **k: pytest.fail("a card worker has no terminal to prompt"),
    )
    with kanban_db.connect_closing() as conn:
        task_id = kanban_db.create_task(conn, title="Build the deck in Canva")
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    return task_id


def _ask_tool():
    return request_elicitation_consent_detailed(
        f"{TOOL}\n{{}}",
        f"Tool '{TOOL}' requires your approval before it runs (approvals.tools).",
        timeout_seconds=5,
        surface="tool-approval",
        persist_key=TOOL,
    )


def _decide(task_id, key, approve):
    with kanban_db.connect_closing() as conn:
        return kanban_db.decide_task_approval(
            conn, task_id, key, approve=approve, actor="user:leo"
        )


def _pending(task_id):
    with kanban_db.connect_closing() as conn:
        return kanban_db.list_task_approvals(conn, [task_id]).get(task_id, [])


class TestGatedTool:
    def test_first_ask_is_recorded_on_the_card_not_refused(self, card):
        assert _ask_tool() == ("decline", "card_pending")
        pending = _pending(card)
        assert [p["key"] for p in pending] == [TOOL]
        assert pending[0]["detail"].startswith(TOOL)

    def test_a_repeat_ask_stays_one_pending_row(self, card):
        _ask_tool()
        assert _ask_tool() == ("decline", "card_pending")
        assert len(_pending(card)) == 1

    def test_the_humans_approval_lets_the_next_call_through(self, card):
        _ask_tool()
        assert _decide(card, TOOL, True) == TOOL
        assert _ask_tool() == ("accept", "approved")
        assert _pending(card) == []

    def test_the_humans_denial_is_reported_as_theirs(self, card):
        _ask_tool()
        _decide(card, TOOL, False)
        assert _ask_tool() == ("decline", "user_denied")

    def test_approval_is_scoped_to_its_card(self, card, monkeypatch):
        _ask_tool()
        _decide(card, TOOL, True)
        with kanban_db.connect_closing() as conn:
            other = kanban_db.create_task(conn, title="Another card")
        monkeypatch.setenv("HERMES_KANBAN_TASK", other)
        assert _ask_tool() == ("decline", "card_pending")

    def test_a_broken_board_is_an_error_not_a_refusal(self, card, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_missing")
        assert _ask_tool() == ("decline", "error")

    def test_without_a_key_there_is_nothing_to_record(self, card):
        """A server-driven elicitation has no stable key: no surface, never 'declined'.

        ``chat -q`` sets HERMES_INTERACTIVE for every worker; with stdin on
        /dev/null that is still nobody to ask.
        """
        assert request_elicitation_consent_detailed(
            "elicit", "server asks", timeout_seconds=5,
        ) == ("decline", "no_surface")
        assert _pending(card) == []


class TestDangerousCommand:
    @pytest.fixture(autouse=True)
    def _ask_mode(self, monkeypatch):
        monkeypatch.setenv("HERMES_EXEC_ASK", "1")
        monkeypatch.setattr(approval_mod, "_get_approval_mode", lambda: "manual")

    def test_pending_then_approved_on_the_card(self, card):
        first = check_all_command_guards(COMMAND, "local")
        assert first["approved"] is False
        assert first["outcome"] == "pending"
        assert "kanban_block" in first["message"]
        assert "needs_input" in first["message"]
        pending = _pending(card)
        assert len(pending) == 1
        assert pending[0]["detail"] == COMMAND

        _decide(card, pending[0]["key"], True)
        assert check_all_command_guards(COMMAND, "local")["approved"] is True

    def test_denied_on_the_card(self, card):
        check_all_command_guards(COMMAND, "local")
        _decide(card, _pending(card)[0]["key"], False)
        result = check_all_command_guards(COMMAND, "local")
        assert result["approved"] is False
        assert result["outcome"] == "denied"
        assert "denied this on this card" in result["message"]


def _load_tool_approval_plugin():
    path = Path(__file__).resolve().parents[2] / "plugins" / "tool-approval" / "__init__.py"
    spec = importlib.util.spec_from_file_location("tool_approval_card_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_card_pending_tells_the_worker_to_block_not_apologise():
    message = _load_tool_approval_plugin()._block_message(TOOL, "card_pending")
    assert "declined" not in message
    assert "kanban_block" in message
    assert f"Approval needed: {TOOL}" in message
    assert "saved" in message  # no credential workaround
