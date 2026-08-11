"""FG-24 — the curated blocks must actually reach the model's context.

Asserting on ``MemoryStore.format_for_system_prompt`` in isolation proves
nothing about what the model sees: the store could be rendered perfectly and
never wired into the request.  These tests run a real ``AIAgent`` turn with the
provider call intercepted at ``_interruptible_api_call`` — the exact kwargs
handed to the provider — and assert on the ``system`` message in that payload.
Delete the injection in ``agent/system_prompt.py`` and they fail.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

for _name, _stub in (
    ("fire", types.SimpleNamespace(Fire=lambda *a, **k: None)),
    ("firecrawl", types.SimpleNamespace(Firecrawl=object)),
    ("fal_client", types.SimpleNamespace()),
):
    if _name not in sys.modules:
        sys.modules[_name] = _stub  # type: ignore[assignment]

import run_agent  # noqa: E402


def _response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(index=0, message=SimpleNamespace(
            role="assistant", content="ok", tool_calls=None, reasoning_content=None,
        ), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1, total_tokens=11),
        model="test-model",
    )


@pytest.fixture
def hermes_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "hermes-root"
    (root / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return root


def _profile(root: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"memory": {"memory_enabled": True, "user_profile_enabled": True}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _captured_system_prompt(monkeypatch: pytest.MonkeyPatch, **agent_kwargs) -> str:
    monkeypatch.setattr(run_agent, "get_tool_definitions", lambda **kwargs: [{
        "type": "function",
        "function": {"name": "t", "description": "t",
                     "parameters": {"type": "object", "properties": {}}},
    }])
    monkeypatch.setattr(run_agent, "check_toolset_requirements", lambda: {})
    captured: dict[str, dict[str, object]] = {}

    agent = run_agent.AIAgent(
        model="test-model",
        api_key="test-key",
        base_url="http://localhost:1234/v1",
        provider="openrouter",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_context_files=True,
        max_iterations=2,
        **agent_kwargs,
    )

    def _call(api_kwargs: dict[str, object]) -> SimpleNamespace:
        captured["kwargs"] = api_kwargs
        return _response()

    for attribute in ("_cleanup_task_resources", "_persist_session", "_save_trajectory"):
        monkeypatch.setattr(agent, attribute, lambda *a, **k: None)
    monkeypatch.setattr(agent, "_interruptible_api_call", _call)
    monkeypatch.setattr(agent, "_disable_streaming", True, raising=False)
    agent.run_conversation("hi")

    assert "kwargs" in captured, "the provider was never called"
    messages = captured["kwargs"]["messages"]
    assert isinstance(messages, list)
    first = messages[0]
    assert isinstance(first, dict)
    assert first["role"] == "system"
    return str(first["content"])


def test_participation_and_person_memory_reach_the_provider_payload(
    hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.memory_tool import MemoryStore

    home = _profile(hermes_root, "school", monkeypatch)
    head = MemoryStore(user_id="u_head", role="owner")
    head.load_from_disk()
    head.add("shared", "term ends on 19 July")
    alice = MemoryStore(user_id="u_alice", role="member")
    alice.load_from_disk()
    alice.add("memory", "alice marks year 9 on fridays")
    alice.add("user", "alice prefers bullet points")
    bob = MemoryStore(user_id="u_bob", role="member")
    bob.load_from_disk()
    bob.add("memory", "bob runs the chess club")
    assert (home / "memories" / "MEMORY.md").exists()

    system_prompt = _captured_system_prompt(
        monkeypatch, internal_user_id="u_alice", internal_user_role="member",
    )

    # Alice's own tiers and the profile-wide block are in the model's context…
    assert "alice marks year 9 on fridays" in system_prompt
    assert "alice prefers bullet points" in system_prompt
    assert "term ends on 19 July" in system_prompt
    # …and Bob's participation memory is not.
    assert "chess club" not in system_prompt
    # Shared knowledge is presented before the participation's own memory.
    assert system_prompt.index("term ends on 19 July") < system_prompt.index("year 9")


def test_another_persons_memory_never_reaches_the_payload(
    hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.memory_tool import MemoryStore

    _profile(hermes_root, "school", monkeypatch)
    alice = MemoryStore(user_id="u_alice", role="member")
    alice.load_from_disk()
    alice.add("memory", "alice marks year 9 on fridays")
    alice.add("user", "alice prefers bullet points")

    system_prompt = _captured_system_prompt(
        monkeypatch, internal_user_id="u_bob", internal_user_role="member",
    )
    assert "year 9" not in system_prompt
    assert "alice prefers bullet points" not in system_prompt


def test_working_memory_does_not_cross_profiles_in_the_payload(
    hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-person-company case, end to end through the agent."""
    from tools.memory_tool import MemoryStore

    _profile(hermes_root, "cto", monkeypatch)
    cto = MemoryStore(user_id="u_founder", role="owner")
    cto.load_from_disk()
    cto.add("memory", "deploys run from the release branch")
    cto.add("user", "the founder prefers terse answers")

    _profile(hermes_root, "cfo", monkeypatch)
    cfo = MemoryStore(user_id="u_founder", role="owner")
    cfo.load_from_disk()
    cfo.add("memory", "VAT returns are quarterly")

    system_prompt = _captured_system_prompt(
        monkeypatch, internal_user_id="u_founder", internal_user_role="owner",
    )
    assert "VAT returns are quarterly" in system_prompt
    assert "release branch" not in system_prompt
    # Identity followed the person into the other participation.
    assert "the founder prefers terse answers" in system_prompt


def test_unscoped_session_payload_carries_the_profile_files(
    hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _profile(hermes_root, "solo", monkeypatch)
    mem_dir = home / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("the build runs on python 3.11", encoding="utf-8")
    (mem_dir / "USER.md").write_text("prefers terse answers", encoding="utf-8")

    system_prompt = _captured_system_prompt(monkeypatch)
    assert "the build runs on python 3.11" in system_prompt
    assert "prefers terse answers" in system_prompt
    assert "SHARED MEMORY" not in system_prompt
