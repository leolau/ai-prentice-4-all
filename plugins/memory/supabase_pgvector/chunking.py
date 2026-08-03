"""Heading-aware chunking for the RAG tier (FG-21 P4).

Retrieval quality is decided here more than in the model: a chunk is the unit a
search can return, so a chunk that spans two unrelated sections dilutes both,
and a chunk cut mid-sentence retrieves text nobody can act on. Three rules
follow from that, and each one exists because of a specific failure:

* **Never merge across a heading.** A section boundary is the author's own
  statement that the topic changed; crossing it is how "3. Scope" ends up
  answering a question about "4. Pricing".
* **Split on paragraph, then sentence, never mid-word.** A chunk is quoted back
  to the user, so it has to read as prose.
* **Keep the heading path on the chunk** (``Proposal › 3. Scope``), because a
  citation without a location is not verifiable, and a chunk taken out of its
  section is ambiguous even to a human.

Token counts are *estimated* rather than tokenised. The embedding model is
loopback-only and 300 ms per call, so tokenising to plan chunks would cost more
than embedding them, and the budget only needs to be right to within a few
percent of the model's 8,192-token window. The estimate is language-aware
because a character-based one is wrong by 4x on Chinese — which is half this
corpus, and the case a naive chunker silently truncates.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

#: Target chunk size, in estimated tokens. Comfortably inside every embedding
#: model's window (bge-m3 has 8,192) while staying specific enough that a hit
#: points at one idea rather than a whole page.
DEFAULT_TARGET_TOKENS = 512

#: Tokens of trailing context repeated at the start of the next chunk, so a fact
#: split across a boundary is still retrievable from one side of it.
DEFAULT_OVERLAP_TOKENS = 64

#: Chunks shorter than this are dropped as pure structure (a page number, a
#: stray footer). Deliberately tiny: a short sentence can be the most important
#: line in a tender ("Deadline: 4 April"), and the rule never applies to a
#: document whose only content is short.
MIN_CHUNK_TOKENS = 3

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_SETEXT_RE = re.compile(r"^(=+|-{2,})$")
_NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(\S.{0,118})$")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？；;:])\s+|(?<=[。！？])")


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text`` for chunk planning.

    CJK and other wide scripts encode to roughly one token per character, Latin
    script to roughly one per four. A single ratio is therefore wrong by 4x on
    one language or the other; for a bilingual corpus both matter, so each
    character is counted according to its own script.
    """
    if not text:
        return 0
    wide = 0
    narrow = 0
    for char in text:
        if char.isspace():
            narrow += 1
            continue
        if unicodedata.east_asian_width(char) in ("W", "F"):
            wide += 1
        else:
            narrow += 1
    return wide + (max(1, round(narrow / 4)) if narrow else 0)


@dataclass(frozen=True)
class Chunk:
    """One embeddable span of a document, with the location it came from."""

    ordinal: int
    text: str
    section: str
    token_count: int


@dataclass(frozen=True)
class _Block:
    """A paragraph plus the heading path in force where it appears."""

    text: str
    section: str
    tokens: int


def _heading_path(stack: Sequence[Tuple[int, str]], title: str) -> str:
    parts = [title] if title else []
    # A document whose first heading repeats its title (a common export shape)
    # would otherwise cite as "Proposal › Proposal › 3. Scope".
    parts.extend(text for _, text in stack if text and text != title)
    return " › ".join(part for part in parts if part)


def _blocks(text: str, title: str) -> List[_Block]:
    """Split into paragraphs, tagging each with the heading path above it.

    Markdown ATX headings, setext underlines and numbered section headers are all
    recognised: exported Google Docs arrive as any of the three, and treating a
    numbered heading as body text is what produces chunks that straddle sections.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    stack: List[Tuple[int, str]] = []
    blocks: List[_Block] = []
    buffer: List[str] = []

    def flush() -> None:
        if not buffer:
            return
        paragraph = "\n".join(buffer).strip()
        buffer.clear()
        if not paragraph:
            return
        blocks.append(
            _Block(
                text=paragraph,
                section=_heading_path(stack, title),
                tokens=estimate_tokens(paragraph),
            )
        )

    def push(level: int, heading: str) -> None:
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))

    for index, raw in enumerate(lines):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        atx = _HEADING_RE.match(stripped)
        if atx:
            flush()
            push(len(atx.group(1)), atx.group(2).strip())
            continue
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if _SETEXT_RE.fullmatch(following) and not buffer:
            flush()
            push(1 if following.startswith("=") else 2, stripped)
            continue
        if _SETEXT_RE.fullmatch(stripped) and not buffer:
            continue
        numbered = _NUMBERED_RE.match(stripped)
        if numbered and len(stripped) < 120 and not stripped.endswith("."):
            flush()
            # Numbered sections nest *under* a document title or setext
            # heading, so "1. Background" is level 2, not level 1.
            push(2 + numbered.group(1).count("."), stripped)
            continue
        buffer.append(line)
    flush()
    return blocks


def _sentences(paragraph: str) -> List[str]:
    parts = [part.strip() for part in _SENTENCE_END.split(paragraph)]
    return [part for part in parts if part]


def _split_oversized(block: _Block, target: int) -> List[_Block]:
    """Break one over-long paragraph on sentence boundaries.

    A single paragraph can exceed the budget on its own (a table, a wall of
    contract text). Splitting on sentences keeps each piece readable; a sentence
    that is itself over budget is kept whole rather than cut mid-word, because a
    truncated sentence retrieves as a fragment nobody can verify.
    """
    if block.tokens <= target:
        return [block]
    pieces: List[_Block] = []
    current: List[str] = []
    used = 0
    for sentence in _sentences(block.text) or [block.text]:
        tokens = estimate_tokens(sentence)
        if current and used + tokens > target:
            pieces.append(
                _Block(" ".join(current), block.section, used)
            )
            current = []
            used = 0
        current.append(sentence)
        used += tokens
    if current:
        pieces.append(_Block(" ".join(current), block.section, used))
    return pieces


def _overlap_text(text: str, overlap_tokens: int) -> str:
    """Trailing sentences of ``text`` worth about ``overlap_tokens``."""
    if overlap_tokens <= 0:
        return ""
    tail: List[str] = []
    used = 0
    for sentence in reversed(_sentences(text)):
        tokens = estimate_tokens(sentence)
        if tail and used + tokens > overlap_tokens:
            break
        tail.insert(0, sentence)
        used += tokens
    return " ".join(tail)


def chunk_document(
    text: str,
    *,
    title: str = "",
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> List[Chunk]:
    """Chunk ``text`` into embeddable spans, each labelled with its section.

    Chunks never span a heading, are numbered in document order, and carry an
    overlap of trailing prose from the previous chunk *within the same section*
    so a fact spanning a boundary stays retrievable.
    """
    target = max(32, int(target_tokens))
    overlap = max(0, min(int(overlap_tokens), target // 2))
    blocks: List[_Block] = []
    for block in _blocks(text, title):
        blocks.extend(_split_oversized(block, target))

    chunks: List[Chunk] = []
    buffer: List[str] = []
    section: Optional[str] = None
    used = 0

    def emit() -> None:
        nonlocal buffer, used
        if not buffer:
            return
        body = "\n\n".join(buffer).strip()
        tokens = estimate_tokens(body)
        if body and (tokens >= MIN_CHUNK_TOKENS or not chunks):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=body,
                    section=section or title,
                    token_count=tokens,
                )
            )
        buffer = []
        used = 0

    for block in blocks:
        if section is not None and block.section != section:
            emit()
            section = block.section
        elif section is None:
            section = block.section
        if buffer and used + block.tokens > target:
            carried = _overlap_text("\n\n".join(buffer), overlap)
            emit()
            if carried:
                buffer.append(carried)
                used = estimate_tokens(carried)
        buffer.append(block.text)
        used += block.tokens
    emit()
    return chunks


__all__ = [
    "Chunk",
    "DEFAULT_OVERLAP_TOKENS",
    "DEFAULT_TARGET_TOKENS",
    "chunk_document",
    "estimate_tokens",
]
