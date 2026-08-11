# FG-31 — Capacity headroom indicator ("when should I upgrade the box?")

**Wave:** P6-E (independent; can run any time after FG-28) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started

## Summary

Phase 6 makes the *access model* serve hundreds of registered people. It does
not change what one box can do at once. The right response is not a scaling
project up front — it is to **tell the owner where they stand** before things
get slow:

> Active conversations 6 / ~15 · memory 5.2 / 15 GB · no write-lock waits.
> **Headroom: comfortable.** Add a profile or ~20 more people freely.

## What "concurrency" actually means here — measured against the code

The distinction matters, because the two limits have different fixes.

**Hermes is genuinely concurrent.** The gateway runs an asyncio event loop with
a `ThreadPoolExecutor` for agent turns, tracks live agents per session
(`_running_agents`), and already has a cross-process active-session lease system
(`hermes_cli/active_sessions.py`) with a configurable
`max_concurrent_sessions` cap that refuses new sessions with a user-facing
message rather than degrading. `SessionDB` runs SQLite in **WAL** mode, so
readers do not block. **Registered users cost nearly nothing.**

**Two things bound it, and both are capacity, not correctness:**

| bound | nature | symptom |
|---|---|---|
| SQLite **single writer** (WAL: many readers, one writer) | serialisation | write-lock waits under simultaneous turns → latency, not corruption |
| one live `AIAgent` per active conversation held in RAM (to keep its prompt cache warm) | memory | RAM pressure tracks **concurrent conversations**, not user count |

So: *hundreds registered is fine; hundreds talking in the same second is
untested and is separate, larger work* (gateway workers sharded by session key,
`SessionDB` off SQLite, per-principal rate/cost quotas). This FG does not do
that work — it makes the need for it **visible in advance**, which is what an
owner of a family, an OPC or a school can actually act on.

## Design / approach

### 1. Measure the real bottlenecks, from data that already exists

| indicator | source | why it is the right signal |
|---|---|---|
| active conversations vs cap | `active_sessions` leases + `max_concurrent_sessions` (both shipped) | the direct RAM driver; already cross-process, so it is correct under one multiplexed gateway |
| resident memory + system available | process RSS + host available memory | measured: gateway ~150 MB, dashboard ~225 MB, embedding server ~790 MB **shared** (does not multiply per profile) |
| SQLite write-lock wait time | `SessionDB` busy/retry timings | the serialisation bound, otherwise invisible until users call it "slow" |
| turn latency p50/p95 | existing turn timing | the number the owner actually experiences |
| profile count | `list_profiles` | multiplies the fixed slabs |

Nothing here needs new instrumentation beyond timing already-instrumented
paths — deliberately, because a monitoring subsystem is exactly the kind of
speculative infrastructure `AGENTS.md` warns against.

### 2. One derived verdict, not a dashboard of graphs

The owner is not an SRE. The output is a single state with a stated reason:

```
comfortable   — headroom for more people/profiles
watch         — a sustained bound is approaching; plan the upgrade
constrained   — actively degrading; upgrade or reduce concurrency now
```

Rules of thumb: `watch` when peak concurrent sessions sustain above ~60% of the
cap, or available memory drops below the cost of one more profile plus a working
margin, or write-lock waits become routine rather than rare. Thresholds live in
`config.yaml`; **the verdict must name the binding constraint** ("memory, driven
by 9 concurrent conversations") — a bare percentage tells the owner nothing
about what to do.

Surfaced in three places that already exist: `hermes status`, `hermes doctor`
(with the recommendation), and the console. It also belongs in FG-29's weekly
digest, so it arrives in the same review moment as everything else.

### 3. Recommend the specific action, and be honest about the cheap ones first

When the state is `watch` or `constrained`, name the action:

- **the cheap ones first** — retire idle profiles (FG-30 already detects them),
  consolidate to one gateway if that has not been enabled (FG-28: removes
  ~150 MB per profile), run one console instead of one per profile (~225 MB
  each);
- **then hardware** — with the measured basis, e.g. "10 profiles + 15 concurrent
  conversations needs roughly the 8/32 tier";
- **and, if the bound is write-lock waits rather than RAM, say so plainly** —
  that one is *not* fixed by a bigger box, it is the SQLite serialisation bound
  and needs the runtime work. Recommending an upgrade that cannot help would be
  worse than saying nothing.

### 4. Calibration is required before the numbers are believable

Everything above rests on thresholds nobody has validated: production has one
principal, and the memory figures are idle-ish snapshots from a gateway that had
been up under three hours. Ship with conservative defaults, then calibrate on
the system-test box with a scripted concurrent load, and record the measured
per-conversation memory cost — the one number the current estimates are missing
— in this doc. A headroom indicator that cries wolf gets ignored, and an
indicator that is ignored is worse than none.

## Reuse map

- `hermes_cli/active_sessions.py` — leases, `max_concurrent_sessions`,
  `resolve_max_concurrent_sessions` (shipped, cross-process).
- `gateway/run.py` — `_running_agents`, `_active_session_limit_message`.
- `hermes_state.py` — WAL config, busy/retry paths for write-wait timing.
- `hermes status` / `hermes doctor` / console; FG-29 weekly digest.
- FG-30 idle-profile detection; FG-28 one-gateway consolidation.

## Scope

**In:** collection of the five indicators; the derived three-state verdict with
the binding constraint named; surfacing in `hermes status`, `hermes doctor`,
console and the weekly digest; action recommendations (cheap ones first,
hardware with a measured basis, honesty when a bigger box will not help);
`config.yaml` thresholds; calibration run on the system-test box.

**Out:** autoscaling; a metrics/monitoring backend or time-series store
(instantaneous + short rolling window only); the runtime scale-out itself
(sharded workers, `SessionDB` off SQLite, per-principal quotas); alerting
integrations.

## Testing requirements

- Verdict transitions at configured thresholds using injected indicator values;
  the binding constraint is named correctly when two bounds are close.
- Write-lock-wait accounting reflects genuine contention (two writers) and stays
  ~zero when idle.
- Active-session accounting is correct **across profiles under one multiplexed
  gateway** — the lease system is cross-process, and a per-process count would
  understate the true load.
- A `constrained` state caused by write-lock waits recommends the runtime work,
  **not** a hardware upgrade.
- Collection is cheap enough to run on the digest cadence and on demand without
  measurably affecting turn latency.

## System testing (system-test box)

Scripted concurrent load on `hermes-systest` at increasing concurrency: record
RSS per additional live conversation (the missing number), the concurrency at
which write-lock waits appear, and p95 turn latency at each step. Set the
default thresholds from those measurements and record them here, including the
per-conversation memory figure.

## Dependencies

- **Related:** FG-28 (one gateway — makes the count cross-profile and the advice
  meaningful), FG-30 (idle profiles are the cheapest recommendation), FG-29
  (digest delivery).
- **Blocks:** nothing. Independent of the goal/skill work; can ship whenever.

## Definition of Done

Indicators collected from existing sources; three-state verdict naming its
binding constraint; surfaced in status/doctor/console/digest; recommendations
ordered cheap-first and honest about the SQLite bound; thresholds in
`config.yaml`; calibrated on the system-test box with the per-conversation
memory cost recorded; `scripts/run_tests.sh`, `ruff`, `ty` clean.

## Progress checklist

- [ ] Collect: active sessions vs cap, RSS + available memory, write-lock waits, turn p50/p95, profile count
- [ ] Derived verdict (comfortable / watch / constrained) naming the binding constraint
- [ ] Surface in `hermes status`, `hermes doctor`, console, FG-29 digest
- [ ] Recommendations: cheap actions first; hardware with measured basis; SQLite bound flagged as not-fixable-by-hardware
- [ ] `config.yaml` thresholds (no env vars)
- [ ] Cross-profile correctness under one multiplexed gateway
- [ ] Calibration load run on `hermes-systest`; per-conversation memory cost recorded here

## Open questions

1. **Should `constrained` do anything, or only report?** Lowering
   `max_concurrent_sessions` would protect responsiveness by refusing new
   sessions with a clear message rather than letting everyone slow down — but it
   is the system deciding to serve fewer people, which is the owner's call.
   Recommend report-only, with a one-click "apply the suggested cap".

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo asked whether the concurrency caveat I kept attaching to Phase 6 was a coding gap or a performance concern, and proposed a simple indicator telling the owner when to upgrade. Checking the code settled it: Hermes **is** concurrent — asyncio loop plus a thread pool for turns, live-agent tracking, a shipped cross-process active-session lease system with a `max_concurrent_sessions` cap that refuses politely, and SQLite in **WAL** mode so readers never block. What bounds it is capacity, in two specific places: SQLite's **single writer** serialises simultaneous writes (latency, never corruption) and each *active conversation* holds a live agent in RAM to keep its prompt cache warm, so RAM tracks concurrent conversations rather than user count. Hence: hundreds registered is fine, hundreds simultaneous is untested. That makes Leo's suggestion the right response — this FG surfaces the need for scale-out work in advance instead of pre-building it. Two design choices are load-bearing. The verdict **names its binding constraint** rather than showing a percentage, because "memory, driven by 9 concurrent conversations" tells the owner what to do and "78%" does not. And when the bound is write-lock waits the FG must say **a bigger box will not help** — recommending a useless upgrade would be worse than silence. All thresholds ship conservative and must be calibrated on the system-test box, since production has one principal and the current memory figures are idle snapshots missing the one number that matters most: RSS per additional live conversation. | Leo: "What is the concurrency issue? Is it a coding issue? There is no concurrent support or it is a performance concern in case when there are hundreds of users? If for performance concern, we need a simple performance indicator to remind the owner when is the time to upgrade the hardware" |

## Cloud-agent prompt

> Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, FG-28, FG-29, FG-30 and this
> doc.
>
> **Add no monitoring subsystem.** Every indicator comes from something shipped:
> `hermes_cli/active_sessions.py` (leases + `max_concurrent_sessions`),
> `gateway/run.py` `_running_agents`, `hermes_state.py` busy/retry paths for
> write-lock waits, existing turn timing, `list_profiles`. Instantaneous plus a
> short rolling window — no time-series store.
>
> Derive **one** verdict (`comfortable` / `watch` / `constrained`) that **names
> the binding constraint**. Thresholds in `config.yaml`, never env vars. Surface
> in `hermes status`, `hermes doctor`, the console, and FG-29's weekly digest.
>
> Recommendations are ordered cheap-first: retire idle profiles (FG-30),
> consolidate to one gateway (FG-28, ~150 MB/profile), one console instead of
> per-profile (~225 MB each) — then hardware with a measured basis. **If the
> bound is SQLite write-lock waits, state explicitly that a bigger box does not
> fix it**; that is the runtime scale-out item.
>
> Verify active-session accounting is correct **across profiles under one
> multiplexed gateway** (the leases are cross-process; a per-process count
> understates load). Then run a scripted concurrent load on `hermes-systest`,
> record RSS per additional live conversation, the concurrency at which
> write-lock waits appear and p95 latency, set the defaults from those numbers,
> and write them into this doc. `scripts/run_tests.sh`, `ruff`, `ty` clean.
