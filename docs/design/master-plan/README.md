# Hermes / ai-prentice-4-all — Master Implementation Plan

> **Status:** PLAN. This document + the per-feature-group
> docs under [`feature-groups/`](./feature-groups/) are the single source of
> truth for the multi-phase build-out — **Phase 1: FG-01–13** (multi-user,
> multi-channel one-brain; largely built + system-tested) and **Phase 2:
> FG-14–19** (requirements 14.0–19.0: Core/Customizable boundary, easy
> onboarding, action tracking, Next.js dashboard, GTS Centre, per-user GTS +
> assignment). Baseline regression tests live in `tests/plan_baseline/`.
>
> **Scope:** turn the single-owner personal Hermes deployment (ai-prentice-4-all) into
> a **multi-user, multi-channel, self-improving agent platform** for an
> organisation (school / company), while respecting the existing architecture's
> hard constraints (prompt-cache safety, one-brain, footprint ladder).

---

## 0. How to read / maintain this plan

- **This README** = cross-cutting decisions, principles, dependency waves,
  parallelisation, testing strategy, governance, and the **edition/audit log**.
- **`feature-groups/FG-XX-*.md`** = one doc per feature group. Each is
  self-contained: reuse map, design, data model, dev/prod + Supabase notes,
  testing requirements, dependencies, wave, definition-of-done, a live
  **progress checklist**, a **per-FG audit log**, and a **ready-to-paste Devin
  cloud-agent prompt**.
- **`agent-prompts.md`** = all cloud-agent prompts in one place.

### Governance / edition tracking (IMPORTANT for parallel agents)
- The **master plan changelog** (§9) is *append-only*. Any change to
  cross-cutting scope, decisions, waves, or contracts adds a row — never
  rewrites history.
- Each feature-group agent edits **only its own `FG-XX-*.md`** (its progress
  checklist + its own audit log). It must **not** edit other FG docs or the
  master changelog except to append one row to §9 when it changes a shared
  contract.
- Every audit entry uses this format:
  `YYYY-MM-DD | edition | author (user / devin:<session>) | FG | change | rationale`.
- Editions are integer, monotonically increasing per document.

---

## 1. Locked decisions (from Leo, this planning session)

| # | Decision | Consequence |
|---|----------|-------------|
| D1 | **Multi-user, NOT multi-tenant.** One shared brain; three-tier visibility: **shared** org knowledge, **per-user private** memory/skills, **owner** sees everything. | Every memory/skill/goal/task/tool/asset row carries `owner` + `visibility` (`shared` \| `private:<user_id>`); reads filtered by requesting principal; owner bypasses. Ownership is transferable (approval-gated). |
| D2 | **Memory consistency = HYBRID.** | Curated durable facts → frozen `MEMORY.md`/`USER.md` snapshot in the prompt (cache-safe). Volatile/coordination state + embeddings → live **queryable store** read/written mid-turn **via a tool call** (never prompt injection). |
| D3 | **OSS integration has two modes.** | **Remote system**: study OSS, clone + host on a *different* machine with minimal/no changes, expose via MCP (≈ design-doc §4.3). **In-house system**: build a *new* tool on the ai-prentice-4-all box, default **Next.js, one Node process per tool**, with **two interfaces: web UI (human) + MCP (agent)**. |
| D4 | **Datastore = bounded hybrid.** | **SQLite** stays for the Hermes *agent core* (SessionDB, kanban, projects, checkpoint, frozen memory files). **Self-hosted Supabase (Postgres + pgvector + GoTrue + Realtime + Storage)** is the datastore for the **new multi-user application layer** (identity/access, embeddings, goals/tasks/tools/change-log, in-house-tool data). |
| D5 | **Dev vs Prod.** | User-developed tools/skills/config start in **dev** (dev DB), promoted to **prod** on confirmation. Provide a **dev Supabase database/schema**. **Incoming channels are PROD-ONLY** (no dev channels). |
| D6 | **Blockchain (2.0) is opt-in + gated.** | DID:ION per user (hosted resolver first); ERC-721 mint per digital asset is the **explicit exception to undo** — **irreversible, must be user-triggered AND user-approved** (agent may never mint autonomously). Ship as plugin + MCP, testnet-first. |
| D7 | **`session_key` gains dimensions.** | `session_key = f(channel identity, account_id, internal user, task)` to maximise prompt-cache locality and isolate `(user, task)` cores. Extends `SessionSource` (adds `account_id`) + the key builder. |
| D8 | **Infra.** | Migrate the ai-prentice-4-all ECS to **`ecs.e-c1m4.xlarge` (4 vCPU / 16 GB)** first, **dedicated ESSD data disk** for Supabase, **EIP** for a stable IP; **in-place resize to `ecs.e-c1m4.2xlarge` (8/32)** when needed (same-family resize, ~5 min stop/start, no data migration). |
| D9 | **Delivery = parallel Devin cloud agents**, one per FG, coordinated in **dependency waves**. Shared contracts merge first (Wave 0). | See §5, §6. |

### Phase-2 locked decisions (reqs 14.0–19.0, from Leo, 2026-07-12)

| # | Decision | Consequence |
|---|----------|-------------|
| D10 | **Core is immutable to the runtime agent AND to end users; only human devs change Core (via repo/PR).** | A repo-committed `core_manifest.yaml` + a **hard runtime write-guard** at the agent's file/terminal chokepoint refuses any agent write to a Core path (fail-closed, no user/config override). Prevents a user from talking the LLM into breaking the system. Everything else (plugins/skills/tools/behavioural `config.yaml`/`app_*` data) is **Customizable** and change-tracked (C5). Publishes **C7**. (FG-14) |
| D11 | **Every interaction is traceable end-to-end via one `trace_id`; the trace is observability-only (cache-safe), RLS-scoped, retention-capped.** | Append-only `interactions` ledger joins inbound→turn→tool→outbound + linked change/cost/approval on one id; **never** injected into the prompt; user sees own, owner sees all; retention/rollup bounds growth. Extends C5 + cost tracking; publishes **C8**. (FG-16) |
| D12 | **Dashboard standardizes on Next.js (App Router) — frontend-only migration over the existing Python API backend.** All new in-house tools stay **Next.js + Node** (D3). | Port `web/` Vite→Next.js feature-for-feature against the unchanged `/api/*` backend (re-run FG-07/10 acceptance). Dashboard = the system "face": Core-area view, embedded Telegram chat, agent webview, tool link/icons. Dashboard + backend are Core (C7). (FG-17) |
| D13 | **Telegram is both a native channel (app) and the dashboard-embedded conversational UI**; both hit the same FG-03 one-brain backend. WhatsApp/email/other channels stay live. | Embedded web-Telegram (or a dashboard-native chat bound to the same bot/session) routes through the same gateway/session as the Telegram app. (FG-17) |
| D14 | **GTS Centre is a Core tool** unifying goals/tasks/skills; its implementation + governing rules are immutable to user/agent (only data is mutable, within its authority rules). | Extends FG-04 goals + FG-06 tasks + skills (no new store): **M:N** task↔goals & skill↔tasks, **hierarchical** goals/tasks with priorities, **user-only** top-level goals + evaluation methods, **agent** sub-goals/tasks, **auto-computed score 0–100** with priority-weighted rollup. Publishes **C9**. (FG-18) |
| D15 | **GTS is per-user isolated; owner sees all; cross-user assignment is a per-item grant (single assignee + optional watchers).** | An assigned item stays private to its creator but the assignee gets scoped access to *that item only* (no leak of the owner's other private GTS). Extends C2 with per-row grants + RLS; top-level goals not assignable; assignee can advance progress but not change eval method/reassign/delete; full C5/C8 audit. (FG-19) |

### Phase-3 locked decisions (from Leo, 2026-07-11)

| # | Decision | Consequence |
|---|----------|-------------|
| D16 | **`agent-home` — a new, mobile-first Next.js app — becomes the user-facing face; the system is a fixed three-tier stack: Next.js UI + Python AI layer (API) + Supabase (storage/DB).** All Phase-2 features move into it; the existing `web/` becomes the operator/admin console. | New `agent-home/` (App Router, mobile-first, PWA) via a **BFF pattern**: the `agent-home` server holds the C1 principal context, proxies agent/authority ops to the Python `/api/*` (one-brain chat, CDP webview, GTS authority, readiness, Core manifest, tool promote), and does **server-side** Supabase reads with the principal's RLS context (+ RLS-scoped Realtime). Browser never gets a privileged Supabase key or bypasses C1/C2/C6/C8. `agent-home` is **Core (C7)**. No new contract — a new surface over C1/C2/C3/C5/C6/C7/C8/C9. (FG-20) |
| D20 | **`agent-home` is THE key and main UI of the product — the dashboard (`web/`) is not.** Unless a request explicitly names another surface, **all UI work is done in `agent-home/`.** | Every new user-facing screen, redesign, UX/mobile/polish change and new feature surface lands in `agent-home/`. `web/` is frozen as the operator/admin console and only changes when a request explicitly says "dashboard" (or an operator-only surface has no `agent-home` equivalent). Applies retroactively to all Phase-2/Phase-3 FGs: where an FG names the dashboard as its surface, read `agent-home` unless the item is operator-only. (Leo, 2026-08-10) |

Cost context (see chat): current 2/4 box ≈ **$36/mo**; target 4/16 ≈ **$137/mo + ~$15 disk**; 8/32 ≈ **$266–317/mo**.

---

## 2. Architectural principles (the review bar — from `AGENTS.md`)

Every FG must obey these or it will not merge:

0. **`agent-home` is the main UI (D20).** All UI improvements go into
   `agent-home/` unless the request explicitly names another surface. The
   `web/` dashboard is the secondary operator/admin console.
1. **Prompt caching is sacred.** The system prompt must stay **byte-stable
   within a conversation**. Never inject fresh memory/goals/tools into the
   system prompt mid-conversation, never hot-swap a live conversation's
   toolset, never reorder/edit past messages. Surface new knowledge via
   **appended tool-call results** or **appended continuation messages**
   (this is exactly what `hermes_cli/goals.py` does today — the reference
   pattern). The one sanctioned exception is context compression.
2. **One brain, one profile.** All channels/users share `HERMES_HOME=/opt/data`
   — one skill store, one memory store, one session DB. Multi-user is an
   **access-control layer over the shared brain**, not separate profiles.
3. **Footprint ladder.** New capability arrives at the **highest (least core)**
   rung that works: extend existing code → CLI+skill → service-gated tool
   (`check_fn`) → plugin → MCP server → (last resort) new core tool. Almost
   nothing here should become a new *core model tool*.
4. **Extend, don't duplicate.** Reuse the primitives listed per FG. Do not add
   a 4th goal/task store, a 2nd approval framework, etc.
5. **Behavior/invariant tests, not change-detector tests.** Assert how data
   must relate; do not freeze current values (counts, model lists, config
   versions). Exercise real paths against a temp `HERMES_HOME`.
6. **`.env` = secrets only.** All behavioural config in `config.yaml` /
   Supabase, never new `HERMES_*` env vars for non-secrets.

---

## 3. Feature groups (index)

### Phase 1 — FG-01–13 (multi-user, multi-channel one-brain)

| FG | Title | Wave | Primary reuse anchors |
|----|-------|------|-----------------------|
| [01](./feature-groups/FG-01-multi-user-access.md) | Multi-users with access rights; single transferable owner | **0** | `gateway/authz_mixin.py`, `gateway/pairing.py`, `dashboard_auth/`, Supabase GoTrue + RLS |
| [02](./feature-groups/FG-02-blockchain-did-erc721.md) | Blockchain per user: DID:ION + ERC-721 assets | **HOLD** | `optional-skills/blockchain/evm`, MCP rung, approval gates |
| [03](./feature-groups/FG-03-multi-channel-redesign.md) | Multi-channel redesign (one brain, all channels) | 1 | `gateway/`, `gateway/session.py`, `custom/*`, design docs #1/#2 |
| [04](./feature-groups/FG-04-goals-priority-measurability.md) | Goals with priority + measurability/progress | 1 | `hermes_cli/goals.py` (`GoalState`/`GoalContract`/judge) |
| [05](./feature-groups/FG-05-embedding-memory-concurrency.md) | Embedding memory with concurrency | **0** | `tools/memory_tool.py`, `plugins/memory/*`, Supabase pgvector |
| [06](./feature-groups/FG-06-task-discovery-progress.md) | Task discovery & progress tracking | 1 | `tools/todo_tool.py`, `tools/kanban_tools.py`, `projects_db.py` |
| [07](./feature-groups/FG-07-tools-creation-dashboard.md) | Tools creation & configuration + Dashboard | 2 | `hermes_cli/tools_config.py`, `hermes mcp`, `web/`, catalog |
| [08](./feature-groups/FG-08-oss-copy-mcp.md) | Copy OSS capability + MCP (remote & in-house) | 2 | design §4.3, terminal sandbox backends, `hermes mcp` |
| [09](./feature-groups/FG-09-goal-management.md) | Management of goals: memory + tasks + tools | 3 | FG-04/05/06/07 + `mcp_serve.py` |
| [10](./feature-groups/FG-10-human-comms.md) | Human comms: Telegram + web app | 2 | `gateway/` telegram, `clarify_gateway`, `approval`, `web/` |
| [11](./feature-groups/FG-11-agent-comms-mcp.md) | Agent comms: MCP | 1 | `mcp_serve.py`, `tools/mcp_tool.py`, catalog |
| [12](./feature-groups/FG-12-change-management.md) | Change management (data/config/code) + undo/redo/approve/backup | 1 | `tools/checkpoint_manager.py`, `approval`, `write_approval`, `backup.py` |
| [13](./feature-groups/FG-13-dev-prod-mode.md) | Dev vs Prod mode + dev SQLite/Supabase (channels prod-only) | **0** | `hermes_constants.py`, `hermes_state.py`, `config.yaml` |

### Phase 2 — FG-14–19 (reqs 14.0–19.0)

| FG | Title | Wave | Primary reuse anchors |
|----|-------|------|-----------------------|
| [14](./feature-groups/FG-14-core-customizable-boundary.md) | Core vs Customizable boundary + protection (C7) | **A** | `core_manifest.yaml` (new), file/terminal write chokepoint, `changes.py` (C5) |
| [16](./feature-groups/FG-16-action-tracking-traceability.md) | Action tracking & traceability (C8) | **A** | `hermes_logging.py`, SessionDB, cost-tracker, `changes.py`, `plugins/observability/` |
| [15](./feature-groups/FG-15-easy-onboarding.md) | Easy onboarding (readiness score) | **B** | `hermes setup`, `config.yaml onboarding:`, `hermes tools`, FG-01/13 |
| [18](./feature-groups/FG-18-gts-centre.md) | GTS Centre (Goals→Tasks→Skills), a Core tool (C9) | **B** | `goal_registry.py`+`goals.py` (FG-04), `tasks`/kanban/todo (FG-06), skills |
| [17](./feature-groups/FG-17-dashboard-nextjs-face.md) | Dashboard = the face → Next.js + embedded Telegram + agent webview | **B→C** | `web/` (port Vite→Next.js), `web_server.py`/`/api/*`, `dashboard_auth`, CDP browser, FG-07 |
| [19](./feature-groups/FG-19-gts-per-user-isolation-assignment.md) | Per-user GTS isolation + cross-user assignment | **C** | FG-18 C9, C2 `can_read`/`scope_filter`+RLS, FG-10 (C6) |

### Phase 3 — FG-20 (mobile-first `agent-home`)

| FG | Title | Wave | Primary reuse anchors |
|----|-------|------|-----------------------|
| [20](./feature-groups/FG-20-agent-home-nextjs-supabase.md) | `agent-home` — mobile-first Next.js face (Next.js UI + Python AI layer + Supabase) | **A→B→C** (Phase-3) | Python `/api/*` (AI layer), Supabase (`app_*` + Storage), `access.py`/`interactions.py`/`gts.py` (C1/C2/C8/C9), `web/` panels as functional reference, `data-component` plugin |

### Phase 4 — FG-21 (semantic recall over the shared brain)

| FG | Title | Wave | Primary reuse anchors |
|----|-------|------|-----------------------|
| [21](./feature-groups/FG-21-local-semantic-memory-rag-shared-recall.md) | Local semantic memory (layer 4), RAG, and shared recall across users | **0→A→B** (Phase-4) | `plugins/memory/supabase_pgvector/` (FG-05), `hermes_cli/access.py` C2 + `item_grants` (FG-01/FG-19), `interactions.py` C8 audit (FG-16), Supabase pgvector (D4) |

### Phase 5 — FG-22, FG-23 (seeing the memory tier)

| FG | Title | Wave | Primary reuse anchors |
|----|-------|------|-----------------------|
| [22](./feature-groups/FG-22-memory-visualizer.md) | Read-only memory visualizer on the operator dashboard | **V1→V4** (Phase-5) | `hermes_cli/memory_explorer.py` over FG-21's store, `_comms_resolve_principal` C1, `memory_projection` under the same C2 RLS as `memories`, `web/` SPA + `@observablehq/plot` |
| [23](./feature-groups/FG-23-memory-on-agent-home.md) | The memory visualizer on `agent-home` (the phone) | **A0→A5** (Phase-5) | FG-22's `/api/memory/explorer/*` endpoints (unchanged), FG-20 BFF (`HermesApiClient`, `requirePrincipal`, `MobileShell`), `deploy/hermes-deploy.sh` + `deploy_state.py` |

### Phase 6 — FG-24–27 (scaling one profile to hundreds of principals)

Phase 1 built multi-user for a handful of principals in one profile. Phase 6 is
what an organisation of 500 actually needs: personal curated memory, a way to
express *sets* of people, and an administration surface that does not require a
CLI. FG-24 and FG-25 are independent and may run in parallel; FG-26 renders
FG-25. FG-27 is a **prerequisite**: it closes a silent cross-profile data-merge
footgun before FG-25/FG-26 add identity-bearing tables to the shared schema.

| FG | Title | Wave | Primary reuse anchors |
|----|-------|------|-----------------------|
| [27](./feature-groups/FG-27-profile-scoped-datastore-isolation.md) | Profile-scoped app-layer datastore isolation (close the shared-schema footgun) | **P6-0** (before P6-A touches app tables) | `hermes_cli/datastore.py` C3 router (`get_store`, `initialize_supabase_app`), `hermes_cli/profiles.py` (`_CLONE_CONFIG_FILES`, `get_active_profile_name`), `hermes_constants.set_hermes_home_override` |
| [24](./feature-groups/FG-24-per-principal-curated-memory.md) | Per-principal curated memory (memory layers 1–2 become per-user) | **P6-A** (Phase-6) | `tools/memory_tool.py` (`get_memory_dir`, `MemoryStore`, frozen snapshot), `agent/agent_init.py` (principal already in scope), `agent/system_prompt.py` volatile tier |
| [25](./feature-groups/FG-25-group-scopes-multi-dimensional.md) | Group scopes: multi-dimensional, hierarchical audiences + scoped admin (**publishes C10**) | **P6-A** (Phase-6) | `hermes_cli/access.py` C2 (`scope_filter`/`apply_scope_rls`/`bind_principal`), FG-19 `item_grants` clause pattern, FG-21 elevation GUC + `memory_access_audit` |
| [26](./feature-groups/FG-26-users-groups-admin-console.md) | Users & Groups admin console + invitation activation | **P6-B** (after FG-25) | `hermes_cli/members.py` (`MemberService`, `GoTrueAdminClient`), `/api/comms/members*`, FG-20 BFF + `MembersView.tsx`, C5/C8 |

---

## 4. Cross-cutting shared contracts (Wave 0 — must merge FIRST)

These are the seams every later FG consumes. They are small, additive, and
land before any parallel feature work so agents don't collide on the god-files
(`cli.py`, `run_agent.py`, `hermes_state.py`, `gateway/run.py`).

- **C1 — Principal/identity model** (FG-01). `Principal{user_id, role∈{owner,admin,member,viewer}, ...}`; a `resolve_principal(source)` seam in the gateway; `owner` transfer op. Backed by Supabase GoTrue.
- **C2 — Visibility/scoping helper** (FG-01 + FG-05). `visibility ∈ {shared, private:<user_id>}` + `can_read(principal, row)` / `scope_filter(principal)` used by memory, skills, goals, tasks, tools, assets.
- **C3 — Datastore router** (FG-13 + FG-04-DB). One accessor that returns the correct connection/schema for **(mode: dev|prod)** and **(store: sqlite-core | supabase-app)**. Everything DB-touching goes through it. Channels force `mode=prod`.
- **C4 — `SessionSource.account_id` + extended `session_key`** (FG-03 + D7). Additive field; `build_session_key` folds in `account_id` (+ user/task where applicable) while remaining **byte-identical for existing single-account callers** (regression-locked by `tests/plan_baseline/test_session_key_baseline.py`).
- **C5 — Change-event schema** (FG-12). Append-only `changes(id, ts, actor, mode, target_kind∈{data,config,code}, op, inverse_op|null, reversible:bool, approval_ref, backup_ref)`; every mutating capability emits one.
- **C6 — Approval/consent policy object** (FG-10 + FG-12 + FG-06/04). One policy surface (reuse `tools/approval.py` + `write_approval.py`) with quiet-hours/rate-limit/consent, shared by proactive messaging (4.1/6.1), change approvals (12), and action gating.

Wave-0 agents publish these as typed interfaces + docstrings + baseline tests
**before** Wave-1 agents start.

### Phase-2 contracts (published by FG-14/16/18; consumed by the rest)

- **C7 — Core/Customizable boundary** (FG-14). A repo-committed `core_manifest.yaml` (globs) + a **hard runtime write-guard** at the agent's file/terminal write chokepoint: any agent write whose resolved path is Core is **refused** (fail-closed, escape-safe, no user/config override) and audited. Applies to the runtime LLM agent only; human dev/git/`hermes update` unaffected. Customizable writes emit C5.
- **C8 — Interaction trace** (FG-16). Append-only `interactions(id, trace_id, parent_id, ts, actor_user_id, session_key, platform, kind∈{inbound,turn,tool_call,tool_result,outbound,approval,change,cost,error,core_denied}, ref, summary, payload_ref, mode)`; one `trace_id` per originating interaction joins messages+tools+changes(C5)+cost; **cache-safe** (never prompt-injected), **RLS-scoped** (owner sees all), retention/rollup-capped. Reuses logging/SessionDB/cost-tracker/`changes.py`/observability plugin.
- **C9 — GTS graph** (FG-18; assignment extended by FG-19). Unified nodes + typed edges over FG-04 goals + FG-06 tasks + skills: hierarchical goals/tasks (`parent_*_id`, cycle-safe) with priorities, **M:N** `task_goals`/`task_skills`, user-owned `evaluation_methods` (agent-immutable), auto-computed `score` (0–100, priority-weighted rollup). FG-19 adds `assignee_user_id` (tasks + sub-goals only) + `item_grants` (single assignee + watchers) extending C2.

### Phase-6 contract (published by FG-25; consumed by FG-26 and every scoped table)

- **C10 — Group scope** (FG-25; extends C2). `visibility ∈ {shared, group:<group_id>, private:<user_id>}`. Groups are a **forest of typed dimensions** — `groups(id, key, dimension, parent_id, elevation_enabled)` with a same-dimension-parent + acyclicity invariant and a materialised `group_closure` — plus `group_members(group_id, user_id, group_role∈{admin,member})`. **Read inheritance goes up** (audience = my groups + ancestors); **admin scope goes down** (groups I admin + descendants). Both are expanded **once per transaction** by `bind_principal()` into the `hermes.principal_groups` / `hermes.principal_admin_scope` GUCs and tested by array membership in the policy — never a recursive CTE per row; an unbound GUC degrades to plain C2 (fail-closed). Reading a **team** row via admin scope is not audited; reading a **person's** `private:` row is per-group-opt-in elevation and is **always** audited with `via_group_id`. Group elevation compares **group** roles, not instance roles.

---

## 5. Dependency graph & waves (for parallel development)

```
WAVE 0 (foundations — merge before anything else; can run 3 agents in parallel,
         but they co-own C1–C6 so land them as small contract PRs first)
  ├─ FG-13  dev/prod mode + datastore router (C3)              ─┐
  ├─ FG-01  multi-user identity/access (C1, C2)                 ├─ contracts C1–C6
  └─ FG-05  embedding memory + concurrency (C2 uses, pgvector)  ─┘

WAVE 1 (core capabilities — parallel; each owns a distinct subsystem)
  ├─ FG-03  multi-channel redesign      (needs C3, C4; needs D2 memory model)
  ├─ FG-04  goals + priority + metrics  (needs C2, C3)
  ├─ FG-06  task discovery + progress   (needs C2, C3; feeds from FG-03 convo)
  ├─ FG-11  agent comms MCP             (needs C1 for auth)
  └─ FG-12  change management           (needs C3, C5; publishes C6 approval)

WAVE 2 (needs Wave 1 + C6 approval frozen — parallel)
  ├─ FG-07  tools creation + dashboard  (needs C3, C5, C6; web/)
  ├─ FG-08  OSS remote + in-house       (needs C6, FG-07 tool-registry, sandbox)
  └─ FG-10  human comms webapp parity   (needs C1, C6, web/)

WAVE 3 (integration)
  └─ FG-09  goal management = memory+tasks+tools across sources/telegram/webapp/MCP
             (needs FG-04, 05, 06, 07, 10, 11)

ON HOLD (not scheduled — resume only on explicit owner go-ahead)
  └─ FG-02  blockchain DID + ERC-721    (would be Wave 2; needs C1, C6; plugin+MCP)
```

### Phase 2 (reqs 14.0–19.0) — waves (start after Phase-1 `develop` is merged)

```
WAVE A (Phase-2 foundations — parallel; publish contracts first, like Wave 0)
  ├─ FG-14  Core/Customizable boundary (C7)   (needs C5/FG-12, C2/FG-01)
  └─ FG-16  action tracking & trace (C8)       (needs C1/C2, C5, C3)

WAVE B (parallel; each owns a distinct subsystem)
  ├─ FG-18  GTS Centre (C9)                    (needs FG-04, FG-06, C2, C5/C6, C3; FG-14 marks engine Core)
  ├─ FG-17a dashboard frontend Vite→Next.js    (parity port over existing /api/* — can start immediately)
  └─ FG-15  easy onboarding (CLI-first)        (needs FG-01, FG-13; dashboard wizard rides FG-17)

WAVE C (integration — after Wave B)
  ├─ FG-19  per-user GTS isolation + assignment (needs FG-18 C9, C2/roles, C6/FG-10)
  └─ FG-17b dashboard new panels               (Core-area view + embedded Telegram + agent webview +
             GTS Centre UI + trace view + onboarding wizard — needs FG-14/16/18/19/15)
```

**Phase-2 parallelization (mirrors §6):** FG-14, FG-16, and FG-17a (parity
port) are independent and can run as **three parallel agents immediately** after
Phase-1 `develop` merges; FG-18 and FG-15 join in Wave B (deps are already
merged Phase-1 FGs). Wave C (FG-19 + FG-17b integration panels) starts once
FG-18/16/14 land. As in Wave 0, **publish the new contracts (C7/C8/C9) as small
interface PRs first** so Wave-B/C agents don't collide on the god-files. Each
agent works on its own branch, edits only its FG doc, keeps baseline green, and
re-runs the affected system-test checklists (FG-17 must re-run FG-07/10).

### Phase 3 (FG-20 `agent-home`) — waves (start after Phase-2 `develop` is merged + owner confirms FG-20 open decisions)

```
WAVE A (Phase-3 foundations — parallel; publish the data/auth seam first)
  ├─ FG-20/A1  agent-home Next.js skeleton      (App Router, Tailwind, mobile shell + bottom-nav,
  │            PWA, supabase-js, data-component, build/CI, on-box Caddy route)
  └─ FG-20/A2  auth + data-access foundation    (C1 principal bridge → server-side Supabase RLS
               context; typed Python-API client; shared types) — SMALL INTERFACE PR FIRST

WAVE B (parallel; each owns a distinct feature area — reads Supabase, authority via Python API)
  ├─ FG-20/B1  GTS Centre (graph, scores, assignment + watchers)   (C9/FG-18/19)
  ├─ FG-20/B2  Core-area view + interaction-trace timeline          (C7/FG-14 + C8/FG-16)
  └─ FG-20/B3  onboarding wizard + readiness + tools registry       (FG-15 + FG-07)

WAVE C (agent-coupled + polish — parallel)
  ├─ FG-20/C1  agent chat pane (one-brain via API) + Supabase Storage attachments/media  (FG-03/D13)
  ├─ FG-20/C2  agent webview (CDP + C6 consent + C8 trace)                                (FG-17b)
  └─ FG-20/C3  comms/notifications + change undo/redo + mobile/PWA polish + system test    (FG-10/12)
```

**Phase-3 parallelization (mirrors §6):** A1 (skeleton) and A2 (auth/data seam)
run as two parallel agents; **A2 publishes its auth/data-access foundation as a
small interface PR first** (like Wave 0's contracts) so the Wave-B/C agents
share one Supabase-context + API-client seam instead of colliding. Once A merges,
Wave B fans out to three agents (each a distinct feature area), then Wave C to
three (chat / webview / comms+polish). Every agent works on its own branch, edits
only the FG-20 doc, keeps baseline + web build green, preserves the one-brain
chat path (cache-safe), and re-runs the negative-access RLS + C6 checks. The
existing `web/` operator console is left intact.

### Phase 6 (FG-24–26 scale-out) — waves (start after Phase-5 `develop` is merged + owner resolves the FG-26 "assign profile" decision)

```
WAVE P6-0 (prerequisite — small; Layers 1+2 must land before P6-A adds tables)
  └─ FG-27  profile-scoped datastore isolation  (C3 router + profile clone; no new
                                                 contract, no shape change)

WAVE P6-A (parallel — two independent agents; FG-25 publishes C10 first)
  ├─ FG-24  per-principal curated memory        (needs C1 only; touches tools/memory_tool.py,
  │         agent/agent_init.py, agent/system_prompt.py — no DB change)
  └─ FG-25  group scopes / C10                  (needs C1/C2, C3, C5, C8; extends scope_filter +
            apply_scope_rls + bind_principal — SMALL CONTRACT PR FIRST, like Wave 0)

WAVE P6-B (after FG-25 merges)
  └─ FG-26  Users & Groups console + invitations (needs C10/FG-25, FG-20 BFF, C5, C8;
            also carries the list_principals N+1 fix that the roster needs at N=500)
```

**Phase-6 parallelization:** FG-24 and FG-25 share no files and can run as two
agents immediately; **FG-25 lands C10 as a small contract PR first** so FG-26
builds against a frozen seam. FG-26 must not start before FG-25 merges — it
renders groups it cannot otherwise query. Runtime scale-out (gateway workers
sharded by session key, `SessionDB` off SQLite, per-principal rate/cost quotas,
the D8 8/32 resize) is **separate work, not part of Phase 6**: Phase 6 makes the
*access model* serve thousands of registered principals, while concurrent-session
capacity remains an open runtime item (§8).

> **FG-02 (blockchain) is ON HOLD** per Leo (2026-07-11). It is excluded from
> the wave schedule and will not be launched until the owner explicitly
> resumes it. All other FGs proceed as scheduled. FG-02 has **no downstream
> dependents** (nothing in Waves 0–3 or FG-09 depends on it), so holding it
> does not block any other feature group.

**Ordering rules for agents:**
- An FG agent may start only when **all its "blocked-by" FGs have merged** (see
  each FG doc's *Dependencies* section).
- Within a wave, agents work in **separate modules/plugins**; edits to shared
  god-files must go through the Wave-0 contract seams, not ad-hoc.
- FG-03 must define/merge `account_id` (C4) early in Wave 1 because FG-06 and
  FG-09 build on the per-`(user,task)` session shape.

---

## 6. Parallel Devin cloud agents

Each FG doc ends with a **ready-to-paste prompt**; all are collected in
[`agent-prompts.md`](./agent-prompts.md). To launch:

- **Manual:** open a new Devin session, paste the FG prompt. Start only FGs
  whose dependencies are merged (respect the waves).
- **Automatic (offered after this plan PR merges):** the orchestrator session
  can spawn child sessions **wave-by-wave** — Wave 0 first, then fan out.
  Never launch all 13 at once; Waves 1–3 depend on Wave-0 contracts.

Every agent must: work on its own branch, edit only its FG doc, keep the
baseline suite green, add its own FG tests, run lint+typecheck, open a PR that
links back to its FG doc.

---

## 7. Testing strategy

Follows the repo's harness (`scripts/run_tests.sh`, per-file isolation,
hermetic `HERMES_HOME`, CI: `tests.yml` / `typecheck.yml` (ty) / `lint.yml`
(ruff `PLW1514`)) and doctrine (invariant/behavior tests, real E2E vs temp
`HERMES_HOME`, **no change-detector tests**).

- **Baseline regression suite (`tests/plan_baseline/`, delivered with this plan):**
  pins the invariants of the primitives every FG extends, so any FG that
  regresses a reuse anchor fails immediately. Current coverage:
  - `test_session_key_baseline.py` — `build_session_key` determinism + per-chat / per-user isolation + namespace shape (locks C4: FG-03 must keep these byte-stable for single-account callers).
  - `test_goal_state_baseline.py` — `GoalState`/`GoalContract` JSON round-trip, defaults, back-compat load of old rows (locks FG-04's base).
  - `test_todo_store_baseline.py` — todo status vocabulary + transition/merge semantics + prompt-injection of only pending/in-progress (locks FG-06's base).
- **Per-FG tests (each FG delivers):** unit + at least one **real-path E2E**
  against a temp `HERMES_HOME` (and a throwaway Postgres/Supabase schema where
  the FG touches the app DB). Multi-user FGs must include a **negative access
  test** (a `private:<other>` row is NOT visible to a different member; owner
  sees it).
- **Definition of Done (every FG):** new FG tests green **AND** full baseline
  suite still green **AND** `ruff`/`ty` clean, before the FG is marked complete
  in its doc. "Enough testing coverage to confirm the new feature group works
  without bugs and did not cause regression" (Leo) is enforced by this gate.

Run: `scripts/run_tests.sh tests/plan_baseline/` (fast) and the FG's own path.

### 7.1 System testing environment (the new ECS)

There are **three** distinct places tests run — keep them separate:

| Layer | Where it runs | When | Data |
|-------|---------------|------|------|
| Baseline + per-FG unit/E2E | each Devin agent's VM + **CI** (per PR) | continuously, during development | temp `HERMES_HOME` + **throwaway** Postgres schema |
| **System / integration testing** | **the new ai-prentice-4-all ECS** (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, `ecs.e-c1m4.xlarge` 4 vCPU/16 GB, cn-hongkong-b, EIP `47.83.199.25`) as the dedicated **system-test host** | **after EACH feature group's development completes** (a required step in that FG's Definition of Done) | **staging** Supabase schema (`app_staging`) + a staging SQLite core on the box — **never the prod schema** |
| Production | **the same new ECS for now** (`app_prod` schema + prod `state.db` on the same box; promote to a larger/dedicated box later per D8) | after an FG's system test passes and it is promoted | prod Supabase (`app_prod`) + prod `state.db` |

**Why the new box:** the existing 2 vCPU/4 GB ai-prentice-4-all ECS is too small for
the new self-hosted-Supabase design, so a **new 4/16 box** (`ecs.e-c1m4.xlarge`,
100 GB ESSD data disk mounted at `/opt/data`, stable EIP `47.83.199.25`) is the
dedicated **system-test host** — and, for now, also hosts production (staging
`app_staging` and prod `app_prod` are separate Supabase schemas + separate
SQLite cores on the one box, isolated via contract C3). It is **not** a
development box. Every feature group, once its code is developed and its per-PR
unit/E2E + baseline gate is green, is deployed here and exercised **end-to-end
on the real stack** before that FG is considered done. Same-family in-place
resize to 8/32 (~5 min, no data migration — D8), or split prod onto its own box
later, when load requires it.

**How the per-FG system test works (repeated for every FG):**
1. The FG's PR passes CI (baseline + its own unit/E2E, `ruff`/`ty`).
2. The FG is deployed to the new ECS in **staging mode** (`mode=dev`/
   `staging` via contract C3; `app_staging` Supabase schema + staging SQLite
   core) on top of the already-merged FGs.
3. Run **this FG's "System testing (system-test box)" acceptance checklist** (see
   its FG doc) against the real deployed stack — real GoTrue/RLS, real pgvector,
   real channel adapters bound to **test** accounts, real Telegram + web app,
   real MCP endpoints.
4. **Definition-of-Done gate:** the FG is not complete/promotable until its
   system-test checklist is green (on top of the per-PR gate). Only then is it
   promoted to production (`app_prod` + prod `state.db`, on the same box for now).

**Ordering note:** because each FG's system test runs on the *cumulative*
deployed stack, an FG whose live behaviour depends on a not-yet-merged FG
verifies what it can in isolation and re-runs the cross-FG checks once its
dependency lands (see each FG's *Dependencies*). Cross-surface end-to-end
coverage is owned by **FG-09**.

**Resource caveat:** the full self-hosted Supabase bundle + Node tools + a
staging *and* prod stack on the 4/16 box is tight; run each FG's system test
**sequentially** (not all channels/tools hot at once), keep staging workloads
transient, and in-place-resize to 8/32 (same-family, ~5 min, no data migration
— D8) or split prod onto its own box when load requires it.

---

## 8. Risks / open items carried into implementation

- **OPEN DECISION (blocks FG-26's create-user form): "assign profile" on user
  creation.** A profile is an isolated brain (own `HERMES_HOME`, config,
  gateway, database config), not an attribute of a user — principals are rows
  *inside* one profile's database, so there is nowhere to record "Dana's
  profile". Three readings (display profile / current-profile-only / true
  cross-profile identity) are tabulated in FG-26; only the third is a real
  architecture change and it would be its own FG. FG-26 assumes the first until
  the owner says otherwise.
- **Concurrent-session capacity is the real scale ceiling, and Phase 6 does not
  address it.** FG-24/25/26 make the *access model* serve thousands of
  registered principals (per-row scoping, two GUC-expansion queries per request
  regardless of N). The runtime does not follow: one gateway process per profile
  holding a live `AIAgent` per active session, `SessionDB` on single-writer
  SQLite shared profile-wide, no per-principal rate or cost cap (only a global
  `max_concurrent_sessions`), all on a 4 vCPU / 14 GB box that also runs
  Supabase. Realistic shape today: **thousands registered, order tens
  concurrent.** Hundreds *simultaneously active* needs sharded gateway workers,
  `SessionDB` on Postgres, per-principal quotas, and the D8 resize — a separate
  FG, larger than FG-24 and FG-25 combined.
- **The invitation redeem endpoint is the highest-value new attack surface** —
  it is unauthenticated and grants account control. Mitigations are specified in
  FG-26 (hash-only storage, single use, 5-minute TTL, constant-time compare,
  per-IP/per-token rate limiting, uniform failure responses, `no-referrer` on
  the activate route) and every one of them is a required test, not an
  implementation detail.
- **5 minutes is short for an asynchronous handover.** It is what the owner
  asked for and it is the right default for a link relayed over chat, but it
  will expire during any store-and-forward delivery — so "Regenerate link" is
  part of the feature, and email/IdP delivery is the eventual answer.
- **Explicit group membership does not survive 4-figure headcount by itself** —
  at that size membership should come from the IdP (SCIM/OIDC group sync writing
  the same FG-25 tables). Deferred out of FG-25 v1 deliberately; revisit before
  any deployment above a few hundred principals.
- **FG-25 changes FG-21 P3 elevation semantics.** Group elevation compares
  `group_members.group_role`, not instance roles, because in a correctly
  configured deployment every team lead is instance-role `member` (instance
  `admin` outranks every member *globally*, which would make group scoping
  decorative). The instance-wide `role_reads` switch is retained but deprecated;
  a deployment that assigns instance `admin` to team leads has silently
  bypassed the group model, so `hermes member add --role admin` should warn.
- **Profile isolation at the app layer is conventional, not structural
  (FG-27).** `get_store()` hard-codes the app schema to `app_dev`/`app_prod`, so
  two profiles are isolated only because each `config.yaml` happens to carry a
  distinct DSN — and `hermes profile create --clone`, the documented "start from
  my default" path, copies `config.yaml` verbatim. Two profiles then share one
  `principals`/`memories`/`changes` set with no error, no log line and no
  on-disk symptom. RLS does not help: it scopes rows correctly inside a database
  both profiles treat as their own. FG-27 Layers 1+2 are sequenced **before**
  FG-25/FG-26 add `groups`/`invitations`, because an invitation redeemable in
  the wrong profile is a much worse failure than a merged memory row.
- **The shared kanban board has no identity namespace.** `tasks` carries
  `owner_user_id` and `visibility` (C2's vocabulary) on a board that is shared
  across profiles *by design*, but `principals` is per profile — so the same
  `user_id` denotes different people in different profiles and nothing
  reconciles them. Harmless while one human uses it; a real ambiguity once
  several multi-user profiles share a board.
- **Anything that couples profiles together fights an explicit design
  intent.** `AGENTS.md` records profiles as "independent islands on purpose"
  and cites a closed PR that added cross-profile config inheritance. A
  multi-profile administration console (one admin managing users in several
  profiles) is therefore a **deliberate reversal** that must be argued on its
  own merits in its own FG, not slipped in as an FG-26 convenience.
- **Supabase resource budget** on a 4/16 box with the full bundle + Node tools
  + concurrent cores — monitor; resize to 8/32 (D8) if RAM-bound.
- **Multi-user vs upstream Hermes divergence** — keep the access layer as an
  additive seam so upstream merges stay tractable.
- **Proactive messaging (4.1/6.1)** must ride C6 (quiet-hours/rate-limit/
  consent) or it becomes spam; also guard against self-generated task loops.
- **ERC-721 irreversibility** (D6) is explicitly outside undo (12.1).
- **`alibabacloud` MCP server currently fails to init** — infra used the
  `aliyun` CLI instead; flagged separately.
- **FG-03 live-gateway wiring IMPLEMENTED (code); live channel round-trip
  pending creds.** `gateway/run.py` now enriches each inbound turn with the C4
  identity (`_enrich_channel_source_identity`: receiving `account_id` +
  sender→internal `Principal`) at the `_handle_message_with_agent` chokepoint,
  before the session-key / cached-`AIAgent` lookup, so multi-channel one-brain /
  per-internal-user isolation is active at runtime (gated to a no-op when the
  app-DB DSN is unset). It reuses the gateway's existing per-session-serial /
  cross-session-parallel cached-agent dispatch instead of adding a second
  `InboundRouter` queue (see *Design decision* in the FG-03 doc). Only Telegram
  is live-tested so far; the **live WhatsApp/email round-trip still needs the
  channel creds** (email = old-box Gmail IMAP app-passwords; WhatsApp = QR
  bind). Status + checklist: *Gateway migration* in
  `feature-groups/FG-03-multi-channel-redesign.md`.
  - **Update (2026-07-12):** live **read-only** validation completed on the
    system-test box — Telegram full round-trip (inbound→DeepSeek→egress) +
    approval parity; WhatsApp (personal `85251922892` + ConnectAR `85296660978`,
    resumed sessions, read-only) and email (Gmail IMAP, read-only) confirmed
    resolving through the migrated C4 identity path. Auto-reply/SMTP send NOT
    tested (owner: read-only for now). Old ECS WhatsApp bridges stopped +
    sessions neutralized (renamed `*.disabled-20260712`). **Prod not promoted**
    (owner deferred — more features/testing first).
- **Phase 2 (reqs 14.0–19.0) added (2026-07-12):** FG-14–19 planned (see §1
  D10–D15, §3, §4 C7–C9, §5 Phase-2 waves). Key constraints carried in: the
  Core write-guard (C7) must be fail-closed with no user override; the
  interaction trace (C8) must stay cache-safe + RLS-scoped + retention-capped;
  the Next.js dashboard is a **frontend-only** migration (keep the Python API)
  and must re-run FG-07/10 acceptance to prove no regression; GTS Centre (C9)
  must **extend** FG-04/06 (no new store) with user-only top-level goals +
  evaluation methods and auto-computed scores; cross-user assignment (D15) adds
  per-item grants to C2 without leaking the owner's other private data.

---

## 9. Master plan changelog (append-only)

| Date | Edition | Author | Scope | Change | Rationale |
|------|---------|--------|-------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 (for Leo) | all | Initial master plan + 13 FG docs + baseline tests | Kickoff of the 13-FG build-out; decisions D1–D9 locked in planning session |
| 2026-07-11 | 2 | devin:8cec0d47 (for Leo) | testing | Added §7.1 + a "System testing" section to every FG doc, as a required Definition-of-Done step after each FG's development | Leo: use a dedicated ai-prentice-4-all ECS as the system-test host, exercised after every feature group's development (per-FG, not a single post-all-waves pass) |
| 2026-07-12 | 5 | devin:8cec0d47 (for Leo) | FG-03 | Documented the outstanding **gateway migration** (Shape-1 `InboundRouter`/producers → live `gateway/run.py`) as an executable checklist in FG-03; added a §8 open item. Clarified WhatsApp/email are not yet live at runtime (only Telegram was live-tested) | Leo: migrate the live gateway to the one-brain router first, then live WhatsApp/email round-trips; make it followable/verifiable by future agents |
| 2026-07-11 | 3 | devin:8cec0d47 (for Leo) | infra/testing | System-test host = a **new** 4/16 ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, EIP `47.83.199.25`, 100 GB ESSD at `/opt/data`), which also hosts prod for now (`app_staging` vs `app_prod` schemas + separate SQLite cores via C3); retitled the FG section to "System testing (system-test box)" | Leo: the existing 2/4 box is too small for the new self-hosted-Supabase design; provisioned the new box and pointed system testing (and, for now, prod) at it |
| 2026-07-11 | 4 | devin:8cec0d47 (for Leo) | scope | **FG-02 (blockchain DID + ERC-721) put ON HOLD** — removed from the Wave-2 schedule; will not be launched until the owner explicitly resumes it. All other FGs proceed. | Leo: hold the blockchain implementation but go ahead with the rest. FG-02 has no downstream dependents, so holding it blocks nothing. |
| 2026-07-12 | 7 | devin:8cec0d47 (for Leo) | scope/Phase-2 | **Added Phase 2 (reqs 14.0–19.0):** new decisions **D10–D15**, contracts **C7 (Core/Customizable boundary), C8 (interaction trace), C9 (GTS graph)**, index rows + docs for **FG-14–19**, a Phase-2 wave/parallelization plan (§5), and a §8 update recording the completed live read-only WhatsApp/email/Telegram validation (prod not promoted). Frontend-only Next.js dashboard migration (keep Python API); GTS Centre extends FG-04/06 (no new store); single-assignee cross-user assignment via per-item grants. | Leo: study reqs 14–19 and revise the plan without regressions; standardize dashboard on Next.js; keep multi-channel one-brain; hard-block the runtime agent from Core; full interaction tracing; per-user GTS + assignment. Decisions confirmed in session (Next.js frontend-only, single assignee + watchers, full trace + retention/cache-safe/access-scoped). |
| 2026-07-11 | 6 | devin:8cec0d47 (for Leo) | FG-03 | **Implemented the FG-03 live-gateway wiring.** `gateway/run.py` now enriches each inbound turn with the C4 identity (receiving `account_id` + sender→internal `Principal`) at the `_handle_message_with_agent` chokepoint before session-key/cached-`AIAgent` lookup, reusing the existing per-session-serial / cross-session-parallel cached-agent dispatch (no second `InboundRouter` queue). Gated → byte-stable no-op when the app-DB DSN is unset. Added `tests/gateway/test_live_gateway_identity_wiring.py`; updated the §8 open item + FG-03 status/checklist. Live WhatsApp/email round-trip still pending channel creds. | Leo: migrate the live gateway to the one-brain router first, then live WhatsApp/email round-trips. |
| 2026-07-12 | 8 | devin:eaf2cdff (for Leo) | FG-16 / C5 / C8 | Published the additive C8 interaction ledger in C3-routed `app_dev`/`app_prod`, and added nullable `trace_id` linkage to C5 change rows. | One gateway-minted correlation id now joins interaction, change, and cost observations without altering prompt bytes or adding a model-facing tool. |
| 2026-07-13 | 10 | devin (for Leo) | FG-18 / C9 | **Refined C9 with the observe/measure goal-evaluation model** (additive to the existing `evaluation_methods`; no new store). Every goal is *observable* but not always *measurable*: `method_json` gains an explicit `measurable` flag, a typed `observation {source: internal\|external\|ask, prompt, ref?}` (external requires a db/api/mcp `ref`), and a measurable-only `scoring {prompt}` that programmatically computes the clamped 0–100 score over a new additive `observed_state` column via a clean evaluator seam (`GtsScoreEvaluator`/`ScoringRequest`; deterministic default, no external calls). Non-measurable goals keep an observation + qualitative status, take no auto-score, and are excluded from priority-weighted parent rollups. Authority unchanged — the observation prompt, `measurable` flag, and scoring prompt are user-owned (agent refused + audited via C8 `core_denied` + durable JSONL); recording observed state is data (agent-allowed). Cache-safe; `hermes_cli/gts.py` stays Core. | Give measurable goals a user-authored, programmatic scoring path over observed state while keeping non-measurable goals qualitative, within the existing GTS structures and the C7/C9 authority boundary. ECS system-test box + prod promotion remain separate gated steps owned by Leo. |
| 2026-07-04 | 11 | devin:3c64bcf2 (for Leo) | FG-19 / C2 / C9 | **Completed FG-19 per-user GTS isolation + per-item cross-user assignment** (extends C2 + C9; no new access/store system). Per-item `item_grants(item_kind,item_id,user_id,grant∈{assignee,watcher},status)` — single active assignee (partial-unique) + read-only watchers — with a grant-aware `can_read`/`scope_filter` **and** FORCE'd Postgres RLS "granted-to-me" clause so an assignee/watcher sees ONLY the assigned item, never the owner's other private GTS; owner still sees all (+ `list_goals_for_user` browse). Assign/reassign/accept/decline/revoke lifecycle, owner-only assignment/eval-method/reassign/revoke, assignee-may-advance-progress/add-sub-tasks, top-level goals not assignable, agent-initiated assignment gated on C6, full C5+C8 audit; score stays auto-computed (FG-18 rollup). **Fixed a latent grant-clause bug**: the app-layer `scope_filter` correlated its grant `EXISTS` on an unqualified `id` that resolved to `item_grants.id` inside the sub-select (never matched) — the 3 GTS read call sites now pass a table-qualified `id_column`, mirroring the already-correct RLS clause. Added real-Postgres E2E `tests/hermes_cli/test_fg19_assignment_e2e.py` (lifecycle + score rollup + RLS negative-access + authority + C6 + audit). | Phase-2 req 19.0 DoD: real-path E2E for the access-control/datastore change; verify behavioral invariants (RLS isolation, authority, audit) not snapshots. ECS system-test box + prod promotion remain separate gated steps owned by Leo. |
| 2026-07-04 | 12 | devin (for Leo) | FG-17b / C6 / C7 / C8 | **Implemented FG-17b dashboard new panels** on top of merged FG-19 (frontend + backend, no API rewrite). Read-only **Core-area** projection (`/api/core/manifest` → `CorePage`: boundary health/globs/self-protection/denials + FG-12 change log + FG-16 trace). **GTS Centre** now renders merged FG-19 assignment — `/api/gts/graph` exposes each node's `assignee_user_id` + per-item `grants` (assignee/watchers, scoped by item_grants RLS) with `assignment={enabled:true,scheme:"per-user"}`, and `GtsCentrePage` shows assignee/watcher badges. **Agent webview** (`hermes_cli/webview.py` + `/api/webview/*` + `WebviewPage`): default-deny, session-scoped consent, read-only vs interactive, credentialed/destructive/off-scope **escalation → C6 approval**, C8 `InteractionLedger` tracing, per-user opaque UUID5 browser-profile dirs, over the existing `tools/browser_cdp_tool` CDP toolset; fixed a `NameError: uuid` in the escalation path. **Embedded Telegram** pane (`TelegramPage`) — doc-sanctioned native-chat fallback that reuses the existing one-brain `/chat` (TUI→`tui_gateway`→`AIAgent`) since the official web-widget can't embed under dashboard auth. **Tool link/icon registration** reused the existing dashboard-plugin manifest system (no new surface, Footprint-Ladder rung 1). Tests: `test_webview.py` (8) + real-FastAPI+Postgres `test_fg17b_dashboard_e2e.py` (7: default-deny/allow/escalate/approval/traces/isolation/Core/GTS+FG-19) + web vitest helpers; web lint(0 err)/typecheck/build green, `ruff` clean, FG-19 E2E still green. | Phase-2 req 17.0 FG-17b: land the new panels + consent-gated agent webview on the merged FG-19 base, tested on real paths, without duplicating chat/agent or growing the core waist. ECS system-test box + prod promotion remain separate gated steps owned by Leo. |
| 2026-07-04 | 13 | devin:3c64bcf2 (for Leo) | infra/prod | **Production cutover to the strong box.** `https://leolau.ai-and-i.io` now served by the 4/16 `hermes-systest` box (`47.83.199.25`) running current `develop` + FG-17 dashboard: Cloudflare DNS (A record repointed 8.217.86.90→47.83.199.25, DNS-only), Caddy + Let's Encrypt HTTPS → `127.0.0.1:9119`, password-gated dashboard, raw port 9119 not exposed. Telegram `@ai_prentice_systest_01_bot` now an always-on `hermes-gateway.service` on this box only (old box poller stopped → dual-poll conflict ended). All 10 targeted FGs (03/04/05/08/11/12/16/18/15/17) promoted to `app_prod` (9→25 tables, RLS, audit rows, backups + functional smoke). Old 2/4 box (`8.217.86.90`) stopped but intact for rollback. Operational note (no design change) captured in [`../SESSION-HANDOFF-2026-07-prod-cutover.md`](../SESSION-HANDOFF-2026-07-prod-cutover.md). Open follow-up: rotate the exposed dashboard owner password. | Leo: put the public product on the stronger hardware (D8), serve current code + FG-17, keep auth + single Telegram poller, retain rollback. |
| 2026-07-13 | 14 | devin:8cec0d47 (for Leo) | docs | **Corrected stale FG status headers + prod-cutover "remaining FGs" list to match `develop`.** FG-01/06/07/10/13/14/19 headers still read "Not started" despite being implemented + merged (PRs #12/#18/#20/#19/#9/#27/#35); updated each to "Implemented — merged to `develop`; ECS system-test/prod-promotion owner-gated". Fixed §8 item 6 of `SESSION-HANDOFF-2026-07-prod-cutover.md`, which had listed those FGs as un-written work: all 19 FGs are implemented + merged except FG-02 (on hold); only 10 were promoted to `app_prod` in the cutover, so the real remaining work for FG-01/06/07/09/10/13/14/19 is the owner-gated system-test + promotion, not code. Docs-only, no code/behavior change. | Keep the plan's status metadata truthful so the next agent/human doesn't re-implement already-merged feature groups. |
| 2026-07-11 | 16 | devin:8cec0d47 (for Leo) | Phase-3 plan / FG-20 | **Added Phase-3 plan: `agent-home` — a new mobile-first Next.js app** (`docs/.../feature-groups/FG-20-agent-home-nextjs-supabase.md`). Locks the three-tier architecture (**D16**): **Next.js UI + Python AI layer (`/api/*`) + Supabase (Postgres + Storage + RLS)**. `agent-home` becomes the user-facing face and hosts **all Phase-2 features** (GTS Centre + assignment, onboarding, Core-area view, interaction trace, one-brain chat, agent webview, tools) in a mobile-first/PWA UI; the existing `web/` stays as the operator/admin console. Uses a **BFF pattern** — the `agent-home` server holds the C1 principal context, proxies agent/authority ops to the Python API, and does server-side Supabase reads with the principal's RLS context (+ RLS-scoped Realtime); the browser never gets a privileged Supabase key or bypasses C1/C2/C6/C8. **No new contract** (new surface over C1/C2/C3/C5/C6/C7/C8/C9), no new core model tools, no new non-secret `HERMES_*` env vars, one-brain chat unchanged (cache-safe). Added a Phase-3 wave/parallelization plan (A skeleton+auth seam → B GTS/Core+trace/onboarding+tools → C chat/webview/comms+polish). Docs-only, no code. **Open decisions flagged for owner** (auth-identity bridge vs GoTrue; deploy on-box vs Vercel; `web/` fate; app location) — implementation gated on confirmation. | Leo: the current dashboard isn't mobile-friendly/is hard to use — build a purpose-built mobile Next.js face on the fixed Next.js + Python + Supabase stack and move all the new features into it. |
| 2026-07-11 | 15 | devin:8cec0d47 (for Leo) | naming | **Project renamed "ai-prentice" → "ai-prentice-4-all".** GitHub repo renamed `leolau/hermes-agent` → `leolau/ai-prentice-4-all` (GitHub auto-redirects old URLs). Product-name references updated across the master plan, per-FG docs, and hand-off docs; the per-FG cloud-agent-prompt repo slugs were repointed `leolau/hermes-agent` → `leolau/ai-prentice-4-all`. Left untouched: the upstream **Hermes** framework identifiers (`hermes_cli/`, the `hermes` CLI, `HERMES_HOME`, package imports, `/opt/data/hermes-agent` paths) and live-infra identifiers (Telegram bot `@ai_prentice_systest_01_bot`, ECS instance names `hermes-systest`/`ai-prentice`/`ai-prentice-agentdoc`, example test hostnames `ai-prentice-2`). Docs/naming-only, no behavior change. | Leo: standardise on the product name "ai-prentice-4-all" everywhere without breaking the running framework or production infra. |
| 2026-07-12 | 9 | devin:b9d4f38f (for Leo) | FG-18 / C9 | Implemented the unified GTS graph (`hermes_cli/gts.py` `GtsCentre`), **extending** FG-04 `goals` + FG-06 `tasks` (+ existing skills) in C3-routed `app_dev`/`app_prod` — additive `parent_*_id`/`level`/`priority`/`score`/`evaluation_method_ref` columns plus `skills_registry`, `task_goals`, `task_skills`, `evaluation_methods`; no new goal/task store. Authority is fail-closed (user-only top-level goals + evaluation methods; agent refused + audited via C8 `core_denied` + durable JSONL + optional C5 sink); scores are always computed, clamped 0–100, and roll up by priority weight; cycle-safe hierarchy; cache-safe surfacing (`render_gts_block`, never mutates the system prompt). Engine marked Core (`core_manifest.yaml` + `agent/core_boundary.py`). | Phase-2 req 18.0: publish C9 as one graph over the existing stores with a hard user/agent authority boundary and computed rollup scores, without breaking prompt-cache or the Core waist. ECS system-test box + prod promotion remain separate gated steps owned by Leo. |
| 2026-07-30 | 17 | devin:bbf60d09 (for Leo) | infra/mcp | **Writable design + cloud MCP access on the live box.** Canva/Vercel/Railway wired natively (open dynamic client registration) and AWS Knowledge without credentials; writable **Figma** is a skill bridging Claude Code, because Figma `403`s uncatalogued OAuth clients and a PAT can never write canvas. Durable **Railway** access is the `railway-cli` skill + account API token: Railway drops `offline_access`, rejects its own DCR-issued `client_secret` (`invalid_client`), and refuses device-code to self-registered clients, so its MCP token expires hourly with no refresh. Two Hermes bugs fixed along the way — endpoint preflight rejected POST-only MCP servers (PR #67), and the OAuth authorization URL lost every parameter when the authorization endpoint already carried a query (PR #68). Vercel's `buy_*` tools excluded from the agent's toolset. Operational note (no design change) in [`../SESSION-HANDOFF-2026-07-mcp-integrations.md`](../SESSION-HANDOFF-2026-07-mcp-integrations.md). | Leo: give ai-prentice write-capable design tools and real deployment/cloud reach, natively where a provider allows it and via a skill where it does not. |
| 2026-08-10 | 19 | devin (for Leo) | UI surface / D20 | **Locked D20: `agent-home` is the key and main UI; the `web/` dashboard is not.** All UI improvements — new screens, redesigns, UX/mobile/polish, new user-facing features — are done in `agent-home/` unless a request explicitly names another surface; `web/` stays the operator/admin console. Recorded as principle 0 in §2, as decision D20 in §1, in `README.md`, `AGENTS.md`, `agent-home/README.md`, `docs/design/architecture-design-number-one.md`, and in FG-20. Docs-only, no code change. | Leo: "the key and main UI is the agent-home, not dashboard. Unless explicitly specify, otherwise, all UI improvements are done in the agent-home." |
| 2026-08-04 | 18 | devin (for Leo) | FG-21 / memory / C2 | **Added FG-21: local semantic memory (layer 4), RAG, and shared recall across users.** Survey of the live box found the pgvector tier is real (HNSW, `vector 0.8.2`) but its embeddings are a hashing bag-of-words (not semantic), `memory_query` has never been called (write-only tier), and `_resolve_principal` never consults the `principals` table — so Telegram runs as `member 8756039695` while the dashboard runs as `leo_owner`, one human with two disjoint private memories and `channel_identities` empty. Plan: on-box embedding service (no text leaves the deployment) + model/dim versioning, automatic recall through the cache-safe `prefetch()` seam, identity enrolment as the prerequisite for cross-user access, audited role/grant-based elevation over C2 (reusing FG-19 `item_grants`), and a `rag_documents`/`rag_chunks` corpus with hybrid retrieval and citations. Docs-only; implementation gated on the open decisions in FG-21 §10. | Leo: local embeddings, must be semantic, use as the 4th memory layer, support RAG, and share memory across users of one instance with higher-privilege access by right |
| 2026-08-05 | 19 | devin (for Leo) | FG-23 / FG-22 / infra | **Added FG-23: move the memory visualizer onto `agent-home` (the phone).** No new data path — FG-22's `/api/memory/explorer/*` endpoints stay the single seam, consumed through the FG-20 BFF, so C1 principal scoping, C2 RLS on `memory_projection` and the C8 elevated-read audit are inherited rather than reimplemented (the plan explicitly forbids `agent-home`'s direct `pg` path for memory: correctly scoped by RLS but silently unaudited). The survey of the live box, not the repo, set the plan's shape: `agent-home` **is** already serving `home.leolau.ai-and-i.io` but from a **second checkout** (`/opt/data/agent-home-app`, at PR #62) that `deploy-hermes.sh` never updates, with a build dated 2026-07-27, as `root`, and with its unit outside `deploy_state.py`'s `hermes-*` capture glob — so a merged page would appear on the dashboard and never on the phone, undetected. Phase A0 (deployment path + state coverage) is therefore the feature, not preparation. Also decided: omit `mode` so the API resolves the memory tier's own schema (`AGENT_HOME_DATASTORE_MODE=prod` would render a healthy page reporting zero memories, since the live tier is `app_dev`), inline SVG instead of a charting dependency in a phone PWA, and a deterministic sampled `/projection` before Drive ingestion turns 37 dots into tens of thousands. Docs-only. | Leo: put the memory visualization in the agent home instead of the dashboard |
| 2026-08-10 | 20 | devin (for Leo) | Phase-6 plan / FG-24 / FG-25 / FG-26 / C10 | **Added Phase 6 — scaling one profile to hundreds of principals**, with three new FG docs and one new contract. **FG-24** makes curated memory layers 1–2 per-principal (shared block + per-user block): investigation of `agent/prompt_caching.py` (a single `system_and_3` layout with one breakpoint at the end of the whole system prompt) and `agent/system_prompt.py` (a per-session `Session ID`/timestamp line already at the tail of the `volatile` tier) established that **the system prompt is already unique per session**, so the long-standing "per-user curated memory would fragment the prompt cache" constraint does not hold — the invariant that matters (byte-stable *within* a conversation) is preserved by the existing frozen-snapshot mechanism. Also unblocks the shared 2200-char ceiling, already at 2029 with writes being refused. **FG-25** publishes **C10**, a group tier for C2 — hierarchical, multi-dimensional audiences with group-scoped admins — designed to serve a school (`cohort` dimension) and a medium business (`org` × `project` dimensions) with the same primitives; one brain, groups partition visibility only (D1). Key design points recorded there: read inheritance runs **up** while admin scope runs **down**; both are pre-expanded into GUCs so RLS never runs a recursive CTE per row; audit fires for reading *people*, not *teams*; and group elevation must compare **group** roles because in a correct deployment every team lead is instance-role `member` (instance-role comparison would refuse every elevation). **FG-26** replaces the flat `/members` page with a Users + Groups console and swaps admin-relayed temporary passwords for **hashed, single-use, 5-minute invitation links** (accounts created banned until activation). Rejected alternatives are recorded in the FG docs: one profile per class/team (that is the multi-tenant model D1 rejects), bulk `item_grants` as a stand-in for groups, intersection scopes, and GoTrue `admin/generate_link` for invitations. **Open decision flagged for the owner:** the "assign profile on user creation" requirement — a profile is an isolated brain, not a user attribute, so as stated it implies cross-profile identity (a separate FG). Docs-only, no code. | Leo: items 3 (per-user memory) and 4 (groups) are the show-stoppers for running one profile with hundreds — and in the general case thousands — of login users, covering both a school ("Students") and a medium business ("Engineers"), with multi-layered/multi-dimensional org topologies, an audited both-sides ledger for any read into a member's private data, and an administration UI for users, groups and invitation-based activation. |
| 2026-08-10 | 21 | devin (for Leo) | FG-27 / profile isolation / risks | **Added FG-27 — profile-scoped app-layer datastore isolation**, sequenced as a Phase-6 **prerequisite** (P6-0) ahead of FG-25/FG-26. Found while scoping multi-profile administration: `get_store()` hard-codes the app schema to `app_dev`/`app_prod`, so a profile's data is addressed by `(dsn, "app_prod")` and the **only** discriminator is a DSN string in that profile's `config.yaml`. `hermes profile create --clone` — the documented "start from my default" path — copies `config.yaml` verbatim (`_CLONE_CONFIG_FILES = ["config.yaml", ".env", "SOUL.md"]`, no mention of `dsn`), so the recommended workflow produces two "isolated" profiles sharing one `principals`/`memories`/`memory_projection`/`changes`/`item_grants` set. Nothing detects it (concurrent connections to one schema are what a legitimate single profile looks like; `initialize_supabase_app()` is `IF NOT EXISTS`) and nothing on disk shows it (`state.db`, `memories/`, `config.yaml`, `skills/` are genuinely separate). RLS does not help — it scopes rows correctly inside a database both profiles treat as their own. Three layers specified, cheapest first: a fail-closed `schema_owner` claim/verify marker, `--clone` no longer copying the app DSN blindly, and profile-derived schema names (`app_prod_<profile>`, default profile byte-identical) resolved through `get_active_profile_name()` so a multiplexed gateway turn scoped with `set_hermes_home_override()` picks the right schema with no caller changes. Prevention is prioritised over migration because interleaved rows carry no provenance column and **cannot** be disentangled automatically. Also recorded three new §8 risks: this footgun, the shared kanban board's missing identity namespace (`tasks.owner_user_id`/`visibility` use C2's vocabulary on a deliberately cross-profile board while `principals` is per profile), and the fact that any multi-profile administration console is a **deliberate reversal** of the "profiles are independent islands on purpose" intent recorded in `AGENTS.md` and must be argued in its own FG. Docs-only, no code. | Leo asked whether each profile has its own SQLite and Supabase DB. SQLite yes, by construction; Supabase only by convention — which surfaced the footgun. He then asked for it as a separate FG. Sequenced first because FG-25/FG-26 add exactly the identity-bearing tables (`groups`, `invitations`) whose cross-profile leakage would be most damaging: an invitation redeemable in the wrong profile is far worse than a merged memory row. |
