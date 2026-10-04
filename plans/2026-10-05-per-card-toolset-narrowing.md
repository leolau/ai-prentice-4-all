# Plan: per-card toolset narrowing + card tool visibility

Status: **shipped** (PR #502, deployed `039264de1`; prod `tasks.toolsets`
column migrated — existing cards keep full surface, new stamped cards
get the narrowed list).

## Problem

Every kanban card worker spawns with the assignee profile's *entire* CLI
tool surface (~30 toolsets, ~80–114k prompt tokens per model call),
regardless of what the card actually needs. Found in production on the
`convert-pdf-to-docx-and-upload` run: a "verify fidelity" card had spent
1.28M cached tokens over 16 calls carrying Figma, Canva, AWS, TTS and
friends it would never touch. Per §4.1 the narrowing machinery
(`resolve_toolsets`: project ∩ profile, drops recorded) already existed
for `mode: "inline"` steps — it just never reached cards.

## Change

- `tasks.toolsets` (TEXT, JSON array) — additive column via
  `_add_column_if_missing`, same pattern as `model_override`. `Task`
  parses it (malformed → None). `create_task(toolsets=…)` normalises:
  strip, dedupe preserving order, reject comma-joined names.
- `projects_run.resolve_card_toolsets(requested, enabled)` — the stamp
  decision: empty request → `None` (full surface, unchanged); else
  `enabled ∩ requested ∪ CARD_TOOLSET_FLOOR(∩ enabled)` where the floor
  is `kanban, file, terminal, clarify, todo` — a worker must be able to
  touch its workspace, manage its todo, ask for clarification, and run
  its lifecycle tools. Drops are returned for recording.
- `start_run` resolves per step: `step.toolsets or project.toolsets`;
  per-step drops land on the run prelude ("Step 'x' toolsets not enabled
  …"). `instantiate_run_cards(card_toolsets=…)` stamps each card at
  creation so the dispatcher stays dumb and the decision is inspectable.
- `_default_spawn` prefers `task.toolsets`; `None` falls back to the
  profile-resolution path exactly as before.
- `projects_db.validate_playbook_steps` accepts `toolsets` per step —
  must be an array of non-empty comma-free names; unknown *names* are
  not rejected (a plan may legitimately name sets only some profiles
  enable — drops are recorded at run start, §4.1).
- Plan drafting: the prompt names the host profile's enabled catalog and
  asks for a minimal `toolsets` per step; `_normalise_draft_steps`
  filters draft output to that catalog so saved plans stay clean.
- UI: `CardDetailView` shows `worker tools: <list>` under the meta row,
  or `worker tools: full profile surface` for unstamped cards.
  `ProjectBoardTask.toolsets` + `PlaybookStep.toolsets` typed.

## Tests

- `test_projects_run.py` — resolution contract (empty/intersect/floor/
  floor-∩-enabled), save-time shape validation, step>project stamping
  with per-step drop reporting, project fallback, `create_task`
  roundtrip + comma rejection.
- `test_kanban_worker_spawn_toolsets.py` — argv uses the stamped list;
  unstamped falls back to full profile surface.
- `ProjectDetailView.test.tsx` — stamped card names its sets; unstamped
  says "full profile surface".
- Migration verified both directions (fresh schema + legacy drop-column
  reconnect → column restored, old rows read NULL).

## Incidental fixes (pre-existing suite breakage found while running)

- `test_kanban_cli_dispatch_passthrough.py`,
  `test_kanban_default_assignee.py`, `test_kanban_per_profile_cap.py` —
  their `isolated_kanban_home` fixtures purged `hermes_cli`/`hermes_state`/
  `hermes_constants` from `sys.modules` without restoring. Re-imported
  `hermes_constants` cached the fixture's `HERMES_HOME`, so every later
  test's `connect()` opened the same leaked kanban DB — reclaim/hook
  tests downstream saw other tests' stale claims (masked suite
  ordering). Fixtures now snapshot and restore `sys.modules`.
- `test_projects_run_seams_defaults.py` — two `SimpleNamespace` projects
  lacked `id` (broken by the `run_activity` publishing added in #499);
  added `id="p_monday"`.
- Still failing on clean `develop` (out of scope):
  `test_signal_handler_kanban_worker.py::test_sigterm_with_kanban_task_env_terminates_quickly`
  — subprocess stays alive >2s after SIGTERM; timing/environment, not
  related to this change.
