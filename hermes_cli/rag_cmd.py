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
from hermes_cli.rag_files import (
    FileIngestSummary,
    collect_files,
    extract_text_from_bytes,
    ingest_files,
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


def _report_files(summary: FileIngestSummary) -> None:
    print(f"\n  files seen     {summary.seen}")
    print(f"  ingested       {summary.ingested} ({summary.chunks} chunks)")
    print(f"  unchanged      {summary.unchanged}")
    print(f"  skipped        {summary.skipped}")
    if summary.failures:
        print(f"  failures       {len(summary.failures)}")
        for failure in summary.failures[:10]:
            print(f"    - {failure}")
    print()


def cmd_rag_ingest_files(args: argparse.Namespace) -> None:
    rag, principals = _stores(args)
    files = collect_files(args.paths, recursive=not args.no_recursive)
    if not files:
        raise SystemExit(
            "No files matched. Name files directly, or a directory holding "
            "text documents (.md, .txt, .rst, .csv)."
        )

    async def run():
        principal = await _principal(principals, args.acting_as)
        await rag.initialize()
        return await ingest_files(
            rag,
            principal,
            files,
            source_kind=args.source_kind,
            progress=_progress if args.verbose else None,
        )

    _report_files(asyncio.run(run()))


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


def cmd_rag_remember_file(args: argparse.Namespace) -> None:
    """Register a file from the inbound registry into the RAG corpus.

    Downloads the bytes from Supabase Storage, extracts text (PDF/DOCX
    included via lazy-deps), ingests as ``source_kind='file'`` with
    ``source_ref=<asset_id>``, and stamps ``document_id`` on the
    ``file_assets`` row so the link from ``/memory`` resolves.
    """
    rag, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)
        await rag.initialize()

        from hermes_cli.file_registry import default_registry
        from hermes_cli.filestore import SupabaseStorage

        storage = SupabaseStorage.from_env()
        registry = default_registry(getattr(args, "mode", None))

        asset = await registry.get(principal, args.asset_id)
        if asset is None:
            raise SystemExit(
                f"No file asset {args.asset_id} visible to {args.acting_as}."
            )
        if asset.remembered and not args.force:
            print(
                f"\n  already remembered (document {asset.document_id}); "
                "use --force to re-ingest.\n"
            )
            return None

        data = await storage.download(asset.storage_path)
        text, skip_reason = extract_text_from_bytes(
            asset.filename, data, asset.content_type
        )
        if skip_reason:
            raise SystemExit(
                f"Cannot extract text from {asset.filename}: {skip_reason}"
            )

        result = await rag.ingest(
            principal,
            source_kind="file",
            source_ref=str(asset.id),
            title=asset.filename,
            text=text,
        )
        if result.document_id is None and result.skipped:
            # The content hash matched — the document is already current.
            # Look up the existing document_id so we can stamp it.
            existing = await rag.ingested_state(principal, "file")
            doc_id = existing.get(str(asset.id))
            if doc_id is None:
                print(
                    f"\n  = {asset.filename} unchanged (already ingested), "
                    "but no document_id found to stamp.\n"
                )
                return None
            result_doc_id = doc_id
        else:
            result_doc_id = result.document_id

        await registry.mark_remembered(
            principal,
            str(asset.id),
            document_id=str(result_doc_id),
            remembered_by=args.remembered_by,
        )
        return result_doc_id, result.chunks, result.skipped

    outcome = asyncio.run(run())
    if outcome is None:
        return
    doc_id, chunks, skipped = outcome
    status = "unchanged" if skipped else "ingested"
    print(
        f"\n  \u2713 {status}: document {doc_id} "
        f"({chunks} chunks) — remembered by {args.remembered_by}\n"
    )


def cmd_rag_backfill_files(args: argparse.Namespace) -> None:
    """Register pre-existing bucket objects that predate the registry.

    Walks the ``agent-home-media`` bucket for objects under the user's
    prefix, and inserts a ``file_assets`` row for each one not already
    recorded. Idempotent on ``storage_path`` — re-running never invents
    a second arrival.
    """
    rag, principals = _stores(args)

    async def run():
        principal = await _principal(principals, args.acting_as)

        from hermes_cli.file_registry import (
            FILE_ASSETS_TABLE,
            MAX_REGISTER_BYTES,
            default_registry,
        )
        from hermes_cli.filestore import SupabaseStorage

        storage = SupabaseStorage.from_env()
        registry = default_registry(getattr(args, "mode", None))
        await registry.initialize()

        prefix = args.prefix or f"{principal.user_id}/"
        objects = await storage.list_objects(prefix=prefix)
        if not objects:
            print(f"\n  no objects found under {prefix}\n")
            return 0, 0, []

        conn = await registry._connect()
        try:
            seen = 0
            written = 0
            skipped: list[str] = []
            for obj in objects:
                seen += 1
                name = str(obj.get("name") or "")
                if not name:
                    continue
                # Idempotent on storage_path — not on hash (see handoff §4).
                exists = await conn.fetchval(
                    f"SELECT 1 FROM {FILE_ASSETS_TABLE} "
                    f"WHERE storage_path = $1 LIMIT 1",
                    name,
                )
                if exists:
                    continue

                meta = obj.get("metadata") or {}
                byte_size = int(meta.get("size") or 0)
                created = obj.get("created_at")

                if byte_size and byte_size > MAX_REGISTER_BYTES:
                    skipped.append(f"{name} ({byte_size} bytes over limit)")
                    continue

                if args.dry_run:
                    print(f"  would register  {name[:70]}")
                    written += 1
                    continue

                # Download to compute the hash; the list API doesn't give one.
                try:
                    data = await storage.download(name)
                except Exception as exc:
                    skipped.append(f"{name}: download failed ({exc})")
                    continue
                import hashlib

                digest = hashlib.sha256(data).hexdigest()
                filename = name.rsplit("/", 1)[-1] if "/" in name else name

                await conn.execute(
                    f"""INSERT INTO {FILE_ASSETS_TABLE} (
                            owner_user_id, visibility, surface, account_id,
                            conversation, sender_id, sender_name, message_id,
                            received_at, filename, content_type, byte_size,
                            sha256, storage_bucket, storage_path)
                        VALUES (
                            $1, $2, 'agent_home', $3, NULL, $4, $5, NULL,
                            COALESCE($6, NOW()), $7, $8, $9, $10, $11, $12)""",
                    principal.user_id,
                    principal.private_visibility,
                    principal.user_id,
                    principal.user_id,
                    principal.display or principal.user_id,
                    created,
                    filename,
                    str(obj.get("mimetype") or "application/octet-stream"),
                    byte_size or len(data),
                    digest,
                    storage.bucket,
                    name,
                )
                written += 1
                print(f"  registered     {name[:70]}")
        finally:
            await conn.close()
        return seen, written, skipped

    seen, written, skipped = asyncio.run(run())
    print(f"\n  objects seen   {seen}")
    print(f"  registered      {written}")
    if skipped:
        print(f"  skipped         {len(skipped)}")
        for reason in skipped[:10]:
            print(f"    - {reason}")
    print()


def cmd_memory_rag(args: argparse.Namespace) -> None:
    """Dispatch ``hermes memory rag <ingest-drive|search|documents|...>``."""
    action = getattr(args, "rag_command", None)
    handlers = {
        "ingest-drive": cmd_rag_ingest_drive,
        "ingest-files": cmd_rag_ingest_files,
        "search": cmd_rag_search,
        "documents": cmd_rag_documents,
        "forget": cmd_rag_forget,
        "share": cmd_rag_share,
        "remember-file": cmd_rag_remember_file,
        "backfill-files": cmd_rag_backfill_files,
    }
    handler = handlers.get(action or "")
    if handler is None:
        print(
            "Usage: hermes memory rag "
            "<ingest-drive|ingest-files|search|documents|forget|share"
            "|remember-file|backfill-files>",
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

    ingest_local = actions.add_parser(
        "ingest-files",
        help="Ingest local text files or directories of them",
        description=(
            "Ingests text documents from disk — including anything uploaded to "
            "this machine through the dashboard's Files page. The file's "
            "absolute path is its identity, so re-running updates a document "
            "in place, and an unchanged file costs nothing."
        ),
    )
    ingest_local.add_argument(
        "paths",
        nargs="+",
        help="Files, or directories to walk for .md/.txt/.rst/.csv documents",
    )
    ingest_local.add_argument(
        "--no-recursive",
        action="store_true",
        help="Do not descend into subdirectories",
    )
    ingest_local.add_argument(
        "--source-kind",
        default="local",
        help="Corpus label these documents belong to (default: local)",
    )
    ingest_local.add_argument(
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

    remember = actions.add_parser(
        "remember-file",
        help="Ingest a registered file into the RAG corpus",
        description=(
            "Downloads a file from the inbound registry, extracts text "
            "(PDF/DOCX included), ingests it as a RAG document with "
            "source_kind='file', and stamps the file_assets row so "
            "the /memory link resolves to the file."
        ),
    )
    remember.add_argument(
        "asset_id",
        help="file_assets UUID (from the /files page or 'hermes files')",
    )
    remember.add_argument(
        "--remembered-by",
        default="user",
        help="Who decided this file matters (default: user; triage "
             "skills pass their own name)",
    )
    remember.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if the file is already remembered",
    )

    backfill = actions.add_parser(
        "backfill-files",
        help="Register pre-existing bucket objects in the file registry",
        description=(
            "Walks the Supabase bucket for objects that predate the "
            "registry and inserts a file_assets row for each one not "
            "already recorded. Idempotent on storage_path."
        ),
    )
    backfill.add_argument(
        "--prefix",
        default=None,
        help="Bucket prefix to scan (default: <user_id>/)",
    )
    backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be registered without writing",
    )
