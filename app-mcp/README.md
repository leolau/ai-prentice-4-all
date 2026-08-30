# app-mcp — the agent's eyes and hands on agent-home

app-mcp is internal app infrastructure: a standalone service that lets the
Hermes agent **see** and **drive** the agent-home UI at runtime.

Two capabilities, exposed as MCP tools (`mcp_app_*` on the agent side):

1. **Introspection** — what page the user is on, which element they last
   touched, and a live, detailed description of every interactive element on
   the page (role, accessible name, value, state). Descriptions come from the
   live DOM — there is no registry to drift.
2. **Control** — click, type, select, focus, read, scroll, navigate. Safe
   actions run immediately; anything destructive (delete / archive / send /
   submit …) must go through `app_act_destructive`, which Hermes gates behind
   its tool-approval flow, so the user approves in-chat before it happens.

**Awareness is automatic**: the agent-home browser bridge reports page +
last-active element to this service, and every chat turn sent from the app
carries a one-line `[app context: …]` ahead of the message (see
`agent-home/src/lib/chat/ui-context.ts`). The user can say "this page" or
"the button I'm on" and the agent knows what they mean.

## Architecture

```
Browser (agent-home)                     Box
────────────────────                     ───
AppMcpBridge (client)   ──WSS──▶  app-mcp (this service)
  · reports page + focus            · WS hub      127.0.0.1:9221
  · executes commands               · MCP endpoint 127.0.0.1:9220/mcp
      ▲                                 ▲
      │ 60s HMAC ticket                 │ mcp_servers: app:
  POST /api/app-mcp/ticket        Hermes agent (tools/mcp_tool.py)
  (BFF, signed session)           approvals.tools: [mcp_app_app_act_destructive]
```

- **Auth**: the browser never shares its session cookie with this service.
  The agent-home BFF (which owns authentication) mints a 60-second
  HMAC-signed ticket (`<user_id>.<expiry_ms>.<sig>`); the service verifies it
  with the shared secret (`APP_MCP_TICKET_SECRET` ==
  `AGENT_HOME_APP_MCP_SECRET`).
- **Exposure**: nothing public except Caddy's `/app-mcp/ws` reverse proxy on
  the agent-home origin. The MCP endpoint is loopback-only.
- **Graceful degradation**: no secret configured → ticket route answers 503,
  the bridge retries quietly, everything else works. Service down → MCP tools
  return a structured "no app session connected" error.

## MCP tools

| Tool | Purpose |
|---|---|
| `app_state` | Current page + last-active element + connection status |
| `app_pages` | The app's page map with the current page marked |
| `app_describe_page` | Live snapshot: every interactive element with id/role/name/state |
| `app_act` | Safe actions (click/type/select/focus/read/scroll/navigate/snapshot); refuses destructive-looking targets |
| `app_act_destructive` | Same, for destructive actions — gated by `approvals.tools` |

## Hermes wiring (box config.yaml)

```yaml
mcp_servers:
  app:
    url: "http://127.0.0.1:9220/mcp"

approvals:
  tools:
    - mcp_app_app_act_destructive
```

Restart the gateway after changing either block.

## Running

```bash
# dev
APP_MCP_TICKET_SECRET=$(openssl rand -hex 32) python -m app_mcp.server

# tests (stdlib + pytest only)
python -m pytest
```

Production install: `deploy/app-mcp.service` + `deploy/app-mcp.env.example`
(see the headers of those files). The service reuses the hermes-agent venv —
`mcp` and `websockets` are already pinned there.
