"""Chunker invariants (FG-21 P4).

A chunk is the unit retrieval can return, so these are correctness properties,
not formatting preferences: a chunk spanning two sections answers questions
about the wrong one, and a mis-estimated budget silently truncates Chinese.
"""

from __future__ import annotations

from plugins.memory.supabase_pgvector.chunking import (
    chunk_document,
    estimate_tokens,
)


def test_token_estimate_is_language_aware() -> None:
    """One ratio for both scripts is wrong by ~4x on Chinese.

    Chinese is half this corpus, so an English-calibrated chars/4 estimate would
    plan chunks four times too large — the case a naive chunker truncates without
    reporting anything.
    """
    english = "the tender submission deadline is the fourth of April"
    chinese = "招標截止日期是四月四日"
    assert estimate_tokens(chinese) == len(chinese)
    # ~1 token per 4 chars of Latin, so a much longer English string estimates
    # smaller than the short Chinese one.
    assert estimate_tokens(english) < len(english) / 2
    assert estimate_tokens("") == 0


def test_chunks_never_span_a_heading() -> None:
    doc = """# Indy Proposal

## 3. Scope
The scope covers civil works only.

## 4. Pricing
Prices are firm for 90 days.
"""
    chunks = chunk_document(doc, title="Indy Proposal")
    assert len(chunks) == 2
    scope, pricing = chunks
    assert "civil works" in scope.text and "firm for 90 days" not in scope.text
    assert scope.section == "Indy Proposal › 3. Scope"
    assert pricing.section == "Indy Proposal › 4. Pricing"
    assert [chunk.ordinal for chunk in chunks] == [0, 1]


def test_numbered_and_underlined_headings_are_headings() -> None:
    """Exported Google Docs use all three heading styles.

    Treating a numbered heading as body text is what produces a chunk that
    straddles two sections, so each style has to be recognised.
    """
    doc = """Tender 2026-0418
================

1. Background
This tender replaces the 2025 award.

2. Requirements
Bidders must hold a valid licence.
"""
    sections = [chunk.section for chunk in chunk_document(doc)]
    assert sections == [
        "Tender 2026-0418 › 1. Background",
        "Tender 2026-0418 › 2. Requirements",
    ]


def test_a_long_section_splits_with_overlapping_prose() -> None:
    body = " ".join(
        f"Clause {n} states that the contractor shall deliver on time."
        for n in range(1, 61)
    )
    chunks = chunk_document(
        f"## 7. Clauses\n\n{body}\n", target_tokens=64, overlap_tokens=16
    )
    assert len(chunks) > 1
    assert all(chunk.section == "7. Clauses" for chunk in chunks)
    # Every chunk is near or under budget, and none is cut mid-word.
    assert all(chunk.token_count <= 96 for chunk in chunks)
    assert all(chunk.text == chunk.text.strip() for chunk in chunks)
    # The boundary overlaps, so a fact sitting on it is retrievable either side.
    tail = chunks[0].text.split()[-4:]
    assert " ".join(tail) in chunks[1].text


def test_plain_text_without_headings_still_chunks_under_the_title() -> None:
    doc = "\n\n".join(f"Paragraph {n} of the note." for n in range(1, 12))
    chunks = chunk_document(doc, title="Meeting note", target_tokens=32)
    assert len(chunks) > 1
    assert all(chunk.section == "Meeting note" for chunk in chunks)
    assert "Paragraph 1" in chunks[0].text


def test_a_tiny_document_is_one_chunk_and_is_never_dropped() -> None:
    """The minimum-size rule must not delete a document's only content."""
    chunks = chunk_document("Deadline: 4 April.", title="Note")
    assert len(chunks) == 1
    assert chunks[0].text == "Deadline: 4 April."
    assert chunks[0].section == "Note"


def test_chinese_document_chunks_on_its_own_budget() -> None:
    body = "。".join(f"第{n}條規定投標文件必須於截止日期前遞交" for n in range(1, 40))
    chunks = chunk_document(f"# 招標文件\n\n{body}\n", target_tokens=128)
    assert len(chunks) > 1
    assert all(chunk.token_count <= 192 for chunk in chunks)
    assert all(chunk.section == "招標文件" for chunk in chunks)


def test_empty_input_produces_no_chunks() -> None:
    assert chunk_document("   \n\n  ", title="Empty") == []
