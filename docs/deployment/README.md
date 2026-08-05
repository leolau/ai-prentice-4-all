# The `hermes-systest` deployment — handover

Written for an agent or engineer picking this up cold, with no session history.
It states what exists, what is verified, what is *not*, and where the detail
lives. The per-topic documents are authoritative for procedure; this file is
authoritative for **what is currently true of the live box**.

Last verified: 2026-08-05, application at `f4bc8af21`.

That line is **checked**, not a promise: `deploy_state.py handover` compares it
against the revision the box is actually running, the deploy prints the result,
and the weekly timer reports a stale document. It went three deploys stale before
that existed. Re-verify the claims below before moving it forward — a fresh date
on stale prose is worse than an old date.

## Read these in this order

| Document | Answers |
|---|---|
| this file | what exists, what is verified, what is missing |
| `deployment-path.md` | capture/render/check, the offsite backup, cold rebuild |
| `deploy-credential.md` | the three deploy keys and why only one may write |
| `runtime-drift.md` | interpreter/package pinning and the weekly timer |
| `os-patching.md` | unattended upgrades and the `needrestart` exemption |
| `mcp-approval-gating.md` | which MCP tools require human approval |
| `../../.agents/skills/testing-hermes-systest-box/SKILL.md` | how to reach the box at all (no SSH) and ~30 traps |

## The box

```
instance      hermes-systest  (i-j6c81aisv2dd8mg17yle, cn-hongkong, ecs.e-c1m4.xlarge)
service user  hermes          (unprivileged; nothing Hermes runs is root)
interpreter   /opt/data/hermes-agent/.venv/bin/python  — Python 3.11.15
age           1.1.1  (/usr/bin/age)
```

Paths, and which of them are reproducible:

```
/opt/data/hermes-agent            application checkout      git, `develop`
  agent-home/agent-home.env       the phone app's secrets   0600, git-ignored, backups only
/opt/data/hermes-home-staging     HERMES_HOME               config in state repo, secrets in backups
/opt/data/hermes-user             hermes user home
/opt/data/deploy-hermes.sh        the deploy tool           deploy/hermes-deploy.sh in git
/opt/data/deploy/                 root-only, 0700
  state-secrets.env               deployment secret values  offsite backup only
  backup-recipient.env            the age public recipient   not secret; recreate from the key
/opt/data/hermes-deploy-state     sanitized state, read-only clone
/opt/data/hermes-deploy-backups   encrypted bundles, write clone
/opt/data/backups                 old manual snapshots — NOT a backup system, see below
```

Three git remotes, three separate deploy keys, exactly one of which may write:

```
leolau/ai-prentice-4-all                  public   source        read-only key
leolau/ai-prentice-4-all-deploy-state     private  what the box should look like   read-only key
leolau/ai-prentice-4-all-deploy-backups   private  age ciphertext                  WRITE key
```

The state key is read-only deliberately: a box that could push would be able to
rewrite the record used to detect its own drift. The backup key must write, which
is why the bundles are in a different repository from the state — a deploy key
cannot be scoped to a subdirectory.

## What runs

12 long-running services, all as `hermes`:

```
hermes-gateway        hermes-dashboard      hermes-digest        hermes-escalation
hermes-wa-bridge-personal    hermes-wa-bridge-connectar   hermes-wa-batcher    hermes-wa-triage
hermes-email-poller   hermes-email-batcher  hermes-email-triage  hermes-embed
agent-home            (the phone PWA — note the name, see below)
```

`hermes-embed` is the loopback embedding service the layer-4 memory tier calls;
see `local-embeddings.md`.

**`agent-home` is the one service not named `hermes-*`**, which is exactly how it
spent ten days invisible: until FG-23 phase A0 (2026-08-05) it ran as **root**
from a *second clone* (`/opt/data/agent-home-app`, frozen at PR #62, serving a
build from 2026-07-27) that the deploy never entered and the `hermes-*` capture
glob could not see. Now:

```
unit         agent-home.service   User=hermes, ProtectSystem=strict, ProtectHome=yes
WorkingDir   /opt/data/hermes-agent/agent-home     the main checkout
public       https://home.leolau.ai-and-i.io → Caddy → 127.0.0.1:3100
build        deploy rebuilds it when agent-home/ or package-lock.json moves
capture      DEFAULT_UNIT_GLOBS = ("hermes-*", "agent-home*")
```

It serves a *compiled* Next.js bundle, so a source change that is not rebuilt is
invisible however green the deploy looks; `agent-home/.next/BUILD_ID` mtime is
the tell. It is an npm **workspace** of the root `package.json` — install and
build from the repo root (`npm ci && npm run build --workspace agent-home`), never
from inside `agent-home/`, which would create a second unhoisted dep tree.

Three timers:

```
hermes-drift-check.timer         Mondays 09:00   three checks, reports only on drift
hermes-secret-backup.timer       daily 04:30     encrypted credential backup, pushes
hermes-memory-projection.timer   daily 03:00     refits the memory explorer's 2-D map
```

The projection fit is a whole-corpus SVD, so it can never run in a page
request; without the timer the map would keep showing the corpus as it was the
day someone last ran the command by hand, and every memory written since would
be counted as "N new" and drawn nowhere.

The weekly unit runs four `ExecStart` lines in order, added as drop-ins so the
captured base unit stays byte-identical:

1. `check_runtime_drift.py` — interpreter and package baseline
2. `deploy_state.py check` — config, `.env` key names, credential modes, units
3. `backup_secrets.py verify` — backup freshness, coverage, and that it is
   actually *offsite*
4. `deploy_state.py handover --notify` — whether this document still describes
   the deployed revision

Confirm the drop-in set with `systemctl cat hermes-drift-check.service`; the
fourth line was added on 2026-08-05 and, like the others, is captured state.

`SuccessExitStatus=0 1` matters: drift is signalled by exit 1, and without it
drift in the first check would stop the other two from running. Silence means
clean — a report only arrives on Telegram when something is wrong.

## The three layers, and the one question each answers

```
runtime drift    "is the interpreter/packages what we pinned?"
state check      "does this box still match the reviewed record of itself?"
backup verify    "if this box vanished, could we rebuild it?"
```

The state check is the answer to the original problem: a hand-edit to
`config.yaml` — by an operator, a deploy step, or the agent acting on an
instruction in a message — now shows up as a weekly finding instead of being
invisible. It never overwrites, because Hermes rewrites its own `config.yaml`
from ~30 call sites and a render-on-deploy scheme would silently discard a change
made from Telegram.

## Reproducing the box from nothing

Full steps are in `deployment-path.md` ("Cold rebuild"). The shape:

1. Clone the source over its deploy key, `develop`.
2. Clone the state repo over *its* deploy key (`git config --system --add
   safe.directory` on it, or root-owned clone lookups fail for `hermes`).
3. `deploy_state.py render` the config from the snapshot plus the secrets file.
4. Restore `.env` and every interactive credential from the newest bundle in the
   backup repo — needs the private age key, which is not on the box.
5. Install the captured systemd units and drop-ins, `daemon-reload`, enable.
6. Run all three checks. A clean result means this box matches the working one.

Steps 3 and 4 are the only ones that need something not in a repository: the
root-owned `state-secrets.env` (itself in the backup) and the private age key.

## What is verified, and what is not

Verified on the live box, not in a fixture:

- `render` reproduces the live `config.yaml` byte-for-byte.
- The weekly unit completes as `hermes` with `Result=success`.
- The state key **cannot** push (`git push --dry-run` → `marked as read only`).
- The installed deploy script is byte-identical to the reviewed copy in git.
- The first bundle pushed: 17 files, 8.7 KB ciphertext, `age-encryption.org/v1`.
- An independent clone of the backup repo contains only the bundle and the index,
  and greps clean for `ghp_`, `GOCSPX`, `ya29`, `1//`, `AKIA`, `sk-`, the Telegram
  token, `postgresql://` and `AGE-SECRET`.
- `verify` reports drift — not success — when a bundle exists locally but the
  push failed. This was a real bug (#91); a local file next to the secrets it
  protects is not a backup.
- The memory explorer answers as the owner over a real dashboard session:
  `/summary`, `/rows`, `/projection` and `/documents` all 200, scoped to
  `leo_owner`, with the fitted PCA map returning its points.
- The phone app's memory page answers through its BFF as the owner: `/memory`
  200 (37 memories, 20 never recalled), `/api/memory/{rows,projection,query}`
  200, query placement returning 5 neighbours; every route 401 without a cookie
  and 401 with a one-byte-tampered one. Hermes tokens stay server-side.
- The deploy prints `deploy OK (<sha>)` and reports all 12 long-running units.
  Until 2026-08-05 it exited **3 on every successful deploy** and reported 2 of
  them: `hermes-*` matched the timer-invoked oneshots, and `is-active` on a
  finished oneshot exits 3 under `set -e` (#113). A deploy also no longer *runs*
  those oneshots — check their `ExecMainStartTimestamp` across a deploy.
- The map **renders** in a real browser (confirmed by Leo on the live phone URL,
  2026-08-05). This needed a human: `MemoryMap` fetches client-side, so server
  HTML contains no `<circle>` and `curl` cannot see a single dot — and the bug
  fixed in #112 was precisely a rendering-geometry one, drawing every point
  outside the SVG viewport. "The JSON is right" was not proof.

**The dashboard login subject must be aliased to a principal.** The basic-auth
provider mints sessions whose subject is the configured *username* (`admin`),
which is not a principal — so every principal-scoped page answered
`409 Authenticated, but no principal is enrolled for this user.` until:

```bash
hermes owner alias admin      # links the login subject to the owner principal
```

Identity lives in `app_prod` (`principals`, `principal_aliases`) regardless of
the datastore mode, because channels and the web surface share one identity
space; memories live in the *configured* mode's schema (`app_dev` here). Both
facts are easy to trip over and neither is obvious from an error message.

**Not verified: nobody has decrypted a bundle.** The box holds only the public
recipient by design, so a restore can only be proved by the key holder. Until
someone runs the `restore` in `deployment-path.md` against a real bundle and
compares, "we have backups" is a belief. Do this periodically, not once.

## Known gaps, stated plainly

- **Data is not backed up.** The bundle holds credentials only. `state.db`,
  the layer-4 memory rows, the interaction ledger, session history and WhatsApp
  message archives are excluded — large, constantly
  changing, and not what makes a rebuild impossible. Full-disk ECS snapshots are
  the right tool and are **not** set up.
- **`/opt/data/backups` is not a backup system.** Manual snapshots, on the same
  volume as the live data, with no timer. As of 2026-08-03 it contained no
  credentials at all despite documentation once claiming otherwise. Do not rely
  on it; it is kept only as history.
- **A lost private age key is unrecoverable.** That is the deliberate trade for a
  box that reads untrusted WhatsApp and email being unable to decrypt its own
  backup history.
- **The weekly `verify` runs unprivileged**, so it reports the root-only secrets
  file as *unverified* rather than covered. The daily backup runs as root and
  does cover it — check the index if you need proof.
- **Alibaba OSS is unavailable** on this account (`ListBuckets` → `UserDisable`),
  which is why the offsite destination is a private GitHub repo.
- **Only `hermes-systest` exists.** Everything is parameterized by
  `--deployment`, but no second deployment has ever been captured.
- **The box cannot publish its own state.** `capture` commits locally and the
  push fails ("marked as read only") — deliberate, so a compromised box cannot
  rewrite the record used to detect its drift. It is also silent: a state commit
  once sat unpushed for a whole deploy cycle. Finish it off-box
  (`format-patch origin/main..main` → apply in a session clone → push) and
  confirm `git log origin/main..main` on the box is empty.

## If you change anything on the box

```bash
PY=/opt/data/hermes-agent/.venv/bin/python
STATE=/opt/data/hermes-deploy-state
sudo $PY scripts/deploy_state.py --state-root $STATE capture --deployment hermes-systest ...
```

Then commit the diff in the state repo **from a session, not from the box** — the
box's key is read-only. The `git diff` there is the review: it names the MCP
server that appeared, the approval pattern that changed, the tool that was
enabled. Rotating a secret needs no capture; the daily backup notices the changed
content digest and writes a new bundle by itself.

Two rules worth restating because they are easy to break by accident: never
render straight onto a running box's `config.yaml`, and never give the box write
access to the state repo.
