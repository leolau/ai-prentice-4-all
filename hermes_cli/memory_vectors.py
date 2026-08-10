"""``hermes memory vectors`` — inspect and migrate the live memory vector space.

The pgvector memory tier stores one embedding per row, and vectors are only
comparable **within one model**: cosine distance between two models' vectors is
a well-formed number with no meaning. So switching ``memory.embedding`` in
``config.yaml`` is not a config change, it is a data migration — and one whose
failure mode is silent, because a store full of mismatched vectors returns
plausible rows in a meaningless order and looks perfectly healthy.

``status`` reports what is actually in the column. ``reembed`` rewrites every
row with the configured embedder, in one transaction, and rebuilds the index.

Runs on the box as the operator against the mode-resolved schema (contract C3),
using the same store the agent uses — so what it migrates is what the agent
reads, rather than a second opinion about where the rows live.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from hermes_cli.config import load_config
from hermes_cli.datastore import get_store


def _store(mode: str | None):
    from plugins.memory.supabase_pgvector.store import PgvectorMemoryStore

    config = load_config()
    app_store = get_store("supabase-app", mode) if mode else get_store("supabase-app")
    return PgvectorMemoryStore(app_store, config=config)


def cmd_vectors_status(args: argparse.Namespace) -> None:
    store = _store(getattr(args, "mode", None))
    space = asyncio.run(store.describe_space())

    print(f"\n  schema             {store.mode}")
    print(f"  configured model   {store.model_id}  ({store.dim} dims)")
    column = space.column_dim
    print(f"  column             vector({column})" if column else "  column             (table not created yet)")
    if not space.rows_by_model:
        print("  rows               0\n")
        return
    print("  rows by model")
    for model in space.models:
        print(f"    {model:32} {space.rows_by_model[model]:>7}")

    stale = space.rows_outside(store.model_id)
    if stale:
        print(
            f"\n  ⚠️  {stale} row(s) were embedded by a different model. They are "
            "EXCLUDED from\n      recall — ranking them against the current "
            "model's vectors would be\n      meaningless. Run 'hermes memory "
            "vectors reembed' to bring them over.\n"
        )
    else:
        print("\n  ✓ every row is in the configured model's space\n")


def cmd_vectors_reembed(args: argparse.Namespace) -> None:
    store = _store(getattr(args, "mode", None))
    space = asyncio.run(store.describe_space())
    total = sum(space.rows_by_model.values())

    print(f"\n  schema             {store.mode}")
    print(f"  target model       {store.model_id}  ({store.dim} dims)")
    print(f"  rows to re-embed   {total}")
    if space.column_dim and space.column_dim != store.dim:
        print(
            f"  column change      vector({space.column_dim}) → "
            f"vector({store.dim})  (column replaced, HNSW index rebuilt)"
        )

    if not getattr(args, "yes", False):
        try:
            answer = input("\n  Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print("  Aborted — nothing was changed.\n")
            return

    def progress(done: int, count: int) -> None:
        print(f"\r  embedding {done}/{count}", end="", flush=True)

    written = asyncio.run(
        store.reembed(
            batch_size=int(getattr(args, "batch_size", 16) or 16),
            progress=progress,
        )
    )
    print(f"\r  embedded {written}/{total}          ")
    print(f"  ✓ {written} row(s) now in {store.model_id}\n")


def cmd_memory_vectors(args: argparse.Namespace) -> None:
    """Dispatch ``hermes memory vectors <status|reembed>``."""
    action = getattr(args, "vectors_command", None) or "status"
    try:
        if action == "status":
            cmd_vectors_status(args)
        elif action == "reembed":
            cmd_vectors_reembed(args)
        else:
            print(f"Unknown vectors action: {action}", file=sys.stderr)
            raise SystemExit(2)
    except Exception as exc:
        # An operator running this on a box without the datastore configured
        # should get the reason, not a traceback ending in asyncpg internals.
        print(f"\n  ✗ {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc


def register_vectors_subparser(memory_sub: argparse._SubParsersAction) -> None:
    """Attach ``vectors`` under an existing ``hermes memory`` subparser."""
    parser = memory_sub.add_parser(
        "vectors",
        help="Inspect or migrate the live memory embedding space",
        description=(
            "The live pgvector memory tier stores one embedding per row. "
            "Vectors are only comparable within one model, so changing "
            "memory.embedding requires re-embedding every row."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["dev", "prod"],
        default=None,
        help="Datastore mode to act on (default: the configured mode)",
    )
    actions = parser.add_subparsers(dest="vectors_command")
    actions.add_parser("status", help="Show column width and rows per model")
    reembed = actions.add_parser(
        "reembed",
        help="Re-embed every row with the configured embedder",
    )
    reembed.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompt"
    )
    reembed.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="Texts per embedding request (default 16)",
    )
