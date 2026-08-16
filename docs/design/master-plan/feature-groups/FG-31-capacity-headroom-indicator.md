# FG-31 — Capacity headroom indicator ("when should I upgrade the box?")

**Wave:** P6-E (independent; can run any time after FG-28) · **Owner agent:** devin · **Status:** IMPLEMENTED — awaiting the calibration load run on `hermes-systest` (§Calibration status)

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
| active conversations vs cap | `active_sessions` leases + `max_concurrent_sessions` (both shipped), **summed across profile homes** | the direct RAM driver; see the correction below — the registry is *profile-local*, not box-wide |
| resident memory + system available | process RSS + host available memory | measured: gateway ~150 MB, dashboard ~225 MB, embedding server ~790 MB **shared** (does not multiply per profile) |
| SQLite write-lock wait time | **new**: `SessionDB._execute_write` times its `BEGIN IMMEDIATE`, flushed to `state_meta` in hourly buckets | the serialisation bound, otherwise invisible until users call it "slow" |
| turn latency p50/p95 | **derived**: assistant row minus the `user` row before it, per session, from `messages.timestamp` | the number the owner actually experiences |
| profile count | `list_profiles` | multiplies the fixed slabs |

A monitoring subsystem is exactly the kind of speculative infrastructure
`AGENTS.md` warns against, so collection is a handful of small reads on demand:
no daemon, no time-series store, no new tables.

### 1a. Four premises in this plan did not hold — corrected during implementation

Each was checked against shipped code before anything was written; the first two
changed the design, not just the wording.

**Correction 1 — the lease registry is profile-local, so "cross-process" is not
box-wide.** `active_sessions._state_dir()` is `get_hermes_home()/runtime/`, which
is profile-scoped: each profile has its own registry file *and* enforces its own
cap, while the RAM it protects is box-wide. Three profiles at 6 conversations
each would each report `6 / 15` while the box carries 18.
`capacity.collect_session_load()` therefore sums every profile's registry through
the new `active_sessions.read_registry_for_home()` — read-only, taking no lock,
so a reader can never stall the profile that owns the file — and reports
`cap_box_wide` as the **sum** of the profiles' caps, which is what the box can be
*asked* to hold. An uncapped profile makes the box cap unknown rather than
understated. **Whether the cap itself should become box-wide is a behaviour
change and is deliberately not done here**: today 3 profiles × 15 means the box
can be asked for 45, and that is the owner's decision to make.

**Correction 2 — write-lock waits were not instrumented at all.**
`_execute_write` retried on `database is locked` and recorded nothing: no
counter, no log, no total. The one bound a bigger box cannot fix was the one
bound with no data. The write path now times its `BEGIN IMMEDIATE` and
accumulates `(events, waited_s, exhausted)`, flushed into `state_meta` in hourly
buckets (24 kept), because `hermes status` runs in a *different process* from the
gateway and an in-memory counter would always read zero. The flush happens
**after** a write that won, never inside the retry loop, so the accounting never
joins the queue it is measuring. It times the acquisition rather than counting
retries: SQLite's own 1 s busy handler absorbs most contention, so a retry-only
counter stays at zero until things are already severe.

**Correction 3 — there is no turn-latency series.** `_running_agents_ts` holds
in-flight *start* times only; no completed-turn duration is persisted anywhere.
Latency is derived from the transcript instead — `recent_turn_latencies_s()`
pairs each `assistant` row with the `user` row immediately before it in the same
session. That is genuinely free, and it measures the wait the person actually
experienced rather than internal timing.

**Correction 4 — "the console" is the wrong surface.** Per **D20** `agent-home`
is the user-facing UI and the dashboard is the operator console, so the card is
`agent-home/src/app/capacity` behind a BFF route. Same correction FG-30's plan
needed in #244.

One recommendation in §3 was also unusable as written: this deployment already
runs **one** console on one `HERMES_HOME`, so "run one console instead of one per
profile" is advice that cannot be taken, and it is not offered. Gateway
consolidation is real (`hermes-gateway-<profile>.service` units exist), so that
one stayed.

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

Surfaced in four places that already exist: `hermes status` (the reading and the
verdict), `hermes doctor` (the reading, the verdict and the **actions** — a
`constrained` verdict becomes a doctor issue), `agent-home`'s Capacity screen
(D20, not the dashboard), and FG-29's weekly digest, so it arrives in the same
review moment as everything else. A comfortable box costs the digest two lines.

When two bounds land on the same state the **more pressed** one is named, and a
bound hardware cannot fix wins the tie — recommending an upgrade that cannot
help is worse than saying nothing. Each bound's pressure is measured against its
own `constrained` line, so the comparison is like-for-like. An indicator that
cannot be read is reported as unknown and produces no bound at all; a measured
zero and a failed measurement are different facts.

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
- `hermes status` / `hermes doctor` / `agent-home`; FG-29 weekly digest.
- FG-30 idle-profile detection; FG-28 one-gateway consolidation.

## As implemented

| piece | where |
|---|---|
| indicators, verdict, bounds, recommendations, rendering | `hermes_cli/capacity.py` — `collect_indicators`, `derive_verdict`, `headroom`, `summary_line`, `digest_lines`, `as_dict` |
| box-wide lease read | `active_sessions.read_registry_for_home()` |
| write-lock accounting | `SessionDB._note_write_contention` / `_flush_write_contention` / `read_write_contention` |
| turn latency | `SessionDB.recent_turn_latencies_s()` |
| thresholds | `config.yaml` → `capacity:` via `CapacityThresholds.from_config`; documented in `cli-config.yaml.example` and the user guide. No env vars |
| CLI surfaces | `hermes_cli/status.py::_show_capacity`, `hermes_cli/doctor.py::_check_capacity_headroom` |
| digest | `hermes_cli/goal_conflicts.py::weekly_digest` — reuses the idle profiles it already computes as the cheapest recommendation |
| API + UI | Python `GET /api/capacity` → agent-home `/api/capacity` BFF → `/capacity` screen, Home tile, sidebar entry |
| tests | `tests/hermes_cli/test_fg31_capacity.py` (30), `tests/hermes_cli/test_fg31_capacity_endpoint.py` (3, the real route over HTTP), `agent-home/src/components/capacity/CapacityView.test.tsx` (5) |

**The open question is answered: report-only.** Nothing lowers the cap; the
verdict says in words that serving fewer people is the owner's call.

## Scope

**In:** collection of the five indicators; the derived three-state verdict with
the binding constraint named; surfacing in `hermes status`, `hermes doctor`,
`agent-home` and the weekly digest; action recommendations (cheap ones first,
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
  gateway** — the registry is profile-local, so a single-profile count would
  understate the true load (three profiles × 6 conversations is 18, not 6).
- An indicator that cannot be read reports as unknown, never as a measured zero,
  and produces no bound of its own.
- The route is driven **over HTTP**, not called as a function: FG-28's three
  route defects all lived in the wiring rather than the function under test.
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

### Calibration status — NOT DONE

`conversation_cost_mb: 250` is an **estimate, not a measurement**, and the code
says so where it is used: the hardware recommendation prints that the
per-conversation figure "is still an estimate — the systest calibration run
replaces it with a measurement". Nothing enforcing depends on it (it feeds sizing
advice only, never a refusal), so the indicator stays honest while the number is
unvalidated. The deploy plus the scripted load run on `hermes-systest` is the
remaining work and needs Leo's go, since it touches the box.

## Dependencies

- **Related:** FG-28 (one gateway — makes the count cross-profile and the advice
  meaningful), FG-30 (idle profiles are the cheapest recommendation), FG-29
  (digest delivery).
- **Blocks:** nothing. Independent of the goal/skill work; can ship whenever.

## Definition of Done

Indicators collected from existing sources; three-state verdict naming its
binding constraint; surfaced in status/doctor/agent-home/digest; recommendations
ordered cheap-first and honest about the SQLite bound; thresholds in
`config.yaml`; calibrated on the system-test box with the per-conversation
memory cost recorded; `scripts/run_tests.sh`, `ruff`, `ty` clean.

## Progress checklist

- [x] Collect: active sessions vs cap, RSS + available memory, write-lock waits, turn p50/p95, profile count — `hermes_cli/capacity.py`; write-lock waits and latency were new work, see §1a
- [x] Derived verdict (comfortable / watch / constrained) naming the binding constraint — `derive_verdict`
- [x] Surface in `hermes status`, `hermes doctor`, **`agent-home`** (not the console — D20), FG-29 digest
- [x] Recommendations: cheap actions first; hardware with measured basis; SQLite bound flagged as not-fixable-by-hardware
- [x] `config.yaml` thresholds (no env vars) — `capacity:`, with a test asserting an env var does **not** take effect
- [x] Cross-profile correctness under one multiplexed gateway — the registry is profile-local; summed for the indicator, cap left per-profile
- [ ] **Calibration load run on `hermes-systest`; per-conversation memory cost recorded here** — needs Leo's go (deploys to the box). Until then `conversation_cost_mb` is labelled an estimate wherever it is shown

## Open questions

1. ~~**Should `constrained` do anything, or only report?**~~ **Answered:
   report-only.** Lowering `max_concurrent_sessions` would protect
   responsiveness by refusing new sessions with a clear message rather than
   letting everyone slow down — but it is the system deciding to serve fewer
   people, which is the owner's call. Nothing in the implementation changes
   runtime behaviour; a `constrained` verdict says the cap is the owner's to
   lower and prints the key to lower it with. A test asserts the cap is
   unchanged after a `constrained` reading.

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-30 | 2 | devin (for Leo) | Implemented FG-31; corrected four false premises in the plan and recorded the calibration debt | Leo: "I have another agent working on F2-F4. I want you to start working on FG-31 now." Checking the plan against shipped code first — the pickup-readiness pass that caught FG-26's and FG-28's stale prompts — found four premises that did not hold, and two of them changed the design rather than the wording (§1a). The load-bearing one: the plan's headline test assumed the lease registry is box-wide, but `_state_dir()` is `get_hermes_home()/runtime/`, so it is **profile-local** — three profiles at 6 live conversations each would each report `6 / 15` while the box carried 18, which is precisely the misreading a headroom indicator exists to prevent. The indicator now sums every profile's registry through a read-only helper that takes no lock, while the **cap** is deliberately left per-profile because making it box-wide is a behaviour change and the owner's call. Second: the plan claimed "nothing needs new instrumentation", but write-lock waits — the one bound a bigger box cannot fix — were counted nowhere, so they had to be instrumented and *persisted*, since `hermes status` is a different process from the gateway; and timing the lock acquisition rather than counting retries matters because SQLite's own 1s busy handler absorbs most contention, which is why the first contention test read zero. Third, no completed-turn latency is stored anywhere, so it is derived from the transcript. Fourth, "the console" contradicts D20, so the card is in `agent-home`. The open question is answered report-only: nothing lowers the cap, because deciding to serve fewer people is the owner's decision. `conversation_cost_mb` remains an **estimate** and is labelled as one wherever it is shown — presenting an unmeasured number as a measurement is how an indicator loses the trust it exists to earn. | Leo: "I have another agent working on F2-F4. I want you to start working on FG-31 now. Is that ok?" |
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo asked whether the concurrency caveat I kept attaching to Phase 6 was a coding gap or a performance concern, and proposed a simple indicator telling the owner when to upgrade. Checking the code settled it: Hermes **is** concurrent — asyncio loop plus a thread pool for turns, live-agent tracking, a shipped cross-process active-session lease system with a `max_concurrent_sessions` cap that refuses politely, and SQLite in **WAL** mode so readers never block. What bounds it is capacity, in two specific places: SQLite's **single writer** serialises simultaneous writes (latency, never corruption) and each *active conversation* holds a live agent in RAM to keep its prompt cache warm, so RAM tracks concurrent conversations rather than user count. Hence: hundreds registered is fine, hundreds simultaneous is untested. That makes Leo's suggestion the right response — this FG surfaces the need for scale-out work in advance instead of pre-building it. Two design choices are load-bearing. The verdict **names its binding constraint** rather than showing a percentage, because "memory, driven by 9 concurrent conversations" tells the owner what to do and "78%" does not. And when the bound is write-lock waits the FG must say **a bigger box will not help** — recommending a useless upgrade would be worse than silence. All thresholds ship conservative and must be calibrated on the system-test box, since production has one principal and the current memory figures are idle snapshots missing the one number that matters most: RSS per additional live conversation. | Leo: "What is the concurrency issue? Is it a coding issue? There is no concurrent support or it is a performance concern in case when there are hundreds of users? If for performance concern, we need a simple performance indicator to remind the owner when is the time to upgrade the hardware" |

## Cloud-agent prompt — for the calibration run only

**The feature is built. Do not re-implement it.** §As implemented lists every
file; §1a lists the four premises in the original plan that were false, so
reading §§1–4 alone would send you to rebuild the wrong design. What is left is
one task, and it needs Leo's authorisation because it touches the live box.

> Repo `leolau/ai-prentice-4-all`. Read `docs/design/master-plan/README.md`,
> `AGENTS.md`, this doc — including §1a and §As implemented — and the
> `testing-hermes-systest-box` skill. **No SSH**: the box is driven only through
> `/home/ubuntu/run_on_box.sh`, and deploying needs Leo's explicit go.
>
> Deploy the merged FG-31 code, then run a scripted concurrent load on
> `hermes-systest` at increasing concurrency and record three numbers: **RSS per
> additional live conversation** (the one figure `conversation_cost_mb: 250`
> currently guesses), the concurrency at which write-lock waits first appear
> (`SessionDB.read_write_contention()` now reports them), and p95 turn latency at
> each step (`recent_turn_latencies_s()`).
>
> Then set the `capacity:` defaults in `cli-config.yaml.example` from those
> measurements, replace §Calibration status with the measured table, tick the last
> checklist item, and drop the "still an estimate" wording from
> `capacity._tier_advice` once the figure is real. Verify the reading
> on the box against what you actually loaded it with — an indicator that cries
> wolf gets ignored, and an indicator that is ignored is worse than none.
> `scripts/run_tests.sh`, `ruff`, `ty` clean.
