"""Read-only query verbs for the unified inbox (``hermes incomings``).

Every inbound channel — WhatsApp, email, calendar, and the gateway's own
mirrors — converges into the ``inbound_items`` registry that backs the
agent-home ``/inbox``. These verbs put that registry on the terminal so any
agent surface with a ``terminal`` tool can ask "what came in" without a new
model tool: ``list`` for a recent page, ``search`` for full-text, ``show``
for one arrival in full. Footprint-ladder rung 2: CLI + skill, zero schema.

Reads honor the instance's datastore config (``default_registry``), exactly
like the ``/inbox`` BFF, and the same RLS scoping applies — an arrival that
is invisible to the reader and one that is absent are the same answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from hermes_cli.access import Principal, PrincipalStore
from hermes_cli.inbound_registry import (
    MAX_PAGE_SIZE,
    InboundItem,
    InboundRegistry,
    default_registry,
)

DEFAULT_LIST_LIMIT = 20


def _csv(value: Optional[str]) -> list:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _when(value: Optional[str], *, field: str) -> Optional[datetime]:
    """ISO timestamp (naive assumed UTC) or a relative ``30d`` / ``12h`` form."""
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    if text[-1] in "dh" and text[:-1].isdigit():
        amount = int(text[:-1])
        delta = timedelta(days=amount) if text[-1] == "d" else timedelta(hours=amount)
        return datetime.now(timezone.utc) - delta
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field} is not an ISO timestamp (or a '30d' / '12h' form)"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _limit(raw: int) -> int:
    return max(1, min(int(raw), MAX_PAGE_SIZE))


def _title(item: InboundItem, width: int = 44) -> str:
    text = item.subject or (item.body or "").strip().split("\n")[0] or ""
    return " ".join(text.split())[:width]


async def _resolve_principal(
    actor: Optional[str],
) -> Tuple[InboundRegistry, Principal]:
    registry = default_registry()
    principals = PrincipalStore(registry._store)  # noqa: SLF001 - same-package
    principal = await principals.get(actor) if actor else await principals.get_owner()
    if principal is None:
        raise RuntimeError(
            "no principal to read as "
            f"({'unknown --actor' if actor else 'no owner enrolled'})"
        )
    return registry, principal


def _continuation(args: argparse.Namespace, *, query: str, cursor: str) -> str:
    parts = ["hermes incomings search" if query else "hermes incomings list"]
    if query:
        parts.append(json.dumps(query))
    for flag, value in (
        ("--surface", args.surface),
        ("--kind", args.kind),
        ("--sender", args.sender),
        ("--importance", args.importance),
        ("--since", args.since),
        ("--until", args.until),
    ):
        if value:
            parts.append(f'{flag} "{value}"')
    if args.remembered:
        parts.append("--remembered")
    if args.unremembered:
        parts.append("--unremembered")
    parts.append(f"--limit {args.limit}")
    parts.append(f"--cursor {cursor}")
    return " ".join(parts)


async def _page(args: argparse.Namespace, *, query: str) -> int:
    registry, principal = await _resolve_principal(args.actor)
    page = await registry.list(
        principal,
        query=query,
        surfaces=_csv(args.surface),
        kinds=_csv(args.kind),
        senders=_csv(args.sender),
        importance=_csv(args.importance),
        since=_when(args.since, field="--since"),
        until=_when(args.until, field="--until"),
        remembered=True if args.remembered else False if args.unremembered else None,
        limit=_limit(args.limit),
        cursor=args.cursor or None,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "items": [item.as_dict() for item in page.items],
                    "next_cursor": page.next_cursor,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not page.items:
        print("No arrivals match.")
        return 0
    for item in page.items:
        occurred = (
            item.occurred_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            if item.occurred_at
            else "—"
        )
        sender = (item.sender_name or item.sender_id or "")[:18]
        markers = ""
        if item.has_attachments:
            markers += " +att"
        if item.remembered:
            markers += " *"
        print(
            f"{occurred}  {item.surface[:10]:<10}  {sender:<18}  "
            f"{_title(item):<44}  {item.id}{markers}"
        )
    if page.next_cursor:
        print(f"more: {_continuation(args, query=query, cursor=page.next_cursor)}")
    return 0


async def _show(args: argparse.Namespace) -> int:
    registry, principal = await _resolve_principal(args.actor)
    item = await registry.get(principal, args.item_id)
    if item is None:
        # Invisible and absent are the same answer here, as they are over HTTP.
        print(f"No arrival {args.item_id} visible to {principal.user_id}.", file=sys.stderr)
        return 1
    attachments = await registry.attachments(principal, item.id)
    if args.json:
        payload = item.as_dict()
        payload["attachments"] = attachments
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(item.id)
    account = f" (account {item.account_id})" if item.account_id else ""
    print(f"surface:    {item.surface}{account}")
    print(f"kind:       {item.kind}")
    sender = item.sender_name or item.sender_id or "unknown"
    angle = (
        f" <{item.sender_id}>"
        if item.sender_name and item.sender_id and item.sender_id != item.sender_name
        else ""
    )
    print(f"from:       {sender}{angle}")
    if item.conversation_name:
        print(f"conversation: {item.conversation_name}")
    if item.subject:
        print(f"subject:    {item.subject}")
    if item.occurred_at:
        print(f"occurred:   {item.occurred_at.isoformat()}")
    if item.ends_at:
        print(f"ends:       {item.ends_at.isoformat()}")
    print(f"importance: {item.importance or 'normal'}")
    print(f"remembered: {'yes' if item.remembered else 'no'}")
    print()
    print(item.body or "(no body)")
    print()
    if attachments:
        print("attachments:")
        for att in attachments:
            print(
                f"  {att['filename']} ({att['content_type']}, "
                f"{att['byte_size']} bytes) {att['id']}"
            )
    else:
        print("(no attachments)")
    return 0


def _validate_bounds(args: argparse.Namespace) -> Optional[int]:
    for field in ("since", "until"):
        try:
            _when(getattr(args, field), field=f"--{field}")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return None


def _run(coro) -> int:
    try:
        return asyncio.run(coro)
    except (RuntimeError, ValueError) as error:
        print(f"Incomings query failed: {error}", file=sys.stderr)
        return 1


def incomings_list_command(args: argparse.Namespace) -> int:
    """``hermes incomings list`` — a recent-first page of arrivals."""
    bad = _validate_bounds(args)
    if bad:
        return bad
    return _run(_page(args, query=""))


def incomings_search_command(args: argparse.Namespace) -> int:
    """``hermes incomings search <text>`` — full-text over arrivals."""
    bad = _validate_bounds(args)
    if bad:
        return bad
    return _run(_page(args, query=args.text))


def incomings_show_command(args: argparse.Namespace) -> int:
    """``hermes incomings show <id>`` — one arrival in full."""
    return _run(_show(args))


def _add_common_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--surface",
        default="",
        help="Comma-separated surfaces (whatsapp, email, calendar, telegram, …)",
    )
    parser.add_argument(
        "--kind", default="", help="Comma-separated kinds (message, event, …)"
    )
    parser.add_argument(
        "--sender", default="", help="Comma-separated sender names or ids"
    )
    parser.add_argument(
        "--importance", default="", help="Comma-separated importance levels"
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO timestamp, or a '30d' / '12h' relative form",
    )
    parser.add_argument("--until", default=None, help="ISO timestamp upper bound")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--remembered",
        action="store_true",
        help="Only arrivals already kept in memory",
    )
    group.add_argument(
        "--unremembered",
        action="store_true",
        help="Only arrivals not yet kept in memory",
    )


def _add_paging_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIST_LIMIT,
        help=f"Page size (default {DEFAULT_LIST_LIMIT}, max {MAX_PAGE_SIZE})",
    )
    parser.add_argument(
        "--cursor",
        default=None,
        help="Continuation token printed by a previous page",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help="Principal to read as (default: the enrolled owner)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")


def register_incomings_query_verbs(sub: argparse._SubParsersAction) -> None:
    """Add ``list`` / ``search`` / ``show`` to the ``incomings`` subparsers."""
    list_parser = sub.add_parser(
        "list",
        help="List recent arrivals, newest first",
        description=(
            "A page of the unified inbox (WhatsApp, email, calendar, "
            "telegram). Full ids are printed so `show` can chain."
        ),
    )
    _add_common_filter_args(list_parser)
    _add_paging_args(list_parser)
    list_parser.set_defaults(func=incomings_list_command)

    search_parser = sub.add_parser(
        "search",
        help="Full-text search over arrivals",
        description=(
            "Search sender, conversation, subject and body across every "
            "surface in the registry."
        ),
    )
    search_parser.add_argument(
        "text", help="Words to search for (quoted for multiple words)"
    )
    _add_common_filter_args(search_parser)
    _add_paging_args(search_parser)
    search_parser.set_defaults(func=incomings_search_command)

    show_parser = sub.add_parser(
        "show",
        help="Print one arrival in full, with attachments",
        description="The complete body and attachment list of one inbound item.",
    )
    show_parser.add_argument("item_id", help="Inbound item id (as printed by list/search)")
    show_parser.add_argument(
        "--actor",
        default=None,
        help="Principal to read as (default: the enrolled owner)",
    )
    show_parser.add_argument(
        "--json", action="store_true", help="Machine-readable output"
    )
    show_parser.set_defaults(func=incomings_show_command)
