"""A digest section that could not run must say so.

Three of ``weekly_digest()``'s six sections are optional by design — one
unconfigured store must not cost the owner the rest of the weekly review — and
each was wrapped in ``except Exception: log.warning(...)``, contributing
nothing. In a digest, contributing nothing is what a section with *nothing to
report* does, so a failed store and a quiet week were the same output; with all
three failing the digest read "Nothing to review this week", which is the
sentence a healthy quiet box produces.

The digest is the review loop's only unattended weekly output, so a step that
cannot run reporting as a step with nothing to say is the same shape as the
defects the box has already produced this phase (retirement reporting success
over goals it never closed; the review pass's swallowed missing table).
"""

from __future__ import annotations

from typing import List

import pytest

from hermes_cli import goal_conflicts


class _Principal:
    user_id = "root"
    display = "root"
    role = "owner"
    is_owner = True


class _Promotions:
    async def digest_candidates(self, _principal: object) -> List[object]:
        return []

    async def demotion_candidates(self, *, now: object = None) -> List[object]:
        return []


class _Tree:
    async def unmeasured_long_lived(
        self, _principal: object, *, now: object = None
    ) -> List[object]:
        return []

    async def stale_published_copies(self, _principal: object) -> List[object]:
        return []


def _break_optional_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make all three optional sections fail, each at its own seam."""

    def _no_store(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no datastore configured")

    async def _no_profiles(*_args: object, **_kwargs: object) -> object:
        raise OSError("profiles root unreadable")

    def _no_capacity(*_args: object, **_kwargs: object) -> object:
        raise ValueError("max_concurrent_sessions missing")

    monkeypatch.setattr("hermes_cli.datastore.get_store", _no_store)
    monkeypatch.setattr("hermes_cli.profile_suggestion.idle_profiles", _no_profiles)
    monkeypatch.setattr("hermes_cli.capacity.headroom", _no_capacity)


async def _digest(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    monkeypatch.setattr(
        goal_conflicts, "detect_conflicts", lambda *a, **k: _empty_conflicts()
    )
    _, lines = await goal_conflicts.weekly_digest(
        _Tree(),  # type: ignore[arg-type]
        _Promotions(),  # type: ignore[arg-type]
        _Principal(),  # type: ignore[arg-type]
    )
    return lines


async def _empty_conflicts() -> List[object]:
    return []


@pytest.mark.asyncio
async def test_a_section_that_could_not_run_is_named_in_the_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_optional_sections(monkeypatch)

    lines = await _digest(monkeypatch)

    assert any(
        line.startswith("Profile suggestion: unavailable —") for line in lines
    ), lines
    assert any(
        line.startswith("Profiles with no recent sessions: unavailable —")
        for line in lines
    ), lines
    assert any(line.startswith("Capacity: unavailable —") for line in lines), lines


@pytest.mark.asyncio
async def test_the_reason_reaches_the_owner_not_only_the_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming the section without the cause would still send the owner reading
    a log they cannot see from their phone."""
    _break_optional_sections(monkeypatch)

    lines = await _digest(monkeypatch)

    capacity = next(line for line in lines if line.startswith("Capacity:"))
    assert "ValueError" in capacity
    assert "max_concurrent_sessions missing" in capacity


@pytest.mark.asyncio
async def test_every_section_failing_is_not_reported_as_a_quiet_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression itself: three failures used to produce the same digest as
    a box with nothing to review."""
    _break_optional_sections(monkeypatch)

    lines = await _digest(monkeypatch)

    assert "Nothing to review this week." not in lines


@pytest.mark.asyncio
async def test_one_failure_does_not_cost_the_other_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property the ``try`` blocks exist for, kept: the sections after a
    failed one still render."""

    def _no_store(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no datastore configured")

    async def _no_idle(*_args: object, **_kwargs: object) -> List[object]:
        return []

    monkeypatch.setattr("hermes_cli.datastore.get_store", _no_store)
    monkeypatch.setattr("hermes_cli.profile_suggestion.idle_profiles", _no_idle)

    lines = await _digest(monkeypatch)

    assert any(line.startswith("Profile suggestion: unavailable") for line in lines)
    assert any(line.startswith("Capacity:") for line in lines)
    assert not any(line.startswith("Capacity: unavailable") for line in lines)


def test_a_long_failure_message_is_trimmed_rather_than_flooding_the_digest() -> None:
    line = goal_conflicts._unavailable("Capacity", RuntimeError("x" * 500))

    assert len(line) < 220
    assert line.endswith("…")


def test_a_multiline_failure_message_stays_one_digest_line() -> None:
    line = goal_conflicts._unavailable("Capacity", RuntimeError("first\nsecond"))

    assert "\n" not in line
    assert "first second" in line
