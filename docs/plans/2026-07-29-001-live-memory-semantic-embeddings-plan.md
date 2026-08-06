---
title: "feat: Real semantic embeddings for the live pgvector memory tier (RAG)"
status: draft
date: 2026-07-29
type: feature
target_repo: ai-prentice-4-all
origin: user request — "how would I do a RAG function with the agent-home's memory?"
---

# feat: Real semantic embeddings for the live pgvector memory tier (RAG)

## Summary

`memory_query` / `memory_write` already give the agent a live, visibility-scoped,
pgvector-backed memory it can drive straight from an agent-home chat turn. What is
missing for real RAG is the *embedding*: the provider always uses
`HashingEmbedder`, a deterministic bag-of-words hash. Similarity therefore means
"shares literal vocabulary", so a paraphrase of a stored chunk does not retrieve
it — the retrieval half of RAG effectively does not work.

This plan makes the embedder config-selectable, adds an OpenAI-compatible
embeddings backend (works with OpenAI, LM Studio, Ollama, TEI, SiliconFlow,
Jina, Voyage — anything exposing `POST /v1/embeddings`), and adds the dimension
migration/re-embed path that switching backends requires. Nothing about the
memory tools, their schemas, the C2 visibility contract, or prompt-cache safety
changes.

---

## Problem Frame

Three concrete defects observed on the live box (`hermes-systest`,
`/opt/data/hermes-home-staging`):

1. **Retrieval is lexical, not semantic.** `get_embedder()` in
   `plugins/memory/supabase_pgvector/embedding.py` ignores all configuration and
   returns `HashingEmbedder(dim=256)`. Storing "the enterprise plan discounts
   seats above 50" and querying "bulk pricing for large teams" shares almost no
   tokens, so cosine distance is near-orthogonal and the row is not recalled.

2. **Config already lies.** The live `config.yaml` contains

   ```yaml
   memory:
     provider: supabase_pgvector
     embedding:
       provider: local_http
   ```

   No code reads `memory.embedding.*` — grep for `local_http` across the repo
   returns nothing. The operator believes a real embedder is active; it is not.
   Any fix must make that key real (or reject it loudly), not leave it inert.

3. **Dimension is baked into the table.** `_schema_sql()` creates
   `embedding vector(256)` under `CREATE TABLE IF NOT EXISTS`. Pointing the
   provider at a 1536-dim or 1024-dim model against an existing table silently
   skips the DDL and then fails per-write with a pgvector dimension error, or
   worse, mixes vector spaces if the column were ever widened by hand. Mixed
   spaces produce plausible-looking but meaningless rankings, which is the worst
   possible failure mode for a memory system.

Non-goals: changing the curated `MEMORY.md` snapshot tier, adding a new core
tool, chunking as a core feature, re-ranking, or hybrid BM25+vector search.

---

## Requirements

- **R1.** The embedder is selected by `memory.embedding.*` in `config.yaml`, with
  `hashing` as the default so a credential-free install and the hermetic test
  suite behave exactly as today.
- **R2.** An `openai_compatible` backend calls `POST {base_url}/embeddings` with
  a configured model and API key, and works against both hosted APIs and a local
  server (LM Studio / Ollama / text-embeddings-inference).
- **R3.** Embedding dimension is discovered from the backend (first successful
  call) rather than hard-coded per provider, and reconciled against the actual
  `memories.embedding` column dimension at initialize time.
- **R4.** A dimension or backend mismatch **fails closed with an actionable
  error** naming the migration command. It never silently falls back to the
  hashing embedder, because that would write rows in a second vector space into
  the same column.
- **R5.** A migration command re-embeds every existing row (the source `text` is
  stored, so re-embedding is lossless) and alters the column dimension in one
  transaction.
- **R6.** Write and query paths are batched and time-bounded: a slow or down
  embedding endpoint degrades to a clear tool-result error inside the turn, not
  a hung agent.
- **R7.** No new non-secret `HERMES_*` env var. The API key follows the existing
  secret convention (`.env` + `${VAR}` reference in `config.yaml`).
- **R8.** Prompt-cache safety is preserved: no change to `system_prompt_block()`,
  results still arrive only as appended tool-result messages.

---

## Design

### Configuration surface

```yaml
memory:
  provider: supabase_pgvector
  embedding:
    provider: hashing            # hashing | openai_compatible
    base_url: ""                 # e.g. http://127.0.0.1:1234/v1  or https://api.openai.com/v1
    model: ""                    # e.g. text-embedding-3-small, nomic-embed-text
    api_key: ""                  # prefer ${EMBEDDING_API_KEY} from .env
    dim: 0                       # 0 = probe from the backend; set to pin/validate
    timeout: 20                  # seconds per request
    batch_size: 64               # inputs per request on ingest
```

Defaults live beside the other `memory` keys in `hermes_cli/config.py`, so
`hermes config` and the dashboard Config page pick them up for free.
`memory.embedding.provider: local_http` (what the box has today) is **not** a
valid value and will now surface as a startup error naming the valid set — an
intentional break, because it is currently a silent no-op.

### Embedder factory

`plugins/memory/supabase_pgvector/embedding.py`:

- Keep `Embedder` protocol and `HashingEmbedder` untouched.
- Add `class OpenAICompatibleEmbedder` implementing `embed(text)` plus a new
  `embed_batch(texts) -> list[list[float]]`; give `HashingEmbedder` a trivial
  `embed_batch` so the store has one code path.
- `get_embedder(config=None, dim=DEFAULT_DIM)` reads `memory.embedding` and
  returns the selected implementation; unknown provider → `ValueError` listing
  the valid names.
- L2-normalise remote vectors defensively (the HNSW index is
  `vector_cosine_ops`; most providers already return unit vectors).
- Dimension: if `dim` is configured, trust and validate on first response; if
  `0`, issue one probe embedding of a fixed short string at initialize and cache
  the length for the process.

### Store / schema reconciliation

`plugins/memory/supabase_pgvector/store.py`:

- On `initialize()`, read the live column dimension:

  ```sql
  SELECT atttypmod FROM pg_attribute
   WHERE attrelid = to_regclass($1) AND attname = 'embedding'
  ```

  `to_regclass` returns NULL for a fresh install → create with the embedder's
  dim (today's behaviour, just no longer hard-coded to 256).
- Existing table, matching dim → proceed.
- Existing table, differing dim → raise `MemoryEmbeddingMismatch` carrying both
  dims and the remediation command. The provider surfaces this through the
  existing `_init_error` path, so `memory_query` returns a clear JSON error
  instead of the agent silently losing its memory.
- Record the active embedder identity (`provider:model:dim`) in a tiny
  `memory_embedding_meta` row so a *same-dimension, different-model* swap (e.g.
  two different 1024-dim models) is also caught — same failure mode, invisible
  otherwise.

### Migration command

`hermes memory reembed [--yes]`:

1. Read config, build the target embedder, probe its dim.
2. Compare with the stored meta/column dim; no-op with a message if identical.
3. In one transaction per batch: stream rows, `embed_batch` their `text`, write
   into a new `embedding_new vector(<dim>)` column.
4. Swap columns, rebuild the HNSW index, update `memory_embedding_meta`.
5. Print counts, timing, and cost-relevant totals (rows, characters).

Resumable and idempotent: rows carry a `reembedded_at` marker during the run, so
an interrupted migration can be re-run without double-charging the whole corpus.
Dry-run (`--dry-run`) prints what it would do, including estimated request count.

### RAG ingestion (no new tool)

Per `AGENTS.md`'s narrow-waist ordering, chunked ingestion stays out of the core:
it is a **skill**, `skills/knowledge/memory-rag/SKILL.md`, that tells the agent
how to (a) read a file or URL, (b) chunk to ~200–400 words with ~15% overlap and
a stable `topic`, (c) `memory_write` each chunk with `kind='doc'`, and (d) recall
with a topic-filtered `memory_query` before answering, citing what came back.
This is exactly what the user can already trigger by prompt today; the skill just
makes it consistent and repeatable.

---

## Implementation Steps

1. `hermes_cli/config.py` — add the `memory.embedding` defaults block with
   comments matching the surrounding style.
2. `plugins/memory/supabase_pgvector/embedding.py` — `embed_batch`,
   `OpenAICompatibleEmbedder`, config-driven `get_embedder`, explicit errors.
3. `plugins/memory/supabase_pgvector/store.py` — dim discovery from
   `pg_attribute`, `memory_embedding_meta`, `MemoryEmbeddingMismatch`,
   batched write path.
4. `plugins/memory/supabase_pgvector/__init__.py` — pass loaded config into
   `get_embedder`, surface mismatch/timeout errors as structured tool results,
   extend `get_config_schema()` so `hermes memory setup` prompts for base_url /
   model / key.
5. `hermes_cli/` — register the `memory reembed` subcommand (mirrors the
   existing `promote` subparser registration pattern).
6. `skills/knowledge/memory-rag/SKILL.md` — ingest + recall procedure.
7. Docs — `website/docs/user-guide/features/memory-providers.md` gains a
   "semantic embeddings for the live tier" section, incl. the honest note that
   `hashing` is lexical.

---

## Testing

Hermetic by default — no test may require a network or an API key.

- **Unit (embedding):** provider selection incl. unknown-name error; hashing
  determinism unchanged; `OpenAICompatibleEmbedder` against a stub transport —
  request shape, batch splitting, normalisation, timeout → typed error, HTTP
  401/429/5xx → typed error.
- **Unit (store):** fresh install creates the column at the embedder's dim;
  matching dim proceeds; mismatched dim raises with both dims in the message;
  same-dim-different-model caught via meta row.
- **Provider:** `memory_query` / `memory_write` return structured JSON errors
  (not exceptions) when the embedding backend is misconfigured or down.
- **Migration:** re-embed over a seeded fake store — row count preserved, text
  unchanged, new dim in place, index rebuilt, interrupted run resumes.
- **Cache safety:** re-run the existing prompt-prefix stability test; the system
  prompt must remain byte-identical across a mid-session write.
- **Live validation on `hermes-systest`** (post-merge, explicit go-ahead):
  ingest a small document via the skill, then query with deliberately
  *disjoint vocabulary* and confirm the right chunks come back — the exact case
  that fails today.

## Rollout & Risk

- Default `hashing` means existing installs are byte-for-byte unaffected until
  someone opts in.
- The live box's inert `embedding.provider: local_http` becomes a hard error;
  fix its config in the same change window (either set `hashing` explicitly or
  complete the `openai_compatible` block).
- Cost: re-embedding and ingestion hit a paid API if a hosted model is chosen.
  `--dry-run` reports request/character counts first; a local endpoint keeps it
  free.
- Reversible: switching back to `hashing` is another `hermes memory reembed`.

## Open Questions

1. Which embedding backend do you want on `hermes-systest` — a local model on
   the box (free, no key, e.g. `nomic-embed-text` via Ollama/LM Studio) or a
   hosted one (better quality, needs a key and spends money)? DeepSeek, the
   box's current chat provider, exposes **no** embeddings endpoint, so it cannot
   be reused here.
2. Should `hermes memory reembed` run automatically at startup when it detects a
   mismatch, or stay strictly manual (current plan: manual — an implicit
   re-embed can silently spend money).
