"""Telling the user a to-do is waiting, once, and at a civilised hour.

The third step of ``docs/plans/2026-08-11-001-todos-staging-layer-plan.md``.
Everything here is one decision expressed twice: **a to-do that reaches
``open`` is worth one interruption, and exactly one.**

That is why this is a thin layer over two pieces that already exist rather
than a new delivery mechanism:

* FG-10's :class:`~hermes_cli.human_comms.NotificationStore` — which already
  holds quiet-hours, rate limiting and cross-surface answer de-duplication
  under contract C6, and which until now had no production writer at all;
* ``tasks.notified_at`` — the store's single-winner stamp, so a retried batch
  or a second worker cannot announce the same to-do twice.

The order matters: the notification is written **first**, then the stamp. The
notification's own ``dedupe_key`` collapses a re-raise onto the pending row, so
a crash between the two costs a duplicate-free retry; the reverse order would
lose the announcement entirely.

A to-do is a *proactive ask*, never an approval: it is the agent saying "this
is waiting for you", not asking permission to act. Approvals enter the same
table from the outgoing seam (step 6), where irreversibility is the point.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hermes_cli.access import Principal
    from hermes_cli.human_comms import NotificationStore
    from hermes_cli.todo_store import Todo, TodoStore

log = logging.getLogger(__name__)

#: Priorities that justify a push the moment they land. Everything else waits
#: for the digest: an interruption the user did not need is the fastest way to
#: teach them to ignore the ones they did.
PUSH_PRIORITIES = ("critical", "high")

MAX_BODY_CHARS = 500


@dataclass(frozen=True)
class Announcement:
    """What happened when a to-do was announced."""

    todo_id: str
    title: str
    #: The one-screen summary the notification carries, ready for a push.
    body: str
    priority: str
    #: True only for the caller that won the ``notified_at`` stamp.
    notified: bool
    #: False when C6 quiet-hours held the ask back; the row still exists and
    #: the user sees it on the page and in the digest.
    deliver_now: bool
    notification_id: Optional[str] = None

    @property
    def should_push(self) -> bool:
        """Whether a channel should push this to the user right now."""
        return (
            self.notified
            and self.deliver_now
            and self.priority in PUSH_PRIORITIES
        )


def format_body(todo: "Todo") -> str:
    """The one-screen version of a to-do, for a push or a list row.

    Deliberately short and written for someone who is not looking at the
    original message: what it is, when it is due, and where it came from.
    """
    parts: List[str] = []
    if todo.description:
        parts.append(todo.description.strip())
    if todo.due_at:
        parts.append(f"Due {todo.due_at.strftime('%a %d %b')}.")
    origin = todo.source_note or (todo.source_kind or "")
    if origin:
        parts.append(f"From {origin}.")
    return " ".join(parts)[:MAX_BODY_CHARS]


async def announce(
    store: "TodoStore",
    notifications: "NotificationStore",
    principal: "Principal",
    todo: "Todo",
    *,
    now: Optional[datetime] = None,
) -> Announcement:
    """Raise one proactive ask for ``todo``. Idempotent per to-do."""
    body = format_body(todo)
    result = await notifications.create(
        kind="proactive_ask",
        target_user_id=todo.owner_user_id,
        title=todo.title,
        body=body,
        # The page is where the user acts on it; nothing here runs a command.
        command="",
        reversible=True,
        visibility=todo.visibility,
        dedupe_key=f"todo:{todo.id}",
        now=now,
    )
    won = await store.mark_notified(principal, todo.id)
    return Announcement(
        todo_id=todo.id,
        title=todo.title,
        body=body,
        priority=todo.priority,
        notified=won,
        deliver_now=result.deliver_now,
        notification_id=result.notification.id,
    )


async def announce_pending(
    store: "TodoStore",
    notifications: "NotificationStore",
    principal: "Principal",
    *,
    limit: int = 20,
    now: Optional[datetime] = None,
) -> List[Announcement]:
    """Announce every open to-do the user has not been told about yet.

    The sweep exists because a to-do can reach ``open`` by routes that cannot
    notify inline — the user promoting a staged one, a snooze lapsing, a
    triage run that raced a database outage. Announcing at the point of
    promotion stays the fast path; this is the floor under it.
    """
    pending = await store.pending_notification(
        principal, limit=limit, now=now or datetime.now(timezone.utc)
    )
    announced: List[Announcement] = []
    for todo in pending:
        try:
            announced.append(
                await announce(store, notifications, principal, todo, now=now)
            )
        except Exception as exc:  # noqa: BLE001 - one bad row is not the batch
            log.warning("todo notifier: could not announce %s (%s)", todo.id, exc)
    return announced


def digest_lines(todos: List["Todo"], *, limit: int = 5) -> List[str]:
    """Plain-text lines for the hourly digest's to-do section.

    The roll-up is what makes a conservative push bar affordable: the
    lower-priority to-dos that deliberately did not interrupt still reach the
    user within the hour instead of waiting for them to open the page.
    """
    lines: List[str] = []
    for todo in todos[:limit]:
        due = f" (due {todo.due_at.strftime('%d %b')})" if todo.due_at else ""
        lines.append(f"[{todo.priority}] {todo.title}{due}")
    remaining = len(todos) - limit
    if remaining > 0:
        lines.append(f"...and {remaining} more")
    return lines


async def open_todos(
    store: "TodoStore",
    principal: "Principal",
    *,
    limit: int = 20,
) -> List["Todo"]:
    """The to-dos worth putting in a digest: open, unsnoozed, newest first."""
    items, _ = await store.list(principal, stages=["open"], limit=limit)
    return items


def default_stores(config: Optional[dict[str, Any]] = None):
    """The (todo store, notification store) pair against the prod schema."""
    from hermes_cli.config import load_config
    from hermes_cli.datastore import get_store
    from hermes_cli.human_comms import NotificationStore
    from hermes_cli.todo_store import TodoStore

    resolved = config if config is not None else (load_config() or {})
    app_store = get_store("supabase-app", "prod", config=resolved)
    return TodoStore(app_store), NotificationStore(app_store, config=resolved)
