# Projects (FG-32) — UAT test suite

**Target deployment:** the live Alibaba Cloud ECS box `hermes-systest`
(`develop`), driven through the `alibaba-cloud` MCP `OOS_RunCommand` tool — there
is no SSH. Read the `testing-hermes-systest-box` skill **before** the first
command; every trap in it (dash not bash, 60 s MCP timeout, `HERMES_HOME` must be
passed explicitly, nonzero exit fails the whole call) applies here and has
already produced false results on this box.

**Status of the feature under test:** steps 1–11 are merged and deployed, and
**21 findings from the end-to-end review are still open** at `dfa32fe3c`. This
suite is therefore *not* a pass/fail gate on shipping — it is the acceptance
record. §3 lists which scenarios are **expected to fail** and why, so the tester
can tell a known defect from a new regression. A known-defect scenario that
*passes* is as interesting as a new failure: it means someone fixed it.

**Source of truth for expected behaviour**, in precedence order:

1. `docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md`
   (the design; section numbers below refer to it)
2. `docs/reviews/2026-08-17-projects-end-to-end-review.md` (findings H1–H4,
   M1–M3, L1–L2, F1a–F7, E1–E5 and the ordered fix checklist)

If the deployment contradicts the design, the design wins and it is a finding.
If the design is silent, say **undefined** and raise it as a design question —
do not invent an expectation.

---

## 1. Scope

| In scope | Out of scope |
| --- | --- |
| `hermes projects` CLI on the box (the operator surface, §14) | Unit tests (already green: 185 py + 46 agent-home) |
| The HTTP API at `127.0.0.1:9119/api/registry/projects` (§13) | `web/` dashboard Projects screens (none exist) |
| `agent-home` Projects list + detail (`127.0.0.1:3100`, public `https://home.leolau.ai-and-i.io`) — the primary UI, D20 | Fixing anything. This run **reports**; it does not patch |
| The mandatory-field contract, outputs/progress, cadence + cron, autonomy gates, guidance, runs, score, retro/learning, permissions, cross-profile isolation, to-do promotion | Load/performance, browser matrix, i18n |

---

## 2. Environment, access and safety

### 2.1 Access path

```
mcp_tool(command="call_tool", server="alibaba-cloud", tool_name="OOS_RunCommand",
  tool_args='{"RegionId":"cn-hongkong","InstanceIds":["<instance-id>"],
              "Command":"<sh script>"}')
```

Instance historically `i-j6c81aisv2dd8mg17yle` (`cn-hongkong`, host
`hermes-systest`) — **confirm it with the requester before the first write.**

### 2.2 Layout used by this suite

| Thing | Path |
| --- | --- |
| Checkout (`develop`) | `/opt/data/hermes-agent` |
| `HERMES_HOME` | `/opt/data/hermes-home-staging` |
| Venv entry point | `/opt/data/hermes-agent/.venv/bin/hermes` |
| API (loopback only) | `http://127.0.0.1:9119/api/registry/projects` |
| `agent-home` | `http://127.0.0.1:3100`, public `https://home.leolau.ai-and-i.io` |
| Projects root store | root-anchored SQLite under `HERMES_HOME` (§11); confirm the real path with `hermes projects doctor --json` before touching anything |

Every CLI command in this suite is shorthand for:

```sh
cd /opt/data/hermes-agent && sudo -u hermes -H env \
  HERMES_HOME=/opt/data/hermes-home-staging ./.venv/bin/hermes projects <...>
```

Omitting `HERMES_HOME` does not fail — it answers about a different, empty home.
`--profile <name>` is a **global** selector and goes *before* the subcommand.

### 2.3 Safety rules (non-negotiable)

1. **No service restarts, no `deploy-hermes.sh`, no edits to `.env`,
   `config.yaml`, unit files or the Supabase stack.** If a scenario can only be
   proven by a restart, mark it `BLOCKED — needs restart` and move on.
2. **This suite writes.** Projects UAT cannot be done read-only: it creates
   projects, cards, cron jobs and runs in the live staging store. Get the
   requester's explicit go-ahead for §2.4 before scenario A1, and stop if it is
   refused (then only the read-only scenarios, marked ⌗, can run).
3. **Namespace everything.** Every project created here has goal/name starting
   `UAT-<yyyymmdd>-` and lands with slug prefix `uat-`. Never touch a project you
   did not create.
4. **No real outbound messages.** Contacts get `platform=note`,
   `address=uat@example.invalid`. Never point a contact at a real Telegram chat
   or email.
5. **No irreversible action approvals.** Scenario F3 *provokes* the gate and
   asserts the gate held; it never taps Approve.
6. Never print secret values. Redact with
   `sed -E 's/[A-Za-z0-9_-]{30,}/***REDACTED***/g'`.
7. End every OOS script with `; true`.

### 2.4 What this run writes, and the cleanup that removes it

Writes: rows in the projects root store (projects, outputs, links, members,
contacts, directives, runs, retros, summaries), kanban cards in the host
profile's board, up to two cron jobs in the host profile, and `__pycache__`
bytecode (expected, gitignored).

Cleanup (run it, then prove it):

```sh
# per project created
hermes projects <slug> ...              # capture `hermes projects show <slug> --json` first
curl -s -X DELETE 127.0.0.1:9119/api/registry/projects/<slug>/schedule -H "$AUTH"
hermes --profile <host> cron list       # assert no uat- job remains
hermes projects list --json | grep -c '"slug": "uat-'   # remaining, with reason
```

Projects have no destructive delete by design (the record is durable) — cancel
their runs, remove the schedule, and leave them archived/`done` with a closing
summary saying `UAT artefact, safe to ignore`. **List every artefact left behind
in the final report**, with slug and why.

### 2.5 Authenticating the API and the UI

The API is loopback-only, so `curl` runs *on the box*. Mint a session as the
service user rather than asking for the password (`password_hash` is scrypt and
cannot be reversed):

1. `BasicAuthProvider(...)._mint_session(username).access_token` using
   `dashboard.basic_auth` from `config.yaml`; confirm it with
   `GET 127.0.0.1:9119/api/comms/whoami` → `principal.user_id` (`leo_owner` on
   this box; login subject is `admin`, linked by `hermes owner alias admin`).
2. For `agent-home`, wrap that token in an `agent_home_session` cookie:
   `base64url(JSON) + "." + base64url(HMAC-SHA256(payload, AGENT_HOME_SESSION_SECRET))`,
   payload `{hermesToken, principal, issuedAt}` (see
   `agent-home/src/lib/auth/session.ts`).

**UI evidence needs a browser, not `curl`.** The detail page's panels are React
and the F1a/F1b/F1c/F4/E3 findings are all *client-state* bugs — server HTML
proves nothing about them. Two options, in order of preference:

- **Public host + minted cookie:** drive `https://home.leolau.ai-and-i.io` in
  your own browser with the cookie from step 2 (same app, same secret). Record
  the session; the recording is the evidence for §H.
- If the public host is unreachable or the cookie is rejected, mark every §H
  scenario `BLOCKED — no browser path to agent-home` and escalate. Do **not**
  substitute `curl` and call §H passed.

Non-owner roles (lead/member/viewer/non-member) may not exist as real principals
on this box. For §I, first try real principals; if the box has only `leo_owner`,
drive the role matrix through the CLI's `--actor` flag and say plainly in the
report that these are **CLI-level** checks, not HTTP-session checks.

---

## 3. Expected results as of `dfa32fe3c` (read before executing)

These 21 findings are open. Scenarios marked **XFAIL** below are expected to
fail; record them as `FAIL (known: <id>)`. Anything else failing is a **new**
finding and needs a full repro.

| Finding | One-line effect | Scenarios |
| --- | --- | --- |
| H1 | `agent.human_comms` import does not exist → checkpoint/budget approvals silently never raised | F2, F4 |
| H2 | synthetic `trace_id` + missing `sum_cost_for_trace` → per-run budget unenforceable, cost unknown | F4, G5 |
| H3 | `_enabled_toolsets_for_profile` ignores its argument, reads the calling process's config → can grant what the host profile disables | F6 |
| H4 | inline spawn omits `profile_home` → run executes in the server's profile, row records the host profile | F5 |
| M1 | a repeatable project that has never run is not `stalled` | D6 |
| M2 | list filters applied *after* pagination slicing → rows skipped / early end | C7 |
| M3 | instance owner/admin who is not a member cannot see contact addresses | I5 |
| L1 | toolsets/skills stored as CSV strings | F6 |
| L2 | imported profile projects can carry NULL goal / no output / no host profile | J4 |
| F1a | accept-output returns an ack envelope, `OutputsPanel` merges it as a row → button/status stay stale | H4 |
| F1b | continue-run returns `{run,…}`, `RunView` reads `data.status` → no state change, `budget_gate` never shown | H5 |
| F1c | add-directive returns `{id,applies_from}`, cast to a full directive → renders blank until reload | H6 |
| F2 | the Attention filter cannot show a `stalled` project | H8 |
| F3 | `@router.get("/")` → every list/create pays a 307 | B3 |
| F4 | `RunView` never revalidates after a write | H5 |
| F5 | a waiting run can fall out of the five-run brief | H7 |
| F6/F7 | upstream error detail/path leakage; raw 404 body rendered | H9 |
| E1 | accept-output and directive-activate have no human gate; the CLI monkeypatches the gate out → the learning loop can close with no human in it | I8, K3, L4 |
| E2 | `from_todo.profile` recorded but not honoured; failed stage transition leaves the `project_links` row | J1, J2, J3 |
| E3 | the events tail has no consumer — nothing polls it | H10 |
| E4 | events tail builds `IN (?,…)` from task ids → breaks past ~999 visible cards | K5 |
| E5 | derived score scans only the latest 25 runs before picking 5 scored ones | L3 |

---

## 4. How to record a result

One row per scenario in the final report (§6), plus, for every `FAIL`:

- the **exact** command or click path, verbatim;
- observed vs. expected, quoted (JSON body, CLI stdout, HTTP status, screenshot);
- the finding id if it matches §3, or `NEW` if it does not;
- severity: **S1** data loss / privilege or profile-boundary breach / contact
  address leak · **S2** a documented user action does not work · **S3** wrong
  state shown, right state stored · **S4** cosmetic or wording.

Evidence rules: CLI/API → paste the command and the response (redacted). UI →
screenshot per assertion, and a screen recording for §H as a whole. Never write
`PASS` for something you inferred; `UNTESTED` and `BLOCKED` are respectable
answers and much more useful than a guess.

⌗ = read-only scenario, safe to run even if §2.3 rule 2 approval is refused.

---

## 5. Scenarios

### §A — Preconditions (do these first; if A1–A4 fail, stop and report)

| ID | Steps | Expected |
| --- | --- | --- |
| A1 ⌗ | `git -C /opt/data/hermes-agent -c safe.directory=/opt/data/hermes-agent log --oneline -1` | A sha at or after `dfa32fe3c`. Record it — the whole report is relative to this sha. |
| A2 ⌗ | `hermes projects --help` | Subcommands present: list/show/create/link/outputs/contacts/tools/members/cards/card/playbook/guidance/run/runs/score/retro/summarise/doctor (§14). |
| A3 ⌗ | `curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:9119/api/registry/projects/doctor -H "$AUTH"` | `200`. A `404` means the router is not mounted in the *running* process — check `ExecMainStartTimestamp` vs. the checkout mtime before blaming the code. |
| A4 ⌗ | `curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:3100/login` and `stat -c %y agent-home/.next/BUILD_ID` | `200`; `BUILD_ID` newer than the last `agent-home/` commit. An older `BUILD_ID` means the UI you are testing is **not** this sha — say so in the report, do not rebuild. |
| A5 ⌗ | `hermes projects doctor --json` | Valid JSON; note the store path, the number of existing projects, and any pre-existing complaint (so it is not later blamed on the UAT). |
| A6 ⌗ | `hermes profile list`, and pick the host profile for §D–§F | Record which profile you will schedule and run in. Prefer a low-traffic profile; never the one running live gateways if a choice exists. |

### §B — Creation and the mandatory contract (§2.2)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| B1 | §2.2 | `projects create "UAT-<date>-golden path record" --description /tmp/uat-brief.md --output "Weekly digest" --host-profile <host> --json` | Created. `goal` echoed; `name` **defaulted from the goal**, ≤60; one output; host profile recorded; `status=planning`; `progress` present and derived. |
| B2 | §2.2 | Same, once each: no `--description`; no `--output`; `--host-profile ''`; a 200-char goal | Each refused with a message **naming the missing field**. Refusal must come from the store, not only the router — repeat the no-output case through `POST /api/registry/projects` and expect the same refusal. |
| B3 | **XFAIL F3** | `curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:9119/api/registry/projects -H "$AUTH"` (no trailing slash) | Expected `200`. Observed today: `307` to `/`. Record the redirect chain. |
| B4 | §1.1 | `PATCH /{slug}` with a new `name` only, then with a new `goal` only | They move independently; editing `goal` never rewrites `name`. Both length caps (60 / 160) enforced with a 422. |
| B5 ⌗ | §1.1 | `projects show <slug>` for a project with no audience/score/plan/contacts/files/memories/tools/skills | Optional fields render as *absent* — no empty headings, no literal `None`/`null` in human output. |
| B6 | §2.2 | `projects create` with `--cadence repeatable` and no playbook | Creation allowed; **scheduling** later refused until an active playbook exists (see D4). If creation itself is refused, that is a design mismatch — quote §3.1. |

### §C — Outputs and progress (§2.3, §9)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| C1 | §2.3 | `projects outputs <slug> add "Second artefact" --spec "one PDF"` then `... list` | Both outputs listed, `declared`. |
| C2 | §2.3 | `... deliver <id> --ref /tmp/uat-out.md` then `... list` | Status `delivered`; the ref stored; **delivery is not acceptance**. |
| C3 | §2.3 | `... accept <id>` then `projects show <slug> --json` | Status `accepted`, acceptor recorded, and the response says whether closure is offered. |
| C4 | §2.3 | `DELETE /{slug}/outputs/{id}` on the **last** non-optional output | Refused (409/422) naming the invariant: a project always has ≥1 declared output. |
| C5 ⌗ | §9 | Compare `progress` before C3 and after | The ladder is labelled and ordered: an accepted output outranks the card ratio. The response says which rung produced the number. |
| C6 ⌗ | §9 | A project with cards but zero accepted outputs | Progress falls back to the card rollup, labelled as such — never silently presented as output progress. |
| C7 | **XFAIL M2** | Create ≥3 `uat-` projects with mixed status, then `GET /?status=active&limit=2` and page through with the returned cursor | Every matching row is returned exactly once. Observed today: filtering happens after slicing, so pages skip rows or end early. |
| C8 ⌗ | §9 | `projects list --health attention` and `--health stalled` | Health is derived on read and both filters return coherent sets (see also H8). |

### §D — Cadence and the schedule (§3)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| D1 | §3.2 | `PUT /{slug}/schedule {"schedule":"0 6 * * 1"}` on a repeatable project with an active playbook | 200; `hermes --profile <host> cron list` shows **one** job for the project; both halves of the link stored; `next_run_at` populated. |
| D2 ⌗ | §3.2 | `projects show <slug> --json` right after D1, and again after `cron` state changes | `next_run_at` is a cache — it matches what `cron` says. A stale value is a finding. |
| D3 | §3.2 | `DELETE /{slug}/schedule` | Job removed from the host profile's cron store **and** both link halves cleared; no orphan job. |
| D4 | §3.1 | `PUT .../schedule` on a repeatable project with **no active playbook** | 409 naming the missing precondition (not a 500, not a silent success). |
| D5 | §3.1 | Remove the host profile from the project (`DELETE /{slug}/profiles/{name}`) while scheduled | Scheduling pauses and the project is marked `stalled`; the cron job does not keep firing into a profile the project no longer names. |
| D6 | **XFAIL M1** | A repeatable project created and never run: `projects list --health stalled` | Expected: it appears (nothing has ever produced output). Observed today: never-run repeatables are not marked stalled. |
| D7 ⌗ | §3 | One-off, repeatable and standing projects side by side in `projects list` | Cadence drives what ends the project: one-off closes on acceptance; standing never shows a completion percentage — it shows review age (§9). |
| D8 ⌗ | §3.2 | `projects doctor --json` after D1–D5 | Reports schedule/link/profile mismatches; count zero *unexplained* complaints. Anything it reports is either explained by a scenario above or a finding. |

### §E — Cards and the board (§10)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| E1 | §10 | `projects card add <slug> "UAT card A"` then `projects cards <slug>` | Card created in the host profile's board and linked to the project; visible through the project, and through `hermes kanban` in that profile. |
| E2 ⌗ | §12 | `GET /{slug}/board` as each available role | Board reads always pass the principal; an unreadable project answers **404**, not 403. |
| E3 | §4 | Set `max_in_progress` (via `PATCH /{slug}/autonomy` / the documented field) to 1, then try to promote two cards | The second promotion is refused/queued; the cap is enforced at the `triage → todo` gate, not in the UI only. |
| E4 | §7 | `projects runs`/cancel a run with unstarted cards (see F7) | Unstarted cards archived; already-running work is not killed mid-flight. |

### §F — Runs, autonomy and the gates (§4, §7)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| F1 ⌗ | §7 | `projects run <slug> --dry-run --json` | Prints the cards it *would* create and the compiled guidance block; creates nothing. Diff `projects cards` before/after to prove it. |
| F2 | **XFAIL H1** | Set autonomy `supervised`, `projects run <slug> --trigger manual`, then look for the approval in `hermes_cli.human_comms` notifications | Expected: a checkpoint notification exists and the successor card waits in triage until `continue`. Observed today: the approval seam import fails and is caught broadly, so **no approval is ever raised**. Prove it by grepping the notification store, not the log. |
| F3 | §4 | Set autonomy `autonomous`, then have the run reach an irreversible action | The action is still approval-gated at every autonomy level. **Do not approve it** — assert the gate exists and leave it pending, then cancel the run. If no gate appears, this is S1. |
| F4 | **XFAIL H1+H2** | Set `budget_usd_per_run` very low, run, inspect the run row | Expected: the run pauses at the budget gate, `budget_gate` is exposed, and `continue` resumes it. Observed today: `trace_id` is synthetic and `run_cost` imports a symbol that does not exist, so the budget is unenforceable and cost is unknown. |
| F5 | **XFAIL H4** | `projects run <slug> --trigger manual` for a project whose host profile is **not** the server's profile; then compare the run row's profile against where the session actually ran | They must match. Observed today: the inline spawn omits `profile_home`, so the work happens in the server's profile while the row claims the host profile. Evidence: the spawned session's home/profile, not the row. |
| F6 | **XFAIL H3 (+L1)** | Give the project a toolset the **host profile disables** (`projects tools <slug> set --toolsets <disabled>`), then run | The run spawns *without* that toolset — project tools narrow, never grant (invariant 14). Observed today: the host profile argument is ignored and the calling process's config is read. This is **S1** if it grants. |
| F7 | §7 | `POST /{slug}/runs/{run_no}/cancel` | Returns the updated run row; unstarted cards archived; the record keeps the cancelled run (runs are never deleted). |
| F8 ⌗ | §7 | `projects runs <slug> --json` after F1–F7 | One row per occurrence, with trigger, outcome (`delivered`/`partial`/`no_output`) judged **against the declared outputs**, not against card completion. |

### §G — Guidance (§5)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| G1 | §5 | Start a run, add `projects guidance <slug> add "always cite the source"` **while it is open**, inspect the open run's prompt, then start a second run | The directive is absent from the first run's prompt and present in the second. The system prompt is frozen for a conversation's life; prompt caching is sacred. A mid-run change would be **S1**. |
| G2 ⌗ | §5 | Add directives past the documented cap | Cap enforced; ordering newest-first; each directive carries author and date. |
| G3 | §5 | `projects guidance <slug> retire <id>`, then start a run | The retired directive does not appear in the new run's prompt and remains visible in the record as retired. |
| G4 | §5 | Leave a proactive ask unanswered, then try to run | Runs block on the unanswered ask (§5/§6) rather than proceeding without the answer. |
| G5 | **XFAIL H2** | `projects runs <slug> --json`, look at cost | With no ledger binding, cost must read **"not recorded"** — never `$0.00`, which would claim a free run. |

### §H — agent-home UI (needs a browser and a recording — §2.5)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| H1 ⌗ | §15 | Open the Projects list | The `uat-` projects appear with goal, cadence, status, progress and health. Progress label matches the API's rung. |
| H2 ⌗ | §15 | Open a project's detail page | Five groups in the design's order: commitment · method · cast · work · record. Optional empty fields are absent, not blank-labelled. |
| H3 ⌗ | §15 | Detail page of a project with a schedule | Cadence, next run and host profile shown; the UI never implies a directive can affect a run already in flight. |
| H4 | **XFAIL F1a** | Click **Accept** on a delivered output, **without reloading** | Expected: row flips to `accepted` and the button goes away. Observed today: the ack envelope is merged as a row, so the row stays `delivered` with the button live until reload. Screenshot before/after + after-reload. |
| H5 | **XFAIL F1b+F4** | Click **Continue** on a waiting run, without reloading | Expected: the run's state changes and any `budget_gate` holding it is shown. Observed today: `data.status` is read instead of `data.run.status`, and the view never revalidates — the click appears to do nothing. |
| H6 | **XFAIL F1c** | Add a directive from the Guidance panel, without reloading | Expected: the new directive renders with body, author and date. Observed today: it renders blank until reload. |
| H7 | **XFAIL F5** | With ≥6 runs, one of them waiting, open the detail page | The waiting run is always visible (it is the one needing a human). Observed today: it can fall out of the five-run brief. |
| H8 | **XFAIL F2** | Filter the list by **Attention** with a `stalled` project present | Expected: stalled projects surface under Attention (or a separate, reachable filter). Observed today: the chip cannot show them. |
| H9 | **XFAIL F6/F7** | Request a project you cannot read, and force an upstream error | A 404 renders as a human "not found" page; no upstream path, internal detail or stack reaches the browser. Capture the exact rendered body. |
| H10 | **XFAIL E3** | Change project state via the CLI, then watch the open detail page for ~2 min without touching it | Expected: the page picks the change up (the events tail exists for exactly this). Observed today: nothing polls it; the page only refreshes after a local write. Record the two minutes — a still frame proves nothing. |
| H11 | §15 | Every UI write in §H | After a reload, the UI state matches the API. A defect that survives reload is worse than F1x and is a **new** finding. |

### §I — Permissions and privacy (§12)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| I1 ⌗ | §12 | Read/write a `uat-` project as owner, lead, member, viewer, non-member and a member of a *different* project (real principals if they exist, else `--actor`; say which) | The §12 matrix holds. Note the surface you used. |
| I2 ⌗ | §12 | Any read of a project the principal cannot see | **404**, and the body reveals nothing about existence. A 403 is a finding. |
| I3 ⌗ | §12 | `viewer` reads a project with contacts | `contacts[].address` is **omitted**, not blanked — an empty-string address that a client renders is still a leak. **S1** if present. |
| I4 | §12 | `viewer` attempts every write verb (patch, outputs, cards, run, score, guidance, activate) | Each refused. Enumerate them; one permitted write is **S1**. |
| I5 | **XFAIL M3** | Instance owner/admin who is *not* a project member reads contacts | Expected per §12: they may see addresses. Observed today: they cannot. (Low severity — the failure direction is safe.) |
| I6 ⌗ | §12 | Compare the CLI's answers to the API's for the same actor/verb | Identical. The CLI must go through the same gate, not around it. |
| I7 ⌗ | §11 | Project A in profile X, project B in profile Y | No response joins across profiles; cross-profile reads are bounded and principal-filtered. Any row from the other profile is **S1**. |
| I8 | **XFAIL E1** | Accept an output, and activate a directive, as a **sessionless/agent** caller (no interactive subject) | Both are human-only acts (§8.1) and must be refused 403 like `score` is. Observed today: neither has an identity gate. **S1**. |

### §J — To-do promotion and profile import (§10)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| J1 | §10 | Create a to-do in the host profile, then `projects card add <slug> "<title>" --from-todo <id>` | Card created, to-do linked, provenance recorded, and the card is visible on both sides. |
| J2 | **XFAIL E2** | Same with a to-do belonging to a **different** profile (`from_todo.profile` set) | Either a 422 refusing unsupported foreign-profile promotion, or a true profile-aware, principal-filtered read. Observed today: the profile is recorded and ignored — the default store is read, so it silently resolves the *wrong* to-do or nothing. Do not test this with a to-do you care about. |
| J3 | **XFAIL E2** | Force the stage transition to fail after the card is created (e.g. promote into a project whose board state refuses it) | Full rollback: no card **and** no `project_links` row. Observed today: the card is deleted and the link row survives. Prove the leftover row. |
| J4 | **XFAIL L2** | Import a profile that contains projects (or inspect an already-imported one) | Imported rows still satisfy the mandatory contract, and slug collisions get a suffix with provenance retained. Observed today: NULL goal / no outputs / no host profile can arrive. |
| J5 ⌗ | §11 | `HERMES_PROJECTS_DB` pointed at a scratch path in a throwaway shell | The root override is honoured and the live store is untouched. Do **not** set it for any other scenario. |
| J6 ⌗ | §11 | Run the root migration path twice (on a **copy** in `/tmp`, chowned to `hermes` — SQLite needs a writable *directory*) | Idempotent; second run is a no-op. Never against the live store. |

### §K — Record, events and summary (§8, §11)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| K1 | §8 | `projects retro <slug> <run_no> --write` (stdin) with `--propose playbook=…`, `--propose directive=…`, `--propose skill=…` | Retro stored; max 3 proposals; **all land inactive**. |
| K2 ⌗ | §8.2 | Inspect the proposals | A playbook revision needs lead/admin activation; a directive needs member activation; a skill proposal records project/run provenance and leaves the loop to `agent/background_review.py`. |
| K3 | **XFAIL E1** | Activate a proposed directive as a sessionless caller | Refused — activation is a human act. Observed today: no gate. |
| K4 | §11 | `GET /{slug}/events` twice, second time with `since=<latest_event_id>` | Second call returns only what is new; the cursor never replays or skips. |
| K5 | **XFAIL E4** | Same on a project with **>999 visible cards** (build it in a scratch store, not the live one, or state that you could not) | Works. Observed today: the tail builds `IN (?,…)` from task ids and hits SQLite's variable limit. If you cannot build 1000 cards safely, mark `UNTESTED` and cite the code path. |
| K6 | §11 | `projects summarise <slug>` (stdin), then read the project | The rolling summary is stored and rendered where the design says. |
| K7 ⌗ | §8 | `projects show <slug> --json` at the end | The record contains the whole history — every run, score, retro, directive and accepted output, in one read. This is the feature's whole point: a reviewer with only this JSON can tell what happened. |

### §L — Score (§8.1)

| ID | Maps to | Steps | Expected |
| --- | --- | --- | --- |
| L1 | §8.1 | `projects score <slug> <run_no> 4 --note "…"`, then `0` and `6` | 1–5 accepted; out-of-range refused; a score is editable and the edit is recorded. |
| L2 ⌗ | §8.1 | Compare the human score with the run's own self-score | Both visible, side by side, never merged. The divergence is the learning signal. |
| L3 | **XFAIL E5** | Score five runs, then add ≥25 unscored runs, then read the project score | The derived score still averages the latest **five scored** runs. Observed today: only the latest 25 runs are scanned, so the scores disappear. Re-score an old run and confirm the derived score moves. |
| L4 | **XFAIL E1** | `projects score` as a sessionless caller, and via the CLI | Refused without a human session. Observed today the CLI monkeypatches `_interactive_subject` to return the principal unconditionally, so the agent's own invocation counts as a human. Quote the patch site. **S1.** |

### §M — Regression sweep (last)

| ID | Steps | Expected |
| --- | --- | --- |
| M1 ⌗ | `hermes --profile <host> kanban`, `todo list`, `cron list`, `goal list` | Projects added rows; it did not change these features' behaviour. Projects depends on them; none of them know about Projects. |
| M2 ⌗ | `systemctl list-unit-files 'hermes-*.service' --state=enabled --no-legend` + `agent-home.service` | All 12 long-running units plus `agent-home` still `active/running` (skill's closing rule). |
| M3 ⌗ | `git -C /opt/data/hermes-agent -c safe.directory=… status --porcelain --untracked-files=no` | Empty. UAT changed no deployed file. `__pycache__` entries are expected and gitignored. |
| M4 | §2.4 cleanup, then `projects list --json` and `cron list` | No `uat-` cron job remains; every remaining `uat-` project is listed in the report with a reason. |

---

## 6. Final report template

Write to `docs/testing/results/2026-xx-xx-projects-uat-run.md` (or attach it):

```markdown
# Projects UAT — run <date>

Deployment: hermes-systest <instance-id>, develop <sha>, agent-home BUILD_ID <ts>
Executed by: <agent/session>   Access: alibaba-cloud OOS_RunCommand (no SSH)
Write approval: <who approved §2.4, when>
Surfaces exercised: CLI ☐  API ☐  agent-home browser ☐ (if ☐ unchecked, why)

## Verdict
<2 sentences: is the deployed Projects feature usable for its purpose, and what
is the single thing most in the way.>

## Result table
| ID | Result | Finding | Severity | Evidence |
|----|--------|---------|----------|----------|
| A1 | PASS   |         |          | sha …    |
…
(Result ∈ PASS / FAIL (known: id) / FAIL (NEW) / BLOCKED / UNTESTED)

## New findings
### N1 — <title>  [S?]
Repro: <verbatim commands / click path>
Observed: <quoted>   Expected: <design §>   Evidence: <path/screenshot>

## Known findings confirmed / no longer reproducing
<per §3 id: confirmed | NOW PASSES (someone fixed it — say what you saw)>

## Not verifiable on this deployment
<scenario id → why (restart forbidden, no second principal, no browser path…)>

## Artefacts left on the box
<slug / cron job / file → why it remains, and how to remove it>
```

Counts to state explicitly: scenarios executed, PASS, FAIL-known, FAIL-new,
BLOCKED, UNTESTED — they must add up to the number of scenarios in §5.
