# Drive → RAG ingestion

How documents get into layer 4 on this deployment, what it costs, and what it
deliberately refuses to do. Design rationale lives in
`docs/design/master-plan/feature-groups/FG-21-local-semantic-memory-rag-shared-recall.md`;
this file is the operational half.

## What is ingested

All of Drive, for every account with a credential file — including "Shared with
me" and shared drives. That was an explicit decision, and it is implemented by
`includeItemsFromAllDrives`, `supportsAllDrives` and `corpora=allDrives` on the
listing call. Without all three, Drive quietly returns only files the account
owns, which would look like it worked.

Text comes from:

| Type | How |
|---|---|
| Google Docs, Slides | exported as `text/plain` |
| Google Sheets | exported as `text/csv` |
| `text/plain`, `text/markdown`, `text/csv`, `text/html`, `application/json` | downloaded |
| PDFs, images, everything else | **skipped**, and counted as skipped |

PDFs are skipped rather than stored as decoded bytes: a chunk of mojibake still
retrieves, and a citation pointing at garbage is worse than no citation. OCR/PDF
extraction is a separate decision with its own dependency cost.

Files over 5 MB are skipped. A 200 MB export becomes thousands of near-useless
chunks and stalls everything behind it in the run.

## Who can read it

An ingested document belongs to the principal that ingested it and is
`private:<that principal>`. There is no "ingest as shared" flag, on purpose: a
file shared *with* an account is not a file the whole instance may read, and an
unattended nightly job is exactly where that mistake would be silent and
irreversible.

It reaches another person only the way memory does:

* a **downward role read** (`memory.sharing.role_reads`, off by default), or
* an **explicit per-document grant**, made by the owner:
  `hermes memory rag --as <owner> share <document-id> <user>`.

Postgres RLS enforces the same matrix independently of the app-layer filter, on
both `rag_documents` and `rag_chunks`. A grant on a document reaches exactly that
document's chunks.

## Running it

```bash
# One staged pass over every connected account, newest modification first.
hermes memory rag --as leo_owner ingest-drive --limit 100 --verbose

# What is ingested, and what it cost.
hermes memory rag --as leo_owner documents

# Retrieval, as a human, without going through the agent.
hermes memory rag --as leo_owner search "when is the next tender due"

# Remove one document and its chunks (Drive file id).
hermes memory rag --as leo_owner forget 1AbCdEf...
```

`--limit` bounds **documents ingested or confirmed unchanged**, not files listed,
so a folder of photographs cannot consume a run's budget before it reaches a
document.

Accounts come from the credential directory
(`$HERMES_HOME/google-workspace/credentials/<email>.json` — the files the Google
Workspace MCP server already keeps), not from config, so connecting an account is
completing consent rather than editing YAML. Naming an account that has no
credential file is an error, not a silent no-op.

## Why it is staged, and nightly

Embedding is the expensive step: ~300 ms per chunk for `bge-m3` on this box's 4
shared vCPUs. A full backfill of an established Drive account is therefore hours
of CPU on the same cores the gateway serves conversations from. So:

* a run does one bounded pass, newest first — this week's tenders are searchable
  in the first minute, the 2019 archive arrives over subsequent nights;
* unchanged documents are detected by content hash and cost nothing, so a
  caught-up pass is nearly free and an interrupted run resumes rather than
  restarting;
* the timer runs at 03:20 with jitter, `Nice=15`, `IOSchedulingClass=idle` and
  `CPUWeight=20` — losing to a live conversation is the correct outcome for a
  background backfill.

Install the units from git rather than hand-writing them on the box:

```bash
install -m 644 deploy/hermes-rag-ingest.service /etc/systemd/system/
install -m 644 deploy/hermes-rag-ingest.timer   /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now hermes-rag-ingest.timer
```

`deploy_state.py`'s unit glob is `hermes-*`, so the installed copies and their
hashes land in the deployment-state repo and a divergence from the tracked file
appears in the weekly drift check.

## Re-ingestion triggers

A document is re-chunked and re-embedded when its text changes **or** when the
embedding model changes — vectors are only comparable within one model, so a
model switch *is* a content change as far as retrieval is concerned. After
`hermes memory vectors reembed` rewrites the memory tier, run one ingestion pass
per corpus to bring documents onto the new model; chunks from the old model are
excluded from search rather than ranked against the new ones.

## Turning retrieval on

Ingest first, then enable the tool:

```yaml
memory:
  rag:
    enabled: true
```

It is off by default because `rag_search` ships on every API call for the life of
a conversation, and on an empty corpus it can only answer "nothing found".
