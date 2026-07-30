# Command Map

`<ref>` is always the 20-char project ref from `supabase projects list`.

## Projects & organizations

```bash
supabase projects list
supabase projects api-keys --project-ref <ref>     # anon / service_role keys
supabase orgs list
```

## Schema & data

```bash
supabase inspect db table-stats   --project-ref <ref>
supabase inspect db index-stats   --project-ref <ref>
supabase inspect db long-running-queries --project-ref <ref>
supabase inspect db locks         --project-ref <ref>
supabase inspect db bloat         --project-ref <ref>
```

Arbitrary SQL — Management API (no local project dir needed):

```bash
curl -fsS -X POST "https://api.supabase.com/v1/projects/<ref>/database/query" \
  -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "<sql>"}'
```

Read the result before writing anything. Wrap multi-statement DDL in a
migration instead.

## Migrations

```bash
supabase migration list  --project-ref <ref>    # local vs remote, drift shows here
supabase migration new   <name>
supabase db diff         --project-ref <ref> -f <name>   # generate from remote drift
supabase db push         --project-ref <ref> --dry-run
supabase db push         --project-ref <ref>
```

`supabase db reset` is local-only *by design* but destroys the linked database
when a project is linked — treat it as destructive, always.

## Edge Functions

```bash
supabase functions list     --project-ref <ref>
supabase functions download <name> --project-ref <ref>
supabase functions deploy   <name> --project-ref <ref>
supabase functions delete   <name> --project-ref <ref>     # approval required
```

## Secrets

```bash
supabase secrets list --project-ref <ref>                  # names + digests only
supabase secrets set  KEY=value --project-ref <ref>
supabase secrets unset KEY --project-ref <ref>
```

Never echo a secret value into chat or a log; `secrets list` is safe, `set` is
not (the value is on the command line — prefer `--env-file`).

## Branches (paid feature)

```bash
supabase branches list   --project-ref <ref>
supabase branches create <name> --project-ref <ref>
supabase branches delete <id>   --project-ref <ref>        # approval required
```

## Storage

```bash
supabase storage ls ss:///<bucket> --project-ref <ref>
supabase storage cp  <local> ss:///<bucket>/<path> --project-ref <ref>
```

## Auth users

The CLI has no user-admin surface; use the Auth admin REST API with that
project's `service_role` key:

```bash
curl -fsS "https://<ref>.supabase.co/auth/v1/admin/users?per_page=5" \
  -H "apikey: $SERVICE_ROLE_KEY" -H "Authorization: Bearer $SERVICE_ROLE_KEY"
```
