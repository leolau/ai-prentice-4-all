"""Projects runs + playbook engine (design ed.3.2 §4–§7).

This module is the *execution* half of Projects; ``projects_api.py`` is the
HTTP seam over it. Everything here is deliberately built on shipped seams:

- The sequencing engine is the board's: playbook steps become cards with
  ``project_id`` + ``task_links`` parents, and ``recompute_ready()`` owns
  todo → ready promotion. Projects writes zero scheduling logic (§7.1).
- The run's own session — when a playbook has ``mode: 'inline'`` steps —
  spawns through ``agent.seeded_session.spawn_seeded_session()``, the ONLY
  session-spawn path this feature may use (§6).
- Guidance is compiled into the seed prompt at spawn time, never injected
  mid-conversation (§5.1): the system prompt is frozen for the life of a
  conversation and per-conversation caching is sacred.
- Toolsets/skills are a **narrowing filter, never a grant** (§4.1): the
  effective set is the intersection with what the host profile enables.

Approvals ride the shipped FG-10 surface (``hermes_cli.human_comms.
NotificationStore``) and run cost reads the shipped C8 ledger
(``hermes_cli.interactions``): a run binds a real trace at start so its
cost is queryable — never stored (§6).
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as _dt
import json
import logging
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

from hermes_cli import kanban_db, projects_db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config (config.yaml ``projects:`` — never env vars, AGENTS.md)
# ---------------------------------------------------------------------------

DEFAULT_MAX_SKILLS = 5
DEFAULT_GUIDANCE_MAX_DIRECTIVES = 20
DEFAULT_GUIDANCE_MAX_CHARS = 4000
DEFAULT_BRIEF_MAX_CHARS = 1200

_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def projects_runtime_config() -> Dict[str, Any]:
    """The ``projects:`` section of config.yaml with fail-open defaults."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    raw: Dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        section = cfg.get("projects")
        if isinstance(section, dict):
            raw = section
    except Exception:  # noqa: BLE001 — config is advisory here
        raw = {}
    out = {
        "max_skills": _int_or(raw.get("max_skills"), DEFAULT_MAX_SKILLS),
        "guidance_max_directives": _int_or(
            raw.get("guidance_max_directives"), DEFAULT_GUIDANCE_MAX_DIRECTIVES
        ),
        "guidance_max_chars": _int_or(
            raw.get("guidance_max_chars"), DEFAULT_GUIDANCE_MAX_CHARS
        ),
        "brief_max_chars": _int_or(
            raw.get("brief_max_chars"), DEFAULT_BRIEF_MAX_CHARS
        ),
    }
    _CONFIG_CACHE = out
    return out


def _int_or(value: Any, default: int) -> int:
    try:
        n = int(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _epoch_day(ts: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "?"


# ---------------------------------------------------------------------------
# §4.1 — toolsets / skills: intersection, never union
# ---------------------------------------------------------------------------

def parse_csv_field(value: Any) -> List[str]:
    """A project's ``toolsets``/``skills`` columns are comma lists."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(",")
    out: List[str] = []
    for item in items:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


def resolve_toolsets(
    requested: Sequence[str], enabled: Sequence[str]
) -> tuple[List[str], List[str]]:
    """``host profile enabled ∩ project requested`` (§4.1 rule 1).

    Returns ``(effective, dropped)``. A name the host profile does not
    enable is dropped — never granted — and the drop must be recorded on
    the run, because a silently narrower run looks like a broken agent.
    Empty ``requested`` means "whatever the host profile normally does".
    """
    if not requested:
        return list(enabled), []
    enabled_set = set(enabled)
    effective = [t for t in requested if t in enabled_set]
    dropped = [t for t in requested if t not in enabled_set]
    return effective, dropped


def resolve_skills(
    requested: Sequence[str],
    available: Sequence[str],
    max_skills: int = DEFAULT_MAX_SKILLS,
) -> tuple[List[str], List[str], bool]:
    """Skills are prompt bytes: cap + resolve through the host loader
    (§4.1 rule 2). Returns ``(effective, dropped, truncated)``."""
    if not requested:
        return [], [], False
    available_set = set(available)
    kept = [s for s in requested if s in available_set]
    dropped = [s for s in requested if s not in available_set]
    truncated = len(kept) > max_skills
    return kept[:max_skills], dropped, truncated


# ---------------------------------------------------------------------------
# §5.2 — the compiled guidance block
# ---------------------------------------------------------------------------

def compile_guidance(
    project: projects_db.Project,
    *,
    run_no: int,
    outputs: List[dict],
    deliveries_by_output: Dict[str, List[dict]],
    sample_links: List[dict],
    directives: List[dict],
    last_run: Optional[dict] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """Assemble the §5.2 block in its fixed order.

    Hard rules enforced here:
    - empty optional sections are omitted **with their heading**;
    - outputs come before instructions and are never truncated —
      truncation eats the brief and the directives instead;
    - directives are capped, newest first, dated and attributed;
    - the compiled block is capped at ``guidance_max_chars``.
    """
    cfg = cfg or projects_runtime_config()
    lines: List[str] = []

    lines.append(f"## Project: {project.name}")
    lines.append(f"Goal: {project.goal}")
    cadence = "one-off" if project.cadence == "one_off" else "repeatable"
    lines.append(
        f"Run {run_no} · {cadence} · autonomy: {project.autonomy or 'supervised'}"
    )
    dod = (project.definition_of_done or "").strip()
    lines.append(
        f"Definition of done: {dod or 'all required outputs accepted'}"
    )

    brief_lines: List[str] = []
    description = (project.description or "").strip()
    if description:
        limit = cfg["brief_max_chars"]
        if len(description) > limit:
            description = (
                description[:limit].rstrip()
                + f"\n…[truncated — see `hermes projects show {project.slug}` "
                "for the rest]"
            )
        brief_lines = ["", "### Brief", description]

    audience_lines: List[str] = []
    audience = (project.target_audience or "").strip()
    if audience:
        audience_lines = ["", "### Audience", audience]

    output_lines: List[str] = ["", "### Outputs expected of this run"]
    any_output = False
    for out in outputs:
        any_output = True
        deliveries = deliveries_by_output.get(out["id"]) or []
        delivered_run = next(
            (d for d in deliveries if d.get("run_no")), None
        )
        flags: List[str] = []
        if out.get("required"):
            flags.append("required")
        if out.get("recurring"):
            flags.append("recurring")
        suffix = f" ({', '.join(flags)})" if flags else ""
        if delivered_run is not None:
            output_lines.append(
                f"- [x] {out['title']} (delivered run {delivered_run['run_no']})"
            )
        else:
            spec = (out.get("spec") or "").strip()
            spec_part = f" — {spec}" if spec else ""
            output_lines.append(f"- [ ] {out['title']}{spec_part}{suffix}")
    if not any_output:
        output_lines = []

    sample_lines: List[str] = []
    if sample_links:
        sample_lines = ["", "### Samples to match"]
        for link in sample_links:
            label = link.get("label") or link.get("ref")
            sample_lines.append(f"- {label} → {link.get('ref')}")

    directive_lines: List[str] = []
    cap = cfg["guidance_max_directives"]
    active = [d for d in directives if d.get("active")]
    active = active[:cap]  # already newest-first from the store
    if active:
        directive_lines = ["", "### Standing instructions (newest first)"]
        for i, d in enumerate(active, 1):
            directive_lines.append(
                f"{i}. {d['body']} [{d.get('author_user_id') or '?'}, "
                f"{_epoch_day(d.get('created_at'))}]"
            )

    retro_lines: List[str] = []
    if last_run and (last_run.get("retro") or "").strip():
        retro_lines = ["", "### What we learnt last run"]
        score = last_run.get("score_user") or last_run.get("score_self")
        score_part = f" (score: {score}/5)" if score else ""
        note = (last_run.get("score_note") or "").strip()
        note_part = f' — "{note}"' if note else ""
        retro_lines.append(
            f"Run {last_run.get('run_no')}:{score_part}{note_part} "
            f"{last_run['retro'].strip()}"
        )

    # Fixed order: header/brief/audience first, outputs before instructions,
    # samples after outputs, then directives, then the retro.
    header = lines
    block_parts = [
        "\n".join(header),
        *[
            "\n".join(part)
            for part in (
                brief_lines,
                audience_lines,
                output_lines,
                sample_lines,
                directive_lines,
                retro_lines,
            )
            if part
        ],
    ]
    block = "\n".join(block_parts)

    # Cap: never the outputs list. Eat the brief, then the directives.
    limit = cfg["guidance_max_chars"]
    if len(block) > limit and brief_lines:
        over = len(block) - limit
        brief_lines = [
            "",
            "### Brief",
            description[: max(0, len(description) - over)].rstrip()
            + "\n…[truncated to fit the guidance budget]",
        ]
        block = _rejoin(
            header, brief_lines, audience_lines, output_lines,
            sample_lines, directive_lines, retro_lines,
        )
    if len(block) > limit and directive_lines:
        while len(block) > limit and len(directive_lines) > 3:
            directive_lines.pop()
        directive_lines.append("…[further standing instructions omitted]")
        block = _rejoin(
            header, brief_lines, audience_lines, output_lines,
            sample_lines, directive_lines, retro_lines,
        )
    return block


def _rejoin(*parts: List[str]) -> str:
    return "\n".join("\n".join(p) for p in parts if p)


# ---------------------------------------------------------------------------
# §7 — playbook instantiation onto the board
# ---------------------------------------------------------------------------

def held_step_keys(steps: Sequence[dict]) -> set:
    """Keys that succeed a ``checkpoint`` step — held until the human
    passes the checkpoint (§7.1)."""
    checkpoints = {s["key"] for s in steps if s.get("checkpoint")}
    return {
        s["key"] for s in steps if set(s.get("depends_on") or []) & checkpoints
    }


def host_profile_name(pconn, project_id: str) -> Optional[str]:
    profiles = projects_db.get_project_profiles(pconn, project_id)
    for p in profiles:
        if p.get("role") == "host":
            return p["profile"]
    return profiles[0]["profile"] if profiles else None


def instantiate_run_cards(
    bconn,
    pconn,
    *,
    project: projects_db.Project,
    run_id: str,
    run_no: int,
    steps: Sequence[dict],
    created_by: str,
) -> Dict[str, Any]:
    """Write every step as a card carrying ``project_id`` + parent links,
    then record the run → card mapping (§7.1). Cards are always CREATED in
    ``triage`` — promotion is a separate, autonomy-aware step — and never
    ``ready``/``running`` at creation (the store would refuse it anyway).

    Returns ``{"cards": {step_key: task_id}, "inline": [step, ...]}``.
    """
    card_ids: Dict[str, str] = {}
    inline_steps: List[dict] = []
    profiles = projects_db.get_project_profiles(pconn, project.id)
    profile_names = {p["profile"] for p in profiles}
    host = host_profile_name(pconn, project.id)

    for step in steps:
        if step.get("mode") == "inline":
            inline_steps.append(step)
            continue
        assignee = step.get("assignee") or host
        if assignee and profile_names and assignee not in profile_names:
            # A bad playbook stalls visibly instead of running under a
            # surprise profile (§7.1).
            raise ValueError(
                f"step {step['key']!r}: assignee {assignee!r} is not one of "
                f"the project's profiles {sorted(profile_names)}"
            )
        body = step.get("body") or ""
        tid = kanban_db.create_task(
            bconn,
            title=step["title"],
            body=(
                f"{body}\n\n(project: {project.slug} · run {run_no} · "
                f"step `{step['key']}`)"
            ).strip(),
            assignee=assignee,
            created_by=created_by,
            triage=True,
            board=project.board_slug or None,
            project_id=project.id,
            owner_user_id=created_by,
            visibility="shared",
        )
        card_ids[step["key"]] = tid

    for step in steps:
        if step.get("mode") == "inline":
            continue
        child = card_ids[step["key"]]
        for dep in step.get("depends_on") or []:
            parent = card_ids.get(dep)
            if parent:
                kanban_db.link_tasks(bconn, parent_id=parent, child_id=child)

    for key, tid in card_ids.items():
        projects_db.link_run_card(pconn, run_id, tid, step_key=key)

    return {"cards": card_ids, "inline": inline_steps}


def _project_in_flight(bconn, project: projects_db.Project) -> int:
    """Cards in ``running`` + ``ready`` — what ``max_in_progress`` caps."""
    tasks = kanban_db.list_tasks(bconn, project_id=project.id)
    return sum(1 for t in tasks if t.status in ("running", "ready"))


def promote_run_cards(
    bconn,
    pconn,
    *,
    project: projects_db.Project,
    run_id: str,
    steps: Sequence[dict],
    autonomy: str,
    held: set,
    force_held: bool = False,
) -> List[str]:
    """Move this run's triage cards to ``todo`` per §4.

    - ``manual``: nothing is ever promoted by a run — a human holds every
      gate. There is no path that silently promotes a manual project.
    - ``supervised``: steps not held by a checkpoint are promoted; the
      checkpoint's successors wait for an explicit continue.
    - ``autonomous``: every step (checkpoints still raise an approval on
      completion — autonomy never waives FG-10).

    ``force_held`` is set by the human "continue" act, which passes the
    checkpoint and releases its successors.

    The ``max_in_progress`` cap is enforced here — the project's own
    promotion step, never by patching the shared dispatcher: count cards in
    running + ready, promote at most up to the cap.
    """
    if autonomy == "manual":
        return []
    by_key = {s["key"]: s for s in steps if s.get("mode") != "inline"}
    run_cards = projects_db.get_run_cards(pconn, run_id)
    key_of = {rc["task_id"]: rc.get("step_key") for rc in run_cards}

    cap = project.max_in_progress
    room = None
    if cap:
        room = max(0, int(cap) - _project_in_flight(bconn, project))
        if room == 0:
            return []

    promoted: List[str] = []
    for rc in run_cards:
        key = key_of.get(rc["task_id"]) or rc.get("step_key")
        if key is None or key not in by_key:
            continue
        if key in held and not force_held:
            continue
        if room is not None and len(promoted) >= room:
            break
        row = bconn.execute(
            "SELECT status FROM tasks WHERE id = ?", (rc["task_id"],)
        ).fetchone()
        if row is None or row["status"] != "triage":
            continue
        if kanban_db.specify_triage_task(bconn, rc["task_id"]):
            promoted.append(rc["task_id"])
    return promoted


# ---------------------------------------------------------------------------
# §6.1 — deliveries, outcome and closure
# ---------------------------------------------------------------------------

def derive_run_outcome(
    pconn, *, project_id: str, run_id: str
) -> tuple[str, str]:
    """Machine-set outcome on close (§6.1 table), from deliveries."""
    outputs = projects_db.get_project_outputs(pconn, project_id)
    deliveries = projects_db.get_output_deliveries(pconn, run_id=run_id)
    delivered_ids = {d["output_id"] for d in deliveries}
    required = [o for o in outputs if o.get("required")]
    required_done = [o for o in required if o["id"] in delivered_ids]
    if required:
        if len(required_done) == len(required):
            return "delivered", (
                f"all {len(required)} required output(s) delivered"
            )
        if required_done:
            missing = ", ".join(
                o["title"] for o in required if o["id"] not in delivered_ids
            )
            return "partial", (
                f"delivered {len(required_done)} of {len(required)} "
                f"required output(s); missing: {missing}"
            )
        return "no_output", (
            "run completed but delivered no required output — the outputs "
            "exist to record that; deliver them or drop them"
        )
    if delivered_ids:
        return "delivered", f"{len(delivered_ids)} output(s) delivered"
    return "done", "no outputs were expected of this run"


def close_run(
    pconn,
    *,
    run: dict,
    status: str = "done",
    summary: Optional[str] = None,
    error: Optional[str] = None,
    outcome: Optional[str] = None,
) -> dict:
    """Close a run: terminal status + outcome; the spawn-time toolset
    prelude in ``summary`` survives an agent-supplied summary."""
    if outcome is None and status == "done":
        _, outcome = derive_run_outcome(pconn, project_id=run["project_id"], run_id=run["id"])
    prelude = (run.get("summary") or "").strip()
    merged = None
    if summary:
        merged = f"{prelude}\n\n{summary}".strip() if prelude else summary
    elif prelude:
        merged = prelude
    projects_db.close_project_run(
        pconn, run["id"], status=status, outcome=outcome,
        summary=merged, error=error,
    )
    return projects_db.get_project_run_by_id(pconn, run["id"])


# ---------------------------------------------------------------------------
# The run's human seams: approvals (FG-10) and cost (C8 ledger)
# ---------------------------------------------------------------------------

def _approval_store(app_store, *, config):
    """Seam over the FG-10 store construction so the default is testable."""
    from hermes_cli.human_comms import NotificationStore

    return NotificationStore(app_store, config=config)


def raise_approval(
    project: projects_db.Project,
    run: dict,
    reason: str,
    *,
    kind: str = "checkpoint",
) -> None:
    """Checkpoint / budget-stop approval through the shipped FG-10 surface.

    Raised with ``reversible=False`` so C6 never auto-answers it (§4: a
    supervised run holds until a human passes the checkpoint). There is no
    fail-open here: a swallowed approval is worse than a failed start, so a
    store failure logs at ERROR and propagates.
    """
    target = project.owner_user_id
    if not target:
        raise RuntimeError(
            f"project '{project.slug}' has no owner_user_id — cannot raise "
            "a run approval"
        )

    async def _raise() -> None:
        from hermes_cli.config import load_config
        from hermes_cli.datastore import get_store

        resolved = load_config() or {}
        app_store = get_store("supabase-app", "prod", config=resolved)
        store = _approval_store(app_store, config=resolved)
        await store.initialize()
        await store.create(
            kind="approval",
            target_user_id=target,
            title=(
                f"Project '{project.name}' run {run.get('run_no')} "
                f"needs you ({kind})"
            ),
            body=reason,
            reversible=False,
            dedupe_key=f"proj:{project.slug}:run:{run.get('run_no')}:{kind}",
        )

    try:
        asyncio.run(_raise())
    except Exception:
        log.error(
            "projects: approval NOT raised for %s run %s (%s) — the "
            "approval surface failed; refusing to continue silently",
            project.slug, run.get("run_no"), reason, exc_info=True,
        )
        raise


_COST_AMOUNT_RE = re.compile(r"amount_usd=([0-9]*\.?[0-9]+)")


def _cost_from_interactions(interactions) -> Optional[float]:
    """Sum a trace's ``cost`` events. The ledger stores no cost column: the
    adapters emit ``kind='cost'`` with ``amount_usd=…`` in the summary."""
    total = 0.0
    seen = False
    for item in interactions:
        if item.kind != "cost":
            continue
        match = _COST_AMOUNT_RE.search(item.summary or "")
        if match:
            seen = True
            total += float(match.group(1))
    return total if seen else None


def _owner_principal(project: projects_db.Project):
    """A read principal scoped to the project owner — the trace's actor."""
    from hermes_cli.access import Principal

    owner = project.owner_user_id or ""
    return Principal(user_id=owner, display=owner, role="member")


def _default_cost_reader(trace_id: str, principal) -> Optional[float]:
    """The shipped reader: ``InteractionLedger.get_trace`` (§6 — cost is
    never stored). Fail-open: a broken observability path reads as "not
    recorded"; a run must never fail because observability is off."""

    async def _read() -> Optional[float]:
        from hermes_cli.config import load_config
        from hermes_cli.datastore import SupabaseAppStore, get_store
        from hermes_cli.interactions import InteractionLedger

        resolved = load_config() or {}
        store = get_store("supabase-app", "prod", config=resolved)
        if not isinstance(store, SupabaseAppStore) or not store.dsn:
            return None
        ledger = InteractionLedger(store, config=resolved)
        interactions, _rollup = await ledger.get_trace(trace_id, principal)
        return _cost_from_interactions(interactions)

    try:
        return asyncio.run(_read())
    except Exception:  # noqa: BLE001 — cost is observability, never fatal
        log.warning(
            "projects: cost read failed for trace %s — not recorded",
            trace_id, exc_info=True,
        )
        return None


def run_cost(
    trace_id: Optional[str],
    *,
    reader: Optional[Callable] = None,
    principal=None,
):
    """Cost of a run, read from the C8 ledger — never stored (§6).

    Fail-open contract: no trace, no ledger, or no read principal →
    ``None`` ("not recorded"); a run must never fail because observability
    is off. An injected ``reader`` always wins — it is the test/override
    seam and may map an untraced run to a number.
    """
    if reader is not None:
        try:
            return reader(trace_id)
        except Exception:  # noqa: BLE001
            return None
    if not trace_id:
        return None
    if principal is None:
        return None
    return _default_cost_reader(trace_id, principal)


def budget_gate(
    project: projects_db.Project, run: dict, *, reader: Optional[Callable] = None
) -> Optional[str]:
    """``budget_usd_per_run`` crossed → the run stops promoting and waits;
    one approval is raised. Never kills a card mid-flight (§4)."""
    budget = project.budget_usd_per_run
    if not budget:
        return None
    cost = run_cost(
        run.get("trace_id"), reader=reader, principal=_owner_principal(project)
    )
    if cost is None or float(cost) <= float(budget):
        return None
    reason = (
        f"run {run.get('run_no')} of project '{project.name}' has spent "
        f"${float(cost):.2f}; budget is ${float(budget):.2f} per run — continue?"
    )
    raise_approval(project, run, reason, kind="budget")
    return reason


# ---------------------------------------------------------------------------
# §6 — the run lifecycle orchestration
# ---------------------------------------------------------------------------

def _mint_run_trace(project: projects_db.Project, *, run_no: int, config):
    """Mint the run's C8 trace — off-gateway surfaces pass ``mode='prod'``.

    Returns ``(trace, ledger)``; ``(None, None)`` when action tracking is
    disabled or the app store is not configured — then the run row carries
    no trace and the budget gate has no numbers to enforce with.
    """
    try:
        from hermes_cli import interactions

        return interactions.create_trace(
            config=config,
            actor_user_id=project.owner_user_id or "",
            session_key=f"projects:{project.slug}:run-{run_no}",
            platform="projects",
            mode="prod",
        )
    except Exception:  # noqa: BLE001 — observability must never break a run
        log.warning(
            "projects: C8 trace mint failed for %s run %s — budget not "
            "enforceable for this run",
            project.slug, run_no, exc_info=True,
        )
        return None, None


def start_run(
    pconn,
    bconn,
    *,
    project: projects_db.Project,
    trigger: str = "manual",
    triggered_by: Optional[str] = None,
    playbook_rev: Optional[int] = None,
    spawn_inline: Optional[Callable] = None,
    cost_reader: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Open a run and bring up its cards + guidance (§6 pipeline).

    The run pins its ``playbook_rev`` (§7.2), compiles the guidance block
    once (§5.1), instantiates the playbook onto the board, promotes per
    autonomy, and — when the playbook has ``mode: 'inline'`` steps —
    spawns ONE seeded session for them through the shared spawn path.
    """
    if project.status not in ("active",):
        raise ValueError(
            f"project '{project.slug}' is '{project.status}': only an active "
            "project may run"
        )

    playbook = projects_db.get_playbook(pconn, project.id, rev=playbook_rev)
    if playbook is None:
        raise ValueError(
            f"project '{project.slug}' has no playbook"
            + (f" revision {playbook_rev}" if playbook_rev else " — save one first")
        )
    steps = playbook.get("steps") or []
    if not steps:
        raise ValueError("the playbook has no steps")

    host = host_profile_name(pconn, project.id)
    if not host:
        raise ValueError("the project has no profiles — add one before running")

    run = projects_db.open_project_run(
        pconn,
        project_id=project.id,
        trigger=trigger,
        triggered_by=triggered_by,
        profile=host,
        playbook_rev=playbook["rev"],
    )

    # §6 + C8: the run binds a REAL trace so its cost is readable through
    # the ledger (never stored). Without tracing there is no trace id and
    # the budget gate has nothing to enforce with.
    try:
        from hermes_cli.config import load_config

        full_cfg = load_config() or {}
    except Exception:  # noqa: BLE001 — observability config is advisory
        full_cfg = {}
    trace, ledger = _mint_run_trace(project, run_no=run["run_no"], config=full_cfg)
    if trace is not None:
        projects_db.update_project_run(pconn, run["id"], trace_id=trace.trace_id)
        run = dict(run, trace_id=trace.trace_id)

    # §4.1 — narrowing filter; drops are recorded on the run, never silent.
    cfg = projects_runtime_config()
    enabled = _enabled_toolsets_for_profile(host)
    requested = parse_csv_field(project.toolsets)
    effective, ts_dropped = resolve_toolsets(requested, enabled)
    available = _available_skill_names(host)
    req_skills = parse_csv_field(project.skills)
    eff_skills, sk_dropped, sk_truncated = resolve_skills(
        req_skills, available, cfg["max_skills"]
    )
    prelude_bits: List[str] = []
    if ts_dropped:
        prelude_bits.append(
            "Toolsets requested but NOT enabled by host profile "
            f"'{host}' (dropped): {', '.join(ts_dropped)}"
        )
    if sk_dropped:
        prelude_bits.append(
            f"Skills not found in host profile (dropped): {', '.join(sk_dropped)}"
        )
    if sk_truncated:
        prelude_bits.append(
            f"Skills capped at {cfg['max_skills']} (projects.max_skills); "
            "the remainder was not preloaded"
        )
    if prelude_bits:
        projects_db.update_project_run(
            pconn, run["id"], summary="; ".join(prelude_bits)
        )
        run = projects_db.get_project_run_by_id(pconn, run["id"])

    guidance = compile_guidance(
        project,
        run_no=run["run_no"],
        outputs=projects_db.get_project_outputs(pconn, project.id),
        deliveries_by_output=_deliveries_by_output(pconn, project.id),
        sample_links=_sample_links(pconn, project.id),
        directives=projects_db.list_project_directives(pconn, project.id),
        last_run=_previous_run(pconn, project.id, run["run_no"]),
        cfg=cfg,
    )

    made = instantiate_run_cards(
        bconn,
        pconn,
        project=project,
        run_id=run["id"],
        run_no=run["run_no"],
        steps=steps,
        created_by=triggered_by or "projects",
    )
    held = held_step_keys(steps)
    autonomy = project.autonomy or "supervised"
    if autonomy == "autonomous":
        # §4 table: autonomous has no checkpoint holds — every step moves.
        held = set()
    promoted = promote_run_cards(
        bconn,
        pconn,
        project=project,
        run_id=run["id"],
        steps=steps,
        autonomy=autonomy,
        held=held,
    )
    if autonomy != "manual" and held:
        raise_approval(
            project,
            run,
            f"run {run['run_no']}: checkpoint step(s) "
            f"{sorted({s['key'] for s in steps if s.get('checkpoint')})} "
            "hold their successors until you continue",
        )

    session_info: Dict[str, Any] = {}
    if made["inline"]:
        from hermes_cli import interactions

        spawner = spawn_inline or _default_spawn_inline
        # The run's session binds the run's trace (contextvar), so its
        # tool calls + cost events land under it; ``copy_context()`` in
        # the spawn carries the binding into the session thread.
        with interactions.bind_trace(trace):
            session_info = spawner(
                project=project,
                run=run,
                guidance=guidance,
                inline_steps=made["inline"],
                enabled_toolsets=effective or None,
            )
        if session_info.get("session_id"):
            projects_db.update_project_run(
                pconn, run["id"], session_id=session_info["session_id"]
            )
    if trace is not None and ledger is not None:
        try:
            asyncio.run(ledger.flush(trace))
        except Exception:  # noqa: BLE001 — observability, never fatal
            log.warning(
                "projects: C8 trace flush failed for %s run %s — cost "
                "reads as not recorded",
                project.slug, run.get("run_no"), exc_info=True,
            )

    gate = budget_gate(project, run, reader=cost_reader)
    if gate:
        projects_db.update_project_run(pconn, run["id"], status="waiting")
        run = projects_db.get_project_run_by_id(pconn, run["id"])

    return {
        "run": projects_db.get_project_run_by_id(pconn, run["id"]),
        "guidance": guidance,
        "cards": made["cards"],
        "inline_steps": [s["key"] for s in made["inline"]],
        "promoted": promoted,
        "toolsets_effective": effective,
        "toolsets_dropped": ts_dropped,
        "skills_effective": eff_skills,
        "skills_dropped": sk_dropped,
        "session": session_info,
        "budget_gate": gate,
    }


def continue_run(
    pconn,
    bconn,
    *,
    project: projects_db.Project,
    run: dict,
    cost_reader: Optional[Callable] = None,
) -> Dict[str, Any]:
    """The human passes a checkpoint or answers a budget stop (§12):
    release the held successors and resume a ``waiting`` run."""
    if run["status"] in ("done", "failed", "cancelled"):
        raise ValueError(f"run {run['run_no']} is already {run['status']}")
    playbook = projects_db.get_playbook(pconn, project.id, rev=run["playbook_rev"])
    steps = (playbook or {}).get("steps") or []
    autonomy = project.autonomy or "supervised"
    held = set() if autonomy == "autonomous" else held_step_keys(steps)
    promoted = promote_run_cards(
        bconn,
        pconn,
        project=project,
        run_id=run["id"],
        steps=steps,
        autonomy=autonomy,
        held=held,
        force_held=True,
    )
    updated = run
    if run["status"] == "waiting":
        gate = budget_gate(project, run, reader=cost_reader)
        if gate:
            return {"run": run, "promoted": promoted, "budget_gate": gate}
        projects_db.update_project_run(pconn, run["id"], status="running")
        updated = projects_db.get_project_run_by_id(pconn, run["id"])
    return {"run": updated, "promoted": promoted, "budget_gate": None}


def cancel_run(pconn, bconn, *, project: projects_db.Project, run: dict) -> dict:
    """Stop promoting; archive this run's un-started cards; NEVER kill a
    running worker (§12)."""
    if run["status"] in ("done", "failed", "cancelled"):
        raise ValueError(f"run {run['run_no']} is already {run['status']}")
    archived: List[str] = []
    left_running: List[str] = []
    for rc in projects_db.get_run_cards(pconn, run["id"]):
        row = bconn.execute(
            "SELECT status FROM tasks WHERE id = ?", (rc["task_id"],)
        ).fetchone()
        if row is None:
            continue
        if row["status"] == "running":
            left_running.append(rc["task_id"])
            continue
        if row["status"] in ("triage", "todo", "ready"):
            kanban_db.archive_task(bconn, rc["task_id"])
            archived.append(rc["task_id"])
    outcome = "cancelled"
    if left_running:
        outcome += f" ({len(left_running)} card(s) left running to finish)"
    return projects_db_close_and_fetch(
        pconn, run, status="cancelled", outcome=outcome
    )


def projects_db_close_and_fetch(
    pconn, run: dict, *, status: str, outcome: str
) -> dict:
    projects_db.close_project_run(pconn, run["id"], status=status, outcome=outcome)
    return projects_db.get_project_run_by_id(pconn, run["id"])


# ---------------------------------------------------------------------------
# Small resolvers (lazy, fail-open)
# ---------------------------------------------------------------------------

def _enabled_toolsets_for_profile(profile: str) -> List[str]:
    """The host profile's enabled toolsets — the superset a project may
    intersect with (§4.1). Read inside the profile's runtime scope — never
    the calling process's config; an unknown profile grants nothing."""
    try:
        from agent.profile_runtime import profile_runtime_scope
        from hermes_cli import profiles
        from hermes_cli.config import load_config_readonly

        home = profiles.get_profile_dir(profile)
        if not home.is_dir():
            return []  # unknown profile → fail closed: no grant
        with profile_runtime_scope(home):
            cfg = load_config_readonly() or {}
            ts = cfg.get("toolsets")
    except Exception:  # noqa: BLE001 — fail closed: no grant
        return []
    return [str(t) for t in ts] if isinstance(ts, list) else []


def _available_skill_names(profile: str) -> List[str]:
    """Skills visible to the host profile through the shipped loader —
    scanned inside the profile's runtime scope (the host's skills, never
    the server's)."""
    try:
        from agent.profile_runtime import profile_runtime_scope
        from agent.skill_commands import get_skill_commands
        from hermes_cli import profiles

        home = profiles.get_profile_dir(profile)
        if not home.is_dir():
            return []  # unknown profile → fail closed: no grant
        with profile_runtime_scope(home):
            return list(get_skill_commands().keys())
    except Exception:  # noqa: BLE001 — fail closed: no grant
        return []


def _deliveries_by_output(pconn, project_id: str) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for d in projects_db.get_output_deliveries(pconn, project_id=project_id):
        out.setdefault(d["output_id"], []).append(d)
    return out


def _sample_links(pconn, project_id: str) -> List[dict]:
    return [
        link
        for link in projects_db.get_project_links(pconn, project_id)
        if link.get("kind") == "sample"
    ]


def _previous_run(pconn, project_id: str, run_no: int) -> Optional[dict]:
    if run_no <= 1:
        return None
    prev = projects_db.get_project_run(pconn, project_id, run_no - 1)
    return prev if prev and (prev.get("retro") or "").strip() else None


def _default_spawn_inline(
    *,
    project: projects_db.Project,
    run: dict,
    guidance: str,
    inline_steps: Sequence[dict],
    enabled_toolsets: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """The ONLY session-spawn path this feature may use (§6)."""
    try:
        from agent.seeded_session import spawn_seeded_session
    except Exception as exc:  # noqa: BLE001
        return {"session_id": None, "error": f"seeded session unavailable: {exc}"}
    step_text = "\n".join(
        f"- [{s['key']}] {s['title']}" + (f": {s['body']}" if s.get("body") else "")
        for s in inline_steps
    )
    prompt = (
        f"{guidance}\n\n### Inline steps for this run\n{step_text}\n\n"
        "Do these steps now, in order. Record every deliverable you "
        "produce as an output delivery for this project."
    )
    session_id = f"proj-run-{run.get('run_no')}-{uuid.uuid4().hex[:8]}"
    from hermes_cli import profiles

    host_profile = run.get("profile")
    result = spawn_seeded_session(
        prompt,
        origin=f"projects:{project.slug}:run-{run.get('run_no')}",
        session_id=session_id,
        # The run executes in the HOST profile's home — its memory, secrets
        # and soul, matching the profile the run row records (§6).
        profile_home=(
            str(profiles.get_profile_dir(host_profile)) if host_profile else None
        ),
        enabled_toolsets=list(enabled_toolsets) if enabled_toolsets else None,
        context=contextvars.copy_context(),
    )
    return {
        "session_id": session_id,
        "error": result.error,
        "timed_out": result.timed_out,
    }
