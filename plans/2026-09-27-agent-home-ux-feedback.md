# agent-home UX feedback + lint baseline cleanup

**Status: implementation complete.** UX work in PR #475 (`agent-home-ux-feedback`,
commit `da9bf79ee`); lint cleanup on a separate branch off `develop`.

## Goal

From the user:

- "Improve the UI so the user can see feedback more quickly, instead of waiting
  for a while."
- "Do not allow the user to click more than once; status needs to be updated
  automatically, especially on Projects."
- Follow-up: "clean up" the pre-existing broken ESLint baseline.

## Implemented (PR #475)

1. `/projects` list auto-refresh: every 30s while visible + on window focus /
   tab return; paused past page 1 so pagination/scroll isn't disturbed.
2. `useProjectEvents` throttles `router.refresh()` to once per 2s with a
   trailing call, so event bursts can't fan out five upstream fetches per frame.
3. Chat send failure restores the typed draft and attachments into the composer
   (retry instead of retype).
4. Open-but-idle chats poll `/api/chat/active?sessionId=…` and attach to
   externally started turns (Telegram, scheduled jobs, other devices) via the
   existing `resumeTurn` path.
5. `OutputsPanel` Accept/Remove stay locked through `POST` + `router.refresh()`
   — the `pending ?? (refreshing ? last : null)` pattern already used by
   `CardActions`/`RunsPanel`/`BoardPanel`.
6. Board card moves are optimistic (instant column change, rollback on server
   rejection); newly created cards appear when the POST returns.

## Lint baseline cleanup

Baseline was broken at `develop` HEAD (16 errors / 5 warnings, all pre-existing,
none introduced by #475). Fixes applied:

- `set-state-in-effect` (React Compiler lint): mount-time data loads wrapped in
  a `setTimeout(0)` deferral (`ConnectedAccounts`, `WhatsAppBridges`,
  `ModelPickerSheet`, `PushEnroll`, `ChatPane` tag effects, `SettingsView`
  `loadTags`). `LeadChatHost` instead derives `loading` from a `loadedFor`
  marker. `SessionTabs` category-follow converted to the documented
  render-time adjustment pattern (prev-value tracking state).
- Render-time mutation: `CoralHost` stagger delays and `ReadinessChecklist`
  step numbers now come from precomputed `Map`s instead of counters mutated
  inside `.map` callbacks.
- `SettingsView`: `loadTags` declaration moved above the effect (was accessed
  before declaration).
- `tags/route.test.ts`: `req as any` → `req as NextRequest`.
- `eslint.config.mjs`: anonymous default export assigned to `config`.
- Remaining `exhaustive-deps` warnings resolved with the codebase's existing
  `eslint-disable-next-line` convention where deps are intentionally narrow.

## Verification

- `npm run lint` (agent-home): **0 errors, 0 warnings** (was 16/5 at HEAD).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 912 passed / 4 skipped on `develop`; 917 / 4 on the UX
  branch (5 new tests cover board helpers and draft restore).

## Not in scope

- Deploying the UX build (follows normal PR → merge → deploy).
- Any redesign of surfaces outside chats/projects.
