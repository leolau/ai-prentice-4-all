"""The clock for the review loop — FG-29/FG-30/FG-31's weekly minute.

Everything the loop produces for a human already exists: promotion candidates
and demotions (FG-29), sibling-goal conflicts (FG-29 §9), profile suggestions
and idle profiles (FG-30), capacity headroom (FG-31). All of it converges on
``goal_conflicts.weekly_digest()``, and every piece of it was reachable only by
someone typing a command. On a box nobody types on, the loop's *output* is
therefore never produced: `generate_suggestion()` had exactly one caller,
``hermes profile suggest``, so a suggestion could not appear no matter how much
evidence accumulated, and the 30-day interval and one-open cap were guarding a
door nobody opened.

This module is that clock and nothing else. It composes the three existing
entry points in the order the design puts them — generate, then alert, then
render — and reports what each one did:

* **Generate** (monthly, self-gated). ``generate_suggestion()`` enforces its own
  cadence, so the pass may run weekly and still produce at most one suggestion a
  month. The gate stays in the function; the caller stays dumb.
* **Alert** conflicts. Immediate by design (FG-29 §9), and ``alert_owner()``
  dedupes per tension, so a weekly floor never re-asks about a live one.
* **Render** the digest as one notification per ISO week. The dedupe key is the
  week, so a re-run — a manual invocation, a `Persistent=true` catch-up after
  the box was off — collapses onto the pending row instead of stacking.

Each step is independent: one failing must not cancel the others (a digest is
still worth delivering when suggestion generation cannot reach the aux LLM), and
a failure must reach the caller rather than a log nobody reads — the FG-30
retirement lesson, where a swallowed exception let a no-op report success.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

#: ``proactive_ask``: the digest asks for attention, it does not ask for
#: consent. An ``approval`` may be auto-answered by the C6 policy, and a review
#: nobody read must never be recorded as a review somebody did.
DIGEST_KIND = "proactive_ask"


def digest_dedupe_key(now: datetime) -> str:
    """One digest per ISO week, whatever runs the pass and however often."""
    year, week, _day = now.isocalendar()
    return f"entity-review:{year}-W{week:02d}"


@dataclass
class ReviewPassResult:
    """What the pass actually did — per step, including what it could not do."""

    suggestion_name: Optional[str] = None
    conflicts_alerted: int = 0
    digest_title: str = ""
    digest_lines: List[str] = field(default_factory=list)
    digest_notification_id: Optional[str] = None
    #: ``(step, error)`` for every step that failed, in the order they ran.
    errors: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def note_failure(self, step: str, exc: BaseException) -> None:
        log.warning("review pass: %s failed: %s", step, exc)
        self.errors.append((step, f"{type(exc).__name__}: {exc}"))


async def run_review_pass(
    *,
    actor: Optional[str] = None,
    deliver: bool = True,
    now: Optional[datetime] = None,
) -> ReviewPassResult:
    """Run the loop's review moment. ``deliver=False`` renders without writing."""
    from hermes_cli.access import PrincipalStore
    from hermes_cli.goal_conflicts import alert_owner, detect_conflicts, weekly_digest
    from hermes_cli.goal_purpose import default_tree_store
    from hermes_cli.human_comms import NotificationStore
    from hermes_cli.profile_suggestion import generate_suggestion
    from hermes_cli.skill_promotion import SkillPromotionStore

    now = now or datetime.now()
    tree = default_tree_store()
    store = tree.registry._store
    principals = PrincipalStore(store)
    principal = await principals.get(actor) if actor else await principals.get_owner()
    if principal is None:
        raise RuntimeError(
            "unknown --actor" if actor else "no owner is enrolled yet"
        )
    await tree.registry.initialize()
    promotions = SkillPromotionStore(store)
    result = ReviewPassResult()

    try:
        suggestion = await generate_suggestion(tree, promotions, principal, now=now)
        if suggestion is not None:
            result.suggestion_name = suggestion.proposed_name
    except Exception as exc:  # noqa: BLE001 - one step is not the pass
        result.note_failure("suggestion", exc)

    notifications = NotificationStore(store)
    try:
        if deliver:
            await notifications.initialize()
        conflicts = await detect_conflicts(tree, principal, now=now)
        if conflicts and deliver:
            ids = await alert_owner(notifications, principal, conflicts, now=now)
            result.conflicts_alerted = len(ids)
    except Exception as exc:  # noqa: BLE001
        result.note_failure("conflicts", exc)

    try:
        title, lines = await weekly_digest(tree, promotions, principal, now=now)
        result.digest_title = title
        result.digest_lines = list(lines)
        if deliver:
            created = await notifications.create(
                kind=DIGEST_KIND,
                target_user_id=principal.user_id,
                title=title,
                body="\n".join(lines),
                command="hermes promotion digest",
                reversible=True,
                dedupe_key=digest_dedupe_key(now),
                now=now,
            )
            result.digest_notification_id = created.notification.id
    except Exception as exc:  # noqa: BLE001
        result.note_failure("digest", exc)

    return result


__all__ = [
    "DIGEST_KIND",
    "ReviewPassResult",
    "digest_dedupe_key",
    "run_review_pass",
]
