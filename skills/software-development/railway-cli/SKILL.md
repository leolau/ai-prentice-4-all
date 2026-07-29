---
name: railway-cli
description: "Railway deploys, logs, and vars via CLI + GraphQL API tokens."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Railway, Deployment, PaaS, GraphQL, DevOps, Logs]
    related_skills: [github-repo-management]
prerequisites:
  commands: [railway]
  env: [RAILWAY_API_TOKEN]
---

# Railway via CLI + API token

Manage Railway projects, services, deployments, variables, and logs with the
`railway` CLI and its GraphQL passthrough. Use this when Railway's MCP server is
not connected — its OAuth access tokens last **one hour** and Railway does not
issue refresh tokens to self-registered clients, so an API token is the only
non-interactive path.

## When to Use

- Deploy, redeploy, or roll back a Railway service
- Read deploy/runtime logs or deployment status
- List or set service variables and domains
- Anything Railway-related when `hermes mcp test railway` reports no token

## Setup

```bash
npm i -g @railway/cli          # provides `railway`
railway --version
```

Put the token in `~/.hermes/.env` (it is a credential, so it belongs there):

```
RAILWAY_API_TOKEN=<token from https://railway.com/account/tokens>
```

Token types matter, and the failure mode looks identical for both:

- **Account token** (no team selected at creation) — everything works, including
  `railway whoami` and `railway list`.
- **Team/workspace token** — has no associated user, so `whoami`, `list`, and any
  GraphQL query touching `me { ... }` return `Unauthorized` / `Not Authorized`
  **even though the token is valid**. Do not conclude the token is bad from
  `whoami` alone; confirm with the `projects` query below.

## Verify the token

```bash
railway api '{ projects { edges { node { id name } } } }'
```

A `data.projects` payload means the token works. If this fails too, the token is
genuinely invalid or revoked — ask for a new one rather than retrying.

## Query and mutate through GraphQL

`railway api` speaks Railway's public GraphQL API directly, which covers
everything the CLI's typed subcommands do and more. Explore the schema first —
guessing field names wastes turns:

```bash
railway api search deployment          # find types/fields by term
railway api describe Deployment        # show a type's fields
railway api schema > /tmp/railway.graphql
```

Then run documents, inline or from a file:

```bash
railway api '{ project(id: "<project-id>") { services { edges { node { id name } } } } }'
railway api -f /tmp/mutation.graphql
```

## Project-scoped CLI commands

The typed subcommands act on a *linked* project directory:

```bash
railway link --project <project-id> --environment production   # writes .railway/
railway status
railway logs --deployment            # deploy logs; --build for build logs
railway redeploy --yes
railway variables                    # list; --set 'KEY=value' to write
railway domain                       # list/add domains
```

Run them from the linked directory (or pass `--project`/`--service` explicitly);
without a link they fail with a project-not-found error, not an auth error.

## Notes

- Mutations here are real infrastructure changes. Confirm the target project,
  service, and environment before `redeploy`, `down`, `variables --set`, or
  `delete` — Railway applies them immediately with no staging step.
- `railway agent` and `railway setup agent` drive Railway's own AI agent and
  install its MCP server; do not run them here — Hermes manages MCP servers
  itself (`hermes mcp install railway`).
- Prefer the MCP server for read-heavy exploration when it *is* authorized: it
  returns structured tool results instead of raw GraphQL.
