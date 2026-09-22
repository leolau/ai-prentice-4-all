# Models page (agent-home, System section)

**Status:** Proposed — awaiting review (plan + mockup only, no code yet)
**Companion mockup:** `plans/2026-09-22-models-page-mockup.html`
**Date:** 2026-09-22

## Goal

A simple, mobile-first page under the **System** cluster of the Coral
launcher that answers two questions at a glance:

1. **Which models are in use right now** — the main brain and every
   specialized role (vision, compression, …), including which roles just
   inherit the default.
2. **Let the user change a slot's model** without touching `config.yaml`.

It is deliberately a *simpler* version of the dashboard's Models Settings
screen (`web/src/screens/ModelsPage.tsx`, ~1300 lines): same backend, a
fraction of the surface.

## What the dashboard does today (reference)

- Per-model analytics cards: token bars (input/output/cache/reasoning),
  session counts, cost, capability badges, 7/30/90-day switcher.
- "Use as" menus to assign any model to any of 11 auxiliary task slots.
- Main-model picker with reload-confirm, expensive-model warning.
- MoA (Mixture-of-Agents) editor.
- Provider OAuth management lives elsewhere (settings/onboarding).

## Simplifications for agent-home

| Dashboard has | Models page does |
| --- | --- |
| Token-segmented bars per model | One "In use (30d)" list: model, sessions, tokens, cost |
| Period switcher 7/30/90d | Fixed 30d |
| "Use as" menu on every model card | One row per *role*; tap → picker sheet |
| MoA editor | **Out of scope** (advanced; stays on dashboard) |
| Provider auth / OAuth | **Out of scope** (stays in onboarding/settings) |
| Model reload confirm dialog | Inline note: "applies to new sessions" |
| Full capability badge set | Compact chips on the main-model card only |

## UI layout (see mockup HTML)

```
┌──────────────────────────────┐
│ Models                       │  ← MobileShell title
├──────────────────────────────┤
│ ◆ Main model                 │
│   glm-5.2                    │
│   alibaba · 1M context       │
│   [tools] [reasoning]        │
│   [ Change ]                 │
├──────────────────────────────┤
│ Task roles                   │
│   Vision        qwen3.8-max ›│
│   Compression   auto → main ›│
│   Web extract   auto → main ›│
│   Title gen     auto → main ›│
│   +7 more roles            ⌄ │
├──────────────────────────────┤
│ In use — last 30 days        │
│   glm-5.2     412 · 8.4M · $ │
│   qwen3.8-max  36 · 1.1M · $ │
│   deepseek      4 · 0.2M · $ │
└──────────────────────────────┘
```

- **Row tap** opens a bottom sheet: provider dropdown (only
  *authenticated* providers, from `/api/model/options`) + model list for
  that provider + "Set" / "Reset to auto" (aux slots only).
- **`auto → main`** rows mean the slot inherits the main model
  (provider `"auto"` in `auxiliary.*` config — matches backend semantics).
- **"In use"** is sorted by spend desc so the expensive models surface
  first; a slot chip (`main`, `vision`, …) marks which config each model
  currently serves, so "configured" and "actually used" cross-reference.

## Backend surface — all already exists (no Python changes)

| Endpoint | Used for |
| --- | --- |
| `GET /api/model/info` | Main-model card: provider, model, effective context length, capabilities |
| `GET /api/model/options` | Picker: authenticated providers + curated/live model lists |
| `GET /api/model/auxiliary` | Task-role rows (11 slots) + current `main` |
| `POST /api/model/set` | Writes `model.provider/default` or `auxiliary.<task>.*` to config.yaml |
| `GET /api/analytics/models?days=30` | "In use" list (tokens, cost, sessions per model) |

Known backend behavior to surface honestly in UI:

- `POST /api/model/set` applies to **new sessions only**; running
  kanban workers keep their model.
- It returns `stale_aux` when aux slots are pinned to a different
  provider than a newly-set main — surface as a nudge ("reset roles to
  follow main?") since stranded pins silently burn credits.
- `confirm_expensive_model` flag exists for pricey models — pass through
  a simple confirm in the sheet when the warning fires.

## Implementation outline (agent-home only + thin BFF)

1. **Registry** — `src/components/coral/coral-apps.ts`: `registerApp` for
   `/models`, `category: "system"`, order ~125 (between Tools and Core),
   glyph `◈`-style pick, hint "Brains + roles".
2. **API client** — `src/lib/api/client.ts`: add `modelInfo()`,
   `modelOptions()`, `auxiliaryModels()`, `setModelAssignment()`,
   `modelsAnalytics(days)` (typed wrappers over the Python endpoints,
   profile-scoped like existing methods).
3. **BFF routes** — `src/app/api/models/overview/route.ts` (GET: fans out
   to info + auxiliary + analytics in parallel server-side, one response),
   `src/app/api/models/options/route.ts` (GET passthrough),
   `src/app/api/models/set/route.ts` (POST passthrough with principal
   check). Follows the `chat/sessions/route.ts` pattern exactly.
4. **Page** — `src/app/models/page.tsx`: server component, `force-dynamic`,
   `requirePrincipal`, `apiClientForRequest`, error-card pattern identical
   to `tools/page.tsx`; hands data to a client component.
5. **Client component** — `src/components/models/ModelsPage.tsx`: three
   sections above; role rows open a picker sheet
   (`src/components/models/ModelPickerSheet.tsx`) modeled on the existing
   modal patterns (`SessionModal`, `ArchivedModal` styling).
6. **Tests** — vitest for the client component (render slots, auto→main
   display, picker open/set calls the BFF route); route tests for the BFF
   proxies mirroring `chat/sessions/route.test.ts`.

## Out of scope (explicit)

- MoA editing, provider OAuth/API-key management, per-card
  `model_override` editing (kanban cards keep doing that on the card),
  cost budgets, hot-swapping a running session's model.

## Risks / open questions

- **Profile scope**: production runs one profile (`default`); picker
  endpoints are profile-aware — pass the page's `?profile=` through like
  the chat page does.
- **`/api/model/options` can be slow** (live catalog fetch; 1h disk
  cache). Load it lazily only when the picker sheet opens, not at page
  load.
- **Card-level `model_override` is invisible to this page** — a running
  kanban card pinned to another model won't show here. The "In use" list
  will still show that model's usage, which may look odd; acceptable for
  v1, noted in the row's hint text if cheap to add later.

## Implementation status

Proposed — not started. Plan + mockup for review.
