"""Automatic recall from ``prefetch()`` — budget, cache safety, and failure.

Before P2 the live memory tier was write-only: rows accumulated and the only
reader was a deliberate ``memory_query`` tool call, which in production never
happened once. Recall now runs from the provider's ``prefetch()`` hook, whose
output ``agent/conversation_loop.py`` appends to the *current* user message at
API-call time only.

These tests hold that hook to its two invariants — the cached prompt prefix and
the stored conversation must be untouched — plus the budget that keeps a recall
affordable on every single turn, and the rule that a failed recall degrades to
silence rather than taking the turn down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from hermes_cli.access import Principal
from plugins.memory.supabase_pgvector import (
    RECALL_DEFAULTS,
    SupabasePgvectorMemoryProvider,
    _format_recall,
    _recall_settings,
)
from plugins.memory.supabase_pgvector.store import MemoryRecord


def _record(text: str, *, topic: Optional[str] = None, score: float = 0.9) -> MemoryRecord:
    return MemoryRecord(
        id=f"id-{abs(hash(text)) % 10_000}",
        owner_user_id="alice",
        visibility="private:alice",
        kind="fact",
        text=text,
        topic=topic,
        source_session=None,
        created_at=None,
        score=score,
    )


@dataclass
class _RecallStore:
    """Records how the provider asked, and answers with canned rows."""

    rows: List[MemoryRecord] = field(default_factory=list)
    dim: int = 256
    mode: str = "prod"
    model_id: str = "hashing"
    calls: List[dict] = field(default_factory=list)
    fail: bool = False

    async def query(self, principal, query_text, *, top_k=10, kind=None,
                    topic=None, min_score=0.0, record_use=False,
                    connection=None):
        self.calls.append(
            {
                "query": query_text,
                "top_k": top_k,
                "min_score": min_score,
                "record_use": record_use,
                "principal": principal.user_id,
            }
        )
        if self.fail:
            raise RuntimeError("embedding service is down")
        return list(self.rows)[:top_k]


def _provider(store: _RecallStore) -> SupabasePgvectorMemoryProvider:
    provider = SupabasePgvectorMemoryProvider()
    provider._store = store  # type: ignore[assignment]
    provider._principal = Principal(user_id="alice", display="a", role="member")
    provider._session_id = "sess-recall"
    return provider


# ---------------------------------------------------------------------------
# The recall itself
# ---------------------------------------------------------------------------

def test_prefetch_recalls_relevant_rows_for_the_turn() -> None:
    store = _RecallStore(rows=[_record("the tender closes on 14 March", topic="tenders")])
    provider = _provider(store)

    block = provider.prefetch("when does the tender close")

    assert "the tender closes on 14 March" in block
    assert "<live-memory-recall>" in block
    # Asked as the session's principal, so recall cannot widen access.
    assert store.calls[0]["principal"] == "alice"


def test_recall_applies_the_configured_budget_and_records_use() -> None:
    store = _RecallStore(rows=[_record("a fact worth recalling")])
    provider = _provider(store)
    provider._recall = dict(RECALL_DEFAULTS, top_k=3, min_score=0.5)

    provider.prefetch("what did we agree about the schedule")

    call = store.calls[0]
    assert call["top_k"] == 3
    assert call["min_score"] == 0.5
    # Automatic recall is what "this row was used" means — see _record_use.
    assert call["record_use"] is True


def test_short_turns_do_not_spend_a_vector_search() -> None:
    store = _RecallStore(rows=[_record("a fact worth recalling")])
    provider = _provider(store)

    assert provider.prefetch("ok") == ""
    assert provider.prefetch("thanks") == ""
    assert store.calls == []


def test_recall_can_be_switched_off_in_config() -> None:
    store = _RecallStore(rows=[_record("a fact worth recalling")])
    provider = _provider(store)
    provider._recall = dict(RECALL_DEFAULTS, auto=False)

    assert provider.prefetch("when does the tender close") == ""
    assert store.calls == []


def test_a_failed_recall_is_silent_and_keeps_the_task_note() -> None:
    """Recall is an enhancement; it must not take the turn down with it."""
    store = _RecallStore(rows=[_record("unreachable")], fail=True)
    provider = _provider(store)
    provider._capture_task_proposal("Track: file the tender response")

    block = provider.prefetch("what is outstanding on the tender")

    assert "live-memory-recall" not in block
    # The pending task-discovery note still reaches the turn.
    assert "file the tender response" in block


def test_no_store_means_no_recall_and_no_crash() -> None:
    provider = SupabasePgvectorMemoryProvider()
    provider._principal = Principal(user_id="alice", display="a", role="member")

    assert provider.prefetch("when does the tender close") == ""


# ---------------------------------------------------------------------------
# Cache safety / history immutability
# ---------------------------------------------------------------------------

def test_recall_never_touches_the_system_prompt_block() -> None:
    store = _RecallStore(rows=[_record("the wifi code is swordfish")])
    provider = _provider(store)

    before = provider.system_prompt_block()
    block = provider.prefetch("what is the wifi code")

    assert "swordfish" in block
    # The prefix frozen into the prompt cache at session start is untouched:
    # recall reaches the model as an injection into the current user message,
    # which conversation_loop.py builds at API-call time from a copy.
    assert provider.system_prompt_block() == before
    assert "swordfish" not in provider.system_prompt_block()


def test_recall_does_not_mutate_the_message_it_was_given() -> None:
    store = _RecallStore(rows=[_record("the tender closes on 14 March")])
    provider = _provider(store)
    turn = "when does the tender close"

    provider.prefetch(turn)

    assert turn == "when does the tender close"


# ---------------------------------------------------------------------------
# Budget arithmetic
# ---------------------------------------------------------------------------

def test_format_recall_drops_whole_memories_at_the_character_cap() -> None:
    rows = [_record("A" * 90), _record("B" * 90), _record("C" * 90)]

    block = _format_recall(rows, 200)

    assert "A" * 90 in block
    # Two fit inside 200 chars; the third is dropped entirely rather than cut,
    # because a truncated fact reads as a complete one.
    assert "C" * 90 not in block
    assert "CCC" not in block


def test_format_recall_is_empty_without_rows() -> None:
    assert _format_recall([], 1200) == ""


def test_recall_settings_default_and_override() -> None:
    assert _recall_settings(None) == RECALL_DEFAULTS
    assert _recall_settings({})["auto"] is True

    tuned = _recall_settings({"memory": {"recall": {"top_k": 9, "auto": False}}})
    assert tuned["top_k"] == 9
    assert tuned["auto"] is False
    # Unspecified keys keep their defaults rather than vanishing.
    assert tuned["max_chars"] == RECALL_DEFAULTS["max_chars"]
