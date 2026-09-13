# Implementation status: blocked card shows no reason/next step

Companion to `plans/2026-09-13-blocked-card-missing-reason.md`.

| # | Description | Status | PR / commit | Notes |
|---|-------------|--------|--------------|-------|
| 1 | `hermes_cli/projects_api.py::get_card()` — attach `latest_summary` (was silently dropped) | **Done** | branch `fix/card-detail-blocked-reason` | Regression test added in `tests/hermes_cli/test_projects_api.py` |
| 2 | `agent-home/src/types/index.ts` — type `block_kind` on `ProjectBoardTask` | **Done** | same branch | Field was already in the API payload, just untyped |
| 3 | `agent-home/src/components/projects/CardDetailView.tsx` — dedicated blocked-reason banner + next-step hint | **Done** | same branch | Tests added in `ProjectDetailView.test.tsx` |

All three verified: `pytest tests/hermes_cli/test_projects_api.py
tests/hermes_cli/test_kanban_db.py` (260 passed), `npm run typecheck`
(agent-home), `npx vitest run` (617 passed; 14 pre-existing unrelated
failures elsewhere).

## How to update this file

When the PR merges to `develop`, fill in the PR link and flip nothing else
— this bug is fully closed by the three rows above.
