---
name: remember-file
description: Remember a file from the inbound registry by ingesting it into the RAG corpus
---

# Remember File

You help the user remember a file that arrived through chat, Telegram,
WhatsApp, or email. "Remembering" means ingesting the file's text into the
RAG corpus so it can be searched and cited later.

## When to use this skill

- The user says "remember that grant PDF" or "save this file to memory"
- A triage skill (email/whatsapp/calendar) decides a file matters and calls
  this skill with `--remembered-by <skill-name>`

## How it works

Run the CLI command:

```bash
hermes memory rag remember-file <asset_id> --as <user_id> [--remembered-by <name>]
```

- `asset_id` — the UUID from the `/files` page or `file_assets` table.
  If the user says "remember the file I just sent" but doesn't give an ID,
  check the `/files` page or ask them which file.
- `--as` — the principal's user_id (e.g. `leo_owner`).
- `--remembered-by` — who decided this file matters. Default is `user`.
  Triage skills pass their own name (e.g. `email-triage`) so the audit
  line answers "why is this in my memory".

## What it does

1. Downloads the file bytes from Supabase Storage.
2. Extracts text — `.md`, `.txt`, `.csv` are read as UTF-8; `.pdf` is
   extracted via pypdf; `.docx` via python-docx. These install
   automatically on first use (lazy-deps).
3. Ingests as a RAG document with `source_kind='file'` and
   `source_ref=<asset_id>`.
4. Stamps `document_id` on the `file_assets` row so the `/memory` page
   links to the file.

## Notes

- PDFs and DOCX are handled directly — no need to convert first.
- Images, voice notes, and binary files are skipped (no extractable text).
- A file already remembered is not re-ingested unless `--force` is passed.
- This is a CLI command, not a model tool — it costs nothing on every
  API call. It runs through the shell.
