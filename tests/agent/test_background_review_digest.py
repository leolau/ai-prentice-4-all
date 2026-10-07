"""When the background review replays a digest instead of the full transcript.

Routed reviews always digest (cold cache on the other model). Same-model
reviews replay in full while the conversation is at or below
``auxiliary.background_review.digest_above_tokens``, and digest above it.
"""

from __future__ import annotations

import pytest

from agent import background_review
from agent.model_metadata import estimate_messages_tokens_rough


def _conversation(chars_per_msg: int, n: int) -> list[dict]:
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": "x" * chars_per_msg})
    return out


def _with_config(monkeypatch, review_cfg):
    from hermes_cli import config as config_mod

    monkeypatch.setattr(
        config_mod, "load_config",
        lambda: {"auxiliary": {"background_review": review_cfg}},
    )


def test_routed_review_always_digests(monkeypatch):
    _with_config(monkeypatch, {"digest_above_tokens": 0})
    assert background_review._review_replays_digest(_conversation(10, 4), True)


@pytest.mark.parametrize("limit,expect", [(1000, True), (100000, False), (0, False)])
def test_same_model_digest_follows_size_limit(monkeypatch, limit, expect):
    convo = _conversation(4000, 6)
    assert 1000 < estimate_messages_tokens_rough(convo) < 100000
    _with_config(monkeypatch, {"digest_above_tokens": limit})
    assert background_review._review_replays_digest(convo, False) is expect


def test_default_limit_applies_when_unset_or_invalid(monkeypatch):
    big = _conversation(4 * background_review.DEFAULT_DIGEST_ABOVE_TOKENS, 1)
    small = _conversation(40, 2)
    for cfg in ({}, {"digest_above_tokens": "lots"}):
        _with_config(monkeypatch, cfg)
        assert background_review._review_replays_digest(big, False)
        assert not background_review._review_replays_digest(small, False)


def test_large_conversation_digest_is_much_smaller(monkeypatch):
    convo = _conversation(20000, 60)
    digest = background_review._digest_history(convo)
    assert estimate_messages_tokens_rough(digest) < estimate_messages_tokens_rough(convo) / 2
    assert digest[-1] == convo[-1]
