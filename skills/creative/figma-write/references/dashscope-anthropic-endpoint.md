# DashScope Anthropic-compatible endpoint

Claude Code (the Figma bridge transport) requires an **Anthropic Messages-compatible**
endpoint — it cannot call a raw OpenAI `/v1/chat/completions` URL. DashScope exposes
both protocols, but **the correct Anthropic endpoint depends on which DashScope key you
hold**, and using the wrong one fails with `403 invalid api-key`.

## Two endpoints

| Key type | Anthropic endpoint | Status |
| --- | --- | --- |
| Standard intl (Model Studio) | `https://dashscope-intl.aliyuncs.com/apps/anthropic/v1/messages` | Works |
| **Token-plan (Singapore)** | `https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1/messages` | Works — `figma_write.sh` defaults to this |
| Token-plan key → intl endpoint | — | `403 invalid api-key` |
| Token-plan host OpenAI path (`/compatible-mode/v1`) | — | `200`, but **OpenAI format — Claude Code cannot use it** |

`figma_write.sh` hardcodes the token-plan URL as the default because that is the key in
this host's `.env`. If you switch to a standard DashScope intl key, override before
calling the script:

```bash
export ANTHROPIC_BASE_URL=https://dashscope-intl.aliyuncs.com/apps/anthropic
```

## Why not the OpenAI endpoint?

The host's `DASHSCOPE_BASE_URL` is
`https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` — OpenAI
Chat Completions format. Works fine for Hermes (OpenAI client) and as a control test
(`POST …/chat/completions` returns 200), but Claude Code's transport only speaks
Anthropic Messages, so pointing it at the OpenAI path will not work. The Anthropic path
lives at `/apps/anthropic/v1/messages` on the same host.

## Probe recipe

When in doubt which endpoint a DashScope key is valid for, probe before wiring it
into the bridge:

```bash
source ~/.hermes/.env

# Token-plan endpoint (works with token-plan keys)
curl -s -w "\n%{http_code}" \
  -X POST "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1/messages" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"glm-5.2","max_tokens":20,"messages":[{"role":"user","content":"OK"}]}'

# Standard intl endpoint (works with intl/Model Studio keys; 403 for token-plan)
curl -s -w "\n%{http_code}" \
  -X POST "https://dashscope-intl.aliyuncs.com/apps/anthropic/v1/messages" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${DASHSCOPE_API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"glm-5.2","max_tokens":20,"messages":[{"role":"user","content":"OK"}]}'
```

A `200` with a JSON body containing `"type":"message"` and a `content` array (possibly
including a `"type":"thinking"` block for reasoning models like glm-5.2) means the
endpoint accepts the key + model. `403 invalid api-key` means wrong endpoint for that
key — try the other one.

## Auth header

The Anthropic endpoint accepts `Authorization: Bearer <key>` OR `x-api-key: <key>`.
Claude Code sets `ANTHROPIC_AUTH_TOKEN`, which surfaces as `Authorization: Bearer`, so
the bridge works without special header handling.

## Other Anthropic-compatible backends (fallback chain)

If neither DashScope endpoint is available, the bridge falls through to DeepSeek's
Anthropic endpoint (`https://api.deepseek.com/anthropic`, model `deepseek-chat`).
Z.AI (GLM) also publishes `https://api.z.ai/api/anthropic` for GLM Coding Plan
subscribers — a separate key (`ZAI_API_KEY`), not wired into this host's `.env` today.
