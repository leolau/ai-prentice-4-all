# app-mcp — the agent's eyes and hands on agent-home

**Status**: built (PR A: agent-home side; PR B: standalone service)
**Owner**: agent-home infrastructure
**Decisions**: product owner, 2026-08-23

## What it is

Infrastructure that lets the running Hermes agent **see** and **drive** the
agent-home UI at runtime, as a first-class MCP server (`mcp_app_*` tools):

1. **Introspection** — the agent can ask what page the user is on and get a
   detailed, element-by-element description of that page (role, accessible
   name, value, checked/disabled state).
2. **Control** — the agent can click, type, select, focus, read, scroll, and
   navigate. Destructive or externally-visible actions (delete, archive,
   send, submit…) are a separate tool gated by Hermes' existing tool-approval
   flow, so the user approves in-chat before anything irreversible runs.
3. **Awareness** — the agent always knows the current page and the last UI
   element the user touched, because every chat turn sent from the app
   carries a one-line `[app context: …]` ahead of the message. The user can
   say "this page" or "the button I'm on" and be understood.

## Settled decisions (2026-08-23)

| Question | Choice |
|---|---|
| Where do element descriptions come from? | **Live DOM introspection** — the running page reports itself; no registry to drift |
| How does the agent stay aware? | **Auto-injected into chat turns** (one line, BFF-sanitized); `app_state` tool for details |
| Control autonomy | **Act freely; destructive actions go through the existing approval flow** |
| Where does the server live? | **Standalone `app-mcp` service** (systemd unit, loopback-only MCP endpoint) |

## Architecture

```
Browser (agent-home)                     Box
────────────────────                     ───
AppMcpBridge (client)   ──WSS──▶  app-mcp (standalone service)
  · reports page + focus            · WS hub        127.0.0.1:9221
  · live DOM snapshot               · MCP endpoint  127.0.0.1:9220/mcp
  · executes commands                   (streamable HTTP, loopback only)
      ▲                                     ▲
      │ 60s HMAC ticket                     │ mcp_servers: app:
  POST /api/app-mcp/ticket            Hermes agent (tools/mcp_tool.py)
  (BFF, under signed session)         approvals.tools: [mcp_app_app_act_destructive]
```

- The browser never shares its session cookie with the service. The BFF —
  which owns authentication — mints a 60-second HMAC ticket
  (`<user_id>.<expiry_ms>.<sig>`, base64url); the service verifies it with
  the shared secret (`APP_MCP_TICKET_SECRET` ==
  `AGENT_HOME_APP_MCP_SECRET`). One ticket, one connection attempt.
- Caddy exposes exactly one path: `/app-mcp/ws` on the agent-home origin.
  The MCP endpoint stays on loopback for the agent only.
- Elements are addressed by stable `data-appmcp-id` ids assigned during a
  snapshot, with CSS-selector and accessible-name fallbacks.
- `type` writes through the native value setter + `input`/`change` events so
  React controlled inputs actually update.
- Everything degrades: no secret → ticket route 503s and the bridge retries
  quietly; service down → MCP tools return a structured "no app session
  connected" error. The app itself never surfaces app-mcp failures.

## MCP tools

| Tool | Purpose | Approval |
|---|---|---|
| `app_state` | Current page + last-active element + connection status | no |
| `app_pages` | The app's page map, current marked | no |
| `app_describe_page` | Live snapshot of every interactive element | no |
| `app_act` | Safe actions; refuses destructive-looking targets | no |
| `app_act_destructive` | Destructive/external actions | yes (`approvals.tools`) |

The destructive split is defense in depth: `app_act` inspects the target's
accessible name (word-boundary match on delete/remove/archive/send/submit/…)
and refuses, directing the agent to the gated tool; the gated tool is
additionally matched by Hermes' fnmatch approval gate.

## Cache-safety note

Awareness rides on the **user message**, never the system prompt — the
per-conversation prompt cache stays intact (a changing one-liner prepended to
each turn is exactly the pattern skill commands already use).

## Files

- Browser side: `agent-home/src/lib/app-mcp/` (state, snapshot, actions,
  bridge), `agent-home/src/components/app-mcp/AppMcpBridge.tsx`,
  `agent-home/src/lib/chat/ui-context.ts`, ticket route under
  `agent-home/src/app/api/app-mcp/`, uiContext on both chat routes.
- Service: `app-mcp/` (ticket, guard, pages, hub, server) +
  `app-mcp/deploy/` (unit + env template).
