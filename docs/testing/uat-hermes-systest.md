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
8. **A case whose subject was empty did not pass — write `PASS (vacuous)`.**
   If the departing member owned no rows, if no memory exists to be withheld, if
   the verdict came out unbound, then the assertion could not have failed and the
   row is evidence of nothing. This is rule 7's twin and the harder one to obey:
   the command exited 0 and the wording was right, so the row *looks* earned.
   Say which half you observed and which half had no subject; the 2026-08-17 run
   recorded three such rows as plain PASS (B6, E1, G4).
9. **Count the subject before you assert its absence.** "Nothing was returned"
   and "nothing exists to return" are different verdicts with identical output.
   Query the table first, put the count in the evidence, and only then read the
   filter's answer — G4's PASS in the 2026-08-17 run rested on "zero memory rows
   exist box-wide" while `app_prod.memories` held 139.

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
`dfa32fe3c` found `?? docs/design/projects-feature-design.md` — byte-identical to
its last committed version, still on the box days after #283 renamed it away.
The deploy could not delete: `git checkout -f <ref> -- .` writes what the ref
has and removes nothing it lacks, so **every** upstream delete or rename had been
surviving on the box. A stale document is the harmless case; a deleted module
that is still importable is the same hole. Fixed since — the deploy now removes
what the two revisions deleted, and lists whatever is left untracked.

So A1 has a second half: the deploy output must not name an untracked file it
cannot account for. Report the path, its owner and its mtime; do not delete it.

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

**Setup, or the case is vacuous:** the departing uid must own at least one
layer-4 `memories` row before the delete, and the count goes in the evidence. A
member enrolled minutes ago owns none, so `0 transferred, 0 deleted` is what the
command prints whether the rule holds or not (that is exactly what the
2026-08-17 run recorded as PASS). Write one as that principal through the store's
own write path — not an `INSERT` — then re-count.

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

**A suggestion has to exist, and four gates decide whether one can.** In
`generate_suggestion`: the one-open cap (a `proposed` row blocks generation), the
monthly clock (`_generation_due` — the interval since this profile last
proposed), the evidence bar (`_evidence_strong_enough`: **two or more skills with
recorded uses**, plus orphan operational goals or more than one participant), and
the dedup key of any `dismissed` row. Read all four before reporting *why* there
is no suggestion — "none pending" is a symptom of one of them, and which one
matters. On 2026-08-18 the box had **0 skills with recorded uses** and its last
suggestion adopted on 2026-08-16, so two independent gates were shut; no amount
of re-running the command would produce one, and the case is a
`SKIP (no suggestion generatable)` until real usage accumulates. Do not fabricate
a row in the production database to unblock it.

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

**`comfortable` on an idle box does not exercise the naming half** — nothing is
binding, so there is no bound to print and the assertion cannot fail. Either
reach a binding state (a temp home whose `max_concurrent_sessions` the current
load already exceeds) or record `PASS (vacuous — verdict unbound)`.

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

Count first (rule 9): `SELECT owner_user_id, visibility, count(*) FROM
app_prod.memories GROUP BY 1,2`. Rows the member must **not** see have to exist,
or a screen showing nothing proves nothing. On 2026-08-18 there were 139 —
128 `private:leo_owner`, 11 `private:owner`.

**Pass:** a member sees their own memory and the profile-shared block, and not
another person's participation memory — with both counts in the evidence.

The surface is client-side, so without a browser assert it at the seam the screen
reads through: `PgvectorMemoryStore.query(principal, …)` for the owner and for a
member, on one connection. Verified 2026-08-18 at `763eb6e2d` — owner 20/20 rows
all `leo_owner`, member **0**, `role_reads False`. That is a read; pass
`record_use=False` so the check does not write `uses` back onto the owner's rows.

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
| Adoption (D2) and retirement's success branch (D3) | no suggestion can be generated: the evidence bar needs two skills with recorded uses (the box has none) and the monthly clock is shut until ~mid-September. Fabricating a `profile_suggestions` row in the production database would test the adoption code against data no user could have produced | real skill usage on the box, or a documented `--force` generation path for testing |
| Box-wide session cap enforcement | 3 profiles × 15 means the box can be *asked* for 45; FG-31 reports the total and enforces nothing, by decision | a decision to make the cap box-wide |

---

## 7. Results template

Revision under test: `17fa7bbbf`  ·  Date: `2026-08-17`  ·  Run by: `Qoder (aliyun ecs RunCommand, i-j6c81aisv2dd8mg17yle)`
Deploy line: `deploy OK (17fa7bbbf)  backup: /opt/data/backups/deploy-20260817-084414` · handover: `current`

Deployed first per §0.1 (box was stale at `d42c7e3c1`): three runs — the
second pulled to `17fa7bbbf` under the old script; the reviewed copy of
`deploy/hermes-deploy.sh` (PR #293) was then installed (`cmp`-identical) and
the third run printed the verdict above with no unaccounted untracked files.

| Case | Verdict | Evidence / reason |
|---|---|---|
| A1 deployed revision | PASS | sha matches; `status --porcelain` empty; deploy #3 (new script) named no untracked file it could not account for |
| A2 units active | PASS | 15 enabled units counted from the box, all `active` |
| A3 no root process | PASS | all 15 `MainPID` owners `hermes` (process checked, not unit file) |
| A4 no state drift | PASS | `No deployment state drift on hermes-systest`; deploy-script layer verified as root — installed copy `cmp`-identical to the reviewed copy |
| A5 handover current | PASS | `handover doc current` from the deploy and from `deploy_state.py handover` (rc=0) |
| A6 end state restored | PASS | before-§B and after-§H captures identical; uat profiles/wrappers/archives/DB schemas/promotion rows all removed; box-wide GoTrue accounts for `uat20260817a–e` remain by design (rule 3) |
| B1 member cannot erase others | PASS | refused with the exact `--all-principals … only an owner or admin` message; memory-file md5s unchanged |
| B2 reset is the caller's | PASS | all five sub-criteria: erase list, `Left in place:`, closing line, shared md5s unchanged, dirs gone as directories |
| B3 purge erases files | PASS | `Erased curated memory:` per path; dirs gone; account intentionally left |
| B4 identity survives elsewhere | PASS | default participation gone; maintenance participation + identity remain |
| B5 last participation takes identity | PASS | existing account enrolled into `maintenance`, purged there; participation and identity both gone |
| B6 transfer never moves memory | PASS (vacuous) | exact "left in place — transfer moves what they owned…" wording; curated files verified present. The rows half had **no subject**: the seeded members owned 0 rows, so `0 transferred, 0 deleted` was the only possible output whether or not the rule holds (re-read 2026-08-18, rule 8) |
| B7 delete help is truthful | PASS | help states memory rows deleted under both strategies; purge erases curated files and identity on last participation |
| C1 roster | PASS | role, email, activation, channels per principal; 50-row cap not observable (3 principals) |
| C2 suspension drops bindings | PASS | real `resolve_principal` seam: principal → `None` while suspended → restored; roster shows `[SUSPENDED HERE]` |
| C3 authority checked where it acts | PASS | over HTTP as an activated member: maintenance-scoped admin action and read → 403 `subject '…' is not enrolled in profile 'maintenance'`; default-scoped `whoami` → 200 |
| C4 account verbs absent | PASS | CLI parser error on `account`; member verbs enrolment-scoped only; HTTP account probes → 404 (earlier 405s were the GET-only SPA catch-all — a bogus GET 404s too) |
| D1 suggestion pass is scheduled | PASS | timer enabled+active; unprompted run 08:10, `Result=success`, `ExecMainStatus=0`, next 2026-08-24 |
| D2 adoption seeds correctly | SKIP | no pending suggestion; the only on-demand generator **crashed in this revision** — `hermes profile suggest` → `TypeError: GoalRegistryStore.__init__() missing 1 required positional argument: 'store'` at `hermes_cli/main.py:11489` (re-run reproduced). Timer path unaffected (`run_review_pass` builds the store correctly). Fabricating a suggestion row in the production DB is out of scope. **Crash fixed since by #300** — re-checked 2026-08-18 at `763eb6e2d`, the command now answers `No profile suggestion generated this cycle`; the case stays SKIP for a different and more durable reason (two generation gates shut: 0 skills with recorded uses, and `_generation_due` false since the 2026-08-16 adoption). See §6 |
| D3 retirement closes goals | PASS | honest-report contract on two real causes: bare profile `⚠ Goals NOT closed (RuntimeError: Supabase app datastore is not configured…)`; clone `⚠ Goals NOT closed (UndefinedTableError: relation "goals" does not exist)` — archive made, retry-safe, no fake exit-0 success (anti-#265). The `Goals closed: N` success branch is only reachable via adoption, blocked by D2 |
| D4 commit-channel refusal | SKIP | no bot token provisioned (§6) |
| E1 capacity names its bound | PASS (vacuous) | one verdict `comfortable` with its load figures in `status` and `doctor`. The naming half had **no subject**: nothing was binding on an idle box, so no bound could be printed and the assertion could not fail — the claim that it names one when binding is read off the render path, not observed |
| E2 latency excludes machines | PASS | 23 interactive samples, p50 10.96s / p95 19.15s; exclusion via `sessions.source` + `cron_*` id convention; 8-sample floor prevents small windows binding |
| F1 digest names what failed | PASS | one section's input broken in a render-only probe → `Capacity: unavailable — RuntimeError: uat-injected: config unreadable`, not omitted |
| F2 one digest per week | PASS | two deliver-mode runs collapsed onto the single `entity-review:2026-W34` row (`ntf_6c9e1b4c…`); nothing stacked |
| F3 promotion store exists | PASS | `app_prod.skill_promotions` present; timer run + 3 manual runs rc=0, no missing-relation error |
| G1 agent-home bundle is current | PASS | `BUILD_ID` mtime 01:54:52+08, right after the last commit touching `agent-home/`/root lock (`5afaa8dcf`); zero such changes between it and HEAD |
| G2 agent-home health | PASS | `/` → 200 with client-side `/login` redirect in the React tree; `/login` → 200 |
| G3 authenticated page | PASS | session minted per the box skill; `/capacity` SSR renders `14.7 GB` / `comfortable` / `p50` / `p95` matching the CLI. Memory-map geometry not proven — client-side fetch, no browser (documented trap) |
| G4 per-principal memory | PASS | real member login via `/api/session/login` resolves the member principal (`is_owner: false`); memory fetches principal-bound rows. The recorded reason was **wrong** — `app_prod.memories` holds 139 rows (128 `private:leo_owner`, 11 `private:owner`), not zero, so the content diff was demonstrable all along. Re-run at the store seam 2026-08-18 at `763eb6e2d`: owner `query()` → 20/20 rows all `leo_owner`, the member → **0**, `role_reads False`. The withholding is now observed rather than assumed; the map's geometry remains unproven (client-side, no browser) |
| H1 clone-all exclusions | PASS | exact contract wording; clone has shared `MEMORY.md`, no `memories/users/`, no `persons/`, no sessions/backups/snapshots |
| H2 clone refuses cleanly | PASS | one error naming `/opt/data/hermes-home-staging/uat-unreadable.bak`; no half-made profile dir (#286 fixed) |
| H3 deletion is clean | PASS | dir gone; list back to `default` + `maintenance` |

**Reviewed 2026-08-18 at `763eb6e2d`** (the box has since moved on from
`17fa7bbbf` through #298–#301). Every claim above was read against the live box
rather than accepted: revision, tree, 15 units, drift, installed deploy tool
`cmp`-identical, and no `uat` leftovers — profiles are back to `default` +
`maintenance`, the roster to `leo_owner` + the older FG-24 systest member, and the
one `profile_suggestions` row is the `adopted` `systest30` from 2026-08-16, not
from this run, so A6 holds. Three verdicts were downgraded and one reason
corrected, per rules 8 and 9; nothing was upgraded, and no row became a FAIL.

**Run-impact disclosures:** the §F double run (and a final re-render to scrub
uat lines from the digest body) re-delivered the W34 digest row — if the
gateway pushes on delivery, the owner may have seen duplicate Telegram copies
of the same weekly digest; dedupe prevented stacked rows, not pushes. No
FAILs; the one defect found is the D2 blocker above.

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
