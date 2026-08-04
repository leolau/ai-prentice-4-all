# Splitting core from user-created apps — analysis, not yet a decision

**Question (Leo, 2026-08-05):** separate the system into two repositories —
(i) `ai-prentice-4-all` as the core, which users don't change, and (ii) a second
one holding the UI and the local apps that users and the agent create and
modify. Was that the original plan, and what are the trade-offs?

**Status:** analysis. No decision, no code change. Measurements are from the
live box and from this fork's actual divergence, both dated 2026-08-05.

---

## 1. Two corrections first

**It was not the plan.** FG-20 Open-decision 4, owner-confirmed 2026-07-11, is
explicit: *"App location → `agent-home/` at repo root (sibling of `web/`)"* —
one repository holding the Python core, the operator console and the mobile app
together. Decision 3 kept `web/` alongside it for the same reason.

**And the two paths on the box are not two repositories.** They are the *same*
repository, cloned twice:

```
/opt/data/hermes-agent      ai-prentice-4-all @ ffabb3b97   ← Python core + web/ + agent-home/
/opt/data/agent-home-app    ai-prentice-4-all @ db68f7554   ← full clone, frozen at PR #62
```

`/opt/data/hermes-agent` **is** `ai-prentice-4-all`; it already contains all the
UI. The second clone exists only because `agent-home.service` was pointed into
it, and it is the subject of FG-23 phase A0. So there is no core/apps split in
place today — there is one codebase in two directories, which is strictly worse
than either design (see §5: it has already cost 10 days of frozen phone app).

## 2. Where the boundaries actually are

The fork has diverged from `NousResearch/hermes-agent` (merge base
`2f7c51a3e`) by **225 commits**, and the shape matters more than the size:

```
files:   400 added   76 modified   6 deleted   19 renamed
lines:   hermes_cli  +21,535 / -33      tests    +19,993 / -4
         agent-home   +9,781 / -0       docs      +7,948 / -0
         plugins      +4,512 / -0       web       +4,389 / -263
         scripts      +2,374 / -2       gateway   +1,027 / -78
```

**The work is ~96% additive**: across every core directory the fork deletes
about 165 lines of upstream code in total. But the additions *mount into*
upstream files at a small number of seams, and those seams are where a split
would actually bite:

```
hermes_cli/web_server.py   +1,713 / -18     ← every FG's API router mounts here
web/src/lib/api.ts           +606 / -0
mcp_serve.py                 +376 / -1
gateway/run.py               +216 / -2
gateway/session.py           +162 / -28
tools/approval.py            +156 / -16
web/src/App.tsx               +53 / -19     ← route + nav registration
```

That yields **three different couplings**, not one, and they have very
different costs to separate:

| layer | coupling to the core | separable? |
|---|---|---|
| `agent-home/` | HTTP only — no Python imports, 9 incidental `HERMES_*`/`hermes_cli` string references, absent from the Python wheel | **Yes, cheaply.** Cost: hand-mirrored types + its own deploy |
| `web/` | `web_dist/**/*` is declared **package data of `hermes_cli`** in `pyproject.toml:344`; the dashboard ships *inside* the Python wheel and is served by `hermes dashboard` | **No, not cheaply.** Would need the dashboard unbundled from the CLI first |
| your features (FG-16…FG-23: memory, principals, access, RAG, explorer) | new modules *inside* `hermes_cli/`, `tools/`, `gateway/`, using the datastore router, `access.py` scope filters, RLS binding and the audit ledger directly | **No.** This is core extension, not an app layer |

The third row is the crux: **the code you'd most want on the "user side" isn't
app code — it's core.** Moving FG-21/22 out means either duplicating the
DB/RLS/audit layer or promoting it to a public, versioned API. That is a much
larger project than moving directories.

## 3. Pros of splitting

- **An agent that writes apps stops needing write access to the code that runs
  it.** This is the strongest argument and it is a *security* argument, not an
  organisational one: today "the agent creates an app" and "the agent edits the
  agent" are the same permission on the same tree. A boundary here is worth
  real cost.
- **Blast radius.** A broken user app cannot break the gateway, the memory tier
  or the approval path if it isn't in the same deployable.
- **Upstream merges get easier** — but see §4: they are already easy, because
  the fork barely deletes upstream code. The one genuine merge battleground is
  `web_server.py`, and that is fixable without a split.
- **Independent cadence.** Apps ship without a Python release; the core ships
  without rebuilding apps.
- **CI proportionality.** A UI change stops running a ~4,000-test Python sweep.
- **A clean licensing/attribution line** between the upstream fork and your own
  product code.

## 4. Cons of splitting

- **The API becomes a contract you must version.** Today a change like #107 —
  the explorer reading the wrong schema — was one commit touching the endpoint
  *and* its consumer, tested together, deployed atomically. Split, that is two
  PRs, two deploys, an ordering rule and a compatibility window. Every FG-22-
  shaped change pays that tax forever.
- **Atomic deploys end, and this box has already shown the bill.** One extra
  checkout was enough to freeze the phone app for ten days with nothing
  reporting it (FG-23 §2). Two repositories means two revisions, two state
  manifests, and a drift check that must reason about the *pair* — everything
  built in #81/#86/#108 is per-repo today.
- **Type duplication becomes contractual.** `agent-home/src/types/index.ts`
  (584 lines) is already a hand-written mirror of the Python responses, and
  `web/src/lib/api.ts` is 2,946 lines of the same. Inside one repo that drift is
  caught by a failing test in the same PR. Across repos it needs generated types
  (OpenAPI → TS) or it drifts silently and is discovered by a user.
- **`web/` cannot come along** without first unbundling `web_dist` from the
  Python wheel (`pyproject.toml:344`), so a naive split leaves the operator
  console in the "core" repo — the opposite of the stated intent.
- **npm workspaces break at the boundary.** `agent-home` is a workspace of the
  root `package.json`; deps hoist to `<checkout>/node_modules`. Split out, it
  needs its own lockfile and its own install tree (~1.6 GB on this box).
- **Two-repo bisects.** "It worked last week" becomes a search across a pair of
  histories with no shared ordering.

## 5. What the evidence actually argues for

The measurements point somewhere slightly different from the question:

1. **Keep `ai-prentice-4-all` as one repo — it is already the core/product
   boundary.** It is a *fork*; upstream `NousResearch/hermes-agent` is the
   "core you don't change", and the fork deletes ~165 lines of it in 225
   commits. That boundary exists and is holding.
2. **Put the agent-created app layer outside the repo entirely** — not as a
   second copy of this tree, but as per-app directories (or one repo per app)
   under a path the agent may write and the core may only read/serve, with
   `agent-home` linking to them. That delivers the property actually wanted:
   users and the agent create apps *without* the ability to modify the system
   that runs them. It is also the only version of this that improves security
   rather than just file layout.
3. **If upstream merges get painful, reduce the seam count, don't split the
   repo.** `web_server.py` is the single hot spot (+1,713 lines of router
   mounting). A registration hook for API routers — and the equivalent for
   dashboard routes/nav in `App.tsx` — removes the conflict surface at a
   fraction of a split's cost, and matches upstream's own "narrow waist,
   capability at the edges" rule in `AGENTS.md`.
4. **Revisit a real split when a second deployment exists.** Versioned API
   contracts and independent release trains pay for themselves once the API has
   consumers you don't deploy yourself. With one box and one owner they are
   pure overhead.
5. **Either way, finish FG-23 A0 first.** One repository in two directories is
   not a step toward a split; it is the failure mode of one, with none of the
   benefits.

## 6. Open question for the owner

Which property is actually wanted?

- **(a) Safety** — the agent can build apps but cannot modify the system. →
  §5.2, an app layer outside the repo. Recommended; largest benefit per unit of
  work.
- **(b) Cleaner upstream merges.** → §5.3, registration hooks. Cheap.
- **(c) Independent release/deploy of the UI.** → a genuine split, worth it only
  once (a) and (b) are in place and a second consumer exists.
