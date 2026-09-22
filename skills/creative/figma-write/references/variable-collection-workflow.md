# Variable Collection (Design Token) Workflow

Established Sept 16, 2026 building the AI&I design-system token file
(E16xDVa48xfndpQfciNmEo): 12 collections, 139 variables, Light+Dark semantic
modes, full Component → Semantic → Primitive alias chains.

## Key finding: variables are NOT bound by the 1-write-per-call rule

The "one write operation per call" constraint applies to **vector/canvas
elements**. Variable creation is metadata-only and batches fine:

- 26 primitives (Primitives/Color) in ONE call: ~6-10 min, success.
- 16 vars × 2 modes with 32 aliases (Semantic/Color) in ONE call: success.
- 4 independent collections (Component/Button, Input, Card, Navigation) run
  IN PARALLEL as 4 background calls: all succeeded, no collisions.

Parallelization rule: independent **collections** can run in parallel even in
the same file. Same-collection writes must be sequential (one call per
collection).

## Prompt pattern that works

One `figma_write.sh --timeout 1800` call per collection. The prompt should
contain:
1. File URL.
2. Exact collection name (e.g. `Semantic / Color` — note spaces around `/`;
   be consistent or you get near-duplicate names).
3. Full variable list with exact names and raw values (or alias targets by
   name, e.g. "color/primary → alias to brand/500 in Primitives / Color").
4. Mode requirements ("create two modes named Light and Dark; set every
   variable's value in both modes").
5. An explicit instruction: "After creating, read back every variable and
   report name, resolved value per mode, and alias target — verify nothing
   was skipped."

The read-back verification in the same call catches silent skips (the bridge
agent occasionally drops a variable from a long list).

## Alias chains

- Semantic vars alias Primitives by variable; Component vars alias Semantic.
- Cross-collection mode resolution works automatically: a Component collection
  with a single Default mode still resolves Semantic/Color's Light/Dark values
  from the consuming frame's explicit mode.
- `setValueForMode(modeId, {type:'VARIABLE_ALIAS', id: targetVar.id})` is
  idempotent — safe to re-apply when audit data conflicts with file state.

## Verification (the part that's tricky)

Three tiers, in order of reliability:

1. **Structural enumeration** (cheap): ask the bridge for
   `getLocalVariableCollectionsAsync()` output — collection names, mode
   names/count, variable counts. Catches missing/renamed collections.
   NOTE: an earlier audit run reported `variable.valueByMode` "not readable"
   while a later run in the same file read it fine — treat a BLOCKED report
   from one run as possibly transient, re-probe before trusting it.

2. **REST API**: `GET /v1/files/{key}/variables/local` requires a PAT with the
   `variables` scope. The Hermes FIGMA_API_KEY does NOT have it (returns
   403 scope error). Don't waste a call on it unless the token is upgraded.

3. **End-to-end resolution probe** (gold standard): have the bridge create a
   temp frame + rectangle, bind the component token to the fill, set the
   frame's explicit mode via `setExplicitVariableModeForCollection`, read the
   computed fill hex, switch modes, re-read, then DELETE the temp frame and
   confirm the page is empty. This verifies the whole alias chain resolves to
   the right raw value in each mode. ~5-8 min per probe call; batch several
   bindings into one probe prompt.

## Pitfalls

- **Stale/conflicting audit reports.** One run reported `card/bg → color/bg`;
  a re-probe showed the stored alias was actually `→ color/surface`. When two
  bridge runs disagree about file state, trust the runtime node-binding probe
  (tier 3), not static reads (tier 1).
- **Expectation drift.** Write expected values from the ACTUAL palette of the
  file (this system: teal brand #2E6B64, warm neutrals #FAF8F5…#1A1612), not
  from another design system's conventions, or you'll chase phantom failures.
- **Probe cleanup.** Always instruct "DELETE the temp frame and confirm the
  page has 0 children" — a forgotten PROBE frame pollutes a token-only file.
- **Collection name spacing.** Use one exact spelling everywhere in prompts
  (`Primitives / Color`, not `Primitives/Color`) — mismatched names create
  duplicate collections that are annoying to merge.
