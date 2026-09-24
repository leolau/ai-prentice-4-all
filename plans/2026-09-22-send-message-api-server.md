# send_message for agent-home chat + Telegram conflict findings — 2026-09-22

## Status

| Item | State |
|---|---|
| `send_message` registered as model tool (check_fn-gated) | Implemented |
| `send_message` added to `hermes-api-server` toolset | Implemented |
| `send_message` added to prod `approvals.tools` (per-send confirm) | Applied to `/opt/data/hermes-home-staging/config.yaml` |
| Tests | Added (toolsets + registration); run on deploy |
| Telegram inbound conflict | Root cause identified — external competing poller since Sep 12; fix pending operator action on old Alibaba box |

## What changed and why

User asked the lead chat to send a WhatsApp message; the agent replied it could
not. That was correct behaviour: `send_message` was deliberately unregistered
("the agent should not decide on its own to fire off cross-platform messages").

Decision (with user): expose the tool **only** to `hermes-api-server` sessions
(agent-home) and require per-send confirmation via the existing
`tool-approval` plugin (`approvals.tools` fnmatch → `approval.request` SSE →
agent-home approve/deny surface, already wired in `web_server.py`).

- `tools/send_message_tool.py` — `registry.register(name="send_message",
  toolset="send_message", check_fn=_send_message_available)`; check_fn returns
  True only when `GatewayConfig.get_connected_platforms()` is non-empty.
- `toolsets.py` — added `"send_message"` to `hermes-api-server` only; comments
  updated to document the approval-gated exception.
- prod `config.yaml` — `send_message` added to `approvals.tools`.

Still deliberately excluded: `_HERMES_CORE_TOOLS` (CLI/messaging/cron
toolsets), `delegate_tool` subagent whitelist, background-review whitelist —
subagents and messaging-platform sessions cannot send.

## Telegram conflict findings (no code change)

`getUpdates` conflict loop since 2026-09-12 19:51 (~31k errors). Exactly one
gateway poller on the prod box; no other on-box token user. Old Alibaba ECS
`hermes-systest` (47.83.199.25, migrated to Hetzner on 2026-08-21) is still
powered on and serving — it holds the same `TELEGRAM_BOT_TOKEN` and is the
competing poller. Inbound is erratic (sessions steal from each other).

Fix options awaiting operator: stop the gateway/telegram adapter on the old
box (also stops ~$103/mo billing), or `/revoke` the bot token in BotFather and
update `TELEGRAM_BOT_TOKEN` on prod.

## Verification plan

- `pytest tests/test_toolsets.py tests/tools/test_send_message_tool.py` on the
  deployed venv.
- In lead chat: "send a telegram message saying test" → approval prompt in
  agent-home → approve → delivery.
