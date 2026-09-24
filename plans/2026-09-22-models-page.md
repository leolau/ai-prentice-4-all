# Models page (agent-home, System section)

**Status:** Implemented (PR #419) — pending review
**Companion mockup:** `plans/2026-09-22-models-page-mockup.html`
**Date:** 2026-09-22

## Goal

A simple, mobile-first page under the **System** cluster of the Coral
launcher that answers two questions at a glance:

1. **Which models are in use right now** — the main brain and every
   specialized role (vision, compression, …), including which roles just
   inherit the default.
2. **Let the user change a slot's model** without touching `config.yaml`.

3. **Let the user connect / disconnect a provider account** (e.g. add an
   OpenCode-Go API key) without leaving the page.

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
| Full provider management (OAuth flows, env editor) | Key-based providers only: "Add provider key" + "Disconnect provider" in the picker sheet. OAuth providers stay in onboarding/settings. |
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

- **Row tap** opens a bottom sheet: provider dropdown + model list for
  the selected provider + "Set" / "Reset to auto" (aux slots only).
- **Provider dropdown lists every catalog provider**, not just
  authenticated ones:
  - *Authenticated* provider → model list renders immediately.
  - *Unauthenticated, key-based* provider (e.g. `opencode-go`) → sheet
    shows an "Add API key" field: paste → `POST /api/providers/validate`
    → `PUT /api/env` → model list appears inline, no page leave.
  - *OAuth-only* provider → "Set up in onboarding" hint link instead of a
    key field (OAuth flows stay out of scope).
- **Disconnect provider** — a small action at the bottom of the picker
  sheet for authenticated providers: confirm sheet → `DELETE /api/env`
  for its key var. Warns when slots are currently pinned to that provider
  (they'll fall back to `auto`/fail) — the "In use" list makes the blast
  radius visible before confirming.
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
| `POST /api/providers/validate` | Live-probe a pasted provider key before saving |
| `PUT /api/env` | Persist a provider API key (e.g. `OPENCODE_GO_API_KEY`) to `.env` |
| `DELETE /api/env` | Remove a provider key — disconnects the provider |

Provider auth note: `opencode-go` is a **key-based** aggregator provider
(`hermes_cli/providers.py` — `openai_chat` transport, `OPENCODE_GO_API_KEY`
+ optional `OPENCODE_GO_BASE_URL`), not OAuth — so "add an account" for it
is just validate + `PUT /api/env`. OAuth-capable providers (anthropic,
nous, openai-codex, xai…) keep their dedicated flows in onboarding.

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
   check), `src/app/api/models/provider-key/route.ts` (POST →
   `/api/providers/validate` then `PUT /api/env`; DELETE → `DELETE
   /api/env`). Follows the `chat/sessions/route.ts` pattern exactly.
   Secrets pass straight through to the Python API — never logged,
   never returned to the client.
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

- MoA editing, OAuth provider flows (anthropic PKCE, nous/codex device
  code, xai loopback — key-based providers only get add/disconnect here),
  per-card `model_override` editing (kanban cards keep doing that on the
  card; the page may later *display* pinned cards — see risks), cost
  budgets, hot-swapping a running session's model.

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

Implemented (PR #419) — pending review.

- `coral-apps.ts` — `/models` registered in the System cluster (8 members, at cap).
- `src/lib/api/client.ts` — `modelInfo` / `modelOptions` / `auxiliaryModels` /
  `setModelAssignment` / `modelsAnalytics` / `validateProviderKey` /
  `setEnvVar` / `deleteEnvVar`; matching response types in `src/types/index.ts`.
- BFF routes — `GET /api/models/overview` (fan-out: info + auxiliary +
  30-day analytics, analytics failure-tolerant), `GET /api/models/options`,
  `POST /api/models/set` (replays `confirm_required` / `stale_aux`), and
  `POST|DELETE /api/models/provider-key` (validate-then-save; a
  confirmed-bad key is refused, an unreachable probe still saves and marks
  `verified:false`; env-var name validated, value never echoed).
- Page — `src/app/models/page.tsx` (RSC + `MobileShell`) renders
  `ModelsView` (main-model card with capabilities, task-role list with
  `auto → main` rows and a "+N more roles" expander, "In use — last 30
  days" sorted by cost with per-slot tags including `not configured`) and
  `ModelPickerSheet` (all catalog providers in the dropdown, inline key
  entry for unauthenticated `api_key` providers, onboarding pointer for
  OAuth providers, `confirm_expensive_model` inline confirm, `Reset to
  auto` for aux slots, `Disconnect provider` with blast-radius confirm).
- Tests — `ModelsView.test.tsx` (SSR render), `ModelPickerSheet.interaction.test.tsx`
  (jsdom handlers: provider list, key-entry swap, save-then-refetch,
  confirm-expensive resend, aux reset), `api/models/set/route.test.ts`,
  `api/models/provider-key/route.test.ts`.
- Verified — `tsc --noEmit` clean, `vitest` 819 passed, `next build` clean
  (`/models` 6.44 kB). jsdom tests require Node ≥20.19 (repo toolchain:
  `~/.nvm/versions/node/v20.19.6`; Node 20.13.1 breaks `html-encoding-sniffer`
  and skips the rolldown arm64 binding).
