# MCP approval gating on hermes-systest

Which MCP tools require the operator to approve each call, and why the rest
do not. The patterns themselves live in the deployment's `config.yaml`, which
is not in the repository — this is the record of what is deployed and the
reasoning behind it, so a rebuild reproduces the decision rather than the
default.

## Deployed patterns

```yaml
plugins:
  enabled:
    - tool-approval

approvals:
  tools:
    - mcp_aws_api*        # all three AWS account servers, reads included
    - mcp_canva_*
    - mcp_github_*
    - mcp_google_workspace_*
    - mcp_railway_*
    - mcp_vercel_*
  tools_timeout: 300
  tools_respect_bypass: false
```

`tools_respect_bypass: false` means `/yolo` and `--yolo` do not skip these
prompts. A blanket bypass is a session convenience; an explicit per-tool gate
on credentialed infrastructure outranks it.

## Coverage

Measured by matching the patterns against the tool names actually registered
after connecting to every server, not by counting the patterns:

| server | tools | gated |
|---|---:|---|
| `github` | 48 | yes |
| `canva` | 33 | yes |
| `vercel` | 31 | yes |
| `railway` | 26 | yes |
| `aws-api` | 2 | yes |
| `aws-api-arprod` | 1 | yes |
| `aws-api-egobid` | 1 | yes |
| `google-workspace` | 8 | yes |
| `aws_knowledge` | 5 | **no** |
| `figma` | 2 | **no** |

**150 of 157 tools gated.**

`google-workspace` is one server for three Google accounts, so the identity a
call touches is an argument (`user_google_email`) rather than a property of
the server. The approval prompt shows it; the gate cannot distinguish the
accounts. If one account ever needs a different policy than the others, that
is a second server with its own credentials directory, not a pattern change.

### Why the remaining seven are not gated

`aws_knowledge` serves AWS's public documentation. It holds no account
credential and cannot reach an account — `read_documentation`,
`search_documentation`, `list_regions`, `get_regional_availability`,
`retrieve_skill`. Gating it would train the operator to approve prompts
reflexively, which is the failure mode that makes gating worthless where it
matters. Note the pattern is `mcp_aws_api*`, not `mcp_aws_*`, precisely so
this server stays out.

`figma` reads design files with a scoped token. Gate it if that token ever
gains write scope.

## Reads are gated too

The gated set includes read-only calls (`mcp_github_get_file_contents`,
`mcp_vercel_list_projects`, `mcp_aws_api_suggest_aws_commands`). That is
deliberate. The agent processes untrusted inbound content — WhatsApp,
Telegram, email — so a successful prompt injection reaches these tools with
the deployment's real credentials. Exfiltration through reads is as much a
loss as a mutation, and the mutation/read split is the server's own
classification, which is exactly what an attacker would be steering.

## Applying a change

The patterns are read through `load_config_readonly()`, whose cache key is
the config file's `(mtime_ns, size)`. Editing `config.yaml` therefore takes
effect on the next tool call — **no gateway restart, no dropped session.**
Verify by loading the config as the service user rather than by reading the
file back:

```bash
sudo -u hermes env HERMES_HOME=/opt/data/hermes-home-staging \
  /opt/data/hermes-agent/.venv/bin/python -c \
  "from hermes_cli.config import load_config_readonly; \
   print(load_config_readonly()['approvals']['tools'])"
```

## Two things that bite when probing this on the box

**Run probes from a directory the service user can read.** The AWS API MCP
server's settings loader stats `.env` relative to the current working
directory; started from a directory `hermes` cannot read (`/root`, say), it
dies with `PermissionError: '.env'` and the connection fails with a bare
`Connection closed`. The systemd units set `WorkingDirectory`, so services
are unaffected — but a manual probe inherits whatever cwd it was given, and
the failure looks like a broken server rather than a broken invocation.

`workspace-mcp` has the same loader and bites harder, because the cwd it
inherits is a *service's* working directory rather than a probe's. Its entry
wraps the launch in `/bin/sh -c 'cd … && exec uvx …'` for that reason — both
to avoid the unreadable-`.env` crash and to keep it from reading Hermes' own
`.env` as its settings when launched from `HERMES_HOME`.

**Never clean up MCP subprocesses by pattern.** `hermes-gateway` and
`hermes-dashboard` each keep their own long-lived `uvx`/`npx` MCP children,
indistinguishable by command line from a probe's. Kill by cgroup
(`/proc/<pid>/cgroup`) or by descendants of the probe's own PID; a
`pkill -f aws-api-mcp-server` takes out a running service's tools and leaves
the service up and apparently healthy with dead pipes.

## Not covered

There is **no Alibaba Cloud MCP server installed**, so nothing gates Alibaba
operations — the box also has no `aliyun` CLI, so the current answer is that
the agent has no Alibaba path at all.
