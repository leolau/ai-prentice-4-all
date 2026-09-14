# Status — card detail live progress + reasoning

**State: implemented, tested locally, not yet committed/deployed.**

## Done

- Backend: `hermes_cli/projects_api.py::get_card()` now returns `comments`
  and `latest_heartbeat`.
- Backend tests: `tests/hermes_cli/test_projects_api.py` —
  `test_card_detail_surfaces_live_progress_while_running` and
  `test_card_detail_has_no_heartbeat_or_comments_when_there_are_none`.
  Verified via `scripts/run_tests.sh` (isolated per-file runner):
  `tests/hermes_cli/test_projects_api.py` 36/36,
  `tests/hermes_cli/test_projects_run.py` 41/41,
  `tests/hermes_cli/test_projects_api_runs.py` 18/18 — all passing.
- Frontend: new `useCardLive.ts` (+ `useCardLive.test.ts`, 5/5 passing under
  `vitest run`), `CardDetailView.tsx` converted to a client component with
  the "What's happening" + "Updates" panels, `types/index.ts` extended.
- `npx tsc --noEmit` — clean, no type errors.
- Non-DOM vitest suite (`useCardLive`, `useRunLive`,
  `client.projects.test.ts`, `write-envelopes.test.ts`) — 23/23 passing.

## Known gap

- No `CardDetailView.test.tsx` (DOM-rendering test) was added. Confirmed
  reproducible independent of this change: `vitest run
  src/components/projects/RunView.test.tsx` fails in this environment with
  `ERR_REQUIRE_ESM` (`html-encoding-sniffer` requiring
  `@exodus/bytes/encoding-lite.js`) before any test body runs — a
  pre-existing jsdom/vitest dependency incompatibility, not something this
  change introduced. Whoever fixes that environment issue should also add a
  render test asserting: the "What's happening" panel only shows while
  `running`, it shows the heartbeat note when present and a placeholder when
  not, and the "Updates" panel lists comments newest-first.

## Next steps

1. Commit on a fresh branch off `develop`, push, open a PR (small, additive,
   no production deploy needed to merge).
2. Once merged, deploy to pick it up live so the reported card page actually
   shows progress on the next `running` card someone opens.
