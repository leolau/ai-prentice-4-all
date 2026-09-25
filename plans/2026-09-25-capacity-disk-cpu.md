# Capacity page: storage + CPU indicators

Status: implemented, pending deploy

## What changed

`hermes_cli/capacity.py` gains two collectors and two bounds:

- **Storage** (`StorageLoad`) — `psutil.disk_usage` on the filesystem holding
  `HERMES_HOME` (i.e. the data volume where the SQLite DBs, bridge session
  dirs, and backups live). Binds the verdict at `watch_disk_pct` (0.80) /
  `constrained_disk_pct` (0.90); reason names the path so it's clear *which*
  volume is full. Recommendation steers to cleanup (logs, `zz_migrated_*`
  tables, rebind backups) — the memory-sizing tier advice is suppressed for
  this bound since a bigger instance doesn't fix a full disk.
- **CPU** (`CpuLoad`) — 1-minute load average per core (`psutil.getloadavg`)
  binds the verdict at `watch_load_ratio` (1.0) / `constrained_load_ratio`
  (2.0); an instantaneous `cpu_percent` sample is collected for display only —
  a spike is too noisy to judge by.

Both are display + verdict inputs; thresholds are tunable via
`config.yaml: capacity:` like the existing ones. Unmeasured indicators list
under "Not measured", never as a fake zero — matching the existing contract.

## Surfaces

- `as_dict` → `CapacityIndicators` fields `disk_used_mb/total_mb/pct/path`,
  `cpu_load1/count/percent`.
- `summary_line` gains `disk N% used · load X/Y cpu`.
- agent-home CapacityView gains "Storage" and "CPU load" rows.
- Tests: 4 new pytest cases (threshold transitions, binding, unmeasured),
  CapacityView fixture + render assertions. The small-sample latency test now
  stubs the real-machine collectors so a loaded dev box can't bind the
  verdict.
