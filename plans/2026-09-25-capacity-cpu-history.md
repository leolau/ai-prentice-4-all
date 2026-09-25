# Capacity page: CPU utilization history

**Date:** 2026-09-25 · **Branch:** `capacity-cpu-history`

## Request

> For CPU utilization in the capacity page: a simple past-24-hrs graph (1 hr = 1
> data point), max utilization in the past 24 hrs, past 7 days, past 30 days.

## The gap

`collect_cpu_load()` only reads instantaneous psutil values — nothing was ever
persisted, so there is no history to graph. Three pieces are needed: a store,
a sampler, and the aggregation.

## Design

**Store** — `<shared root>/capacity.db` (same box-wide root as `kanban.db`;
CPU is a box resource, so history must not fork per profile). One table,
`cpu_samples(ts, pct, load1)`, one row per minute, pruned at 31 days. Reads
never create the file — a missing DB means "no history yet", not an error.

**Sampler** — a daemon thread in the dashboard's lifespan
(`cpu-history-sampler`). The dashboard is the long-lived process that also
serves `/api/capacity`, on server and desktop alike. Each tick calls
`psutil.cpu_percent(interval=None)`, which reports busy share *since the
previous call* — so every stored sample is the average utilization over the
prior minute, not a single-tick snapshot.

**API** — `/api/capacity` gains a top-level `cpu_history` key (additive; the
verdict payload is untouched):

```json
"cpu_history": {
  "hourly": [{"ts": ..., "avg_pct": 12.3, "max_pct": 41.0}, ...24],
  "max_24h": 64.0, "max_7d": 88.5, "max_30d": 99.9
}
```

`hourly` is exactly 24 hour-aligned buckets ending at the current hour;
buckets with no samples are `null` so the UI renders a gap, not a fake zero.
Peaks are `MAX(pct)` over raw samples — i.e. the busiest sampled minute in
each window.

**UI** — `CapacityView` gains a "CPU utilization" section: a 24-bar chart
(hourly averages; title tooltip carries avg + peak per hour) and three rows
("Busiest minute, 24h / 7d / 30d"). No chart library — the codebase has none,
and 24 flex bars need none.

## Caveat

History starts accumulating at deploy time. The 24h graph fills within a day;
`max_7d`/`max_30d` only cover real samples, so for the first week they describe
"since deploy" — the peaks rows are honest (`—` when no data) rather than
implying a measured zero.

## Files

- `hermes_cli/capacity.py` — `capacity.db` connect/record/sampler/`cpu_history`
- `hermes_cli/web_server.py` — sampler thread in lifespan; `cpu_history` key in
  the endpoint payload
- `agent-home/src/types/index.ts` — `CpuHistory`, `CpuHistoryPoint`
- `agent-home/src/components/capacity/CapacityView.tsx` — chart + peaks section
- Tests: 4 new pytest cases, 2 new vitest cases

## Status

- [x] Implementation + tests (44 pytest, 8 vitest green; tsc + eslint clean)
- [ ] PR / merge
- [ ] Deploy + verify live
