# Session Hand-off — agent-home (FG-20) built + deployed (2026-07)

> **Purpose:** let the next agent (or human) resume without re-deriving state.
> This note is **operational** — the design source of truth remains
> [`master-plan/README.md`](./master-plan/README.md) (decisions D1–D16,
> contracts C1–C9, FG index, gates, append-only §9 changelog) and
> [`FG-20-agent-home-nextjs-supabase.md`](./master-plan/feature-groups/FG-20-agent-home-nextjs-supabase.md).
> The prior operational note
> [`SESSION-HANDOFF-2026-07-prod-cutover.md`](./SESSION-HANDOFF-2026-07-prod-cutover.md)
> still holds for the base prod-box/DNS/TLS/gateway facts — **read it first**;
> this file layers the **agent-home** state on top and records the newer frontier.
>
> Author: devin:8cec0d47 (for Leo). Product name: **ai-prentice-4-all** (built on Hermes).

---

## 0. TL;DR of current state

- **agent-home is BUILT and LIVE.** The new mobile-first Next.js PWA
  (**FG-20**) is deployed on the prod box and reachable at
  **<https://home.leolau.ai-and-i.io>** (HTTPS via Caddy/Let's Encrypt → Next on
  `127.0.0.1:3100`). All of Wave A/B/C landed and merged to `develop`.
- **Two front-ends coexist** (Decision 3A): `agent-home` = the user/mobile face
  (all Phase-2 user panels); `web/` (served at `leolau.ai-and-i.io`) stays the
  operator/admin console. Both hit the **same** Python API + Supabase.
- **Repo renamed** `leolau/hermes-agent` → **`leolau/ai-prentice-4-all`** (old
  URLs auto-redirect). Local checkout is still `/home/ubuntu/repos/hermes-agent`.
- **Dashboard owner password was rotated** (the earlier exposed-in-chat item is
  resolved; plaintext never stored — scrypt hash + fresh signing secret).
- **NEW frontier (parallel workstream, not FG-20):** a **multi-user** PR series
  (**PR-1..PR-5**) is landing — PR-1 security foundation (identity binding +
  least-privilege DB role, #55), PR-2 Supabase/GoTrue email-password
  dashboard-auth provider (#56), PR-3 member management (`hermes member` CLI,
  #57), PR-4 agent-home Members UI (#58) — all merged; **PR-5 (private storage /
  signed URLs) is still pending** (a fresh child session was spun up for it after
  the original driving session hit `out_of_quota`). See §6.
- **Owner-gated, NOT done:** FG-20 ECS system-test; broader prod promotion of the
  still-`develop`-only FGs; live WhatsApp/email round-trip.

---

## 1. Where the code is

- **Repo:** `leolau/ai-prentice-4-all` (GitHub auto-redirects the old
  `leolau/hermes-agent`). Default working branch: **`develop`**.
- **Local checkout on the agent VM:** `/home/ubuntu/repos/hermes-agent`
  (directory name intentionally unchanged; the remote is repointed).
- **`develop` HEAD at hand-off:** `5d1af871b` — "Merge pull request #57 …
  multiuser-pr3-member-mgmt". (agent-home Wave A→C = PRs #43–#53; responsive
  desktop = #54; multi-user = #55/#56/#57.)
- **The prod box's agent-home clone** is a **separate** checkout at
  `/opt/data/agent-home-app` (a real git clone of `develop`, unlike the
  `/opt/data/hermes-agent` copied tree which has no `.git`). At hand-off it was
  deployed at commit `6fcfffa` (PR #54); redeploy to pull newer `develop`.

---

## 2. agent-home (FG-20) — what shipped

New app at repo root: **`agent-home/`** (sibling of `web/`; `web/` untouched).
Architecture is fixed (**D16**): **Next.js UI → Python AI layer (`/api/*`) →
Supabase (Postgres + Storage + RLS)**, BFF pattern — the Next server holds the
authenticated **C1 principal** context, proxies authority ops to the Python API,
and does server-side Supabase reads under the principal's RLS context. The
browser never gets a service-role key or bypasses C1/C2/C6/C8.

### Waves (all merged)
- **Wave A** (PR #43) — mobile-first Next.js 15 App-Router shell (bottom-nav,
  safe-area, 44px targets, PWA manifest + service worker + offline shell,
  `data-component` babel plugin) + the **auth/data seam**: C1 principal bridge
  (reuses `dashboard_auth`, signed HttpOnly `agent_home_session` cookie,
  principal via `/api/comms/whoami`), server-side Supabase context
  (`withPrincipalContext` sets `hermes.principal_*` GUCs + C3 `search_path` so
  Postgres FORCE'd RLS enforces C2), typed Python-API client (`HermesApiClient`),
  shared types, RLS-scoped Realtime stub. Deploy artifacts landed in #44/#45.
- **Wave B1** (PR #46) — read-only mobile **GTS Centre** at `/graph` over
  `GET /api/gts/graph` (C2 + FG-19 `item_grants` enforced upstream).
- **Wave B2** (PR #47) — read-only **Core-area view** at `/core` +
  **C8 interaction-trace timeline** at `/activity` (+ `/activity/[traceId]`).
- **Wave B3** (PR #48 tools-mode; B3 feature commit) — **onboarding** wizard +
  readiness at `/onboarding` + **tool registry** at `/tools` (read-only).
- **Wave C1** (PR #49 backend + #50 UI) — **one-brain chat** pane at `/chat`.
  Backend added `POST /api/sessions/{id}/chat` in `hermes_cli/web_server.py`
  (principal-scoped; extracted the shared agent builder into
  `gateway/session_chat.py` so the api_server adapter and the dashboard build the
  **identical** one-brain agent — cache-/alternation-safe, no synthetic user
  message, system prompt not rebuilt mid-conversation). Attachments upload
  **server-side** to principal-scoped Supabase Storage (`<user_id>/<session>/…`)
  via a server-only `SUPABASE_SERVICE_ROLE_KEY`; browser never holds a storage key.
- **Wave C2** (PR #51) — **agent-webview** console at `/webview` over the existing
  FG-17b `/api/webview/*` (Option-B session-scoped consent + escalation, C2 CDP
  profile isolation, C8 trace; consent decided server-side, never in the browser).
- **Wave C3** (PR #52, +#53 undo-409 UI) — comms **Inbox** at `/inbox`
  (FG-10 notifications + FG-12 change undo/redo) — the first interactive-write
  mobile surface; cross-surface parity met (Telegram-answered items dedupe).
- **Responsive desktop** (PR #54) — desktop/responsive polish on top.

### Routes present in `agent-home/src/app`
`/login`, `/` (seam proof: principal + one RLS-scoped read), `/graph`,
`/activity` (+ `/activity/[traceId]`), `/core`, `/onboarding`, `/tools`, `/chat`,
`/webview`, `/inbox`, plus BFF route handlers under `/api/{session,chat,webview,comms}/*`.

### FG-20 remaining (owner-gated) — in the FG-20 doc's Progress checklist
- [ ] tests (parity + mobile/PWA + negative-access RLS + C6 + cache-safety) green
- [ ] **System test on the ECS** — **owner-gated**

> Invariants respected across all waves: `web/` untouched, **zero** new core model
> tools, **zero** new non-secret `HERMES_*` env vars (only new env is the
> `SUPABASE_SERVICE_ROLE_KEY` **secret** + `AGENT_HOME_*` non-secret topology
> values, since a Node server can't read the Python `config.yaml`). Authority
> writes go through the Python API; TS never re-implements C1/C2/C6/C8 logic.

---

## 3. agent-home deployment (live) — how it's served

- **Subdomain:** `home.leolau.ai-and-i.io` → Cloudflare A record `47.83.199.25`
  (DNS-only/unproxied, matching the dashboard record). Caddy fetches the LE cert.
- **Caddy** (`/etc/caddy/Caddyfile`) has a second site block:
  ```caddy
  home.leolau.ai-and-i.io {
      encode zstd gzip
      reverse_proxy 127.0.0.1:3100
  }
  ```
  Do **not** split `/api/*` to `:9119` here — agent-home's own `/api/session/*`
  BFF routes must go to Next; the Python API stays private on loopback.
- **Port 3100** (not 3000 — `:3000` is the WhatsApp bridge).
- **systemd:** `agent-home.service` (enabled, `Restart=on-failure`), runs
  `next start` from `/opt/data/agent-home-app/agent-home`, secrets from a
  non-committed `agent-home/agent-home.env` (0600 — NOT `.env.production`, which
  Next's dotenv-expand would choke on). Verified **active (running)**, listening
  on `127.0.0.1:3100`, local `GET /` → 307 → `/login`, public HTTPS 200 on `/login`.
- **Deployed with `AGENT_HOME_DATASTORE_MODE=prod`** (reads `app_prod`).
- **Full runbook + unit + Caddy snippet:** `agent-home/deploy/`
  (`DEPLOY.md`, `agent-home.service`, `Caddyfile.agent-home`, `start.sh`).

### Redeploy agent-home (owner-gated)
```bash
APP_DIR=/opt/data/agent-home-app
cd "$APP_DIR" && git fetch origin develop && git checkout develop && git reset --hard origin/develop
npm ci && npm run build -w agent-home
systemctl restart agent-home.service
# verify: curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/   # -> 307
```

> ⚠️ **RLS caveat (from `deploy/DEPLOY.md`):** the Supabase Postgres role
> agent-home connects as (`postgres`, via `DATABASE_URL`) has **BYPASSRLS**, so
> per-principal RLS is **not** enforced for it. Safe while the **owner is the only
> login** (owner sees all rows anyway), but **provision a dedicated
> non-BYPASSRLS role before onboarding other users**. (The multi-user PR-1 work
> in §6 begins addressing least-privilege DB roles — reconcile these.)

---

## 4. Reaching the box (no SSH key on file)

Use the `aliyun` CLI → ECS RunCommand (Cloud Assistant). Creds are in the agent
VM env (`ALIBABA_CLOUD_ACCESS_KEY_ID` / `_SECRET`). Helper on the agent VM:
```bash
bash /home/ubuntu/runbox.sh i-j6c81aisv2dd8mg17yle <script-file>
```
The `alibabacloud` MCP server is down — use the `aliyun` CLI path. Prod box:
`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, `47.83.199.25`, cn-hongkong, 4 vCPU /
16 GB, `/opt/data`. (Full infra/DNS/TLS/gateway facts:
`SESSION-HANDOFF-2026-07-prod-cutover.md`.)

Operational helper scripts used this session live on the agent VM under
`/home/ubuntu/ecs-scripts/` and `/home/ubuntu/runbox.sh` — **not** in the repo;
recreate/commit if needed in future sessions.

---

## 5. Telegram / auth quick facts

- **Bot the owner talks to:** `@ai_prentice_systest_01_bot` — always-on
  `hermes-gateway.service` on the prod box; owner Telegram ID `8756039695`
  allowlisted (`telegram.allow_from`), everyone else denied; model DeepSeek.
- **agent-home / dashboard login:** username `admin` + the **rotated** password
  (scrypt `password_hash` in the box `config.yaml` under `dashboard.basic_auth`;
  the token-signing `secret` was rotated too, invalidating old sessions).
  Config backup: `/opt/data/hermes-home-staging/config.yaml.bak-20260721-235219`.
- **PWA install:** open `https://home.leolau.ai-and-i.io` → iOS Safari
  Share → *Add to Home Screen*; Android Chrome ⋮ → *Install app*.

---

## 6. NEW frontier — multi-user workstream (PR-1..PR-5, #55–#58 + pending PR-5; NOT FG-20)

A separate **PR-1..PR-5** multi-user sequence moves toward true multi-user
(beyond the single-owner deployment). Confirm its own driving session/plan before
extending it:
- **PR-1 (#55) — security foundation:** login→own-principal identity binding +
  idempotent non-`BYPASSRLS` `hermes_app` DB role (`hermes owner db-role` /
  `hermes owner alias`). Unenrolled subject fails closed (409), never upgraded to owner.
- **PR-2 (#56) — Supabase/GoTrue email+password dashboard-auth provider** (a step
  toward the D4 GoTrue direction — note FG-20's C1 bridge deliberately deferred
  browser-direct GoTrue; reconcile the two auth paths).
- **PR-3 (#57) — member management:** GoTrue admin backend + `hermes member` CLI.
- **PR-4 (#58) — agent-home Members UI:** owner/admin `/members` screen + BFF
  routes (double-guarded: BFF UX gate + Python authority 403).
- **PR-5 — private storage / signed URLs (PENDING):** per-principal Supabase
  Storage isolation so users fetch only their own objects via short-lived signed
  URLs instead of relying on the shared service-role key. A fresh child session
  was created for this (the original driving session `a7a37d33…` is suspended
  `out_of_quota`).

**Owner-gated multi-user deploy steps still outstanding** (from PR-4/PR-1 notes):
create the owner Supabase alias, set `dashboard.supabase_auth` config,
`GOTRUE_DISABLE_SIGNUP=true`, and a maintenance-window switch of the serving DB
role to `hermes_app`. This intersects the §3 BYPASSRLS caveat (a non-BYPASSRLS
per-principal DB role is prerequisite to real multi-user RLS enforcement).

---

## 7. Open follow-ups (owner-gated)

1. **FG-20 ECS system-test** on `hermes-systest` (staging/`app_dev`, never
   `app_prod`) — parity + mobile/PWA + negative-access RLS + C6 + cache-safety.
2. **Provision a dedicated non-BYPASSRLS Supabase role** for agent-home before
   onboarding non-owner users (see §3 caveat; ties into the §6 multi-user work).
3. **Broader prod promotion:** only 10 FGs were promoted in the cutover
   (03/04/05/08/11/12/15/16/17/18); still `develop`-only for prod:
   **FG-01, 06, 07, 09, 10, 13, 14, 19** — remaining work is system-test +
   promotion, not new code.
4. **Live WhatsApp/email round-trip** + auto-reply/SMTP — pending channel creds.
5. Decide keep/decommission the stopped old box (`8.217.86.90`, kept for rollback).
6. **FG-02 blockchain** stays ON HOLD unless the owner resumes it.
7. Reconcile agent-home's C1 bridge auth with the new GoTrue email/password
   provider (§6) so there's one coherent auth story.

---

## 8. Constraints to respect (from `AGENTS.md` / master plan)

- Prompt-cache safety: system prompt byte-stable within a conversation; strict
  role alternation; never inject a synthetic user message mid-loop.
- The core is a narrow waist — no new core model tools without clearing the
  footprint ladder; `.env` = secrets only, behavioral config in `config.yaml`.
- Core is immutable to the runtime agent (C7, fail-closed); every meaningful
  interaction traced end-to-end (C8 `trace_id`) — traces are side-channel, never
  injected into prompts.
- Never weaken access control / RLS or Core write-protection to make something
  work. Real-path E2E (incl. negative-access RLS) for security/datastore/network
  changes; assert invariants, not snapshots. Preserve contributor authorship.
- `web/` stays the operator console; `agent-home/` is the user/mobile face; both
  hit the same Python API + Supabase. `agent-home` is Core under C7 — the runtime
  agent cannot modify it.
- Request GitHub user `leolau` as reviewer on every PR.
