# Folder Bridge — plan

Source spec: `~/Downloads/folder-bridge-web-app-spec.md` ("Folder Bridge Web App —
Function Spec", v1.0.0).

Status: **in progress** — see the companion status file.

## Decision: extend `app-mcp`, don't build a new service

The spec describes a browser app that (1) lets the user grant local-folder
access via the File System Access API, (2) holds one persistent WebSocket to
a cloud backend, and (3) lets that backend search/read within approved
folders. That is, almost verbatim, the shape `app-mcp/` already is:

- A ticket-authenticated WebSocket hub the agent-home browser bridge dials
  (`agent-home/src/lib/app-mcp/bridge.ts` → `/app-mcp/ws` → `app_mcp/hub.py`).
- An MCP server (`FastMCP("app")`) exposing tools that forward a command to
  the browser and await its structured result (`app_mcp/server.py`).
- A short-lived HMAC ticket minted by the agent-home BFF under the signed
  session cookie (`/api/app-mcp/ticket`), so the standalone service never
  sees the session cookie.

Per `AGENTS.md`'s "extend, don't duplicate" and the Footprint Ladder, this
gets built as a **second capability on the same service** — a second `Hub`
instance and a second WebSocket path on the *same* loopback port (the
`websockets` library dispatches by request path already; `app_mcp/hub.Hub`
is already connection-shape-agnostic) — rather than a new systemd unit, new
port, new ticket secret, or a rewritten Next.js server with a custom
WebSocket-capable Node process (which `agent-home` deliberately doesn't have
— see the investigation below).

## Why not a custom Next.js server / new Node WS service

Investigated first (see chat history): `agent-home` and `web` both run via
plain `next start`; no custom `server.js`/`ws` dependency exists anywhere in
the repo. The one precedent for "browser needs a persistent authenticated
WebSocket to a cloud brain" is exactly `app-mcp`, a **separate Python
service** using `websockets` + `mcp` (FastMCP), not a Next.js addition.
Building a second, near-identical service (new port, new secret, new
systemd unit, new Caddy block) for a feature that is architecturally
identical to what's already deployed would be pure duplication.

## Deviations from the spec's literal shape

The spec's message envelope (§4, `{id,type,method,params,result,error,ts}`)
is illustrative ("Suggested Implementation Notes"), not a wire contract to a
system that doesn't exist yet. `app-mcp`'s existing envelope
(`{type:"cmd",id,command}` from server, `{type:"result",id,...}` from
browser, correlated by `id` with a `Future`/timeout in `Hub`) already does
the same job and is deployed, tested, and understood. Reusing it — instead
of running two envelope shapes side by side in one service — is the
"extend, don't duplicate" call. `addFolder`/`removeFolder` are **not**
server-issued commands: only a real user gesture can open
`showDirectoryPicker()`, so those stay entirely client-side (the browser
manages its own folder registry); the server-issuable operations are the
read-side ones (`listFolders`, `listDirectory`, `searchFiles`, `readFile`,
`getFileMetadata`).

## Architecture

```
Browser (agent-home, /files/bridge)          Box (app-mcp, unchanged ports)
────────────────────────────────             ─────────────────────────────
FolderBridge (client)     ──WSS──▶   WS hub  127.0.0.1:9221
  · showDirectoryPicker()              · /app-mcp/ws          → ui hub (existing)
  · Map<folderId, handle>              · /app-mcp/folders/ws  → folder hub (new)
  · list/search/read locally,              ▲
    scoped to approved handles              │ mcp_servers: app:  (existing entry,
      ▲                                     │  new tools added)
      │ 60s HMAC ticket (same ticket   Hermes agent
      │  route/secret as app-mcp)      approvals.tools:
  POST /api/app-mcp/ticket               [mcp_app_folder_bridge_read_file,
  (BFF, signed session)                   mcp_app_folder_bridge_search_files]
```

- Same shared secret, same ticket route, same MCP endpoint/port. Only a new
  WS path (routed inside the existing `_ws_handler`) and new `folder_bridge_*`
  tools on the same `FastMCP("app")` instance (so they register as
  `mcp_app_folder_bridge_*`, consistent with the existing `mcp_app_*` prefix).
- `showDirectoryPicker()` requires a real page + user gesture, so this lives
  on its own route (`/files/bridge`), linked from `/files`, not injected into
  the always-on `AppMcpBridge` that every page mounts.
- Folder handles live in memory + IndexedDB (for the "add it again without
  the picker" restoration path the API supports); nothing about them is sent
  to the server except what an explicit `listFolders`/`searchFiles`/etc.
  command asks for.

## Security posture (why this needs care beyond app-mcp's existing bar)

`app_act`/`app_act_destructive` only touch the agent-home UI itself — a
compromise there is bounded by what the app already lets a signed-in user
do. `folder_bridge_read_file` and `folder_bridge_search_files` (snippets)
read **arbitrary user-approved local files on the user's own Mac** — this is
new blast radius, exactly the kind of "reads must be gated too" case
`docs/deployment/mcp-approval-gating.md` already calls out for other
providers (prompt injection from an email/WhatsApp message could ask the
agent to read and exfiltrate a local file). Recommended config, to be
enabled deliberately by the box owner, not silently on deploy:

```yaml
approvals:
  tools:
    - mcp_app_app_act_destructive
    - mcp_app_folder_bridge_read_file
    - mcp_app_folder_bridge_search_files
```

`folder_bridge_list_folders` / `_list_directory` / `_get_file_metadata` are
left ungated by default (names/sizes/timestamps only, same sensitivity as
`app_describe_page`) — an operator who wants maximum caution can widen the
pattern to `mcp_app_folder_bridge_*`.

## What "MCP access so the user can launch this feature in chat" means here

No new "magic link" mechanism is needed: `/files/bridge` is a normal,
already-authenticated agent-home page. The agent can just tell the user to
open **Files → Folder Bridge**, the same way `app_page_help`/`app_pages`
already let it explain any other screen — so `/files/bridge` gets a
`PAGES` entry and a `HELP` guide, and `folder_bridge_state` (like
`app_state`) reports whether a browser session is currently attached, so the
agent's chat message text can say "not connected yet — open Files → Folder
Bridge" or "connected, 2 folders" without a special-purpose link tool.

## Files touched

Backend (`app-mcp/`):
- `app_mcp/server.py` — second `Hub`, path-routed `_ws_handler`, new tools.
- `app_mcp/pages.py`, `app_mcp/help.py` — `/files/bridge` entry + guide.
- `app_mcp/deploy/app-mcp.env.example` — optional longer timeout for
  search/read vs. UI-click commands.
- `README.md` — tool table, approvals recommendation.
- `tests/` — routing-helper unit test.

Frontend (`agent-home/`):
- `src/lib/folder-bridge/{types,handles,fsOps,transport}.ts`
- `src/components/files/FolderBridgeView.tsx` (+ sub-components)
- `src/app/files/bridge/page.tsx`
- `src/components/files/FilesView.tsx` — entry link
- `src/lib/folder-bridge/fsOps.test.ts`

Docs:
- `docs/design/folder-bridge.md` (new, mirrors `docs/design/app-mcp.md`)
- `agent-home/deploy/Caddyfile.agent-home` — add the (currently missing from
  the repo) `/app-mcp/ws` block plus the new `/app-mcp/folders/ws` block.
  Note: the existing `/app-mcp/ws` proxy isn't in the checked-in Caddyfile at
  all, which is a pre-existing doc/deploy gap independent of this feature —
  fixed here since this file is being touched anyway.

## Explicitly out of scope this pass (matches spec §10, plus this repo's norms)

- Write/delete file operations.
- Deploying to the production box or flipping `approvals.tools` in the live
  `config.yaml` — that's a deliberate owner decision (per the established
  norm in this session), done after this is reviewed.
- Verifying whether `app-mcp` itself is currently running in production at
  all (its own doc predates the Hetzner migration's service catalog in
  `PRODUCTION.md`, which doesn't list it) — flagged for the owner, not
  assumed.
