"""``hermes goal`` and ``hermes promotion`` — the FG-29 operator surface.

Deliberately a CLI command rather than a model tool: the goal tree is edited by
a person a handful of times a quarter, and every core tool is paid for on every
API call of every session. The agent reaches these through a skill if it needs
them at all.

Both commands are read-mostly and print plain text, so the weekly digest works
with no delivery dependency: ``hermes promotion digest`` in a terminal, the same
lines in a notification, the same lines in agent-home.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional, Sequence

from hermes_cli.access import Principal, PrincipalStore
from hermes_cli.goal_purpose import (
    default_tree_store,
    ensure_default_entity_goal,
    sync_snapshot,
)
from hermes_cli.goal_registry import GOAL_TIERS
from hermes_cli.goal_tree import GoalTreeError, GoalTreeStore, prompt_slot


async def _resolve(actor: Optional[str]) -> tuple[GoalTreeStore, Principal]:
    tree = default_tree_store()
    principals = PrincipalStore(tree.registry._store)
    principal = await principals.get(actor) if actor else await principals.get_owner()
    if principal is None:
        raise RuntimeError(
            "unknown --actor" if actor else "no owner is enrolled yet"
        )
    await tree.registry.initialize()
    return tree, principal


# ---------------------------------------------------------------------------
# hermes goal
# ---------------------------------------------------------------------------


async def _tree(actor: Optional[str]) -> int:
    tree, principal = await _resolve(actor)
    goals = await tree.registry.list_goals(principal, status="active")
    if not goals:
        print("No active goals.")
        return 0
    by_parent: dict[Optional[str], list] = {}
    for goal in goals:
        by_parent.setdefault(goal.parent_goal_id, []).append(goal)

    def render(parent: Optional[str], depth: int) -> None:
        for goal in sorted(by_parent.get(parent, []), key=lambda item: item.title):
            marks = []
            if goal.is_published_copy:
                marks.append(f"published from {goal.published_from_profile}")
            if goal.stale:
                marks.append("STALE")
            if goal.primary_metric:
                marks.append(f"metric={goal.primary_metric}")
            marks.append(f"prompt={prompt_slot(goal.tier)}")
            print(
                f"{'  ' * depth}- [{goal.tier}] {goal.title} "
                f"({goal.id}) — {'; '.join(marks)}"
            )
            render(goal.id, depth + 1)

    render(None, 0)
    return 0


async def _set_parent(actor: Optional[str], goal_id: str, parent_id: str) -> int:
    tree, principal = await _resolve(actor)
    goal = await tree.set_parent(principal, goal_id, parent_goal_id=parent_id)
    await sync_snapshot(tree, principal)
    print(f"{goal.title} now ladders into {parent_id}.")
    return 0


async def _set_tier(actor: Optional[str], goal_id: str, tier: str) -> int:
    tree, principal = await _resolve(actor)
    goal = await tree.set_tier(principal, goal_id, tier)
    await sync_snapshot(tree, principal)
    print(
        f"{goal.title} is now a {goal.tier} goal "
        f"(prompt tier: {prompt_slot(goal.tier)}). "
        f"Takes effect in the NEXT session — a conversation already running "
        f"keeps the prompt it was cached with."
    )
    return 0


async def _set_metric(actor: Optional[str], goal_id: str, metric: str) -> int:
    tree, principal = await _resolve(actor)
    goal = await tree.set_primary_metric(principal, goal_id, metric)
    await sync_snapshot(tree, principal)
    print(f"{goal.title} is now measured by {goal.primary_metric}.")
    return 0


async def _edit(
    actor: Optional[str],
    goal_id: str,
    *,
    title: Optional[str],
    description: Optional[str],
) -> int:
    tree, principal = await _resolve(actor)
    goal = await tree.set_entity_goal_text(
        principal, goal_id, title=title, description=description
    )
    await sync_snapshot(tree, principal)
    print(
        f"{goal.title} is now at rev {goal.source_rev}. Every published copy "
        f"reports itself stale until you re-run `hermes goal publish`."
    )
    return 0


async def _publish_audit(actor: Optional[str], goal_id: str) -> int:
    tree, _principal = await _resolve(actor)
    rows = await tree.publish_audit(goal_id)
    if not rows:
        print(f"{goal_id} has never been published.")
        return 0
    for row in rows:
        print(
            f"{row['at']:%Y-%m-%d %H:%M} {row['action']} into "
            f"{row['target_profile']} at rev {row['revision']} "
            f"by {row['actor_user_id']}"
        )
    return 0


async def _rollup(actor: Optional[str], goal_id: str) -> int:
    tree, principal = await _resolve(actor)
    result = await tree.rollup(principal, goal_id)
    if result is None:
        print(f"No goal {goal_id} visible to you.", file=sys.stderr)
        return 1
    excluded = len(result.unmeasured_children)
    if not result.measured:
        print(
            f"{result.title}: no measurable progress yet "
            f"({excluded} unmeasured contributor(s)). "
            f"Reported, not scored as 0%."
        )
        return 0
    source = "child goals" if result.children is not None else "its own measure"
    print(
        f"{result.title}: {result.progress:.0%} from {source}; "
        f"{excluded} unmeasured child goal(s) excluded rather than counted as 0."
    )
    return 0


async def _ladder(actor: Optional[str], goal_id: str) -> int:
    tree, principal = await _resolve(actor)
    chain = await tree.ladder(principal, goal_id)
    if not chain:
        print(f"No goal {goal_id} visible to you.")
        return 1
    for depth, goal in enumerate(chain):
        print(f"{'  ' * depth}{goal.tier}: {goal.title} ({goal.id})")
    return 0


async def _publish(actor: Optional[str], profiles: Sequence[str]) -> int:
    tree, principal = await _resolve(actor)
    results = await tree.publish_entity_goal(
        principal, profiles=list(profiles) or None
    )
    if not results:
        print("No other profiles to publish into.")
        return 0
    for result in results:
        verb = "created" if result.created else "refreshed"
        print(f"{result.profile}: {verb} copy {result.goal_id} at rev {result.revision}")
    return 0


async def _stale(actor: Optional[str]) -> int:
    tree, principal = await _resolve(actor)
    stale = await tree.stale_published_copies(principal)
    if not stale:
        print("Every published entity-goal copy here is current.")
        return 0
    for goal in stale:
        print(
            f"STALE: {goal.title} (copy of {goal.published_from_goal_id} from "
            f"{goal.published_from_profile}, rev {goal.published_rev}) — "
            f"re-run `hermes goal publish` in that profile."
        )
    return 1


async def _default_goal(actor: Optional[str]) -> int:
    tree, principal = await _resolve(actor)
    goal, created = await ensure_default_entity_goal(tree, principal)
    print(
        f"{'Created' if created else 'Entity goal already exists'}: "
        f"{goal.title} ({goal.id})"
    )
    return 0


async def _sync(actor: Optional[str]) -> int:
    tree, principal = await _resolve(actor)
    snapshot = await sync_snapshot(tree, principal)
    print(
        f"Purpose snapshot written for profile {snapshot.profile}: "
        f"{len(snapshot.stable)} stable goal(s), "
        f"{len(snapshot.participants)} participant(s). "
        f"New sessions will see it."
    )
    return 0


async def _conflicts(actor: Optional[str], *, alert: bool) -> int:
    from hermes_cli.goal_conflicts import alert_owner, detect_conflicts

    tree, principal = await _resolve(actor)
    conflicts = await detect_conflicts(tree, principal)
    if not conflicts:
        print("No sibling goals detected pulling against each other.")
        return 0
    for conflict in conflicts:
        print(f"{conflict.kind}: {conflict.title()}")
        print(f"  {conflict.evidence}")
    if alert:
        from hermes_cli.human_comms import NotificationStore

        notifications = NotificationStore(tree.registry._store)
        await notifications.initialize()
        ids = await alert_owner(notifications, principal, conflicts)
        print(f"Alerted the owner ({len(ids)} notification(s)); nothing was changed.")
    return 0


async def _decide(
    actor: Optional[str],
    left_goal_id: str,
    right_goal_id: str,
    decision: str,
) -> int:
    from hermes_cli.goal_conflicts import record_decision_for_pair

    tree, principal = await _resolve(actor)
    left, right = await record_decision_for_pair(
        tree, principal, left_goal_id, right_goal_id, decision=decision
    )
    print(
        f"Recorded against both {left.title} and {right.title}. "
        f"Neither goal's priority or status was changed."
    )
    return 0


# ---------------------------------------------------------------------------
# hermes promotion
# ---------------------------------------------------------------------------


async def _promotion_store(actor: Optional[str]):
    from hermes_cli.skill_promotion import SkillPromotionStore

    tree, principal = await _resolve(actor)
    promotions = SkillPromotionStore(tree.registry._store)
    await promotions.initialize()
    return tree, promotions, principal


async def _propose(
    actor: Optional[str],
    skill: str,
    *,
    rationale: str,
    goal_id: Optional[str],
    private: bool,
    consent: Sequence[str],
) -> int:
    tree, promotions, principal = await _promotion_store(actor)
    progress: Optional[float] = None
    if goal_id:
        rollup = await tree.rollup(principal, goal_id)
        progress = rollup.progress if rollup is not None else None
    candidate = await promotions.propose(
        principal,
        skill,
        rationale=rationale,
        goal_id=goal_id,
        goal_progress=progress,
        derived_from_private=private,
        consent_user_ids=list(consent),
    )
    verdict = (
        "above" if candidate.score >= promotions.settings.threshold else "below"
    )
    print(
        f"Proposed {candidate.skill_name} ({candidate.id}) at score "
        f"{candidate.score:.2f} — {verdict} the digest threshold "
        f"({promotions.settings.threshold:.2f})."
    )
    return 0


async def _promotion_list(actor: Optional[str], *, all_states: bool) -> int:
    from hermes_cli.skill_promotion import PROMOTION_STATES, OPEN_STATES

    _tree_store, promotions, principal = await _promotion_store(actor)
    states = PROMOTION_STATES if all_states else OPEN_STATES
    candidates = await promotions.list_candidates(principal, states=states)
    if not candidates:
        print("No promotion proposals.")
        return 0
    for candidate in candidates:
        print(
            f"{candidate.id} [{candidate.state}] {candidate.skill_name} "
            f"score={candidate.score:.2f} from={candidate.origin_profile}"
        )
    return 0


async def _digest(actor: Optional[str]) -> int:
    from hermes_cli.goal_conflicts import weekly_digest

    tree, promotions, principal = await _promotion_store(actor)
    title, lines = await weekly_digest(tree, promotions, principal)
    print(title)
    for line in lines:
        print(line)
    return 0


async def _approve(actor: Optional[str], promotion_id: str, *, note: str) -> int:
    _tree_store, promotions, principal = await _promotion_store(actor)
    candidate = await promotions.get(principal, promotion_id)
    if candidate is None:
        print(f"No promotion proposal {promotion_id}.", file=sys.stderr)
        return 1
    if candidate.state == "proposed":
        updated = await promotions.approve_in_profile(
            principal, promotion_id, note=note
        )
        print(
            f"{updated.skill_name}: origin profile approved. The owner must "
            f"still accept it for the entity."
        )
        return 0
    candidate, displaced = await promotions.approve_for_entity(
        principal, promotion_id, note=note
    )
    message = f"{candidate.skill_name}: shared. New sessions will see it."
    if displaced is not None:
        message += (
            f" Displaced {displaced.skill_name} (score {displaced.score:.2f}); "
            f"it stays in {displaced.origin_profile}."
        )
    print(message)
    return 0


async def _reject(actor: Optional[str], promotion_id: str, reason: str) -> int:
    _tree_store, promotions, principal = await _promotion_store(actor)
    candidate = await promotions.reject(principal, promotion_id, reason=reason)
    print(f"{candidate.skill_name}: rejected. The proposal and its audit remain.")
    return 0


async def _demote(actor: Optional[str], skill: str, reason: str) -> int:
    _tree_store, promotions, principal = await _promotion_store(actor)
    demoted = await promotions.demote(principal, skill, reason=reason)
    if demoted is None:
        print(f"{skill} is not in the shared library.", file=sys.stderr)
        return 1
    print(
        f"{skill}: removed from the shared library. "
        f"{demoted.origin_profile} keeps its own copy."
    )
    return 0


async def _shared(actor: Optional[str]) -> int:
    _tree_store, promotions, _principal = await _promotion_store(actor)
    residents = await promotions.shared_skills()
    cap = promotions.settings.max_shared_skills
    print(f"Shared library: {len(residents)}/{cap} skills")
    for resident in residents:
        print(
            f"  {resident.skill_name} score={resident.score:.2f} "
            f"from={resident.origin_profile}"
        )
    return 0


async def _audit(actor: Optional[str], promotion_id: str) -> int:
    _tree_store, promotions, _principal = await _promotion_store(actor)
    rows = await promotions.audit_trail(promotion_id)
    if not rows:
        print(f"No audit rows for {promotion_id}.")
        return 1
    for row in rows:
        print(
            f"{row['at']:%Y-%m-%d %H:%M} {row['action']} by "
            f"{row['actor_user_id']} ({row['actor_role']}) {row['detail']}"
        )
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _run(coro) -> int:
    try:
        return asyncio.run(coro)
    except (GoalTreeError, PermissionError, RuntimeError, ValueError) as error:
        print(f"{error}", file=sys.stderr)
        return 1


def goal_command(args: argparse.Namespace) -> int:
    actor = args.actor
    action = args.goal_command
    if action == "tree":
        return _run(_tree(actor))
    if action == "parent":
        return _run(_set_parent(actor, args.goal_id, args.parent_goal_id))
    if action == "tier":
        return _run(_set_tier(actor, args.goal_id, args.tier))
    if action == "metric":
        return _run(_set_metric(actor, args.goal_id, args.metric))
    if action == "edit":
        return _run(
            _edit(
                actor,
                args.goal_id,
                title=args.title,
                description=args.description,
            )
        )
    if action == "audit":
        return _run(_publish_audit(actor, args.goal_id))
    if action == "rollup":
        return _run(_rollup(actor, args.goal_id))
    if action == "ladder":
        return _run(_ladder(actor, args.goal_id))
    if action == "publish":
        return _run(_publish(actor, args.profile or []))
    if action == "stale":
        return _run(_stale(actor))
    if action == "default":
        return _run(_default_goal(actor))
    if action == "sync":
        return _run(_sync(actor))
    if action == "conflicts":
        return _run(_conflicts(actor, alert=args.alert))
    if action == "decide":
        return _run(
            _decide(actor, args.left_goal_id, args.right_goal_id, args.decision)
        )
    print(f"Unknown goal action: {action}", file=sys.stderr)
    return 2


def promotion_command(args: argparse.Namespace) -> int:
    actor = args.actor
    action = args.promotion_command
    if action == "propose":
        return _run(
            _propose(
                actor,
                args.skill,
                rationale=args.rationale,
                goal_id=args.goal,
                private=args.private_derived,
                consent=args.consent or [],
            )
        )
    if action == "list":
        return _run(_promotion_list(actor, all_states=args.all))
    if action == "digest":
        return _run(_digest(actor))
    if action == "approve":
        return _run(_approve(actor, args.promotion_id, note=args.note))
    if action == "reject":
        return _run(_reject(actor, args.promotion_id, args.reason))
    if action == "demote":
        return _run(_demote(actor, args.skill, args.reason))
    if action == "shared":
        return _run(_shared(actor))
    if action == "audit":
        return _run(_audit(actor, args.promotion_id))
    print(f"Unknown promotion action: {action}", file=sys.stderr)
    return 2


def register_goal_tree_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``hermes goal`` (tree, publish, measure, conflicts)."""
    parser = subparsers.add_parser(
        "goal",
        help="The entity's goal tree: ladder, measure, publish, conflicts",
        description=(
            "The owner's goal is the root and every sub-goal ladders into it. "
            "Goal lifetime decides where a goal may appear: entity and profile "
            "goals may sit in the stable prompt tier, participant goals in the "
            "volatile one, and an operational goal never reaches a prompt at "
            "all. Tier changes take effect in the next session."
        ),
    )
    parser.add_argument(
        "--actor", default=None, help="Principal to act as (default: the owner)"
    )
    sub = parser.add_subparsers(dest="goal_command", required=True)

    sub.add_parser("tree", help="Print the goal tree with each goal's prompt tier")

    parent = sub.add_parser("parent", help="Ladder one goal into another")
    parent.add_argument("goal_id")
    parent.add_argument("parent_goal_id")

    tier = sub.add_parser("tier", help="Change a goal's tier (next session)")
    tier.add_argument("goal_id")
    tier.add_argument("tier", choices=list(GOAL_TIERS))

    metric = sub.add_parser("metric", help="Designate the goal's primary measure")
    metric.add_argument("goal_id")
    metric.add_argument("metric", help="Name of an existing metric on the goal")

    edit = sub.add_parser(
        "edit",
        help="Edit a prompt-tier goal's text (owner only, next session)",
    )
    edit.add_argument("goal_id")
    edit.add_argument("--title", default=None)
    edit.add_argument("--description", default=None)

    audit = sub.add_parser(
        "audit", help="Who published which revision of a goal, into which profile"
    )
    audit.add_argument("goal_id")

    rollup = sub.add_parser("rollup", help="Roll measurable child progress up")
    rollup.add_argument("goal_id")

    ladder = sub.add_parser("ladder", help="Show the chain from a goal to the root")
    ladder.add_argument("goal_id")

    publish = sub.add_parser(
        "publish",
        help="Copy the entity goal into every profile (owner only)",
        description=(
            "Profiles are isolated, so the entity goal is published as a copy "
            "with provenance and a staleness flag — not read live across the "
            "boundary. Re-run it after editing the source goal."
        ),
    )
    publish.add_argument(
        "--into",
        dest="profile",
        action="append",
        default=None,
        metavar="PROFILE",
        help="Publish only into this profile (repeatable; default: all)",
    )

    sub.add_parser("stale", help="Published copies here that are behind their source")
    sub.add_parser("default", help="Create the default first entity goal if absent")
    sub.add_parser("sync", help="Rewrite the purpose snapshot for new sessions")

    conflicts = sub.add_parser(
        "conflicts",
        help="Detect sibling goals pulling against each other",
        description=(
            "Reports only. Two sub-goals can each be served correctly and "
            "still contradict; which one yields is the owner's decision, never "
            "the system's."
        ),
    )
    conflicts.add_argument(
        "--alert",
        action="store_true",
        help="Also raise a notification for the owner",
    )

    decide = sub.add_parser(
        "decide",
        help="Record your decision about two goals in tension (against both)",
    )
    decide.add_argument("left_goal_id")
    decide.add_argument("right_goal_id")
    decide.add_argument("decision", help="What you decided, and why")

    parser.set_defaults(func=goal_command)


def register_promotion_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``hermes promotion`` (the audited path into shared skills)."""
    parser = subparsers.add_parser(
        "promotion",
        help="Promote a distilled skill out of one profile into the shared tier",
        description=(
            "The only way a skill crosses the profile boundary. Scored and "
            "reviewed weekly, capped so promotion is competitive, and approved "
            "twice: the origin profile's reviewer first, then the owner. "
            "Autonomous curation cannot write the shared tier."
        ),
    )
    parser.add_argument(
        "--actor", default=None, help="Principal to act as (default: the owner)"
    )
    sub = parser.add_subparsers(dest="promotion_command", required=True)

    propose = sub.add_parser("propose", help="Propose one of this profile's skills")
    propose.add_argument("skill", help="Skill name as it appears in this profile")
    propose.add_argument("--rationale", default="", help="Why it should be shared")
    propose.add_argument(
        "--goal", default=None, help="Goal it helped (its measure scores the candidate)"
    )
    propose.add_argument(
        "--private-derived",
        action="store_true",
        help="Set when the skill was distilled from someone's private material",
    )
    propose.add_argument(
        "--consent",
        action="append",
        default=None,
        help="User id who consented to sharing (repeatable; required if private)",
    )

    listing = sub.add_parser("list", help="Open proposals, strongest first")
    listing.add_argument(
        "--all", action="store_true", help="Include decided proposals"
    )

    sub.add_parser("digest", help="The weekly review: candidates, demotions, conflicts")

    approve = sub.add_parser(
        "approve",
        help="Approve the next stage (profile reviewer, then owner)",
    )
    approve.add_argument("promotion_id")
    approve.add_argument("--note", default="", help="Recorded with the decision")

    reject = sub.add_parser("reject", help="Refuse a proposal (the row remains)")
    reject.add_argument("promotion_id")
    reject.add_argument("reason")

    demote = sub.add_parser(
        "demote",
        help="Remove a shared skill; its origin profile keeps its copy",
    )
    demote.add_argument("skill")
    demote.add_argument("reason")

    sub.add_parser("shared", help="What is in the shared library, and the cap")

    audit = sub.add_parser("audit", help="The full audit trail of one proposal")
    audit.add_argument("promotion_id")

    parser.set_defaults(func=promotion_command)


__all__ = [
    "goal_command",
    "promotion_command",
    "register_goal_tree_subparser",
    "register_promotion_subparser",
]
