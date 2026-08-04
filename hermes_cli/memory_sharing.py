"""``hermes memory sharing`` — who read whose memory, and one-off memory shares.

Layer-4 memory is owned per person (``owner_user_id``). Two mechanisms let it
cross a person boundary, and this command is how a human inspects and uses them
outside a conversation:

* **Downward role reads** (``memory.sharing.role_reads``) — a principal reads the
  private memory of anyone it ranks strictly above on ``owner > admin > member >
  viewer``. Every such read is written to the audit ledger, and ``audit`` shows
  the ledger to *both* sides: the reader sees what they read, and the person read
  sees who read them. That second half is the point; an unobservable elevated
  read is surveillance rather than an access right.
* **Per-memory grants** (FG-19 ``item_grants``) — the sideways case the hierarchy
  deliberately does not cover: one person shares one memory with a peer.

``--as`` names the principal to act as; its role is loaded from the
``principals`` table, never asserted on the command line, so this command cannot
be used to claim a role the database does not agree with.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from hermes_cli.access import PrincipalStore
from hermes_cli.config import load_config
from hermes_cli.datastore import get_store


def _stores(args: argparse.Namespace):
    from plugins.memory.supabase_pgvector.store import PgvectorMemoryStore

    config = load_config()
    mode = getattr(args, "mode", None)
    app_store = (
        get_store("supabase-app", mode) if mode else get_store("supabase-app")
    )
    return PgvectorMemoryStore(app_store, config=config), PrincipalStore(
        app_store
    )


async def _principal(principals: PrincipalStore, user_id: str):
    principal = await principals.get(user_id)
    if principal is None:
        raise SystemExit(
            f"No principal '{user_id}'. Run 'hermes member list' to see who "
            "is enrolled."
        )
    return principal


def cmd_sharing_audit(args: argparse.Namespace) -> None:
    memory, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)
        return principal, await memory.read_audit(
            principal, limit=int(args.limit)
        )

    principal, entries = asyncio.run(run())
    print(f"\n  acting as          {principal.user_id} ({principal.role})")
    print(f"  role reads         {'on' if memory.role_reads else 'off'}")
    if not entries:
        print("  cross-user reads   none recorded\n")
        return
    print(f"  cross-user reads   {len(entries)}\n")
    for entry in entries:
        when = entry.created_at.isoformat(timespec="seconds") if entry.created_at else "?"
        direction = (
            "you read" if entry.reader_user_id == principal.user_id else "read you"
        )
        who = (
            entry.subject_user_id
            if entry.reader_user_id == principal.user_id
            else entry.reader_user_id
        )
        print(
            f"  {when}  {direction:8} {who:20} "
            f"{len(entry.memory_ids)} memory(s)"
        )
        if entry.query:
            print(f"      query: {entry.query}")
    print()


def cmd_sharing_share(args: argparse.Namespace) -> None:
    memory, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)
        if args.revoke:
            return "revoke", await memory.unshare(
                principal, args.memory_id, args.user_id
            )
        return "share", await memory.share(
            principal, args.memory_id, args.user_id
        )

    action, ok = asyncio.run(run())
    if action == "share":
        if ok:
            print(f"\n  ✓ {args.memory_id} shared with {args.user_id}\n")
        else:
            # Only the row's owner may share it: an elevated reader who could
            # re-share would turn a scoped read into redistribution.
            print(
                f"\n  ✗ {args.acting_as} does not own memory "
                f"{args.memory_id}; nothing was shared\n",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return
    if ok:
        print(f"\n  ✓ grant to {args.user_id} revoked\n")
        return
    print(
        f"\n  ✗ no active grant of {args.memory_id} to {args.user_id} "
        f"from {args.acting_as}\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


def cmd_memory_sharing(args: argparse.Namespace) -> None:
    """Dispatch ``hermes memory sharing <audit|share>``."""
    action = getattr(args, "sharing_command", None) or "audit"
    try:
        if action == "audit":
            cmd_sharing_audit(args)
        elif action == "share":
            cmd_sharing_share(args)
        else:
            print(f"Unknown sharing action: {action}", file=sys.stderr)
            raise SystemExit(2)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  ✗ {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc


def register_sharing_subparser(memory_sub: argparse._SubParsersAction) -> None:
    """Attach ``sharing`` under an existing ``hermes memory`` subparser."""
    parser = memory_sub.add_parser(
        "sharing",
        help="Audit cross-user memory reads, or share one memory",
        description=(
            "Layer-4 memory is per person. Reads cross a person boundary only "
            "by role (downward only, always audited) or by an explicit "
            "per-memory grant."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["dev", "prod"],
        default=None,
        help="Datastore mode to act on (default: the configured mode)",
    )
    parser.add_argument(
        "--as",
        dest="acting_as",
        required=True,
        help="Principal user_id to act as (its role is read from the database)",
    )
    actions = parser.add_subparsers(dest="sharing_command")
    audit = actions.add_parser(
        "audit",
        help="Show cross-user reads you made, and reads made of your memory",
    )
    audit.add_argument("--limit", type=int, default=50)
    share = actions.add_parser(
        "share", help="Grant (or revoke) one user's read access to one memory"
    )
    share.add_argument("memory_id", help="UUID of the memory row you own")
    share.add_argument("user_id", help="Principal to share it with")
    share.add_argument(
        "--revoke",
        action="store_true",
        help="Revoke an existing grant instead of creating one",
    )
