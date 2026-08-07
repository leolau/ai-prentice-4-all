"""Ingest local text documents into layer 4 (``hermes memory rag ingest-files``).

Drive ingestion covers what lives in Google Workspace. This covers the other
half of the corpus: files on disk — notes, exported specs, transcripts, a repo's
docs — including anything a user uploaded to the box through the dashboard's
Files page.

``source_ref`` is the resolved absolute path, so re-ingesting the same file
updates that document in place rather than creating a second copy of it, and an
unchanged file costs nothing (the content hash short-circuits before embedding).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

#: Suffixes read as UTF-8 text. Deliberately an allowlist: a directory walk
#: would otherwise try to "read" images and binaries and fill the corpus with
#: mojibake. Binary document formats (PDF, DOCX) are reported as skipped with
#: the conversion hint rather than silently dropped.
TEXT_SUFFIXES = frozenset(
    {".md", ".markdown", ".txt", ".rst", ".text", ".csv", ".tsv", ".org"}
)

#: Formats a user most plausibly points this at and that need a conversion step
#: first. Named individually so the skip reason can say what to do.
CONVERTIBLE_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".rtf", ".odt"})

#: Files above this size are skipped: a multi-megabyte log is thousands of
#: chunks, i.e. an accidental hours-long embedding job and a corpus of noise.
MAX_BYTES = 2_000_000


@dataclass
class FileIngestSummary:
    """What one ``ingest-files`` run did, in reportable terms."""

    seen: int = 0
    ingested: int = 0
    unchanged: int = 0
    skipped: int = 0
    chunks: int = 0
    failures: List[str] = field(default_factory=list)


def _title_for(path: Path, text: str) -> str:
    """Prefer the document's own first Markdown H1 over its filename."""
    for line in text.splitlines()[:20]:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or path.name
        if stripped:
            break
    return path.name


def collect_files(
    paths: Sequence[str | Path],
    *,
    recursive: bool = True,
) -> List[Path]:
    """Expand the given paths into an ordered, de-duplicated file list.

    Directories contribute their text files (recursively unless told not to);
    an explicitly named file is always included, whatever its suffix, so the
    caller finds out *why* an unsupported file was skipped instead of having it
    silently filtered out of a directory walk.
    """
    found: List[Path] = []
    seen: set[Path] = set()

    def add(candidate: Path) -> None:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            found.append(resolved)

    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            walk: Iterable[Path] = (
                sorted(path.rglob("*")) if recursive else sorted(path.glob("*"))
            )
            for child in walk:
                if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES:
                    add(child)
            continue
        add(path)
    return found


def read_document(path: Path) -> tuple[str, str]:
    """Return ``(text, skip_reason)`` for one file; text is empty when skipped."""
    if not path.exists():
        return "", "no such file"
    if not path.is_file():
        return "", "not a file"
    suffix = path.suffix.lower()
    if suffix in CONVERTIBLE_SUFFIXES:
        return "", f"{suffix} is not text — convert it first (e.g. to .md/.txt)"
    if suffix not in TEXT_SUFFIXES:
        return "", f"unsupported suffix {suffix or '(none)'}"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", f"unreadable: {exc}"
    if size > MAX_BYTES:
        return "", f"{size} bytes exceeds the {MAX_BYTES}-byte limit"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "", "not valid UTF-8 text"
    except OSError as exc:
        return "", f"unreadable: {exc}"
    if not text.strip():
        return "", "empty"
    return text, ""


def extract_text_from_bytes(
    filename: str,
    data: bytes,
    content_type: str = "",
) -> tuple[str, str]:
    """Return ``(text, skip_reason)`` for in-memory bytes.

    Mirrors :func:`read_document` but works on bytes the registry already has
    rather than a path on disk — the ``remember-file`` command downloads from
    the bucket, so there is no path to ``stat`` or ``read_text``.

    Text suffixes are decoded as UTF-8 (same allowlist). PDF and DOCX are
    extracted directly via lazy-deps (:data:`TEXT_SUFFIXES` is still the
    allowlist for the disk path, which reports PDFs as "convert first").
    """
    suffix = Path(filename).suffix.lower()
    if not data:
        return "", "empty"
    if suffix in TEXT_SUFFIXES:
        if len(data) > MAX_BYTES:
            return "", f"{len(data)} bytes exceeds the {MAX_BYTES}-byte limit"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return "", "not valid UTF-8 text"
        if not text.strip():
            return "", "empty"
        return text, ""
    if suffix == ".pdf":
        return _extract_pdf(data)
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix == ".doc":
        return "", ".doc is a legacy binary format — convert to .docx or .pdf first"
    if suffix in CONVERTIBLE_SUFFIXES:
        return "", f"{suffix} is not directly extractable — convert to .pdf, .docx, or .txt first"
    return "", f"unsupported suffix {suffix or '(none)'}"


def _extract_pdf(data: bytes) -> tuple[str, str]:
    """Extract text from a PDF via pypdf (lazy-installed)."""
    try:
        from tools.lazy_deps import ensure
        ensure("rag.pypdf", prompt=False)
    except ImportError:
        pass  # lazy_deps unavailable — fall through to raw import
    except Exception as exc:
        return "", f"pypdf not available: {exc}"
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        if not text:
            return "", "PDF contained no extractable text (may be scanned images)"
        return text, ""
    except Exception as exc:
        return "", f"PDF extraction failed: {exc}"


def _extract_docx(data: bytes) -> tuple[str, str]:
    """Extract text from a DOCX via python-docx (lazy-installed)."""
    try:
        from tools.lazy_deps import ensure
        ensure("rag.python_docx", prompt=False)
    except ImportError:
        pass
    except Exception as exc:
        return "", f"python-docx not available: {exc}"
    try:
        import io
        from docx import Document
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(parts).strip()
        if not text:
            return "", "DOCX contained no extractable text"
        return text, ""
    except Exception as exc:
        return "", f"DOCX extraction failed: {exc}"


async def ingest_files(
    rag,
    principal,
    paths: Sequence[Path],
    *,
    source_kind: str = "local",
    progress: Optional[Callable[[Path, str], None]] = None,
) -> FileIngestSummary:
    """Ingest each file, recording rather than raising per-file problems.

    One unreadable file must not end the run: the remaining documents are the
    reason the command was invoked.

    Documents land ``private:<principal>``, as Drive ingestion's do, and reach
    anyone else only through ``hermes memory rag share``. There is deliberately
    no "ingest as shared" switch: an ingestion run is the worst place for an
    instance-wide disclosure, because it is bulk, unattended and irreversible.
    """
    summary = FileIngestSummary()
    for path in paths:
        summary.seen += 1
        text, skip_reason = read_document(path)
        if skip_reason:
            summary.skipped += 1
            if progress is not None:
                progress(path, f"skipped ({skip_reason})")
            continue
        try:
            result = await rag.ingest(
                principal,
                source_kind=source_kind,
                source_ref=str(path),
                title=_title_for(path, text),
                text=text,
            )
        except Exception as exc:  # per-file failure, not a run failure
            summary.failures.append(f"{path}: {exc}")
            if progress is not None:
                progress(path, f"failed ({exc})")
            continue
        if result.skipped:
            summary.unchanged += 1
            if progress is not None:
                progress(path, result.reason or "unchanged")
            continue
        summary.ingested += 1
        summary.chunks += result.chunks
        if progress is not None:
            progress(path, f"ingested ({result.chunks} chunks)")
    return summary
