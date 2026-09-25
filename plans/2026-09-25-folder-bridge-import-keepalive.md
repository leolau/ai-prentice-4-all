# Folder Bridge: activity-based command timeout (fix >1 GB imports)

Date: 2026-09-25
Branch: `folder-bridge-keepalive`

## Problem

`folder_bridge_import_file*` fails for large files. A ~1 GiB browser → BFF
→ Supabase upload takes far longer than the folder hub's fixed
`APP_MCP_FOLDER_COMMAND_TIMEOUT` (default 30 s), so the MCP tool returns
"Timed out waiting for the app" while the browser is still happily
uploading. Retrying cannot help — the wall is a fixed total deadline, and
every retry re-uploads from zero.

Production box was also missing the app-mcp deployment entirely (no unit,
no env, no Caddy `/app-mcp/*` routes, no `mcp_servers: app:` entry) — tracked
in the deployment section below.

## Design

Turn the hub timeout into a **silence timeout**: the browser sends
`{type: "progress", id}` keepalives while a command is in flight; the hub
resets the deadline on each ping. A command now only fails after
`timeout` seconds with *no sign of life* — arbitrarily large imports work
as long as the tab is alive, and a dead/closed tab still fails fast
(keepalives stop, or the WS closes and `detach` fails pending commands).

No new env vars; `APP_MCP_FOLDER_COMMAND_TIMEOUT` keeps its meaning but now
measures inactivity instead of total elapsed time.

## Changes

- `app-mcp/app_mcp/hub.py` — per-command `_progress` timestamps;
  `note_progress()`; `send_command` loops `wait_for(shield(fut), timeout)`
  and only raises `HubError` after `timeout` seconds of silence.
- `app-mcp/app_mcp/server.py` — `_ws_handler` routes `{type:"progress"}`
  messages to `note_progress`.
- `agent-home/src/lib/folder-bridge/transport.ts` — every in-flight
  command gets a 10 s progress ping until its result is sent.
- `app-mcp/tests/test_hub.py` — pings extend the deadline; silence after a
  ping still times out.
- `app-mcp/README.md` — document keepalive semantics + recommend a long
  `timeout` on the `app` MCP server entry (agent-side tool calls are
  capped by `mcp_servers.<name>.timeout`, default 300 s).

## Deployment (production box)

Separate from the code change — installs the service that was missing:

- `app-mcp.service` unit → `/etc/systemd/system/`, `app-mcp.env` at
  `/opt/data/hermes-agent/app-mcp/` (fresh `APP_MCP_TICKET_SECRET`, 0600).
- `AGENT_HOME_APP_MCP_SECRET` (same value) → agent-home env.
- `/etc/caddy/Caddyfile` `home.` site: add `/app-mcp/ws` +
  `/app-mcp/folders/ws` → `127.0.0.1:9221` (matches
  `agent-home/deploy/Caddyfile.agent-home`).
- `hermes-home-staging/config.yaml`: `mcp_servers: app:` with
  `timeout: 3600` so a long import survives the agent-side call cap, plus
  the README's `approvals.tools` gating.
- Restart gateway/dashboard/agent-home; enable + start `app-mcp.service`.

## Status

- [ ] Implementation
- [ ] Tests pass
- [ ] PR merged
- [ ] Code deployed
- [ ] app-mcp service installed + wired on the box
- [ ] Live verification (ticket, WS connect, large import)
