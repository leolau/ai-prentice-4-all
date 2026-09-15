# Qoder CLI remote-control daemons (production box) — NOT part of Hermes

**These are a separate tool, unrelated to Hermes.** They run on the same
Hetzner box (`188.245.219.105`, see [`PRODUCTION.md`](./PRODUCTION.md)) as a
different, unprivileged Linux user (`aicoder`), for a coding-agent product
called **Qoder** (`@qoder-ai/qodercli`, npm). They are documented here only
because they compete with Hermes for RAM/swap on the same host, and an agent
diagnosing "why is this box slow / swapping" needs to know they exist and how
to safely stop/start them. **Do not touch them as part of a Hermes deploy or
troubleshooting session unless the human operator explicitly asks.**

## What they are

One persistent **systemd service per repository** under
`/opt/data/aicoding/repos/`, each running `qoder remote-control` for that repo
so a mobile/web Qoder client can open a live coding session against it at
any time without a cold start. There's also one shared web-terminal service.

```
User            aicoder   (unprivileged, separate from the hermes service user)
Repos root      /opt/data/aicoding/repos/<name>
Binary          /usr/bin/qoder   (wraps /usr/lib/node_modules/@qoder-ai/qodercli/bundle/qodercli.js)
Unit pattern    qoder-daemon-<repo>.service   (ai-prentice-4-all's is named plain `qoder-daemon.service`)
Companion unit  qoder-ttyd.service            (ttyd web terminal onto a persistent tmux session)
```

As of 2026-09-15 there are **21 per-repo daemons + 1 ttyd unit** (22 total),
each daemon spawning 2 processes (a thin `qoder` launcher + the `qodercli.js`
Node process it execs), so ~44 processes and roughly **4.5–5 GB RSS**
combined when all are up. See `docs/deployment/PRODUCTION.md` for the box's
overall memory picture.

Full current list (get the live list any time with the command in "Checking
status" below):

```
qoder-daemon-ai-and-i.service
qoder-daemon-ar-fashion-designer.service
qoder-daemon-arfd-portal.service
qoder-daemon-class-intelligence.service
qoder-daemon-diamondbox.service
qoder-daemon-diy-client.service
qoder-daemon-diy-portal.service
qoder-daemon-ebid-mobile.service
qoder-daemon-ebid-portal.service
qoder-daemon-ebid-server.service
qoder-daemon-greenfield.service
qoder-daemon-learn-word-la-web.service
qoder-daemon-learn-word-la.service
qoder-daemon-next-supabase-cms-template.service
qoder-daemon-P-Univ.service
qoder-daemon-peeppop-server.service
qoder-daemon-peeppop.service
qoder-daemon-proxy-advisor.service
qoder-daemon-snappop-portal.service
qoder-daemon-storytellar-webar.service
qoder-daemon.service                    # ai-prentice-4-all (this repo)
qoder-ttyd.service                      # shared web terminal, no repo/envId
```

Each unit file looks like this (example, `qoder-daemon.service`):

```ini
[Unit]
Description=Qoder remote-control daemon for ai-prentice-4-all
After=network-online.target

[Service]
User=aicoder
ExecStart=/usr/bin/qoder remote-control --name ai-prentice-4-all --directory /opt/data/aicoding/repos/ai-prentice-4-all --spawn worktree
Restart=always
RestartSec=5
CPUWeight=50
MemoryMax=4G
Nice=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

## ⚠️ `Restart=always` — killing the PID does NOT stop it

Every daemon unit has `Restart=always` + `RestartSec=5`. If you `kill` the
underlying `node` process directly (or `pkill -f qodercli`), systemd will
just relaunch it ~5 seconds later. **The only real way to stop one is
`systemctl stop`** (which is what an operator who "killed" one and saw it
come back was hitting).

## Checking status

```bash
# One-line status for every qoder unit, with the friendly description:
systemctl list-units --all --no-legend 'qoder-*'

# Which ones are currently down:
systemctl list-units --all --no-legend --state=inactive,failed 'qoder-*'

# Detail on one unit (restart count, since-when):
systemctl show qoder-daemon-<repo>.service -p ActiveEnterTimestamp -p SubState -p NRestarts

# The mobile/web session URL a given daemon is currently registered under
# (each restart mints a new envId — the latest line in that unit's journal wins):
journalctl -u qoder-daemon-<repo>.service --no-pager | grep -oE 'envId=env_[a-z0-9]+' | tail -1
# → visit https://qoder.com/agents/session/new?envId=<that id>
```

`qoder-ttyd.service` has no repo/envId — it's a raw ttyd web terminal onto a
shared `tmux` session (`aicoder`), independent of the per-repo daemons.

## Stopping

```bash
# Stop one:
systemctl stop qoder-daemon-<repo>.service

# Stop all 21 daemons + the ttyd terminal:
systemctl stop 'qoder-daemon*' qoder-ttyd.service
```

This is a plain `stop`, not `disable` — it only affects the currently running
process; the units remain enabled and **will start again on the next reboot**
of the box unless you also `systemctl disable` them. That's deliberate: this
runbook is for freeing RAM/swap on an already-running box, not decommissioning
Qoder.

## Restarting (this is what you came here for)

```bash
# Bring one repo's daemon back:
systemctl start qoder-daemon-<repo>.service

# Bring everything back:
systemctl start 'qoder-daemon*' qoder-ttyd.service

# Confirm it came up and grab its fresh session URL:
systemctl is-active qoder-daemon-<repo>.service
journalctl -u qoder-daemon-<repo>.service --no-pager -n 20 | grep -E 'envId=|registered|deregistered'
```

Starting is unconditionally safe — worst case is it just re-registers a new
`envId` and starts idling again, exactly like it does after any restart or
reboot today. No repo state, git history, or worktree is affected by
stopping/starting these; they only hold a live process, not any data these
repos don't already have on disk under `/opt/data/aicoding/repos/`.

## If you need to permanently remove one

Only do this if explicitly asked — it's a step beyond what this runbook is
normally for:

```bash
systemctl disable --now qoder-daemon-<repo>.service
rm /etc/systemd/system/qoder-daemon-<repo>.service
systemctl daemon-reload
```

## Why this exists / when to reach for it

This box has been observed with **swap fully used (4 GB/4 GB)** while Hermes
itself was healthy (no OOM kills) — the dominant swap consumer was these
idle `aicoder`/Qoder daemons, not Hermes. If diagnosing memory/swap pressure
again, check `ps -u aicoder -o pid,etime,rss,cmd` and the units above before
assuming it's a Hermes regression. See `PRODUCTION.md` for how Hermes's own
services are laid out on this same box.
