"""FG-31 — capacity headroom: where the owner stands before things get slow.

Phase 6 made the access model serve hundreds of registered people. It did not
change what one box can do *at once*. Two things bound that, and they have
different fixes:

* **memory** — every active conversation holds a live agent in RAM to keep its
  prompt cache warm, so RAM tracks concurrent conversations, not user count;
* **SQLite's single writer** — simultaneous writes serialise, which costs
  latency and never correctness.

The second one matters most for the advice: a bigger box does not fix it. So
this module does not report a percentage — it derives one verdict and **names
the bound that is binding**, because "memory, driven by 9 concurrent
conversations" tells the owner what to do and "78%" does not.

Every indicator comes from something already shipped: the active-session
leases, ``psutil`` for RSS/available memory, the write-retry accounting in
``SessionDB``, transcript timestamps for latency, and the profiles directory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

log = logging.getLogger(__name__)

COMFORTABLE = "comfortable"
WATCH = "watch"
CONSTRAINED = "constrained"

_SEVERITY = {COMFORTABLE: 0, WATCH: 1, CONSTRAINED: 2}

#: Bound labels. Used in the verdict text and asserted by the tests, so the
#: "which bound is binding" contract is one vocabulary rather than prose.
BOUND_SESSIONS = "concurrent conversations"
BOUND_MEMORY = "memory"
BOUND_WRITE_LOCK = "write-lock waits"
BOUND_LATENCY = "turn latency"

#: Bounds a bigger box cannot fix. The single-writer bound is serialisation:
#: more RAM and more cores do not remove it, the runtime scale-out work does.
_HARDWARE_CANNOT_FIX = {BOUND_WRITE_LOCK}


@dataclass(frozen=True)
class CapacityThresholds:
    """Verdict thresholds. Conservative until calibrated on the systest box.

    Deliberately in ``config.yaml`` (``capacity:``) and never environment
    variables: these are behavioural settings, not credentials.
    """

    #: Peak share of the concurrency cap that reads as approaching the bound.
    watch_session_ratio: float = 0.60
    constrained_session_ratio: float = 0.85
    #: RAM a live conversation costs. PROVISIONAL — the systest calibration run
    #: exists to replace this number, and the verdict says so while it is
    #: unmeasured, rather than presenting an estimate as a measurement.
    conversation_cost_mb: float = 250.0
    #: RAM one more profile costs (a gateway of its own, ~150 MB measured).
    profile_slab_mb: float = 150.0
    #: Working margin the box should keep free beyond one more profile.
    memory_margin_mb: float = 512.0
    #: Write-lock waits per hour. Rare waits are normal on a shared state.db;
    #: routine waits are the serialisation bound biting.
    watch_contention_per_hour: float = 6.0
    constrained_contention_per_hour: float = 30.0
    #: Reply latency the owner actually experiences, p95.
    watch_p95_s: float = 25.0
    constrained_p95_s: float = 60.0
    #: Trailing window for contention and latency.
    window_s: float = 86400.0

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "CapacityThresholds":
        section = {}
        if isinstance(config, dict):
            raw = config.get("capacity")
            if isinstance(raw, dict):
                section = raw
        values: Dict[str, float] = {}
        for name in cls.__dataclass_fields__:  # noqa: F821 - dataclass API
            if name not in section:
                continue
            try:
                values[name] = float(section[name])
            except (TypeError, ValueError):
                log.warning(
                    "Ignoring invalid capacity.%s=%r (expected a number)",
                    name,
                    section[name],
                )
        return cls(**values)


@dataclass
class SessionLoad:
    """Live conversations, box-wide and per profile."""

    active_total: int = 0
    per_profile: Dict[str, int] = field(default_factory=dict)
    #: The cap enforced in *this* profile. The registry is profile-local, so
    #: each profile enforces its own — see ``cap_box_wide``.
    cap_here: Optional[int] = None
    #: Sum of every profile's cap: what the box can be asked to hold at once.
    #: ``None`` when any profile leaves the cap unset (i.e. unbounded).
    cap_box_wide: Optional[int] = None
    profiles_seen: int = 0

    @property
    def ratio(self) -> Optional[float]:
        if not self.cap_box_wide:
            return None
        return self.active_total / float(self.cap_box_wide)


@dataclass
class MemoryLoad:
    available_mb: Optional[float] = None
    total_mb: Optional[float] = None
    hermes_rss_mb: Optional[float] = None
    by_process: Dict[str, float] = field(default_factory=dict)

    @property
    def measured(self) -> bool:
        return self.available_mb is not None


@dataclass
class WriteContention:
    events: float = 0.0
    waited_s: float = 0.0
    exhausted: float = 0.0
    window_s: float = 86400.0
    available: bool = True

    @property
    def per_hour(self) -> float:
        hours = max(self.window_s / 3600.0, 1.0 / 3600.0)
        return self.events / hours


@dataclass
class TurnLatency:
    samples: int = 0
    p50_s: Optional[float] = None
    p95_s: Optional[float] = None


@dataclass
class CapacityIndicators:
    sessions: SessionLoad = field(default_factory=SessionLoad)
    memory: MemoryLoad = field(default_factory=MemoryLoad)
    contention: WriteContention = field(default_factory=WriteContention)
    latency: TurnLatency = field(default_factory=TurnLatency)
    profile_count: int = 1
    collected_at: float = field(default_factory=time.time)
    #: Indicators that could not be read, by name, so a surface can say
    #: "unknown" instead of implying a measured zero.
    unavailable: List[str] = field(default_factory=list)


@dataclass
class Bound:
    """One capacity bound, its state, and whether hardware can move it."""

    name: str
    state: str
    reason: str
    #: 0..1+ share of the way to `constrained`, for tie-breaking only.
    pressure: float
    hardware_helps: bool = True


@dataclass
class CapacityVerdict:
    state: str
    binding: Optional[Bound]
    bounds: List[Bound]
    recommendations: List[str]
    indicators: CapacityIndicators

    def headline(self) -> str:
        """One line: the verdict and the constraint that produced it."""
        if self.binding is None or self.state == COMFORTABLE:
            return f"Headroom: {self.state}."
        return f"Headroom: {self.state} — {self.binding.name}, {self.binding.reason}."


# ── Collection ───────────────────────────────────────────────────────────────


def _profile_homes() -> List[Tuple[str, Path]]:
    """``(name, home)`` for every profile on the box, default first."""
    from hermes_cli.profiles import _get_default_hermes_home, _get_profiles_root

    homes: List[Tuple[str, Path]] = []
    default_home = _get_default_hermes_home()
    if default_home.is_dir():
        homes.append(("default", default_home))
    root = _get_profiles_root()
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and entry.name != "default":
                homes.append((entry.name, entry))
    return homes


def _profile_cap(home: Path) -> Tuple[Optional[int], bool]:
    """``(cap, known)`` for a profile, read from its own ``config.yaml``."""
    from hermes_cli.active_sessions import resolve_max_concurrent_sessions

    path = home / "config.yaml"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return None, True
    except Exception as exc:
        log.debug("capacity: could not read %s: %s", path, exc)
        return None, False
    if not isinstance(config, dict):
        return None, True
    return resolve_max_concurrent_sessions(config), True


def collect_session_load(config: Optional[Dict[str, Any]] = None) -> SessionLoad:
    """Live conversations across every profile, not just this one.

    The lease registry lives under each profile's home, so a per-profile read
    understates the box: three profiles at 6 live conversations each would each
    report 6 while the box carries 18 — and RAM, the resource the cap protects,
    is box-wide.
    """
    from hermes_cli.active_sessions import (
        read_registry_for_home,
        resolve_max_concurrent_sessions,
    )
    from hermes_constants import get_hermes_home

    load = SessionLoad()
    here = Path(get_hermes_home()).resolve()
    load.cap_here = resolve_max_concurrent_sessions(config or {})
    caps: List[int] = []
    every_cap_known = True
    for name, home in _profile_homes():
        try:
            entries = read_registry_for_home(home)
        except Exception as exc:
            log.debug("capacity: lease read failed for %s: %s", name, exc)
            every_cap_known = False
            continue
        load.per_profile[name] = len(entries)
        load.active_total += len(entries)
        load.profiles_seen += 1
        if home.resolve() == here and load.cap_here is not None:
            cap, known = load.cap_here, True
        else:
            cap, known = _profile_cap(home)
        if not known:
            every_cap_known = False
        elif cap is None:
            # Cap disabled for this profile: the box is unbounded, and a sum
            # would understate that.
            every_cap_known = False
        else:
            caps.append(cap)
    if caps and every_cap_known:
        load.cap_box_wide = sum(caps)
    return load


#: Process-name fragments to the slab they represent. Matched against the
#: command line, so a profile's gateway and the console are counted separately.
_PROCESS_LABELS: Sequence[Tuple[str, str]] = (
    ("gateway", "gateway"),
    ("web_server", "console"),
    ("dashboard", "console"),
    ("embed", "embedding server"),
    ("hermes", "other hermes"),
)


def collect_memory_load() -> MemoryLoad:
    load = MemoryLoad()
    try:
        import psutil
    except ImportError:
        return load
    try:
        virtual = psutil.virtual_memory()
        load.available_mb = virtual.available / 1048576.0
        load.total_mb = virtual.total / 1048576.0
    except Exception as exc:
        log.debug("capacity: virtual_memory unavailable: %s", exc)
        return load
    total_rss = 0.0
    for proc in psutil.process_iter(["cmdline", "memory_info"]):
        try:
            info = proc.info
            cmdline = " ".join(info.get("cmdline") or []).lower()
            mem = info.get("memory_info")
            if not cmdline or mem is None or "hermes" not in cmdline:
                continue
            rss_mb = mem.rss / 1048576.0
        except Exception:
            continue
        total_rss += rss_mb
        for fragment, label in _PROCESS_LABELS:
            if fragment in cmdline:
                load.by_process[label] = load.by_process.get(label, 0.0) + rss_mb
                break
    load.hermes_rss_mb = total_rss
    return load


def collect_write_contention(
    thresholds: CapacityThresholds, *, now: Optional[float] = None
) -> WriteContention:
    """Write-lock waits recorded by ``SessionDB``'s retry path."""
    from hermes_state import SessionDB

    result = WriteContention(window_s=thresholds.window_s)
    db: Optional[SessionDB] = None
    try:
        # Read-only: an indicator must never take the write lock it is
        # measuring, nor initialise a schema on a box that has none yet.
        db = SessionDB(read_only=True)
        totals = db.read_write_contention(window_s=thresholds.window_s, now=now)
        result.events = totals.get("events", 0.0)
        result.waited_s = totals.get("waited_s", 0.0)
        result.exhausted = totals.get("exhausted", 0.0)
    except Exception as exc:
        log.debug("capacity: contention read failed: %s", exc)
        result.available = False
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return result


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, max(0, int(round(pct * (len(ordered) - 1)))))
    return ordered[index]


def collect_turn_latency(
    thresholds: CapacityThresholds, *, now: Optional[float] = None
) -> TurnLatency:
    from hermes_state import SessionDB

    latency = TurnLatency()
    db: Optional[SessionDB] = None
    try:
        db = SessionDB(read_only=True)
        samples = db.recent_turn_latencies_s(window_s=thresholds.window_s, now=now)
    except Exception as exc:
        log.debug("capacity: latency read failed: %s", exc)
        return latency
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    latency.samples = len(samples)
    latency.p50_s = _percentile(samples, 0.50)
    latency.p95_s = _percentile(samples, 0.95)
    return latency


def collect_indicators(
    config: Optional[Dict[str, Any]] = None,
    *,
    thresholds: Optional[CapacityThresholds] = None,
    now: Optional[float] = None,
) -> CapacityIndicators:
    """Every indicator, each degrading to "unavailable" on its own.

    Cheap by construction: four small reads (one JSON file per profile, one
    ``psutil`` sweep, two indexed queries), so it can run on the digest cadence
    and on demand without touching the turn path.
    """
    thresholds = thresholds or CapacityThresholds.from_config(config)
    indicators = CapacityIndicators(collected_at=now or time.time())
    try:
        indicators.sessions = collect_session_load(config)
    except Exception as exc:
        log.debug("capacity: session load unavailable: %s", exc)
        indicators.unavailable.append("active conversations")
    indicators.memory = collect_memory_load()
    if not indicators.memory.measured:
        indicators.unavailable.append("memory")
    indicators.contention = collect_write_contention(thresholds, now=now)
    if not indicators.contention.available:
        indicators.unavailable.append("write-lock waits")
    indicators.latency = collect_turn_latency(thresholds, now=now)
    if not indicators.latency.samples:
        indicators.unavailable.append("turn latency")
    try:
        indicators.profile_count = max(1, len(_profile_homes()))
    except Exception as exc:
        log.debug("capacity: profile count unavailable: %s", exc)
    return indicators


# ── Verdict ──────────────────────────────────────────────────────────────────


def _session_bound(
    load: SessionLoad, thresholds: CapacityThresholds
) -> Optional[Bound]:
    ratio = load.ratio
    if ratio is None:
        return None
    detail = (
        f"{load.active_total} of {load.cap_box_wide} "
        f"across {max(1, load.profiles_seen)} profile(s)"
    )
    pressure = ratio / thresholds.constrained_session_ratio
    if ratio >= thresholds.constrained_session_ratio:
        return Bound(BOUND_SESSIONS, CONSTRAINED, f"{detail} — at the cap", pressure)
    if ratio >= thresholds.watch_session_ratio:
        return Bound(BOUND_SESSIONS, WATCH, f"{detail} — approaching the cap", pressure)
    return Bound(BOUND_SESSIONS, COMFORTABLE, detail, pressure)


def _memory_bound(
    memory: MemoryLoad,
    load: SessionLoad,
    thresholds: CapacityThresholds,
) -> Optional[Bound]:
    if memory.available_mb is None:
        return None
    #: What the box must keep free to absorb one more profile and its work.
    need = thresholds.profile_slab_mb + thresholds.memory_margin_mb
    driver = f"{load.active_total} concurrent conversation(s)"
    detail = f"{memory.available_mb / 1024.0:.1f} GB available, driven by {driver}"
    # Pressure is measured against the *constrained* line, as every other bound
    # is, so the tie-break compares like with like.
    pressure = thresholds.memory_margin_mb / max(memory.available_mb, 1.0)
    if memory.available_mb < thresholds.memory_margin_mb:
        return Bound(BOUND_MEMORY, CONSTRAINED, f"{detail} — below the working margin", pressure)
    if memory.available_mb < need:
        return Bound(
            BOUND_MEMORY,
            WATCH,
            f"{detail} — under the cost of one more profile plus margin",
            pressure,
        )
    return Bound(BOUND_MEMORY, COMFORTABLE, detail, pressure)


def _contention_bound(
    contention: WriteContention, thresholds: CapacityThresholds
) -> Optional[Bound]:
    if not contention.available:
        return None
    per_hour = contention.per_hour
    detail = (
        f"{per_hour:.1f} write-lock wait(s)/hour, "
        f"{contention.waited_s:.1f}s waited over "
        f"{contention.window_s / 3600.0:.0f}h"
    )
    pressure = per_hour / max(thresholds.constrained_contention_per_hour, 1e-6)
    if per_hour >= thresholds.constrained_contention_per_hour:
        return Bound(
            BOUND_WRITE_LOCK,
            CONSTRAINED,
            f"{detail} — writes are serialising routinely",
            pressure,
            hardware_helps=False,
        )
    if per_hour >= thresholds.watch_contention_per_hour:
        return Bound(
            BOUND_WRITE_LOCK,
            WATCH,
            f"{detail} — waits are becoming routine",
            pressure,
            hardware_helps=False,
        )
    return Bound(BOUND_WRITE_LOCK, COMFORTABLE, detail, pressure, hardware_helps=False)


def _latency_bound(
    latency: TurnLatency, thresholds: CapacityThresholds
) -> Optional[Bound]:
    if latency.p95_s is None:
        return None
    detail = f"p95 {latency.p95_s:.1f}s over {latency.samples} turn(s)"
    pressure = latency.p95_s / max(thresholds.constrained_p95_s, 1e-6)
    if latency.p95_s >= thresholds.constrained_p95_s:
        return Bound(BOUND_LATENCY, CONSTRAINED, f"{detail} — replies are slow", pressure)
    if latency.p95_s >= thresholds.watch_p95_s:
        return Bound(BOUND_LATENCY, WATCH, f"{detail} — replies are lengthening", pressure)
    return Bound(BOUND_LATENCY, COMFORTABLE, detail, pressure)


def derive_verdict(
    indicators: CapacityIndicators,
    thresholds: Optional[CapacityThresholds] = None,
    *,
    config: Optional[Dict[str, Any]] = None,
    idle_profiles: Optional[Sequence[str]] = None,
) -> CapacityVerdict:
    """One verdict, naming the bound that produced it.

    When two bounds sit at the same state the more pressed one is named, and a
    bound hardware cannot fix wins a tie — recommending an upgrade that cannot
    help would be worse than saying nothing.
    """
    thresholds = thresholds or CapacityThresholds.from_config(config)
    bounds = [
        bound
        for bound in (
            _session_bound(indicators.sessions, thresholds),
            _memory_bound(indicators.memory, indicators.sessions, thresholds),
            _contention_bound(indicators.contention, thresholds),
            _latency_bound(indicators.latency, thresholds),
        )
        if bound is not None
    ]
    state = COMFORTABLE
    binding: Optional[Bound] = None
    for bound in bounds:
        if _SEVERITY[bound.state] > _SEVERITY[state]:
            state, binding = bound.state, bound
        elif (
            binding is not None
            and bound.state == state
            and _SEVERITY[bound.state] > _SEVERITY[COMFORTABLE]
        ):
            if (not bound.hardware_helps and binding.hardware_helps) or (
                bound.hardware_helps == binding.hardware_helps
                and bound.pressure > binding.pressure
            ):
                binding = bound
    return CapacityVerdict(
        state=state,
        binding=binding,
        bounds=bounds,
        recommendations=_recommendations(
            state, binding, indicators, thresholds, idle_profiles or []
        ),
        indicators=indicators,
    )


def _tier_advice(indicators: CapacityIndicators, thresholds: CapacityThresholds) -> str:
    """Hardware advice with the measured basis stated, or its absence stated."""
    profiles = indicators.profile_count
    active = indicators.sessions.active_total
    need_mb = (
        profiles * thresholds.profile_slab_mb + active * thresholds.conversation_cost_mb
    )
    basis = (
        f"{profiles} profile(s) + {active} concurrent conversation(s) ≈ "
        f"{need_mb / 1024.0:.1f} GB of agent working set"
    )
    return (
        f"Size the next tier from the load, not the user count: {basis}. "
        "The per-conversation figure is still an estimate — the systest "
        "calibration run replaces it with a measurement."
    )


def _recommendations(
    state: str,
    binding: Optional[Bound],
    indicators: CapacityIndicators,
    thresholds: CapacityThresholds,
    idle: Sequence[str],
) -> List[str]:
    if state == COMFORTABLE or binding is None:
        return []
    out: List[str] = []
    if binding.name == BOUND_WRITE_LOCK:
        # Said first and plainly: this is the one an upgrade cannot move.
        out.append(
            "A bigger box does not fix this. Write-lock waits are SQLite's "
            "single-writer bound — the fix is the runtime scale-out work "
            "(sharded workers, moving the session store off SQLite), not "
            "hardware."
        )
    # The cheap actions first, and only the ones this box can actually take.
    if idle:
        out.append(
            "Retire idle profiles — "
            + ", ".join(idle)
            + f" (each costs about {thresholds.profile_slab_mb:.0f} MB of gateway)."
        )
    if indicators.profile_count > 1:
        gateways = indicators.memory.by_process.get("gateway")
        if gateways is None or gateways > thresholds.profile_slab_mb * 1.5:
            out.append(
                "Consolidate to one multiplexed gateway if that is not enabled "
                f"yet — it removes about {thresholds.profile_slab_mb:.0f} MB per "
                "profile."
            )
    if binding.hardware_helps:
        out.append(_tier_advice(indicators, thresholds))
    if binding.name == BOUND_SESSIONS:
        out.append(
            "Or reduce concurrency deliberately: lowering "
            "`max_concurrent_sessions` in config.yaml refuses new sessions with "
            "a clear message instead of slowing everyone down. Reported, never "
            "applied automatically — serving fewer people is the owner's call."
        )
    return out


# ── Rendering ────────────────────────────────────────────────────────────────


def _fmt_optional_gb(mb: Optional[float]) -> str:
    return "unknown" if mb is None else f"{mb / 1024.0:.1f} GB"


def summary_line(verdict: CapacityVerdict) -> str:
    """The single line for `hermes status` and the digest."""
    ind = verdict.indicators
    sessions = f"{ind.sessions.active_total}"
    if ind.sessions.cap_box_wide:
        sessions += f" / ~{ind.sessions.cap_box_wide}"
    memory = _fmt_optional_gb(ind.memory.available_mb)
    if ind.memory.total_mb:
        memory += f" free of {_fmt_optional_gb(ind.memory.total_mb)}"
    waits = (
        "no write-lock waits"
        if ind.contention.available and ind.contention.events == 0
        else f"{ind.contention.per_hour:.1f} write-lock waits/h"
        if ind.contention.available
        else "write-lock waits unknown"
    )
    return f"Active conversations {sessions} · memory {memory} · {waits}"


def digest_lines(verdict: CapacityVerdict) -> List[str]:
    """Plain-text lines for the FG-29 weekly digest."""
    lines = [summary_line(verdict), verdict.headline()]
    lines.extend(f"  {rec}" for rec in verdict.recommendations)
    if verdict.indicators.unavailable:
        lines.append(
            "  Not measured: " + ", ".join(sorted(verdict.indicators.unavailable))
        )
    return lines


def as_dict(verdict: CapacityVerdict) -> Dict[str, Any]:
    """JSON shape for the agent-home card (D20) and the API."""
    ind = verdict.indicators
    return {
        "state": verdict.state,
        "headline": verdict.headline(),
        "summary": summary_line(verdict),
        "binding_constraint": None
        if verdict.binding is None
        else {
            "name": verdict.binding.name,
            "reason": verdict.binding.reason,
            "hardware_helps": verdict.binding.hardware_helps,
        },
        "bounds": [
            {
                "name": bound.name,
                "state": bound.state,
                "reason": bound.reason,
                "hardware_helps": bound.hardware_helps,
            }
            for bound in verdict.bounds
        ],
        "recommendations": list(verdict.recommendations),
        "indicators": {
            "active_conversations": ind.sessions.active_total,
            "per_profile": dict(ind.sessions.per_profile),
            "cap_here": ind.sessions.cap_here,
            "cap_box_wide": ind.sessions.cap_box_wide,
            "available_mb": ind.memory.available_mb,
            "total_mb": ind.memory.total_mb,
            "hermes_rss_mb": ind.memory.hermes_rss_mb,
            "by_process": dict(ind.memory.by_process),
            "write_lock_waits_per_hour": ind.contention.per_hour
            if ind.contention.available
            else None,
            "write_lock_waited_s": ind.contention.waited_s
            if ind.contention.available
            else None,
            "turn_p50_s": ind.latency.p50_s,
            "turn_p95_s": ind.latency.p95_s,
            "turn_samples": ind.latency.samples,
            "profile_count": ind.profile_count,
        },
        "unavailable": sorted(ind.unavailable),
        "collected_at": ind.collected_at,
    }


def headroom(
    config: Optional[Dict[str, Any]] = None,
    *,
    idle_profiles: Optional[Sequence[str]] = None,
    now: Optional[float] = None,
) -> CapacityVerdict:
    """Collect and judge in one call — what every surface uses."""
    thresholds = CapacityThresholds.from_config(config)
    indicators = collect_indicators(config, thresholds=thresholds, now=now)
    return derive_verdict(
        indicators, thresholds, config=config, idle_profiles=idle_profiles
    )
