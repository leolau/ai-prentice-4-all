# Projects Feature — Session Handoff

Written 2026-08-21 for the next agent picking up the Projects feature, at
`develop` `f605f8088`. Everything below was verified against the tree, not
inferred from the implementing agent's claims — where something is *unverified*
it says so.

The purpose of this file is to make the next session cheap: what Projects is,
where every document lives, what is done, what is not, how to verify it, and
the defect classes this feature keeps producing.

---

## 1. What Projects is

A **Project** is the durable record of a piece of work: the owner's 15 fields,
the kanban cards that execute it, and one **run** row per occurrence — so the
work can be executed, reviewed, repeated and learnt from over time.

The 15 fields (M = mandatory, O = optional), frozen by the owner:

1. Goal (short outcome sentence) — M
2. Requirements / description — M
3. Outputs — M
4. Participants — M
5. Progress — M
6. Target audience — O
7. Score — O
8. Samples / references — O
9. Plan — O
10. Contacts — O
11. Files — O
12. Memories — O
13. Tools — O
14. Skills — O
15. Conversation histories — O

`projects.name` (short label, ≤60) is a **separate** field from
`projects.goal` (≤160); the name defaults from the goal at create and never
tracks it afterwards.

**Projects adds no engine.** Schedules are `hermes cron` jobs in the host
profile; step sequencing is card parent links plus the shipped
`recompute_ready()`; asks are FG-10 + a to-do; cost comes from the C8 ledger.
Projects depends on the board, to-dos and goals — none of them know about
Projects.

Three axes carry the owner's brief: **cadence** (one-off / repeatable /
standing), **autonomy** (manual / supervised / autonomous, enforced at the
existing `triage → todo` gate), and **guidance** (durable attributed
directives compiled into the **next** run's seed prompt).

---

## 2. The documents, and what each is for

| File | Role |
|---|---|
| `docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md` | **The canonical design.** Numbered FG doc: §12 endpoints, §13 the pages + lifecycle contract, §16 the behaviour contracts (test plan), §17 sequencing, §18 decisions, §20.2 the open-defect register. |
| `docs/design/projects-feature-design.md` | The original standalone design (ed.3.2). Superseded by FG-32 for record purposes; still readable prose. |
| `docs/reviews/2026-08-17-projects-end-to-end-review.md` | **The working review + worklist.** Every finding (F/H/E/L/U series) with call site, runtime effect, the fix against a real seam, and the test that catches it — plus the ordered block checklists the implementing agent works from. This is the file to read first. |
| `docs/reviews/2026-08-13-projects-steps-1-8-review.md` | The earlier steps 1–8 review (17 findings, all closed). Historical. |
| `docs/testing/projects-uat.md` | **The standalone UAT suite** for the ECS systest box. Self-contained by design: instance id, token/cookie minting, fixtures, safety fence, teardown, report template. Never needs this session's context. |

Convention worth keeping: a finding is **never** silently deleted. It is ticked
in place with a parenthetical note saying *how* it landed and what pinned it.
That trail is why blocks 4c → 4f could each be reviewed against the previous
one's promise.

---

## 3. Where the work stands

**Backend, agent-home surface, CLI, runs, retro/learning and the create/remove
lifecycle are all implemented and merged into `develop`.** Every finding from
every review block is ticked. The feature has **not** been deployed and the UAT
suite has **not** been executed.

### PR map

| PR | What it did |
|---|---|
| #241 / #242 / #245 | The design: standalone → rebuilt around the 15 fields → goal/name split |
| #251–#259 | Steps 1–5: store, rollup helper, API, run lifecycle, schedule |
| #261 / #263 / #266 | Steps 6–8: BFF, list page, detail page |
| #270 | Steps 1–8 review (17 findings) |
| #283 / #284 | FG-32 + the end-to-end review, then fix-ready detail and the ordered checklist |
| #290 / #291 | The UAT suite, then made executable with no session context |
| #294 / #295 / #296 / #297 / #301 | Blocks 1–5: the 21 findings from #270/#284 (run-lifecycle seams, health/list routing, the later steps, coverage holes) |
| #304 | The create/remove UI specification (U1) |
| #305 / #307 / #310 / #312 / #314 | Blocks 4b → 4f implementation |
| #306 / #308 / #309 / #311 / #313 | Blocks 4c → 4f review write-ups and plans |

### The lifecycle blocks, in order (the recent work)

- **4b (#305)** — the create form, the `[⋯]` menu, archive/restore/delete
  endpoints, the Archived chip. Findings **U2–U6**.
- **4c (#307)** — the archived-inert gate (five routes), structured 422
  forwarding to the form, archived-inclusive card count, flag-aware chip.
  Findings **U7–U8**.
- **4d (#310)** — the gate on all twelve growing routes, the archive-time
  open-run precondition, the run page's archived wiring, the principal-blind
  delete count. Findings **U9–U12**.
- **4e (#312)** — the gate below the router (`kanban_db.create_task`), the
  unpaged open-run scan, the missing frontend tests, the U12 doc narrowing.
  Findings **U13–U15**.
- **4f (#314)** — `ArchivedProjectError`, the `getattr` removal, real-path
  boundary tests. **Not yet reviewed** — see §7.

---

## 4. The invariants a reviewer or implementer must hold

These are the contracts that keep getting broken, in the order they matter.
FG-32 §16 is the full list; these are the load-bearing ones.

1. **Prompt caching is sacred.** Guidance affects the **next** run, never a
   running conversation — the system prompt is frozen for a conversation's
   life, and the UI must not imply mid-conversation correction.
2. **A shelved (archived) project does not run and does not learn.** Archive
   stops *execution and learning*; **record bookkeeping stays open** and is
   named explicitly: `PATCH /{slug}`, links (how samples, references, files,
   memories and conversation histories attach), member/profile/contact
   bookkeeping, every DELETE verb, directive retirement, and cancel. Archive
   itself refuses to shelve a project holding a `running`/`waiting` run, so
   cancel is the sanctioned way out.
3. **Removal is archive by default.** Archive keeps the whole record, detaches
   the cron job, and restore returns the project to `paused`. **Hard delete**
   is deliberately narrow: already archived, no runs, no delivered/accepted
   output, no cards (**including archived cards, across all principals**),
   typed-slug confirmation, human gate, active-pointer and schedule cleanup —
   and it must **never** cascade into the per-profile kanban store, because
   `tasks.project_id` has no FK back to `projects`.
4. **Human-only acts need a verified human identity**: accepting an output,
   scoring a run, activating a directive.
5. **Progress is an ordered ladder** (outputs accepted → goal metric → cards
   ratio), never a bare card ratio. **Human score is 1–5 and separate** from
   the run's self-score; the divergence is the signal.
6. **A run is judged against its declared outputs**, not its cards:
   `delivered` / `partial` / `no_output` (every card green and nothing produced
   is `no_output`).
7. **Project tools/skills only ever narrow the host profile's** — never grant.
8. **Board reads always pass `principal`**; 404 not 403 for invisible records;
   `viewer` responses omit `contacts[].address`.
9. **`agent-home` is the primary UI.** `web/` is a secondary operator console.

---

## 5. Verification know-how (the expensive part to rediscover)

### Checkouts

- `/home/ubuntu/repos/ai-prentice-4-all` — holds **`develop`**. Leave it there.
- `/home/ubuntu/repos/proj-review` — the review worktree, for branches.
  Checking out `develop` here fails (`already checked out at …`), which is
  intentional: branch here, read `develop` there.

### Commands that matter

```bash
cd /home/ubuntu/repos/ai-prentice-4-all && source .venv/bin/activate
# the lifecycle/writer surface — fast and the one to run on any archive change
python -m pytest tests/hermes_cli/test_projects_api_lifecycle.py \
  tests/hermes_cli/test_todos_promote.py tests/hermes_cli/test_kanban_db.py \
  tests/hermes_cli/test_kanban_cli.py -q
ruff check hermes_cli/projects_api.py hermes_cli/kanban_db.py

cd agent-home
npx vitest run "src/app/projects/[slug]/runs/[runNo]/page.test.tsx" src/components/projects
npx tsc --noEmit
```

### ⚠ Pre-existing test failures — do not chase them

A wide sweep (`pytest tests/hermes_cli -q -k "project or kanban or todo"`)
fails **22 tests**. They are **pre-existing cross-file pollution in the kanban
suites**, not a regression: the identical set fails at `4c40a5e45` (before
#312). Named examples: `test_kanban_db.py::test_detect_stale_*`,
`test_kanban_decompose.py::*`, `test_kanban_lifecycle_hooks.py::*`,
`test_projects_api_promote.py::test_promote_stage_failure_rolls_the_card_back`,
`test_projects_run_seams_defaults.py::*`,
`test_projects_schedule.py::test_doctor_cadence_overdues_and_broken_board`.
The same files pass when run alone.

**The method, and it is the method to reuse:** before calling any failure a
regression, check out the pre-change commit in `proj-review` and run the *same*
selection. Never report "pre-existing" without that comparison.

`agent-home` also has pre-existing eslint errors in `chat/` and `settings/`
files unrelated to Projects.

### PR mechanics for this repo

- Use the builtin git/PR tools, not `gh pr view` / `gh pr create`.
- Requesting a reviewer has no builtin, so use the API:
  `gh api -X POST repos/leolau/ai-prentice-4-all/pulls/<n>/requested_reviewers -f "reviewers[]=leolau"`.
  **`leolau` is requested on every PR.**
- `fetch_pr_template` before creating a PR.
- Docs conflicts happen when a block's tick-note lands on the same checklist
  line a new section starts after — merge `origin/develop` in and keep both
  halves; never rewrite the other block's note.

---

## 6. Defect classes this feature keeps producing

Read these before reviewing the next block: every one of them recurred.

1. **Ack envelope merged as a row.** Three write endpoints returned
   `{ok: …}` envelopes and the UI merged them as project rows, so accept-output,
   continue-run and add-directive *looked* like no-ops until reload. `cancel`
   worked only because it returned a bare row. **Rule: a write returns the
   updated row.**
2. **A gate installed at one layer only.** The archived refusal was fixed three
   times: five routes (4c) → twelve routes (4d) → the writer below the router
   (4e), because `tasks.project_id` is writable from `todos_api.promote_todo`,
   `hermes kanban create --project`, `kanban_swarm`, `tools/kanban_tools.py`
   and `projects_run.py`. **Rule: put the gate where the resource is resolved,
   once, and enumerate the writers to prove it.**
3. **A "does not grow" promise widened in docs while the code covers a
   subset.** Every time the wording and the gate disagreed it produced a new
   finding. **Rule: change the wording and the gate in the same PR, and name
   the deliberately-open list.**
4. **A page window standing in for a question.** `list_project_runs(limit=50)`
   as an open-run precondition missed an old held run. **Rule: ask preconditions
   in SQL (`WHERE status IN …`), never over a page of the newest rows.**
5. **A boundary pinned by a mock.** The promote-refuses-archived test patched
   `create_task` with a `side_effect`, so nothing exercised the real route
   against a real archived project. **Rule (AGENTS.md): security boundaries and
   resolution chains get one real-path test against real stores; keep the
   mocked test for the status-code mapping if you like, but not instead.**
6. **A consumer left untested while the payload is tested.** `total_all_principals`
   was produced and asserted, but nothing pinned the UI reading it, so a rename
   would silently restore the old behaviour. **Rule: test producer *and*
   consumer.**
7. **A broad `except` overloading a status code.** `except ValueError → 409`
   also caught `create_task`'s ordinary input errors and dropped their log
   line. **Rule: give a refusal its own exception type** (Block 4f's
   `ArchivedProjectError(ValueError)` — subclassing keeps existing catches
   working).
8. **`getattr` on a declared field.** `getattr(project_obj, "archived", 0)` on a
   dataclass field that is declared and coerced. AGENTS.md forbids it, and the
   default silently reads falsy after a rename.
9. **Upstream seams imported from APIs that do not exist** (steps 1–5: an
   `agent.human_comms` import while the shipped seam is
   `hermes_cli.human_comms.NotificationStore.create`; a nonexistent
   `sum_cost_for_trace`), which made approvals and per-run budgets silently
   inert. **Rule: assert the real default resolves a real symbol.**
10. **A 307 on every list call** (`@router.get("/")` where the todos router uses
    `""`), and `.../{slug}` reads that never revalidate. Cheap, recurring.

---

## 7. Open work

1. **Block 4f (#314) has not been reviewed.** It landed after the Block 4f
   worklist was written and closes U13–U15 by the commit message's account:
   `ArchivedProjectError(ValueError)` raised at the `create_task` gate and
   caught precisely by `todos_api` (409) and the CLI (its own voice, with a
   separate "invalid arguments" branch), `project_obj.archived` instead of the
   `getattr`, and real-path tests on both doors. **Verified only this far:**
   `test_todos_promote.py + test_kanban_db.py + test_kanban_cli.py +
   test_projects_api_lifecycle.py` = **310 passed** at `f605f8088`, and the
   diff does what the message says. A full review pass (do the real-path tests
   assert against the stores? does the CLI branch keep rc 2? is anything else
   catching the bare `ValueError` it no longer raises?) has **not** been done.
   One nit already visible: `todos_api.promote_todo` imports
   `ArchivedProjectError` **inside the function** — the repo rule is imports at
   the top of the file.
2. **The UAT suite has never been executed.** It targets the ECS systest box
   and is written for a *different* agent with no session context. Do not
   execute it as part of a review; hand it over (§8).
3. **No deployment.** Nothing in Projects has been exercised against the
   deployed instance, so every "works" claim in the review docs is a
   test-and-code claim, not a live one.

---

## 8. Ready-to-use handoff prompts

**To review the next implementation block:**

> Review the latest Projects block in `leolau/ai-prentice-4-all` against the
> worklist in `docs/reviews/2026-08-17-projects-end-to-end-review.md` (read
> `docs/projects-feature-handoff.md` first). Verify against the tree, not the
> implementing agent's claims: read the diff, run the lifecycle test selection
> in §5, and before calling any failure a regression compare the same selection
> at the pre-change commit. Report what landed, what did not, and any residual
> with exact file, route and function. Do not fix product code; if asked, write
> the findings into the review doc as the next block and open a docs PR.

**To implement the next block:**

> Implement Block <n> of `docs/reviews/2026-08-17-projects-end-to-end-review.md`
> in `leolau/ai-prentice-4-all`, branching from `develop`. Read
> `docs/projects-feature-handoff.md` §4 (invariants) and §6 (recurring defect
> classes) first, plus FG-32 §12/§13/§16. Do the items in order, backend before
> UI. Behaviour contracts, not change-detector tests; real-path tests for the
> security boundary; agent-home is the primary UI. Tick each finding in place
> with a note on how it landed, and update FG-32 §20.2. One PR per block, with
> `leolau` requested as reviewer.

**To execute the UAT:**

> Execute the Projects UAT suite in `docs/testing/projects-uat.md` on the
> `leolau/ai-prentice-4-all` repo (branch `develop`) against the deployed
> systest box. Read that file first and follow it exactly — it is
> self-contained; do not ask for anything and do not substitute a local server.
> Read the `testing-hermes-systest-box` skill before your first command. All
> scenarios, obeying §2.3's safety rules and §2.4's teardown. Compute which
> failures are expected from the deployed sha via the `merge-base` checks — do
> not assume. Change no product code. Write the report per §6 to
> `docs/testing/results/<yyyy-mm-dd>-projects-uat-run.md` and open a PR.
