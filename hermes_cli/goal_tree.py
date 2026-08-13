"""The FG-29 goal tree: lifetime tiers, the shared measure, the publish.

The registry from FG-04/FG-09 already stores goals, metrics and progress. This
module adds the three things that make a *hierarchy* out of a flat list, and it
adds them as behaviour over the same tables rather than as a second goal
system:

* **The ladder.** ``goals.parent_goal_id`` plus ``goals.tier`` give an
  operational goal a chain upward to the entity goal it ultimately serves.
  The chain is validated on write — a parent must outlive its child, cycles
  are refused, and the tree may be at most :data:`MAX_TREE_DEPTH` deep, which
  is exactly the four named tiers.
* **The shared measure.** ``goals.primary_metric`` designates *one* of a
  goal's metrics as the comparable one, so a child's progress can roll into
  its parent and two siblings can be compared at all. Normalisation is
  direction-aware and is deliberately the FG-04
  :class:`~hermes_cli.goals.GoalMetric` computation, not a second opinion.
* **The publish.** Profiles are isolated schemas (FG-27), so the entity goal
  reaches a profile as an owner-initiated *copy* carrying provenance and a
  revision, never as a live cross-profile read. An entity-goal edit bumps the
  revision, which marks every copy stale; re-publishing clears it.

Why the tier is not merely a label: it decides whether a goal may enter the
system prompt at all (see :mod:`hermes_cli.goal_purpose`). A goal that can
change mid-session must never sit in a cached prefix, so ``operational`` is
the default tier, is refused by the prompt path, and stays tool-appended
exactly as FG-09 has it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from hermes_cli.goal_registry import (
    GOAL_COLUMNS,
    GOAL_TIERS,
    GOALS_TABLE,
    GoalRecord,
    GoalRegistryStore,
    normalize_tier,
)
from hermes_cli.goals import GoalMetric

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg

    from hermes_cli.access import Principal

log = logging.getLogger(__name__)

#: The four named tiers *are* the depth limit: entity → profile → participant
#: → operational. A fifth level would have no lifetime to describe it.
MAX_TREE_DEPTH = len(GOAL_TIERS)

#: Tiers whose goals may be rendered into a prompt at all.
PROMPT_TIERS: Tuple[str, ...] = ("entity", "profile", "participant")

#: Tiers that may sit in the *stable* (cached-prefix) tier: measured in
#: quarters and years, so a change between sessions is the fast case.
STABLE_PROMPT_TIERS: Tuple[str, ...] = ("entity", "profile")

#: Tiers that go in the volatile tier, beside ``USER.md``: a participant goal
#: is long-lived but belongs to whoever is talking, so it is per-participant
#: rather than per-session-stable.
VOLATILE_PROMPT_TIERS: Tuple[str, ...] = ("participant",)

#: How long a long-lived goal may sit without a designated primary metric
#: before it is *reported* rather than scored. An unmeasured goal is not a
#: goal at 0% — that number would roll into its parent and lie.
#: **Uncalibrated guess**, overridable via ``goals.measure.unmeasured_after``.
DEFAULT_UNMEASURED_AFTER = timedelta(days=14)

_TIER_RANK: Dict[str, int] = {tier: index for index, tier in enumerate(GOAL_TIERS)}


class GoalTreeError(RuntimeError):
    """Base class for a refused goal-tree operation."""


class GoalCycleError(GoalTreeError):
    """The requested parent would close a cycle."""


class GoalDepthError(GoalTreeError):
    """The requested parent would push the tree past :data:`MAX_TREE_DEPTH`."""


class GoalTierError(GoalTreeError):
    """The requested parent/child tiers cannot stand in that relation."""


def tier_rank(tier: str) -> int:
    """Rank a tier by lifetime: ``entity`` is 0, ``operational`` is last."""
    return _TIER_RANK[normalize_tier(tier)]


def may_enter_prompt(tier: str) -> bool:
    """Whether a goal of ``tier`` may be rendered into a prompt at all."""
    return normalize_tier(tier) in PROMPT_TIERS


def prompt_slot(tier: str) -> str:
    """Which prompt tier a goal lifetime maps onto.

    ``"stable"`` for entity/profile, ``"volatile"`` for participant, and
    ``"never"`` for operational. The mapping lives here, once, so the prompt
    assembler cannot accidentally hold a different opinion from the store.
    """
    resolved = normalize_tier(tier)
    if resolved in STABLE_PROMPT_TIERS:
        return "stable"
    if resolved in VOLATILE_PROMPT_TIERS:
        return "volatile"
    return "never"


async def _load_tier(
    conn: "asyncpg.Connection", goal_id: str
) -> Optional[Tuple[str, Optional[str]]]:
    row = await conn.fetchrow(
        f"SELECT tier, parent_goal_id FROM {GOALS_TABLE} WHERE id = $1",
        goal_id,
    )
    if row is None:
        return None
    parent = row["parent_goal_id"]
    return str(row["tier"]), (str(parent) if parent is not None else None)


async def validate_parent(
    conn: "asyncpg.Connection",
    *,
    child_id: Optional[str],
    child_tier: str,
    parent_goal_id: str,
) -> None:
    """Refuse a parent link that could not resolve upward.

    ``child_id`` is ``None`` while the child is still being inserted, in which
    case there is nothing to close a cycle with yet — the depth and tier
    checks still apply.

    The checks are deliberately at write time. Validating at read time would
    mean a malformed tree is discovered while building a system prompt, which
    is the one place that must not fail.
    """
    resolved_child_tier = normalize_tier(child_tier)
    parent = await _load_tier(conn, parent_goal_id)
    if parent is None:
        raise GoalTreeError(f"Parent goal {parent_goal_id} does not exist")
    parent_tier, _ = parent

    if tier_rank(parent_tier) >= tier_rank(resolved_child_tier):
        raise GoalTierError(
            f"A {resolved_child_tier} goal cannot hang beneath a "
            f"{parent_tier} goal: a parent must outlive its child"
        )

    # Walk upward from the proposed parent: that chain plus the child is the
    # depth, and meeting the child on the way up is the cycle.
    depth = 2  # the child plus its proposed parent
    cursor: Optional[str] = parent[1]
    seen = {parent_goal_id}
    while cursor is not None:
        if child_id is not None and cursor == child_id:
            raise GoalCycleError(
                f"Goal {parent_goal_id} already descends from {child_id}; "
                f"linking them would close a cycle"
            )
        if cursor in seen:
            raise GoalCycleError(
                f"The ancestry of goal {parent_goal_id} already contains a "
                f"cycle at {cursor}"
            )
        seen.add(cursor)
        depth += 1
        if depth > MAX_TREE_DEPTH:
            raise GoalDepthError(
                f"A goal tree may be at most {MAX_TREE_DEPTH} levels deep "
                f"(entity → profile → participant → operational)"
            )
        ancestor = await _load_tier(conn, cursor)
        if ancestor is None:  # pragma: no cover - FK makes this unreachable
            break
        cursor = ancestor[1]


@dataclass(frozen=True)
class Rollup:
    """A goal's progress, and an honest account of where it came from."""

    goal_id: str
    tier: str
    title: str
    #: Normalised progress of this goal's own primary metric, if it has one
    #: with a target. ``None`` means *unmeasured*, which is not zero.
    own: Optional[float]
    #: Mean of the children's rolled progress, over the children that have a
    #: measure. ``None`` when no child is measurable.
    children: Optional[float]
    #: What a caller should display: children when they exist, else own.
    progress: Optional[float]
    #: Children that could not contribute, by id, so the gap is visible.
    unmeasured_children: Tuple[str, ...] = ()

    @property
    def measured(self) -> bool:
        return self.progress is not None


@dataclass(frozen=True)
class PublishResult:
    """One profile's outcome from a downward publish."""

    profile: str
    goal_id: str
    revision: int
    created: bool


def normalized_progress(metric: GoalMetric) -> Optional[float]:
    """Direction-aware progress in ``[0, 1]``, or ``None`` when unmeasured.

    Deliberately delegates to :attr:`GoalMetric.progress_fraction` (FG-04)
    rather than recomputing: two normalisations that disagree would make a
    parent's roll-up contradict its own child's displayed progress. The one
    thing added here is that an unmeasured metric answers ``None`` instead of
    ``0.0`` — a goal nobody has set a target for has not failed.
    """
    if not metric.is_measurable():
        return None
    return metric.progress_fraction


class GoalTreeStore:
    """Tree, measure and publish operations over the existing goal registry.

    Composition, not inheritance: every read and write still goes through
    :class:`~hermes_cli.goal_registry.GoalRegistryStore`, so C2 scoping and
    the profile-derived schema resolution are inherited rather than restated.
    """

    def __init__(self, registry: GoalRegistryStore) -> None:
        self._registry = registry

    @property
    def registry(self) -> GoalRegistryStore:
        return self._registry

    async def _connect(self) -> "asyncpg.Connection":
        return await self._registry._connect()

    # -- tree ---------------------------------------------------------------

    async def set_parent(
        self,
        principal: "Principal",
        goal_id: str,
        parent_goal_id: Optional[str],
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Attach ``goal_id`` beneath ``parent_goal_id`` (or detach with None)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            goal = await self._registry.get_goal(principal, goal_id, connection=conn)
            if goal is None:
                raise PermissionError(
                    f"Goal {goal_id} not found or not visible to "
                    f"{principal.user_id}"
                )
            if parent_goal_id is not None:
                if (
                    await self._registry.get_goal(
                        principal, parent_goal_id, connection=conn
                    )
                    is None
                ):
                    raise PermissionError(
                        f"Parent goal {parent_goal_id} not found or not "
                        f"visible to {principal.user_id}"
                    )
                await validate_parent(
                    conn,
                    child_id=goal_id,
                    child_tier=goal.tier,
                    parent_goal_id=parent_goal_id,
                )
            return await self._registry._update_goal_field(
                principal,
                goal_id,
                "parent_goal_id",
                parent_goal_id,
                connection=conn,
            )
        finally:
            if own:
                await conn.close()

    async def set_tier(
        self,
        principal: "Principal",
        goal_id: str,
        tier: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Change a goal's lifetime tier.

        Only the owner may move a goal *into* a tier that reaches the prompt:
        promoting a goal to ``entity`` or ``profile`` puts its text in every
        session's cached prefix, which is an entity-level decision.

        The change is recorded immediately and observed **next session**. That
        is not a scheduling trick: the purpose block is built from a snapshot
        taken once per session (see :mod:`hermes_cli.goal_purpose`), so a live
        conversation keeps the bytes it started with and the cache survives.
        """
        resolved = normalize_tier(tier)
        own = connection is None
        conn = connection or await self._connect()
        try:
            goal = await self._registry.get_goal(principal, goal_id, connection=conn)
            if goal is None:
                raise PermissionError(
                    f"Goal {goal_id} not found or not visible to "
                    f"{principal.user_id}"
                )
            if resolved in STABLE_PROMPT_TIERS and not principal.is_owner:
                raise PermissionError(
                    f"Only the owner may put a goal in the {resolved} tier: "
                    f"its text enters every session's system prompt"
                )
            if goal.parent_goal_id is not None:
                await validate_parent(
                    conn,
                    child_id=goal_id,
                    child_tier=resolved,
                    parent_goal_id=goal.parent_goal_id,
                )
            if resolved != "entity":
                return await self._registry._update_goal_field(
                    principal, goal_id, "tier", resolved, connection=conn
                )
            existing = await self._active_entity_goal(principal, connection=conn)
            if existing is not None and existing.id != goal_id:
                raise GoalTreeError(
                    f"Goal {existing.id} is already the active entity goal; "
                    f"an entity has exactly one root"
                )
            return await self._registry._update_goal_field(
                principal, goal_id, "tier", resolved, connection=conn
            )
        finally:
            if own:
                await conn.close()

    async def set_primary_metric(
        self,
        principal: "Principal",
        goal_id: str,
        metric_name: Optional[str],
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Designate the one comparable measure for ``goal_id``."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            if metric_name is not None:
                metrics = await self._registry.list_metrics(
                    principal, goal_id, connection=conn
                )
                names = {metric.name for metric in metrics}
                if metric_name not in names:
                    raise GoalTreeError(
                        f"Goal {goal_id} has no metric named {metric_name!r}; "
                        f"add the metric before designating it"
                    )
            return await self._registry._update_goal_field(
                principal, goal_id, "primary_metric", metric_name, connection=conn
            )
        finally:
            if own:
                await conn.close()

    async def set_entity_goal_text(
        self,
        principal: "Principal",
        goal_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Edit a prompt-tier goal's text, bumping the source revision.

        The bump is the point: the text of an entity goal is what every other
        profile received a copy of, and an edit that left those copies looking
        current would put a stale purpose in someone else's system prompt with
        nothing to indicate it. One revision bump makes every copy report
        itself behind the next time it is read.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            goal = await self._registry.get_goal(principal, goal_id, connection=conn)
            if goal is None:
                raise PermissionError(
                    f"Goal {goal_id} not found or not visible to "
                    f"{principal.user_id}"
                )
            if goal.tier in STABLE_PROMPT_TIERS and not principal.is_owner:
                raise PermissionError(
                    f"Only the owner may edit a {goal.tier} goal: its text is "
                    f"in every session's system prompt"
                )
            updated = goal
            if title is not None:
                cleaned = title.strip()
                if not cleaned:
                    raise ValueError("A goal needs a title")
                updated = await self._registry._update_goal_field(
                    principal, goal_id, "title", cleaned, connection=conn
                )
            if description is not None:
                updated = await self._registry._update_goal_field(
                    principal, goal_id, "description", description, connection=conn
                )
            if title is None and description is None:
                return updated
            return await self.bump_revision(principal, goal_id, connection=conn)
        finally:
            if own:
                await conn.close()

    async def children(
        self,
        principal: "Principal",
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GoalRecord]:
        """The goals hanging directly beneath ``goal_id`` that C2 allows."""
        goals = await self._registry.list_goals(principal, connection=connection)
        return [goal for goal in goals if goal.parent_goal_id == goal_id]

    async def ladder(
        self,
        principal: "Principal",
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GoalRecord]:
        """The chain from ``goal_id`` up to the goal it ultimately serves.

        Returned child-first, so ``ladder(...)[-1]`` is the root the goal
        rolls into. An **orphan** operational goal — one nobody attached to
        anything — resolves to the profile goal, then onward: the common case
        of a goal created in passing during a session should still ladder into
        why the profile exists, rather than dangling.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            goals = {
                goal.id: goal
                for goal in await self._registry.list_goals(principal, connection=conn)
            }
            start = goals.get(goal_id)
            if start is None:
                return []
            chain: List[GoalRecord] = [start]
            seen = {start.id}
            cursor = start
            while True:
                parent_id = cursor.parent_goal_id
                if parent_id is None:
                    fallback = self._orphan_fallback(cursor, goals)
                    if fallback is None or fallback.id in seen:
                        break
                    parent_id = fallback.id
                parent = goals.get(parent_id)
                if parent is None or parent.id in seen:
                    break
                chain.append(parent)
                seen.add(parent.id)
                cursor = parent
                if len(chain) >= MAX_TREE_DEPTH:
                    break
            return chain
        finally:
            if own:
                await conn.close()

    @staticmethod
    def _orphan_fallback(
        goal: GoalRecord, goals: Dict[str, GoalRecord]
    ) -> Optional[GoalRecord]:
        """The implicit parent of an unattached goal, if there is one."""
        wanted = {
            "operational": ("profile", "entity"),
            "participant": ("profile", "entity"),
            "profile": ("entity",),
        }.get(goal.tier)
        if not wanted:
            return None
        for tier in wanted:
            candidates = [
                candidate
                for candidate in goals.values()
                if candidate.tier == tier and candidate.status == "active"
            ]
            if candidates:
                return sorted(candidates, key=lambda item: item.id)[0]
        return None

    async def _active_entity_goal(
        self,
        principal: "Principal",
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[GoalRecord]:
        goals = await self._registry.list_goals(
            principal, status="active", connection=connection
        )
        entity = [goal for goal in goals if goal.tier == "entity"]
        if not entity:
            return None
        # A published copy wins over a locally authored one. Both can exist —
        # a profile may have written its own before the owner published — and in
        # that state the owner's published text is the entity's goal, while the
        # local row is a draft the profile invented for itself.
        for goal in entity:
            if goal.is_published_copy:
                return goal
        return entity[0]

    async def entity_goal(
        self,
        principal: "Principal",
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[GoalRecord]:
        """The one active entity goal, local or published into this profile."""
        return await self._active_entity_goal(principal, connection=connection)

    # -- the shared measure -------------------------------------------------

    async def rollup(
        self,
        principal: "Principal",
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
        _depth: int = 0,
    ) -> Optional[Rollup]:
        """Progress for ``goal_id``, rolling its children's measures upward.

        A parent's number is the **mean of its children's** normalised
        progress, over the children that have a measure; a leaf reports its
        own. Unmeasured children are named rather than counted as zero, so a
        90% parent with one unmeasured child cannot look like 45%.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            goal = await self._registry.get_goal(principal, goal_id, connection=conn)
            if goal is None:
                return None
            own_progress = await self._own_progress(principal, goal, connection=conn)
            child_values: List[float] = []
            unmeasured: List[str] = []
            if _depth < MAX_TREE_DEPTH:
                for child in await self.children(principal, goal_id, connection=conn):
                    if child.status != "active":
                        continue
                    rolled = await self.rollup(
                        principal, child.id, connection=conn, _depth=_depth + 1
                    )
                    if rolled is None or rolled.progress is None:
                        unmeasured.append(child.id)
                    else:
                        child_values.append(rolled.progress)
            children_mean = (
                sum(child_values) / len(child_values) if child_values else None
            )
            return Rollup(
                goal_id=goal.id,
                tier=goal.tier,
                title=goal.title,
                own=own_progress,
                children=children_mean,
                progress=children_mean if children_mean is not None else own_progress,
                unmeasured_children=tuple(unmeasured),
            )
        finally:
            if own:
                await conn.close()

    async def _own_progress(
        self,
        principal: "Principal",
        goal: GoalRecord,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[float]:
        if not goal.primary_metric:
            return None
        metrics = await self._registry.list_metrics(
            principal, goal.id, connection=connection
        )
        for metric in metrics:
            if metric.name == goal.primary_metric:
                return normalized_progress(metric)
        return None

    async def unmeasured_long_lived(
        self,
        principal: "Principal",
        *,
        now: Optional[datetime] = None,
        older_than: timedelta = DEFAULT_UNMEASURED_AFTER,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GoalRecord]:
        """Long-lived active goals that have gone this long with no measure.

        These are reported, not scored: the roll-up leaves them out of the
        mean, and this list is what tells the owner *why* a parent has fewer
        contributing children than it has children.
        """
        moment = now or datetime.now(timezone.utc)
        goals = await self._registry.list_goals(
            principal, status="active", connection=connection
        )
        stale: List[GoalRecord] = []
        for goal in goals:
            if goal.tier not in PROMPT_TIERS or goal.primary_metric:
                continue
            created = goal.created_at
            if created is None or (moment - created) >= older_than:
                stale.append(goal)
        return stale

    # -- the downward publish ----------------------------------------------

    async def bump_revision(
        self,
        principal: "Principal",
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Record that the source goal changed, marking published copies stale.

        Staleness is derived, not pushed: a copy carries the revision it was
        made from, and any copy whose ``published_rev`` is behind the source's
        ``source_rev`` is stale. Nothing has to reach into another profile's
        schema on an edit — the profile learns it is behind the next time it
        looks, which is also the only moment the answer matters.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                UPDATE {GOALS_TABLE}
                SET source_rev = source_rev + 1, updated_at = NOW()
                WHERE id = $1 AND published_from_profile IS NULL
                RETURNING {GOAL_COLUMNS}
                """,
                goal_id,
            )
            if row is None:
                raise PermissionError(
                    f"Goal {goal_id} is not a locally-owned goal here"
                )
            await self._mark_copies_stale(conn, goal_id, int(row["source_rev"]))
            from hermes_cli.goal_registry import _row_to_goal

            return _row_to_goal(row)
        finally:
            if own:
                await conn.close()

    async def _mark_copies_stale(
        self,
        conn: "asyncpg.Connection",
        source_goal_id: str,
        revision: int,
    ) -> None:
        """Flag every copy of ``source_goal_id`` as behind its source.

        Which profiles to visit comes from the publish audit rather than from
        the profiles on the box: a profile that never received a copy has
        nothing to stale, and one that did must be reached even if it has since
        been renamed away. This runs on an owner's edit — a handful of times a
        quarter — and never per turn.
        """
        rows = await conn.fetch(
            """
            SELECT DISTINCT target_profile FROM goal_publish_audit
            WHERE source_goal_id = $1
            """,
            source_goal_id,
        )
        for profile in sorted(str(row["target_profile"]) for row in rows):
            try:
                target = await self._publish_connection(profile)
            except Exception as exc:  # noqa: BLE001 - a copy is not the source
                log.debug(
                    "goal publish: %s unreachable for staleness (%s)", profile, exc
                )
                continue
            try:
                await target.execute(
                    f"""
                    UPDATE {GOALS_TABLE}
                    SET stale = TRUE, updated_at = NOW()
                    WHERE published_from_goal_id = $1 AND published_rev < $2
                    """,
                    source_goal_id,
                    revision,
                )
            except Exception as exc:  # noqa: BLE001 - best effort per profile
                log.debug("goal publish: could not stale %s (%s)", profile, exc)
            finally:
                await target.close()

    async def _publish_connection(self, profile: str) -> "asyncpg.Connection":
        from hermes_cli.datastore import connect_for_publish

        return await connect_for_publish(self._registry._store, profile=profile)

    async def publish_entity_goal(
        self,
        principal: "Principal",
        *,
        profiles: Optional[Sequence[str]] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[PublishResult]:
        """Copy the entity goal into every profile. Owner only, audited.

        This is the downward flow: a profile is the instrument for one
        sub-goal, and a sub-goal that cannot see the goal it serves is just a
        task list. The copy is read-only in the receiving profile (the
        registry's update path refuses a row with ``published_from_profile``),
        carries the revision it came from, and re-publishing clears staleness.
        """
        if not principal.is_owner:
            raise PermissionError(
                "Only the owner may publish the entity goal into profiles"
            )
        own = connection is None
        conn = connection or await self._connect()
        try:
            source = await self._active_entity_goal(principal, connection=conn)
            if source is None:
                raise GoalTreeError(
                    "There is no active entity goal to publish; create one first"
                )
            if source.is_published_copy:
                raise GoalTreeError(
                    "This entity goal is itself a published copy; publish from "
                    "the profile that owns it"
                )
            targets = list(profiles) if profiles is not None else _publish_targets()
            results: List[PublishResult] = []
            origin = _origin_profile()
            for profile in targets:
                if profile == origin:
                    continue
                result = await self._publish_one(principal, source, profile, origin)
                results.append(result)
                await self._record_publish(
                    conn,
                    principal,
                    source=source,
                    origin=origin,
                    result=result,
                )
            await self._registry.record_progress(
                principal,
                source.id,
                note=(
                    f"published rev {source.source_rev} to "
                    f"{', '.join(result.profile for result in results) or 'no profiles'}"
                ),
                connection=conn,
            )
            return results
        finally:
            if own:
                await conn.close()

    async def _publish_one(
        self,
        principal: "Principal",
        source: GoalRecord,
        profile: str,
        origin: str,
    ) -> PublishResult:
        conn = await self._publish_connection(profile)
        try:
            # The target profile may never have connected yet: its schema is
            # created on first use, and a publish is allowed to be that first
            # use so a profile is not required to have run before it can be
            # told what the entity is for.
            from hermes_cli.datastore import app_schema

            target_schema = app_schema(self._registry._store.mode, profile=profile)
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{target_schema}"')
            await self._registry.initialize(connection=conn)
            existing = await conn.fetchrow(
                f"""
                SELECT id FROM {GOALS_TABLE}
                WHERE published_from_goal_id = $1
                """,
                source.id,
            )
            if existing is None:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {GOALS_TABLE}
                        (owner_user_id, visibility, title, description,
                         priority, status, tier, primary_metric, source_rev,
                         published_from_profile, published_from_goal_id,
                         published_rev, published_at, stale)
                    VALUES ($1, 'shared', $2, $3, $4, 'active', 'entity', $5,
                            $6, $7, $8, $6, NOW(), FALSE)
                    RETURNING id
                    """,
                    source.owner_user_id,
                    source.title,
                    source.description,
                    source.priority,
                    source.primary_metric,
                    source.source_rev,
                    origin,
                    source.id,
                )
                created = True
            else:
                row = await conn.fetchrow(
                    f"""
                    UPDATE {GOALS_TABLE}
                    SET title = $2, description = $3, priority = $4,
                        primary_metric = $5, published_rev = $6,
                        source_rev = $6, published_at = NOW(), stale = FALSE,
                        status = 'active', updated_at = NOW()
                    WHERE id = $1
                    RETURNING id
                    """,
                    existing["id"],
                    source.title,
                    source.description,
                    source.priority,
                    source.primary_metric,
                    source.source_rev,
                )
                created = False
            return PublishResult(
                profile=profile,
                goal_id=str(row["id"]),
                revision=source.source_rev,
                created=created,
            )
        finally:
            await conn.close()

    async def _record_publish(
        self,
        conn: "asyncpg.Connection",
        principal: "Principal",
        *,
        source: GoalRecord,
        origin: str,
        result: PublishResult,
    ) -> None:
        """Append the authorisation record for one profile's copy.

        Written in the *source* profile, where the decision was taken and where
        the owner asks "what did I publish, and when?". The receiving profile
        has the provenance on the row itself.
        """
        await conn.execute(
            """
            INSERT INTO goal_publish_audit
                (source_goal_id, source_profile, target_profile, target_goal_id,
                 revision, action, actor_user_id, detail)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            source.id,
            origin,
            result.profile,
            result.goal_id,
            result.revision,
            "created" if result.created else "refreshed",
            principal.user_id,
            source.title,
        )

    async def publish_audit(
        self,
        source_goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[dict]:
        """Every publish of one goal, oldest first."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                """
                SELECT target_profile, target_goal_id, revision, action,
                       actor_user_id, detail, at
                FROM goal_publish_audit
                WHERE source_goal_id = $1
                ORDER BY at, target_profile
                """,
                source_goal_id,
            )
            return [dict(row) for row in rows]
        finally:
            if own:
                await conn.close()

    async def stale_published_copies(
        self,
        principal: "Principal",
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GoalRecord]:
        """Published copies in *this* profile that are behind their source."""
        goals = await self._registry.list_goals(principal, connection=connection)
        return [goal for goal in goals if goal.is_published_copy and goal.stale]


def _origin_profile() -> str:
    from hermes_cli.profiles import get_active_profile_name

    return get_active_profile_name()


def _publish_targets() -> List[str]:
    """Every profile on this box except the one publishing.

    Enumerated at publish time only. Nothing here runs per turn: the profiles
    a copy went to are recorded in the copies themselves.
    """
    from hermes_cli.profiles import list_profiles

    origin = _origin_profile()
    return [info.name for info in list_profiles() if info.name and info.name != origin]


__all__ = [
    "DEFAULT_UNMEASURED_AFTER",
    "GoalCycleError",
    "GoalDepthError",
    "GoalTierError",
    "GoalTreeError",
    "GoalTreeStore",
    "MAX_TREE_DEPTH",
    "PROMPT_TIERS",
    "PublishResult",
    "Rollup",
    "STABLE_PROMPT_TIERS",
    "VOLATILE_PROMPT_TIERS",
    "may_enter_prompt",
    "normalized_progress",
    "prompt_slot",
    "tier_rank",
    "validate_parent",
]
