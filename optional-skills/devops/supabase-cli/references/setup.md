# Setup & Authentication

## Install

The CLI is a single static binary. `npm i -g supabase` is unsupported by
upstream — use the release tarball (pin the version so a broken release can't
land silently):

```bash
V=2.107.0
curl -fsSL -o /tmp/supabase.tar.gz \
  "https://github.com/supabase/cli/releases/download/v${V}/supabase_linux_amd64.tar.gz"
tar -xzf /tmp/supabase.tar.gz -C /tmp supabase
sudo install -m 0755 /tmp/supabase /usr/local/bin/supabase
supabase --version
```

macOS: `brew install supabase/tap/supabase`.

The CLI prints an "a new version is available" notice on every run; it is a
notice, not an error.

## Personal access token

Create at https://supabase.com/dashboard/account/tokens — it is account-wide
(every project, full admin) and shown once. Store it as a credential:

```bash
grep -q '^SUPABASE_ACCESS_TOKEN=' ~/.hermes/.env \
  || printf 'SUPABASE_ACCESS_TOKEN=%s\n' "$TOKEN" >> ~/.hermes/.env
chmod 600 ~/.hermes/.env
```

`.env` is loaded into the environment at process start, so a running gateway
does not see a newly appended token until it restarts:

```bash
systemctl restart hermes-gateway     # or: /reload in an interactive session
```

Do not run `supabase login` when the env var is set — it opens a browser and
writes a second copy of the credential to `~/.supabase/access-token`.

## Verify

```bash
supabase projects list                      # CLI path
curl -fsS https://api.supabase.com/v1/projects \
  -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" | head -c 200   # API path
```

A `401` means the token is wrong or revoked; an empty list means the token
belongs to an account with no projects (a different account than expected).

## Scoping

There is no read-only or per-project PAT. If a task only needs one project's
data plane, prefer that project's `service_role` key against PostgREST over the
PAT, and keep the PAT for control-plane work (projects, migrations, functions).
