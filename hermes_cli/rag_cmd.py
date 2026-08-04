"""``hermes memory rag`` — ingest documents into layer 4, and search them.

The agent gets ``rag_search`` as a tool; a human gets this, because ingestion is
a long, resumable, off-peak job rather than something to run inside a
conversation. Everything here acts as a named principal whose role is read from
the ``principals`` table (never asserted on the command line), so a document
ingested by one person is that person's until they share it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from hermes_cli.access import PrincipalStore
from hermes_cli.config import load_config
from hermes_cli.datastore import get_store
from hermes_cli.rag_drive import (
    DriveError,
    GoogleDriveReader,
    IngestSummary,
    accounts_to_ingest,
    credential_path,
    ingest_drive,
)

#: Where the Google Workspace MCP server keeps its per-account credentials,
#: relative to the Hermes home. Ingestion reuses them rather than asking the
#: user to consent a second time for the same scopes.
DEFAULT_CREDENTIALS_SUBPATH = "google-workspace/credentials"


def _credentials_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "credentials_dir", None):
        return Path(args.credentials_dir).expanduser()
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / DEFAULT_CREDENTIALS_SUBPATH


def _stores(args: argparse.Namespace):
    from plugins.memory.supabase_pgvector.rag import RagStore
    from plugins.memory.supabase_pgvector.store import PgvectorMemoryStore

    config = load_config()
    mode = getattr(args, "mode", None)
    app_store = (
        get_store("supabase-app", mode) if mode else get_store("supabase-app")
    )
    memory = PgvectorMemoryStore(app_store, config=config)
    return RagStore(memory), PrincipalStore(app_store)


async def _principal(principals: PrincipalStore, user_id: str):
    principal = await principals.get(user_id)
    if principal is None:
        raise SystemExit(
            f"No principal '{user_id}'. Run 'hermes member list' to see who "
            "is enrolled."
        )
    return principal


def _report(summary: IngestSummary) -> None:
    print(f"\n  account        {summary.account or '(default)'}")
    print(f"  files seen     {summary.seen}")
    print(f"  ingested       {summary.ingested} ({summary.chunks} chunks)")
    print(f"  unchanged      {summary.unchanged}")
    print(f"  skipped        {summary.skipped}")
    if summary.failures:
        print(f"  failures       {len(summary.failures)}")
        for failure in summary.failures[:10]:
            print(f"    - {failure}")
    print()


def cmd_rag_ingest_drive(args: argparse.Namespace) -> None:
    rag, principals = _stores(args)
    directory = _credentials_dir(args)
    accounts = accounts_to_ingest(directory, args.account or [])
    if not accounts:
        raise SystemExit(
            f"No Google credentials in {directory}. Complete consent first "
            "(see the google-workspace skill)."
        )

    async def run():
        principal = await _principal(principals, args.acting_as)
        await rag.initialize()
        summaries = []
        for account in accounts:
            reader = GoogleDriveReader.from_file(
                credential_path(directory, account)
            )
            summaries.append(
                await ingest_drive(
                    rag,
                    principal,
                    reader,
                    limit=int(args.limit),
                    account=account,
                    progress=_progress if args.verbose else None,
                )
            )
        return summaries

    for summary in asyncio.run(run()):
        _report(summary)


def _progress(file, outcome: str) -> None:
    print(f"  {outcome:34} {file.name[:60]}")


def cmd_rag_search(args: argparse.Namespace) -> None:
    rag, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)
        return await rag.search(
            principal,
            args.query,
            top_k=int(args.top_k),
            source_kind=args.source_kind,
        )

    hits = asyncio.run(run())
    if not hits:
        print("\n  no passages matched\n")
        return
    print()
    for hit in hits:
        owner = "" if hit.owner_user_id == args.acting_as else f"  [{hit.owner_user_id}]"
        print(f"  {hit.citation}{owner}")
        print(f"    {hit.text.strip()[:400]}")
        arms = ",".join(
            name
            for name, rank in (
                ("vector", hit.vector_rank),
                ("lexical", hit.lexical_rank),
            )
            if rank
        )
        print(f"    ({arms}; {hit.source_kind}:{hit.source_ref})\n")


def cmd_rag_documents(args: argparse.Namespace) -> None:
    rag, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)
        return await rag.documents(principal, source_kind=args.source_kind)

    documents = asyncio.run(run())
    if not documents:
        print("\n  no documents ingested\n")
        return
    print(f"\n  {len(documents)} document(s)\n")
    for document in documents:
        print(
            f"  {document.chunk_count:4} chunks  {document.visibility:22} "
            f"{document.title[:50]}"
        )
        print(f"                  {document.source_kind}:{document.source_ref}")
    print()


def cmd_rag_forget(args: argparse.Namespace) -> None:
    rag, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)
        return await rag.forget(
            principal,
            source_kind=args.source_kind or "gdrive",
            source_ref=args.source_ref,
        )

    removed = asyncio.run(run())
    if removed:
        print(f"\n  ✓ removed {removed} chunk(s)\n")
        return
    print(
        f"\n  ✗ {args.acting_as} has no ingested document "
        f"{args.source_ref}; nothing was removed\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


def cmd_rag_share(args: argparse.Namespace) -> None:
    rag, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)
        if args.revoke:
            return "revoke", await rag.unshare(
                principal, args.document_id, args.user_id
            )
        return "share", await rag.share(principal, args.document_id, args.user_id)

    action, ok = asyncio.run(run())
    if ok:
        verb = "revoked" if action == "revoke" else "shared"
        print(f"\n  ✓ {args.document_id} {verb} ({args.user_id})\n")
        return
    print(
        f"\n  ✗ {args.acting_as} does not own document {args.document_id}, "
        "or there was no active grant to withdraw\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


def cmd_memory_rag(args: argparse.Namespace) -> None:
    """Dispatch ``hermes memory rag <ingest-drive|search|documents|...>``."""
    action = getattr(args, "rag_command", None)
    handlers = {
        "ingest-drive": cmd_rag_ingest_drive,
        "search": cmd_rag_search,
        "documents": cmd_rag_documents,
        "forget": cmd_rag_forget,
        "share": cmd_rag_share,
    }
    handler = handlers.get(action or "")
    if handler is None:
        print(
            "Usage: hermes memory rag "
            "<ingest-drive|search|documents|forget|share>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        handler(args)
    except SystemExit:
        raise
    except DriveError as exc:
        print(f"\n  ✗ {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"\n  ✗ {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc


def register_rag_subparser(memory_sub: argparse._SubParsersAction) -> None:
    """Attach ``rag`` under an existing ``hermes memory`` subparser."""
    parser = memory_sub.add_parser(
        "rag",
        help="Ingest and search documents in the layer-4 vector store",
        description=(
            "Documents are ingested per person and stay private to that person "
            "unless shared. Retrieval is hybrid (meaning + exact text) and "
            "every passage carries the document and section it came from."
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
    actions = parser.add_subparsers(dest="rag_command")

    ingest = actions.add_parser(
        "ingest-drive",
        help="Ingest Google Drive documents, newest first",
        description=(
            "Walks all of Drive for each connected account (including 'Shared "
            "with me'), newest modification first, and ingests up to --limit "
            "documents. Unchanged documents cost nothing, so re-running is "
            "cheap and a interrupted run resumes."
        ),
    )
    ingest.add_argument(
        "--account",
        action="append",
        help="Google account to ingest (repeatable; default: all connected)",
    )
    ingest.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max documents to ingest or confirm per account (default 50)",
    )
    ingest.add_argument(
        "--credentials-dir",
        default=None,
        help="Override the Google credential directory",
    )
    ingest.add_argument(
        "--verbose",
        action="store_true",
        help="Print each file and what happened to it",
    )

    search = actions.add_parser("search", help="Search ingested documents")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--source-kind", default=None)

    documents = actions.add_parser(
        "documents", help="List the documents you can read"
    )
    documents.add_argument("--source-kind", default=None)

    forget = actions.add_parser(
        "forget", help="Delete one ingested document and its chunks"
    )
    forget.add_argument("source_ref", help="Drive file id (or other source ref)")
    forget.add_argument("--source-kind", default="gdrive")

    share = actions.add_parser(
        "share", help="Grant (or revoke) one user's access to one document"
    )
    share.add_argument("document_id", help="UUID from 'hermes rag documents'")
    share.add_argument("user_id")
    share.add_argument("--revoke", action="store_true")
