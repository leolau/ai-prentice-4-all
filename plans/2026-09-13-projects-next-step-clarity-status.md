# Implementation status — Projects "next step" UX clarity

Plan: `2026-09-13-projects-next-step-clarity.md`

| Item | Description | Status |
|---|---|---|
| A | Fix `nextAction()` activation link pointing at `#panel-settings` (no control there) | **Done** |
| B | Share readiness-item reasoning between header checklist and Progress card | **Done** |
| C | Numbered "what's left before it can run" sequence with inline action buttons | **Done** (numbered checklist + inline Activate; see notes) |
| D | Visible (non-hover-only) disabled-reason next to "Run now" | **Done** (points at the now-accurate checklist) |
| E | Optional: merge Activate + Run for one-off projects | Not started — still needs a product decision, left as a follow-up |

## What changed

- `agent-home/src/components/projects/readiness.ts` — added a `"status"`
  item to `readinessItems()` (the `ReadinessItem` type already reserved
  this key but nothing populated it). It checks `project.status ===
  "active"` and anchors at `#project-header`, positioned right after
  `"plan"` so the ladder reads outputs → profile → plan → activate →
  schedule. `isRunnable()` picking this up is safe: the "Run now" button is
  only ever rendered once `status === "active"`, so this item is always
  `ok` on that code path — no behavior change there, just a real entry in
  the shared checklist for the draft state.
- `agent-home/src/components/projects/panels/ProgressPanel.tsx` —
  `nextAction()` now takes the same `ReadinessItem[]` the header uses
  (instead of a bare `runnable` boolean) and surfaces the *first failing
  item's own hint/anchor* instead of two bespoke, sometimes-wrong messages
  ("Activate the project… #panel-settings" and "Finish the readiness
  checklist… #panel-plan"). The Progress card and the header checklist can
  no longer disagree.
- `agent-home/src/components/projects/ReadinessChecklist.tsx` — numbered
  the unmet items (in the order they should be cleared) and gave the
  `"status"` item a real inline **Activate now** button (calls the same
  `onActivate` the header's own Activate button uses) instead of a link to
  a panel with no control. Kept links for the other items, which do need
  navigation to fill in data.
- `agent-home/src/components/projects/ProjectDetailView.tsx` — added
  `id="project-header"` to the header (so the "status" item's anchor
  resolves to something real), wired `onActivate`/`activating` into
  `ReadinessChecklist`, passed `readiness` into `ProgressPanel` instead of
  `runnable`, and swapped the invisible-until-hover `title` on "Run now"
  for one that points at the now-trustworthy checklist.
- `agent-home/src/components/projects/ProjectDetailView.test.tsx` —
  updated the one call site that constructs `<ProgressPanel>` directly to
  pass `readiness={[]}` (prop is now required).

## Verification

- `npx tsc -p . --noEmit` — clean.
- `npx vitest run` — could not execute in this environment: the shared
  root `node_modules` has a pre-existing broken native binding
  (`@rolldown/binding-darwin-arm64` missing, used by Vite/Vitest under
  this Next.js version.) This predates this change (confirmed via `git
  status` — no lockfile/node_modules changes came from this session) and
  blocks the whole test suite, not just these files. Recommend the team
  run `npm install` fresh (or clear `node_modules` per the vitest error
  message) in a normal dev environment to confirm the updated
  `ProjectDetailView.test.tsx` still passes.
- `npx eslint …` — also blocked in this environment (`eslint-plugin-react-hooks`
  not resolvable from the installed tree), same pre-existing cause.
- Manually traced the new ladder end-to-end against `readiness.ts`,
  `PlanPanel.tsx` (draft/activate), and `ProjectDetailView.tsx`'s `activate()`
  handler to confirm the inline "Activate now" button in the checklist
  calls the exact same code path as the header's "Activate" button.

## Notes on (C) — not a full step-by-step wizard

The plan's more ambitious version of (C) imagined a dedicated numbered
sequence component separate from the checklist, with inline one-click
actions for every step. In practice only two steps in the chain
(**activate the plan revision**, already a button in `PlanPanel`; and
**activate the project**, now a button in the checklist too) are
single-click actions — the others (declare outputs, add a profile, write a
schedule) need the user to enter data in another panel, so a link is the
right affordance, not a fake "do it here" button. I extended the existing
`ReadinessChecklist` (numbered, ordered, one real gap closed) rather than
building a parallel component, since duplicating the ladder logic was
exactly the bug in (A)/(B).

## Open follow-up (E)

Still needs a product decision: merge "Activate" + "Run now" into one
click for one-off projects, or keep them separate so a user can activate
now and run later. Not implemented.
