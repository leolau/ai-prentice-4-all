"""Tests for skills/creative/figma-write/scripts/*.sh"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "creative"
    / "figma-write"
    / "scripts"
)
WRITE = SCRIPTS_DIR / "figma_write.sh"
LOGIN = SCRIPTS_DIR / "figma_login.sh"
BASH = shutil.which("bash") or "/bin/bash"

CLAUDE_STUB = """#!/usr/bin/env bash
if [ "$1" = "mcp" ] && [ "$2" = "list" ]; then
  echo "figma: https://mcp.figma.com/mcp (HTTP) - {status}"
  exit 0
fi
if [ "$1" = "mcp" ]; then
  echo "mcp $*"
  exit 0
fi
echo "ARGS: $*"
echo "BASE_URL: ${{ANTHROPIC_BASE_URL:-}}"
echo "TOKEN: ${{ANTHROPIC_AUTH_TOKEN:-}}"
echo "MODEL: ${{ANTHROPIC_MODEL:-}}"
"""


@pytest.fixture
def bin_dir(tmp_path):
    """A PATH containing only the coreutils we need plus a stubbed `claude`."""
    d = tmp_path / "bin"
    d.mkdir()
    for tool in (
        "bash",
        "timeout",
        "grep",
        "sed",
        "cat",
        "curl",
        "sleep",
        "tmux",
        "env",
    ):
        found = shutil.which(tool)
        if found:
            (d / tool).symlink_to(found)
    return d


def _write_claude(bin_dir: Path, status: str = "Connected") -> None:
    stub = bin_dir / "claude"
    stub.write_text(CLAUDE_STUB.format(status=status))
    stub.chmod(0o755)


def _run(script: Path, *args: str, bin_dir: Path, **env_extra):
    env = {"PATH": str(bin_dir), "HOME": str(bin_dir.parent)}
    env.update(env_extra)
    return subprocess.run(
        [BASH, str(script), *args], capture_output=True, text=True, env=env, timeout=60
    )


class TestFigmaWrite:
    def test_missing_prompt_exits_2(self, bin_dir):
        _write_claude(bin_dir)
        res = subprocess.run(
            [BASH, str(WRITE)],
            capture_output=True,
            text=True,
            env={"PATH": str(bin_dir)},
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        assert res.returncode == 2

    def test_missing_client_exits_127(self, bin_dir):
        res = _run(WRITE, "draw a frame", bin_dir=bin_dir)
        assert res.returncode == 127
        assert "claude-code" in res.stderr

    def test_missing_backend_exits_78(self, bin_dir):
        _write_claude(bin_dir)
        res = _run(WRITE, "draw a frame", bin_dir=bin_dir)
        assert res.returncode == 78
        assert "model backend" in res.stderr

    def test_unauthenticated_server_exits_77(self, bin_dir):
        _write_claude(bin_dir, status="! Needs authentication")
        res = _run(WRITE, "draw a frame", bin_dir=bin_dir, DEEPSEEK_API_KEY="sk-test")
        assert res.returncode == 77
        assert "figma_login.sh" in res.stderr

    def test_deepseek_fallback_and_tool_scope(self, bin_dir):
        _write_claude(bin_dir)
        res = _run(
            WRITE, "add a frame to <url>", bin_dir=bin_dir, DEEPSEEK_API_KEY="sk-test"
        )
        assert res.returncode == 0, res.stderr
        assert "add a frame to <url>" in res.stdout
        # Only the figma MCP server is unlocked; no repo/file tools.
        assert "--allowedTools mcp__figma" in res.stdout
        assert "BASE_URL: https://api.deepseek.com/anthropic" in res.stdout
        assert "TOKEN: sk-test" in res.stdout
        assert "MODEL: deepseek-chat" in res.stdout

    def test_existing_anthropic_creds_are_not_overridden(self, bin_dir):
        _write_claude(bin_dir)
        res = _run(
            WRITE,
            "add a frame",
            bin_dir=bin_dir,
            DEEPSEEK_API_KEY="sk-test",
            ANTHROPIC_AUTH_TOKEN="sk-ant",
        )
        assert res.returncode == 0, res.stderr
        assert "TOKEN: sk-ant" in res.stdout
        assert "BASE_URL: " in res.stdout

    def test_timeout_flag_is_consumed(self, bin_dir):
        _write_claude(bin_dir)
        res = _run(
            WRITE, "--timeout", "5", "draw", bin_dir=bin_dir, DEEPSEEK_API_KEY="sk-test"
        )
        assert res.returncode == 0, res.stderr
        assert "ARGS: -p draw" in res.stdout
        assert "--timeout" not in res.stdout


class TestFigmaLogin:
    def test_no_subcommand_prints_usage(self, bin_dir):
        _write_claude(bin_dir)
        res = _run(LOGIN, bin_dir=bin_dir)
        assert res.returncode == 2
        assert "figma_login.sh start" in res.stdout

    def test_complete_rejects_non_loopback_url(self, bin_dir):
        _write_claude(bin_dir)
        res = _run(
            LOGIN, "complete", "https://example.com/callback?code=x", bin_dir=bin_dir
        )
        assert res.returncode == 2
        assert "localhost" in res.stderr

    def test_status_reports_registered_server(self, bin_dir):
        _write_claude(bin_dir)
        res = _run(LOGIN, "status", bin_dir=bin_dir)
        assert res.returncode == 0
        assert "mcp.figma.com/mcp" in res.stdout

    def test_status_fails_when_unregistered(self, bin_dir):
        stub = bin_dir / "claude"
        stub.write_text("#!/usr/bin/env bash\nexit 0\n")
        stub.chmod(0o755)
        res = _run(LOGIN, "status", bin_dir=bin_dir)
        assert res.returncode == 1
        assert "not registered" in res.stderr


def test_scripts_are_executable():
    for script in (WRITE, LOGIN):
        assert os.access(script, os.X_OK), script


def test_skill_description_within_limit():
    import re

    text = (SCRIPTS_DIR.parent / "SKILL.md").read_text()
    match = re.search(r"^description: (.*)$", text, re.MULTILINE)
    assert match, "SKILL.md needs a description"
    assert len(match.group(1)) <= 60, len(match.group(1))
