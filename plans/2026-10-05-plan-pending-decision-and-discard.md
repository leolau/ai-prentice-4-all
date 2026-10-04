# Plan: pending plan revision = the only decision, plus Discard

Status: **shipped** (PR pending merge; deployed after merge).

## Problem

Once an agent draft (or a retro proposal) lands, it sits in "Proposed
revisions" awaiting a decision — but the panel still offered "Draft with
the agent" and "Write plan" as if nothing were pending. Starting another
draft on top just stacks undecided revisions, and there was **no way to
say no**: a wrong proposal could only be activated or left forever.

## Change

- `awaitingDecision = !editing && proposed.length > 0` — while a proposal
  waits, **Draft with the agent** and **Write plan/Revise** are disabled
  with a tooltip ("activate or discard it first"). The heading reads
  "awaiting a decision".
- Each proposed row gets **Discard** beside **Activate** (lead/admin,
  `data-action="discard-revision"`), through `useProjectAction` — its own
  lock, idempotency key, refresh on success, inline error on refusal.
- New backend: `DELETE /api/registry/projects/{slug}/playbook/{rev}` →
  `projects_db.discard_playbook_rev`. Same gates as activation
  (`_require_write` + `_require_human` + archived refusal). Refusals:
  404 not found, **409 active** (the live plan cannot be discarded), and
  **409 pinned:<run_no>** — a rev a run pinned is that run's recorded
  plan and stays for history even once superseded.
- BFF route `agent-home/src/app/api/projects/[slug]/playbook/[rev]/route.ts`
  + `HermesApiClient.discardProjectPlaybook`.

## Tests

- `test_projects_api_lifecycle.py` — discard removes a proposed rev
  (and 404s a second delete); 409 for the active rev; 409 pinned by a
  run; 403 for a session-less caller.
- `PlanDraft.interaction.test.tsx` — Draft/Write disabled while a
  proposal waits; Discard sends `DELETE …/playbook/2` with an
  idempotency key and refreshes; after refresh the doors reopen; a 409
  refusal surfaces inline and loses nothing.
- `ProjectDetailView.test.tsx` — static render: disabled buttons,
  Discard present for leads, absent for members.

Verified: `tsc --noEmit` clean, eslint clean, 60 focused tests + the
full projects/api suite (963) green, 73 backend lifecycle/api tests green.
