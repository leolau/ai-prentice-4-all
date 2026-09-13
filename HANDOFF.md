# Hermes Agent — Session Hand-off

_Last updated: 2026-09-14 by Devin (session: agent-home Projects UX fix +
Folder Bridge feature, requested by Leo)._

> **Everything below the "Live deployment status" section referencing
> Alibaba Cloud / `hermes-systest` is superseded and kept only as history.**
> Production moved to Hetzner on 2026-08-20 — see
> `docs/deployment/PRODUCTION.md` (authoritative ops doc) and
> `docs/deployment/hetzner-migration-runbook.md`. This hand-off replaces the
> stale 2026-08-05 version of this file with the current box and this
> session's work.

Product name: **ai-prentice-4-all** (built on Hermes).

## Live deployment status (verified 2026-09-13/14)

Production is **Hetzner Cloud** (project `snappop`), server `hermes`
(CX43, `nbg1`, `188.245.219.105`). Full details, service catalog, and
deploy procedures: `docs/deployment/PRODUCTION.md`. SSH:
`ssh -i ~/.ssh/hetzner_hermes_ed25519 root@188.245.219.105`.

Verified this session (read-only checks + the deploy below): all 16
long-running units (`hermes-*` + `agent-home` + `app-mcp`) `enabled/active`;
Supabase 11/11 containers healthy; the two pre-existing failed units
(`cloud-init-hotplugd.service`, `hermes-secret-backup.service`) are
unrelated to anything in this session and unchanged.

Current deployed revision: **`develop`/`main` @ `83df0eeca`** (this
session's work, merged and deployed — see below).

## What this session did

Two pieces of work, both merged to `develop` and `main`, both deployed to
production:

### 1. agent-home Projects UX fix (PR #391)

The Projects page's "what do I click next" guidance (`ProgressPanel`'s
"Next for you" card, the header's `ReadinessChecklist`) sent users to a
panel with no matching control and gave a generic reason even when a
specific one was known. Fixed by giving the `"status"` (activation) gate a
real slot in the shared `readinessItems()` ladder and having both UI
surfaces read from the same source instead of computing their own,
disagreeing messages. Full writeup:
`plans/2026-09-13-projects-next-step-clarity.md` /
`plans/2026-09-13-projects-next-step-clarity-status.md`.

### 2. Folder Bridge (PR #395, #397)

Built the feature from `folder-bridge-web-app-spec.md`: a browser page
(`/files/bridge` in agent-home) that lets a user grant the agent read-only
access to local Mac folders via the File System Access API, plus MCP tools
(`mcp_app_folder_bridge_*`) so the agent can list/search/read them during
chat. Built as a second capability on the existing `app-mcp` service
(ticket-authenticated WS hub + MCP server) rather than new infrastructure.
Full writeup: `plans/2026-09-13-folder-bridge.md` /
`plans/2026-09-13-folder-bridge-status.md`; architecture doc:
`docs/design/folder-bridge.md`.

**Deploying this required three manual steps beyond the standard deploy
script** — worth knowing for next time, all documented in
`plans/2026-09-13-folder-bridge-status.md` under "Production deploy":

1. `app-mcp.service` is **not** restarted by `/opt/data/deploy-hermes.sh`
   (its `UNITS` glob is `hermes-*.service` + `agent-home` only) — restart it
   by hand after any deploy that touches `app-mcp/`.
2. The box's `/etc/caddy/Caddyfile` is **hand-maintained, not rendered from
   the repo** — it had (and, independent of this feature, still has) drift
   from the checked-in `agent-home/deploy/Caddyfile.agent-home`. Confirmed
   this session: the box's `/app-mcp/ws*` matcher was a literal prefix match
   that would not have covered the new `/app-mcp/folders/ws` path; it was
   hand-patched to a named matcher covering both, `caddy validate`'d, then
   `systemctl reload caddy`.
3. `approvals.tools` in `/opt/data/hermes-home-staging/config.yaml` needed
   `mcp_app_folder_bridge_read_file` and `mcp_app_folder_bridge_search_files`
   added by hand (per Leo's explicit choice — these tools reach arbitrary
   local files the user approved, so reads are gated like other
   providers per `docs/deployment/mcp-approval-gating.md`), then
   `hermes-gateway.service` restarted to pick it up.

Verified post-deploy with a live probe on the box: all 6
`mcp_app_folder_bridge_*` tools register and are reachable through
`mcp_servers: app:`.

**Self-flagged issue**: I deleted the pre-edit `.bak` copies of
`config.yaml` and the Caddyfile from the box right after confirming both
changes were good, instead of leaving a rollback window open for a few
days. Their exact prior content is in the relevant PR/session transcript,
not on the box, if a revert is ever needed.

## Known gaps / open items for the next agent

- **`PRODUCTION.md`'s service catalog doesn't list `app-mcp`** even though
  it's confirmed running (verified this session). Worth adding a row for it
  there — I added detail to `docs/design/folder-bridge.md` and
  `app-mcp/README.md` instead of editing `PRODUCTION.md` itself, since that
  file's own staleness-tracking (`deploy_state.py handover`) is keyed to a
  specific set of deploy-tooling files and I didn't want to fight that
  mechanism without checking it first.
- **Projects UX item E** (optionally merging "Activate" + "Run now" into one
  click for one-off projects) was deliberately left as an open product
  decision — see `plans/2026-09-13-projects-next-step-clarity.md`.
- **`folder_bridge_list_folders`/`_list_directory`/`_get_file_metadata` are
  ungated by default** (metadata only). If more caution is wanted later,
  widen the `approvals.tools` pattern to `mcp_app_folder_bridge_*`.
- The `docs/deployment/README.md` handover-freshness check flagged itself
  as stale during this session's deploy (`handover doc STALE: fed034fa4
  ... 2 deployment path(s) changed`) — pre-existing, unrelated to this
  session's changes, not investigated further.
- Two pre-existing, unrelated warnings observed in `hermes-gateway` logs
  during this session (not touched, not caused by this work): a Telegram
  `getUpdates` polling conflict (implies another poller is holding the bot
  token off-box) and `google-workspace`/`aws-api*` MCP servers failing to
  connect (the latter: `[Errno 13] Permission denied: 'uvx'`).

## Environment / access notes

- Hetzner: `~/.config/hcloud/cli.toml` (context `snappop`); SSH key
  `~/.ssh/hetzner_hermes_ed25519`, user `root`.
- No Alibaba Cloud access is needed anymore for day-to-day ops — the
  `alibaba-cloud` MCP / `aliyun` CLI notes below are historical only
  (kept for the pre-migration rollback path noted in `PRODUCTION.md` §6).
- GitHub: standard PR flow, `develop` → `main` are kept in sync via a
  periodic "Merge develop into main" PR (see recent PR history) — direct
  pushes to either are not the norm.

---

## Historical (pre-2026-08-20 Alibaba Cloud deployment) — superseded

_Kept for history only; the Alibaba `hermes-systest` box was stopped at the
Hetzner cutover and its subscription lapsed 2026-08-26. Do not act on
anything below without re-verifying against `PRODUCTION.md` first._

The production ai-prentice-4-all (Hermes) was running on Alibaba Cloud ECS
(`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, `cn-hongkong`,
`ecs.e-c1m4.xlarge`), deployed via `aliyun` CLI `RunCommand` (no SSH). See
`docs/design/SESSION-HANDOFF-2026-07-prod-cutover.md` and
`docs/deployment/README.md` (marked superseded at the top) for the full
prior history and the migration runbook
(`docs/deployment/hetzner-migration-runbook.md`) for how the cutover itself
was done.
