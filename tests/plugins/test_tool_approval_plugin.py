"""Tests for the tool-approval plugin.

Covers the bundled plugin at ``plugins/tool-approval/``:

  * pattern matching against ``approvals.tools`` (no-op when unset),
  * accept lets the call through, decline / timeout / missing approval
    channel block it (fail closed),
  * credential-looking argument values are redacted out of the prompt,
  * bypass (``--yolo`` / ``approvals.mode: off``) does NOT skip the gate
    unless ``approvals.tools_respect_bypass`` is set,
  * discovery through ``PluginManager.discover_and_load``,
  * the block directive shape the executor consumes
    (``get_pre_tool_call_block_message`` returns the message).
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest
import yaml


@pytest.fixture(autouse=True)
def _hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _write_config(home: Path, config: dict) -> None:
    (home / "config.yaml").write_text(yaml.safe_dump(config))
    # load_config caches on (mtime_ns, size); a fresh file per test is enough,
    # but clear explicitly so same-size rewrites within a test are seen.
    from hermes_cli import config as cfgmod
    getattr(cfgmod, "_CONFIG_CACHE", {}).clear()


def _load_plugin():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "tool-approval"
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.tool_approval",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.tool_approval"
    sys.modules["hermes_plugins.tool_approval"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin():
    return _load_plugin()


class TestPatternMatching:
    def test_noop_when_unconfigured(self, plugin, _hermes_home, monkeypatch):
        _write_config(_hermes_home, {})
        called = []
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed",
            lambda *a, **k: called.append(a) or ("accept", "approved"),
        )
        assert plugin._on_pre_tool_call("mcp_aws_api_call_aws", {"x": 1}) is None
        assert called == []

    def test_non_matching_tool_is_untouched(self, plugin, _hermes_home, monkeypatch):
        _write_config(_hermes_home, {"approvals": {"tools": ["mcp_aws_api_*"]}})
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed",
            lambda *a, **k: pytest.fail("must not prompt for a non-matching tool"),
        )
        assert plugin._on_pre_tool_call("terminal", {"command": "ls"}) is None

    def test_glob_matches_mcp_tool(self, plugin, _hermes_home, monkeypatch):
        _write_config(_hermes_home, {"approvals": {"tools": ["mcp_aws_api_*"]}})
        seen = {}

        def _consent(message, description, **kwargs):
            seen["message"] = message
            seen["description"] = description
            return "accept", "approved"

        monkeypatch.setattr("tools.approval.request_elicitation_consent_detailed", _consent)
        assert plugin._on_pre_tool_call(
            "mcp_aws_api_call_aws", {"cli_command": "aws sts get-caller-identity"}
        ) is None
        assert "aws sts get-caller-identity" in seen["message"]
        assert "mcp_aws_api_call_aws" in seen["description"]

    def test_matching_is_case_sensitive(self, plugin, _hermes_home, monkeypatch):
        _write_config(_hermes_home, {"approvals": {"tools": ["MCP_AWS_*"]}})
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed",
            lambda *a, **k: pytest.fail("case-insensitive match"),
        )
        assert plugin._on_pre_tool_call("mcp_aws_api_call_aws", {}) is None


class TestDecisions:
    @pytest.fixture(autouse=True)
    def _configured(self, _hermes_home):
        _write_config(_hermes_home, {"approvals": {"tools": ["mcp_aws_api_*"]}})

    def test_accept_allows_the_call(self, plugin, monkeypatch):
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed", lambda *a, **k: ("accept", "approved")
        )
        assert plugin._on_pre_tool_call("mcp_aws_api_call_aws", {}) is None

    @pytest.mark.parametrize(
        "decision",
        [("decline", "user_denied"), ("cancel", "timeout"), ("decline", "no_surface")],
    )
    def test_non_accept_blocks(self, plugin, monkeypatch, decision):
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed",
            lambda *a, **k: decision,
        )
        result = plugin._on_pre_tool_call("mcp_aws_api_call_aws", {})
        assert result["action"] == "block"
        assert "mcp_aws_api_call_aws" in result["message"]
        assert "NOT executed" in result["message"]

    def test_timeout_wording_distinguishes_no_answer(self, plugin, monkeypatch):
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed", lambda *a, **k: ("cancel", "timeout")
        )
        result = plugin._on_pre_tool_call("mcp_aws_api_call_aws", {})
        assert "did not respond in time" in result["message"]

    def test_consent_exception_fails_closed(self, plugin, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("no approval channel")

        monkeypatch.setattr("tools.approval.request_elicitation_consent_detailed", _boom)
        with pytest.raises(RuntimeError):
            # The approval helper itself is documented to fail closed rather
            # than raise; if it ever raises, the executor's try/except around
            # the hook turns it into "no block". Assert the raise is visible
            # so that contract change is caught here rather than silently
            # opening the gate.
            plugin._on_pre_tool_call("mcp_aws_api_call_aws", {})

    def test_timeout_passed_through(self, plugin, _hermes_home, monkeypatch):
        _write_config(
            _hermes_home,
            {"approvals": {"tools": ["mcp_aws_api_*"], "tools_timeout": 120}},
        )
        seen = {}

        def _consent(message, description, *, timeout_seconds=None, **kwargs):
            seen["timeout"] = timeout_seconds
            return "accept", "approved"

        monkeypatch.setattr("tools.approval.request_elicitation_consent_detailed", _consent)
        plugin._on_pre_tool_call("mcp_aws_api_call_aws", {})
        assert seen["timeout"] == 120

    def test_invalid_timeout_falls_back_to_default(self, plugin, _hermes_home, monkeypatch):
        _write_config(
            _hermes_home,
            {"approvals": {"tools": ["mcp_aws_api_*"], "tools_timeout": "soon"}},
        )
        seen = {}

        def _consent(message, description, *, timeout_seconds=None, **kwargs):
            seen["timeout"] = timeout_seconds
            return "accept", "approved"

        monkeypatch.setattr("tools.approval.request_elicitation_consent_detailed", _consent)
        plugin._on_pre_tool_call("mcp_aws_api_call_aws", {})
        assert seen["timeout"] is None


class TestScopeMemory:
    """Prior scope choices must skip the prompt (the "Always approve" fix)."""

    @pytest.fixture(autouse=True)
    def _configured(self, _hermes_home):
        _write_config(_hermes_home, {"approvals": {"tools": ["mcp_aws_api_*"]}})

    def test_session_approval_skips_the_prompt(self, plugin, monkeypatch):
        from tools import approval as amod

        monkeypatch.setenv("HERMES_SESSION_ID", "chat-1")
        amod.approve_session("chat-1", "mcp_aws_api_call_aws")
        try:
            monkeypatch.setattr(
                "tools.approval.request_elicitation_consent_detailed",
                lambda *a, **k: pytest.fail("approved tool must not prompt"),
            )
            assert plugin._on_pre_tool_call("mcp_aws_api_call_aws", {}) is None
        finally:
            amod.clear_session("chat-1")
            monkeypatch.delenv("HERMES_SESSION_ID", raising=False)

    def test_permanent_approval_skips_the_prompt(self, plugin, monkeypatch):
        from tools import approval as amod

        amod.approve_permanent("mcp_aws_api_call_aws")
        try:
            monkeypatch.setattr(
                "tools.approval.request_elicitation_consent_detailed",
                lambda *a, **k: pytest.fail("always-approved tool must not prompt"),
            )
            assert plugin._on_pre_tool_call("mcp_aws_api_call_aws", {}) is None
        finally:
            with amod._lock:
                amod._permanent_approved.discard("mcp_aws_api_call_aws")

    def test_unapproved_tool_still_prompts(self, plugin, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_ID", "chat-1")
        seen = {}

        def _consent(message, description, **kwargs):
            seen["persist_key"] = kwargs.get("persist_key")
            seen["persist_session_key"] = kwargs.get("persist_session_key")
            return "accept", "approved"

        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed", _consent
        )
        try:
            assert plugin._on_pre_tool_call("mcp_aws_api_call_aws", {}) is None
        finally:
            monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
        # The scope the choice will be remembered under is the stable session
        # id, and the pattern is the tool name.
        assert seen["persist_key"] == "mcp_aws_api_call_aws"
        assert seen["persist_session_key"] == "chat-1"


class TestBypass:
    def test_yolo_does_not_skip_the_gate_by_default(self, plugin, _hermes_home, monkeypatch):
        _write_config(
            _hermes_home, {"approvals": {"mode": "off", "tools": ["mcp_aws_api_*"]}}
        )
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed", lambda *a, **k: ("decline", "user_denied")
        )
        result = plugin._on_pre_tool_call("mcp_aws_api_call_aws", {})
        assert result["action"] == "block"

    def test_bypass_honoured_when_opted_in(self, plugin, _hermes_home, monkeypatch):
        _write_config(
            _hermes_home,
            {
                "approvals": {
                    "mode": "off",
                    "tools": ["mcp_aws_api_*"],
                    "tools_respect_bypass": True,
                }
            },
        )
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed",
            lambda *a, **k: pytest.fail("bypass should skip the prompt"),
        )
        assert plugin._on_pre_tool_call("mcp_aws_api_call_aws", {}) is None


class TestPromptRendering:
    def test_secret_values_are_redacted(self, plugin, _hermes_home, monkeypatch):
        _write_config(_hermes_home, {"approvals": {"tools": ["demo_*"]}})
        seen = {}

        def _consent(message, description, **kwargs):
            seen["message"] = message
            return "accept", "approved"

        monkeypatch.setattr("tools.approval.request_elicitation_consent_detailed", _consent)
        plugin._on_pre_tool_call(
            "demo_tool",
            {
                "api_key": "sk-live-should-not-appear",
                "nested": {"aws_secret_access_key": "hunter2"},
                "region": "ap-east-1",
            },
        )
        assert "sk-live-should-not-appear" not in seen["message"]
        assert "hunter2" not in seen["message"]
        assert "ap-east-1" in seen["message"]

    def test_long_payload_is_truncated(self, plugin, _hermes_home, monkeypatch):
        _write_config(_hermes_home, {"approvals": {"tools": ["demo_*"]}})
        seen = {}

        def _consent(message, description, **kwargs):
            seen["message"] = message
            return "accept", "approved"

        monkeypatch.setattr("tools.approval.request_elicitation_consent_detailed", _consent)
        plugin._on_pre_tool_call("demo_tool", {"body": "x" * 5000})
        assert len(seen["message"]) < 1200
        assert "truncated" in seen["message"]


class TestIntegration:
    def test_discovered_and_hook_registered(self, _hermes_home):
        _write_config(
            _hermes_home,
            {"plugins": {"enabled": ["tool-approval"]}, "approvals": {"tools": []}},
        )
        from hermes_cli import plugins as pmod

        mgr = pmod.PluginManager()
        mgr.discover_and_load()
        loaded = mgr._plugins["tool-approval"]
        assert loaded.enabled, loaded.error
        assert "pre_tool_call" in loaded.hooks_registered

    def test_block_reaches_the_executor_helper(self, _hermes_home, monkeypatch):
        """The directive shape must be what get_pre_tool_call_block_message reads."""
        _write_config(
            _hermes_home,
            {
                "plugins": {"enabled": ["tool-approval"]},
                "approvals": {"tools": ["mcp_aws_api_*"]},
            },
        )
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent_detailed", lambda *a, **k: ("decline", "user_denied")
        )
        from hermes_cli import plugins as pmod

        mgr = pmod.PluginManager()
        mgr.discover_and_load()
        monkeypatch.setattr(pmod, "_plugin_manager", mgr, raising=False)
        message = pmod.get_pre_tool_call_block_message(
            "mcp_aws_api_call_aws", {"cli_command": "aws s3 ls"}
        )
        assert message is not None
        assert "requires user approval" in message
