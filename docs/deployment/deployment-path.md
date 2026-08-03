# The deployment path: making a box reproducible

**Applies to:** a long-lived deployment (the `hermes-systest` ECS box running
the gateway and its sidecars as systemd services), not laptop installs.

## The gap

A deployment is more than its checkout. Code was already reproducible — the
tree comes from `develop`, packages are exact-pinned, and the interpreter has
a declared floor watched weekly (`docs/deployment/runtime-drift.md`).
Everything that makes the box *that* deployment was not:

| Layer | Lived only on the box | Rebuild without this doc |
|---|---|---|
| `config.yaml` — MCP servers, approval gates, model/memory wiring | yes | agent comes back with no tools and no approval gating |
| `.env` — the credentials | yes (plus `/opt/data/backups/creds`) | nothing authenticates |
| 13 systemd units + 12 drop-ins — the unprivileged `hermes` model | yes | services run as root, or not at all |
| Interactive credentials — Google, WhatsApp, Canva/Railway/Vercel OAuth | yes | integrations silently dead |
| `deploy-hermes.sh` — the deploy tool itself | yes, untracked | no way to deploy |

Two distinct failures came out of that. A rebuilt box could not be brought
back without archaeology; and a change to any of it — by an operator, by a
deploy step, or by the agent itself acting on an instruction from a message —
left no trace anywhere a reviewer would look. The second is the one that
prompted this work: a runtime that drifts from what the repo says while every
dashboard reports healthy.

## What owns what

```
repo (public)                       scripts/deploy_state.py     the tooling
                                    deploy/hermes-deploy.sh     the deploy tool
private state store                 <deployment>/config.snapshot.yaml
  default: deploy/ in this repo     <deployment>/state.manifest.yaml
  systest: /opt/data/hermes-deploy-state
                                    <deployment>/systemd/*
box, root-only                      /opt/data/deploy/state-secrets.env
box, hermes-only                    $HERMES_HOME/.env, credential files
```

**The snapshot holds no credentials** — every secret value is replaced by a
`${PLACEHOLDER}` and the values live in one root-owned file on the box. It
still describes a box in detail (account names, service layout, which
identities are enrolled), and this repo is public, so the state store is
**not** committed here; `.gitignore` blocks it and `--state-root` points the
tool at a private location. The tooling and this document are generic and
belong in the repo.

## Why `check` never writes

Hermes rewrites `config.yaml` itself — `/model` from Telegram, `hermes tools`,
`hermes setup`, memory setup, roughly 30 call sites. A scheme where the repo
is authoritative and a deploy step overwrites the file would silently discard
a change made from a phone.

So the box stays authoritative and divergence becomes a *report*:

| Command | Direction | When |
|---|---|---|
| `capture` | box → state store | after any deliberate change; review the diff |
| `check` | compares, writes nothing | weekly timer, and after every deploy |
| `render` | state store → a config.yaml | rebuilding a box. Never scheduled |

## Everyday use

All commands run from the checkout on the box (`/opt/data/hermes-agent`) with
the deployment's interpreter. `PY` and `STATE` below are:

```bash
PY=/opt/data/hermes-agent/.venv/bin/python
STATE=/opt/data/hermes-deploy-state
```

### After changing anything on the box

```bash
sudo $PY scripts/deploy_state.py --state-root $STATE capture \
  --deployment hermes-systest \
  --hermes-home /opt/data/hermes-home-staging \
  --deploy-script /opt/data/deploy-hermes.sh \
  --secrets-out /opt/data/deploy/state-secrets.env \
  --credential-glob 'google-workspace/credentials/*.json' \
  --credential-glob 'whatsapp/session-*/creds.json' \
  --credential-glob 'mcp-tokens/*.json'
```

Then commit the diff in the state store. `git diff` there is the review: it
names the MCP server that appeared, the approval pattern that changed, the
tool that was enabled.

`capture` **refuses to write** if credential material survives sanitization
(`find_leaks`), so a heuristic miss fails the run instead of committing a
token.

### Checking for drift

```bash
$PY scripts/deploy_state.py --state-root $STATE check --deployment hermes-systest
```

Exit `0` clean, `1` drift, `2` unreadable. `--json` for machine-readable
findings, `--notify` to send Telegram **only** when drift is found — so
silence is meaningful, matching `check_runtime_drift.py`.

What counts as drift: a config key added, removed or changed; a `.env` key the
manifest requires that is now missing; a credential file gone or with looser
permissions; an installed unit whose bytes changed; the installed deploy
script differing from the reviewed copy in the repo.

What deliberately does **not**: rotating a secret in place (values are
placeholders), or adding a new `.env` key — that is a `note` saying "capture
this", not a failure. A check that cries wolf gets ignored.

### Deploying code

```bash
sudo deploy/hermes-deploy.sh [branch]     # default: develop
```

Fetches through the root-owned read-only deploy key
(`docs/deployment/deploy-credential.md`), aborts on unexpected local
modifications, backs up known ones, reinstalls the package, re-asserts
ownership (`hermes` owns the tree, root owns `.venv`), restarts every unit and
verifies each came back active.

The copy in the repo is the reviewed source of truth; the installed copy at
`/opt/data/deploy-hermes.sh` is compared against it by `check`. After editing
it, reinstall and re-capture:

```bash
sudo install -m 700 -o root -g root deploy/hermes-deploy.sh /opt/data/deploy-hermes.sh
```

## Rebuilding a box from nothing

Ordered, and the order matters — services fail to start if credentials are not
in place first.

1. **Provision** the instance and install the OS prerequisites
   (`git`, `uv`, `nodejs` for the WhatsApp bridges, `caddy` if the dashboard is
   exposed).
2. **Deploy key**: recreate the root-owned read-only key and the
   `github-hermes-deploy` SSH alias per `docs/deployment/deploy-credential.md`.
   Clone `develop` to `/opt/data/hermes-agent`.
3. **Service user**: `useradd --system --home /opt/data/hermes-user hermes`.
   Build the venv (`uv venv .venv --python 3.11 && uv sync --frozen`), then
   `chown -R hermes:hermes` the tree except `.venv`, which stays root-owned —
   root runs `pip` from it, so a `hermes`-writable venv is a path back to root.
4. **Restore the private state store** to `$STATE` and the secrets file to
   `/opt/data/deploy/state-secrets.env` (`0600`, root). Without the secrets
   file, `render` cannot produce a working config and will tell you exactly
   which placeholders it is missing.
5. **Render the config**:
   ```bash
   sudo $PY scripts/deploy_state.py --state-root $STATE render \
     --deployment hermes-systest \
     --secrets /opt/data/deploy/state-secrets.env \
     --out /opt/data/hermes-home-staging/config.yaml
   sudo chown hermes:hermes /opt/data/hermes-home-staging/config.yaml
   ```
6. **Restore `.env`** from `/opt/data/backups/creds` (or re-enter the keys the
   manifest's `env_keys` lists), `0600`, `hermes`-owned. `render` does not
   produce this file: `.env` is secrets only, and the manifest carries key
   *names* so a rebuild knows what to ask for.
7. **Restore credential files** listed in the manifest's `credential_files`,
   with the owner and mode recorded there. Google Workspace tokens come from
   `/opt/data/backups/creds`; if they are gone, re-run consent
   (`custom/calendar/calendar_auth.py`, see
   `optional-mcps/google-workspace/manifest.yaml`). WhatsApp sessions cannot be
   restored from nothing — a lost `session-*/creds.json` means re-pairing the
   phone.
8. **Install the units** from `$STATE/hermes-systest/systemd/` into
   `/etc/systemd/system/` (drop-in directories included), then
   `systemctl daemon-reload && systemctl enable --now 'hermes-*'`.
9. **Restore data**: `state.db` (sessions, memory) from
   `/opt/data/backups/`. Config makes the agent work; this is what makes it
   remember. It is not part of the state store — it is a database, and backups
   own it.
10. **Verify**:
    ```bash
    $PY scripts/deploy_state.py --state-root $STATE check --deployment hermes-systest
    $PY scripts/check_runtime_drift.py
    systemctl list-units 'hermes-*' --no-pager
    ```
    A clean `check` means this box matches the one that was working. Expect
    `NRestarts=0` on every unit.

## Weekly check

The interpreter check already runs weekly; the state check is a second
`ExecStart` on the same unit rather than a new timer, so one report covers all
four layers:

```ini
# /etc/systemd/system/hermes-drift-check.service
ExecStart=/opt/data/hermes-agent/.venv/bin/python \
    /opt/data/hermes-agent/scripts/check_runtime_drift.py --notify
ExecStart=/opt/data/hermes-agent/.venv/bin/python \
    /opt/data/hermes-agent/scripts/deploy_state.py \
    --state-root /opt/data/hermes-deploy-state check \
    --deployment hermes-systest --notify
```

`SuccessExitStatus=0 1` is already set — drift is reported by `--notify`, and a
non-zero exit is the signal, not a fault. Systemd runs sequential `ExecStart`
lines in order and stops at the first *failure*, which is why the tolerated
exit status matters here: without it, drift in the interpreter check would
prevent the state check from running at all.

The service runs as `hermes`, so the state store must be root-owned but
world-readable (`0755`); it contains no secrets. The secrets file is not read
by `check` at all and stays `0600` under a `0700` directory.

## Responding to a drift report

1. **Read the finding.** It names a dotted config path, an env key, a
   credential file or a unit.
2. **Decide which side is right.** A change you or the agent made on purpose:
   `capture` and commit. A change nobody intended: that is the interesting
   case — investigate before reverting, because "the agent enabled an MCP
   server" and "an approval pattern disappeared" are security events, not
   configuration noise.
3. **To revert**, render the snapshot to a scratch path and diff it against
   the live file before copying anything over. Never render straight onto
   `config.yaml` on a running box — you will lose any legitimate change made
   since the last capture.
4. **Restart** only what the change affects. `config.yaml` is read at
   process start, so a config revert needs `systemctl restart hermes-gateway`.

## Limits, stated plainly

- **This is not infrastructure-as-code.** It does not create the instance,
  install packages, or configure Caddy. It captures and restores the
  deployment's *state*, which is the part that was undocumented.
- **A capture is only as fresh as the last run.** Between captures the box can
  diverge; the weekly check is what bounds how long that goes unnoticed.
- **Sanitization is heuristic.** `find_leaks` is the backstop that turns a miss
  into a failed run rather than a committed secret, and
  `tests/scripts/test_deploy_state.py` pins both properties. Review a snapshot
  diff before committing it anyway.
- **Data is out of scope.** `state.db`, session history and WhatsApp message
  stores belong to backups.

## Related

- `docs/deployment/runtime-drift.md` — the interpreter/package layer and the
  weekly timer this check shares.
- `docs/deployment/deploy-credential.md` — the root-owned read-only deploy key.
- `docs/deployment/mcp-approval-gating.md` — what the approval patterns in the
  snapshot mean.
- `docs/deployment/os-patching.md` — the needrestart exemption, so patching
  does not kill an in-flight turn.
