"""Unit tests for the CJK-aware search tokenisation.

The failure this guards against is silent: if write-time and query-time
segmentation ever disagree, nothing errors — searches just stop matching. So
these assert the *relationship* between the two sides, not only the shape of
one.
"""

from __future__ import annotations

import pytest

from hermes_cli.text_search import (
    bigrams,
    has_unsegmented,
    is_unsegmented,
    needs_substring_fallback,
    searchable,
)


def test_chinese_run_becomes_overlapping_bigrams() -> None:
    assert searchable("明天的會議") == "明天 天的 的會 會議"


def test_a_word_in_the_middle_of_a_sentence_is_produced_verbatim() -> None:
    """The under-match bug, stated as a test.

    Postgres indexes the whole sentence as one lexeme and 會議 never matches.
    After segmentation the query's own token must appear literally among the
    body's tokens — that identity is what makes the index find it.
    """
    body = searchable("請問明天的會議改到下午三點嗎").split()
    assert searchable("會議") in body


def test_latin_text_is_left_alone() -> None:
    """Space-delimited scripts already tokenise correctly; touching them
    would only lose information."""
    assert searchable("Invoice #42 from Ada Wong") == "Invoice #42 from Ada Wong"


def test_mixed_script_segments_only_the_cjk_runs() -> None:
    assert searchable("Re: 報價單 for Q3") == "Re: 報價 價單 for Q3"


def test_japanese_and_korean_are_segmented_too() -> None:
    assert searchable("会議室") == "会議 議室"
    assert searchable("회의실") == "회의 의실"


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_input_yields_empty_output(text: str) -> None:
    assert searchable(text) == ""


def test_single_character_survives_rather_than_vanishing() -> None:
    """A one-character run has no bigram; dropping it would make the text
    unsearchable by any means, so it is emitted whole."""
    assert searchable("會") == "會"
    assert bigrams("會") == ["會"]


def test_single_cjk_query_is_flagged_for_the_substring_path() -> None:
    """It cannot be answered by bigrams however the row was indexed."""
    assert needs_substring_fallback("會") is True
    assert needs_substring_fallback("會議") is False
    assert needs_substring_fallback("a") is False
    assert needs_substring_fallback("") is False


def test_script_classification() -> None:
    assert is_unsegmented("會") and is_unsegmented("あ") and is_unsegmented("한")
    assert not is_unsegmented("a") and not is_unsegmented(" ")
    assert has_unsegmented("hello 會議") and not has_unsegmented("hello")


def test_long_text_is_bounded() -> None:
    """Bigram expansion doubles the token count, so the input is capped."""
    out = searchable("會" * 100_000)
    assert len(out.split()) < 70_000


def test_punctuation_between_runs_does_not_merge_them() -> None:
    """Two separate words must not produce a bigram spanning the boundary."""
    tokens = searchable("會議,報價").split()
    assert "議," not in tokens and "議報" not in tokens
