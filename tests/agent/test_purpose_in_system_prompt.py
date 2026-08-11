"""FG-29: the purpose block is load-bearing in real prompt assembly.

Not a unit test of the renderer — that is
``tests/agent/test_purpose_prompt_placement.py``. This one runs the actual
``build_system_prompt_parts`` and asserts where the block lands: the stable tier
directly after identity, the participant block in the volatile tier, and an
operational goal in neither.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agent.purpose_prompt import PurposeGoal, PurposeSnapshot, write_snapshot
from agent.system_prompt import build_system_prompt_parts

IDENTITY_MARKER = "You are Hermes"


def _make_agent(**overrides):
    base = dict(
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        _internal_user_id=None,
        model="",
        provider="",
        platform="",
        pass_session_id=False,
        session_id="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _parts(agent):
    with (
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_nous_subscription_prompt", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
    ):
        return build_system_prompt_parts(agent)


def test_entity_goal_lands_in_the_stable_tier_right_after_identity() -> None:
    write_snapshot(
        PurposeSnapshot(
            stable=(
                PurposeGoal(
                    goal_id="ent-1",
                    tier="entity",
                    title="Keep the school open",
                    description="Enrolment above 420 without borrowing",
                ),
            )
        )
    )
    stable = _parts(_make_agent())["stable"]
    assert "[PURPOSE]" in stable
    assert "Keep the school open" in stable
    # Immediately after identity: nothing between the identity block and the
    # purpose block.
    assert stable.index("[PURPOSE]") > stable.index(IDENTITY_MARKER)
    between = stable[
        stable.index(IDENTITY_MARKER) : stable.index("[PURPOSE]")
    ]
    assert between.count("\n\n") <= 2


def test_a_participant_goal_lands_in_the_volatile_tier_only() -> None:
    write_snapshot(
        PurposeSnapshot(
            participants={
                "teacher": (
                    PurposeGoal(
                        goal_id="p-1",
                        tier="participant",
                        title="Get Y7 through mocks",
                    ),
                )
            }
        )
    )
    parts = _parts(_make_agent(_internal_user_id="teacher"))
    assert "Get Y7 through mocks" in parts["volatile"]
    assert "Get Y7 through mocks" not in parts["stable"]

    # Someone else's session does not see it at all.
    other = _parts(_make_agent(_internal_user_id="parent"))
    assert "Get Y7 through mocks" not in other["volatile"]


def test_no_snapshot_means_no_block_at_all() -> None:
    parts = _parts(_make_agent())
    assert "[PURPOSE]" not in parts["stable"]
    assert "[PARTICIPANT PURPOSE]" not in parts["volatile"]
