# Migration Runbook: hermes-systest → Hetzner CX43 (x86, Nuremberg)

**Source:** Alibaba ECS `hermes-systest` (`i-j6c81aisv2dd8mg17yle`), ecs.e-c1m4.xlarge (4 vCPU / 16 GB), Ubuntu 24.04, cn-hongkong, EIP `47.83.199.25`, subscription $103.74/mo (expires 2026-08-26)
**Target:** Hetzner Cloud **CX43** (8 vCPU Intel shared / 16 GB / 160 GB), Nuremberg (nbg1), **€15.99/mo (~$19)** + ~€0.60 IPv4
  *(originally planned CAX31 ARM — out of stock in all EU locations at provisioning time 2026-08-20; CX43 is x86, which also simplifies the migration — no architecture rebuilds)*
**Provisioned (2026-08-20):** server ID `162808556`, name `hermes`, IPv4 **188.245.219.105**, IPv6 `2a01:4f8:1c1a:edd4::/64`, firewall `hermes-fw` (TCP 22/443/9119 inbound), SSH key `hermes-migration` (private key: `~/.ssh/hetzner_hermes_ed25519`)
**Savings (Track A):** ~$85/mo (~82%)

**Architecture decision (final): dual-track split**
- **Track A (Phases 0-7 below):** Hermes stack → Hetzner **CX43 x86 (Nuremberg)**, 24/7, €15.99/mo (~$19)
- **Track B (Track B section):** Unity Editor (Windows + real-time 3D) → **Vast.ai RTX 4090 GPU**, hourly, weekly use
- Rationale: Unity Editor is x86-only with no ARM build, Windows is the best-supported platform, and real-time 3D needs a GPU — none of which fit the Hermes box. Splitting avoids paying 24/7 rates for a once-a-week workload.

---

## Phase 0 — Prerequisites & Safety Net

- [ ] **Hetzner account**: sign up (ID verification may be required for new accounts), add billing, create project
- [ ] **Generate Hetzner API token** (Read & Write) if managing via `hcloud` CLI / MCP
- [ ] **Timing decision**: Alibaba subscription expires **2026-08-26**. Recommended: **renew 1 month** ($103.74) to allow parallel validation without deadline pressure; cancel the *following* renewal
- [x] **Take Alibaba snapshots of BOTH disks** — DONE 2026-08-20: system `s-j6celbxttva3ab4eovpp` (100 GB, accomplished), data `s-j6caaw39ye8gab5d3s6e` (40 GB, accomplished)
- [ ] **Record the current environment inventory** (done in audit — keep this list updated):

| Category | Items |
|---|---|
| systemd services | agent-home, caddy, hermes-dashboard, hermes-gateway, hermes-embed, hermes-escalation, hermes-digest, hermes-email-poller/batcher/triage, hermes-calendar-poller/triage, hermes-wa-batcher, hermes-wa-triage, hermes-wa-bridge-connectar, hermes-wa-bridge-personal |
| Docker (Supabase) | postgres+pgvector, gotrue, realtime, storage-api, studio, supavisor, postgres-meta, edge-runtime, postgrest, kong, imgproxy |
| Data tree | `/opt/data` (hermes-agent, hermes-embed, hermes-home-staging WA sessions, agent-home) |
| Binaries to replace with arm64 | `/usr/bin/caddy`, `/usr/local/bin/supabase`, `/usr/local/bin/uv` |
| Listening ports | 22, 443, 2019 (caddy admin), 3000/3001 (node WA bridges), 3100 (next-server), 5432/6543/8000/8443 (docker), 7900/7901/7903/8791 (hermes-embed py), 9119 (hermes dashboard) |

⚠️ **Identify now**: list every external system that calls INTO this box (webhook URLs containing `47.83.199.25` or its DNS name): Connectar WA bridge callbacks, calendar/email provider webhooks, any clients hitting the dashboard/gateway via caddy. Each needs an endpoint update at cutover.

---

## Phase 1 — Provision Hetzner Target ✅ DONE 2026-08-20

Provisioned as **CX43 in nbg1** (see header for details). Original commands kept for reference:

- [x] Server created: **CX43**, Ubuntu 24.04, nbg1, SSH key attached
  ```bash
  hcloud server create --name hermes --type cx43 --image ubuntu-24.04 \
    --location nbg1 --ssh-key hermes-migration --firewall hermes-fw
  ```
- [ ] Create firewall **before** exposing anything:
  - Inbound: TCP 22 (restrict to your IP ideally), TCP 443, TCP 9119 (dashboard — restrict to your IP)
  - Deny everything else (Docker ports 5432/8000/8443/6543 stay internal)
- [ ] Note the new public IPv4/IPv6

## Phase 2 — Base System Setup (target) ✅ DONE 2026-08-20

- [x] `apt update && apt full-upgrade`, installed: docker.io 29.1.3, docker-compose-v2 2.40.3, fail2ban, chrony, rsync, curl, git, unattended-upgrades (all enabled)
- [x] **4 GB swap** configured + persisted in fstab
  ```bash
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ```
- [x] Toolchain installed (x86_64 — same arch as source, binaries are directly portable):
  - `uv` 0.12.5 (symlinked to /usr/local/bin)
  - Node 22.23.2 (nodesource) + npm 10.9.8
  - Caddy v2.11.4 amd64
  - Supabase CLI 2.115.0
- [x] Directory layout created: `/opt/data`, `/opt/uv`; timezone set to UTC; Docker sanity-checked (hello-world)

## Phase 3 — Export Data from Alibaba (source) ✅ DONE 2026-08-20

Executed via Cloud Assistant; all md5-verified on target; throwaway key used and destroyed on both sides. Source staging files cleaned up afterwards.

**What moved** (`/root/migration/` on target):
- `migration-pgdump.sql` — 41 MB, `pg_dumpall` from container `supabase-db` (no write-freeze; Phase 6 delta dump covers skew)
- `migration-optdata.tar.gz` — 11 GB: `/opt/data` excluding docker data-root (12 GB, rebuilt fresh), node_modules/venvs/.next (rebuilt), retired dirs, supabase repo .git/node_modules
- `migration-configs.tar.gz` — Caddy configs, hermes-*/agent-home systemd units, .env/docker-compose file inventory

Notable source findings: `/opt/data` = 36 GB total; hermes-embed/models = 6.4 GB (bge-m3 embedding weights — transferred); hermes-user = 5 GB; backups = 1.3 GB.

> Reference commands (as executed):

- [ ] **Freeze writes briefly** for a consistent Postgres dump (or accept minor skew):
  ```bash
  docker exec <supabase-db-container> pg_dumpall -U supabase_admin > /tmp/hermes-pgdump.sql
  # or per-database pg_dump for finer restore control
  ```
- [ ] **Snapshot the data tree** (includes WA session files — critical, they are login state):
  ```bash
  tar czf /tmp/opt-data.tar.gz -C / opt/data
  ```
- [ ] **Export configs**:
  ```bash
  tar czf /tmp/hermes-configs.tar.gz \
    /etc/caddy /etc/systemd/system/hermes-*.service /etc/systemd/system/agent-home.service \
    $(find /opt/data -maxdepth 3 -name '.env' -o -name 'docker-compose*.yml' -o -name 'Caddyfile')
  ```
- [ ] Transfer to target: `rsync -avP --partial /tmp/{hermes-pgdump.sql,opt-data.tar.gz,hermes-configs.tar.gz} root@<hetzner-ip>:/root/migration/`
  (Single files — avoid streaming secrets over untrusted channels; rsync over SSH is fine)

## Phase 4 — Restore on Target ✅ DONE 2026-08-20

- [x] `/opt/data` extracted (17 GB, permissions preserved); configs at `/root/migration/migration-configs/`
- [x] **Supabase stack**: 11/11 containers **healthy**. DB data directory migrated intact (`volumes/db/data`, 207 MB) — used directly, no dump restore needed (pg_dump kept as fallback at `/root/migration/`)
  - Verified: `app_prod` 44 tables, `auth`/`storage`/`realtime` schemas present
- [x] Images pulled via `docker compose pull` (same arch — no manifest checks needed)
- [x] **Python venvs rebuilt**:
  - `hermes-agent`: `uv sync` + source pip-freeze top-up (~60 manual extras missing from uv.lock, e.g. asyncpg, aiohttp, google/telegram SDKs) — `hermes --version` verified working
  - `hermes-embed`: fresh venv, torch 2.13.0+cpu + sentence-transformers 5.7.0 (models already migrated)
- [x] **node_modules reinstalled**: hermes-agent workspace (agent-home/web/ui-tui), whatsapp-bridge, photon sidecar — both live and `_deploy_develop` staging trees
- [x] **19 systemd units installed, all disabled** (incl. 4 extras not previously running: drift-check, memory-projection, review-pass, secret-backup)
- [x] Side-effect services (pollers/bridges/batchers) remain STOPPED — per cutover order
- Disk after restore: 45 GB used / 100 GB free

- [ ] Extract `/opt/data` (preserve ownership/permissions: `tar xzpf`)
- [ ] **Supabase stack**: restore docker-compose files + `.env`, pin image tags to current versions, `docker compose pull` (pulls arm64 variants automatically) → restore DB:
  ```bash
  docker compose up -d db  # wait healthy
  docker exec -i <db> psql -U supabase_admin < /root/migration/hermes-pgdump.sql
  docker compose up -d
  ```
  ✅ Target is x86_64 — same architecture as source; all current image tags work as-is, no manifest checks needed.
- [ ] **Rebuild Python venvs** with uv (fresh env on new OS; architecture matches source)
  - `/opt/data/hermes-agent/.venv`, `/opt/data/hermes-embed/venv`, any others found
- [ ] **Reinstall node_modules** (delete + reinstall for a clean tree on the new OS):
  - `hermes-agent/agent-home`, `hermes-agent/web`, WA bridge dirs
- [ ] Install systemd units from exported configs; `systemctl daemon-reload`
- [ ] **DO NOT start the pollers/bridges yet** (see Phase 5 cutover order)

## Phase 5 — Parallel Validation (old box still PRIMARY) ✅ DONE 2026-08-20

**Issues found & fixed during validation:**
1. Missing `hermes` system user (uid 996/gid 986) → recreated identical to source
2. hermes-agent venv had ~60 packages outside uv.lock (asyncpg, aiohttp, google/telegram SDKs…) → installed exact source pip-freeze set
3. Root workspace npm install failed on `node-pty` native build → installed build-essential + python3-dev, reinstall succeeded (1423 packages)
4. agent-home `.next` build missing (excluded from tarball) → `npm run build` ✓ Compiled successfully

**Validation results:**
- Supabase: 11/11 healthy (from Phase 4)
- hermes-dashboard (9119): active, HTTP 302 ✓
- agent-home / next-server (3100): active, HTTP 200 ✓
- hermes-embed: active on 127.0.0.1:8791, bge-m3 dim=1024 loaded, live embedding test ✓ (note: single port 8791, not the multi-worker 7900-7903 layout seen on source)
- **All 12 side-effect services confirmed inactive** (pollers, bridges, batchers, escalation, digest, gateway)
- Zero error-level journal entries in validation window
- Caddy: config validated and staged at /etc/caddy/Caddyfile — **deferred start until DNS cutover** (domains leolau.ai-and-i.io, home.leolau.ai-and-i.io still point at old IP; TLS issuance would fail)

**Original validation checklist (kept for reference):**

Run target in "dry" mode — everything except externally-acting services:

- [ ] Start on target: Supabase stack, hermes-dashboard (9119), hermes-embed, next-server, agent-home, caddy
- [ ] **Keep STOPPED on target**: hermes-email-poller/batcher/triage, hermes-calendar-poller/triage, hermes-wa-bridge-*, hermes-wa-batcher/triage, hermes-escalation, hermes-digest
- [ ] Validate:
  - [ ] All Supabase containers healthy; dashboard loads via `https://<new-ip>` (hosts-file override locally)
  - [ ] hermes dashboard reachable on 9119, data reads correctly from restored Postgres
  - [ ] No arm64 crash loops: `journalctl -u 'hermes-*' --since -10m` clean
  - [ ] Spot-check one embed API call (7900-7903)

> **Why the split**: pollers/bridges/batchers have side effects (fetching email, sending WA messages, escalation). Running them on BOTH boxes would double-process messages and fight over WA sessions (one active session per number).

## Phase 6 — Cutover (planned window, ~30 min) — EXECUTED 2026-08-20 ~16:20 HKT

**Completed steps:**
1. ✅ All 12 side-effect services stopped + disabled on source
2. ✅ Delta `pg_dumpall` taken (42 MB) + fresh WA session dirs (182 MB) transferred via throwaway key (md5-verified, key destroyed)
3. ✅ **DB restore (learnings)**: full dumpall into fresh cluster broke `auth.users` — fresh entrypoint init created a NEWER auth schema (dropped `is_sso_user`) than source. Correct approach used: restore source's exact data directory from Phase-3 tarball, then apply app-schema delta only (TRUNCATE+COPY of all 76 `app_*` tables). Result: auth.users=11 ✓, approvals=80 ✓ (parity with source)
4. ✅ Supabase 11/11 healthy; all side-effect services started on target — **both WA bridges reconnected using migrated sessions, no QR re-link needed**
5. ✅ Caddy installed as systemd service (user caddy, CAP_NET_BIND_SERVICE), listening 80/443 — awaiting DNS flip for TLS issuance
6. ✅ Smoke: dashboard 302, agent-home 200, embed live vectors, calendar poller syncing, zero crash loops
7. ✅ Old-IP config scan: `47.83.199.25` appears only in historical session dumps — no live webhook/config references (domain-based)

**Known behaviors (same as source, not regressions):**
- Dashboard port 9119 resets external HTTP connections by design (internal-only; served via caddy on domain)
- One transient `sqlite3 database is locked` in email poller (shared credits.db) — monitor

**DNS flipped 2026-08-20 ~16:55 HKT; Caddy obtained Let's Encrypt production certs for both domains.**

**Post-flip issue found & fix**: with records proxied (orange), `home.leolau` failed TLS at Cloudflare's edge — free-plan Universal SSL (`*.ai-and-i.io`) cannot cover the two-level hostname, and no dedicated cert is issued on free plans. **Final config: both records DNS-only (grey); Caddy terminates TLS with Let's Encrypt certs** (verified 2026-08-21: leolau 302, home.leolau 200 from 3 external monitors each). Separately, one client network (agent's test path) RST-injects direct TLS to the origin — client-path specific, global access fine.

**Original cutover procedure (reference):**

Order matters — old side stops BEFORE new side starts acting:

1. [ ] Announce/freeze window; stop side-effect services on **source**:
   ```bash
   systemctl stop hermes-email-poller hermes-email-batcher hermes-email-triage \
     hermes-calendar-poller hermes-calendar-triage hermes-wa-bridge-connectar \
     hermes-wa-bridge-personal hermes-wa-batcher hermes-wa-triage \
     hermes-escalation hermes-digest
   ```
2. [ ] **Final delta dump** of Postgres (catch writes since Phase 3) → restore on target
3. [ ] Start side-effect services on **target**; verify WA bridges reconnect using moved session files (QR re-link may be needed if sessions are machine-bound — test in Phase 5 if possible by copying sessions to a throwaway env first)
4. [ ] Update external endpoints: DNS records and/or webhook URLs → new IP/domain (Connectar callback, calendar/email webhooks, client URLs)
5. [ ] Smoke test end-to-end: send a test WA message, test email ingestion, check dashboard

## Phase 7 — Monitoring & Decommission

- [ ] Monitor 3-7 days: `journalctl` errors, message flow, Supabase health, disk usage
- [ ] Keep source box **stopped but alive** during this window (stopped = no PAYG extras; subscription already paid)
- [ ] Once stable:
  - [ ] Take final Alibaba snapshots (retain ~1 month)
  - [ ] **Do NOT renew** the subscription due 2026-08-26 (or release after confirming backups)
  - [ ] Release EIP `47.83.199.25` only after all external references are updated
- [ ] Update any monitoring/backup jobs to target; set up Hetzner snapshots schedule (automated, ~€0.05/GB/mo)

## Rollback Plan

If target fails at any point before decommission:
1. Stop side-effect services on target
2. Restart them on source (data there is stale by the delta since cutover — re-export anything written to target DB if needed)
3. Revert DNS/webhooks to `47.83.199.25`
Worst case after decommission: rebuild from retained Alibaba snapshots + pg_dump.

## Known Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Double-processing of email/WA/calendar during overlap | Strict stop-before-start order in Phase 6; side-effect services never run on both |
| WA session files machine-bound | Test session portability on throwaway env before cutover; be ready to QR re-link |
| ~~Missing arm64 Docker image~~ | Not applicable — target is x86_64, same as source |
| Native deps needing rebuild | Same-arch reinstall; budget time; check `journalctl` for import errors |
| External webhooks still pointing to old IP | Phase 0 inventory list; grep configs for `47.83.199.25` on target after restore |
| EU latency vs HK | Acceptable for API-polling workload; revisit if user-facing latency matters |
| Timezone/cron drift | Verify `timedatectl` (UTC) and all systemd timers/crontabs migrated |

## Track B — Unity Editor Workstation (Vast.ai, On-Demand)

**Why separate from Track A:** Unity Editor has no ARM build, Windows is the best-supported platform, real-time 3D needs a GPU, and usage is ~1 day/week — an hourly GPU rental beats any 24/7 VM on both cost and capability.

**Target spec:** RTX 4090 (median ~$0.35/hr on Vast.ai; RTX 3090 ~$0.15/hr as budget fallback), Windows template, ≥32 GB system RAM, ≥150 GB disk.
**Expected cost:** ~35 active hours/month ≈ **$12/mo compute + a few dollars storage while stopped**.

### B-1 — Account & first provisioning

- [ ] Create Vast.ai account, add ~$20 credit
- [ ] Search instances: GPU RTX 4090; filters — reliability ≥ 95% (prefer verified datacenter hosts), disk ≥ 150 GB, RAM ≥ 32 GB
- [ ] Choose a **Windows image** (host-provided licensing)
- [ ] Rent the instance; record instance ID and connection details

### B-2 — First-session setup (~1.5 h)

- [ ] Connect via RDP (Vast.ai console exposes endpoint/credentials)
- [ ] Install **Parsec** on the VM and your laptop — much smoother than RDP for editor work
- [ ] Install Unity Hub → sign in (Unity Personal is free under the revenue threshold) → install the editor version your project requires (pinned)
- [ ] Pull your project (git), open it, verify 3D scenes render smoothly
- [ ] **Set up asset-safe backups**: git + Git LFS for assets (or rclone to object storage) — the marketplace host is NOT your source of truth

### B-3 — Weekly workflow

1. Start the stopped instance from the Vast.ai dashboard (~1-2 min)
2. Connect via Parsec, work
3. Before finishing: commit + push project changes (including LFS uploads)
4. **Stop** the instance — only storage is billed while stopped

### B-4 — Safety & cost guardrails

- [ ] Treat the host as disposable: anything not in git can be lost (marketplace host failures/maintenance happen)
- [ ] Keep the Unity editor version pinned in the project (`ProjectSettings/ProjectVersion.txt` in git)
- [ ] Set a billing alert on Vast.ai; never leave the instance running overnight
- [ ] Re-evaluate quarterly: if usage grows to several days/week, compare against a persistent Paperspace/RunPod Windows GPU VM

## Estimated Effort & Cost

**Track A (Hermes migration)**
- Hands-on time: **~4-6 hours** (Phase 1-2: 1h; Phase 3-4: 2h; Phase 5: 1h; Phase 6: 0.5h)
- Migration cost: ~$2-5 (snapshots + one month overlap if renewing)

**Track B (Unity workstation)**
- First-time setup: **~1.5 hours**
- Setup cost: ~$2-3 (first rental session)

**Ongoing total**

| Workload | Monthly |
|---|---|
| Hermes stack (Hetzner CX43 x86, nbg1, 24/7) | ~$19 |
| Unity Editor (Vast.ai RTX 4090, ~8h/week + stopped storage) | ~$14 |
| **Total** | **~$33/mo** |

vs current **$103.74/mo** → **~$71/month (~$850/year) saved**, with a far more capable Unity workstation than the local laptop.
