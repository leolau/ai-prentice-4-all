"""Sibling goals that pull against each other, and the weekly review.

Two sub-goals can each be served perfectly and still contradict — hold cash
*and* spend on quality, ship faster *and* cut defects. Nothing in the goal
registry notices, because each goal looks healthy from inside itself. The
tension only appears when you compare siblings, and it is the single thing an
owner most wants to be told about, because it is the one thing only they can
resolve.

So this module detects and *reports*. It never reprioritises, never pauses a
goal, never edits a priority. An automatic resolution would be the system
quietly choosing which of the owner's two intentions matters less.

Three signals, all read from data that already exists:

* **Antagonistic metrics** — sibling metrics that moved in opposite directions
  over a shared window, repeatedly. One crossing is noise; a pattern is a
  conflict. Direction-awareness matters: two ``at_most`` metrics both rising is
  not antagonism, it is two goals both losing.
* **Resource contention** — two siblings whose ``goal_links`` (FG-09) point at
  the same resource. The same person, tool or task cannot be fully committed
  twice.
* **Stated blockage** — a progress note on one sibling that names the other as
  what is in the way. Someone already said it; the system should not need to
  infer it.

The digest and the alert both go through the existing FG-10 notification store,
so they arrive where the owner already looks. No SMTP, no new channel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from hermes_cli.goal_management import GOAL_LINKS_TABLE
from hermes_cli.goal_registry import GoalRecord
from hermes_cli.goal_tree import GoalTreeStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg

    from hermes_cli.access import Principal
    from hermes_cli.human_comms import NotificationStore
    from hermes_cli.skill_promotion import SkillPromotionStore

log = logging.getLogger(__name__)

#: How many opposed movements in the shared window make a pattern rather than a
#: coincidence. **Uncalibrated guess.**
MIN_OPPOSED_OBSERVATIONS = 2

#: How far back to look for opposed movement. **Uncalibrated guess.**
DEFAULT_WINDOW = timedelta(days=30)

CONFLICT_KINDS: Tuple[str, ...] = (
    "antagonistic_metrics",
    "resource_contention",
    "stated_blockage",
)

#: How much of a failure's text a digest line carries. Enough to recognise the
#: cause; the log has the rest.
_REASON_CHARS = 160


def _unavailable(section: str, exc: BaseException) -> str:
    """The line a section that could not run contributes to the digest.

    A section whose store is unreachable used to contribute *nothing*, which in
    a digest is indistinguishable from a section with nothing to report — and
    with every section failing, the digest read "Nothing to review this week".
    The three optional sections stay optional (one unconfigured store must not
    cost the owner the rest of the review), but they say so.
    """
    log.warning("digest: %s not rendered: %s", section, exc)
    reason = " ".join(f"{type(exc).__name__}: {exc}".split())
    if len(reason) > _REASON_CHARS:
        reason = reason[: _REASON_CHARS - 1] + "…"
    return f"{section}: unavailable — {reason}"


@dataclass(frozen=True)
class Conflict:
    """One detected tension between two sibling goals."""

    kind: str
    left: GoalRecord
    right: GoalRecord
    #: Human-readable evidence — what was observed, not what to do about it.
    evidence: str
    window_start: Optional[datetime]
    window_end: Optional[datetime]

    @property
    def dedupe_key(self) -> str:
        """Stable across re-detection so one tension is one notification."""
        first, second = sorted((self.left.id, self.right.id))
        return f"goal-conflict:{self.kind}:{first}:{second}"

    def title(self) -> str:
        return (
            f"Sibling goals pulling apart: {self.left.title} vs {self.right.title}"
        )

    def body(self) -> str:
        window = ""
        if self.window_start and self.window_end:
            window = (
                f"\nWindow: {self.window_start:%Y-%m-%d} to "
                f"{self.window_end:%Y-%m-%d}"
            )
        return (
            f"{self.left.title} ({self.left.id})\n"
            f"{self.right.title} ({self.right.id})\n\n"
            f"Evidence: {self.evidence}{window}\n\n"
            f"Nothing has been changed. Both goals are still active at their "
            f"current priorities — deciding between them is yours. Record the "
            f"decision with `hermes goal decide {self.left.id} "
            f"{self.right.id} \"...\"`."
        )


async def detect_conflicts(
    tree: GoalTreeStore,
    principal: "Principal",
    *,
    parent_goal_id: Optional[str] = None,
    now: Optional[datetime] = None,
    window: timedelta = DEFAULT_WINDOW,
    connection: Optional["asyncpg.Connection"] = None,
) -> List[Conflict]:
    """Every detectable tension among siblings, deduplicated by pair and kind.

    Runs over sibling *sets*, so the work is bounded by the number of goals
    under one parent, not by the whole tree — and it runs on the digest/close
    path, never per turn.
    """
    moment = now or datetime.now(timezone.utc)
    own = connection is None
    conn = connection or await tree.registry._connect()
    try:
        goals = await tree.registry.list_goals(
            principal, status="active", connection=conn
        )
        groups: Dict[str, List[GoalRecord]] = {}
        for goal in goals:
            key = goal.parent_goal_id or f"__roots__:{goal.tier}"
            if parent_goal_id is not None and goal.parent_goal_id != parent_goal_id:
                continue
            groups.setdefault(key, []).append(goal)

        conflicts: List[Conflict] = []
        for siblings in groups.values():
            if len(siblings) < 2:
                continue
            for index, left in enumerate(siblings):
                for right in siblings[index + 1 :]:
                    conflicts.extend(
                        await _pair_conflicts(
                            tree,
                            principal,
                            left,
                            right,
                            conn=conn,
                            now=moment,
                            window=window,
                        )
                    )
        return conflicts
    finally:
        if own:
            await conn.close()


async def _pair_conflicts(
    tree: GoalTreeStore,
    principal: "Principal",
    left: GoalRecord,
    right: GoalRecord,
    *,
    conn: "asyncpg.Connection",
    now: datetime,
    window: timedelta,
) -> List[Conflict]:
    found: List[Conflict] = []
    start = now - window
    antagonism = await _antagonistic_metrics(
        tree, principal, left, right, conn=conn, start=start
    )
    if antagonism is not None:
        found.append(
            Conflict(
                kind="antagonistic_metrics",
                left=left,
                right=right,
                evidence=antagonism,
                window_start=start,
                window_end=now,
            )
        )
    contention = await _resource_contention(left, right, conn=conn)
    if contention is not None:
        found.append(
            Conflict(
                kind="resource_contention",
                left=left,
                right=right,
                evidence=contention,
                window_start=None,
                window_end=None,
            )
        )
    blockage = await _stated_blockage(left, right, conn=conn, start=start)
    if blockage is not None:
        found.append(
            Conflict(
                kind="stated_blockage",
                left=left,
                right=right,
                evidence=blockage,
                window_start=start,
                window_end=now,
            )
        )
    return found


def _direction_of(delta: float, metric_direction: str) -> int:
    """``+1`` when the goal got better, ``-1`` worse, ``0`` flat.

    This is where direction-awareness earns its place: for an ``at_most``
    metric (cost, defects, hours) a *fall* is progress, so the raw sign of the
    delta says nothing on its own.
    """
    if delta == 0:
        return 0
    improving = delta > 0 if metric_direction != "at_most" else delta < 0
    return 1 if improving else -1


async def _metric_series(
    conn: "asyncpg.Connection",
    goal: GoalRecord,
    *,
    start: datetime,
) -> Dict[date, float]:
    """One value per day for a goal's primary measure, latest wins.

    Bucketed by day rather than returned raw because the comparison downstream
    is *between two goals*, and two goals are not measured on the same
    schedule. Zipping raw rows by position would compare Monday's cashflow with
    March's defect count and call the result antagonism.
    """
    if not goal.primary_metric:
        return {}
    rows = await conn.fetch(
        """
        SELECT ts, value FROM goal_progress
        WHERE goal_id = $1 AND metric_name = $2 AND value IS NOT NULL
          AND ts >= $3
        ORDER BY ts
        """,
        goal.id,
        goal.primary_metric,
        start,
    )
    series: Dict[date, float] = {}
    for row in rows:
        series[row["ts"].date()] = float(row["value"])
    return series


async def _antagonistic_metrics(
    tree: GoalTreeStore,
    principal: "Principal",
    left: GoalRecord,
    right: GoalRecord,
    *,
    conn: "asyncpg.Connection",
    start: datetime,
) -> Optional[str]:
    """Repeated opposed movement of two siblings' primary measures.

    Requires the shared measure to exist on both sides — which is exactly why
    FG-29 insists on one comparable metric per goal. Without it this check
    cannot run at all, and the owner is told nothing.
    """
    left_series = await _metric_series(conn, left, start=start)
    right_series = await _metric_series(conn, right, start=start)
    # Only days on which *both* goals were measured can say anything about the
    # two moving against each other.
    shared = sorted(set(left_series) & set(right_series))
    if len(shared) < 2:
        return None
    left_dir = await _metric_direction(tree, principal, left, conn=conn)
    right_dir = await _metric_direction(tree, principal, right, conn=conn)
    if left_dir is None or right_dir is None:
        return None

    opposed = 0
    for previous, current in zip(shared, shared[1:]):
        left_sign = _direction_of(
            left_series[current] - left_series[previous], left_dir
        )
        right_sign = _direction_of(
            right_series[current] - right_series[previous], right_dir
        )
        if left_sign != 0 and right_sign != 0 and left_sign != right_sign:
            opposed += 1
    if opposed < MIN_OPPOSED_OBSERVATIONS:
        return None
    return (
        f"{left.primary_metric} and {right.primary_metric} moved in opposite "
        f"directions between {opposed} of the {len(shared) - 1} intervals on "
        f"which both were measured (one improved while the other regressed, "
        f"accounting for each metric's direction)"
    )


async def _metric_direction(
    tree: GoalTreeStore,
    principal: "Principal",
    goal: GoalRecord,
    *,
    conn: "asyncpg.Connection",
) -> Optional[str]:
    metrics = await tree.registry.list_metrics(principal, goal.id, connection=conn)
    for metric in metrics:
        if metric.name == goal.primary_metric:
            return metric.direction
    return None


async def _resource_contention(
    left: GoalRecord,
    right: GoalRecord,
    *,
    conn: "asyncpg.Connection",
) -> Optional[str]:
    # ``goal_links`` belongs to FG-09's goal-management service, which a
    # deployment may not have initialised. Its absence means "no resource
    # commitments recorded", not an error worth failing the whole sweep for.
    # asyncpg is a lazy dependency, so its exception type is imported here
    # rather than at module scope.
    from asyncpg.exceptions import UndefinedTableError

    try:
        rows = await conn.fetch(
            f"""
            SELECT a.resource_kind, a.resource_ref
            FROM {GOAL_LINKS_TABLE} a
            JOIN {GOAL_LINKS_TABLE} b
              ON a.resource_kind = b.resource_kind
             AND a.resource_ref = b.resource_ref
            WHERE a.goal_id = $1 AND b.goal_id = $2
            ORDER BY a.resource_kind, a.resource_ref
            """,
            left.id,
            right.id,
        )
    except UndefinedTableError:
        return None
    if not rows:
        return None
    shared = ", ".join(f"{row['resource_kind']}:{row['resource_ref']}" for row in rows)
    return f"both goals are committed to the same resource(s): {shared}"


async def _stated_blockage(
    left: GoalRecord,
    right: GoalRecord,
    *,
    conn: "asyncpg.Connection",
    start: datetime,
) -> Optional[str]:
    """A progress note on one goal that names the other as the obstacle."""
    rows = await conn.fetch(
        """
        SELECT goal_id, note, ts FROM goal_progress
        WHERE goal_id = ANY($1::uuid[]) AND ts >= $2 AND note <> ''
        ORDER BY ts DESC
        LIMIT 200
        """,
        [left.id, right.id],
        start,
    )
    for row in rows:
        note = str(row["note"])
        lowered = note.lower()
        if "block" not in lowered and "conflict" not in lowered:
            continue
        other = right if str(row["goal_id"]) == left.id else left
        if other.id in note or other.title.lower() in lowered:
            return f"a progress note names the sibling as blocking: {note.strip()!r}"
    return None


async def alert_owner(
    notifications: "NotificationStore",
    principal: "Principal",
    conflicts: Sequence[Conflict],
    *,
    owner_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[str]:
    """Tell the owner immediately, once per tension. Returns notification ids.

    A ``proactive_ask``, not an ``approval``: the C6 policy may auto-answer a
    reversible approval, and a conflict must never be auto-answered — there is
    nothing to approve, only something to decide.
    """
    target = owner_user_id or principal.user_id
    ids: List[str] = []
    for conflict in conflicts:
        try:
            result = await notifications.create(
                kind="proactive_ask",
                target_user_id=target,
                title=conflict.title(),
                body=conflict.body(),
                command="",
                reversible=True,
                dedupe_key=conflict.dedupe_key,
                now=now,
            )
            ids.append(result.notification.id)
        except Exception as exc:  # noqa: BLE001 - one alert is not the batch
            log.warning("goal conflict: could not alert on %s (%s)", conflict.kind, exc)
    return ids


async def record_decision(
    tree: GoalTreeStore,
    principal: "Principal",
    conflict: Conflict,
    *,
    decision: str,
    connection: Optional["asyncpg.Connection"] = None,
) -> None:
    """Write the owner's decision against **both** goals.

    Against both deliberately: six months later the question is asked from
    whichever goal the reader is standing on, and a decision recorded only on
    the winner leaves the loser looking merely neglected.
    """
    text = (decision or "").strip()
    if not text:
        raise ValueError("A conflict decision needs to say something")
    note = f"conflict ({conflict.kind}) decision by {principal.user_id}: {text}"
    for goal in (conflict.left, conflict.right):
        await tree.registry.record_progress(
            principal, goal.id, note=note, connection=connection
        )


async def record_decision_for_pair(
    tree: GoalTreeStore,
    principal: "Principal",
    left_goal_id: str,
    right_goal_id: str,
    *,
    decision: str,
    kind: str = "unspecified",
    connection: Optional["asyncpg.Connection"] = None,
) -> Tuple[GoalRecord, GoalRecord]:
    """Record a decision about two goals named by id (the CLI's entry point).

    Does not require the conflict to still be detectable: the owner may decide
    weeks after the evidence stopped accumulating, and the decision is still the
    thing worth keeping.
    """
    left = await tree.registry.get_goal(principal, left_goal_id, connection=connection)
    right = await tree.registry.get_goal(
        principal, right_goal_id, connection=connection
    )
    if left is None or right is None:
        raise PermissionError(
            "Both goals must exist and be visible to you to record a decision"
        )
    await record_decision(
        tree,
        principal,
        Conflict(
            kind=kind,
            left=left,
            right=right,
            evidence="decided by the owner",
            window_start=None,
            window_end=None,
        ),
        decision=decision,
        connection=connection,
    )
    return left, right


async def weekly_digest(
    tree: GoalTreeStore,
    promotions: "SkillPromotionStore",
    principal: "Principal",
    *,
    now: Optional[datetime] = None,
) -> Tuple[str, List[str]]:
    """``(title, lines)`` for the weekly review the owner reads.

    One digest covers both flows because they are the same weekly minute: what
    the entity might learn (promotion candidates, demotions) and where its
    goals are pulling apart. Everything in it is text, so it can be delivered
    through a notification, printed by the CLI, or rendered in agent-home
    without a delivery dependency.
    """
    from hermes_cli.skill_promotion import digest_lines

    lines: List[str] = []
    candidates = await promotions.digest_candidates(principal)
    demotions = await promotions.demotion_candidates(now=now)
    promotion_lines = digest_lines(candidates, demotions)
    if promotion_lines:
        lines.append("Skill promotion:")
        lines.extend(f"  {line}" for line in promotion_lines)

    conflicts = await detect_conflicts(tree, principal, now=now)
    if conflicts:
        lines.append("Sibling goals pulling apart:")
        lines.extend(
            f"  {conflict.left.title} vs {conflict.right.title} "
            f"({conflict.kind}): {conflict.evidence}"
            for conflict in conflicts
        )

    unmeasured = await tree.unmeasured_long_lived(principal, now=now)
    if unmeasured:
        lines.append("Long-lived goals with no measure (reported, not scored):")
        lines.extend(f"  {goal.title} ({goal.tier})" for goal in unmeasured)

    stale_copies = await tree.stale_published_copies(principal)
    if stale_copies:
        lines.append("Published entity-goal copies behind their source:")
        lines.extend(f"  {goal.title} (from {goal.published_from_profile})" for goal in stale_copies)

    # Profile suggestion (FG-30). The digest only *renders* an open
    # suggestion — generation is a separate monthly pass. An open suggestion
    # appears in every weekly review until the owner reviews it.
    try:
        from hermes_cli.datastore import get_store
        from hermes_cli.profile_suggestion import (
            ProfileSuggestionStore,
            digest_lines as suggestion_digest_lines,
        )

        suggestion_store = ProfileSuggestionStore(get_store("supabase-app", "prod"))
        # "prod" hard-coded by decision (FG-30 §4.2 T3 Q2): the digest's
        # suggestion render is a one-tier C3 consumer with no dev context;
        # the same assumption is recorded in profile_suggestion._resolve_store.
        suggestion = await suggestion_store.digest_suggestion(principal)
        suggestion_lines = suggestion_digest_lines(suggestion)
        if suggestion_lines:
            lines.append("Profile suggestion:")
            lines.extend(f"  {line}" for line in suggestion_lines)
    except Exception as exc:
        lines.append(_unavailable("Profile suggestion", exc))

    # Idle profile detection (FG-30). Flag profiles with no sessions for
    # N weeks rather than waiting for someone to notice.
    idle_names: List[str] = []
    try:
        from hermes_cli.profile_suggestion import (
            idle_lines as idle_digest_lines,
            idle_profiles,
        )

        idle = await idle_profiles(now=now)
        idle_names = [name for name, _age in idle]
        idle_lines_rendered = idle_digest_lines(idle)
        if idle_lines_rendered:
            lines.append("Profiles with no recent sessions:")
            lines.extend(f"  {line}" for line in idle_lines_rendered)
    except Exception as exc:
        lines.append(_unavailable("Profiles with no recent sessions", exc))

    # Capacity headroom (FG-31). Arrives in the same review moment as
    # everything else, and reuses the idle profiles just computed as its
    # cheapest recommendation.
    try:
        from hermes_cli.capacity import (
            COMFORTABLE,
            digest_lines as capacity_digest_lines,
            headroom,
        )
        from hermes_cli.config import load_config

        verdict = headroom(load_config(), idle_profiles=idle_names)
        # A comfortable box says the reading and the verdict, nothing to act on.
        capacity_lines = (
            capacity_digest_lines(verdict)[:2]
            if verdict.state == COMFORTABLE
            else capacity_digest_lines(verdict)
        )
        lines.append("Capacity:")
        lines.extend(f"  {line}" for line in capacity_lines)
    except Exception as exc:
        lines.append(_unavailable("Capacity", exc))

    if not lines:
        lines.append("Nothing to review this week.")
    return "Weekly entity review", lines


__all__ = [
    "CONFLICT_KINDS",
    "Conflict",
    "DEFAULT_WINDOW",
    "MIN_OPPOSED_OBSERVATIONS",
    "alert_owner",
    "detect_conflicts",
    "record_decision",
    "record_decision_for_pair",
    "weekly_digest",
]
