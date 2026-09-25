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
- `agent-home/src/lib/folder-bridge/fsOps.ts` — `importFile` now posts the
  `File` directly as the request body (browser streams from disk, zero
  buffering) instead of `arrayBuffer()` + `new File([bytes])` + FormData.
- `agent-home/src/app/api/files/import/route.ts` — rewritten to stream the
  raw request body through a SHA-256 `TransformStream` straight to Supabase
  Storage with `duplex: 'half'` (zero buffering in the BFF). Metadata
  travels in headers (`x-file-name`, `x-source-path`, `x-folder-label`).
- `agent-home/src/lib/supabase/storage.ts` — `uploadChatMediaStream()`
  accepts a `ReadableStream` body and passes `duplex: 'half'` to storage-js.
- `agent-home/src/instrumentation.ts` — Next.js instrumentation hook that
  disables Node's default 300s `requestTimeout` on server startup so
  streaming uploads of any size aren't killed mid-transfer.
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

- [x] Implementation
- [x] Tests pass (27 app-mcp pytest, 38 vitest)
- [ ] PR merged — [#457](https://github.com/leolau/ai-prentice-4-all/pull/457)
- [ ] Code deployed
- [x] app-mcp service installed + wired on the box — unit active, ports
      9220/9221 listening, Caddy `/app-mcp/*` routes added, `mcp_servers.app`
      (timeout 3600) + README approvals gating in `config.yaml`, gateway /
      dashboard / agent-home restarted 2026-09-25 ~21:07 box time
- [ ] Live verification (ticket, WS connect, large import)
