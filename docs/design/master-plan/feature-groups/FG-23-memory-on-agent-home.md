# FG-23 — the memory visualizer on `agent-home` (the phone), not the dashboard

**Status:** plan, ready for another agent to pick up cold.
**Depends on:** FG-20 (`agent-home` BFF), FG-21 (layer-4 semantic memory),
FG-22 (the visualizer and its `/api/memory/explorer/*` endpoints, shipped and
live).
**Written after surveying the live `hermes-systest` box**, not from the repo
alone — several of the facts below are only true of the deployment and are the
reason this is phased the way it is.

---

## 1. Why this exists

FG-22 put the memory visualizer in `web/` — the desktop operator console at
`https://leolau.ai-and-i.io/memory`. The request is to have it on **`agent-home`**,
the mobile-first face at `https://home.leolau.ai-and-i.io` (FG-20), which is
what the owner actually opens.

Nothing about the data layer changes. The endpoints FG-22 shipped are already
the right seam:

```
GET  /api/memory/explorer/summary            counts, space health, recall use
GET  /api/memory/explorer/rows               paginated + semantic search (q, owner, topic, kind)
GET  /api/memory/explorer/projection         the fitted 2-D map
POST /api/memory/explorer/projection/query   place a typed query, never persisted
GET  /api/memory/explorer/documents          RAG documents (empty until ingestion)
```

They are principal-scoped, RLS-bound, and audit every elevated read. **This
plan adds a second *view*; it must not add a second *path to the data*.**

## 2. What is actually true of `agent-home` on the box (verified 2026-08-05)

This is the part that changes the plan's shape, and it is not visible from the
repository:

```
url            https://home.leolau.ai-and-i.io      (Caddy → 127.0.0.1:3100)
unit           agent-home.service                   active since 2026-07-29, User=root
checkout       /opt/data/agent-home-app/agent-home  ← a SECOND checkout
               at db68f7554 (PR #62), while the app runs from ffabb3b97
build          .next/BUILD_ID dated 2026-07-27
env            agent-home.env: AGENT_HOME_API_URL=127.0.0.1:9119,
               AGENT_HOME_DATASTORE_MODE=prod, DATABASE_URL user=agent_home_app
               (NOBYPASSRLS, provisioned by `hermes owner read-role`)
```

Four findings follow from that, and three of them are blockers:

1. **`agent-home` is not in the deployment path.** `/opt/data/deploy-hermes.sh`
   never mentions it: it pulls and reinstalls `/opt/data/hermes-agent` only. A
   Memory page merged to `develop` would appear on the *dashboard* and never on
   the phone. Whoever picks this up must not assume "merge = deployed" here.
2. **It runs from a different checkout than the application.** Today the two
   `agent-home/src` trees happen to be byte-identical (nothing has touched
   `agent-home/` since #62), so there is no content drift *yet* — but the
   arrangement guarantees it the moment this feature lands.
3. **`deploy_state.py` cannot see it.** The capture's unit glob is `hermes-*`,
   so `agent-home.service` is not in the state manifest and the weekly drift
   check is structurally blind to it. The installed unit already differs from
   the reviewed copy in git (comments and `Documentation=` only — verified — but
   nothing would have told us if it were more).
4. **It runs as `root`**, alone among everything on the box. That is what the
   reviewed unit in `agent-home/deploy/` says, so it is a deliberate FG-20
   choice, not drift — but a public-facing Node server that renders another
   person's memories is the worst place on this box to keep that property.

**Phase A0 below is therefore not optional preparation — it is the feature.**
Without it "move the visualizer to agent-home" cannot be delivered, only
committed.

## 3. Decisions

### D1 — Move, or mirror? **Mirror the user view; demote the operator one.**

The endpoints are the single source of truth, so a second view costs nothing at
the data layer. But the two audiences want different things:

| | phone (`agent-home`) | operator console (`web/`) |
|---|---|---|
| "what does it remember about me" | yes | incidental |
| the map, query placement | yes | yes |
| `vector(1024)` / `rows_by_model` / mixed-model staleness | no | **yes** — this is how you catch a half-re-embedded corpus |
| per-owner counts across people | only what the principal may see | same, but it is the operator's job |

So: build the phone view (§4), and **remove `/memory` from `web/`'s primary
nav while keeping the route and the page**. Deep links and the diagnostics
survive; the phone becomes the place you go to look at your memory. Deleting
`web/src/screens/MemoryPage.tsx` outright is the alternative — it is 795 tested
lines and it is the only surface that shows embedding-space health, so the plan
does not recommend it. This is a one-line nav change and trivially reversible;
if the owner wants the page gone, delete it in a follow-up.

### D2 — Reads go through the Python API. Never `pg`.

`agent-home` *can* read Postgres directly under `agent_home_app` (NOBYPASSRLS,
FORCE'd RLS), and for GTS it does. **Memory must not use that path.** RLS would
still scope the rows correctly, but the elevated-read *audit* — the ledger row
written in the same transaction as a cross-person read, FG-21 P3 — lives in the
Python store. A direct `SELECT` would be correctly scoped and silently
unaudited, which is precisely the failure FG-22's review fixed.

Enforce it with a test that greps the memory modules for `withPrincipalContext`
/ `scopedSelect` imports and fails if present, so the shortcut cannot be taken
later by someone optimising a page load.

### D3 — Do **not** pass `mode`.

`agent-home` sets `AGENT_HOME_DATASTORE_MODE=prod` and `client.tools()` forwards
it. The memory endpoints must **omit** it and let the Python layer resolve its
own configured mode, because on this box that resolves to `dev`:

```
app_dev.memories    37 rows, vector(1024), BAAI/bge-m3   ← the live memory tier
app_prod.memories    0 rows, vector(256)                 ← pre-re-embed leftover
```

Passing `prod` would render a perfectly healthy page reporting zero memories —
the same misleading failure #107 fixed in the dashboard. Identity is the
exception and already handled upstream: `principals` / `principal_aliases` live
in `app_prod` regardless of mode, because channels and web share one identity
space.

### D4 — No charting dependency. Inline SVG.

`web/` uses `@observablehq/plot` (already a dependency there). `agent-home` has
no charting package and should not gain one: Plot is an imperative DOM renderer
that ships hundreds of KB into a PWA whose whole point is a phone on mobile
data. A scatter plot is `<circle>` elements and a linear scale. Budget: SVG up
to ~2,000 points, `<canvas>` above that (see §5 on sampling).

### D5 — Identity: nothing to build.

`agent-home`'s login already bridges to the Python `dashboard_auth` provider,
replays the `hermes_session_at` cookie, and resolves the principal via
`/api/comms/whoami` — returning its own honest `409 no_principal` if the login
subject maps to nobody. That is the same 409 the dashboard produced on
2026-08-04; it is fixed for both surfaces by the alias that now exists
(`hermes owner alias admin` → `leo_owner`). No code change, and **no new
fallback**: a login that maps to no principal must keep failing loudly.

## 4. What to build

### Phase A0 — put `agent-home` in the deployment path (blocker)

Ordered, and each step is verifiable on the box:

1. Decide the checkout. **Recommended: retire `/opt/data/agent-home-app` and
   run from `/opt/data/hermes-agent/agent-home`**, so one `git pull` moves the
   whole system and the existing drift check covers the source. The unit's
   `WorkingDirectory`, `EnvironmentFile` and `ExecStart` move with it; the
   `agent-home.env` file (0600, secrets) is copied, not regenerated.
2. Teach `deploy/hermes-deploy.sh` to rebuild and restart it — mirroring the
   dashboard-bundle rule #107 added, i.e. only when `agent-home/` changed
   between `$BEFORE` and `$AFTER`, or when `.next/BUILD_ID` is absent:
   `npm ci --workspace agent-home && npm run --prefix agent-home build`,
   then `systemctl restart agent-home`. `nice -n 15`: a Next build on 4 shared
   vCPUs must lose to the gateway.
3. Widen the state capture to see it: `--unit-glob 'hermes-*'` →
   `--unit-glob 'hermes-*,agent-home*'` (the argument is single-valued today;
   make it repeatable rather than a comma-split, and default to both). Capture,
   review the diff, commit the state repo.
4. Install the reviewed `agent-home/deploy/agent-home.service` so installed ==
   git, then re-check drift; it should go clean, not quiet.
5. Separately (own PR, own review): move the service off `root` to `hermes`,
   with the same hardening stanza the `hermes-*` units carry
   (`ProtectSystem=strict`, `ReadWritePaths=` the build dir + env file,
   `NoNewPrivileges`). Needs the build dir and `agent-home.env` re-owned. Do
   not bundle this with the feature — if it breaks the phone, the cause should
   be unambiguous.

**Acceptance:** a commit to `develop` that changes only `agent-home/src` is
visible at `https://home.leolau.ai-and-i.io` after one deploy run, with no
manual build, and `deploy_state.py check` clean afterwards.

### Phase A1 — the API client seam

`agent-home/src/lib/api/client.ts`, following the existing typed-method style:

```ts
async memorySummary(): Promise<MemorySummary>
async memoryRows(opts: { q?: string; topic?: string; kind?: string;
                         limit?: number; offset?: number }): Promise<MemoryRowsResponse>
async memoryProjection(): Promise<MemoryProjection>
async memoryQuery(text: string): Promise<MemoryQueryPlacement>
async memoryDocuments(): Promise<MemoryDocuments>
```

No `mode` parameter on any of them (D3) — and add a comment saying why, or
someone will "fix" the inconsistency with `tools()`.

Types go in `src/types/index.ts`, mirrored from `hermes_cli/memory_explorer.py`
**exactly**; the response shapes are already typed in
`web/src/screens/MemoryPage.tsx:16-100` and can be lifted verbatim. Tests:
`client.memory.test.ts` in the style of `client.gts.test.ts` — assert the
forwarded path/query, that the bridged cookie *and* bearer are sent, and that
`mode` never appears in the URL.

### Phase A2 — `/memory`: counts + rows (ships alone)

- `src/app/memory/page.tsx` — RSC: `await requirePrincipal()`, then
  `apiClientForRequest()` → `memorySummary()` + first page of `memoryRows()`,
  `export const dynamic = "force-dynamic"`, rendered in `<MobileShell title="Memory">`.
  Follow `src/app/graph/page.tsx` verbatim, including its error branch: a failed
  fetch renders a sentence, never a stack.
- `src/components/memory/MemoryView.tsx` — client component. Phone layout:
  summary pills (total, never recalled, documents) → search field → rows as
  cards (text, topic, `uses`, relative `last_used`), infinite "load more" on
  `offset`. A row the principal sees by role carries its
  `from <owner>'s memory` provenance label — FG-21's rule that another person's
  fact is never presented as the reader's own applies to every surface.
- Nav: add `{ href: "/memory", label: "Memory", glyph: "◇" }` to
  `PRIMARY_NAV` in `src/components/nav-items.ts`. Five primary tabs is already
  the bottom-bar budget on a phone — **make Memory primary and move
  `Activity` to `SECONDARY_NAV`** (traces are an operator's tool; memory is the
  user's). `nav-items.test.ts` asserts the split, so update it deliberately.
- Service worker: `public/sw.js` already refuses to cache `/api/*` and
  `/auth/*`; the page itself is per-principal HTML, so confirm it is not
  precached either.

**Acceptance:** on a phone, logged in as the owner, the page lists the real
memories with search working, and a `curl` of the page without a session
redirects to `/login`.

### Phase A3 — the map

- `src/components/memory/MemoryMap.tsx` — client component, `memoryProjection()`
  fetched *after* first paint (the map is the slow part; counts must not wait
  for it). Inline `<svg viewBox>` with a linear scale over the returned extent,
  one `<circle r=3>` per point, colour by `topic` (stable hash → palette),
  opacity by recency. Tap a dot → bottom sheet with the hover label, owner and
  provenance. Pinch-zoom via a `viewBox` transform, not CSS scale, so dots stay
  crisp and hit-targets stay correct.
- Staleness must be legible, and the two causes are different: FG-22 returns
  `unprojected_count` (N memories written since the fit) and sets `stale` for a
  model mismatch. Render them as different sentences — "3 new memories aren't on
  the map yet" vs "this map was fitted with a different embedder" — because the
  second is a correctness warning and the first is just a nightly job pending.
  The fit itself is `hermes-memory-projection.timer` (nightly 03:00) and must
  never be triggered from a request.

### Phase A4 — query placement

A text field posting to `memoryQuery()`, drawing the query as a distinct marker
(hollow ring, labelled) plus a nearest-memories list under the map. Two things
to state in the UI, because both are true and both surprise people: the query
is **never stored**, and a UMAP-fitted basis may return neighbours *without* a
position (FG-22 degrades rather than drawing a position it cannot justify) — in
that case show the list and say the map has no place for it.

### Phase A5 — demote `web/`'s nav entry (D1)

Remove the `/memory` item from `BUILTIN_NAV_REST` in `web/src/App.tsx`; leave
`"/memory": MemoryPage` in `BUILTIN_ROUTES_CORE`. One line, plus the locale
keys stay (they are still used by the page header).

## 5. The scaling problem, and the API change it needs

Today `/projection` returns **every** point the principal may see — 37 rows, so
nobody noticed. After Drive ingestion that is tens of thousands of chunks, and
the endpoint would ship megabytes of JSON to a phone.

FG-22 left this open; this plan closes it, because a phone is where it bites
first:

- add `limit` (default 5,000) and a **deterministic** sample to `/projection` —
  `ORDER BY hashtext(id::text)`, not `random()`, so panning does not reshuffle
  the map;
- return `sampled: true` and the true total, and say so on the page ("showing
  5,000 of 41,880") — an unlabelled sample is a lie about density;
- keep the nearest-neighbour list in `/projection/query` exact (it runs in the
  database against all rows), so search quality never depends on what was
  sampled for drawing.

Do this **in Phase A3, before Drive ingestion**, not after.

## 6. Testing

Mirror what `agent-home` already does — `vitest`, in `agent-home/test/` and
alongside components:

- `client.memory.test.ts` — forwarded path/query/auth headers; no `mode`.
- `MemoryView.test.tsx` — renders rows; renders the provenance label for an
  elevated row; renders the two staleness sentences distinctly; renders the
  empty state without crashing (a fresh box has no projection at all:
  `algorithm: null, points: []`).
- `MemoryMap.test.tsx` — scales an extent to the viewBox; a single point does
  not divide by zero (FG-22 hit exactly this in PCA); sampled banner appears
  when `sampled`.
- `memory-no-direct-db.test.ts` — the D2 guard.
- Python side: extend `tests/hermes_cli/test_memory_explorer_*.py` for the new
  `limit`/`sampled` contract, including that the sample is stable across two
  calls and that `total` is the unsampled count.

No new Postgres integration tests are needed — authorization is unchanged and
already covered under a role that cannot bypass RLS.

## 7. What this plan deliberately does not do

- **No write path.** No editing, deleting, re-embedding or forgetting from the
  phone. A one-tap "forget this" on a device in a pocket is not a feature.
- **No UMAP fitting from the UI.** Minutes of CPU on 4 shared vCPUs; it is the
  timer's job.
- **No direct Supabase Realtime subscription** for memory rows. Live-updating
  the map means a per-row projection, which does not exist (a point only has a
  position relative to a fitted basis).
- **No `mode` switcher.** One deployment, one memory tier; a picker would just
  offer a way to look at the empty 256-dim `app_prod`.

## 8. Open decisions for the owner

1. **A0 checkout:** retire `/opt/data/agent-home-app` and run `agent-home` from
   the main checkout (recommended), or keep two checkouts and teach the deploy
   script to update both?
2. **`root` → `hermes`** for `agent-home.service`: do it as part of this work,
   or as its own change afterwards? (Recommended: afterwards, immediately.)
3. **`web/`'s Memory page:** demote from nav (recommended) or delete?
4. **Bottom-bar budget:** Memory promoted in place of Activity (recommended),
   or a sixth tab?
