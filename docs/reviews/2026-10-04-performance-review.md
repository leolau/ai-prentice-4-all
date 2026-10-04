# ai-prentice-4-all — Performance Review

**Commit:** `6840c13d6` · **Branch:** `develop` · **Date:** 2026-10-04 · **Method:** I ran a parallel static review across 14 areas, then did a consolidation and re-verification pass against the checked-out source.

Unverified raw output of all 14 area reviewers (145 findings, many Medium/Low items not repeated here): [`2026-10-04-performance-raw-findings.md`](2026-10-04-performance-raw-findings.md). Companion report: [`2026-10-04-security-review.md`](2026-10-04-security-review.md).

## Executive summary

The codebase already does several things well: bounded agent caches, SQLite WAL, a preserved prompt prefix, tool-result spill, and incremental calendar sync. The main production risk is different. **Blocking or unbounded work keeps landing on hot paths of a single shared host.** The highest-leverage problems:

1. **The gateway runs synchronous persistence and SQLite work on its asyncio loop.** A slow disk or database operation therefore stalls adapters and streams (PERF-001).
2. **Every agent-home chat turn re-runs Supabase schema DDL and `DROP/CREATE POLICY`.** The cause is that a fresh `AIAgent` is built per turn, so the memory provider re-initialises on every turn. This takes `ACCESS EXCLUSIVE` locks on the shared memory and task tables (PERF-004). Separately, there is no connection pooling: each logical operation opens a new `asyncpg` connection (PERF-003).
3. **Transcripts are repeatedly loaded, decoded, deep-copied, and stringified in full.** As a result, latency and memory grow with conversation length and with every tool-loop iteration (PERF-002, PERF-005, PERF-014).
4. **Status and project endpoints do recurring heavy scans for every tab and every stream.** This includes a 100 ms blocking `psutil` sample inside an `async` handler (PERF-006, PERF-007, PERF-013).
5. **Ingestion and triage do slow work inside critical sections.** Triage holds a SQLite write transaction across Postgres and Telegram calls, and a lock failure drops the inbound WhatsApp message. Email batching re-reads the whole unbatched archive every cycle (PERF-009, PERF-010).

## Scope & methodology

This was a read-only static review of commit `6840c13d6`. I re-checked every High-rated finding against the source. Where findings overlapped across areas, I merged them, and I re-rated anything whose reachability in production could not be shown. I did not touch any production host, third-party service, or live deployment, and ran no load or penetration tests.

| Area | Coverage |
|---|---|
| agent-core | Conversation conversion, caching, replay, persistence, request accounting |
| tools-exec | Checkpoints, file/process/code execution |
| tools-net-skills | MCP client, URL safety, skills |
| gateway-core | Session routing, inbound queues, persistence |
| gateway-platforms | WhatsApp, API server, app delivery, streaming |
| cli-http-api | Project/status/session APIs, SSE producers |
| cli-core | Datastore, access store, credentials, Kanban DB |
| agent-home-api | BFF routes, uploads, status aggregation |
| agent-home-ui | Streaming render, polling, media |
| web-tui-desktop | Dashboard, PTY, TUI gateway |
| plugins-cron | Memory (pgvector), achievements, Kanban plugin, scheduler |
| custom-integrations | Email, Calendar, WhatsApp batcher, triage |
| mcp-acp | `mcp_serve` EventBridge, goal tools |
| infra-supply-chain | Embedding service, deploy script, systemd |

## Summary table

| ID | Severity | Area | Title | Effort |
|---|---|---|---|---|
| PERF-001 | High | gateway-core | Synchronous session persistence and SQLite writes block the gateway event loop | M |
| PERF-002 | High | agent-core / gateway-core | Full conversation history is repeatedly loaded and decoded on hot paths | L |
| PERF-003 | High | cli-core / plugins-cron / mcp-acp | Every Supabase operation opens a fresh `asyncpg` connection (no pool) | M |
| PERF-004 | High | plugins-cron / cli-core | Per-chat-turn schema DDL and RLS policy recreation on shared tables | S |
| PERF-005 | High | agent-core | Chat-completions conversion deep-copies the whole transcript on most provider calls | S |
| PERF-006 | High | cli-http-api | Project listing scans all projects with per-project query fan-out | L |
| PERF-007 | High | cli-http-api / agent-home-api | Status strip fans out to five endpoints per tab every 30 s, one blocking the loop | M |
| PERF-008 | High | plugins-cron / infra | Memory recall/task discovery run blocking per-turn thread + event loop + connection setup | L |
| PERF-009 | High | custom-integrations | Triage holds a SQLite write transaction across Postgres/Telegram calls; batcher drops messages on lock | S |
| PERF-010 | High | custom-integrations | Email batcher re-reads all unbatched rows (`SELECT *`) each cycle with quadratic grouping | S |
| PERF-011 | High | plugins-cron | Achievements `/rescan` runs a multi-minute scan inline on the shared web event loop | S |
| PERF-012 | Medium | mcp-acp | EventBridge reloads full transcripts of every session whenever `state.db` changes | M |
| PERF-013 | Medium | cli-http-api / plugins-cron | Each live project/Kanban stream polls SQLite independently; Kanban requests force re-migration | M |
| PERF-014 | Medium | agent-core | Request-size accounting stringifies the full transcript several times per iteration | M |
| PERF-015 | Medium | agent-home-api | Multipart uploads are buffered multiple times with no finite default cap | M |
| PERF-016 | Medium | agent-home-ui | Streaming Markdown is reparsed (GFM + raw + sanitize) for every delta | M |
| PERF-017 | Medium | tools-net-skills | One MCP `_rpc_lock` serialises all calls to a server across sessions, all transports | S |
| PERF-018 | Medium | infra-supply-chain | RAG ingestion embeds one chunk per blocking HTTP request | S |
| PERF-019 | Medium | cli-http-api / agent-home / mcp-acp | Session-messages read paths return/load the entire transcript with no window or size cap | M |
| PERF-020 | Medium | cli-http-api | Project routes eagerly fan out across every profile with two uncached Supabase connections each | M |
| PERF-021 | Medium | custom-integrations | Calendar triage makes one LLM call per expanded recurring instance, oldest (already past) first | M |
| PERF-022 | Low | agent-core | Prompt-cache marker injection deep-copies the full API message list on every cached request | S |

## Detailed findings

### High

#### PERF-001 — Synchronous session persistence and SQLite writes block the gateway event loop
- **Severity:** High · **Effort:** M · **Confidence:** High
- **Locations:** `gateway/session.py:1128-1160`, `gateway/session.py:1475-1520`, `gateway/session.py:1323-1349`, `gateway/run.py:10573`, `hermes_state.py:1355-1420`
- **Description:** `SessionStore._save()` serialises the whole routing map with `json.dump(indent=2)`, then calls `fsync` and atomically replaces `sessions.json`. `get_or_create_session()` calls `_save()` while holding the store lock, and it also probes `state.db` synchronously (`_is_session_ended_in_db` → `db.get_session`). `load_transcript()` at `run.py:10573` runs a synchronous full-history query. All of this runs on the gateway's asyncio loop. That loop shares `state.db` with agent workers, which use `BEGIN IMMEDIATE`, a one-second busy timeout, retry sleeps, and periodic WAL/FTS maintenance. The effect is that any lock wait or `fsync` stall freezes every adapter at once.
- **Recommendation:** Put `SessionStore` and `SessionDB` behind `asyncio.to_thread`, or behind a dedicated single-thread DB executor. Make `sessions.json` persistence write-behind and debounced, with a flush on shutdown. Move WAL checkpoints and FTS optimisation to a maintenance task.

#### PERF-002 — Full conversation history is repeatedly loaded and decoded on hot paths
- **Severity:** High · **Effort:** L · **Confidence:** High
- **Locations:** `hermes_state.py:4187-4294`, `gateway/run.py:10573`, `gateway/platforms/api_server.py:1545-1561`, `hermes_state.py:3865-3897`
- **Description:** `get_messages_as_conversation()` runs `fetchall()` over every active message in the session lineage, JSON-decodes the tool and reasoning fields, and builds a new list. The gateway reloads this for every inbound turn, even when a cached agent already holds a coherent history (`_agent_cache`, `run.py:2770`). The API history handlers also load full transcripts before applying limits. For long, tool-heavy sessions, the per-turn cost of I/O, allocation, and serialisation grows linearly with total history.
- **Recommendation:** Keep a message-ID coherence cursor on cached agents and load only the delta. Add bounded, cursor-paged history APIs that return the newest messages first. Select display columns only, and fetch large tool payloads on demand.

#### PERF-003 — Every Supabase operation opens a fresh `asyncpg` connection (no pool)
- **Severity:** High · **Effort:** M · **Confidence:** High
- **Locations:** `hermes_cli/datastore.py:93-120`, `hermes_cli/datastore.py:414`, `plugins/memory/supabase_pgvector/store.py:435-441` (and the ~10 `conn = connection or await self._connect()` sites at `store.py:459-1120`), `hermes_cli/access.py:575-587`
- **Description:** `SupabaseAppStore.connect()` calls `asyncpg.connect()` for each logical operation, with no pool anywhere. Each pgvector connection then runs `_prepare_connection()`, which executes `CREATE SCHEMA IF NOT EXISTS`, `CREATE EXTENSION IF NOT EXISTS vector`, a `pg_extension` lookup, `set_config(search_path)` and `set_type_codec` before any real query runs. That is 4–5 round-trips of setup per operation, on top of TLS and auth. Access-store calls repeat `initialize_access()` DDL; there are 15 `await initialize_access` sites in `access.py`. Separately, `claim_schema_owner()` clears the global `_verified_schemas` cache (`datastore.py:414`), which defeats the verification cache.
- **Recommendation:** Use one bounded `asyncpg` pool per DSN and schema, and use the pool's `init=` hook to register the vector codec and `search_path` once per physical connection. Keep `CREATE SCHEMA/EXTENSION` in migration code only. Invalidate only the affected key in `_verified_schemas`.

#### PERF-004 — Per-chat-turn schema DDL and RLS policy recreation on shared tables
- **Severity:** High · **Effort:** S · **Confidence:** High
- **Locations:** `gateway/session_chat.py:118-136` (`run_session_turn_sync` → `build_session_agent` → new `AIAgent`), `hermes_cli/web_server.py:10510-10540`, `agent/agent_init.py:1336-1397`, `plugins/memory/supabase_pgvector/__init__.py:345-372`, `plugins/memory/supabase_pgvector/__init__.py:125-140`, `plugins/memory/supabase_pgvector/store.py:452-492`, `hermes_cli/task_registry.py:380-391`, `hermes_cli/access.py:640-665`, `hermes_cli/goal_management.py:153-165`, `mcp_serve.py:1159-1163`
- **Description:** The agent-home chat endpoints build a brand-new `AIAgent` for every turn. Each build calls `MemoryManager.initialize_all()`, which makes the `supabase_pgvector` provider run `_initialize_stores()`: `memory_store.initialize()` plus `registry.initialize()`, and `initialize_changes()` in prod mode. These execute the full table DDL and call `apply_scope_rls()` several times. Each `apply_scope_rls()` issues `ALTER TABLE … ENABLE/FORCE ROW LEVEL SECURITY; DROP POLICY IF EXISTS …; CREATE POLICY …`. Two things make this a real latency problem rather than just wasted work:
  - Postgres takes `ACCESS EXCLUSIVE` locks for policy DDL. So each chat turn briefly blocks, and queues behind, every concurrent reader and writer of the memory, task, and discovery-proposal tables.
  - Each policy change also invalidates cached plans and churns the catalog.

  The same pattern appears in the MCP goal tools: every `goal_*` call runs `service.initialize()`, which re-applies RLS on four stores.
- **Recommendation:** Move schema creation and RLS installation into a startup/migration step that is version-gated (for example a `schema_version` row), and skip it when the version matches. Cache "initialised" status per process and per DSN/schema so per-turn agent construction does no DDL. Use `CREATE POLICY` only when the policy is missing or its definition hash has changed.

#### PERF-005 — Chat-completions conversion deep-copies the whole transcript on most provider calls
- **Severity:** High · **Effort:** S · **Confidence:** High
- **Locations:** `agent/transports/chat_completions.py:164-198`, `agent/transports/chat_completions.py:279`, `agent/tool_dispatch_helpers.py:361-385`, `hermes_state.py:4231-4236`
- **Description:** `convert_messages()` falls back to `copy.deepcopy(messages)` whenever a message carries `tool_name` or `timestamp`. Those fields are set deliberately by `make_tool_result_message()` and `get_messages_as_conversation()`, so the fast path is effectively never taken once a session has used a tool or been restored. The copy walks the entire history, including large tool outputs and base64 images, just to delete a handful of metadata keys. It does this on every provider iteration.
- **Recommendation:** Strip transport-only keys during the existing shallow API-copy step (`conversation_loop.py:796-836`), or rebuild only the affected top-level dicts. Keep nested content by reference.

#### PERF-006 — Project listing scans all projects with per-project query fan-out
- **Severity:** High · **Effort:** L · **Confidence:** High
- **Locations:** `hermes_cli/projects_api.py:597-700`, `hermes_cli/projects_db.py:932-940`
- **Description:** `_list_sync` loads every project and sorts the whole set in Python. It then walks candidates until the page is full, and each candidate can trigger separate membership, profile, output, delivery, link, member, board-rollup, and health queries. The work runs in a thread via `asyncio.to_thread`, but `limit` caps only the response, not the scan or the N+1 query volume. `/api/status/summary` calls this endpoint with `limit=100` every 30 s per tab (see PERF-007).
- **Recommendation:** Push status, archive, search, access, health, ordering, and keyset pagination into SQL. Fetch only the page's IDs, then batch-load related data with `IN (…)` queries, and add indexes that match the dominant filter and ordering.

#### PERF-007 — Status strip fans out to five endpoints per tab every 30 s, one blocking the loop
- **Severity:** High · **Effort:** M · **Confidence:** High
- **Locations:** `agent-home/src/components/ui/StatusStrip.tsx:8`, `agent-home/src/components/ui/StatusStrip.tsx:97-115`, `agent-home/src/app/api/status/summary/route.ts:12-40`, `hermes_cli/web_server.py:2469-2516`, `hermes_cli/web_server.py:15636-15688`, `agent/insights.py:102`, `agent/insights.py:190-335`
- **Description:** Each visible tab refreshes every 30 s, and also on every `focus` or `visibilitychange`. Each refresh fans out to `systemStats`, `usageAnalytics(30)`, `capacity`, `cronJobs`, and `projects(limit=100)`. Three of those are costly:
  - `get_system_stats` is `async def` and calls `psutil.cpu_percent(interval=0.1)`, so it **blocks the Python event loop for 100 ms per call per tab**.
  - Usage analytics runs `InsightsEngine.generate(days=30)`, which loads 30 days of sessions and JSON-parses `tool_calls`, even though the strip only reads totals.
  - `running_cards` is derived from the 100-project listing (PERF-006).

  None of this is shared across tabs or users.
- **Recommendation:** Add a short-TTL (10–30 s) server-side cache for the summary, keyed by profile. Expose a cheap totals-only aggregate and a direct running-card count. Use a background CPU sampler or `cpu_percent(interval=None)`. Debounce the focus and visibility triggers.

#### PERF-008 — Memory recall/task discovery run blocking per-turn thread + event loop + connection setup
- **Severity:** High · **Effort:** L · **Confidence:** High
- **Locations:** `plugins/memory/supabase_pgvector/__init__.py:380-416`, `plugins/memory/supabase_pgvector/__init__.py:589-613`, `plugins/memory/supabase_pgvector/embedding.py:200-212`, `hermes_cli/task_registry.py:322-345`
- **Description:** `_run_async()` spawns a new `threading.Thread`, runs `asyncio.run()` in it, and **joins it synchronously**. Every turn does this at least twice, before the model call:
  - `on_turn_start` runs task discovery: write `intent_signal`, embed, then a top-100 vector query.
  - `prefetch` runs recall.

  Each coroutine opens fresh connections with the per-connection setup described in PERF-003. The embedder uses blocking `urllib.request.urlopen` with a 20 s timeout. So the cost is added straight onto time-to-first-token, and a slow embedding service or database stalls the turn.
- **Recommendation:** Run one long-lived background loop per process and submit work with `run_coroutine_threadsafe`, backed by a pool. Run discovery and recall concurrently, or overlap them with prompt assembly. Use an async HTTP client for embeddings. Use one embedding call for both discovery and recall when the text is the same.

#### PERF-009 — Triage holds a SQLite write transaction across Postgres/Telegram calls; batcher drops messages on lock
- **Severity:** High · **Effort:** S · **Confidence:** High
- **Locations:** `custom/shared/triage_handlers.py:64-150`, `custom/shared/triage_handlers.py:380-392`, `custom/shared/todo_registration.py:170-215`, `custom/whatsapp/triage_agent.py:190-229`, `custom/email/email_triage_agent.py:223-271`, `custom/calendar/calendar_triage_agent.py:233-285`, `custom/whatsapp/batcher.py:58-62`, `custom/whatsapp/batcher.py:265-280`
- **Description:** `process_result()` dispatches handlers in result-key order. The `tasks`/`notes` handlers `INSERT` into SQLite, which opens an implicit write transaction. The `todos` handler then calls `asyncio.run()` for Supabase registration and may `push_todo` to Telegram. `db.commit()` runs only after all handlers return. During those network calls the shared messaging database's write lock is held. Meanwhile the WhatsApp batcher uses `busy_timeout=5000`, and on any insert exception it logs and `return`s. **An inbound message is silently dropped whenever triage holds the lock for more than 5 s.** `PRODUCTION.md` already reports "occasional lock warnings" on `credits.db`.
- **Recommendation:** Commit local writes before any network side effect, and move registration and notification to an outbox processed after commit. In the batcher, retry `database is locked` with bounded backoff instead of returning.

#### PERF-010 — Email batcher re-reads all unbatched rows (`SELECT *`) each cycle with quadratic grouping
- **Severity:** High · **Effort:** S · **Confidence:** High
- **Locations:** `custom/email/email_batcher.py:33-67`, `custom/email/email_batcher.py:125-184`
- **Description:** On each cycle, `get_unbatched_emails()` runs `SELECT * FROM email_messages WHERE batch_id IS NULL ORDER BY received_at`. I found no partial index on `batch_id`, and the query pulls full bodies into Python dicts. `group_emails()` then, for each email that has a thread ID, scans every email in every existing group to find a thread match, which is O(n²) in the backlog. A backlog after an outage, or after a triage failure that leaves rows unbatched, makes each cycle progressively more expensive.
- **Recommendation:** Add `CREATE INDEX … ON email_messages(received_at) WHERE batch_id IS NULL`. Select only `id, from_addr, account_id, thread_id, received_at`, and fetch bodies only for emails assigned to a batch. Keep a `thread_id → group key` dict.

#### PERF-011 — Achievements `/rescan` runs a multi-minute scan inline on the shared web event loop
- **Severity:** High · **Effort:** S · **Confidence:** High
- **Locations:** `plugins/hermes-achievements/dashboard/plugin_api.py:950-985`, `plugins/hermes-achievements/dashboard/plugin_api.py:1037-1039`
- **Description:** The handler is `@router.post("/rescan") async def rescan()`, and it calls `evaluate_all(force=True)`. That function's docstring says it "block[s] the caller" and that "cold scans on 8000+ session databases take minutes". Because the route is `async def`, the scan runs on the dashboard's event loop. Every other request on that process stalls until it finishes, including chat SSE, approvals, and agent-home BFF calls.
- **Recommendation:** Kick off the existing background scan, return `202`, and let the client poll scan status. As a minimum fix, use `await asyncio.to_thread(evaluate_all, force=True)` or make the route a plain `def`.

### Medium

#### PERF-012 — EventBridge reloads full transcripts of every session whenever `state.db` changes
- **Severity:** Medium (downgraded: only runs inside `hermes mcp serve`; an mtime/size guard skips idle ticks) · **Effort:** M · **Confidence:** High
- **Locations:** `mcp_serve.py:324-325`, `mcp_serve.py:478-580`
- **Description:** The bridge polls every 200 ms. Whenever `state.db` changes, which is any write by any session, `_poll_once()` calls `db.get_messages(session_id)` with no limit for **every** session in the index, then filters by timestamp in Python. During an active conversation this happens about 5 times per second, at a cost of O(total messages across all sessions). The queue trims with `list.pop(0)` while holding the lock.
- **Recommendation:** Keep a per-session `max(id)` cursor and query `WHERE session_id=? AND id>? ORDER BY id LIMIT ?`. Better still, run one `WHERE id > ?` query across sessions. Seed the cursors at the current maxima. Use `deque(maxlen=QUEUE_LIMIT)`.

#### PERF-013 — Each live project/Kanban stream polls SQLite independently; Kanban requests force re-migration
- **Severity:** Medium (downgraded: polling is offloaded to threads and bounded by `LIMIT 200`) · **Effort:** M · **Confidence:** High
- **Locations:** `hermes_cli/projects_api.py:3029`, `hermes_cli/projects_api.py:3038-3068`, `hermes_cli/projects_api.py:3149-3168`, `plugins/kanban/dashboard/plugin_api.py:120-130`, `plugins/kanban/dashboard/plugin_api.py:2175-2209`, `hermes_cli/kanban_db.py:1881-1905`
- **Description:** Every connected project or card stream ticks every second (`_ROW_TICK_SECONDS = 1.0`), and every Kanban WebSocket polls `task_events` in a loop. Each tick opens and closes its own connection. The cost therefore grows linearly with open tabs and competes with board writes. In addition, the Kanban plugin's `_conn()` calls `kanban_db.init_db()` on **every request**. `init_db` deliberately discards the init cache and re-runs the full schema and migration pass.
- **Recommendation:** Run one tailer per board or project per process and fan its output out to subscriber queues. Replace `init_db` in `_conn()` with the cached `connect()` auto-init.

#### PERF-014 — Request-size accounting stringifies the full transcript several times per iteration
- **Severity:** Medium · **Effort:** M · **Confidence:** High
- **Locations:** `agent/conversation_loop.py:962-967`, `agent/model_metadata.py:2216-2312`
- **Description:** On every provider iteration the code computes `sum(len(str(msg)))`, then builds shadow dicts for token estimation, then estimates again with the tool schemas included. That is several full-history stringify passes on top of PERF-005's deep copy.
- **Recommendation:** Cache an estimate per message, maintain running totals, and cache the tool-schema estimate.

#### PERF-015 — Multipart uploads are buffered multiple times with no finite default cap
- **Severity:** Medium · **Effort:** M · **Confidence:** High
- **Locations:** `agent-home/src/app/api/chat/upload/route.ts:43-76`, `agent-home/src/app/api/projects/[slug]/files/upload/route.ts:107-138`, `agent-home/src/lib/chat/upload-limit.ts:11-14`
- **Description:** `request.formData()` followed by `file.arrayBuffer()` holds multiple full copies of each upload in memory. The size limit is effectively unlimited unless an environment variable sets one. One large upload can push the shared Node process into memory pressure.
- **Recommendation:** Set a finite default limit, reject oversized uploads early using `Content-Length`, and stream hashing and storage writes.

#### PERF-016 — Streaming Markdown is reparsed (GFM + raw + sanitize) for every delta
- **Severity:** Medium · **Effort:** M · **Confidence:** High
- **Locations:** `agent-home/src/components/chat/RichText.tsx:25-55`, `agent-home/src/components/chat/ChatPane.tsx:396-420`, `agent-home/src/components/chat/ChatPane.tsx:519-525`, `agent-home/src/lib/chat/stream.ts:253-266`
- **Description:** Every SSE delta updates state, which re-renders `<Markdown>` over the accumulated text. The plugin arrays and the `components` object are recreated inline on every render. Parse cost is O(response length) per delta, so a full response costs O(n²), and the frequent updates also drive scroll and layout work.
- **Recommendation:** Batch deltas per animation frame or every 50–100 ms. Hoist the plugins and `components` to module scope. Render in-flight text simply and do the full Markdown parse once on completion. Memoise finished messages.

#### PERF-017 — One MCP `_rpc_lock` serialises all calls to a server across sessions, all transports
- **Severity:** Medium · **Effort:** S · **Confidence:** Medium
- **Locations:** `tools/mcp_tool.py:1462-1468`, `tools/mcp_tool.py:3458-3466`
- **Description:** `_rpc_lock` (an `asyncio.Lock`) is held for each entire `call_tool`. The comment says it is applied to HTTP transports only for "conservative per-server ordering". The MCP client is process-wide, so one slow tool call blocks every other session's calls to that server, along with list-tools refreshes. This is true even for servers flagged `supports_parallel_tool_calls`.
- **Recommendation:** Keep the mutex for stdio. Use a bounded semaphore for HTTP and for servers flagged parallel-safe, and serialise only the refresh operation.

#### PERF-018 — RAG ingestion embeds one chunk per blocking HTTP request
- **Severity:** Medium · **Effort:** S · **Confidence:** High
- **Locations:** `plugins/memory/supabase_pgvector/rag.py:380-382`, `plugins/memory/supabase_pgvector/embedding.py:200-212`, `scripts/embedding_server.py:108-115`
- **Description:** Inside an `async` method, `RagStore.ingest` runs `[self._memory.embedder.embed(chunk.text) for chunk in chunks]`. That is one blocking `urllib` request per chunk, made on the event loop. On the server side, each request takes the model lock separately. `embed_batch()` already exists and the server supports batching.
- **Recommendation:** Call `embed_batch` on bounded groups, run it via `asyncio.to_thread`, and keep groups small so interactive queries can interleave.

### Added in the second verification pass

The first consolidation pass dropped these high-rated area findings. A dedicated reviewer then re-checked each one against the code.

#### PERF-019 — Session-messages read paths return/load the entire transcript with no window or size cap
- **Severity:** Medium · _Verification: partially-confirmed_

**Evidence**
- `hermes_cli/web_server.py:10307-10318`: `GET /api/sessions/{session_id}/messages` is an `async def` that opens a fresh `SessionDB` (`_open_session_db_for_profile`, `:10141-10153`), calls `db.get_messages(sid)` and returns every row in one JSON body. It takes no `limit`, cursor or byte-budget parameter. The SQLite read and decode run synchronously on the dashboard event loop.
- `hermes_state.py:3865-3899`: `get_messages` runs `SELECT * FROM messages WHERE session_id = ? AND active = 1 ORDER BY id` with no LIMIT. It calls `fetchall()`, then JSON-decodes `content` (`_decode_content`, `:3548-3559`) and `tool_calls` for every row. `SELECT *` also pulls the reasoning columns and large tool outputs.
- `agent-home/src/app/chat/page.tsx:83-85`: the server-side render of `/chat` loads the full transcript of the selected session through `client.sessionMessages` (`agent-home/src/lib/api/client.ts:509-512`).
- `agent-home/src/components/chat/ChatPane.tsx:319-353` (`openConversation`, fetch at `:330-333`): every conversation switch re-fetches the full transcript via the BFF (`agent-home/src/app/api/chat/messages/route.ts:26`).
- `ChatPane.tsx:361-377` (`reloadTranscript`) fetches it again after the idle watcher (`:210-239`, every `ACTIVE_WATCH_MS = 10_000` at `:87`) attaches to an externally started turn (`:230`). A turn sent from the pane itself does not trigger a re-fetch.
- `ChatPane.tsx:90-92` (`visible`): the client keeps only `user`/`assistant` rows. All `tool` rows, `tool_calls` and reasoning are read, decoded, serialized, proxied through Next.js and parsed in the browser, then thrown away.
- `mcp_serve.py:726-772` (`messages_read`, `get_messages` at `:755`): loads the full session, then filters and slices `filtered[-limit:]` (limit capped at 200, content truncated to 2000 chars). The output is bounded, but the read and decode scale with the whole history.
- `mcp_serve.py:783-826` (`attachments_fetch`, `get_messages` at `:811`): loads the full session to find one message by id.
- No mitigation found: no pagination helper is used on these paths. `get_messages_around` (`hermes_state.py:3901`) has a windowed shape, but only `session_search` uses it. Caddy compresses the response but applies no size limit.

**Impact**
- Any authenticated agent-home user opening, or switching back to, a long-lived conversation such as the lead chat pays costs that grow with total history, mostly from tool output: a large SQLite read, a Python decode, JSON encoding, two network hops (API to BFF to browser) and browser parse/memory. Chat open gets slower over time and can show as slow or janky on mobile.
- Because the handler is `async def` with blocking SQLite, a large transcript load stalls every other request on the `hermes-dashboard` process for its duration, including status-strip and SSE producers.
- The MCP tools have the same root cause, but `mcp_serve.py` runs only under `hermes mcp serve` (`hermes_cli/mcp_config.py:934`). It is not a production systemd unit (`docs/deployment/PRODUCTION.md` section 4.2; the `app-mcp` unit is `app_mcp.server`), so its production impact is low.
- Preconditions: the conversation has accumulated many turns or large tool outputs. No attacker is needed; normal use triggers it.

**Fix**
- Add `SessionDB.get_messages_page(session_id, limit, before_id=None, roles=None)`: `SELECT id, role, content, timestamp, tool_name, ... FROM messages WHERE session_id=? AND active=1 AND (? IS NULL OR id < ?) [AND role IN (...)] ORDER BY id DESC LIMIT ?`, reversed in Python. Add `get_message(session_id, message_id)` for single lookups.
- Endpoint: `limit` (default about 100, server max about 500), `before_id` and `roles=user,assistant`. Return `{messages, next_before_id, has_more}`. Make it a plain `def`, or wrap it in `run_in_threadpool`, so SQLite does not block the loop.
- agent-home: request only `user`/`assistant` rows with the newest window in `page.tsx`, `openConversation` and `reloadTranscript`. Load older pages on scroll-up. `reloadTranscript` can fetch only `after_id=<last persisted id>`.
- Truncate or omit large `content` and tool payloads in list responses; serve full bodies on demand.
- `mcp_serve.py`: have `messages_read` use the paged method with a role filter, and `attachments_fetch` use `get_message`.

Effort: M · Confidence: high

#### PERF-020 — Project routes eagerly fan out across every profile with two uncached Supabase connections each
- **Severity:** Medium · _Verification: confirmed_

**Evidence**
- `hermes_cli/projects_api.py:105-125` `_enrolled_profiles` calls `console_scope.enrolled_profiles` with a factory that builds a fresh `PrincipalStore(_comms_app_store())` per profile; no cache of any kind.
- Called unconditionally at `hermes_cli/projects_api.py:208` (`_require_read`, which `_require_write` also goes through, so every `/{slug}/...` read and write: 58 call sites), `:694` (`GET ""` list) and `:870` (`GET /doctor`).
- `hermes_cli/projects_api.py:149-161` `_can_read` only looks at `enrolled` after the instance-admin, creator and member-role checks fail. In production the dashboard login (`admin`) is aliased to the owner (docs/deployment/README.md), so the fan-out result is computed and then never used on the main path.
- `hermes_cli/console_scope.py:204-241` loops over `get_profile_registry()` one profile at a time. `:100-111` `_principal_for_subject` awaits `resolve_alias` and then `get`.
- `hermes_cli/access.py:1175-1197` and `:1554-1577`: each of those opens its own `asyncpg.connect` (`hermes_cli/datastore.py:93-119`, no pool) and runs `initialize_access` (`access.py:575-587`). That means `ensure_app_schema` (SELECT search_path, `CREATE SCHEMA IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS schema_owner`, `INSERT ... ON CONFLICT`) plus the 8-statement `ACCESS_SCHEMA_SQL`, which includes `ALTER TABLE principals ADD COLUMN IF NOT EXISTS` and so takes an ACCESS EXCLUSIVE lock on every call. `get` also runs `_channels_for`.
- `hermes_cli/datastore.py:409` `claim_schema_owner` calls `_verified_schemas.clear()` on every call, so `verify_schema_owner` (`:477-506`, 2 queries) on the next connect is never served from cache.
- In total, each profile costs 2 TLS connection setups and about 17 serial queries. This comes on top of the same 2 connections `_comms_resolve_principal` (`hermes_cli/web_server.py:3183-3184`) already opened for the caller.
- `hermes_cli/profile_registry.py:93-121` `get_profile_registry` calls `list_profiles()` (`hermes_cli/profiles.py:900-960`) synchronously on the event loop. For every profile it reads config and distribution metadata, probes the gateway PID, and computes a skills-dir signature, all on each request.
- Polling drivers: `agent-home/src/app/api/status/summary/route.ts:23` (projects list, `StatusStrip.tsx` `POLL_MS = 30_000`, also on focus) and `agent-home/src/components/projects/ProjectsList.tsx:8,119` (30 s). Both run per open tab.

**Impact**
- Every project request, plus each tab's 30 s status-strip and project-list polls, pays about (2N+2) serial Postgres connection setups before any project work starts. N is the number of profiles on the box. The response time grows linearly with N.
- The registry walk is synchronous filesystem work, so it stalls the shared dashboard event loop for all other requests.
- Every connection repeats the access DDL, including the ACCESS EXCLUSIVE `ALTER TABLE principals`, so concurrent polls queue on the principals table with gateway principal lookups.
- Preconditions: Supabase configured (production). Any authenticated dashboard user can trigger it just by keeping tabs open. When N is small (1-3 profiles, as documented for FG-27), the cost is a few hundred ms per request, not seconds. That is why this is Medium, not High.
- Overlap: the per-operation connect/DDL cost is the root cause of PERF-003/PERF-004. What is distinct here is the eager, uncached, sequential N-profile fan-out on every project route.

**Fix**
- Make the enrolment set lazy. Pass a callable or `functools.cache`'d coroutine into `_can_read` and only await it when the admin, creator and role checks fail and `visibility == "shared"`. That removes the fan-out completely for owner/admin and member callers.
- Cache `_enrolled_profiles(user_id)` in a process-local TTL dict (30-60 s). Invalidate it from the `PrincipalStore` mutators (`enroll`, `set_active`, `remove`, alias changes).
- In `console_scope._principal_for_subject`, open one connection per profile and pass it to both `resolve_alias(..., connection=conn)` and `get(..., connection=conn)`. Better still, add a single `SELECT p.active FROM principals p LEFT JOIN principal_aliases a ON a.user_id = p.user_id WHERE p.user_id = $1 OR a.alias_subject = $1` and skip `_channels_for`, which enrolment does not need.
- Run the per-profile lookups concurrently, e.g. `asyncio.gather` with each task in its own `profile_runtime_scope`, since contextvars copy per task. Once PERF-003's pool exists, reuse it.
- Make `initialize_access` run once per (dsn, schema) per process: memoise it like `_verified_schemas`, and stop `claim_schema_owner` from clearing that cache when the claim is a no-op.
- Build the profile list for this path from `profiles_to_serve(multiplex=True)` (a cheap directory scan), not `get_profile_registry()`/`list_profiles()`. If it stays, move it off the loop with `asyncio.to_thread`.

Effort: M · Confidence: high

#### PERF-021 — Calendar triage makes one LLM call per expanded recurring instance, oldest (already past) first
- **Severity:** Medium · _Verification: partially-confirmed_

**Evidence**
- `custom/calendar/calendar_poller.py:171` requests `singleEvents: True` on every request shape, so Google returns each occurrence of a recurring series as its own item.
- `custom/calendar/calendar_poller.py:187-192`: a full sync (first run, new account, or `last_synced` older than 7 days) covers `now - 30d` to `now + 365d`. A weekly series comes back as about 56 rows and a daily one as about 280. The delta branch (`:177-186`, `updatedMin`) has no time bound, so a new or edited series also comes back fully expanded.
- `custom/calendar/calendar_poller.py:291-315` inserts every new instance with `triaged = 0`. Nothing groups instances by `recurring_event_id` (stored at `:309`) or skips instances whose `end_time` has already passed.
- `custom/calendar/calendar_triage_agent.py:125-138` `get_untriaged_events` selects `WHERE triaged = 0 AND status = 'confirmed' ORDER BY start_time ASC LIMIT 10`, so the 30 days of past instances are triaged first.
- `custom/calendar/calendar_triage_agent.py:149-216` `triage_event` makes one `call_deepseek` per row. Each call re-reads every skill file from disk (`:151`) and sends about 9.6 KB of skill text (`custom/skills/calendar-triage/*.md`) plus the event.
- `custom/calendar/calendar_triage_agent.py:319-347`: each cycle handles 10 events in series (DeepSeek call with a 30 s timeout, plus `time.sleep(1)`), then sleeps `triage_poll_interval` (default 30 s).
- `custom/shared/triage_handlers.py:40-57, 102-149, 251-264`: each instance's result separately runs `remember_facts`, `register_todo` (keyed by the instance's `google_event_id`) and the importance stamp. Identical occurrences can therefore produce repeated memory facts and to-dos.
- Production scale: `plans/2026-10-03-calendar-delta-sync-and-aux-entitlement-fallback.md:8-11` and `calendar_poller.py:249` record about 770 events across 3 Google accounts.

**Impact**
- Onboarding an account, or the first sync after a DB reset, queues hundreds of LLM calls per account. At the production scale above that is about 770 calls of roughly 3k input tokens each, mostly recurring duplicates, and about 30 of the 395 days in the window are already over.
- The queue drains at about 10 events per 45-80 s, so the backlog takes about 1-2 h. A meeting created during that time is not triaged (importance, conflicts, escalation, prep notes) until every older row has been processed, past instances included.
- Each repeated series instance can stage another to-do or memory fact, which adds noise to `/todos` and long-term memory.
- Steady-state cost is small. An edit to an existing series does not trigger re-triage: the UPDATE branch at `calendar_poller.py:262-288` never resets `triaged`. Each weekly full sync adds only about one new instance per series.
- Precondition: no user action needed. It happens automatically for any connected account with recurring events.

**Fix**
- In `get_untriaged_events`, filter `AND (end_time >= :now OR all_day = 1 AND end_time >= :today)` and set `ORDER BY start_time >= :now DESC, start_time ASC`. Mark past rows with `triaged = 1, importance = 'past'` in bulk, without an LLM call.
- Limit triage to a near-term horizon (for example `start_time <= now + 30d`). Later instances get triaged when they move into the horizon.
- Triage once per series. If `recurring_event_id` is set and a sibling already has `triaged = 1`, copy its `importance`, `importance_reason` and `prep_notes` with one `UPDATE ... WHERE recurring_event_id = ? AND triaged = 0`. Do not repeat `memory_facts`, `todos` or `escalate` for siblings. Re-run the LLM only when the instance's summary, description or attendees differ from the series result.
- Load the skills once per loop iteration instead of once per event in `triage_event`.

Effort: M · Confidence: high

#### PERF-022 — Prompt-cache marker injection deep-copies the full API message list on every cached request
- **Severity:** Low · _Verification: partially-confirmed_

**Evidence**
- `agent/prompt_caching.py:62` `apply_anthropic_cache_control()` starts with `copy.deepcopy(api_messages)`. It then marks at most four messages: the system prompt (`:70-72`) and the last three non-system messages (`:74-77`).
- `agent/conversation_loop.py:897-902` calls it on every model iteration when `agent._use_prompt_caching` is set. `anthropic_prompt_cache_policy` (`agent/agent_runtime_helpers.py:1408`) sets that flag for native Anthropic, OpenRouter/Nous Claude, Anthropic-wire gateways, and some Qwen/MiniMax routes.
- `agent/conversation_loop.py:798` has already built `api_messages` as a per-call list of shallow `msg.copy()` dicts. The outer list and top-level keys are therefore already isolated. The deepcopy additionally walks every nested `content` list, content block, `tool_calls` entry and `reasoning_details` structure.
- The deepcopy is also doing a correctness job, so it cannot simply be deleted. `_apply_cache_marker` mutates `content[-1]` in place (`agent/prompt_caching.py:35-38`). Later in-place writes also depend on the copy: tool-call argument repair at `agent/conversation_loop.py:949` and `_sanitize_messages_surrogates` at `:960`, which rewrites list-part `text`. Without isolation these would leak `cache_control` and edits into the persisted `messages`.
- Correction to the original finding: `deepcopy` does not duplicate `str` objects. Large tool outputs and base64 image data URLs stay shared, so this does not create a second in-memory copy of the transcript bytes. The cost scales with the number of container objects, not payload size. A local benchmark at this commit measured about 0.4 ms for 151 messages, about 2.2 ms for 601, and about 5.5 ms for 1801 (each with 50 KB tool outputs and 2 MB images). In every case the image strings were the same objects.

**Impact**
- Every Claude/cached-route iteration pays an extra O(number of messages and blocks) Python object walk plus allocation for each nested dict and list. That is single-digit milliseconds even at about 1800 messages, compared with multi-second LLM round trips. Context compression also caps transcript length.
- There is a small amount of extra GC pressure and GIL hold time in the agent thread. No user-visible latency or token-cost impact is expected. It is not exploitable. It adds to the per-call copy overhead already tracked in PERF-005 (`agent/transports/chat_completions.py:198`), but the root cause is separate.

**Fix**
- Replace the deepcopy with targeted copies. Shallow-copy the outer list. For each of the at most four breakpoint indices, replace the message with `dict(msg)`. If its `content` is a list, also copy the list and its last block:
  ```python
  messages = list(api_messages)
  for idx in targets:
      m = dict(messages[idx])
      c = m.get("content")
      if isinstance(c, list) and c and isinstance(c[-1], dict):
          m["content"] = c[:-1] + [dict(c[-1])]
      messages[idx] = m
      _apply_cache_marker(m, marker, native_anthropic)
  ```
- Before landing, make the later in-place mutators copy-on-write so stored history stays isolated even when caching is off. In `conversation_loop.py:949`, build a new `function` dict instead of assigning `tc["function"]["arguments"]`. In `_sanitize_messages_surrogates`, copy a content part before rewriting `text`. Add a regression test asserting `messages` is unchanged after one `_build_api_messages` plus caching pass.

Effort: S · Confidence: high

## Prioritised remediation roadmap

### Quick wins (< 1 day each)
1. **PERF-004:** gate memory, task, and goal store `initialize()` behind a process-level "already initialised" flag or a schema-version check, so chat turns run no DDL.
2. **PERF-011:** make `/rescan` run in the background (or at least via `to_thread`).
3. **PERF-007:** switch to `psutil.cpu_percent(interval=None)` and add a 15 s TTL cache to `/api/status/summary`.
4. **PERF-009:** commit SQLite writes before network side effects, and add lock retry in the batcher.
5. **PERF-010:** add the partial index, select metadata columns only, and build a thread dict.
6. **PERF-005:** remove the `deepcopy` fallback.
7. **PERF-013:** stop calling `init_db` per Kanban request.
8. **PERF-018 and PERF-015:** batch embedding, and set a finite upload cap.

### Next 2–4 weeks
1. **PERF-003:** shared `asyncpg` pools with a codec/search-path `init` hook, and remove request-path `CREATE SCHEMA/EXTENSION`.
2. **PERF-001:** async facade over `SessionStore`/`SessionDB` and debounced `sessions.json` writes.
3. **PERF-008:** persistent background loop for the memory provider, with discovery and recall run concurrently.
4. **PERF-002:** cursor-based transcript APIs and delta loading for cached agents.
5. **PERF-006:** SQL-side project filtering and pagination with batched relation loads.
6. **PERF-012 and PERF-013:** EventBridge ID cursors, and shared stream tailers.
7. **PERF-016:** coalesce streaming renders.

### Additions from the second pass
1. **PERF-019:** paginate `GET /api/sessions/{id}/messages` (newest window, `before_id` cursor, role filter) and move it off the event loop.
2. **PERF-020:** resolve the target profile first and cache Supabase connections and pools per profile.
3. **PERF-021:** skip past recurring instances and dedupe recurring series before calling the LLM.
4. **PERF-022:** replace the `deepcopy` in `apply_anthropic_cache_control` with targeted copies of the 4 breakpoint messages.

### Longer-term / architectural
1. Reuse agents across agent-home turns, as the gateway already does with `_agent_cache`, so per-turn construction (provider init, tool registry, memory) stops.
2. Route all SQLite writes and maintenance through dedicated executors or services.
3. Move routing metadata from the full-document `sessions.json` into SQLite.
4. Use an outbox/event pipeline for integration side effects.
5. Add load tests for concurrent dashboard tabs, long tool-heavy sessions, and ingestion running alongside chat.

## Strengths worth keeping
- Config reads use mtime/size caches; EventBridge skips idle ticks using stat stamps.
- SQLite uses WAL, busy timeouts, retries, and sensible indexes. Agent-side DB calls often go through `AsyncSessionDB`.
- Agent caches and in-memory registries are bounded, and the prompt prefix is protected by keeping ephemeral context out of the system prompt.
- Large tool results spill to storage rather than always entering the model context.
- Project and dashboard updates use SSE, and the SSE routes send `no-transform`.
- Calendar sync uses `syncToken`/`updatedMin` delta sync; calendar triage pulls in `LIMIT`ed batches.
- The deploy script rebuilds the dashboard and agent-home only when their sources changed, using `nice -n 15`.
- The embedding service runs one model behind a lock with configured batching and resource limits.

## Appendix: per-area summaries
- **agent-core:** The dominant cost on long, tool-heavy turns is repeated full-history work: replay, deep copy, and stringification (PERF-002/005/014).
- **tools-exec:** Subprocess and polling overhead exists, but checkpoint costs only apply when checkpoints are enabled, and they are disabled by default. Nothing promoted.
- **tools-net-skills:** MCP per-server serialisation (PERF-017). The caches and spill paths are sound.
- **gateway-core:** Synchronous persistence on the loop (PERF-001). The earlier "unbounded inbound queue" finding was dropped: `InboundDispatcher` is never instantiated outside tests.
- **gateway-platforms:** API history loads full transcripts (PERF-002). App delivery opens a `SessionDB` per message (minor; omitted).
- **cli-http-api:** Project listing, status analytics, and per-client streams (PERF-006/007/013).
- **cli-core:** No pooling, repeated DDL, and cache clearing (PERF-003/004). Kanban `init_db` per request (PERF-013).
- **agent-home-api:** Status fan-out (PERF-007) and upload buffering (PERF-015).
- **agent-home-ui:** Streaming Markdown reparse (PERF-016).
- **web-tui-desktop:** Shares the dashboard event loop with plugin routes, so PERF-007 and PERF-011 affect it directly.
- **plugins-cron:** Per-turn memory init with DDL, recall blocking, achievements, and Kanban polling (PERF-004/008/011/013).
- **custom-integrations:** Transaction scope and message drops, plus the mailbox scan (PERF-009/010).
- **mcp-acp:** EventBridge replay and per-call goal-service initialisation (PERF-012, PERF-004).
- **infra-supply-chain:** Embedding batching is bypassed (PERF-018). I did not assess Caddy SSE buffering because the authoritative `/etc/caddy/Caddyfile` is not in the repository.
