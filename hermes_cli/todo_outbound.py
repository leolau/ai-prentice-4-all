"""The outgoing seam: a finished to-do may *propose* something leaves.

The last clause of requirement 2 in
``docs/plans/2026-08-11-001-todos-staging-layer-plan.md`` — "once work is
completed, it can also trigger out-going actions". The design position, and the
whole reason this module is thirty lines of routing rather than a sender, is:

    **the system may propose; only the user may send.**

FG-10 already has exactly the right primitive for that, so nothing new is
invented here. A proposal is an ``approval`` notification with
``reversible=False``, which is what stops C6 from ever auto-answering it (D6):
standing consent may let the agent do reversible things unattended, and an
email that has left the building is not one of them.

Routing is contract C4, not a new dimension: the reply leaves by the **same
account the message arrived on**, taken off the ``inbound_items`` row the to-do
already points at. Two accounts therefore never cross-post, and the human sees
the answer where they asked.

What is deliberately *not* here: the send. The notification carries a
``command`` for the gateway's existing egress path and the approval decision is
recorded on the to-do's own audit history — the per-channel send
implementations are a follow-up (§9 of the plan). This module's job is to leave
the door the right shape rather than to walk through it.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hermes_cli.access import Principal
    from hermes_cli.human_comms import NotificationStore
    from hermes_cli.todo_store import Todo, TodoStore

log = logging.getLogger(__name__)

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 4000


class OutboundError(ValueError):
    """A proposed action the user could not act on even if they approved it."""


@dataclass(frozen=True)
class ProposedAction:
    """A drafted outgoing action, awaiting the user's approval.

    ``account_id`` is the C4 receiving-inbox identity and ``target`` the
    conversation it goes back to; both default to the arrival behind the to-do,
    which is why a caller can propose a reply without knowing any routing.
    """

    channel: str
    target: str
    body: str
    subject: str = ""
    account_id: Optional[str] = None
    thread_id: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "target": self.target,
            "body": self.body,
            "subject": self.subject,
            "account_id": self.account_id,
            "thread_id": self.thread_id,
        }


@dataclass(frozen=True)
class Proposal:
    """The record left behind by :func:`propose`."""

    todo_id: str
    action: ProposedAction
    notification_id: str
    command: str
    #: Always False for a proposal — an irreversible approval is never
    #: auto-answered, which is the point of raising it this way.
    auto_approved: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.todo_id,
            "action": self.action.as_dict(),
            "notification_id": self.notification_id,
            "command": self.command,
            "auto_approved": self.auto_approved,
        }


def target_from_arrival(item: Mapping[str, Any]) -> dict[str, Optional[str]]:
    """The C4 reply route implied by the arrival a to-do came from.

    The conversation is preferred over the sender: replying to a group message
    in a private chat with its author answers a different question than the one
    that was asked.
    """
    target = str(item.get("conversation") or item.get("sender_id") or "")
    return {
        "channel": str(item.get("surface") or ""),
        "target": target,
        "account_id": (str(item["account_id"]) if item.get("account_id") else None),
    }


def parse_action(
    payload: Mapping[str, Any],
    *,
    arrival: Optional[Mapping[str, Any]] = None,
) -> ProposedAction:
    """Validate a proposed action, filling routing in from ``arrival``.

    A missing channel or target is an error rather than a best guess: an
    approval the user cannot act on, or one that would leave by the wrong
    account, is worse than no proposal at all.
    """
    defaults = target_from_arrival(arrival) if arrival is not None else {}
    channel = str(payload.get("channel") or defaults.get("channel") or "").strip()
    target = str(payload.get("target") or defaults.get("target") or "").strip()
    account_id = payload.get("account_id") or defaults.get("account_id")
    body = str(payload.get("body") or "").strip()
    if not channel:
        raise OutboundError("a proposed action needs a channel")
    if not target:
        raise OutboundError("a proposed action needs somewhere to go")
    if not body:
        raise OutboundError("a proposed action needs a body to send")
    return ProposedAction(
        channel=channel,
        target=target,
        body=body[:MAX_BODY_CHARS],
        subject=str(payload.get("subject") or "").strip()[:MAX_SUBJECT_CHARS],
        account_id=str(account_id) if account_id else None,
        thread_id=(
            str(payload["thread_id"]) if payload.get("thread_id") else None
        ),
    )


def command_for(todo_id: str, action: ProposedAction) -> str:
    """The shell command an approval authorises, as FG-10 records it.

    A CLI string rather than a serialised payload, for the same reason
    :mod:`hermes_cli.changes` uses one: it is what the user is shown before
    they approve, so it has to be legible to a human and runnable by the
    gateway without a second interpreter.
    """
    parts = [
        "hermes",
        "todos",
        "send",
        todo_id,
        "--channel",
        action.channel,
        "--to",
        action.target,
    ]
    if action.account_id:
        parts += ["--account", action.account_id]
    if action.thread_id:
        parts += ["--thread", action.thread_id]
    return shlex.join(parts)


async def propose(
    store: "TodoStore",
    notifications: "NotificationStore",
    principal: "Principal",
    todo: "Todo",
    action: ProposedAction,
    *,
    now: Optional[datetime] = None,
) -> Proposal:
    """Offer ``action`` to the user as an irreversible FG-10 approval."""
    title = action.subject or f"Reply on {action.channel}: {todo.title}"
    result = await notifications.create(
        kind="approval",
        target_user_id=todo.owner_user_id,
        title=title[:MAX_SUBJECT_CHARS],
        body=action.body,
        command=command_for(todo.id, action),
        # D6: irreversible, so standing consent can never answer it for them.
        reversible=False,
        visibility=todo.visibility,
        dedupe_key=f"todo-action:{todo.id}",
        now=now,
    )
    await store.record_outbound(
        principal,
        todo.id,
        event="proposed",
        channel=action.channel,
        actor=f"user:{principal.user_id}",
    )
    return Proposal(
        todo_id=todo.id,
        action=action,
        notification_id=result.notification.id,
        command=result.notification.command,
        auto_approved=result.auto_answered,
    )
