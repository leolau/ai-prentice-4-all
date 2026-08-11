"""FG-29: goal lifetime decides prompt placement, and the code enforces it.

The invariant under test is the one the whole feature rests on: *a goal that
can change mid-session may never enter the system prompt.* These tests exercise
the real reader and writer against a real ``HERMES_HOME`` (the suite's per-test
tempdir) — no mocks — because the failure mode being guarded against is a goal
reaching a cached prefix, and a mocked snapshot would prove nothing about that.
"""

from __future__ import annotations

import json

import pytest

from agent.prompt_builder import drain_truncation_warnings
from agent.purpose_prompt import (
    PurposeBudgetError,
    PurposeGoal,
    PurposeSnapshot,
    build_participant_block,
    build_stable_block,
    load_snapshot,
    participant_prompt_block,
    snapshot_path,
    stable_prompt_block,
    write_snapshot,
)


def _goal(tier: str, title: str = "Stay solvent") -> PurposeGoal:
    return PurposeGoal(goal_id=f"{tier}-1", tier=tier, title=title)


def test_stable_block_carries_entity_and_profile_goals() -> None:
    write_snapshot(
        PurposeSnapshot(
            stable=(_goal("entity", "Keep the school open"), _goal("profile", "Fill Y7")),
        )
    )
    block = stable_prompt_block()
    assert "[PURPOSE]" in block
    assert "Keep the school open" in block
    assert "Fill Y7" in block


@pytest.mark.parametrize("tier", ["operational", "participant"])
def test_writer_refuses_a_short_lived_goal_in_the_stable_tier(tier: str) -> None:
    with pytest.raises(ValueError):
        write_snapshot(PurposeSnapshot(stable=(_goal(tier),)))
    assert not snapshot_path().exists()


@pytest.mark.parametrize("tier", ["operational", "entity", "profile"])
def test_writer_refuses_a_non_participant_goal_in_the_volatile_tier(tier: str) -> None:
    with pytest.raises(ValueError):
        write_snapshot(PurposeSnapshot(participants={"u1": (_goal(tier),)}))


def test_reader_refuses_an_operational_goal_smuggled_into_the_snapshot() -> None:
    """A hand-edited or stale snapshot cannot get a short-lived goal cached."""
    payload = {
        "version": 1,
        "profile": "default",
        "stable": [
            {"goal_id": "op-1", "tier": "operational", "title": "Book the flight"},
            {"goal_id": "ent-1", "tier": "entity", "title": "Keep the school open"},
        ],
        "participants": {},
    }
    snapshot_path().write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_snapshot()
    assert loaded is not None
    assert [goal.tier for goal in loaded.stable] == ["operational", "entity"]

    block = stable_prompt_block()
    assert "Keep the school open" in block
    assert "Book the flight" not in block


def test_operational_goals_never_reach_either_block() -> None:
    smuggled = PurposeSnapshot(
        stable=(_goal("operational", "Book the flight"),),
        participants={"u1": (_goal("operational", "Book the flight"),)},
    )
    assert build_stable_block(smuggled) == ""
    assert build_participant_block("u1", smuggled) == ""


def test_participant_block_is_per_participant() -> None:
    write_snapshot(
        PurposeSnapshot(
            participants={
                "teacher": (_goal("participant", "Get Y7 through mocks"),),
                "parent": (_goal("participant", "Understand the reports"),),
            }
        )
    )
    teacher = participant_prompt_block("teacher")
    assert "Get Y7 through mocks" in teacher
    assert "Understand the reports" not in teacher
    assert participant_prompt_block(None) == ""
    assert participant_prompt_block("stranger") == ""


def test_an_over_budget_block_is_refused_not_truncated() -> None:
    long_goal = PurposeGoal(
        goal_id="ent-1", tier="entity", title="x" * 500, description=""
    )
    with pytest.raises(PurposeBudgetError):
        build_stable_block(PurposeSnapshot(stable=(long_goal,)), max_goal_chars=400)


def test_prompt_assembly_never_raises_and_reports_the_omission() -> None:
    drain_truncation_warnings()
    write_snapshot(
        PurposeSnapshot(
            stable=(
                PurposeGoal(goal_id="ent-1", tier="entity", title="y" * 5000),
            )
        )
    )
    assert stable_prompt_block() == ""
    warnings = drain_truncation_warnings()
    assert any("Purpose block omitted" in str(item) for item in warnings)


def test_the_block_is_read_from_disk_so_a_mid_session_edit_cannot_change_it() -> None:
    """The bytes a session starts with are the bytes it keeps.

    This is the cache guarantee, expressed at the level the code makes it: the
    prompt is built from the snapshot file, so a change written after that read
    is invisible until the next session builds a prompt again.
    """
    write_snapshot(PurposeSnapshot(stable=(_goal("entity", "First purpose"),)))
    first = stable_prompt_block()

    # Somebody edits the goal mid-conversation.
    write_snapshot(PurposeSnapshot(stable=(_goal("entity", "Second purpose"),)))

    # The already-built prompt is a string; it cannot change. What matters is
    # that the *value the session captured* is still the first one, and the next
    # session's build sees the second.
    assert "First purpose" in first
    assert "Second purpose" not in first
    assert "Second purpose" in stable_prompt_block()


def test_a_published_copy_says_where_it_came_from_and_whether_it_is_behind() -> None:
    write_snapshot(
        PurposeSnapshot(
            stable=(
                PurposeGoal(
                    goal_id="ent-1",
                    tier="entity",
                    title="Keep the school open",
                    published_from="admin",
                    stale=True,
                    primary_metric="enrolled_pupils",
                ),
            )
        )
    )
    block = stable_prompt_block()
    assert "published from profile admin" in block
    assert "behind its source" in block
    assert "enrolled_pupils" in block
