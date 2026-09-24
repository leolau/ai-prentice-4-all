# Fix: OpenCode Go `x-opencode-session` header (#81584)

## Symptom

Lead chat answered a trivial question ("Which LLM are you using?") in ~2m30s,
and setting DeepSeek V4.1 Flash under OpenCode Go appeared to fail.

## Root cause

OpenCode Go began enforcing a previously optional `x-opencode-session` header
(a stable per-conversation id used for backend routing / prompt-cache
affinity). Hermes never sent it, so every inference request returned
HTTP 400 `MissingSessionID`. The turn only completed after the retry ladder
(3 attempts), a failed context-compression call, a 60s compression pause, and
fallback — hence the minutes-long simple answer.

Production log evidence (`errors.log`):

```
API call failed (attempt 1/3) provider=opencode-go
  base_url=https://opencode.ai/zen/go/v1 model=deepseek-v4.1-flash
  summary=HTTP 400: Request is missing x-opencode-session ...
context_compressor: Failed to generate context summary: 400 MissingSessionID
```

The model set itself had succeeded — the runtime was already running
`provider=opencode-go model=deepseek-v4.1-flash`; it was the calls that
failed. The picker's "Load failed" banner is a separate transient
options-refetch error, not the set path.

## Fix

Send `x-opencode-session` to `opencode.ai` hosts only, on every
construction/rebuild/request path:

- `agent/auxiliary_client.py`
  - `build_opencode_session_headers(session_id=None)` — resolves the value
    from the explicit arg, then `get_session_env("HERMES_SESSION_ID")`
    (ContextVar + env fallback), then a process-stable `hermes-<uuid>` token.
  - `_build_call_kwargs` injects `extra_headers` per request — this is the
    load-bearing fix for aux calls (compression, title gen, vision), because
    aux clients are cached across conversations in long-lived processes and
    request-level headers override the cached client's defaults.
  - `resolve_provider_client` generic api_key branch + both aux construction
    sites set `default_headers` for opencode.ai hosts.
- `agent/agent_init.py` — header on the main client's `default_headers`;
  refreshed to the final `agent.session_id` after assignment (the OpenAI SDK
  stores `default_headers` by reference in `client._custom_headers`, so one
  dict mutation covers the live client and the interrupt-rebuild path).
- `agent/agent_runtime_helpers.py` — `switch_model` rebuild path.
- `run_agent.py` — `_apply_client_headers_for_base_url` (credential
  refresh / rebuild).
- `agent/anthropic_adapter.py` — `build_anthropic_client` merges the header
  for opencode.ai hosts (Go MiniMax / qwen3.7-max run on the Anthropic wire).

The header is never sent to non-opencode hosts, and no OpenCode CLI identity
(`User-Agent`, `x-opencode-client`) is impersonated.

## Status

- [x] Implemented
- [x] Tests: `TestOpencodeSessionHeader` (6) + 3 run_agent header tests
- [x] Ruff clean; local suite green (SDK-dependent tests run on the box venv)
- [ ] Merged to develop
- [ ] Deployed + production verified (chat turn on opencode-go succeeds fast)
