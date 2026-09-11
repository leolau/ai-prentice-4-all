"""Alibaba Token Plan ``insufficient_quota`` 429s: retry through the
per-minute window before handing the turn to ``fallback_providers``.

The Token Plan endpoint reports a tripped tokens-per-minute ceiling with the
same ``insufficient_quota`` body it uses for a spent plan and no Retry-After.
The generic rate-limit path falls over to the fallback chain on the first
429, which moves every long turn off the primary model (and its prompt
cache) for a bucket that refills within a minute. The loop must instead
spend its retry budget on the long schedule and fall back only once that is
exhausted; an ordinary 429 keeps the eager fallback.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent

TOKEN_PLAN_URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
FALLBACK = [{"provider": "alibaba", "model": "qwen3.8-max", "base_url": TOKEN_PLAN_URL}]


def _tool_defs():
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "search",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def _make_agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs()),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI", return_value=MagicMock()),
    ):
        agent = AIAgent(
            api_key="primary-key-abcdef12",
            base_url=TOKEN_PLAN_URL,
            provider="alibaba",
            model="glm-5.2",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            fallback_model=FALLBACK,
        )
        agent.client = MagicMock()
        agent._api_max_retries = 3
        return agent


def _response(content: str):
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(choices=[choice], model="m", usage=None)


class QuotaError(Exception):
    status_code = 429

    def __init__(self, body: dict):
        super().__init__(f"Error code: 429 - {body}")
        self.response = SimpleNamespace(headers={})
        self.body = body


def _token_plan_quota_error():
    return QuotaError(
        {
            "error": {
                "message": "Allocated quota exceeded, please increase your quota limit.",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        }
    )


def _plain_rate_limit_error():
    return QuotaError({"error": {"message": "rate limit exceeded", "code": "rate_limit_exceeded"}})


def _run(agent, fake_api_call):
    fb_client = MagicMock()
    fb_client.api_key = "primary-key-abcdef12"
    fb_client.base_url = TOKEN_PLAN_URL
    fb_client._custom_headers = None
    fb_client.default_headers = None
    with (
        patch.object(agent, "_interruptible_api_call", side_effect=fake_api_call),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch("run_agent.OpenAI", return_value=MagicMock()),
        patch("agent.agent_runtime_helpers.time.sleep"),
        patch("agent.conversation_loop.time.sleep"),
        patch("agent.conversation_loop.jittered_backoff", return_value=2.0),
        patch("agent.retry_utils.jittered_backoff", side_effect=lambda *a, **kw: kw["base_delay"]),
        patch(
            "agent.auxiliary_client.resolve_provider_client",
            return_value=(fb_client, "qwen3.8-max"),
        ),
        patch(
            "hermes_cli.model_normalize.normalize_model_for_provider",
            side_effect=lambda m, p: m,
        ),
        patch("agent.model_metadata.get_model_context_length", return_value=200000),
    ):
        result = agent.run_conversation("hello")
    return result


def _retry_waits(caplog) -> list[float]:
    waits = []
    for record in caplog.records:
        match = re.match(r"Retrying API call in ([0-9.]+)s", record.getMessage())
        if match:
            waits.append(float(match.group(1)))
    return waits


def test_token_plan_quota_retries_primary_through_window_then_falls_back(caplog):
    caplog.set_level(logging.WARNING, logger="agent.conversation_loop")
    agent = _make_agent()
    calls: list[tuple[str, str]] = []

    def fake_api_call(api_kwargs):
        calls.append((agent.provider, agent.model))
        if agent.model == "glm-5.2":
            raise _token_plan_quota_error()
        return _response("finished on fallback")

    result = _run(agent, fake_api_call)

    assert result["completed"] is True
    assert result["final_response"] == "finished on fallback"
    # The whole retry budget is spent on the primary before switching.
    assert calls == [("alibaba", "glm-5.2")] * 3 + [("alibaba", "qwen3.8-max")]
    # The second retry already waits for the per-minute bucket, not seconds.
    assert _retry_waits(caplog) == [2.0, 45.0]


def test_token_plan_quota_recovers_on_primary_without_fallback():
    agent = _make_agent()
    calls: list[str] = []

    def fake_api_call(api_kwargs):
        calls.append(agent.model)
        if len(calls) < 3:
            raise _token_plan_quota_error()
        return _response("window rolled")

    result = _run(agent, fake_api_call)

    assert result["final_response"] == "window rolled"
    assert calls == ["glm-5.2", "glm-5.2", "glm-5.2"]
    assert agent._fallback_activated is False


def test_plain_429_on_same_host_keeps_eager_fallback():
    agent = _make_agent()
    calls: list[str] = []

    def fake_api_call(api_kwargs):
        calls.append(agent.model)
        if agent.model == "glm-5.2":
            raise _plain_rate_limit_error()
        return _response("fallback")

    result = _run(agent, fake_api_call)

    assert result["final_response"] == "fallback"
    assert calls == ["glm-5.2", "qwen3.8-max"]
