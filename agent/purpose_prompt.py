"""The ``[PURPOSE]`` prompt block — long-lived goals, and only those.

FG-09 established that goals never touch the system prompt: they arrive as
tool-result content, which is what let a goal change mid-session without
invalidating a cached prefix. FG-29 carves out one exception, and this module
is the whole of it.

The exception is narrow on purpose. A goal may enter the system prompt only if
it *cannot change during a session*:

* ``entity`` and ``profile`` goals live for years and quarters, so they go in
  the **stable** tier, immediately after identity — the agent should know what
  it is for before it knows anything else.
* ``participant`` goals live for months but belong to whoever is talking, so
  they go in the **volatile** tier beside ``USER.md``.
* ``operational`` goals live for minutes. They never appear here, in either
  tier, and stay tool-appended exactly as FG-09 has them.

Two properties make that enforceable rather than aspirational:

1. **The block is built from a snapshot on disk, not from the database.** A
   session reads one small JSON file once; a tier change written mid-session
   is invisible to it, so the prompt it started with is the prompt it keeps
   and the cache survives. The next session sees the change. This is also why
   nothing here scales with the number of profiles or goals per turn: it is
   one file read per prompt build, and a prompt is built once per session.
2. **The tier filter is applied when reading, not when writing.** The writer
   already refuses to record an operational goal, and the reader refuses again
   — a hand-edited or stale snapshot cannot smuggle a short-lived goal into a
   cached prefix.

Budget: the block is capped, and an over-budget block is **refused, not
truncated**. Half a goal is worse than no goal: the model would confidently
pursue the first clause of a sentence whose qualifier was cut off, and the
user would have no way to see it happened. Refusal surfaces through the
existing prompt-warning channel.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent.prompt_builder import _record_truncation_warning, _scan_context_content

# The tier → prompt-slot mapping is imported rather than restated: two
# opinions about which lifetime may be cached is exactly the bug this feature
# is trying to make impossible. The store module is pure-stdlib at import
# time, so this costs the prompt path nothing.
from hermes_cli.goal_tree import (
    STABLE_PROMPT_TIERS,
    VOLATILE_PROMPT_TIERS,
    prompt_slot,
)
from hermes_constants import get_hermes_home
from utils import atomic_json_write

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1

#: Character budget for each rendered block. Small by design: the purpose is a
#: sentence or two per goal, not a strategy document, and every character here
#: is paid for on every API call of every session.
#: **Uncalibrated guess** — override with ``goals.prompt.max_chars``.
DEFAULT_MAX_BLOCK_CHARS = 1200

#: Longest single goal title/description pair rendered. A goal longer than
#: this is a document that belongs in SOUL.md or a skill.
DEFAULT_MAX_GOAL_CHARS = 400


class PurposeBudgetError(RuntimeError):
    """The rendered block exceeded its budget and was refused."""


@dataclass(frozen=True)
class PurposeGoal:
    """One long-lived goal, as the prompt needs it."""

    goal_id: str
    tier: str
    title: str
    description: str = ""
    primary_metric: Optional[str] = None
    #: Present only on a copy published from another profile: the profile it
    #: came from, so the agent can say where the purpose is authored.
    published_from: Optional[str] = None
    stale: bool = False

    def as_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "tier": self.tier,
            "title": self.title,
            "description": self.description,
            "primary_metric": self.primary_metric,
            "published_from": self.published_from,
            "stale": self.stale,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PurposeGoal":
        return cls(
            goal_id=str(payload.get("goal_id") or ""),
            tier=str(payload.get("tier") or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            primary_metric=(
                str(payload["primary_metric"])
                if payload.get("primary_metric")
                else None
            ),
            published_from=(
                str(payload["published_from"])
                if payload.get("published_from")
                else None
            ),
            stale=bool(payload.get("stale")),
        )


@dataclass(frozen=True)
class PurposeSnapshot:
    """What this profile is for, as of the last time anything changed it."""

    #: Entity + profile goals, in that order.
    stable: Tuple[PurposeGoal, ...] = ()
    #: Participant goals by ``user_id``.
    participants: Dict[str, Tuple[PurposeGoal, ...]] = field(default_factory=dict)
    profile: str = "default"

    def as_dict(self) -> dict:
        return {
            "version": SNAPSHOT_VERSION,
            "profile": self.profile,
            "stable": [goal.as_dict() for goal in self.stable],
            "participants": {
                user_id: [goal.as_dict() for goal in goals]
                for user_id, goals in self.participants.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PurposeSnapshot":
        raw_stable = payload.get("stable")
        stable = [
            PurposeGoal.from_dict(item)
            for item in (raw_stable if isinstance(raw_stable, list) else [])
            if isinstance(item, dict)
        ]
        participants: Dict[str, Tuple[PurposeGoal, ...]] = {}
        raw_participants = payload.get("participants")
        if isinstance(raw_participants, dict):
            for user_id, goals in raw_participants.items():
                if not isinstance(goals, list):
                    continue
                participants[str(user_id)] = tuple(
                    PurposeGoal.from_dict(item)
                    for item in goals
                    if isinstance(item, dict)
                )
        return cls(
            stable=tuple(stable),
            participants=participants,
            profile=str(payload.get("profile") or "default"),
        )


def snapshot_path() -> Path:
    """Where the purpose snapshot lives for the active profile.

    Profile-derived like every other Hermes path: ``HERMES_HOME`` already
    points at the active profile, so a profile can never read another's
    purpose by accident.
    """
    return get_hermes_home() / ".purpose_snapshot.json"


def write_snapshot(snapshot: PurposeSnapshot) -> None:
    """Persist the snapshot the next session's prompt will be built from.

    Refuses to record a goal that may not enter a prompt at all. The reader
    checks again; this check is here so the refusal is visible at the moment
    somebody tries, rather than as a silently missing block later.
    """
    for goal in snapshot.stable:
        if prompt_slot(goal.tier) != "stable":
            raise ValueError(
                f"Goal {goal.goal_id} is tier {goal.tier!r}, which does not "
                f"belong in the stable prompt tier"
            )
    for goals in snapshot.participants.values():
        for goal in goals:
            if prompt_slot(goal.tier) != "volatile":
                raise ValueError(
                    f"Goal {goal.goal_id} is tier {goal.tier!r}, which does "
                    f"not belong in the volatile prompt tier"
                )
    atomic_json_write(snapshot_path(), snapshot.as_dict())


def clear_snapshot() -> None:
    """Remove the snapshot (test hook and ``hermes goal purpose --clear``)."""
    try:
        snapshot_path().unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - a missing file is the goal
        logger.debug("Could not remove purpose snapshot: %s", exc)


def load_snapshot() -> Optional[PurposeSnapshot]:
    """Read the snapshot, or ``None`` when there is nothing to say."""
    path = snapshot_path()
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a bad snapshot is no snapshot
        logger.debug("Could not read purpose snapshot: %s", exc)
        return None
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
        return None
    return PurposeSnapshot.from_dict(payload)


def _render(goals: List[PurposeGoal], *, heading: str, max_goal_chars: int) -> str:
    lines: List[str] = [heading]
    for goal in goals:
        label = {
            "entity": "Entity goal",
            "profile": "This profile's goal",
            "participant": "Their goal",
        }.get(goal.tier, goal.tier)
        text = goal.title.strip()
        if goal.description.strip():
            text = f"{text} — {goal.description.strip()}"
        if len(text) > max_goal_chars:
            raise PurposeBudgetError(
                f"Goal {goal.goal_id} is {len(text)} characters, over the "
                f"{max_goal_chars}-character per-goal budget"
            )
        suffix = ""
        if goal.primary_metric:
            suffix += f" [measured by: {goal.primary_metric}]"
        if goal.published_from:
            suffix += f" [published from profile {goal.published_from}]"
        if goal.stale:
            suffix += " [copy is behind its source; re-publish to refresh]"
        lines.append(f"- {label}: {text}{suffix}")
    return "\n".join(lines)


def build_stable_block(
    snapshot: Optional[PurposeSnapshot] = None,
    *,
    max_chars: int = DEFAULT_MAX_BLOCK_CHARS,
    max_goal_chars: int = DEFAULT_MAX_GOAL_CHARS,
) -> str:
    """The ``[PURPOSE]`` block for the stable tier, or ``""``.

    Raises :class:`PurposeBudgetError` when the block would exceed its
    budget — the caller omits the block and warns rather than truncating it.
    """
    resolved = snapshot if snapshot is not None else load_snapshot()
    if resolved is None:
        return ""
    goals = [
        goal
        for goal in resolved.stable
        if goal.tier in STABLE_PROMPT_TIERS and goal.title.strip()
    ]
    if not goals:
        return ""
    block = _render(
        goals,
        heading=(
            "[PURPOSE]\nWhat this system exists to achieve. These goals are "
            "measured in quarters and years; they do not change during a "
            "conversation. Treat them as the standing reason for the work, "
            "not as a task to execute now."
        ),
        max_goal_chars=max_goal_chars,
    )
    scanned = _scan_context_content(block, "[PURPOSE]")
    if len(scanned) > max_chars:
        raise PurposeBudgetError(
            f"The purpose block is {len(scanned)} characters, over the "
            f"{max_chars}-character budget; shorten the goal text"
        )
    return scanned


def build_participant_block(
    user_id: Optional[str],
    snapshot: Optional[PurposeSnapshot] = None,
    *,
    max_chars: int = DEFAULT_MAX_BLOCK_CHARS,
    max_goal_chars: int = DEFAULT_MAX_GOAL_CHARS,
) -> str:
    """The participant-goal block for the volatile tier, or ``""``."""
    if not user_id:
        return ""
    resolved = snapshot if snapshot is not None else load_snapshot()
    if resolved is None:
        return ""
    goals = [
        goal
        for goal in resolved.participants.get(str(user_id), ())
        if goal.tier in VOLATILE_PROMPT_TIERS and goal.title.strip()
    ]
    if not goals:
        return ""
    block = _render(
        goals,
        heading=(
            "[PARTICIPANT PURPOSE]\nWhat the person you are talking to is "
            "trying to achieve here, over months. Fixed for this session."
        ),
        max_goal_chars=max_goal_chars,
    )
    scanned = _scan_context_content(block, "[PARTICIPANT PURPOSE]")
    if len(scanned) > max_chars:
        raise PurposeBudgetError(
            f"The participant purpose block is {len(scanned)} characters, "
            f"over the {max_chars}-character budget"
        )
    return scanned


def _budget_from_config() -> Tuple[int, int]:
    """``(max_chars, max_goal_chars)`` from ``config.yaml``, with defaults."""
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
    except Exception:  # noqa: BLE001 - a missing config is the default config
        return DEFAULT_MAX_BLOCK_CHARS, DEFAULT_MAX_GOAL_CHARS
    goals_cfg = config.get("goals")
    prompt_cfg = goals_cfg.get("prompt") if isinstance(goals_cfg, dict) else None
    if not isinstance(prompt_cfg, dict):
        return DEFAULT_MAX_BLOCK_CHARS, DEFAULT_MAX_GOAL_CHARS
    block = prompt_cfg.get("max_chars")
    per_goal = prompt_cfg.get("max_goal_chars")
    return (
        int(block) if isinstance(block, int) and block > 0 else DEFAULT_MAX_BLOCK_CHARS,
        int(per_goal)
        if isinstance(per_goal, int) and per_goal > 0
        else DEFAULT_MAX_GOAL_CHARS,
    )


def stable_prompt_block() -> str:
    """The stable-tier block for prompt assembly, refusing over budget.

    Never raises: prompt assembly must not fail. An over-budget or malformed
    block is omitted and reported through the same channel that reports a
    truncated context file, so the user is told the agent does not know its
    purpose instead of being handed half of it.
    """
    max_chars, max_goal_chars = _budget_from_config()
    try:
        return build_stable_block(max_chars=max_chars, max_goal_chars=max_goal_chars)
    except PurposeBudgetError as exc:
        _record_truncation_warning(f"Purpose block omitted: {exc}")
        return ""
    except Exception as exc:  # noqa: BLE001 - never block prompt assembly
        logger.debug("Could not build the purpose block: %s", exc)
        return ""


def participant_prompt_block(user_id: Optional[str]) -> str:
    """The volatile-tier participant block for prompt assembly."""
    max_chars, max_goal_chars = _budget_from_config()
    try:
        return build_participant_block(
            user_id, max_chars=max_chars, max_goal_chars=max_goal_chars
        )
    except PurposeBudgetError as exc:
        _record_truncation_warning(f"Participant purpose block omitted: {exc}")
        return ""
    except Exception as exc:  # noqa: BLE001 - never block prompt assembly
        logger.debug("Could not build the participant purpose block: %s", exc)
        return ""


__all__ = [
    "DEFAULT_MAX_BLOCK_CHARS",
    "DEFAULT_MAX_GOAL_CHARS",
    "PurposeBudgetError",
    "PurposeGoal",
    "PurposeSnapshot",
    "SNAPSHOT_VERSION",
    "build_participant_block",
    "build_stable_block",
    "clear_snapshot",
    "load_snapshot",
    "participant_prompt_block",
    "snapshot_path",
    "stable_prompt_block",
    "write_snapshot",
]
