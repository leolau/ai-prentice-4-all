---
title: "feat: Real semantic embeddings for the live pgvector memory tier (RAG)"
status: superseded
date: 2026-07-29
type: feature
target_repo: ai-prentice-4-all
origin: user request — "how would I do a RAG function with the agent-home's memory?"
---

# SUPERSEDED — this was already built before the plan was written

**Do not implement this plan.** It was written against a stale checkout and
every requirement in it had already shipped on `develop` weeks earlier. It is
kept only so the link in PR #130 resolves and nobody re-derives the same wrong
conclusions.

What the plan claimed, and what is actually true:

| Plan claimed | Reality on `develop` |
|---|---|
| `get_embedder()` ignores config, always hashing | Config-driven since `1b102b1f6`; `hashing` and `local_http` both selectable |
| `memory.embedding.*` is read by no code | Read in `plugins/memory/supabase_pgvector/embedding.py`; documented in `cli-config.yaml.example` |
| Dimension is hard-coded at 256 | Column width is discovered from `pg_attribute`; a mismatch raises `EmbeddingSpaceMismatch` |
| No migration path exists | `hermes memory vectors status` / `reembed` (`c89c3cfb0`) |
| Chunked RAG ingestion would need a new skill | Full RAG shipped in `39b54df94`: chunking, hybrid retrieval, `rag_search` tool, `hermes memory rag` |

Where to read instead:

- Design rationale — `docs/design/master-plan/feature-groups/FG-21-local-semantic-memory-rag-shared-recall.md`
- Embedding service on the box — `docs/deployment/local-embeddings.md`
- Ingestion, corpora, and what it refuses — `docs/deployment/rag-ingestion.md`

The lesson worth keeping: **`develop` moves fast and `main` is stale.** Before
planning anything for this repo, `git fetch` and read `origin/develop`, not a
checkout that has been sitting on a branch for a while — and grep for the
feature before writing a plan to build it.
