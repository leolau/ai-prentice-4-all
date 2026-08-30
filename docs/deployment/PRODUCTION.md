# Production Environment — Hermes Stack on Hetzner

> **Audience**: any agent/operator maintaining this system.
> Migrated from Alibaba Cloud (cn-hongkong) on **2026-08-20**. Full migration history: `hetzner-migration-runbook.md`.
> **No secrets are stored in this file.** See "Credentials & Access" for where they live.

---

## 1. System Overview

| | |
|---|---|
| **Provider** | Hetzner Cloud, project `snappop` |
| **Server** | `hermes` (ID `162808556`), type **CX43** — 8 vCPU / 16 GB RAM / 160 GB NVMe |
| **Location** | Nuremberg, Germany (`nbg1`) |
| **OS** | Ubuntu 24.04 LTS (x86_64), UTC, 4 GB swap |
| **IPv4** | `188.245.219.105` |
| **IPv6** | `2a01:4f8:1c1a:edd4::/64` |
| **Firewall** | Hetzner firewall `hermes-fw`: inbound TCP 22, 443, 9119 only |
| **Cost** | €15.99/mo (~$19) + ~€0.60 IPv4 — hourly-billed, flat monthly cap |

**What runs here**: the complete Hermes production stack — Supabase (self-hosted), Hermes agent services, WhatsApp bridges, email/calendar automation, dashboard, agent-home PWA.

## 2. Credentials & Access (locations only — never commit values)

| Credential | Location | Notes |
|---|---|---|
| Hetzner API token | `~/.config/hcloud/cli.toml` (context `snappop`, perms 600) | Used by `hcloud` CLI; Read & Write scope |
| Server SSH key | private: `~/.ssh/hetzner_hermes_ed25519` | user `root`; public uploaded as `hermes-migration` in Hetzner |
| Cloudflare | zone `ai-and-i.io` | Owner-managed via dashboard; agents do not hold CF credentials |

SSH: `ssh -i ~/.ssh/hetzner_hermes_ed25519 root@188.245.219.105`

## 3. DNS & TLS

- **Zone**: `ai-and-i.io` on **Cloudflare** (NS: patrick/zoe.ns.cloudflare.com)
- **Records**: both **DNS-only (grey cloud)** — REQUIRED on the free Cloudflare plan:
  - `leolau` A → 188.245.219.105 → dashboard
  - `home.leolau` A → 188.245.219.105 → agent-home
  - Reason: Cloudflare Universal SSL covers only apex + `*.ai-and-i.io` (one level). Multi-level hostnames like `home.leolau` get NO edge cert when proxied → TLS handshake failure at the edge. With grey records, Caddy terminates TLS directly using its Let's Encrypt certs (which cover multi-level hostnames).
  - If proxying is ever desired: order an Advanced Certificate ($10/mo) that lists the multi-level hostname.
- **TLS**: Caddy auto-manages Let's Encrypt certs for both hostnames (auto-renew). Certs issued 2026-08-20.
- If certs ever fail: check `journalctl -u caddy`; renewals use HTTP-01.

## 4. Service Catalog

### 4.1 Supabase stack (Docker Compose)

- Compose dir: `/opt/data/supabase/docker` (`.env` + `docker-compose.yml` live there)
- 11 services: `db` (supabase/postgres 17.6.1.136), kong, auth (gotrue), rest (postgrest), realtime, storage-api, studio, meta (postgres-meta), imgproxy, supavisor (pooler), edge-functions
- Data: `volumes/db/data` — **single `postgres` DB holding all schemas** (`app_prod`, `app_prod_maintenance`, `app_dev`, `auth`, `storage`, `realtime`, …). NOTE: `app_prod` is a **schema**, not a separate database.
- Manage: `cd /opt/data/supabase/docker && docker compose ps|up -d|pull`
- Health: all 11 must show `healthy`

### 4.2 systemd services (Hermes)

All units in `/etc/systemd/system/`, run as user `hermes` (uid 996 / gid 986 — recreate identically if ever lost).

| Unit | Role | Port/Notes |
|---|---|---|
| `hermes-dashboard` | Hermes dashboard web UI | 0.0.0.0:9119 (proxied by caddy on `leolau.ai-and-i.io`) |
| `agent-home` | FG-20 mobile PWA (Next.js) | 127.0.0.1:3100 (caddy → `home.leolau.ai-and-i.io`); requires built `.next` |
| `hermes-embed` | Local embedding server (bge-m3, torch-cpu) | 127.0.0.1:8791; models in `/opt/data/hermes-embed/models` (6.4 GB) |
| `hermes-gateway` | Messaging gateway (start-gateway.sh) | |
| `hermes-wa-bridge-connectar` | WhatsApp bridge (Connectar) | 127.0.0.1:3001; session: `/opt/data/hermes-home-staging/whatsapp/session-connectar` |
| `hermes-wa-bridge-personal` | WhatsApp bridge (personal) | 127.0.0.1:3000; session: `…/session-personal` |
| `hermes-email-poller` / `-batcher` / `-triage` | Email IMAP automation | shares sqlite `credits.db` (occasional lock warnings) |
| `hermes-calendar-poller` / `-triage` | Google Calendar automation | |
| `hermes-escalation`, `hermes-digest` | Escalation & digest jobs | |
| `hermes-drift-check`, `hermes-memory-projection`, `hermes-review-pass`, `hermes-secret-backup` | Aux jobs (installed, not active on source) | |
| `caddy` | Reverse proxy + TLS | 80/443; config `/etc/caddy/Caddyfile` |

**Caddy + SSE trap**: `encode zstd gzip` compresses `text/*`, which includes
`text/event-stream` — gzip then holds every small event frame until the
stream ends, so chat streaming arrives as one final burst. The Caddyfile
therefore routes the SSE paths (`/api/chat/stream`, `/api/chat/attach` on
`home.`, `/api/sessions/*/chat/stream*` on the dashboard host) through a
separate `handle` block with **no** `encode` and `flush_interval -1`. If you
add a new SSE endpoint, add its path to the `@sse` matcher or it will look
"frozen until the answer lands". Reload with `caddy validate` +
`caddy reload` (admin API on :2019, no root needed).

**Side-effect rule**: pollers/bridges/batchers/escalation/digest act on the outside world. **Never run them simultaneously on more than one host** (double-processing + WA session fights).

### 4.3 Logs

- systemd: `journalctl -u <unit>`
- WA bridges: `/var/log/hermes-wa-bridge-connectar.log`, `/var/log/hermes-wa-bridge-personal.log`
- Other hermes services: `/var/log/hermes-*.log` (see each unit's StandardOutput)
- Caddy: `journalctl -u caddy`

## 5. Deployment Procedures

### 5.1 Full code deploy — two ways of running it

The reviewed deploy tool is `/opt/data/deploy-hermes.sh` (source: `deploy/hermes-deploy.sh`).
What it does: check for local modifications → fetch and fast-forward `origin/develop`
(3 retries) → remove files the new revision deleted → `pip install -e .` → rebuild
dashboard/agent-home only when their sources moved → fix ownership → restart all
enabled `hermes-*` services + `agent-home` → verify `active` → print `deploy OK (<sha>)`.
Full step-by-step: `README.md` § "Deploying a code change" (written for the old
Alibaba box; its `aliyun ecs RunCommand` transport no longer applies here).

**Shared prerequisites (both ways):**

- Code reaches the box only via git: `develop` requires a PR — branch → PR → merge,
  never a direct push.
- The script needs **root** (systemd restarts, `chown` of the checkout/`.venv`).
- The deploy restarts all 15 services — check that no gateway conversation is
  mid-turn before triggering it (the pollers/bridges pause for the restart window;
  this box is the only host, so the side-effect rule in §4.2 is satisfied by
  restarting in place).
- The deploy tool does not deploy itself: a merged change to `deploy/hermes-deploy.sh`
  is inert until someone copies it onto the box (`install -m 755 deploy/hermes-deploy.sh /opt/data/deploy-hermes.sh`). The script reports `DEPLOY TOOL STALE` when they differ.

**Way A — remote: SSH in from another machine and run it there.**

```bash
ssh -i ~/.ssh/hetzner_hermes_ed25519 root@188.245.219.105 \
  '/opt/data/deploy-hermes.sh develop'
```

Output is live; no invocation id, no polling, nothing to decode. (This replaces the
old `aliyun ecs RunCommand` + `DescribeInvocationResults` + base64 flow, which existed
only because the previous box had no SSH.)

**Way B — local: run it on-box from a colocated session.**

Agent sessions now run on this box itself (e.g. under `/opt/data/aicoding/`). From
there the remote machinery is pure overhead — run the script directly. If the
session's user has root:

```bash
/opt/data/deploy-hermes.sh develop
```

If it does not (e.g. an unprivileged service account without sudo), the session
still cannot execute the deploy — route through Way A or another root path. Being
on the box changes *how the command is carried*, not who may run it.

Either way, success ends with `deploy OK (<sha>)` and all services `active`; verify
independently with the §5.7 health check.

### 5.2 Dashboard update
```bash
# code lives at /opt/data/hermes-agent (Python, uv-managed)
cd /opt/data/hermes-agent && git pull   # or deploy via the hermes tooling
sudo systemctl restart hermes-dashboard  # unit runs with --skip-build (uses prebuilt assets)
```

### 5.3 agent-home (Next.js) update
```bash
cd /opt/data/hermes-agent/agent-home
npm install --no-audit --no-fund      # root workspace deps hoisted from /opt/data/hermes-agent
npm run build                          # produces .next (REQUIRED before start)
sudo systemctl restart agent-home
```
Build tools note: `build-essential` + `python3-dev` are required (node-pty native module).

### 5.4 Python dependency changes
```bash
cd /opt/data/hermes-agent && uv sync            # lockfile deps
uv pip install --python .venv/bin/python <pkg>  # manual extras (source venv historically had ~60 beyond the lock)
```
Embed service venv: `/opt/data/hermes-embed/venv` (torch-cpu, sentence-transformers, fastapi, uvicorn).

### 5.5 WA bridge update
```bash
cd /opt/data/hermes-agent/scripts/whatsapp-bridge && npm install
sudo systemctl restart hermes-wa-bridge-connectar hermes-wa-bridge-personal
# check logs: tail -f /var/log/hermes-wa-bridge-connectar.log — expect "✅ WhatsApp connected!"
```

### 5.6 Supabase upgrade
```bash
cd /opt/data/supabase/docker
docker compose pull && docker compose up -d
# ALWAYS back up first: docker exec supabase-db pg_dumpall -U supabase_admin > /root/backup-$(date +%F).sql
```
⚠️ **Schema-version caution**: the supabase/postgres image's entrypoint initializes a schema version that may be NEWER than what app code expects (observed: fresh init lacked `auth.users.is_sso_user`). Prefer in-place upgrades of the running data dir; never restore a dump into a freshly-initialized cluster without schema verification.

### 5.7 Full-stack health check
```bash
docker ps --format '{{.Names}}: {{.Status}}' | grep -c healthy   # expect 11
systemctl --failed                                               # expect empty
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9119/    # 302
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/    # 200
curl -s -m 30 -X POST http://127.0.0.1:8791/embed -H 'Content-Type: application/json' -d '{"texts":["ok"]}' | head -c 60
```

## 6. Backup & Disaster Recovery

- **Hetzner snapshots**: not yet scheduled — set up a weekly automated snapshot via `hcloud` or console (retention ~2).
- **DB backups**: no recurring dump yet — schedule `pg_dumpall` (or per-schema `pg_dump` of `app_prod*` + `auth`) to off-box storage.
- **WA sessions**: `/opt/data/hermes-home-staging/whatsapp/` — include in backups; they are login state (loss = QR re-link).
- **Historical safety net**: Alibaba pre-migration snapshots (source of the 2026-08-20 migration):
  - system disk: `s-j6celbxttva3ab4eovpp`, data disk: `s-j6caaw39ye8gab5d3s6e` (region cn-hongkong, account 5756074612064497). Retain until migration is proven stable; then safe to delete.
- **Rollback path** (while Alibaba subscription alive): re-snapshot exports available via Alibaba Cloud Assistant; runbook Phase 6 documents the reverse flow.

## 7. Known Behaviors & Gotchas

1. **Dashboard port 9119 resets direct external HTTP connections** — by design (same as old host). Access only via `https://leolau.ai-and-i.io` through Caddy.
2. **Cloudflare records must stay DNS-only (grey)** on the free plan: Universal SSL cannot cover the multi-level `home.leolau` hostname when proxied (edge TLS handshake failure observed 2026-08-20/21). Caddy terminates TLS at the origin instead. (Separately, one client network was observed RST-injecting direct TLS to the origin IP — that is client-path specific, not a server problem; global access verified fine.)
3. **sqlite lock warnings** in email poller (`database is locked` on shared credits.db) — transient; investigate only if persistent.
4. **hermes venv drift**: `uv.lock` alone is not the full dependency set (manual installs exist). For reproducible rebuilds use the pip-freeze approach (see runbook Phase 5 fix #2).
5. **Embed service** binds a single port 8791 (source previously ran multi-worker 7900-7903 layout); adjust callers if needed.
6. Hetzner `hcloud` CLI quirks: no `--dry-run`; firewall rules via `firewall add-rule` (not `--rule`); `context create` needs a TTY — write `~/.config/hcloud/cli.toml` directly in automation.
7. **Delta-apply TRUNCATE CASCADE ordering bug (fixed 2026-08-21)**: the cutover delta script applied `TRUNCATE x CASCADE; COPY x` per table alphabetically, so a later `TRUNCATE principals CASCADE` re-wiped already-copied child tables. 5 tables lost rows: `principal_aliases`, `channel_identities`, `goal_progress`, `profile_suggestion_audit`, `rag_chunks` — symptom was agent-home login 409 "no principal enrolled" (basic-auth subject `admin` unmapped). Rows re-inserted from `/tmp/app-delta.sql`. Lesson: when re-applying dumps, disable FK cascades or order parents-first; verify per-table row counts against the dump after any restore.

## 8. Cost & Account Notes

- Hetzner billing: hourly with monthly cap; invoice email on the account owner's address.
- If resizing ever needed: CX53 (16 vCPU/32 GB, €29.49) is the next step up; ARM (CAX) types were out of stock EU-wide as of 2026-08-20.
- Decommissioned: Alibaba `hermes-systest` (ecs.e-c1m4.xlarge, $103.74/mo) — stop/stop-renew on 2026-08-26 expiry; EIP `47.83.199.25` releasable after confirming nothing still references it.
