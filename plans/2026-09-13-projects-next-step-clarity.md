# Projects UX: "what do I click next?" — findings & proposal

Status: **proposal, not yet implemented** — see companion status file.

## Where I looked

`agent-home/src/app/projects/**` and `agent-home/src/components/projects/**`:
`ProjectsList`, `NewProjectForm`, `ProjectDetailView`, `ProgressPanel`
(`nextAction()`), `ReadinessChecklist`, `readiness.ts`, `PlanPanel`,
`SettingsPanel`, `ProjectLifecycleMenu`.

The team already built real "what's next" infrastructure here — a
`ProgressPanel` headline (`nextAction()`), a `ReadinessChecklist` with
per-item links, and a `PlanPanel` that polls a background draft job and
shows a status line. This is good design intent. The problem isn't "no
guidance exists," it's that **the guidance is wrong, buried, or split
across three places that don't agree with each other**. That's what reads
as "unclear which button to press."

## Concrete problems found

1. **The "Next for you" link for activation points at the wrong panel — the
   button isn't there.**
   `ProgressPanel.nextAction()` (`panels/ProgressPanel.tsx:37-39`) returns:
   ```
   { text: "Activate the project to let it run.", href: "#panel-settings" }
   ```
   But `SettingsPanel` (`panels/SettingsPanel.tsx`) only has **Schedule**
   and **Autonomy** controls — no Activate button. The actual "Activate"
   button lives in the sticky **page header**
   (`ProjectDetailView.tsx:294-317`), rendered above the scrollable panel
   list, easy to have already scrolled past. A user who clicks "Next for
   you" lands on Settings, sees no activate control, and is stuck exactly
   the way you described.

2. **The readiness checklist always blames the Plan panel, even when the
   real blocker is something else.**
   `nextAction()` (`panels/ProgressPanel.tsx:40-41`) collapses every
   `!runnable` case into one message — *"Finish the readiness checklist so
   it can run."* — linked to `#panel-plan`. But `isRunnable()`
   (`readiness.ts`) can fail on **outputs**, **profile**, **plan**, or
   **schedule**. If the actual gap is a missing host profile, the user is
   sent to the Plan panel, finds a plan already active, and has no idea
   what's actually missing — the real answer is only visible in the
   (easy-to-miss) `ReadinessChecklist` block inside the header.

3. **Two independent "next step" surfaces that don't cross-reference each
   other.** The header (`ReadinessChecklist`, correct and specific) and the
   `ProgressPanel` (`nextAction`, generic and sometimes wrong) both claim to
   answer "what do I do now," with different fidelity. A user reasonably
   trusts the more prominent `ProgressPanel` card ("Next for you") — which
   is the less accurate one.

4. **Multi-step chain, each step a differently-styled control in a
   different panel, with no visual sequence.** To go from "created a
   project" to "it ran," a user must, depending on choices:
   - Plan panel: wait for "Agent is drafting…" → **Activate** the proposed
     revision (a small button buried under "Proposed revisions").
   - Header: click **Activate** (only appears once `status !== active`).
   - Header: click **Run now** (disabled with only a hover `title` tooltip
     explaining why — no visible reason when not hovering, and no
     indication anywhere that this button exists until the two prior steps
     are done).
   None of these are visually connected as "step 1 of 3" — they look like
   three unrelated buttons that happen to sit in different rounded boxes.

5. **"Run now" disabled-reason is only a `title` attribute**
   (`ProjectDetailView.tsx:298-303`) — invisible on mobile / no explanation
   without a mouse hover, and it's a generic "Finish the checklist below,"
   not "here's the one item to fix."

6. **New-project flow's "agent drafts a plan" choice doesn't say the plan
   still needs manual activation until much later.** `NewProjectForm`'s
   plan-choice copy ("...activate the plan when it looks right") is good,
   but it's step 2 microcopy read once at creation time; by the time the
   draft finishes (up to ~1 minute later, per `PlanPanel`'s comment) the
   user has forgotten and is now looking at the top-level page wondering
   what to click.

## Proposed UX fixes (ranked by effort)

**A. Fix the broken link (bug, not a redesign) — do first, trivial diff.**
Change `nextAction()`'s activation case to link to wherever Activate
actually lives. Two options:
   - Point it at the header itself (`href: "#top"` / no anchor, just prose:
     "Activate the project — the button is at the top of this page.") or
   - Move a real "Activate" affordance into `SettingsPanel` too (a project
     lifecycle control belongs in Settings conceptually) and keep the
     header one in sync, so the link is correct either way.
   Recommend the latter — a Settings panel with no lifecycle control while
   the page's guidance sends you there is the concrete bug to close.

**B. Make `nextAction()` reuse `readinessItems()` instead of collapsing to
one generic message.** When `!runnable`, surface the *first failing
readiness item's own hint and anchor* (already computed, already
correct) instead of a second, vaguer message. One source of truth for
"why can't this run," referenced by both the header checklist and the
Progress card.

**C. Turn the create→draft→activate→run chain into a visible numbered
sequence on the project page** (only while `!runnable`, collapses once
runnable — same "quiet once healthy" philosophy the `ReadinessChecklist`
already uses):
   ```
   1. ✓ Outputs declared
   2. ✓ Profile attached
   3. ⏳ Plan — agent is drafting… (or) ○ Activate the proposed plan → [Activate]
   4. ○ Activate the project → [Activate]
   5. ○ Run now (disabled until 1–4 are done)
   ```
   Each incomplete step shows its own action button inline (not a link to
   scroll away) where the action is a single click (activate plan /
   activate project); only "Run now" needs the full page context and stays
   in the header. This directly answers "which button do I press next" by
   putting the next button in the same component as the question.

**D. Replace the `title`-only disabled reason on "Run now" with a visible
inline hint** next to the button (reuse the same first-failing-item hint
from (B)), so it's visible without hovering and matches on mobile.

**E. (Optional, larger) Collapse "Activate project" and "Run now" into one
guided action** for the common case (`one_off` + freshly created): if the
project is otherwise runnable, "Activate" could just say "Activate and
run" and do both in one click, since nothing else happens between them.
Keep the two-step version for `repeatable`/`standing` where activation
without running is meaningful (arms the schedule).

## Suggested implementation order
1. (A) fix the dead link — 1-line change, immediate correctness win.
2. (B) share readiness reasoning between header and Progress card.
3. (D) inline visible disabled-reason.
4. (C) the numbered sequence component — the real "which button next"
   fix; touches `ProjectDetailView` layout most.
5. (E) only if the team wants to reduce clicks further; changes the
   project lifecycle API contract expectations, so needs a decision, not
   just a UI tweak.

## Open questions for the team / user
- Should "Activate" move into `SettingsPanel`, or should the header stay
  the only lifecycle control and the Progress-card link just point at the
  header instead of Settings? (Affects (A).)
- Is (E) (merging Activate + Run for one-offs) desired, or is the
  two-step gate intentional (e.g., to let a user activate now and run
  later)?
