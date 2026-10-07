"""Backfill ``api_call_log`` from ``agent.log`` history.

The per-call ledger only fills from the build that introduced it; older
main-model calls exist only as ``agent.conversation_loop`` log lines. This
parses those lines (successes with latency and tokens, failed attempts
with their error type), tags worker calls with their board card and run,
and inserts them — once. Rows get a ``log:`` request ID, so a re-run skips
what it already wrote, and nothing at or after the ledger's first live
row is touched (the live recorder already has those calls).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from hermes_cli import kanban_db
from hermes_constants import get_hermes_home
from hermes_state import SessionDB

_PREFIX = (
    r"^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(?P<msec>\d{3}) \w+ "
    r"\[(?P<session>[^\]]*)\] "
)
_OK_RE = re.compile(
    _PREFIX
    + r"agent\.conversation_loop: API call #(?P<n>\d+): model=(?P<model>\S+)"
    r" provider=(?P<provider>\S+) in=(?P<inp>\d+) out=(?P<out>\d+)"
    r" total=\d+ latency=(?P<latency>[\d.]+)s(?: cache=(?P<cache>\d+)/\d+)?"
)
_FAIL_RE = re.compile(
    _PREFIX
    + r"agent\.conversation_loop: API call failed \(attempt (?P<n>\d+)/\d+\)"
    r" error_type=(?P<error>\S+).*? provider=(?P<provider>\S+)"
    r".*? model=(?P<model>\S+)"
)
_TURN_RE = re.compile(
    _PREFIX
    + r"agent\.turn_context: conversation turn: session=(?P<sid>\S+)"
    r" .*?msg='work kanban task (?P<task>t_[0-9a-f]+)"
)


@dataclass
class LoggedCall:
    request_id: str
    ts: float
    session_id: str
    model: str
    provider: str
    status: str
    duration_ms: Optional[float] = None
    started_at: Optional[float] = None
    error_type: Optional[str] = None
    retry_count: Optional[int] = None
    usage: dict = field(default_factory=dict)


@dataclass
class BackfillResult:
    parsed: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    skipped_after_cutoff: int = 0
    card_calls: int = 0


def _line_ts(m: re.Match) -> float:
    # agent.log stamps are the host's local time (logging's default).
    base = time.mktime(time.strptime(m["date"], "%Y-%m-%d %H:%M:%S"))
    return base + int(m["msec"]) / 1000.0


def log_files(logs_dir: Path) -> list[Path]:
    """``agent.log`` and its rotations, oldest first."""
    rotated = sorted(
        (p for p in logs_dir.glob("agent.log.*") if p.suffix[1:].isdigit()),
        key=lambda p: int(p.suffix[1:]),
        reverse=True,
    )
    current = logs_dir / "agent.log"
    return rotated + ([current] if current.exists() else [])


def parse_lines(
    lines: Iterable[str],
) -> tuple[list[LoggedCall], dict[str, str]]:
    """Calls found in ``lines`` plus a worker session → card ID map.

    Identical lines (the same record written by two handlers) count once.
    """
    calls: list[LoggedCall] = []
    session_task: dict[str, str] = {}
    seen: set[str] = set()
    for line in lines:
        if "agent.conversation_loop: API call" not in line and (
            "work kanban task" not in line
        ):
            continue
        if line in seen:
            continue
        seen.add(line)
        m = _OK_RE.match(line)
        if m:
            ts = _line_ts(m)
            latency = float(m["latency"])
            cache = int(m["cache"] or 0)
            sid = m["session"]
            calls.append(LoggedCall(
                request_id=f"log:{sid}:{int(ts * 1000)}:ok{m['n']}",
                ts=ts,
                started_at=ts - latency,
                session_id=sid,
                model=m["model"],
                provider=m["provider"],
                status="ok",
                duration_ms=latency * 1000,
                usage={
                    "input_tokens": max(0, int(m["inp"]) - cache),
                    "output_tokens": int(m["out"]),
                    "cache_read_tokens": cache,
                },
            ))
            continue
        m = _FAIL_RE.match(line)
        if m:
            ts = _line_ts(m)
            sid = m["session"]
            calls.append(LoggedCall(
                request_id=f"log:{sid}:{int(ts * 1000)}:err{m['n']}",
                ts=ts,
                session_id=sid,
                model=m["model"],
                provider=m["provider"],
                status="error",
                error_type=m["error"],
                retry_count=int(m["n"]) - 1,
            ))
            continue
        m = _TURN_RE.match(line)
        if m:
            session_task[m["sid"]] = m["task"]
    return calls, session_task


def _read_lines(paths: Iterable[Path]) -> Iterator[str]:
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            yield from fh


def _task_runs(task_ids: set[str]) -> dict[str, list[tuple[int, float, float]]]:
    """``task_id → [(run_id, started_at, ended_at)]`` across every board."""
    runs: dict[str, list[tuple[int, float, float]]] = {}
    if not task_ids:
        return runs
    marks = ",".join("?" * len(task_ids))
    for board in kanban_db.list_boards():
        slug = board.get("slug")
        if not kanban_db.kanban_db_path(board=slug).exists():
            continue
        conn = kanban_db.connect(board=slug)
        try:
            for row in conn.execute(
                "SELECT id, task_id, started_at, ended_at FROM task_runs"
                f" WHERE task_id IN ({marks})",
                tuple(task_ids),
            ).fetchall():
                runs.setdefault(row["task_id"], []).append((
                    int(row["id"]),
                    float(row["started_at"] or 0),
                    float(row["ended_at"] or float("inf")),
                ))
        finally:
            conn.close()
    return runs


def _run_for(
    runs: list[tuple[int, float, float]], ts: float
) -> Optional[int]:
    for run_id, started, ended in runs:
        if started - 5 <= ts <= ended + 5:
            return run_id
    return None


def backfill(
    *,
    home: Optional[Path] = None,
    logs_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> BackfillResult:
    home = Path(home or get_hermes_home())
    calls, session_task = parse_lines(
        _read_lines(log_files(Path(logs_dir or home / "logs")))
    )
    result = BackfillResult(parsed=len(calls))
    db = SessionDB(db_path=home / "state.db")
    try:
        conn = db._conn
        assert conn is not None
        live_start = conn.execute(
            "SELECT MIN(ts) FROM api_call_log"
            " WHERE request_id IS NULL OR request_id NOT LIKE 'log:%'"
        ).fetchone()[0]
        existing = {
            r[0] for r in conn.execute(
                "SELECT request_id FROM api_call_log"
                " WHERE request_id LIKE 'log:%'"
            ).fetchall()
        }
        runs = _task_runs({session_task[c.session_id] for c in calls
                           if c.session_id in session_task})
        # A worker session is one attempt: the run holding its first call
        # owns them all, including wrap-up calls that land after the run
        # was marked finished.
        first_seen: dict[str, float] = {}
        for c in calls:
            if c.session_id in session_task:
                start = c.started_at if c.started_at is not None else c.ts
                first_seen[c.session_id] = min(
                    first_seen.get(c.session_id, start), start
                )
        session_run = {
            sid: _run_for(runs.get(session_task[sid], []), ts)
            for sid, ts in first_seen.items()
        }
        for call in calls:
            if live_start is not None and call.ts >= live_start:
                result.skipped_after_cutoff += 1
                continue
            if call.request_id in existing:
                result.skipped_existing += 1
                continue
            existing.add(call.request_id)
            task = session_task.get(call.session_id)
            run_id = session_run.get(call.session_id) if task else None
            if task:
                result.card_calls += 1
            result.inserted += 1
            if dry_run:
                continue
            db.record_api_call(
                request_id=call.request_id,
                ts=call.ts,
                started_at=call.started_at,
                session_id=call.session_id,
                caller="main",
                model=call.model,
                provider=call.provider,
                duration_ms=call.duration_ms,
                status=call.status,
                error_type=call.error_type,
                retry_count=call.retry_count,
                usage=call.usage,
                kanban_task_id=task or "",
                kanban_run_id=run_id,
            )
    finally:
        db.close()
    return result
