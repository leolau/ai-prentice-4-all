# UAT suite — the live `hermes-systest` deployment

**For the agent running the test, not for the person reading the result.** Every
case below is executable on the live box and has a written pass criterion, so
two different agents running this suite on the same revision should produce the
same verdict.

This suite exists because of one repeated finding: **defects in this system
survive the repository and die on the box.** A wrong status word passed `ruff`,
`ty`, 53 unit tests and a Postgres E2E suite (#265). A read-back asked "is there
a value" instead of "is it *my* value" (#262). A scheduled pass met a
`TIMESTAMPTZ` with a naive clock, and read a table that had never been created,
because nothing had ever run it (#269). A `--help` sentence denied what the
command did (#285). None of those are reachable from a test client. So the box
is not a smoke test after the real testing — for this class, **it is the real
testing**.

- **Access, layout, and the traps that produce false results:**
  [`.agents/skills/testing-hermes-systest-box/SKILL.md`](../../.agents/skills/testing-hermes-systest-box/SKILL.md).
  Read it first; this document does not repeat it.
- **Deployment procedure and inventory:** [`docs/deployment/README.md`](../deployment/README.md).

---

## 0. Before you start

### 0.1 Preconditions

| # | Check | Command | Required |
|---|---|---|---|
| P1 | You can reach the box | `run_on_box.sh` / OOS `RunCommand` returns output | yes |
| P2 | The revision under test is deployed | `git -c safe.directory=/opt/data/hermes-agent -C /opt/data/hermes-agent log --oneline -1` | yes — record the sha |
| P3 | The working tree is the deploy's, not yours | `git -C /opt/data/hermes-agent status --porcelain` is empty | yes |
| P4 | You know what you are accepting | the PR list in the release, or the FG doc | yes |

If P2 shows a revision older than the one you were asked to accept, **deploy
first** (`nohup /opt/data/deploy-hermes.sh develop > /tmp/deploy.log 2>&1 &`,
then poll the log — the OOS call returns long before the deploy finishes) and
record the deploy's own last two lines, which are its verdict:

```text
deploy OK (<sha>)  backup: /opt/data/backups/deploy-<timestamp>
handover doc current (docs/deployment/README.md)
```

### 0.2 Rules of engagement — read these, they have all been violated

1. **Every destructive step acts on a principal or profile this run created.**
   Never on `leo_owner`, never on `default` or `maintenance`, never on an
   existing member. Prefix everything you create with `uat` plus the date:
   `uat20260817a`, `uat20260817@example.com`.
2. **Teardown is part of the case, not a courtesy.** A case that leaves a seeded
   file behind makes the *next* run's assertions ambiguous — this has already
   happened twice. Every case below ends with a teardown block; run it even when
   the case fails, and re-run §A6 at the end to prove the box is where it started.
3. **A member's box-wide account survives profile deletion by design.** Do not
   "fix" it, do not delete GoTrue accounts, and do not report it as a defect. It
   is the FG-26 contract: accounts are shared across profiles.
4. **Never print a secret.** Grep for key *names*, print `${#VAR}`, or redact:
   `sed -E 's/[A-Za-z0-9_-]{30,}/***REDACTED***/g'`.
5. **A negative check must not fail the invocation.** OOS fails the whole call on
   a nonzero exit; end scripts with `; true`.
6. **`HERMES_HOME` is not optional.** Without it the CLI answers, consistently
   and wrongly, about `/opt/data/hermes-user/.hermes`. Every command in this
   suite is written as:

   ```bash
   cd /opt/data/hermes-agent
   sudo -u hermes -H env HERMES_HOME=/opt/data/hermes-home-staging ./.venv/bin/hermes <cmd>
   ```

   Below, that prefix is abbreviated **`H`**, and **`$HH`** is
   `/opt/data/hermes-home-staging`. `--profile <name>` goes **before** the
   subcommand; trailing, it silently selects instead of targeting (#207).
7. **Report what you could not run, with the reason.** A skipped case that reads
   as a pass is the defect this project keeps finding in itself (#279). Use
   `SKIP (reason)` in the results table — never leave a row blank.

### 0.3 What a run produces

Fill in §7 and hand back: the revision, the per-case verdict, the evidence for
every FAIL (command + full output), and the §A6 end-state proof. Nothing else is
required — do not paraphrase passes.

---

## A. Deployment truth — is the box the thing we think it is?

These come first: every later case's verdict is meaningless if the box is
running something other than the revision under test.

### A1 — the deployed revision is the one under test

```bash
git -c safe.directory=/opt/data/hermes-agent -C /opt/data/hermes-agent log --oneline -1
git -c safe.directory=/opt/data/hermes-agent -C /opt/data/hermes-agent status --porcelain | head
```

**Pass:** the sha matches the revision you were asked to accept, and the tree is
clean. **Fail:** any modification — the box is then not testing the repository,
which is the drift this whole programme exists to prevent.

**Untracked files are a finding, not noise.** A dry run of this suite on
`dfa32fe3c` found `?? docs/design/projects-feature-design.md` (102 KB, owned by
`hermes`) — something wrote a document into the deployment's own checkout. It
changes no behaviour, so nothing else in this suite would ever see it, which is
precisely why A1 reads `--porcelain` and not just the sha. Report the path, its
owner and its mtime; do not delete it.

### A2 — every enabled unit is active

```bash
for u in $(systemctl list-unit-files 'hermes-*.service' 'agent-home*.service' \
           --state=enabled --no-legend | awk '{print $1}'); do
  printf '%-42s %s\n' "$u" "$(systemctl is-active "$u")"
done; true
```

**Pass:** every unit `active`. At `dfa32fe3c` that is **15**: 14 `hermes-*`
(`calendar-poller`, `calendar-triage`, `dashboard`, `digest`, `email-batcher`,
`email-poller`, `email-triage`, `embed`, `escalation`, `gateway`, `wa-batcher`,
`wa-bridge-connectar`, `wa-bridge-personal`, `wa-triage`) plus
`agent-home.service`. Count from the box, not from this list — a unit missing
from the enabled set is invisible to a loop over the enabled set, so compare the
number too.

**Note:** during a deploy `agent-home.service` is legitimately inactive while the
bundle rebuilds — re-check after the deploy log's final line, not during.

### A3 — nothing runs as root

Check the *process*, not the unit file — a drop-in can change the account
without the unit moving.

```bash
for u in $(systemctl list-unit-files 'hermes-*.service' 'agent-home*.service' \
           --state=enabled --no-legend | awk '{print $1}'); do
  pid=$(systemctl show -p MainPID --value "$u")
  [ "$pid" != 0 ] && printf '%-42s %s\n' "$u" "$(ps -o user= -p "$pid")"
done; true
```

**Pass:** every owner is `hermes`. **Fail:** any `root`. This is the
`hermes-calendar-triage` incident's exact shape (#203).

### A4 — no deployment-state drift, including things nobody captured

```bash
cd /opt/data/hermes-agent
./.venv/bin/python scripts/deploy_state.py \
  --state-root /opt/data/hermes-deploy-state check --deployment hermes-systest
```

**Pass:** `No deployment state drift on hermes-systest`. The check enumerates
*installed* units and drop-ins, not only the manifest's (#272), so an
unrecorded unit or a `User=`-overriding drop-in is reported here.

### A5 — the handover document is current

The deploy prints this itself as its last line. **Pass:** `handover doc current`.
A printed behind-HEAD note is a real finding: since #274 the line is silent when
nothing documented has changed, so its presence means something documented moved
without the document.

### A6 — end-state proof (run again at the end of the whole suite)

```bash
H profile list
H member list
find /opt/data/hermes-home-staging/memories /opt/data/hermes-home-staging/persons \
     -maxdepth 3 | sed 's|/opt/data/hermes-home-staging/||' | sort
ls -d /opt/data/hermes-home-staging/profiles/*
find /opt/data/hermes-home-staging ! -user hermes -printf '%u %p\n' | head; true
```

Capture this **before** §B and again after §H. **Pass:** identical, except for
members whose box-wide accounts intentionally remain (rule 3). Any `uat*`
profile, file or directory still present is a teardown failure — clean it and
say so in the report.

---

## B. FG-24 — curated memory belongs to a person

Only the box has two profiles and real principals, which is exactly what the
FG-24 defects hid behind.

### B1 — a member cannot erase memory about other people

```bash
H member local-principal            # record what it says; restore it in teardown
H member local-principal --set <a-non-owner uat member's uid>
H memory reset --all-principals --yes; true
```

**Pass:** refused with `--all-principals erases memory about other people; only
an owner or admin can do that.` and **no file is deleted** — verify with §A6's
`find` before and after.

### B2 — an ordinary reset takes the caller's memory and only the caller's

Setup (as `hermes`), for a `uat` uid that is *not* an existing member:

```bash
mkdir -p $HH/memories/users/<uat-uid> $HH/persons/<uat-uid>
echo '# uat participation' > $HH/memories/users/<uat-uid>/MEMORY.md
echo '# uat identity'      > $HH/persons/<uat-uid>/USER.md
md5sum $HH/memories/MEMORY.md $HH/memories/USER.md        # the shared + legacy files
H member local-principal --set <uat-uid>
H memory reset --yes
md5sum $HH/memories/MEMORY.md $HH/memories/USER.md
```

**Pass — all five:**
1. Output lists, under `This will permanently erase`, exactly the caller's
   `MEMORY.md` (participation) and `USER.md` (person).
2. Output lists, under `Left in place:`, the profile-shared block, the legacy
   `memories/USER.md` if present, and every *other* principal's participation.
3. The closing line is `Erased what was listed. New sessions still see the
   memory left in place above.` — **not** "blank slate", which is only correct
   when nothing survived.
4. The two md5s are unchanged.
5. `memories/users/<uat-uid>/` and `persons/<uat-uid>/` are **gone as
   directories**, not merely emptied (#285) — an empty participation directory is
   a person the profile still appears to know.

**Teardown:** `H member local-principal --set <original>` (or `--clear`).

### B3 — purge erases the departing person's memory files

```bash
H member add uat<date>b@example.com --display "UAT B3"     # email is positional
H member delete <new-uid> --strategy purge
```

Seed `memories/users/<new-uid>/MEMORY.md` and `persons/<new-uid>/USER.md` first.

**Pass:** the CLI prints `Erased curated memory: …` for each path it removed, the
files are gone, and it says the box-wide account still exists. **Not a failure:**
the account remaining (rule 3).

### B4 — an identity file survives while the person still participates elsewhere

Seed the same uid in **both** profiles:
`$HH/memories/users/<uid>/MEMORY.md` and
`$HH/profiles/maintenance/memories/users/<uid>/MEMORY.md`, plus
`$HH/persons/<uid>/USER.md`. Then purge from `default` only.

**Pass:** the default-profile participation file is gone; the maintenance one and
`persons/<uid>/USER.md` **remain**. This looks like a leak and is the contract:
identity is cross-profile, so it goes only with the last participation.

### B5 — the last participation takes the identity with it

Purge that same uid from `maintenance`
(`H --profile maintenance member delete <uid> --strategy purge`).

**Pass:** both the maintenance participation file and `persons/<uid>/USER.md` are
gone.

### B6 — transfer moves rows but never memory

`H member delete <uid> --strategy transfer --transfer-to <uid2>`.

**Pass:** the summary reports rows transferred, prints *"Their curated memory
files were left in place — transfer moves what they owned, and memory about a
person is not inheritable"*, and the memory **rows** are deleted rather than
reassigned (check the `memories` table for `owner`/`user_id` = the departing
uid: zero rows, and none under `<uid2>` that were theirs).

### B7 — the help does not deny what the command does

```bash
H member delete --help
```

**Pass:** the description states that memory rows are deleted under both
strategies and that purge erases the curated files. **Fail:** any wording
claiming nothing cascades to memories — that exact sentence shipped for days
while purge was deleting those files (#285). Read the *help*, not the code.

---

## C. FG-26 / FG-28 — members, roles, and the console's scope

### C1 — the roster is real and paginated from one constant

`H member list` — **pass:** it returns the enrolled principals with role, email
and activation state, and the first page is 50 rows (#218).

### C2 — a suspended member loses every binding

Deactivate a `uat` member (`H member deactivate <uid>`) and confirm channels,
surfaces and memory bindings stop resolving for them (#214), then reactivate.

### C3 — the console checks authority where it acts

For a `uat` member enrolled in `default` only, confirm an administrative action
scoped to `maintenance` is refused (#233). Drive it **over HTTP** against
`127.0.0.1:9119`, not in-process — three of #253's nine defects were invisible to
a test client.

### C4 — account verbs are absent, not guarded

**Pass:** there is no reachable command or endpoint that creates or deletes a
box-wide account from a profile console. Prevention here is by absence (FG-28
§account-verb), so the evidence is a search that finds nothing plus a 404/parser
error, not a permission denial.

---

## D. FG-30 — profile lifecycle

### D1 — a suggestion appears without anyone typing

The monthly pass is driven by `hermes-review-pass.timer`.

```bash
systemctl list-timers 'hermes-review-pass*' --all
systemctl show -p ExecMainStartTimestamp,Result hermes-review-pass.service
```

**Pass:** the timer is armed and its last run's `Result=success`. Do **not**
force generation by hand and then call it scheduled — the whole point of #268 is
that nothing had ever run it unprompted.

### D2 — adoption seeds what it promises

`H profile adopt <suggestion-id>` on a `uat` suggestion.

**Pass:** the new profile gets its sub-goal, the published entity goal, and an
`.env` that does **not** carry the parent's credentials.

### D3 — retirement closes the goals it claims to close

`H profile retire <uat-profile> -y`.

**Pass:** the output states `Goals closed: N` (or `⚠ Goals NOT closed (<cause>)`)
and the profile's goal is actually `done` in the registry. A bare archive path
with exit 0 is the #265 defect: it reported success over goals it never closed.
The parent's published copy correctly stays `active`.

### D4 — `commit-channel` refuses a token already in use

Only runnable with a **real** platform token you are authorised to use. With
one: committing the parent's token to a second profile must be refused by name,
the read-back must compare the written value against the token you supplied (not
merely "a value exists" — #262), and a stale token must be replaced. Without a
token: **SKIP (no bot token provisioned)** — do not simulate it.

---

## E. FG-31 — capacity headroom

### E1 — the verdict names its bound

```bash
H status
H doctor
```

**Pass:** one verdict — `comfortable` / `watch` / `constrained` — and it names
the bound that produced it. `constrained` is report-only; it must not refuse
work.

### E2 — the latency figure is about turns a human waited for

**Pass:** cron- and machine-origin turns are excluded from p50/p95, and a sample
count too small to bind a verdict does not bind one. The pre-#264 reading was
`p95 1195.8s` from three cron samples while interactive replies took 5.9–32.5 s.
Cross-check against a handful of recent interactive turns in the transcript.

---

## F. FG-29 — digest and promotion

### F1 — a section that could not run says so

**Pass:** an unavailable section renders `Capacity: unavailable — <trimmed
reason>` rather than being omitted (#279). To exercise it, break one section's
input in a *throwaway* profile only.

### F2 — one digest per ISO week, not one per run

Run the review pass twice in the same week.

**Pass:** the second run returns the *same* notification id rather than stacking
a second digest.

### F3 — the promotion store exists before anything reads it

**Pass:** no `relation "skill_promotions" does not exist`. That error is what
proved the loop had never run on this deployment (#269).

---

## G. `agent-home` — the primary user surface

Per `AGENTS.md`, `agent-home` is *the* user-facing UI; the dashboard is the
operator console. Test the phone app.

### G1 — it is serving the revision under test

`next start` serves a **compiled** bundle, so a `git pull` alone changes nothing:

```bash
stat -c '%y %n' /opt/data/hermes-agent/agent-home/.next/BUILD_ID
```

**Pass:** `BUILD_ID`'s mtime is at or after the deploy under test.

### G2 — health without credentials

```bash
curl -s -o /dev/null -w '/       -> %{http_code} redirect=%{redirect_url}\n' 127.0.0.1:3100/
curl -s -o /dev/null -w '/login  -> %{http_code}\n' 127.0.0.1:3100/login
```

**Pass:** both **200**, and unauthenticated `/` contains a client-side
redirect to `/login` (`curl -s 127.0.0.1:3100/ | grep -o /login`). It is **not**
a 307 — the gate is in the React tree, so a status-code assertion alone proves
nothing about whether the app is guarded. Verified on `dfa32fe3c`.

### G3 — an authenticated page renders real data

Mint a session as described in the box skill (§"Verifying an authenticated phone
page without the password") and load the memory and capacity screens.

**Pass:** the screens render the same numbers the CLI reports. **Trap:**
server-rendered HTML cannot prove the memory map — `MemoryMap` fetches
client-side, so `curl` sees rows but zero `<circle>`. Geometry claims need a
browser; otherwise **SKIP (no browser)**.

### G4 — the memory screens are per-principal

**Pass:** a member sees their own memory and the profile-shared block, and not
another person's participation memory.

---

## H. Profiles — creation, cloning, deletion

### H1 — `--clone-all` leaves people behind

```bash
mkdir -p $HH/memories/users/uatclone && echo x > $HH/memories/users/uatclone/MEMORY.md
H profile create uat<date>c --clone-all
find $HH/profiles/uat<date>c/memories $HH/profiles/uat<date>c/persons -maxdepth 3; true
```

**Pass:** the clone has `memories/MEMORY.md` (shared) but **no** `memories/users/`
and no `persons/`; session history, backups and snapshots did not travel; and the
CLI says so: *"Full copy from … excluding session history, backups, snapshots,
and each person's own memory — who this profile serves is decided by
enrolment."*

### H2 — a copy it cannot finish never starts

```bash
touch $HH/uat-unreadable.bak && chown root:root $HH/uat-unreadable.bak && chmod 600 $HH/uat-unreadable.bak
H profile create uat<date>d --clone-all; true
ls -d $HH/profiles/*; rm -f $HH/uat-unreadable.bak
```

**Pass:** one error line naming the unreadable path, **and no
`profiles/uat<date>d` directory** (#286 — the half-made profile was the worse
half of the bug: it made every retry fail with "profile already exists").

### H3 — deletion is clean

`H profile delete uat<date>c -y` — **pass:** the directory is gone and
`H profile list` is back to `default` + `maintenance`.

---

## 6. Out of scope — state these as SKIP, never as pass

| Area | Why it cannot be tested here | What would unblock it |
|---|---|---|
| FG-28 secret isolation across profiles | `default` and `maintenance` share one Supabase credential, so an unmigrated `os.getenv` reading the wrong store **cannot fail the test** — and a test that cannot fail cannot pass | a second, distinct Supabase credential |
| `commit-channel` with a live bot (D4) | needs a real platform token you are authorised to use | a provisioned test bot token |
| Delivery to a real phone / push | needs a human holding the device | a human tester |
| Whether the behaviour is *wanted* | acceptance is a judgement, not an assertion — this suite proves the system does what we said it does | Leo's review of the run |
| Box-wide session cap enforcement | 3 profiles × 15 means the box can be *asked* for 45; FG-31 reports the total and enforces nothing, by decision | a decision to make the cap box-wide |

---

## 7. Results template

Revision under test: `________`  ·  Date: `________`  ·  Run by: `________`
Deploy line: `deploy OK (____)` · handover: `current / behind (____)`

| Case | Verdict | Evidence / reason |
|---|---|---|
| A1 deployed revision | PASS / FAIL | |
| A2 units active | PASS / FAIL | |
| A3 no root process | PASS / FAIL | |
| A4 no state drift | PASS / FAIL | |
| A5 handover current | PASS / FAIL | |
| A6 end state restored | PASS / FAIL | |
| B1 member cannot erase others | PASS / FAIL / SKIP | |
| B2 reset is the caller's | PASS / FAIL / SKIP | |
| B3 purge erases files | PASS / FAIL / SKIP | |
| B4 identity survives elsewhere | PASS / FAIL / SKIP | |
| B5 last participation takes identity | PASS / FAIL / SKIP | |
| B6 transfer never moves memory | PASS / FAIL / SKIP | |
| B7 delete help is truthful | PASS / FAIL | |
| C1 roster | PASS / FAIL / SKIP | |
| C2 suspension drops bindings | PASS / FAIL / SKIP | |
| C3 authority checked where it acts | PASS / FAIL / SKIP | |
| C4 account verbs absent | PASS / FAIL / SKIP | |
| D1 suggestion pass is scheduled | PASS / FAIL / SKIP | |
| D2 adoption seeds correctly | PASS / FAIL / SKIP | |
| D3 retirement closes goals | PASS / FAIL / SKIP | |
| D4 commit-channel refusal | PASS / FAIL / SKIP | |
| E1 capacity names its bound | PASS / FAIL | |
| E2 latency excludes machines | PASS / FAIL | |
| F1 digest names what failed | PASS / FAIL / SKIP | |
| F2 one digest per week | PASS / FAIL / SKIP | |
| F3 promotion store exists | PASS / FAIL | |
| G1 agent-home bundle is current | PASS / FAIL | |
| G2 agent-home health | PASS / FAIL | |
| G3 authenticated page | PASS / FAIL / SKIP | |
| G4 per-principal memory | PASS / FAIL / SKIP | |
| H1 clone-all exclusions | PASS / FAIL | |
| H2 clone refuses cleanly | PASS / FAIL | |
| H3 deletion is clean | PASS / FAIL | |

**Report format:** the revision, this table, and for every FAIL the command and
its full output. For every SKIP, the reason from §6 or a new one. Do not
summarise passes.

### If a case fails

1. Re-run it once — some failures are a deploy still in flight (A2's
   `agent-home` in particular).
2. Capture the command, full output, and the relevant log file (`journalctl` is
   nearly empty here; units write to `$HH/logs/*.log` — confirm with
   `systemctl show -p StandardOutput <unit>`).
3. Do **not** fix it on the box. A fix applied live is drift; it goes through the
   repository, a PR, and a deploy — that is the property this deployment is
   supposed to have.
4. Finish the rest of the suite. One failure is not a reason to stop testing.
