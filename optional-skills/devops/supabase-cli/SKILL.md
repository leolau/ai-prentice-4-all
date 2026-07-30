---
name: supabase-cli
description: "Inspect and manage hosted supabase.com projects and a self-hosted Supabase stack with the `supabase` CLI and Management API — list projects, run SQL, manage migrations, functions, and secrets. Triggers: supabase, supabase.com, project ref, postgres migration, edge function, RLS policy"
version: 1.0.0
author: joyaether
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [supabase, postgres, database, migrations, edge-functions, devops]
    category: devops
    requires_toolsets: [terminal]
---

# Supabase CLI

Reach Supabase from the terminal tool: hosted projects on supabase.com through
the `supabase` CLI / Management API, and a self-hosted stack through its own
Postgres.

Two credentials, two different jobs — mixing them up is the usual failure:

| Credential | Env var | Scope |
| --- | --- | --- |
| Personal access token (`sbp_…`) | `SUPABASE_ACCESS_TOKEN` | **Every project in the account.** Management API + CLI. |
| `anon` / `service_role` key (JWT) | per-project | One project's data plane (PostgREST, Auth, Storage). Never a substitute for the PAT. |

## When to Use

- "Can you access the Supabase of <project>?" — list projects, find the ref
- Inspect schema, run a read query, or apply a migration on a hosted project
- Manage Edge Functions, project secrets, or database branches
- Query the self-hosted stack on this host

## Prerequisites

```bash
supabase --version                  # install: see references/setup.md
[ -n "$SUPABASE_ACCESS_TOKEN" ] && echo "PAT present" || echo "PAT missing"
```

The PAT is a credential, so it lives in `~/.hermes/.env` as
`SUPABASE_ACCESS_TOKEN=sbp_…` — not in `config.yaml`. A gateway restart is
needed after adding it, because `.env` is read into the environment at startup.
`supabase login` is *not* needed when the env var is set; skip it (it wants a
browser).

## Workflow

### 1. Resolve the project ref first

Never guess a ref. Project names are fuzzy; refs are exact.

```bash
supabase projects list
```

The ref is the 20-char ID (e.g. `xnjpsbhlzgxbnrjdvwad`). Every later command
takes `--project-ref <ref>`.

### 2. Read before you write

```bash
supabase --project-ref <ref> inspect db table-stats     # largest tables
supabase --project-ref <ref> migration list             # local vs remote history
```

For arbitrary SQL, the Management API query endpoint is the shortest path:

```bash
curl -fsS -X POST "https://api.supabase.com/v1/projects/<ref>/database/query" \
  -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "select table_name from information_schema.tables where table_schema = '\''public'\'' order by 1"}'
```

### 3. Change schema through a migration, never ad-hoc DDL

```bash
supabase migration new add_widgets_table     # writes supabase/migrations/<ts>_*.sql
supabase db push --project-ref <ref>         # applies pending migrations
```

`db push` on a linked project asks for confirmation; add `--dry-run` first to
see what it would apply.

### 4. Edge Functions and secrets

```bash
supabase functions list  --project-ref <ref>
supabase functions deploy <name> --project-ref <ref>
supabase secrets  list   --project-ref <ref>     # names only, values are hidden
```

## Never Run These Without Explicit Approval

Each one destroys data or costs money, and none is reversible from the CLI:

- `supabase projects delete`, `supabase branches delete`
- `supabase db reset` — **drops and recreates the database**
- `supabase db push` against a production ref when `migration list` shows
  remote migrations you did not author
- `supabase link` inside an unrelated repo — it rewrites `supabase/config.toml`
- Any `drop`/`truncate`/`delete` SQL through the query endpoint

Ask first, quoting the exact command and the target ref.

## Self-Hosted Stack

A self-hosted stack is *not* reachable with the PAT — it has no Management API.
Talk to its Postgres directly (Hermes already resolves this DSN as
`datastore.supabase_app.dsn`):

```bash
psql "$DATABASE_URL" -c "\dn"                    # schemas
docker compose -f <stack>/docker/docker-compose.yml ps
```

Its `anon` / `service_role` keys live in the stack's own `docker/.env`; use them
against Kong (`http://127.0.0.1:8000/rest/v1/…`), not against api.supabase.com.

## References

- `references/setup.md` — install, PAT creation, verification
- `references/commands.md` — command map by task
