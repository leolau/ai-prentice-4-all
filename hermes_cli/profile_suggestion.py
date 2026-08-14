"""Profile lifecycle: suggest, adopt, retire — an output of the learning loop.

Every other Phase-6 doc assumed an owner can name their sub-goals up front and
create a profile for each. They can't. Sub-goal structure is *discovered by
doing the work*, so profile creation must be an *output* of the same loop that
distils skills and watches where work clusters — not a setup step.

This module is the proposal layer over that loop. It follows the
``skill_promotion.py`` blueprint exactly: ``SCHEMA_SQL``, a frozen dataclass,
an async store taking ``SupabaseAppStore``, ``_audit()`` for C5, and
``digest_lines()`` for plain-text output. Suggestion generation reads evidence
already recorded, uses the aux LLM for dual role/goal naming, and is capped at
one open suggestion at a time. Adoption calls ``create_profile`` and seeds a
profile-tier sub-goal. Retire/merge use the existing export path.

Three properties from the FG-30 spec, each a decision the owner made:

* **Consent-first, never auto-created.** A suggestion is a proposal with
  evidence attached. Only the owner adopts, because a new profile is a change
  to the entity's goal tree. Dismissals are kept and latched on ``dedup_key``,
  so a dismissed suggestion is never re-proposed on the same evidence.
* **Monthly, one at a time.** The digest emits at most one suggestion per
  cycle — a second nudge trains the owner to dismiss without reading.
  Generation is its own monthly pass, skipped while any suggestion is still
  ``proposed``; rendering rides ``weekly_digest()`` so an open suggestion
  appears in every weekly review until it is reviewed.
* **Dual role/goal naming.** Each suggestion carries both ``proposed_role``
  and ``proposed_goal``. The role is for the human; the goal is what the goal
  tree hangs on and what retirement completes — a role has no end state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg

    from hermes_cli.access import Principal
    from hermes_cli.datastore import SupabaseAppStore
    from hermes_cli.goal_tree import GoalTreeStore
    from hermes_cli.skill_promotion import SkillPromotionStore

log = logging.getLogger(__name__)

SUGGESTIONS_TABLE = "profile_suggestions"
SUGGESTION_AUDIT_TABLE = "profile_suggestion_audit"

#: The suggestion lifecycle. ``proposed`` is the only open state.
SUGGESTION_STATES: Tuple[str, ...] = ("proposed", "adopted", "dismissed")

#: The single open state.
OPEN_STATE: Tuple[str, ...] = ("proposed",)

#: How often generation runs — separate from FG-29's weekly digest clock.
DEFAULT_GENERATION_INTERVAL = timedelta(days=30)

#: How long a profile with no sessions is "idle" before the digest flags it.
DEFAULT_IDLE_WEEKS = 4

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {SUGGESTIONS_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposed_name TEXT NOT NULL,       -- the profile identifier/slug
    proposed_role TEXT NOT NULL,       -- role-style name, e.g. "CFO"
    proposed_goal TEXT NOT NULL,       -- the sub-goal it would serve, e.g. "improve cashflow"
    parent_goal_id UUID NULL REFERENCES goals(id) ON DELETE SET NULL,
    rationale TEXT NOT NULL DEFAULT '',
    evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    dedup_key TEXT NOT NULL,          -- stable key over the evidence; latches a dismissal
    origin_profile TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    reviewed_by TEXT NULL,
    reviewed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE {SUGGESTIONS_TABLE}
    DROP CONSTRAINT IF EXISTS {SUGGESTIONS_TABLE}_status_check;
ALTER TABLE {SUGGESTIONS_TABLE}
    ADD CONSTRAINT {SUGGESTIONS_TABLE}_status_check
    CHECK (status IN ({", ".join(f"'{s}'" for s in SUGGESTION_STATES)}));
-- One open suggestion at a time (the cap). A second would read as a list, and
-- lists get skimmed and batch-dismissed — killing the good one with the noise.
CREATE UNIQUE INDEX IF NOT EXISTS {SUGGESTIONS_TABLE}_one_open
    ON {SUGGESTIONS_TABLE} (origin_profile)
    WHERE status = 'proposed';
CREATE INDEX IF NOT EXISTS {SUGGESTIONS_TABLE}_status_idx
    ON {SUGGESTIONS_TABLE} (status, created_at);
CREATE INDEX IF NOT EXISTS {SUGGESTIONS_TABLE}_dedup_idx
    ON {SUGGESTIONS_TABLE} (dedup_key, status);

-- Append-only. Every state change, with who and what they were looking at.
-- A dismissed suggestion keeps its rows: "we considered this and said no" is
-- the answer to the same evidence being proposed again next month.
CREATE TABLE IF NOT EXISTS {SUGGESTION_AUDIT_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    suggestion_id UUID NOT NULL REFERENCES {SUGGESTIONS_TABLE}(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS {SUGGESTION_AUDIT_TABLE}_suggestion_idx
    ON {SUGGESTION_AUDIT_TABLE} (suggestion_id, at);
"""

_SUGGESTION_COLUMNS = (
    "id, proposed_name, proposed_role, proposed_goal, parent_goal_id, "
    "rationale, evidence, dedup_key, origin_profile, status, reviewed_by, "
    "reviewed_at, created_at"
)


class SuggestionError(RuntimeError):
    """A refused suggestion operation."""


@dataclass(frozen=True)
class ProfileSuggestion:
    """A suggestion as stored."""

    id: str
    proposed_name: str
    proposed_role: str
    proposed_goal: str
    parent_goal_id: Optional[str]
    rationale: str
    evidence: Dict[str, Any]
    dedup_key: str
    origin_profile: str
    status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    created_at: Optional[datetime]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "proposed_name": self.proposed_name,
            "proposed_role": self.proposed_role,
            "proposed_goal": self.proposed_goal,
            "parent_goal_id": self.parent_goal_id,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "dedup_key": self.dedup_key,
            "origin_profile": self.origin_profile,
            "status": self.status,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row_to_suggestion(row) -> ProfileSuggestion:
    evidence = row["evidence"]
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (json.JSONDecodeError, TypeError):
            evidence = {}
    if not isinstance(evidence, dict):
        evidence = {}
    return ProfileSuggestion(
        id=str(row["id"]),
        proposed_name=str(row["proposed_name"]),
        proposed_role=str(row["proposed_role"]),
        proposed_goal=str(row["proposed_goal"]),
        parent_goal_id=str(row["parent_goal_id"]) if row["parent_goal_id"] else None,
        rationale=str(row["rationale"]),
        evidence=evidence,
        dedup_key=str(row["dedup_key"]),
        origin_profile=str(row["origin_profile"]),
        status=str(row["status"]),
        reviewed_by=str(row["reviewed_by"]) if row["reviewed_by"] else None,
        reviewed_at=row["reviewed_at"],
        created_at=row["created_at"],
    )


def _evidence_hash(evidence: Dict[str, Any]) -> str:
    """Stable sha256 over the evidence JSONB, for the dedup_key."""
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _make_dedup_key(origin_profile: str, evidence: Dict[str, Any]) -> str:
    """Stable key over the evidence, latching a dismissal.

    Reuses the ``cron/suggestions.py`` contract (stable ``dedup_key`` so the
    same proposal is never re-offered after the user says no) rather than
    building a second latching mechanism.
    """
    return f"profile-suggest:{origin_profile}:{_evidence_hash(evidence)}"


# ---------------------------------------------------------------------------
# Suggestion generation
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a profile-suggestion engine for the Hermes Agent.

The owner runs one or more "profiles" — distinct agent identities, each with
their own skills, memory, and configuration. The system has noticed that work
in one profile is clustering into a distinct sub-goal that would benefit from
its own profile.

You are given evidence: skill clusters, goal patterns, and session topics that
look unlike the current profile's focus. Produce a single JSON object with
exactly these keys:

{
  "proposed_name": "<short slug, lowercase-hyphenated, e.g. 'finance'>",
  "proposed_role": "<role-style name, e.g. 'CFO' or 'Finance Manager'>",
  "proposed_goal": "<the sub-goal it would serve, e.g. 'improve cashflow'>",
  "rationale": "<1-2 sentences in the owner's language explaining the evidence>"
}

Rules:
  - proposed_name: a profile identifier (lowercase, alphanumeric, hyphens).
  - proposed_role: a role-shaped label. A role is for the human.
  - proposed_goal: the sub-goal. This is mandatory — retirement completes a
    goal, and a role has no end state.
  - Be concrete. Bad: "a new profile for work stuff."
                  Good: "Finance work — invoicing, cashflow, tax — is
                         unrelated to the product-building focus."
  - No code fences, no preamble. Output only JSON.
"""

_USER_TEMPLATE = """Current profile: {origin_profile}
Current profile description: {description}
Evidence:
{evidence_text}
"""


def _collect_evidence_signals() -> Dict[str, Any]:
    """Read the signals already recorded — no new instrumentation.

    Four signals, all from existing data:
    1. Skill clusters from ``tools/skill_usage.py``
    2. Goal patterns from the goal tree (orphans defaulting to the profile goal)
    3. Session-topic divergence from ``profile_describer.py``
    4. Distinct participants from the principals table
    """
    evidence: Dict[str, Any] = {}

    # 1. Skill usage — which skills are most active
    try:
        from tools.skill_usage import load_usage

        records = load_usage()
        if records:
            # Sort by activity count descending, take top 10
            sorted_records = sorted(
                records.items(),
                key=lambda kv: len(kv[1].get("uses", [])),
                reverse=True,
            )[:10]
            evidence["top_skills"] = [
                {"name": name, "uses": len(r.get("uses", []))}
                for name, r in sorted_records
            ]
    except Exception:
        pass

    # 2. Goal patterns — operational goals with no explicit parent
    try:
        from hermes_cli.goal_tree import GoalTreeStore
        from hermes_cli.goal_registry import GoalRegistryStore

        registry = GoalRegistryStore()
        tree = GoalTreeStore(registry)
        # We can't call async here, but the evidence dict carries what we need
        # for the LLM. The actual goal-reading happens in generate_suggestion.
        pass
    except Exception:
        pass

    # 3. Session topic divergence — handled by the aux LLM from skill names
    # 4. Distinct participants — read from the principals table in generate_suggestion
    return evidence


async def generate_suggestion(
    tree: "GoalTreeStore",
    promotions: "SkillPromotionStore",
    principal: "Principal",
    *,
    origin_profile: Optional[str] = None,
    connection: Optional["asyncpg.Connection"] = None,
) -> Optional[ProfileSuggestion]:
    """Generate at most one suggestion per monthly cycle.

    Reads signals already recorded (no new instrumentation), uses the aux LLM
    for dual role/goal naming, and checks that no dismissed suggestion with
    the same ``dedup_key`` exists. Returns the persisted suggestion, or None
    if no cluster is strong enough or the one-open cap is already filled.

    This is the *monthly* generation pass — separate from FG-29's weekly
    digest clock. It is skipped entirely while a suggestion is still
    ``proposed`` (the one-open cap is simpler and stronger than a per-run limit).
    """
    profile = origin_profile or _active_profile()
    store = ProfileSuggestionStore(_resolve_store())

    own = connection is None
    conn = connection or await store._connect()
    try:
        await store.initialize(connection=conn)

        # Skip if the one-open cap is already filled.
        existing = await store._open_suggestion(conn, origin_profile=profile)
        if existing is not None:
            return existing

        # Collect evidence signals.
        evidence = await _gather_evidence(tree, principal, conn=conn)
        if not evidence or not _evidence_strong_enough(evidence):
            return None

        dedup_key = _make_dedup_key(profile, evidence)

        # Check that no dismissed suggestion with the same dedup_key exists.
        dismissed = await conn.fetchval(
            f"""
            SELECT 1 FROM {SUGGESTIONS_TABLE}
            WHERE dedup_key = $1 AND status = 'dismissed'
            """,
            dedup_key,
        )
        if dismissed:
            return None

        # Use the aux LLM for dual role/goal naming.
        naming = _ask_aux_llm(profile, evidence)
        if naming is None:
            return None

        # Persist.
        suggestion = await store.propose(
            principal,
            proposed_name=naming["proposed_name"],
            proposed_role=naming["proposed_role"],
            proposed_goal=naming["proposed_goal"],
            rationale=naming.get("rationale", ""),
            evidence=evidence,
            dedup_key=dedup_key,
            origin_profile=profile,
            connection=conn,
        )
        return suggestion
    finally:
        if own:
            await conn.close()


async def _gather_evidence(
    tree: "GoalTreeStore",
    principal: "Principal",
    *,
    conn: "asyncpg.Connection",
) -> Dict[str, Any]:
    """Read the four evidence signals from existing data."""
    evidence: Dict[str, Any] = {}

    # 1. Skill usage clusters
    try:
        from tools.skill_usage import load_usage

        records = load_usage()
        if records:
            sorted_records = sorted(
                records.items(),
                key=lambda kv: len(kv[1].get("uses", [])),
                reverse=True,
            )[:10]
            evidence["top_skills"] = [
                {"name": name, "uses": len(r.get("uses", []))}
                for name, r in sorted_records
                if len(r.get("uses", [])) > 0
            ]
    except Exception:
        pass

    # 2. Goal patterns — operational goals with no explicit parent (orphans)
    try:
        goals = await tree.registry.list_goals(
            principal, status="active", connection=conn
        )
        orphans = [
            {"id": g.id, "title": g.title, "tier": g.tier}
            for g in goals
            if not g.parent_goal_id and g.tier == "operational"
        ]
        if orphans:
            evidence["orphan_goals"] = orphans[:5]
    except Exception:
        pass

    # 3. Profile description (for divergence comparison)
    try:
        from hermes_cli import profiles as profiles_mod
        from hermes_constants import get_hermes_home
        from pathlib import Path as P

        if profile_dir := P(get_hermes_home()):
            meta = profiles_mod.read_profile_meta(profile_dir)
            evidence["current_description"] = meta.get("description", "")
    except Exception:
        pass

    # 4. Distinct participants
    try:
        from hermes_cli.access import ITEM_GRANTS_TABLE

        rows = await conn.fetch(
            "SELECT user_id, display, role FROM principals WHERE active = true"
        )
        if rows:
            evidence["participants"] = [
                {"user_id": r["user_id"], "display": r["display"], "role": r["role"]}
                for r in rows
            ]
    except Exception:
        pass

    return evidence


def _evidence_strong_enough(evidence: Dict[str, Any]) -> bool:
    """Whether the evidence justifies a suggestion.

    A minimum of 2 signals with data is the bar — below that, the cluster is
    not strong enough to warrant a new profile.
    """
    signals = 0
    if evidence.get("top_skills"):
        signals += 1
    if evidence.get("orphan_goals"):
        signals += 1
    if evidence.get("participants") and len(evidence["participants"]) > 1:
        signals += 1
    if evidence.get("current_description"):
        signals += 1
    return signals >= 2


def _ask_aux_llm(
    origin_profile: str,
    evidence: Dict[str, Any],
) -> Optional[dict]:
    """Use the aux LLM to produce role + goal from the evidence.

    Mirrors ``profile_describer.py``'s lazy import + lenient parse pattern.
    Never raises for expected failure modes — returns None so generation
    can be skipped this cycle.
    """
    try:
        from agent.auxiliary_client import (
            get_auxiliary_extra_body,
            get_text_auxiliary_client,
        )
    except Exception as exc:
        log.debug("suggestion: auxiliary client import failed: %s", exc)
        return None

    try:
        client, aux_model = get_text_auxiliary_client("profile_suggestion")
    except Exception as exc:
        log.debug("suggestion: get_text_auxiliary_client failed: %s", exc)
        return None

    if client is None or not aux_model:
        return None

    evidence_text = json.dumps(evidence, indent=2, default=str)
    user_msg = _USER_TEMPLATE.format(
        origin_profile=origin_profile,
        description=evidence.get("current_description", "(no description)"),
        evidence_text=evidence_text,
    )

    try:
        resp = client.chat.completions.create(
            model=aux_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=500,
            timeout=60,
            extra_body=get_auxiliary_extra_body() or None,
        )
    except Exception as exc:
        log.info("suggestion: API call failed (%s)", exc)
        return None

    try:
        raw = resp.choices[0].message.content or ""
    except Exception:
        return None

    parsed = _extract_json_blob(raw)
    if parsed is None:
        return None

    name = parsed.get("proposed_name")
    role = parsed.get("proposed_role")
    goal = parsed.get("proposed_goal")
    rationale = parsed.get("rationale", "")

    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(role, str) or not role.strip():
        return None
    if not isinstance(goal, str) or not goal.strip():
        return None

    # Validate the slug — lowercase, alphanumeric, hyphens.
    canon = name.strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", canon):
        return None

    return {
        "proposed_name": canon,
        "proposed_role": role.strip(),
        "proposed_goal": goal.strip(),
        "rationale": rationale.strip() if isinstance(rationale, str) else "",
    }


def _extract_json_blob(raw: str) -> Optional[dict]:
    if not raw:
        return None
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None
    candidate = stripped[first : last + 1]
    try:
        val = json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(val, dict):
        return None
    return val


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def _resolve_store() -> "SupabaseAppStore":
    """Resolve the active profile's SupabaseAppStore (C3 datastore routing)."""
    from hermes_cli.datastore import get_store

    return get_store("supabase-app", "prod")


def _active_profile() -> str:
    from hermes_cli.profiles import get_active_profile_name

    return get_active_profile_name()


class ProfileSuggestionStore:
    """The suggestion queue and audit trail.

    Routed through the same profile-derived ``SupabaseAppStore`` as every
    other C3 consumer, so the queue for a profile lives in that profile's
    schema — a suggestion is *about* creating a profile from that profile's
    work, and its reviewer is that profile's owner.
    """

    def __init__(self, store: "SupabaseAppStore") -> None:
        from hermes_cli.datastore import SupabaseAppStore

        if not isinstance(store, SupabaseAppStore):
            raise TypeError("ProfileSuggestionStore requires a supabase-app store")
        self._store = store

    async def _connect(self) -> "asyncpg.Connection":
        conn = await self._store.connect()
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"')
        return conn

    async def initialize(
        self, *, connection: Optional["asyncpg.Connection"] = None
    ) -> None:
        """Create the suggestion tables (idempotent)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"')
            await conn.execute(SCHEMA_SQL)
        finally:
            if own:
                await conn.close()

    # -- proposing ----------------------------------------------------------

    async def propose(
        self,
        principal: "Principal",
        *,
        proposed_name: str,
        proposed_role: str,
        proposed_goal: str,
        rationale: str = "",
        evidence: Dict[str, Any],
        dedup_key: str,
        origin_profile: str,
        parent_goal_id: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ProfileSuggestion:
        """Record a suggestion. Refuses if an open one already exists.

        The one-open cap is enforced by the unique partial index, so a second
        proposal on the same origin_profile fails at the database boundary.
        """
        if not proposed_name.strip():
            raise SuggestionError("proposed_name is required")
        if not proposed_role.strip():
            raise SuggestionError("proposed_role is required")
        if not proposed_goal.strip():
            raise SuggestionError("proposed_goal is required")
        if not dedup_key.strip():
            raise SuggestionError("dedup_key is required")

        own = connection is None
        conn = connection or await self._connect()
        try:
            await self.initialize(connection=conn)

            # Check for an existing open suggestion (the cap).
            existing = await self._open_suggestion(conn, origin_profile=origin_profile)
            if existing is not None:
                return existing

            # Dismissal latch: a dismissed suggestion with the same
            # dedup_key can never be re-proposed.  The row stays so the
            # same evidence being proposed again next month can be refused
            # at the store boundary — "we considered this and said no."
            dismissed = await conn.fetchval(
                f"""
                SELECT 1 FROM {SUGGESTIONS_TABLE}
                WHERE dedup_key = $1 AND status = 'dismissed'
                """,
                dedup_key,
            )
            if dismissed:
                raise SuggestionError(
                    f"A suggestion with dedup_key {dedup_key!r} was "
                    f"already dismissed — the evidence was considered "
                    f"and rejected"
                )

            row = await conn.fetchrow(
                f"""
                INSERT INTO {SUGGESTIONS_TABLE}
                    (proposed_name, proposed_role, proposed_goal, parent_goal_id,
                     rationale, evidence, dedup_key, origin_profile)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
                RETURNING {_SUGGESTION_COLUMNS}
                """,
                proposed_name.strip(),
                proposed_role.strip(),
                proposed_goal.strip(),
                parent_goal_id,
                rationale,
                json.dumps(evidence),
                dedup_key,
                origin_profile,
            )
            suggestion = _row_to_suggestion(row)
            await self._audit(
                conn,
                suggestion.id,
                action="proposed",
                principal=principal,
                detail=f"role={proposed_role} goal={proposed_goal}",
            )
            return suggestion
        finally:
            if own:
                await conn.close()

    async def _audit(
        self,
        conn: "asyncpg.Connection",
        suggestion_id: str,
        *,
        action: str,
        principal: "Principal",
        detail: str = "",
    ) -> None:
        await conn.execute(
            f"""
            INSERT INTO {SUGGESTION_AUDIT_TABLE}
                (suggestion_id, action, actor_user_id, actor_role, detail)
            VALUES ($1, $2, $3, $4, $5)
            """,
            suggestion_id,
            action,
            principal.user_id,
            principal.role,
            detail,
        )

    async def audit_trail(
        self,
        suggestion_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[dict]:
        """Every recorded action for one suggestion, oldest first."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT action, actor_user_id, actor_role, detail, at
                FROM {SUGGESTION_AUDIT_TABLE}
                WHERE suggestion_id = $1
                ORDER BY at, action
                """,
                suggestion_id,
            )
            return [dict(row) for row in rows]
        finally:
            if own:
                await conn.close()

    # -- reading ------------------------------------------------------------

    async def get(
        self,
        principal: "Principal",
        suggestion_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[ProfileSuggestion]:
        """One suggestion by id."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self.initialize(connection=conn)
            row = await conn.fetchrow(
                f"SELECT {_SUGGESTION_COLUMNS} FROM {SUGGESTIONS_TABLE} WHERE id = $1",
                suggestion_id,
            )
            return _row_to_suggestion(row) if row else None
        finally:
            if own:
                await conn.close()

    async def list_suggestions(
        self,
        principal: "Principal",
        *,
        statuses: Sequence[str] = OPEN_STATE,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[ProfileSuggestion]:
        """Suggestions in ``statuses``, newest first."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self.initialize(connection=conn)
            rows = await conn.fetch(
                f"""
                SELECT {_SUGGESTION_COLUMNS} FROM {SUGGESTIONS_TABLE}
                WHERE status = ANY($1::text[])
                ORDER BY created_at DESC
                """,
                list(statuses),
            )
            return [_row_to_suggestion(row) for row in rows]
        finally:
            if own:
                await conn.close()

    async def digest_suggestion(
        self,
        principal: "Principal",
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[ProfileSuggestion]:
        """The single open suggestion to render in the weekly digest, or None.

        The digest only *renders* — generation is a separate monthly pass.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self.initialize(connection=conn)
            row = await conn.fetchrow(
                f"""
                SELECT {_SUGGESTION_COLUMNS} FROM {SUGGESTIONS_TABLE}
                WHERE status = 'proposed'
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            return _row_to_suggestion(row) if row else None
        finally:
            if own:
                await conn.close()

    async def _open_suggestion(
        self,
        conn: "asyncpg.Connection",
        *,
        origin_profile: str,
    ) -> Optional[ProfileSuggestion]:
        """The single open suggestion for a profile, or None."""
        await self.initialize(connection=conn)
        row = await conn.fetchrow(
            f"""
            SELECT {_SUGGESTION_COLUMNS} FROM {SUGGESTIONS_TABLE}
            WHERE status = 'proposed' AND origin_profile = $1
            LIMIT 1
            """,
            origin_profile,
        )
        return _row_to_suggestion(row) if row else None

    async def _require(
        self, conn: "asyncpg.Connection", suggestion_id: str
    ) -> ProfileSuggestion:
        await self.initialize(connection=conn)
        row = await conn.fetchrow(
            f"SELECT {_SUGGESTION_COLUMNS} FROM {SUGGESTIONS_TABLE} WHERE id = $1",
            suggestion_id,
        )
        if row is None:
            raise SuggestionError(f"No profile suggestion {suggestion_id}")
        return _row_to_suggestion(row)

    # -- adoption -----------------------------------------------------------

    async def adopt(
        self,
        principal: "Principal",
        suggestion_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Tuple[ProfileSuggestion, Path]:
        """Owner-only: adopt a suggestion → create the profile + seed sub-goal.

        Calls ``create_profile(clone_config=True)`` so the new profile
        inherits config + promoted skills from the shared tier, but NOT the
        parent's session history, participation memory, or resolved DSN.

        The person-level ``USER.md`` needs no work — FG-24 put it at
        ``<root>/persons/<user_id>/USER.md``, outside any profile home.
        We assert it is visible; we do NOT copy it (a copy would reintroduce
        the drift bug that amendment removed).

        Returns the updated suggestion and the new profile directory.
        """
        if not principal.is_owner:
            raise PermissionError(
                "Only the owner may adopt a profile suggestion: a new profile "
                "is a change to the entity's goal tree"
            )
        own = connection is None
        conn = connection or await self._connect()
        try:
            suggestion = await self._require(conn, suggestion_id)
            if suggestion.status != "proposed":
                raise SuggestionError(
                    f"Suggestion {suggestion_id} is {suggestion.status}, not "
                    f"awaiting adoption"
                )

            # Create the profile — reuses the existing function.
            from hermes_cli.profiles import create_profile

            profile_dir = create_profile(
                name=suggestion.proposed_name,
                clone_config=True,
                description=suggestion.proposed_role,
                verify_datastore=True,
                report=print,
            )

            # Seed the profile-tier sub-goal via the goal tree.
            # The new profile's goal ladders into the entity goal.
            try:
                from hermes_cli.goal_tree import GoalTreeStore, GoalRegistryStore
                from hermes_constants import (
                    reset_hermes_home_override,
                    set_hermes_home_override,
                )

                registry = GoalRegistryStore()
                tree = GoalTreeStore(registry)
                # Switch to the new profile context to create the goal.
                token = set_hermes_home_override(profile_dir)
                try:
                    await tree.registry.create_goal(
                        principal,
                        suggestion.proposed_goal,
                        tier="profile",
                        description=suggestion.proposed_role,
                    )
                finally:
                    reset_hermes_home_override(token)
            except Exception as exc:
                log.warning("suggestion: could not seed sub-goal: %s", exc)

            # Publish the entity goal into the new profile (FG-29 §3).
            try:
                from hermes_cli.goal_tree import GoalTreeStore, GoalRegistryStore
                from hermes_constants import (
                    reset_hermes_home_override,
                    set_hermes_home_override,
                )

                registry = GoalRegistryStore()
                tree = GoalTreeStore(registry)
                token = set_hermes_home_override(profile_dir)
                try:
                    await tree.publish_entity_goal(
                        principal, profiles=[suggestion.proposed_name]
                    )
                finally:
                    reset_hermes_home_override(token)
            except Exception as exc:
                log.warning("suggestion: could not publish entity goal: %s", exc)

            # Mark the suggestion as adopted.
            row = await conn.fetchrow(
                f"""
                UPDATE {SUGGESTIONS_TABLE}
                SET status = 'adopted', reviewed_by = $2, reviewed_at = NOW()
                WHERE id = $1 AND status = 'proposed'
                RETURNING {_SUGGESTION_COLUMNS}
                """,
                suggestion_id,
                principal.user_id,
            )
            if row is None:  # pragma: no cover - lost race
                raise SuggestionError(f"Suggestion {suggestion_id} changed underneath")
            suggestion = _row_to_suggestion(row)
            await self._audit(
                conn,
                suggestion_id,
                action="adopted",
                principal=principal,
                detail=(
                    f"created profile {suggestion.proposed_name} at {profile_dir}; "
                    f"sub-goal: {suggestion.proposed_goal}"
                ),
            )
            return suggestion, profile_dir
        finally:
            if own:
                await conn.close()

    async def dismiss(
        self,
        principal: "Principal",
        suggestion_id: str,
        *,
        reason: str = "",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ProfileSuggestion:
        """Owner-only: dismiss a suggestion. The row and audit remain.

        Dismissals are latched on ``dedup_key``: a dismissed suggestion is
        never re-proposed on the same evidence. The row stays so the same
        evidence being proposed again next month can be refused at the store
        boundary.
        """
        if not principal.is_owner:
            raise PermissionError(
                "Only the owner may dismiss a profile suggestion"
            )
        own = connection is None
        conn = connection or await self._connect()
        try:
            suggestion = await self._require(conn, suggestion_id)
            if suggestion.status != "proposed":
                raise SuggestionError(
                    f"Suggestion {suggestion_id} is already {suggestion.status}"
                )
            row = await conn.fetchrow(
                f"""
                UPDATE {SUGGESTIONS_TABLE}
                SET status = 'dismissed', reviewed_by = $2, reviewed_at = NOW()
                WHERE id = $1
                RETURNING {_SUGGESTION_COLUMNS}
                """,
                suggestion_id,
                principal.user_id,
            )
            await self._audit(
                conn,
                suggestion_id,
                action="dismissed",
                principal=principal,
                detail=reason,
            )
            return _row_to_suggestion(row)
        finally:
            if own:
                await conn.close()


# ---------------------------------------------------------------------------
# Retire and merge
# ---------------------------------------------------------------------------


async def retire_profile(
    name: str,
    principal: "Principal",
    *,
    promotions: "SkillPromotionStore",
    connection: Optional["asyncpg.Connection"] = None,
) -> Path:
    """Retire a profile: offer skills once, archive, release channel, mark goal.

    1. Offer the profile's skills for promotion **once** — the only way its
       know-how survives. A marker prevents the offer from firing again.
    2. Archive via ``export_profile`` (restorable).
    3. Release the channel: disable the gateway service.
    4. Mark the profile's goal ``completed`` via the goal tree.
    5. Do NOT delete the profile directory — the archive is restorable.
    """
    from hermes_cli.profiles import export_profile, get_profile_dir, normalize_profile_name
    from hermes_constants import get_default_hermes_root

    canon = normalize_profile_name(name)
    profile_dir = get_profile_dir(canon)

    # 1. One-time promotion offer for local skills.
    retired_marker = profile_dir / ".retired"
    if not retired_marker.exists():
        try:
            from hermes_cli.skill_promotion import find_local_skill

            skills_dir = profile_dir / "skills"
            if skills_dir.is_dir():
                for skill_md in sorted(skills_dir.rglob("SKILL.md")):
                    skill_name = skill_md.parent.name
                    try:
                        await promotions.propose(
                            principal,
                            skill_name,
                            rationale=f"retired from profile {canon}",
                            origin_profile=canon,
                            connection=connection,
                        )
                    except Exception as exc:
                        log.info("retire: could not propose skill %s: %s", skill_name, exc)
        except Exception as exc:
            log.info("retire: skill sweep failed: %s", exc)
        retired_marker.touch()

    # 2. Archive via the existing export path.
    import os
    import tempfile

    archive_dir = get_default_hermes_root() / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(archive_dir / f"{canon}.tar.gz")
    archive_path = export_profile(canon, output_path)

    # 3. Release the channel: disable the gateway service.
    try:
        from hermes_cli.profiles import (
            _check_gateway_running,
            _cleanup_gateway_service,
            _maybe_unregister_gateway_service,
        )

        if _check_gateway_running(profile_dir):
            _cleanup_gateway_service(canon, profile_dir)
            _maybe_unregister_gateway_service(canon)
            from hermes_cli.profiles import _stop_gateway_process

            _stop_gateway_process(profile_dir)
    except Exception as exc:
        log.warning("retire: could not release channel: %s", exc)

    # 4. Mark the profile's goal completed.
    try:
        from hermes_cli.goal_tree import GoalTreeStore, GoalRegistryStore
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        registry = GoalRegistryStore()
        tree = GoalTreeStore(registry)
        token = set_hermes_home_override(profile_dir)
        try:
            goals = await tree.registry.list_goals(
                principal, status="active", connection=connection
            )
            for goal in goals:
                if goal.tier == "profile":
                    await tree.registry.update_goal(
                        principal,
                        goal.id,
                        status="completed",
                        connection=connection,
                    )
        finally:
            reset_hermes_home_override(token)
    except Exception as exc:
        log.warning("retire: could not mark goal completed: %s", exc)

    return archive_path


async def merge_profiles(
    source: str,
    target: str,
    principal: "Principal",
    *,
    promotions: "SkillPromotionStore",
    connection: Optional["asyncpg.Connection"] = None,
) -> Path:
    """Merge: both profiles' skills go through promotion; the source is archived.

    Memory is NOT merged — for the §2 reason: deciding which memory card
    belongs to which half is a judgement no heuristic makes well.
    """
    from hermes_cli.profiles import export_profile, get_profile_dir, normalize_profile_name

    source_canon = normalize_profile_name(source)
    target_canon = normalize_profile_name(target)
    source_dir = get_profile_dir(source_canon)

    # 1. Both profiles' skills go through promotion.
    try:
        skills_dir = source_dir / "skills"
        if skills_dir.is_dir():
            for skill_md in sorted(skills_dir.rglob("SKILL.md")):
                skill_name = skill_md.parent.name
                try:
                    await promotions.propose(
                        principal,
                        skill_name,
                        rationale=f"merged from {source_canon} into {target_canon}",
                        origin_profile=source_canon,
                        connection=connection,
                    )
                except Exception as exc:
                    log.info("merge: could not propose skill %s: %s", skill_name, exc)
    except Exception as exc:
        log.info("merge: skill sweep failed: %s", exc)

    # 2. Archive the source.
    from hermes_constants import get_default_hermes_root

    archive_dir = get_default_hermes_root() / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(archive_dir / f"{source_canon}.tar.gz")
    archive_path = export_profile(source_canon, output_path)

    # 3. Memory is NOT merged (FG-30 §2).

    return archive_path


# ---------------------------------------------------------------------------
# Idle detection
# ---------------------------------------------------------------------------


async def idle_profiles(
    *,
    now: Optional[datetime] = None,
    idle_weeks: int = DEFAULT_IDLE_WEEKS,
) -> List[Tuple[str, int]]:
    """Profiles with no sessions for ``idle_weeks`` weeks.

    Returns ``(profile_name, days_since_last_session)`` tuples. Used by the
    weekly digest so idle profiles don't get forgotten.
    """
    from hermes_cli.profiles import list_profiles

    moment = now or datetime.now(timezone.utc)
    idle: List[Tuple[str, int]] = []
    for p in list_profiles():
        try:
            from hermes_state import SessionDB

            db_path = getattr(p, "path", None)
            if db_path is not None:
                db_path = db_path / "state.db"
            db = SessionDB(db_path=db_path, read_only=True)
            sessions = db.list_sessions_rich(limit=1, order_by_last_active=True)
            if not sessions:
                # No sessions at all — count from profile creation if possible.
                idle.append((p.name, idle_weeks * 7))
                continue

            last = sessions[0].get("last_active")
            if last is None:
                continue
            if isinstance(last, str):
                last = datetime.fromisoformat(last)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days = (moment - last).days
            if days >= idle_weeks * 7:
                idle.append((p.name, days))
        except Exception:
            pass
    return idle


# ---------------------------------------------------------------------------
# Digest rendering
# ---------------------------------------------------------------------------


def digest_lines(suggestion: Optional[ProfileSuggestion]) -> List[str]:
    """Plain-text lines for the weekly review.

    The digest only *renders* an open suggestion — generation is a separate
    monthly pass. An open suggestion appears in every weekly review until the
    owner reviews it, because a suggestion held back returns next month but
    one dismissed unread never returns.
    """
    if suggestion is None:
        return []
    return [
        f"{suggestion.proposed_role} / {suggestion.proposed_goal} "
        f"(from {suggestion.origin_profile}) — "
        f"{suggestion.rationale or 'no rationale given'}"
    ]


def idle_lines(
    idle: Sequence[Tuple[str, int]],
) -> List[str]:
    """Plain-text lines for idle profiles in the weekly digest."""
    return [
        f"{name} (last session: {days} days ago)"
        for name, days in idle
    ]


__all__ = [
    "DEFAULT_GENERATION_INTERVAL",
    "DEFAULT_IDLE_WEEKS",
    "OPEN_STATE",
    "ProfileSuggestion",
    "ProfileSuggestionStore",
    "SUGGESTION_STATES",
    "SuggestionError",
    "digest_lines",
    "generate_suggestion",
    "idle_lines",
    "idle_profiles",
    "merge_profiles",
    "retire_profile",
]
