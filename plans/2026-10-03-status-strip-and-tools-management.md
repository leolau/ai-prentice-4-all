# Status strip + Tools & Integrations management in agent-home

Status: **implemented, awaiting merge/deploy** — 2026-10-03

## Request

Two improvements to agent-home:

1. A dashboard at the top of the page showing CPU utilization, storage
   utilization, monthly token utilization, active background tasks, and
   scheduled cron jobs — self-refreshing.
2. A settings sub-page listing all MCP/CLI integrations with restart,
   re-auth, disable controls — specifically to fix Canva OAuth without
   leaving agent-home. Reuse the Hermes Python API/infra; do not send the
   user back to the `web/` dashboard.

## Design

### Status strip

- New component `agent-home/src/components/ui/StatusStrip.tsx`, mounted in
  `MobileShell` under the header, gated on `showCoral` (authenticated app
  pages only — never on login).
- Polls `GET /api/status/summary` every 30s while the tab is visible, and
  re-reads on window focus / tab return (same pattern as
  `useChatUnreadCount`). A failed read keeps the last values and dims the
  strip with an "updating…" hint — a stale strip beats a blank one.
- New BFF aggregate `agent-home/src/app/api/status/summary/route.ts` fans
  out to five existing Python endpoints, each leg independent (a failing
  leg → null field, never a failed strip):
  - `GET /api/system/stats` → cpu_pct, mem_pct, disk_pct
  - `GET /api/analytics/usage?days=30` → tokens_month {input, output, cost}
  - `GET /api/capacity` → indicators.active_conversations
  - `GET /api/registry/projects?status=active` → running_cards (kanban
    `running` rollup summed across projects)
  - `GET /api/cron/jobs` → cron_total / cron_enabled
- Chips link to the detail pages (`/capacity`, `/models`); Tasks and Cron
  are display-only for now.
- Token chip shows measured 30-day usage, not quota — OpenCode/provider
  quota limits aren't exposed by the API, and a fake "0% of quota" would be
  worse than an honest absolute number.

### Tools & Integrations (`/settings/tools`)

- New page `agent-home/src/app/settings/tools/page.tsx` + client component
  `agent-home/src/components/settings/ToolsSettings.tsx`; linked from a new
  "Tools & Integrations" section in `SettingsView`.
- MCP server rows: enabled/transport/auth pills, endpoint, **Test
  connection** (probe → tool count), enable/disable, remove (ConfirmDialog),
  and — for `auth: oauth` servers — **Re-authenticate** plus a
  "signed in / needs auth" pill driven by token-file presence.
- CLI toolset rows: label, description, tool count, "missing keys" pill,
  enable/disable (same `platform_toolsets.cli` write the `hermes tools`
  picker uses — GUI and CLI stay in lockstep).
- All actions hold their busy state through `useRefresh()`'s post-write
  refresh so nothing can double-fire.

### MCP OAuth over HTTP (the genuinely new backend piece)

`hermes mcp login` + `hermes mcp oauth-paste` only work at a TTY. New
endpoints in `hermes_cli/web_server.py` expose the same flow to the browser:

- `POST /api/mcp/servers/{name}/oauth/start` — spawns a daemon thread that
  wipes the server's tokens (`manager.remove`), injects an
  `OAuthClientProvider` whose `redirect_handler` *captures* the
  authorization URL into shared flow state instead of printing it, and runs
  the stock `_probe_single_server`. The pasted redirect still travels the
  existing drop-file path — no parallel plumbing.
- `GET …/oauth/status` — poll for `authorization_url` / success / error.
- `POST …/oauth/redirect {url}` — validates the pasted URL carries
  `code=`/`error=` then `write_drop_redirect`s it into the waiting flow.
- `POST …/oauth/cancel` — drops an `?error=access_denied` redirect so the
  callback poller fails fast.
- `GET /api/mcp/servers` now reports `oauth_token_present` per OAuth server
  (resolved inside the profile scope — `HermesTokenStorage` reads the
  profile's `HERMES_HOME` at call time).
- One active flow at a time (the SDK keys off module-level callback
  globals); flows are keyed by `(profile, server)`; finished flows linger
  10 min for UI polling then get swept.

BFF routes under `agent-home/src/app/api/tools/mcp/[name]/{enabled,test,
oauth/{start,status,redirect,cancel}}` and `api/tools/toolsets/[name]`
proxy these with the standard principal check + `apiClientForRequest`
(profile auto-scoped via `?profile=`/`{profile}` injection).

## Canva auth recovery path (post-deploy)

Settings → Tools & Integrations → Canva row shows "needs auth" when the
token file is gone (the eviction failure mode). Click **Re-authenticate** →
the sheet shows the Canva authorization URL → approve → the browser fails
to load the `127.0.0.1` callback (expected, headless box) → paste the full
address-bar URL into the sheet → token is written under the profile's
`HERMES_HOME/mcp-tokens/` → "Test connection" confirms tools listed.

## Files

Python: `hermes_cli/web_server.py` (+~240 lines), new
`tests/hermes_cli/test_mcp_oauth_flow.py` (10 tests).

agent-home: `StatusStrip.tsx`, `MobileShell.tsx`, `settings/tools/page.tsx`,
`ToolsSettings.tsx`, `SettingsView.tsx`, `client.ts` (+12 methods),
`types/index.ts` (+7 types), 8 BFF routes, 3 new test files (15 tests).

## Verification

- `pytest tests/hermes_cli/test_mcp_oauth_flow.py`: 10 passed
- `npm run lint`: clean · `npx tsc --noEmit`: clean
- `npx vitest run`: 932 passed / 4 skipped

## Follow-ups / known limits

- Quota-style "percent of monthly allowance" isn't possible until the API
  exposes provider quota limits — the strip intentionally shows absolute
  usage.
- `Restart` isn't a per-server operation; a gateway restart endpoint
  already exists (`/api/gateway/restart`) but isn't surfaced — Canva
  re-auth + test covers the actual reported need. Add a "Restart gateway"
  button on the page later if a stuck stdio server turns out to need it.
