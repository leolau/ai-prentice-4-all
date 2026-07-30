# Session Hand-off — writable design + cloud MCP integrations (2026-07)

> **Purpose:** let the next agent pick up the MCP integration work without
> re-deriving which providers can be reached, how each one authenticates, and
> which of them lie about it. Companion to
> [`SESSION-HANDOFF-2026-07-prod-cutover.md`](./SESSION-HANDOFF-2026-07-prod-cutover.md),
> which documents the hosts, services, and profiles this work runs on.
>
> Scope of the session: Figma (writable), Canva, Vercel, Railway, AWS.

---

## 0. TL;DR

Five MCP servers are configured on the live box (`hermes-systest`,
`HERMES_HOME=/opt/data/hermes-home-staging`); the sixth is catalogued and
verified locally but deliberately not installed there yet (§2.5):

| Server | Endpoint | Auth | Status | Notes |
|--------|----------|------|--------|-------|
| `figma` | `figma-developer-mcp@0.13.2` (stdio) | PAT | 2 tools | **read-only by construction** |
| `canva` | `https://mcp.canva.com/mcp` | native OAuth (DCR) | 33 tools | write scopes granted |
| `vercel` | `https://mcp.vercel.com` | native OAuth (DCR) | 31 tools | purchase tools excluded |
| `railway` | `https://mcp.railway.com` | native OAuth (DCR) | 26 tools | **token expires hourly, no refresh** |
| `aws_knowledge` | `https://knowledge-mcp.global.api.aws/mcp` | none | 5 tools | docs/reference only |
| `aws-api` | `uvx awslabs.aws-api-mcp-server==1.3.47` (stdio) | IAM access key | 2 tools | catalogued, **not yet on the box**; reaches real resources, read-only by default |

Writable **Figma** is not an MCP server here — it is a skill
(`skills/creative/figma-write`) that shells out to Claude Code, because Figma's
own MCP refuses clients it hasn't catalogued. Durable **Railway** access is also
a skill (`skills/software-development/railway-cli`) driving the `railway` CLI
with an account API token, because Railway will not issue a refresh token to a
self-registered OAuth client.

Merged this session: #65 (figma-write skill), #66 (canva catalog entry),
#67 (vercel/railway/aws-knowledge catalog entries + POST preflight fix +
railway-cli skill). Open: #68 (authorization-URL query fix).

**Target branch is `develop`, not `main`.** `main` is a stale divergent line
(~127 commits behind at the time of writing); PRs #66/#67 were opened against it
by mistake and had to be retargeted. Check `git log origin/develop..origin/main`
before assuming which is current.

---

## 1. The decision that generalizes: what kind of integration is possible?

Remote MCP providers fall into three classes, and the class determines the
footprint-ladder rung (see `AGENTS.md`). Classify **before** designing:

1. **Open dynamic client registration (DCR)** — the authorization server's
   `/register` endpoint accepts anyone. Hermes connects natively; the whole
   integration is a catalog manifest (rung 5). Canva, Vercel, Railway.
2. **Catalogued clients only** — registration returns `403` for unknown clients;
   only vendor-approved client IDs (Claude Code, Codex, Cursor…) may connect.
   Bridge through one of those clients behind a skill (rung 2). Figma.
3. **No hosted server at all** — the vendor ships local stdio servers that wrap
   their own CLI/SDK and need cloud credentials. AWS account access
   (`uvx awslabs.aws-api-mcp-server`). Deliberately not set up here.

Probe order that answers this in about two minutes, without any client code:

```bash
# 1. Is there a protected-resource document, and who is the auth server?
curl -s https://mcp.example.com/.well-known/oauth-protected-resource | jq

# 2. Does that auth server allow open registration?
curl -s -X POST https://auth.example.com/register \
  -H 'Content-Type: application/json' \
  -d '{"client_name":"probe","redirect_uris":["http://127.0.0.1:37413/callback"],
       "grant_types":["authorization_code"],"response_types":["code"],
       "token_endpoint_auth_method":"none"}'
# 201 => class 1. 403 => class 2. 404/no metadata => class 3 (or not an MCP server).

# 3. Is the URL even right? Speak MCP at it directly.
curl -s -X POST https://mcp.example.com -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-06-18","capabilities":{},
       "clientInfo":{"name":"probe","version":"1"}}}'
```

Send a `User-Agent`; Railway's metadata endpoints answer `403` to bare
`urllib` defaults. Do not loop on `/register` — Railway rate-limits it (`429`,
~15 min), and a cached client file is the difference between a two-minute retry
and a fifteen-minute wait.

---

## 2. Per-provider findings

### 2.1 Figma — read-only PAT, catalogued clients only

- The installed `figma-developer-mcp` exposes only `get_figma_data` and
  `download_figma_images`; it issues REST GETs. Read-only **by construction**, not
  by permission. No token upgrade changes this.
- Figma REST itself cannot write canvas at any tier: PAT scopes cover comments,
  dev resources, and webhooks (variables are Enterprise-only). Canvas writes exist
  only through Figma's own MCP (`use_figma`).
- `https://mcp.figma.com/mcp` rejects PATs (`401`, wants an OAuth token with
  `mcp:connect`) and `403`s dynamic registration. It also requires a **Full**
  seat — the account here reports `"seat": "Full"`, tier pro, so writes are
  allowed once a catalogued client holds the token.
- Solution: Claude Code installed on the box (backed by the existing DeepSeek
  config — no extra subscription), OAuth'd to `mcp.figma.com`, invoked by
  `skills/creative/figma-write/scripts/figma_write.sh`. Re-auth helper:
  `figma_login.sh start|complete|status`.
- Always pass the file/selection URL in the prompt; `use_figma` has no notion of
  "the file we were just working on". Images and custom fonts are unsupported;
  large pages land better as several incremental prompts.
- Verified by creating a real file with frames and text
  (`figma.com/design/jy44V4TeS6S3aLfToNF328`).

### 2.2 Canva — the easy case

Open DCR, native OAuth, write scopes advertised on the protected-resource
document (`design:content:write`, `folder:write`, `asset:write`,
`comment:write`). Config is three lines; the write path is
`start-editing-transaction` → `perform-editing-operations` →
`commit-editing-transaction`. Verified by generating a real presentation.

### 2.3 Vercel — open DCR, plus tools that spend money

Endpoint is the **bare host** `https://mcp.vercel.com`; `/mcp` is a 404.
Advertised scopes are only `openid offline_access` — the token works regardless.

Its toolset includes `buy_pro`, `buy_credits`, `buy_addon`, and `buy_domain`,
which charge the account with no confirmation step of their own. They are
excluded in the live config and should stay excluded unless the operator asks:

```yaml
vercel:
  url: "https://mcp.vercel.com"
  auth: oauth
  enabled: true
  tools:
    exclude: [buy_pro, buy_credits, buy_addon, buy_domain]
```

Worth applying the same review to any future provider's tool list before
enabling it.

### 2.4 Railway — connects, but cannot stay connected

Endpoint is the bare host `https://mcp.railway.com` (`/mcp` 404s). OAuth works
and discovers 26 tools (projects, services, deployments, variables, domains,
metrics, logs, feature flags, railway-agent). Everything after that is a wall:

| Attempt | Result |
|---------|--------|
| Public client + `offline_access` in the authorize request | Token returns `scope: openid profile email workspace:member` — `offline_access` **silently dropped**, `expires_in: 3600`, no refresh token |
| Confidential client (`client_secret_post`) | Registration accepts `offline_access`, but the token endpoint rejects its own DCR-issued secret: `401 invalid_client: client authentication failed` |
| Same, `client_secret_basic` | Identical `invalid_client` |
| Device-code grant (client registered *with* `urn:ietf:params:oauth:grant-type:device_code`) | `invalid_request: … is not allowed for this client` — advertised in metadata, reserved for Railway's own CLI |

So: **no non-interactive OAuth path exists for a self-registered Railway
client.** The MCP server is usable only in bursts of one hour, each needing a
browser approval. Durable access is the CLI + an account API token
(`skills/software-development/railway-cli`).

Railway's token *types* also produce a convincing false negative — this cost
two rounds of "the token you gave me is dead":

- **Team/workspace token** — valid, but has no user behind it, so
  `railway whoami`, `railway list`, and any GraphQL touching `me { … }` return
  `Unauthorized`/`Not Authorized`. Verify with
  `railway api '{ projects { edges { node { id name } } } }'`.
- **Account token** (team dropdown left empty at creation) —
  `whoami`/`list` work; the flat `projects` query returns `{"edges": []}`
  regardless. Verify with `railway list` or `{ me { workspaces { id name } } }`.

Never conclude a Railway token is invalid from one query shape. The live box
currently holds an **account** token in
`/opt/data/hermes-home-staging/.env` as `RAILWAY_API_TOKEN`.

### 2.5 AWS — two unrelated things

- **AWS Knowledge MCP** (`https://knowledge-mcp.global.api.aws/mcp`) is public,
  needs no credentials, and gives 5 tools: `aws___search_documentation`,
  `aws___read_documentation`, `aws___list_regions`,
  `aws___get_regional_availability`, `aws___retrieve_skill`. It reaches **no AWS
  resources** — documentation and regional metadata only. The manifest says so
  explicitly so nobody expects otherwise.
- **AWS account access** is a different integration: awslabs' local stdio servers
  (`uvx awslabs.aws-api-mcp-server`) driving the AWS CLI, requiring IAM
  credentials on the box. Now catalogued as `aws-api` (class 3 — no hosted
  server) and verified end-to-end against account `454267863464`: 2 tools
  (`call_aws`, `suggest_aws_commands`), with `aws sts get-caller-identity`
  returning 200 through Hermes' own stdio spawn path.
  - The server's own defaults are permissive: mutations are **allowed**
    (`READ_OPERATIONS_ONLY` defaults false) and it tags its AWS user-agent with
    the MCP client name and config flags (`AWS_API_MCP_TELEMETRY` defaults
    true). The manifest collects both with safe defaults instead. With read-only
    on, a mutating command is refused before it reaches AWS ("Execution of this
    operation is denied by security policy" — verified with `ec2 create-tags`).
  - `READ_OPERATIONS_ONLY` is a client-side guard, not a permission boundary.
    The key still wants a least-privilege IAM policy; the two working keys the
    agent holds are `full-admin` (454267863464) and `devin-egobid`
    (444643374336), so neither is a good fit for the box yet.
  - **Catalogued stdio servers with `auth: api_key` could not work at all**
    before this: a stdio child gets a *filtered* environment
    (`tools/mcp_tool.py:_build_safe_env` — PATH/HOME/XDG_* only), and the
    catalog wrote the credentials to `.env` without naming them in the server
    config, so the server started with none (`n8n` had the same hole). The
    install now writes `env: {VAR: "${VAR}"}` per collected var; the plaintext
    stays in `.env`.
  - Ambient `AWS_*` variables in the parent process shadow `$HERMES_HOME/.env`
    when `${VAR}` is resolved. On a dev box that already exports AWS keys, a
    `SignatureDoesNotMatch` is that shadowing, not a broken manifest.

---

## 3. Bugs found in Hermes itself

Both were found only by driving real providers; neither would surface against a
mocked server.

1. **Endpoint preflight rejected POST-only MCP servers** (fixed, #67).
   `_preflight_content_type()` judged an endpoint from HEAD/GET alone. AWS
   Knowledge serves an HTML landing page on GET and JSON-RPC on POST, so
   `hermes mcp test aws_knowledge` failed with
   `returned Content-Type 'text/html', not an MCP response`. Now a non-MCP
   HEAD/GET verdict is confirmed with an `initialize` POST
   (`_speaks_mcp_on_post()`) before rejecting. The verdict is the POST's
   **content type, not its status** — an MCP server answers in JSON/SSE whether
   it accepts (`200`) or refuses (`401` no token, `400` bad session), while a web
   app returns HTML or `501`, so genuine wrong-URL typos are still caught.

2. **Authorization URL lost every parameter when the endpoint had a query**
   (fix in #68). The SDK builds
   `f"{authorization_endpoint}?{urlencode(params)}"` (mcp
   `client/auth/oauth2.py:346`). Railway publishes
   `.../oauth/auth?resource=https://backboard.railway.com`, so the result had two
   `?` and everything the SDK appended parsed as part of the endpoint's own
   `resource` value — the provider saw no `client_id`, `state`, or PKCE
   challenge. `_normalize_authorization_url()` in `tools/mcp_oauth.py` re-splits
   the flattened query and lets the last occurrence of each key win, so the SDK's
   parameters (appended last, including the RFC 8707 `resource` naming the MCP
   server) take effect. Any provider with a parameterized authorize endpoint was
   affected, not just Railway.

Still open, if someone wants it: an **expired non-refreshable token produces a
40-second hang** rather than a clear error. Railway answers `403` (not `401`) to
a stale token, `_is_auth_error()` only recognizes `401`, so the flow falls
through to an interactive re-auth that cannot complete headless and times out at
`configured timeout: 40.0s`. A clean fix would classify `403`-with-expired-token
as an auth error and fail fast with "run `hermes mcp login <server>`".

---

## 4. Headless OAuth on the box — the working procedure

The callback listener runs on the ECS box; the operator's browser is elsewhere,
so the redirect to `127.0.0.1:<port>/callback` always shows a connection error.
That error is expected and the URL in the address bar is the payload.

Hermes' built-in prompt waits only ~5 minutes, which one Canva code outran. For
anything interactive, use the split helper instead — it has no deadline between
the two halves:

```bash
python3 /opt/data/mcp_oauth_manual.py start <server> <mcp-url> [extra-scope …]
# operator approves in a browser, pastes back the failed callback URL
python3 /opt/data/mcp_oauth_manual.py complete <server> "<redirect URL>"
```

It performs discovery, DCR, PKCE, and the token exchange, and writes
`$HERMES_HOME/mcp-tokens/<server>.json` at mode `0600`. A Canva-specific variant
lives at `/opt/data/canva_oauth.py`. With #68 merged, `hermes mcp login
<server>` prints a usable URL directly and the helper is only needed when the
5-minute window is a problem.

Ask for the **whole** callback URL, not just the code: the `state` must match.

---

## 5. Operating the live box

```bash
cd /home/ubuntu && ./run.sh '<shell command>'      # ECS RunCommand + poll, on the agent VM
```

- Install: `/opt/data/hermes-agent` (venv `.venv`); home:
  `/opt/data/hermes-home-staging`; service: `hermes-gateway.service`.
- Config: `/opt/data/hermes-home-staging/config.yaml`; tokens:
  `.../mcp-tokens/<server>.json`; secrets: `.../.env` (mode `0600`).
- Verify a server: `hermes mcp test <name>` with `HERMES_HOME` exported and both
  `.env` files sourced.
- RunCommand has a **content-length limit** — pushing a whole large module fails
  with `CmdContent.ExceedLimit`. Send a `git diff` and `patch -p1` instead of the
  file.
- `.env` is for credentials only. Behavioral settings go in `config.yaml`
  (`AGENTS.md`), so a token belongs there but a timeout does not.

---

## 6. Follow-ups

1. **#68** — merge the authorization-URL fix; then Railway/any parameterized
   authorize endpoint re-auths through plain `hermes mcp login`.
2. **Fail fast on expired tokens** (§3) — the 40-second hang is a real
   papercut on every session that has a stale Railway token.
3. ~~**AWS account access**~~ — done: `optional-mcps/aws-api`, verified live
   (§2.5). Remaining: install it on `hermes-systest` with a **least-privilege**
   key rather than the `full-admin` one, and decide which account.
4. **Prune tool lists.** figma + canva + vercel + railway + aws_knowledge ship
   ~97 tools into every prompt. Prompt caching is sacred (`AGENTS.md`); trim per
   server with `hermes mcp configure <name>` to what the operator actually uses.
5. **Reconcile `main` and `develop`** — they have diverged badly, and PRs keep
   getting aimed at the wrong one.
6. **Railway MCP vs CLI** — decide whether to keep the MCP server enabled at all
   given the hourly expiry, or disable it and rely on the `railway-cli` skill
   until Railway supports non-interactive clients.
