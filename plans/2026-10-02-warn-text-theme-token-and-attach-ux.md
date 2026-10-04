# Theme-aware warn text + unlinked-files attach UX

Status: **implemented, PR pending** — 2026-10-02

## Request

On the Projects Outputs tab's "unattached files" warning (light theme),
the amber text was invisible, and the attach controls were confusing:
every row showed an identical output select + a long "Attach to …" button,
with no way to attach a whole run's files at once.

## Root cause: invisible amber

`text-amber-200/300/400/500` (and `text-yellow-300`) are hardcoded,
dark-theme-tuned Tailwind colors — near-white yellow on light surfaces.
33 non-test sites had the same bug class (`UnattachedWarning`, `Pill`
warning tone, `CardTile`, `StatusStrip`, `ScopeTab`, `RunView`, `NeedsYou`,
dashboard cards, settings rows…), not just the reported banner.

## Changes

### `agent-home/src/app/globals.css`

- New per-theme tokens: `--color-warn` (amber accent/borders) and
  `--color-warn-text` (readable warning text): `amber-500/amber-300` on
  dark + colourful, `amber-700` on light (~5.9:1 on white).

### Text-color sweep (22 files)

- `text-amber-*` / `text-yellow-*` → `text-[var(--color-warn-text)]`
  everywhere it was used as warning text. Alpha-blended amber backgrounds
  and borders stay (visible in every theme).

### `UnattachedWarning.tsx`

- Warn tokens throughout; header copy now says what the controls do:
  "Pick the output each file delivers, then Attach — or attach them all
  to the same output at once."
- New `AttachAll` row per run group (files > 1): one target select + one
  "Attach all" button that delivers every file in the group sequentially
  with `Attaching n/N…` progress; stops on the first failure so the
  remaining files stay attachable by hand.
- Per-row button simplified: `Attach` when the target picker is visible
  (the select already names the output); keeps the titled
  `Attach to "…"` form only when there is exactly one candidate output.

### Pre-existing breakage fixed on the way

- `dashboard/whereItStands.ts` renamed to `standProgress.ts`: it collided
  with `WhereItStands.tsx` on case-insensitive filesystems — tsc resolved
  `@/…/WhereItStands` to the wrong module and failed at HEAD.
- `useStickToBottom.test.tsx`: moved the module-level `api`/`harnessRenders`
  writes into an effect — the react-hooks compiler flagged render-time
  reassignment, breaking the lint baseline at HEAD.

## Verification

- `npx tsc --noEmit` — clean (was failing at HEAD on the casing collision)
- `npm run lint` — clean (was failing at HEAD on the test file)
- `npx vitest run` — 1665 passed / 4 skipped
