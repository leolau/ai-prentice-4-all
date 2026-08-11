"""Keeping the purpose snapshot in step with the registry.

The prompt side (:mod:`agent.purpose_prompt`) deliberately never touches the
database: it reads one small JSON file, once per session. This module is the
other half — the writer that refreshes that file whenever a long-lived goal
actually changes, which is a handful of times a quarter rather than once a
turn. That split is what keeps the feature off the per-turn path entirely: no
amount of profiles or goals adds work to a conversation.

It also owns the **default first goal**. A new owner should not meet an empty
system: the first goal exists from the first run, says what it is for, and is
editable in agent-home settings. It is created as an ordinary entity goal, so
nothing downstream needs to know it was a default.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from agent.purpose_prompt import PurposeGoal, PurposeSnapshot, write_snapshot
from hermes_cli.goal_registry import GoalRecord, GoalRegistryStore
from hermes_cli.goal_tree import (
    STABLE_PROMPT_TIERS,
    VOLATILE_PROMPT_TIERS,
    GoalTreeStore,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hermes_cli.access import Principal

log = logging.getLogger(__name__)

#: The goal a fresh install starts with. Phrased as a prompt to the owner
#: rather than as a guess about their business: an invented goal in the system
#: prompt would be worse than an obviously-unfinished one.
DEFAULT_ENTITY_GOAL_TITLE = "Describe what this system is for"
DEFAULT_ENTITY_GOAL_DESCRIPTION = (
    "Replace this with the entity's goal — the outcome everything else "
    "ladders into. Editable in agent-home settings."
)


def _as_purpose_goal(goal: GoalRecord) -> PurposeGoal:
    return PurposeGoal(
        goal_id=goal.id,
        tier=goal.tier,
        title=goal.title,
        description=goal.description,
        primary_metric=goal.primary_metric,
        published_from=goal.published_from_profile,
        stale=goal.stale,
    )


def _participant_owner(goal: GoalRecord) -> Optional[str]:
    """Whose participant goal this is.

    A participant goal is private to the person whose goal it is, so the C2
    visibility tag already names them; a ``shared`` participant goal has no
    single owner to attach it to and falls back to the creating principal.
    """
    if goal.visibility.startswith("private:"):
        return goal.visibility.split(":", 1)[1] or None
    return goal.owner_user_id or None


async def build_snapshot(
    tree: GoalTreeStore,
    principal: "Principal",
    *,
    profile: Optional[str] = None,
) -> PurposeSnapshot:
    """Render the current long-lived goals into a snapshot.

    Reads with the owner's scope: the stable block is entity-wide by
    definition, and a participant block is filtered by ``user_id`` at prompt
    time so one participant's goal never renders for another.
    """
    goals = await tree.registry.list_goals(principal, status="active")
    stable: List[GoalRecord] = []
    participants: Dict[str, List[PurposeGoal]] = {}
    for goal in goals:
        if goal.tier in STABLE_PROMPT_TIERS:
            stable.append(goal)
        elif goal.tier in VOLATILE_PROMPT_TIERS:
            owner = _participant_owner(goal)
            if owner:
                participants.setdefault(owner, []).append(_as_purpose_goal(goal))

    # Entity first, then the profile's own sub-goal: the reader should see the
    # reason before the instrument.
    order = {tier: index for index, tier in enumerate(STABLE_PROMPT_TIERS)}
    stable.sort(key=lambda item: (order.get(item.tier, len(order)), item.id))

    if profile is None:
        from hermes_cli.profiles import get_active_profile_name

        profile = get_active_profile_name()

    return PurposeSnapshot(
        stable=tuple(_as_purpose_goal(goal) for goal in stable),
        participants={
            user_id: tuple(items) for user_id, items in participants.items()
        },
        profile=profile,
    )


async def sync_snapshot(
    tree: GoalTreeStore,
    principal: "Principal",
    *,
    profile: Optional[str] = None,
) -> PurposeSnapshot:
    """Rebuild and persist the purpose snapshot. Effective next session.

    Called after any write that could change a long-lived goal. It cannot
    affect a conversation already in flight — the prompt for that session was
    built from the previous file and is cached — which is exactly the
    guarantee the tier rule needs.
    """
    snapshot = await build_snapshot(tree, principal, profile=profile)
    write_snapshot(snapshot)
    return snapshot


async def ensure_default_entity_goal(
    tree: GoalTreeStore,
    principal: "Principal",
) -> Tuple[GoalRecord, bool]:
    """Return the entity goal, creating the default first one if absent.

    ``(goal, created)``. Idempotent, and safe to call from a read path: the
    single-active-entity index means a race creates one goal, not two, and the
    loser re-reads the winner's row.
    """
    existing = await tree.entity_goal(principal)
    if existing is not None:
        return existing, False
    if not principal.is_owner:
        raise PermissionError("Only the owner may create the entity goal")
    try:
        goal = await tree.registry.create_goal(
            principal,
            DEFAULT_ENTITY_GOAL_TITLE,
            description=DEFAULT_ENTITY_GOAL_DESCRIPTION,
            priority="high",
            visibility="shared",
            tier="entity",
        )
    except Exception as exc:  # noqa: BLE001 - a lost race is not an error
        existing = await tree.entity_goal(principal)
        if existing is None:
            raise
        log.debug("default entity goal already existed (%s)", exc)
        return existing, False
    await sync_snapshot(tree, principal)
    return goal, True


def default_tree_store(config: Optional[dict] = None) -> GoalTreeStore:
    """The tree store bound to this profile's schema."""
    from hermes_cli.config import load_config
    from hermes_cli.datastore import get_store

    resolved = config if config is not None else (load_config() or {})
    store = get_store("supabase-app", "prod", config=resolved)
    return GoalTreeStore(GoalRegistryStore(store))


__all__ = [
    "DEFAULT_ENTITY_GOAL_DESCRIPTION",
    "DEFAULT_ENTITY_GOAL_TITLE",
    "build_snapshot",
    "default_tree_store",
    "ensure_default_entity_goal",
    "sync_snapshot",
]
