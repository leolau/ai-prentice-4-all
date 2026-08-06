"""Local-file ingestion: what it collects, what it refuses, and what it reports.

The store is faked (the RAG store's own behaviour is covered by
``test_rag_store_e2e``); what matters here is the *policy* — a directory walk
never feeds binaries into the corpus, a file's path is its identity so a second
run updates rather than duplicates, one unreadable file never ends a run, and
nothing is ingested as ``shared`` unless asked.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from hermes_cli.access import Principal
from hermes_cli.rag_cmd import register_rag_subparser
from hermes_cli.rag_files import (
    MAX_BYTES,
    collect_files,
    ingest_files,
    read_document,
)

PRINCIPAL = Principal(user_id="leo_owner", display="Leo", role="owner")


class FakeRag:
    """Records ingest calls and replays a scripted outcome per source_ref."""

    def __init__(self, outcomes=None, failing: set[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.outcomes = outcomes or {}
        self.failing = failing or set()

    async def ingest(self, principal, **kwargs):
        from plugins.memory.supabase_pgvector.rag import IngestResult

        self.calls.append({"principal": principal.user_id, **kwargs})
        ref = str(kwargs["source_ref"])
        if ref in self.failing:
            raise RuntimeError("embedding service is down")
        return self.outcomes.get(ref, IngestResult("doc-1", 3, False))


def _write(directory: Path, name: str, text: str = "hello world") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path.resolve()


def test_a_directory_walk_takes_text_documents_and_leaves_binaries(tmp_path) -> None:
    """A folder of mixed content must not put images into a text corpus."""
    _write(tmp_path, "notes.md", "# Notes\nbody")
    _write(tmp_path, "spec.txt")
    _write(tmp_path, "nested/deep.rst")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0binary")
    (tmp_path / "archive.zip").write_bytes(b"PK\x03\x04")

    found = collect_files([tmp_path])

    assert sorted(path.name for path in found) == ["deep.rst", "notes.md", "spec.txt"]


def test_no_recursive_stops_at_the_top_level(tmp_path) -> None:
    _write(tmp_path, "top.md")
    _write(tmp_path, "nested/deep.md")

    found = collect_files([tmp_path], recursive=False)

    assert [path.name for path in found] == ["top.md"]


def test_a_named_file_is_kept_whatever_its_suffix(tmp_path) -> None:
    """Naming a PDF explicitly must produce a *reason*, not silence.

    Filtering it out during collection would leave the user staring at "files
    seen: 0" with no idea their file was rejected for being a PDF.
    """
    pdf = _write(tmp_path, "tender.pdf", "%PDF-1.4")

    found = collect_files([pdf])

    assert found == [pdf]
    text, reason = read_document(pdf)
    assert text == ""
    assert "convert it first" in reason


def test_the_same_file_named_twice_is_ingested_once(tmp_path) -> None:
    path = _write(tmp_path, "notes.md")

    found = collect_files([path, tmp_path, str(path)])

    assert found == [path]


@pytest.mark.parametrize(
    "name,contents,expected",
    [
        ("empty.md", "   \n", "empty"),
        ("weird.xyz", "text", "unsupported suffix .xyz"),
    ],
)
def test_read_document_explains_each_refusal(tmp_path, name, contents, expected) -> None:
    path = _write(tmp_path, name, contents)

    text, reason = read_document(path)

    assert text == ""
    assert expected in reason


def test_read_document_refuses_a_file_that_is_not_utf8(tmp_path) -> None:
    path = tmp_path / "latin.txt"
    path.write_bytes(b"caf\xe9 not utf-8")

    text, reason = read_document(path)

    assert text == ""
    assert "UTF-8" in reason


def test_read_document_refuses_an_oversized_file(tmp_path) -> None:
    """A giant log is thousands of chunks — an accidental hours-long job."""
    path = _write(tmp_path, "huge.txt", "x" * (MAX_BYTES + 1))

    text, reason = read_document(path)

    assert text == ""
    assert "exceeds" in reason


def test_read_document_reports_a_missing_file(tmp_path) -> None:
    text, reason = read_document(tmp_path / "gone.md")

    assert (text, reason) == ("", "no such file")


@pytest.mark.asyncio
async def test_the_absolute_path_is_the_documents_identity(tmp_path) -> None:
    """Re-running must update the document, not create a second copy."""
    path = _write(tmp_path, "notes.md")
    rag = FakeRag()

    await ingest_files(rag, PRINCIPAL, [path])
    await ingest_files(rag, PRINCIPAL, [path])

    assert [call["source_ref"] for call in rag.calls] == [str(path), str(path)]
    assert {call["source_kind"] for call in rag.calls} == {"local"}


@pytest.mark.asyncio
async def test_a_markdown_h1_titles_the_document_over_its_filename(tmp_path) -> None:
    path = _write(tmp_path, "2026-q3-final-v2.md", "# Q3 Revenue Review\n\nbody")
    rag = FakeRag()

    await ingest_files(rag, PRINCIPAL, [path])

    assert rag.calls[0]["title"] == "Q3 Revenue Review"


@pytest.mark.asyncio
async def test_the_filename_titles_a_document_with_no_heading(tmp_path) -> None:
    path = _write(tmp_path, "notes.txt", "body first, no heading\n# late heading")
    rag = FakeRag()

    await ingest_files(rag, PRINCIPAL, [path])

    assert rag.calls[0]["title"] == "notes.txt"


@pytest.mark.asyncio
async def test_ingestion_never_asks_for_shared_visibility(tmp_path) -> None:
    """Bulk ingestion is the worst place for an instance-wide disclosure.

    Sharing is a deliberate, per-document act (``rag share``), so the ingest
    path must not carry a visibility argument at all — matching Drive
    ingestion, which has no "ingest as shared" flag either.
    """
    path = _write(tmp_path, "notes.md")
    rag = FakeRag()

    await ingest_files(rag, PRINCIPAL, [path])

    assert "visibility" not in rag.calls[0]


@pytest.mark.asyncio
async def test_one_failing_file_never_ends_the_run(tmp_path) -> None:
    first = _write(tmp_path, "a.md")
    broken = _write(tmp_path, "b.md")
    last = _write(tmp_path, "c.md")
    rag = FakeRag(failing={str(broken)})

    summary = await ingest_files(rag, PRINCIPAL, [first, broken, last])

    assert (summary.seen, summary.ingested, summary.chunks) == (3, 2, 6)
    assert len(summary.failures) == 1
    assert "b.md" in summary.failures[0]


@pytest.mark.asyncio
async def test_unchanged_and_skipped_are_reported_apart(tmp_path) -> None:
    """"Nothing to do" and "I refused this" are different operator signals."""
    from plugins.memory.supabase_pgvector.rag import IngestResult

    unchanged = _write(tmp_path, "same.md")
    refused = _write(tmp_path, "scan.pdf", "%PDF")
    rag = FakeRag(outcomes={str(unchanged): IngestResult("doc-1", 0, True, "unchanged")})

    summary = await ingest_files(rag, PRINCIPAL, [unchanged, refused])

    assert (summary.unchanged, summary.skipped, summary.ingested) == (1, 1, 0)
    assert [call["source_ref"] for call in rag.calls] == [str(unchanged)]


@pytest.mark.asyncio
async def test_progress_reports_every_file_including_the_skipped_ones(tmp_path) -> None:
    good = _write(tmp_path, "a.md")
    refused = _write(tmp_path, "b.pdf", "%PDF")
    seen: list[tuple[str, str]] = []
    rag = FakeRag()

    await ingest_files(
        rag,
        PRINCIPAL,
        [good, refused],
        progress=lambda path, outcome: seen.append((path.name, outcome)),
    )

    assert seen[0] == ("a.md", "ingested (3 chunks)")
    assert seen[1][0] == "b.pdf" and "skipped" in seen[1][1]


def test_the_cli_exposes_ingest_files_with_its_defaults() -> None:
    parser = argparse.ArgumentParser()
    memory = parser.add_subparsers(dest="memory_command")
    register_rag_subparser(memory)

    args = parser.parse_args(
        ["rag", "--as", "leo_owner", "ingest-files", "/tmp/a.md", "/tmp/docs"]
    )

    assert args.rag_command == "ingest-files"
    assert args.paths == ["/tmp/a.md", "/tmp/docs"]
    assert args.source_kind == "local"
    assert args.no_recursive is False
    assert not hasattr(args, "shared")
