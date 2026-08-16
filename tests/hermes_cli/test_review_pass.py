"""The review loop's clock — that it exists, and what it does when a step fails.

The defect these cover is an absence: every human-facing output of FG-29/FG-30/
FG-31 converges on ``weekly_digest()``, and nothing on the box called it or
``generate_suggestion()``. A suggestion could therefore never be produced, no
matter how much evidence accumulated.

Two of these are wiring tests over the source tree rather than behaviour tests,
deliberately: "something calls this on a schedule" is a property of the
deployment, and the way it was false for a year is that no test could tell.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from hermes_cli import review_pass as rp

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The clock exists and is installed from git
# ---------------------------------------------------------------------------


def test_the_review_pass_has_a_timer_that_runs_the_command() -> None:
    service = (REPO / "deploy" / "hermes-review-pass.service").read_text()
    timer = (REPO / "deploy" / "hermes-review-pass.timer").read_text()

    exec_start = re.search(r"^ExecStart=(.+)$", service, re.M)
    assert exec_start is not None
    assert exec_start.group(1).strip().endswith("promotion review-pass")
    assert "User=hermes" in service
    assert "Unit=hermes-review-pass.service" in timer
    # A box that was off must catch up, or a missed week is a silently skipped
    # review rather than a late one.
    assert "Persistent=true" in timer
    assert re.search(r"^OnCalendar=", timer, re.M)


def test_the_command_the_timer_names_is_a_real_subcommand() -> None:
    source = (REPO / "hermes_cli" / "goal_tree_cmd.py").read_text()
    assert '"review-pass"' in source
    assert "_review_pass(" in source


def test_generation_is_reachable_without_a_human_typing() -> None:
    """``generate_suggestion`` had exactly one caller: the interactive CLI."""
    callers = [
        path
        for path in (REPO / "hermes_cli").glob("*.py")
        if "generate_suggestion(" in path.read_text()
        and path.name != "profile_suggestion.py"
    ]
    assert any(path.name == "review_pass.py" for path in callers), (
        "nothing schedulable calls generate_suggestion — a suggestion can only "
        "appear if somebody types `hermes profile suggest`"
    )


# ---------------------------------------------------------------------------
# One digest per week, whatever runs the pass
# ---------------------------------------------------------------------------


def test_the_digest_dedupes_on_the_iso_week() -> None:
    monday = datetime(2026, 8, 17, 8, 0)
    friday = datetime(2026, 8, 21, 23, 0)
    next_monday = datetime(2026, 8, 24, 8, 0)

    assert rp.digest_dedupe_key(monday) == rp.digest_dedupe_key(friday)
    assert rp.digest_dedupe_key(next_monday) != rp.digest_dedupe_key(monday)


def test_the_digest_is_an_ask_and_never_an_approval() -> None:
    """A C6 policy may auto-answer a reversible approval; a review must not be."""
    from hermes_cli.human_comms import NOTIFICATION_KINDS

    assert rp.DIGEST_KIND == "proactive_ask"
    assert rp.DIGEST_KIND in NOTIFICATION_KINDS


# ---------------------------------------------------------------------------
# One step failing is not the pass failing
# ---------------------------------------------------------------------------


def test_a_failed_step_is_reported_rather_than_logged_away() -> None:
    result = rp.ReviewPassResult()
    assert result.ok

    result.note_failure("suggestion", ValueError("aux LLM unreachable"))

    assert not result.ok
    assert result.errors == [("suggestion", "ValueError: aux LLM unreachable")]


@pytest.mark.asyncio
async def test_the_digest_is_still_delivered_when_generation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A digest is worth delivering even when the aux LLM is down."""
    delivered: dict[str, object] = {}

    class _Principal:
        user_id = "root"
        display = "root"
        role = "owner"
        is_owner = True

    class _Registry:
        _store = object()

        async def initialize(self) -> None:
            return None

    class _Tree:
        registry = _Registry()

    class _Principals:
        def __init__(self, _store: object) -> None:
            pass

        async def get_owner(self) -> _Principal:
            return _Principal()

    class _Created:
        class notification:  # noqa: N801 - a stand-in for the row
            id = "notif-1"

    class _Notifications:
        def __init__(self, _store: object) -> None:
            pass

        async def initialize(self) -> None:
            return None

        async def create(self, **kwargs: object) -> _Created:
            delivered.update(kwargs)
            return _Created()

    async def _boom(*_args: object, **_kwargs: object):
        raise RuntimeError("aux LLM unreachable")

    async def _no_conflicts(*_args: object, **_kwargs: object):
        return []

    async def _digest(*_args: object, **_kwargs: object):
        return "Weekly entity review", ["Capacity:", "  comfortable"]

    monkeypatch.setattr("hermes_cli.goal_purpose.default_tree_store", lambda: _Tree())
    monkeypatch.setattr("hermes_cli.access.PrincipalStore", _Principals)
    monkeypatch.setattr("hermes_cli.human_comms.NotificationStore", _Notifications)
    monkeypatch.setattr("hermes_cli.skill_promotion.SkillPromotionStore", lambda _s: object())
    monkeypatch.setattr("hermes_cli.profile_suggestion.generate_suggestion", _boom)
    monkeypatch.setattr("hermes_cli.goal_conflicts.detect_conflicts", _no_conflicts)
    monkeypatch.setattr("hermes_cli.goal_conflicts.weekly_digest", _digest)

    result = await rp.run_review_pass(now=datetime(2026, 8, 17, 8, 0))

    assert result.digest_notification_id == "notif-1"
    assert delivered["dedupe_key"] == "entity-review:2026-W34"
    assert result.errors and result.errors[0][0] == "suggestion"


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Principal:
        user_id = "root"
        display = "root"
        role = "owner"
        is_owner = True

    class _Registry:
        _store = object()

        async def initialize(self) -> None:
            return None

    class _Tree:
        registry = _Registry()

    class _Principals:
        def __init__(self, _store: object) -> None:
            pass

        async def get_owner(self) -> _Principal:
            return _Principal()

    class _Notifications:
        def __init__(self, _store: object) -> None:
            pass

        async def initialize(self) -> None:
            raise AssertionError("a dry run must not touch the notification store")

        async def create(self, **_kwargs: object):
            raise AssertionError("a dry run must not create a notification")

    async def _none(*_args: object, **_kwargs: object):
        return None

    async def _conflict_pair(*_args: object, **_kwargs: object):
        return ["cashflow vs quality"]

    async def _alert(*_args: object, **_kwargs: object):
        raise AssertionError("a dry run must not alert")

    async def _digest(*_args: object, **_kwargs: object):
        return "Weekly entity review", ["Nothing to review this week."]

    monkeypatch.setattr("hermes_cli.goal_purpose.default_tree_store", lambda: _Tree())
    monkeypatch.setattr("hermes_cli.access.PrincipalStore", _Principals)
    monkeypatch.setattr("hermes_cli.human_comms.NotificationStore", _Notifications)
    monkeypatch.setattr("hermes_cli.skill_promotion.SkillPromotionStore", lambda _s: object())
    monkeypatch.setattr("hermes_cli.profile_suggestion.generate_suggestion", _none)
    monkeypatch.setattr("hermes_cli.goal_conflicts.detect_conflicts", _conflict_pair)
    monkeypatch.setattr("hermes_cli.goal_conflicts.alert_owner", _alert)
    monkeypatch.setattr("hermes_cli.goal_conflicts.weekly_digest", _digest)

    result = await rp.run_review_pass(deliver=False)

    assert result.ok
    assert result.digest_notification_id is None
    assert result.conflicts_alerted == 0
    assert result.digest_lines == ["Nothing to review this week."]
