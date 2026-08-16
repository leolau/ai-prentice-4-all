"""FG-31 — capacity headroom: the verdict, the bound it names, and the reading.

The behaviours under test are the ones a wrong implementation gets *plausibly*
wrong: a box-wide count that silently reports only this profile, a verdict that
does not say which bound produced it, and — the load-bearing one — a
``constrained`` caused by SQLite's single writer recommending a bigger box, which
would be money spent for no change.
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from hermes_cli import active_sessions, capacity
from hermes_cli.capacity import (
    BOUND_LATENCY,
    BOUND_MEMORY,
    BOUND_SESSIONS,
    BOUND_WRITE_LOCK,
    COMFORTABLE,
    CONSTRAINED,
    WATCH,
    CapacityIndicators,
    CapacityThresholds,
    MemoryLoad,
    SessionLoad,
    TurnLatency,
    WriteContention,
    derive_verdict,
)


# ── Thresholds come from config.yaml, never the environment ──────────────────


def test_thresholds_read_config_and_reject_nonsense(caplog):
    thresholds = CapacityThresholds.from_config(
        {"capacity": {"watch_p95_s": 5, "constrained_p95_s": "12.5"}}
    )
    assert thresholds.watch_p95_s == 5.0
    assert thresholds.constrained_p95_s == 12.5
    # Untouched keys keep their conservative defaults.
    assert thresholds.watch_session_ratio == CapacityThresholds().watch_session_ratio

    bad = CapacityThresholds.from_config({"capacity": {"watch_p95_s": "soon"}})
    assert bad.watch_p95_s == CapacityThresholds().watch_p95_s
    assert CapacityThresholds.from_config(None) == CapacityThresholds()
    assert CapacityThresholds.from_config({"capacity": "on"}) == CapacityThresholds()


def test_no_capacity_env_vars_are_introduced(monkeypatch):
    """A behavioural threshold set in the environment must not take effect."""
    monkeypatch.setenv("HERMES_CAPACITY_WATCH_P95_S", "0.001")
    assert CapacityThresholds.from_config({}).watch_p95_s == 25.0


# ── The verdict, and the bound it names ─────────────────────────────────────


def _indicators(
    *,
    active=0,
    cap=None,
    available_mb=8192.0,
    waits=0.0,
    p95=1.0,
    profiles=1,
) -> CapacityIndicators:
    return CapacityIndicators(
        sessions=SessionLoad(
            active_total=active, cap_box_wide=cap, cap_here=cap, profiles_seen=profiles
        ),
        memory=MemoryLoad(available_mb=available_mb, total_mb=16384.0),
        contention=WriteContention(events=waits, waited_s=waits * 0.1, window_s=3600.0),
        latency=TurnLatency(samples=100, p50_s=1.0, p95_s=p95),
        profile_count=profiles,
    )


def test_idle_box_is_comfortable_and_recommends_nothing():
    verdict = derive_verdict(_indicators(active=1, cap=15))
    assert verdict.state == COMFORTABLE
    assert verdict.recommendations == []
    assert "comfortable" in verdict.headline()


@pytest.mark.parametrize(
    "active,expected",
    [(1, COMFORTABLE), (9, WATCH), (13, CONSTRAINED)],
)
def test_session_verdict_transitions_at_the_configured_thresholds(active, expected):
    verdict = derive_verdict(_indicators(active=active, cap=15))
    assert verdict.state == expected
    if expected != COMFORTABLE:
        assert verdict.binding is not None
        assert verdict.binding.name == BOUND_SESSIONS
        # The count and the cap are in the reason, not just a percentage.
        assert f"{active} of 15" in verdict.binding.reason


def test_a_verdict_always_names_its_binding_constraint():
    verdict = derive_verdict(_indicators(active=14, cap=15, available_mb=200.0))
    assert verdict.state == CONSTRAINED
    assert verdict.binding is not None
    assert verdict.binding.name in {BOUND_SESSIONS, BOUND_MEMORY}
    assert verdict.binding.name in verdict.headline()


def test_memory_watch_when_one_more_profile_would_not_fit():
    thresholds = CapacityThresholds(profile_slab_mb=150.0, memory_margin_mb=512.0)
    verdict = derive_verdict(_indicators(active=2, cap=15, available_mb=600.0), thresholds)
    assert verdict.state == WATCH
    assert verdict.binding is not None
    assert verdict.binding.name == BOUND_MEMORY
    # Names the driver, so the owner knows what to reduce.
    assert "concurrent conversation" in verdict.binding.reason


def test_the_more_pressed_bound_is_the_one_named_when_two_are_close():
    # Both bounds land on `watch`; sessions is further along its own scale.
    thresholds = CapacityThresholds(profile_slab_mb=150.0, memory_margin_mb=512.0)
    verdict = derive_verdict(
        _indicators(active=12, cap=15, available_mb=660.0), thresholds
    )
    assert verdict.state == WATCH
    assert verdict.binding is not None
    assert verdict.binding.name == BOUND_SESSIONS


def test_latency_alone_can_produce_a_verdict():
    verdict = derive_verdict(_indicators(active=1, cap=15, p95=90.0))
    assert verdict.state == CONSTRAINED
    assert verdict.binding is not None
    assert verdict.binding.name == BOUND_LATENCY


# ── The bound hardware cannot fix ───────────────────────────────────────────


def test_write_lock_constrained_recommends_runtime_work_not_hardware():
    verdict = derive_verdict(_indicators(active=2, cap=15, waits=120.0))
    assert verdict.state == CONSTRAINED
    assert verdict.binding is not None
    assert verdict.binding.name == BOUND_WRITE_LOCK
    assert verdict.binding.hardware_helps is False
    joined = " ".join(verdict.recommendations)
    assert "bigger box does not fix this" in joined
    # No tier advice at all: the upgrade cannot move this bound.
    assert "next tier" not in joined


def test_a_bound_hardware_cannot_fix_wins_a_tie_against_one_it_can():
    # Sessions and write-lock waits both reach `constrained`.
    verdict = derive_verdict(_indicators(active=15, cap=15, waits=200.0))
    assert verdict.state == CONSTRAINED
    assert verdict.binding is not None
    assert verdict.binding.name == BOUND_WRITE_LOCK


def test_hardware_advice_states_its_measured_basis():
    verdict = derive_verdict(_indicators(active=13, cap=15, available_mb=8192.0))
    joined = " ".join(verdict.recommendations)
    assert "13 concurrent conversation(s)" in joined
    # The per-conversation cost is an estimate until the systest run; say so
    # rather than presenting it as a measurement.
    assert "still an estimate" in joined


def test_idle_profiles_are_recommended_before_hardware():
    verdict = derive_verdict(
        _indicators(active=13, cap=15, profiles=3),
        idle_profiles=["hr", "finance"],
    )
    assert verdict.recommendations, "a constrained box must say something"
    assert verdict.recommendations[0].startswith("Retire idle profiles")
    assert "hr, finance" in verdict.recommendations[0]


def test_constrained_reports_and_never_lowers_the_cap():
    """The open question, answered: report-only."""
    before = _indicators(active=15, cap=15)
    verdict = derive_verdict(before)
    assert verdict.state == CONSTRAINED
    assert verdict.indicators.sessions.cap_here == 15
    assert any("owner's call" in rec for rec in verdict.recommendations)


def test_an_unmeasurable_indicator_is_not_a_measured_zero():
    indicators = _indicators(active=1, cap=15)
    indicators.memory = MemoryLoad()
    indicators.contention = WriteContention(available=False)
    indicators.latency = TurnLatency()
    verdict = derive_verdict(indicators)
    assert verdict.state == COMFORTABLE
    names = {bound.name for bound in verdict.bounds}
    assert names == {BOUND_SESSIONS}
    payload = capacity.as_dict(verdict)
    assert payload["indicators"]["available_mb"] is None
    assert payload["indicators"]["write_lock_waits_per_hour"] is None


# ── Box-wide accounting across profiles ─────────────────────────────────────


def _write_registry(home: Path, count: int, cap: int | None) -> None:
    (home / "runtime").mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "session_id": f"{home.name}-{i}",
            "surface": "gateway:telegram",
            "pid": os.getpid(),
            "started_at": time.time(),
        }
        for i in range(count)
    ]
    active_sessions._write_entries(home / "runtime" / "active_sessions.json", entries)
    config = {} if cap is None else {"max_concurrent_sessions": cap}
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")


def test_active_sessions_are_summed_across_profiles_under_one_gateway(
    tmp_path, monkeypatch
):
    """The registry is profile-local; the RAM it protects is not.

    Three profiles at 6 live conversations each would each report `6 / 15` while
    the box actually carries 18 — the reading that matters is the sum.
    """
    default_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    _write_registry(default_home, 6, 15)
    for name in ("hr", "finance"):
        _write_registry(default_home / "profiles" / name, 6, 15)

    load = capacity.collect_session_load({"max_concurrent_sessions": 15})
    assert load.active_total == 18
    assert load.per_profile == {"default": 6, "finance": 6, "hr": 6}
    assert load.profiles_seen == 3
    assert load.cap_here == 15
    # What the box can be *asked* to hold: 3 profiles × 15.
    assert load.cap_box_wide == 45


def test_an_uncapped_profile_makes_the_box_cap_unknown_not_understated(
    tmp_path, monkeypatch
):
    default_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    _write_registry(default_home, 2, 15)
    _write_registry(default_home / "profiles" / "hr", 3, None)

    load = capacity.collect_session_load({"max_concurrent_sessions": 15})
    assert load.active_total == 5
    assert load.cap_box_wide is None
    assert load.ratio is None
    # And a ratio-less reading cannot invent a session verdict.
    verdict = derive_verdict(CapacityIndicators(sessions=load))
    assert BOUND_SESSIONS not in {bound.name for bound in verdict.bounds}


def test_reading_another_profiles_registry_prunes_dead_leases_without_writing(
    tmp_path, monkeypatch
):
    home = tmp_path / "other"
    _write_registry(home, 1, 5)
    path = home / "runtime" / "active_sessions.json"
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    entries.append(
        {
            "session_id": "dead",
            "surface": "cli",
            "pid": 99999999,
            "started_at": time.time(),
        }
    )
    active_sessions._write_entries(path, entries)
    monkeypatch.setattr("gateway.status._pid_exists", lambda pid: int(pid) != 99999999)
    before = path.read_bytes()

    live = active_sessions.read_registry_for_home(home)

    assert [entry["session_id"] for entry in live] == ["other-0"]
    # The owner reclaims its own file; a reader must not take its lock.
    assert path.read_bytes() == before


# ── Real write-lock contention, measured through SQLite ─────────────────────


def _session_db(tmp_path, monkeypatch):
    from hermes_state import SessionDB

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    return SessionDB(db_path=tmp_path / "state.db")


def test_an_idle_database_records_no_write_lock_waits(tmp_path, monkeypatch):
    db = _session_db(tmp_path, monkeypatch)
    try:
        for i in range(20):
            db.set_meta(f"k{i}", "v")
        totals = db.read_write_contention()
        assert totals["events"] == 0.0
        assert totals["waited_s"] == 0.0
    finally:
        db.close()


def test_two_writers_contending_are_counted_and_timed(tmp_path, monkeypatch):
    """Real contention, not a simulated one: a second connection holds the
    WAL write lock while ``SessionDB`` tries to write."""
    db = _session_db(tmp_path, monkeypatch)
    holder = sqlite3.connect(
        str(tmp_path / "state.db"), isolation_level=None, check_same_thread=False
    )
    released = threading.Event()
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute(
            "INSERT INTO state_meta (key, value) VALUES ('held', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )

        def _release():
            # Held long enough to force real retries, well inside the retry
            # budget (15 attempts × 20-150 ms) so the write still succeeds.
            time.sleep(0.5)
            holder.commit()
            released.set()

        thread = threading.Thread(target=_release, daemon=True)
        thread.start()
        db.set_meta("contended", "yes")  # must wait, retry, then succeed
        thread.join(timeout=10)
        assert released.is_set()

        totals = db.read_write_contention()
        assert totals["events"] >= 1, "a real wait must be counted"
        assert totals["waited_s"] > 0.0, "the wait must be timed, not just counted"
        assert totals["exhausted"] == 0.0
        assert db.get_meta("contended") == "yes"
    finally:
        holder.close()
        db.close()


def test_contention_totals_ignore_buckets_outside_the_window(tmp_path, monkeypatch):
    from hermes_state import SessionDB

    db = _session_db(tmp_path, monkeypatch)
    try:
        now = time.time()
        old_bucket = str(int((now - 300000) // SessionDB._CONTENTION_BUCKET_S))
        new_bucket = str(int(now // SessionDB._CONTENTION_BUCKET_S))
        db.set_meta(
            SessionDB._CONTENTION_META_KEY,
            json.dumps(
                {
                    "buckets": {
                        old_bucket: {"events": 99, "waited_s": 9.9, "exhausted": 1},
                        new_bucket: {"events": 3, "waited_s": 0.3, "exhausted": 0},
                        "not-a-bucket": {"events": 5},
                    }
                }
            ),
        )
        totals = db.read_write_contention(window_s=3600.0, now=now)
        assert totals["events"] == 3.0
        assert round(totals["waited_s"], 3) == 0.3
    finally:
        db.close()


def test_contention_rate_is_per_hour_over_the_window():
    contention = WriteContention(events=48.0, window_s=86400.0)
    assert round(contention.per_hour, 2) == 2.0


# ── Turn latency, derived from the transcript ──────────────────────────────


def test_turn_latency_pairs_a_reply_with_the_message_that_asked_for_it(
    tmp_path, monkeypatch
):
    db = _session_db(tmp_path, monkeypatch)
    try:
        base = time.time()
        db.create_session("s1", "Test")
        # Two turns: 2s and 30s. The trailing assistant row has no user before
        # it in the same session, so it must not become a sample.
        db.append_message("s1", "user", "a", timestamp=base)
        db.append_message("s1", "assistant", "b", timestamp=base + 2)
        db.append_message("s1", "user", "c", timestamp=base + 10)
        db.append_message("s1", "assistant", "d", timestamp=base + 40)
        db.append_message("s1", "assistant", "e", timestamp=base + 41)

        samples = db.recent_turn_latencies_s(window_s=3600.0, now=base + 60)
        assert sorted(samples) == [2.0, 30.0]
        assert capacity._percentile(sorted(samples), 0.50) == 2.0
        assert capacity._percentile(sorted(samples), 0.95) == 30.0
    finally:
        db.close()


def test_turn_latency_ignores_samples_outside_the_window(tmp_path, monkeypatch):
    db = _session_db(tmp_path, monkeypatch)
    try:
        now = time.time()
        db.create_session("s1", "Test")
        db.append_message("s1", "user", "old", timestamp=now - 100000)
        db.append_message("s1", "assistant", "old", timestamp=now - 99000)
        db.append_message("s1", "user", "new", timestamp=now - 10)
        db.append_message("s1", "assistant", "new", timestamp=now - 5)
        assert db.recent_turn_latencies_s(window_s=3600.0, now=now) == [5.0]
    finally:
        db.close()


def test_percentiles_of_an_empty_sample_are_unknown():
    assert capacity._percentile([], 0.5) is None
    assert capacity._percentile([4.0], 0.95) == 4.0


# ── Cost of collection ─────────────────────────────────────────────────────


def test_collection_is_cheap_enough_to_run_on_demand(tmp_path, monkeypatch):
    """Runs on every `hermes status` and in the digest, so it must not be a
    monitoring sweep: a handful of small reads, well under a second."""
    default_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    _write_registry(default_home, 3, 15)
    _write_registry(default_home / "profiles" / "hr", 2, 15)

    started = time.monotonic()
    indicators = capacity.collect_indicators({"max_concurrent_sessions": 15})
    elapsed = time.monotonic() - started

    assert indicators.sessions.active_total == 5
    assert indicators.profile_count == 2
    assert elapsed < 5.0, f"collection took {elapsed:.2f}s"


# ── Rendering contracts the surfaces depend on ─────────────────────────────


def test_summary_line_reports_unknowns_as_unknown():
    indicators = _indicators(active=2, cap=15)
    indicators.memory = MemoryLoad()
    indicators.contention = WriteContention(available=False)
    line = capacity.summary_line(derive_verdict(indicators))
    assert "memory unknown" in line
    assert "write-lock waits unknown" in line


def test_digest_lines_lead_with_the_reading_then_the_verdict():
    verdict = derive_verdict(_indicators(active=14, cap=15))
    lines = capacity.digest_lines(verdict)
    assert lines[0].startswith("Active conversations 14 / ~15")
    assert lines[1].startswith("Headroom: constrained")
    assert any("max_concurrent_sessions" in line for line in lines[2:])


def test_as_dict_carries_the_binding_constraint_and_its_fixability():
    payload = capacity.as_dict(derive_verdict(_indicators(active=2, cap=15, waits=200.0)))
    assert payload["state"] == CONSTRAINED
    assert payload["binding_constraint"]["name"] == BOUND_WRITE_LOCK
    assert payload["binding_constraint"]["hardware_helps"] is False
    assert payload["indicators"]["cap_box_wide"] == 15
    assert payload["recommendations"]
