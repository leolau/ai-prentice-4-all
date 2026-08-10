# Hermes Agent — Session Hand-off

_Last updated: 2026-08-05 by Devin (session: verify production ECS deployment, requested by Leo)._

This note captures the current state so the next agent can pick up without
re-discovering context. It documents **live deployment status** only — no code
changes were made to the agent this session (only this hand-off note).

Product name: **ai-prentice-4-all** (built on Hermes). The operational
source of truth for the production cutover is
[`docs/design/SESSION-HANDOFF-2026-07-prod-cutover.md`](docs/design/SESSION-HANDOFF-2026-07-prod-cutover.md);
this file is the quick top-level summary.

## Live deployment status (verified 2026-08-05)

The production ai-prentice-4-all (Hermes) is **running and healthy** on Alibaba
Cloud ECS.

- **Host:** ECS instance `hermes-systest` (`i-j6c81aisv2dd8mg17yle`)
  - Public EIP `47.83.199.25` (= Cloudflare DNS `home.leolau.ai-and-i.io`), private IP `172.29.18.231`
  - Region `cn-hongkong` (VPC), `ecs.e-c1m4.xlarge` (4 vCPU / 16 GB), Ubuntu 24.04, `Running`
  - Public product URL: <https://leolau.ai-and-i.io> (HTTPS via Caddy, password-gated dashboard)
- **Deploy layout:** code at `/opt/data/hermes-agent`, venv at
  `/opt/data/hermes-agent/.venv`, data under `/opt/data`.
- **Deployed revision:** branch `develop` @ `9ac3ce251`
  (_Merge PR #115 — handover-freshness_).
- **Runs directly on the host via `systemd`** (no `hermes-agent` docker container
  anymore — see the change note below). The Supabase self-host stack (11
  containers: db, auth, storage, rest, realtime, kong, studio, meta, pooler,
  edge-functions, imgproxy) also runs on this host via docker.

`systemd` services observed (all `active (running)`):

| Service | Notes |
|---|---|
| `hermes-gateway.service` | Main messaging gateway (systest); active since 2026-08-05 12:35 CST |
| `hermes-dashboard.service` | Web dashboard on `:9119` (`0.0.0.0`); returns HTTP 302 (healthy) |
| `hermes-embed.service` | Local embedding server `BAAI/bge-m3` on `127.0.0.1:8791` (loopback only) |
| `hermes-wa-bridge-personal.service` | WhatsApp bridge, session `personal`, `127.0.0.1:3000` |
| `hermes-wa-bridge-connectar.service` | WhatsApp bridge, session `connectar`, `127.0.0.1:3001` |
| `hermes-wa-batcher.service` / `hermes-wa-triage.service` | WhatsApp batcher + triage agent |
| `hermes-email-poller.service` / `hermes-email-batcher.service` / `hermes-email-triage.service` | Gmail IMAP poller + batcher + triage agent |
| `hermes-digest.service` | Hourly digest (Telegram) |
| `hermes-escalation.service` | Escalation pusher (Telegram) |

MCP servers running under the gateway: `workspace-mcp` (calendar/docs/drive),
`awslabs.aws-api-mcp-server`, and `figma-developer-mcp`.

### What changed since the previous hand-off (2026-07-05)

- **Production host moved.** The old box `ai-prentice` /
  `ai-prentice-agentdoc` (`i-j6camnt3ocwlmzajthil`, `8.217.86.90`,
  `ecs.e-c1m2.large` 2 vCPU / 4 GB) that ran the dockerized `hermes-agent:local`
  container **no longer exists** (released — `DescribeInstances` returns 0 for
  that ID). Production is now the "strong box" `hermes-systest`.
- **No docker for the agent.** The agent + all sidecars now run as native
  `systemd` units from `/opt/data/hermes-agent`, not inside a `hermes-agent`
  docker container. Docker on the host is used only for the Supabase stack.

### How this was verified

Via the Alibaba Cloud OpenAPI (`aliyun` CLI, region `cn-hongkong`):
`DescribeInstances` to locate the host and confirm the old one is gone, then
`RunCommand` (Cloud Assistant `RunShellScript`) + `DescribeInvocationResults`
to run `docker ps`, `ps aux`, `systemctl list-units`, `git log`, `curl`, and
`ss` on the instance. No SSH key needed — Cloud Assistant runs commands on the
instance directly.

## Environment / access notes

- Alibaba Cloud is reachable via the `aliyun` CLI (and the `alibaba-cloud` MCP
  when it starts) using `ALIBABA_CLOUD_ACCESS_KEY_ID` /
  `ALIBABA_CLOUD_ACCESS_KEY_SECRET`. During this session the `alibaba-cloud` MCP
  stdio server failed to initialize, so the `aliyun` CLI was used directly.
- Only one ECS instance now exists in `cn-hongkong` (`hermes-systest`); the old
  rollback box has been released.

## Suggested next steps

- No pending code changes from this session. Pick up feature/bug work from the
  active branch (`develop`).
- To inspect or restart the live agent, use the `aliyun` CLI `RunCommand`
  against `i-j6c81aisv2dd8mg17yle` (`cn-hongkong`), or `systemctl` on the box,
  rather than direct SSH.
