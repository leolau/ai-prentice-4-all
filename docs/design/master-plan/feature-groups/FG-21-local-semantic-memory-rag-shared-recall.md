# FG-21 — Local semantic memory (layer 4), RAG, and shared recall across users

**Wave:** 0 (foundation for recall) → A → B · **Owner agent:** unassigned · **Status:** Plan — implementation gated on the open decisions in §10

## Summary

Turn the existing pgvector tier into the agent's **fourth memory layer**: a
**semantic**, **local-only**, **RLS-scoped** store that (a) is recalled
automatically rather than only when the model remembers to ask, (b) backs a
**RAG** corpus over documents/messages/sessions, and (c) is **shared across
users of one instance** — memory belongs to a user, and a higher-privilege
principal (owner, and by explicit right an admin or grantee) can read another
user's memory, with every such read audited.

Three things are wrong today and each is a separate piece of work: the
embeddings are not semantic, nothing reads the store, and the multi-user
identity that access rights depend on is not wired up on the channels people
actually use. This FG fixes them in that order, because each one makes the next
one meaningful.

## Decisions applied

- **D2** hybrid memory (frozen curated snapshot + live queryable tier) — unchanged.
- **D1 / C2** per-user private vs shared vs owner-sees-all — extended, not replaced.
- **D4** pgvector in self-hosted Supabase is the datastore for the application layer.
- **New (proposed, needs owner sign-off — see §10):** *embeddings are computed
  **on the box**; memory text never leaves the deployment for embedding.*

## 1. What is actually live today (measured on `hermes-systest`, 2026-08-04)

| Layer | Mechanism | State |
|---|---|---|
| **1 — curated snapshot** | `MEMORY.md` / `USER.md`, loaded once at session start, byte-stable in the system prompt (`tools/memory_tool.py`) | live, and **at its ceiling** — peak `Memory at 2,029/2,200 chars`, and **6** writes refused with `would exceed the limit` |
| **2 — user profile** | `USER.md` profile block, same snapshot semantics | live |
| **3 — session history** | SessionDB (SQLite + FTS5), reached via `session_search` | live, **keyword-only**: 10 calls in 12 days |
| **4 — live semantic tier** | `memory.provider: supabase_pgvector` → `app_dev.memories`, `vector(256)`, HNSW `vector_cosine_ops` | **table and index exist; 28 rows; never read** |

Supporting facts, all verified on the box rather than assumed:

- Supabase stack: 11 containers up 3 weeks, `vector 0.8.2` installed, DB 16 MB, 1.4 GB on disk.
- `app_prod.interactions`: **2,459 rows** since Jul 22 (387/day), 2,452 from Telegram — the C8 action ledger is healthy and is the natural spine for ingestion.
- `app_dev.memories`: 28 rows, **all** `kind=intent_signal / topic=task_discovery`, **all** `owner_user_id=8756039695`, **all** `visibility=private:8756039695`.
- `memory_query` / `memory_write` are registered (`Memory provider 'supabase_pgvector' registered (2 tools)`) and appear **0 times** in the agent log. The tier is write-only in practice.
- `app_prod.principals` = 1 row (`leo_owner`), **`channel_identities` = 0**, `item_grants` = 0.
- Box: 4 vCPU, 14 GB RAM (≈10 GB available), **no GPU**.

## 2. Findings — the gaps this FG closes

**G1 — the embeddings are not semantic.** `plugins/memory/supabase_pgvector/embedding.py`
is a `HashingEmbedder`: sha256 per token into 256 dimensions, L2-normalised.
Cosine distance therefore measures **vocabulary overlap**, not meaning:
"when is the tender due" does not retrieve "招標截止日期", and does not even
retrieve "RFP deadline". This is deliberate — it is the credential-free default
so the provider works on a fresh box and its tests stay hermetic — and the
module's own docstring names the intended extension point ("a real embedding
model … can be dropped in later … selected from `config.yaml`"). That hook was
never built: `get_embedder()` returns `HashingEmbedder` unconditionally.

**G2 — nothing recalls.** Recall depends entirely on the model choosing to call
`memory_query`. It never has. The provider's `prefetch()` hook is the existing,
cache-safe seam for this: `agent/conversation_loop.py` wraps its output with
`build_memory_context_block()` and appends it to **the current turn's user
message at API-call time only** — the stored message is never mutated and the
system prompt is untouched. Today that hook returns only task-discovery
proposals. Automatic recall belongs there.

**G3 — the identity that access rights depend on is not wired up.** The
provider's `_resolve_principal()` never consults FG-01's `resolve_principal`
or the `principals` table; it falls back to the **gateway user id as
`role=member`**. Consequences, all observable in the data:

- Telegram sessions run as principal `8756039695`, role **member** — *not* owner.
  The "owner sees everything" property is never exercised on the channel that
  carries 99% of traffic.
- The dashboard runs as `leo_owner` (7 ledger rows). **One human, two
  principals, disjoint memory.** A fact learned on Telegram is invisible in the
  dashboard and vice versa.
- A second channel (WhatsApp, email) would mint a *third* identity with its own
  private tier.
- `channel_identities` is empty, so nothing maps a channel identity onto a
  system user — the table designed for exactly this is unused.

**Cross-user memory cannot be built on this.** G3 is the prerequisite, not a
detail.

**G4 — role elevation is all-or-nothing and unaudited.** In `hermes_cli/access.py`,
only `owner` bypasses scoping (`scope_filter` → `TRUE`); **`admin` has no
elevated read at all**. So "a higher-level user can read another user's memory
based on access right" today means *owner sees literally everything* or *nobody
sees anything* — and no record is kept of who read whose private memory.

**G5 — memories are not grant-scoped.** FG-19 built per-item grants
(`item_grants`, grant-aware `can_read` / `scope_filter` / RLS) for GTS items.
`PgvectorMemoryStore.query()` calls `scope_filter(principal)` **without**
`grant_item_kind`, so a memory cannot be shared with one named person the way a
goal can.

**G6 — no RAG corpus.** `memories` holds short single-text rows. There is no
document/chunk model, no ingestion, no chunking, no provenance, and no
citations — so "read that Drive spec and summarise section 3" still requires
you to name the file.

**G7 — no model/dim versioning.** The vector column is `vector(256)` with the
model implied. Changing embedder silently mixes two vector spaces in one
column, at which point cosine ranking is meaningless and the failure is
invisible. Migrating costs nothing today (28 rows) and grows with every row.

**G8 — layer 1 is full and there is no promotion path.** Layer 1 is at
1,864/2,200 chars and the agent is spending turns pruning it. With layer 4
readable, most of that content belongs there, with only durable identity-level
facts kept in the snapshot.

## 3. Target architecture

```
                    ┌─────────────────────────────────────────┐
   turn N  ─────────▶│ prefetch(query)  ── semantic recall     │──▶ <memory-context>
                     │   top-k, RLS-scoped, budgeted           │    appended to the
                     └─────────────────────────────────────────┘    USER turn (cache-safe)
                                    │
   mid-turn ──▶ memory_query / rag_search (tools, appended results)
                                    │
                     ┌──────────────▼───────────────────────────────────┐
                     │ Supabase Postgres (on-box)                        │
                     │  memories(embedding, owner_user_id, visibility)   │  layer 4
                     │  rag_documents / rag_chunks(embedding, source_ref)│  RAG
                     │  item_grants  · principals · channel_identities   │  access
                     │  FORCE'd RLS on every scoped table                │
                     └──────────────▲───────────────────────────────────┘
                                    │  embed(text) over loopback only
                     ┌──────────────┴───────────────┐
                     │ hermes-embed.service (local)  │  no egress, unprivileged
                     │ 127.0.0.1 · model pinned      │
                     └──────────────────────────────┘
```

Invariants that must survive every step below:

1. **Prompt caching is sacred.** Recall arrives as an appended `<memory-context>`
   block on the user turn or as tool results — never in the system prompt.
2. **RLS is the boundary, the app filter is the convenience.** Every new table
   gets `apply_scope_rls`, and every new read path that widens access must widen
   the RLS clause in the same commit (the FG-19 pattern).
3. **Fail closed.** An embedding service that is down must degrade to a
   keyword/hash fallback with a logged warning — never to "no rows found",
   which is indistinguishable from "nothing is known".
4. **Local only.** No text leaves the box to be embedded.

## 4. The embedding service (fixes G1, G7)

### 4.1 Model candidates (CPU-only, bilingual zh/en)

| Model | Params | Dim | Ctx | Notes |
|---|---|---|---|---|
| `multilingual-e5-small` | 118M | 384 | 512 | fastest; weakest on long zh passages |
| `multilingual-e5-base` | 278M | 768 | 512 | good middle; solid zh |
| **`bge-m3`** | 568M | 1024 | 8192 | strongest zh/en of the three, long context, ~2.3 GB resident |
| `Qwen3-Embedding-0.6B` | 600M | 1024 (MRL-truncatable) | 32k | strong multilingual, same vendor family as the current LLM |

**Recommendation: `bge-m3`, subject to measurement.** Long context matters for
RAG chunks and the corpus is bilingual tender/RFP material. But CPU latency on
4 shared vCPUs is the deciding number and I have not measured it yet — so step
P1 is a benchmark on the box (throughput at 512-token chunks, resident memory,
p95 latency for a single query embed) across `bge-m3` and
`multilingual-e5-base`, with the probe set from §7. Pick on the numbers, record
them in this doc's audit log.

### 4.2 Serving

A dedicated **`hermes-embed.service`** bound to `127.0.0.1`, unprivileged,
`NoNewPrivileges`, no egress after model download, `MemoryMax` set so it can
never squeeze the 11 Hermes services. Two viable shapes:

- **Text-Embeddings-Inference container** (CPU image) — same lifecycle as the
  Supabase stack, no new Python deps in the Hermes venv. Preferred.
- **`sentence-transformers` in a small FastAPI app** — fewer moving parts,
  heavier venv, in-process option useful for tests.

The model is pulled once and cached under `/opt/data/`, pinned by revision, and
the unit + revision are captured by `deploy_state.py` so a rebuilt box gets the
same model (the deployment-path work already covers units and manifests).

### 4.3 Config surface (no new env vars — `config.yaml` only)

```yaml
memory:
  provider: supabase_pgvector
  embedding:
    provider: local_http        # local_http | hashing (offline default)
    endpoint: http://127.0.0.1:8791
    model: BAAI/bge-m3
    dim: 1024
    timeout_seconds: 20
    batch_size: 16
```

`hashing` stays the default when nothing is configured, so a fresh clone and
the hermetic test suite behave exactly as they do today.

### 4.4 Model/dim versioning and migration (G7)

- Add `embedding_model TEXT NOT NULL` and `embedding_dim INT NOT NULL` to every
  embedded table; **never** mix models in one ranking. Queries filter on the
  active model, so a model switch degrades to "no rows for this model yet"
  (visible, loud) instead of silently wrong ranking.
- Column type moves `vector(256)` → `vector(<dim>)` in a migration that
  re-embeds existing rows; HNSW index rebuilt per active model.
- A `hermes memory reembed` CLI command does the backfill in batches, resumable,
  reporting progress — the same command used for a future model change.
- The 28 existing rows are `intent_signal` captures with no durable value; they
  are re-embedded rather than discarded so the path is exercised on real rows.

## 5. Layer 4 becomes a memory layer (fixes G2, G8)

1. **Automatic recall in `prefetch()`**: embed the incoming user message,
   `top_k` (default 5) RLS-scoped nearest neighbours above a similarity floor,
   rendered into the existing `<memory-context>` wrapper with a hard character
   budget (default 1,200) and one line per fact including its owner when the
   fact is not the caller's own. Cache-safety is asserted by test, not assumed.
2. **`memory_write` gains automatic capture**: end-of-turn extraction of durable
   facts (via the existing `on_session_end` / `on_pre_compress` hooks) instead of
   only the model's explicit writes, with dedup by cosine similarity ≥ 0.95
   against the same owner's existing rows.
3. **Decay and usefulness**: `uses` / `last_used` are already columns and are
   never updated — increment them on recall, and rank by
   `similarity × recency × log(1+uses)` so stale one-offs sink.
4. **Promotion / demotion between layers** (relieves G8): a scheduled review
   promotes a high-`uses`, high-similarity, owner-approved fact into layer 1 and
   demotes layer-1 lines that layer 4 already covers. Layer 1 stops being a
   manually-pruned 2,200-char budget and becomes the small durable core.

## 6. Shared recall across users (fixes G3, G4, G5)

This is the part Leo called out as most important, and it is mostly *access
work*, not vector work.

### 6.1 P0 — make identity real (G3) — **delivered**

The root cause turned out to be narrower than "resolution is unwired", and
worth recording because it will recur for every user Leo adds:

- `resolve_principal()` **does** consult `channel_identities`; it auto-enrols
  only senders the *pairing* store approved. Leo reaches the gateway through
  `telegram.allow_from`, which authorises without pairing — so he was
  authorised, never enrolled, and `channel_identities` stayed empty. Authz and
  identity are two different gates and only one of them was passed.
- The role never travelled even when a principal *was* resolved: the seam
  stamped `internal_user_id` only, and `agent_init` then hardcoded
  `principal_role="member"`. So enrolment alone could not have produced an
  owner-level session.

What landed:

- `hermes member link-channel <user_id> <platform> <channel_user_id>` — the
  owner/admin surface that states "this handle is this principal", covering
  allow-listed users that pairing never touches. Deliberately GoTrue-free
  (linking is a principal operation, so it must work with no dashboard auth
  configured), refuses an unenrolled `user_id` rather than minting one, and
  validates the platform against the gateway's own enum so a link can't be
  stored in a form intake would never match. `hermes member list` now prints
  each member's linked channels, so an unlinked member is visible.
- `bind_channel_principal` stamps `internal_user_role` from the `principals`
  row alongside `internal_user_id`, and it is threaded
  `InboundEvent → SessionSource → init_agent → memory provider`. Unresolved
  degrades to `member` via `normalize_role()`; nothing is elevated by failure.
- The role is **excluded from session persistence** (`to_dict`/`from_dict`),
  like `delivered_via_upstream_relay`: it decides what a session may read of
  other users' data, so it is re-read from the database every turn rather than
  replayed from a file that could be stale or edited.
- Role management itself needed nothing new: `hermes member add/set-role/...`
  and the owner/admin-gated `/api/comms/members` API already existed.

Still open in P0's scope: the 28 existing rows still carry
`owner_user_id=8756039695`. Linking the channel makes *new* rows land on
`leo_owner`, which splits the history rather than joining it, so the migration
is a deliberate, separately-verified data change — not a side effect of a code
deploy.

### 6.2 Access model for memory (G4, G5)

Keep C2 exactly as it is and extend it in the two ways it was designed to be
extended:

| Need | Mechanism | New? |
|---|---|---|
| my own memory | `visibility = private:<uid>` | no |
| instance-wide knowledge | `visibility = shared` | no |
| owner reads anyone | `principal.is_owner` bypass | no |
| **admin reads a member's memory** | **role-scoped elevation, downward only**: `scope_filter` gains a rung comparison over `owner > admin > member > viewer`, mirrored in RLS | **yes** |
| **one named person reads one memory** | `item_grants(item_kind='memory')` + `grant_item_kind='memory'` on the memory read paths | **yes** (reuses FG-19) |
| **a team shares a tier** | `visibility = team:<team_id>` + `team_members` | **not wanted** — the role ladder covers it (§10.5) |

Non-negotiables:

- **Every cross-principal read is audited.** A new C8 interaction kind
  (`memory_read_elevated`) records reader, owner, memory ids, and the query that
  surfaced them. Unaudited owner-bypass is the current behaviour and is not
  acceptable in a multi-user instance — if the owner can read a member's private
  memory, the member must be able to see that it happened.
- **Reads go down the ladder only, never sideways.** An admin reads members and
  viewers; an admin does **not** read another admin. Peer-level reads are the
  difference between a hierarchy and a free-for-all, and are not granted by
  role (an explicit `item_grants` row remains the way to share with a peer).
- **One person belongs to one role, and memory is one pool per person** keyed on
  `owner_user_id`. A Hermes profile is the *role's* shared configuration
  (layers 1–2: `MEMORY.md` / `USER.md`); layer 4 is the person. Several people
  on one profile therefore share behaviour and keep separate private memory —
  and no one's private memory sits in a profile file the whole role reads.
- **Elevation is opt-in per instance** and defaults off, so a single-user
  deployment behaves exactly as today.
- **RLS mirrors every widening** in the same commit, with a negative test per
  path (member cannot read another member; admin can only when elevation is
  enabled; grantee sees only the granted row and none of the owner's others).
- Recall output **labels provenance** when a fact is not the caller's own
  ("from <user>'s memory") — a shared brain that silently blends other people's
  facts into an answer is a privacy problem *and* a correctness problem.

## 7. RAG (fixes G6)

### 7.1 Data model

```sql
CREATE TABLE rag_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility     TEXT NOT NULL,          -- C2, inherited by every chunk
    source_kind    TEXT NOT NULL,          -- gdrive | gdoc | email | whatsapp | session | file | skill
    source_ref     TEXT NOT NULL,          -- stable id (Drive file id, message id, session key)
    title          TEXT NOT NULL DEFAULT '',
    content_hash   TEXT NOT NULL,          -- re-ingest only when it changes
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_kind, source_ref, owner_user_id)
);

CREATE TABLE rag_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,           -- denormalised for RLS
    visibility    TEXT NOT NULL,
    ordinal       INT  NOT NULL,
    text          TEXT NOT NULL,
    embedding     vector(<dim>) NOT NULL,
    embedding_model TEXT NOT NULL,
    token_count   INT NOT NULL,
    section       TEXT,                    -- heading path, for citations
    UNIQUE (document_id, ordinal)
);
```

RLS on both, by the same C2 predicate as `memories`, plus the grant clause.
Deleting a document cascades its chunks and its right to be recalled.

### 7.2 Ingestion

- **Sources, in the order they pay off**: (1) Google Drive/Docs the agent can
  already read — the tender/RFP corpus; (2) session transcripts from SessionDB;
  (3) the `app_prod.interactions` ledger summaries; (4) email; (5) WhatsApp
  archives.
- **Chunking**: heading-aware, ~512 tokens with ~64 overlap, keeping the heading
  path in `section` so a citation reads `Indy Project Proposal › 3. Scope`.
- **Incremental**: `content_hash` gates re-embedding; a nightly systemd timer
  (same shape as the existing backup/drift timers) re-scans and re-embeds only
  what changed.
- **Access inheritance**: a document ingested from a user's Drive is
  `private:<that user>` unless explicitly shared. Ingestion must never launder a
  private document into `shared`.
- **Approval boundary preserved**: ingestion reads Drive through the same
  approval-gated Workspace tools; a scheduled ingest runs under the documented
  cron approval mode, never by bypassing the gate.

### 7.3 Retrieval

- **Hybrid**: vector (`<=>` on HNSW) + lexical (`pg_trgm` / FTS) with
  reciprocal-rank fusion. Pure vector loses exact identifiers ("Tender
  2026-0418"); pure lexical loses paraphrase and cross-language. Both are
  cheap here.
- One new tool, `rag_search`, returning text + document title + section +
  score, so the model can cite. Results are appended messages (cache-safe).
  It is a **provider tool** (like `memory_query`), not a new core tool — the
  footprint ladder rung for this is "extend the existing provider".
- **Answer-with-citations** is the acceptance target: the answer names the
  document and section, and a wrong citation is a test failure.

### 7.4 Recall quality probes (the measurable acceptance gate)

A fixed bilingual probe set committed with the code, each probe naming the row
or chunk that *must* be in the top-3:

| Query | Must retrieve | Fails today because |
|---|---|---|
| "when is the next tender due" | the 招標 deadline chunk | no shared vocabulary |
| "投標文件要求" | the RFP requirements section | different language |
| "Tender 2026-0418" | that exact document | vector-only misses ids → hybrid |
| "what did Leo decide about backups" | the age/backup decision memory | not ingested yet |
| a member querying another member's private note | **nothing** | negative access |

Report `recall@3` before and after. "It feels better" is not a result.

## 8. Work breakdown (each phase is a PR)

| # | Phase | Contents | Gate |
|---|---|---|---|
| **P0** | Identity is real — **done** | `hermes member link-channel`; role stamped by `bind_channel_principal` and threaded to the memory provider; role never persisted | one human = one principal across channels; unresolved → `member`; negative-access tests unchanged |
| **P0b** | Existing rows | migrate the 28 `8756039695` rows onto `leo_owner` (or alias), verified against a copy first | no row loses its owner; recall before/after identical for Leo |
| **P1** | Embedding service | benchmark `bge-m3` vs `e5-base` on the box; `hermes-embed.service`; `local_http` embedder behind `memory.embedding.*`; hashing stays default | numbers recorded here; service survives restart; fail-closed fallback logged |
| **P2** | Semantic layer 4 | model/dim columns + migration + `hermes memory reembed`; recall in `prefetch()` with budget; `uses`/`last_used`; dedup | `recall@3` on the probe set ≥ agreed floor; prompt-prefix byte-stability test green |
| **P3** | Shared recall | role-scoped elevation (config, default off) + RLS; `item_grants` for memories; provenance labels; `memory_read_elevated` audit | full negative-access matrix (member/member, admin off/on, grantee) at the **RLS** level, not just app level |
| **P4** | RAG | `rag_documents` / `rag_chunks` + RLS; Drive + session ingestion; nightly timer; `rag_search` with citations | answer-with-citation on 3 real Drive docs; no private→shared laundering (test) |
| **P5** | Consolidation | layer-1 promotion/demotion; email/WhatsApp ingestion (teams dropped — §10.5) | layer 1 back under budget without losing facts |

Nothing here needs a new core tool, a new `HERMES_*` env var for behaviour, or a
change to the frozen-snapshot semantics.

## 9. Operational plan

- **Resources**: 4 vCPU / ~10 GB free today, of which the embedding service
  takes ~2.5 GB resident for `bge-m3`. `MemoryMax` on the unit; ingestion runs
  `nice`d off-peak so a backfill can never starve the gateway.
- **Storage**: 1,024 dims ≈ 4 KB/vector. 100k chunks ≈ 400 MB + index — trivial
  against 83 GB free.
- **Backups**: vectors are regenerable and are **excluded** from the encrypted
  credential bundle; the source documents and the `memories` rows are what
  matter. Add a `pg_dump` of the `app_*` schemas to the daily job — today the
  bundle covers credentials only, so **the memory rows themselves are in no
  backup**.
- **Deployment path**: new unit + config keys captured by `deploy_state.py`
  (`capture` → PR → merge), so a rebuilt box brings the embedding service and
  the model pin back with it. Weekly drift check covers them automatically.
- **Security**: service is loopback-only and unprivileged; model files are
  root-owned and read-only to the service; no egress after the pull; nothing
  new is exposed to the agent's shell.

## 10. Open decisions for the owner

All five are **decided** (Leo, 2026-08-04):

1. **Model** — `bge-m3` if the P1 benchmark clears the latency bar on 4 shared
   vCPUs, else `multilingual-e5-base`. Decided on the measured numbers, which
   P1 records here.
2. **Elevation policy** — **by role**, not grant-only. Downward only: a role
   reads every rung below it and never its peers. `item_grants` stays for
   sharing sideways or with one named person.
3. **Audit visibility** — **yes**. A member can see that a higher-privilege
   principal read their private memory, which makes the `memory_read_elevated`
   audit load-bearing rather than decorative.
4. **Ingestion scope for P4** — **all of Drive**, both accounts, including
   "Shared with me". Staged newest-first so current tenders are searchable
   before the backfill completes, and every chunk keeps its Drive file id so a
   folder can be un-indexed with one delete.
5. **Teams** — **not needed**. The role ladder covers the hierarchy; `team:<id>`
   is dropped from P5 rather than deferred.

## 11. Testing requirements

- **Invariants, not snapshots**: assert that a private row is unreadable by a
  non-grantee *through RLS on a real Postgres*, not that a helper returns a
  fixed string.
- **Cache-safety**: system-prompt prefix byte-stable across a turn that recalls
  and a turn that ingests.
- **Concurrency**: N writers + readers across `(user, task)` sessions; no lost
  writes, no cross-session bleed (extends the existing FG-05 concurrency test).
- **Negative access matrix**: member↛member, admin↛member with elevation off,
  admin→member with elevation on, grantee→one row only, owner→all with audit row
  written.
- **Degradation**: embedding service down → logged fallback, not silent empty
  recall.
- **Recall quality**: the §7.4 probe set, with `recall@3` reported in CI output.
- **Real E2E** for every access/datastore change, per `AGENTS.md`.

## 12. Dependencies

- **Builds on**: FG-01 (C1/C2 identity + roles), FG-05 (the pgvector tier being
  extended), FG-13 (C3 dev/prod routing), FG-19 (per-item grants + grant-aware
  RLS), FG-16 (C8 audit ledger).
- **Unblocks**: retrieval-grounded answers for FG-09/FG-18 (goal/task context),
  and the mobile face in FG-20 showing "what the agent knows about me".
- **Note**: FG-05's own ECS system-test checklist is still open; P2 here
  subsumes and completes it.

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-04 | 2 | devin | Folded in Leo's five decisions (local model on measured numbers; elevation **by role, downward only**; audit visible to the member; **all of Drive** incl. Shared-with-me; **no teams** — one person, one role, one memory pool, profile = role config) and recorded the delivered P0: the identity split was `telegram.allow_from` authorising without pairing (so enrolment never happened) *plus* a hardcoded `principal_role="member"` that discarded the role even when it did | Leo answered §10 and asked for the role model to be the basis of cross-user memory; P0 shipped as `hermes member link-channel` + role propagation |
| 2026-08-04 | 1 | devin | Created FG-21 from a live survey of `hermes-systest`: measured the pgvector tier (28 rows, hashing embeddings, 0 reads), the identity split (Telegram `8756039695` as `member` vs `leo_owner`, `channel_identities` empty), and the absent RAG corpus; planned local semantic embeddings, layer-4 recall, cross-user shared recall with audited elevation, and RAG | Leo: local embeddings, must be semantic, use as the 4th memory layer, support RAG, and share memory across users of one instance with higher-privilege access by right |
