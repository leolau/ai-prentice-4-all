"""The ``rag_search`` tool surface: when it exists, and what it returns.

Whether a tool is *offered* matters as much as what it does — every schema is
sent on every API call for the life of the conversation, so an unconfigured
instance must not pay for a tool that can only answer "nothing found".
"""

from __future__ import annotations

import json

import pytest

from hermes_cli.access import Principal
from plugins.memory.supabase_pgvector import (
    RAG_DEFAULTS,
    SupabasePgvectorMemoryProvider,
    _rag_settings,
)
from plugins.memory.supabase_pgvector.rag import RagHit

PRINCIPAL = Principal(user_id="leo_owner", display="Leo", role="owner")


class FakeRagStore:
    """Records the arguments the provider passes down."""

    def __init__(self, hits: list[RagHit] | None = None) -> None:
        self.hits = hits or []
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        principal: Principal,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.35,
        source_kind: str | None = None,
    ) -> list[RagHit]:
        self.calls.append(
            {
                "principal": principal.user_id,
                "query": query,
                "top_k": top_k,
                "min_score": min_score,
                "source_kind": source_kind,
            }
        )
        return self.hits


def _hit(**overrides: object) -> RagHit:
    fields: dict[str, object] = {
        "chunk_id": "c1",
        "document_id": "d1",
        "title": "Tender 2026-0418",
        "section": "Tender 2026-0418 › 2. Submission",
        "source_kind": "gdrive",
        "source_ref": "file-abc",
        "text": "Bids close at 17:00 on 4 April 2026.",
        "owner_user_id": "leo_owner",
        "score": 0.03,
        "vector_rank": 1,
        "lexical_rank": 2,
    }
    fields.update(overrides)
    return RagHit(**fields)  # type: ignore[arg-type]


def _provider(rag: FakeRagStore | None) -> SupabasePgvectorMemoryProvider:
    provider = SupabasePgvectorMemoryProvider()
    provider._store = object()  # type: ignore[assignment]
    provider._principal = PRINCIPAL
    provider._rag = rag  # type: ignore[assignment]
    return provider


def test_rag_search_is_not_offered_until_it_is_enabled() -> None:
    names = [schema["name"] for schema in _provider(None).get_tool_schemas()]

    assert names == ["memory_query", "memory_write"]


def test_rag_search_is_offered_once_a_corpus_is_configured() -> None:
    names = [
        schema["name"] for schema in _provider(FakeRagStore()).get_tool_schemas()
    ]

    assert names == ["memory_query", "memory_write", "rag_search"]


def test_rag_defaults_are_off_and_overridable_from_config() -> None:
    assert RAG_DEFAULTS["enabled"] is False
    assert _rag_settings(None)["enabled"] is False
    assert _rag_settings({"memory": {"rag": {"enabled": True, "top_k": 9}}}) == {
        **RAG_DEFAULTS,
        "enabled": True,
        "top_k": 9,
    }


def test_rag_search_returns_passages_with_their_citations() -> None:
    rag = FakeRagStore([_hit()])
    provider = _provider(rag)

    payload = json.loads(provider.handle_tool_call("rag_search", {"query": "bids"}))

    assert payload["passages"][0]["citation"] == (
        "Tender 2026-0418 › 2. Submission"
    )
    assert payload["passages"][0]["source_ref"] == "file-abc"
    assert rag.calls[0]["principal"] == "leo_owner"


def test_the_similarity_floor_comes_from_config_not_the_model() -> None:
    """The model may choose ``top_k``; it may not lower the relevance floor.

    A floor the model can set is a floor it will set to zero when it wants more
    results, which is exactly the failure the floor exists to prevent.
    """
    rag = FakeRagStore()
    provider = _provider(rag)
    provider._rag_settings = {**RAG_DEFAULTS, "min_score": 0.5}

    provider.handle_tool_call(
        "rag_search", {"query": "bids", "top_k": 3, "min_score": 0.0}
    )

    assert rag.calls[0]["min_score"] == 0.5
    assert rag.calls[0]["top_k"] == 3


def test_rag_search_without_a_corpus_says_so_instead_of_failing() -> None:
    payload = json.loads(
        _provider(None).handle_tool_call("rag_search", {"query": "bids"})
    )

    assert "not enabled" in payload["error"]


@pytest.mark.parametrize("query", ["", "   "])
def test_an_empty_query_is_refused_rather_than_searched(query: str) -> None:
    rag = FakeRagStore()

    payload = json.loads(
        _provider(rag).handle_tool_call("rag_search", {"query": query})
    )

    assert "requires a 'query'" in payload["error"]
    assert rag.calls == []


def test_non_ascii_passages_survive_the_json_round_trip() -> None:
    """CJK text must not come back as ``\\u62db`` escapes.

    The corpus is bilingual; a citation the user cannot read is not a citation.
    """
    rag = FakeRagStore([_hit(text="招標截止日期為四月四日", section="投標文件要求")])

    payload = json.loads(
        _provider(rag).handle_tool_call("rag_search", {"query": "招標"})
    )

    assert payload["passages"][0]["text"] == "招標截止日期為四月四日"
