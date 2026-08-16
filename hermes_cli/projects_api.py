"""HTTP routes for the Projects feature — API (design §12 / §17 steps 3–5).

The record API: read/write of the project itself plus the sub-objects the
first page needs — outputs (+ deliveries + the human-only accept), members,
profiles, contacts, links, the principal-filtered board and card creation —
plus the run machinery (step 4): the playbook and its revisions (§7),
directives/feedback guidance (§5), the run lifecycle (§6), and the
toolsets/skills narrowing filter + autonomy route (§4/§4.1) — plus the
schedule wiring (step 5): ``PUT/DELETE /schedule`` against the host
profile's cron job (§3.2), the ``next_run_at`` display cache, the full
derived health (§9.2) and ``doctor`` (§15 failure mode 1). Derived values
are computed on read and never stored: progress (§9.1 ladder), health and
the card rollup.

Mounted by ``web_server.py`` beside the todos and incomings routers, prefix
``/api/registry/projects``. Calls ``projects_db``, ``kanban_db``,
``kanban_view`` and ``cron.jobs`` directly — never the dashboard kanban
plugin over HTTP.

Permissions are enforced here and only here (§11): the store has no RLS.

* Reads by somebody without access are **404, not 403** — the existence of
  a project is itself information.
* Writes need ``lead`` or an instance ``admin``/``owner``. The judgement
  acts — links, contacts, proposing an output, delivering, accepting — are
  additionally open to the ``member`` role. A ``viewer`` never writes and
  never sees ``contacts[].address`` (dropped, not blanked).
* Board reads always pass the caller's principal into
  ``kanban_db.list_tasks`` — a project view must not become the way to read
  another user's ``private:`` card.

Not in this step (later steps in §17): score routes (step 9b). The
``from_todo`` card seam (§10, step 8b) landed on ``POST /{slug}/cards``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from hermes_cli import kanban_db, kanban_view, projects_db, projects_schedule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/registry/projects")

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# ---------------------------------------------------------------------------
# Principal resolution (§11 rule 1)
# ---------------------------------------------------------------------------


async def _principal_read(request: Request):
    """C1 principal for reads — ``?as=`` narrowing allowed, as /todos does."""
    from hermes_cli.web_server import _comms_resolve_principal

    return await _comms_resolve_principal(request, allow_as=True)


async def _principal_write(request: Request):
    """C1 principal for writes — always the acting principal, never ``?as=``."""
    from hermes_cli.web_server import _comms_resolve_principal

    return await _comms_resolve_principal(request, allow_as=False)


async def _enrolled_profiles(user_id: str) -> set[str]:
    """Profiles where the caller holds an **active** principals row.

    The bounded fan-out the shared-read rule needs (§11 rule 2): iterates
    the profile registry under the caller's own subject. Degrades to an
    empty set — never a 500, never a grant — when the access store is
    unreachable.
    """
    try:
        from hermes_cli.access import PrincipalStore
        from hermes_cli.console_scope import enrolled_profiles
        from hermes_cli.web_server import _comms_app_store

        factory = lambda home: PrincipalStore(_comms_app_store())  # noqa: E731
        names = await enrolled_profiles(
            user_id, store_factory=factory, active_only=True
        )
        return set(names)
    except Exception:  # pragma: no cover - defensive: fail closed
        logger.debug("projects: enrollment fan-out unavailable", exc_info=True)
        return set()


# ---------------------------------------------------------------------------
# Access decisions (§11 rules 2–3)
# ---------------------------------------------------------------------------


def _instance_admin(principal) -> bool:
    """Box-wide ``owner``/``admin`` — the instance roles that outrank the
    per-project membership matrix."""
    if getattr(principal, "is_owner", False):
        return True
    return getattr(principal, "role", "") in ("owner", "admin")


def _member_role_sync(conn, project_id: str, user_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    return row["role"] if row else None


def _can_read(project, role, principal, enrolled: set[str], profiles) -> bool:
    if _instance_admin(principal):
        return True
    if project.owner_user_id and project.owner_user_id == principal.user_id:
        return True
    if role is not None:
        return True
    # Shared-read: visibility='shared' AND an active principals row in one
    # of the project's profiles. A suspended enrolment carries no weight.
    if getattr(project, "visibility", "shared") == "shared":
        if enrolled & {p["profile"] for p in profiles}:
            return True
    return False


def _can_write(project, role, principal) -> bool:
    """Record writes: lead, the project creator, or an instance admin/owner."""
    if _instance_admin(principal):
        return True
    if project.owner_user_id and project.owner_user_id == principal.user_id:
        return True
    return role == "lead"


def _can_member_act(project, role, principal) -> bool:
    """The judgement acts (§11 rule 3): links, contacts, proposing an
    output, delivering, accepting. Everything ``_can_write`` plus the plain
    ``member`` role — a viewer still never writes."""
    return _can_write(project, role, principal) or role == "member"


def _load_ctx_sync(slug: str, user_id: str):
    """One synchronous read of the project + its membership context.

    Returns ``(project, member_role, profiles)`` or ``None`` when the slug
    names no project. Callers gate on this — the 404/403 decision is made
    from one consistent snapshot.
    """
    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, slug)
        if project is None:
            return None
        role = _member_role_sync(conn, project.id, user_id)
        profiles = projects_db.get_project_profiles(conn, project.id)
    return project, role, profiles


async def _require_read(request: Request):
    """Resolve the principal, load the project and gate the read.

    Anything the caller may not read is a **404** — indistinguishable from
    a project that does not exist.
    """
    slug = request.path_params["slug"]
    principal = await _principal_read(request)
    ctx = await asyncio.to_thread(_load_ctx_sync, slug, principal.user_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="project not found")
    project, role, profiles = ctx
    enrolled = await _enrolled_profiles(principal.user_id)
    if not _can_read(project, role, principal, enrolled, profiles):
        raise HTTPException(status_code=404, detail="project not found")
    return project, role, profiles, principal


async def _require_write(request: Request, *, judgement: bool = False):
    """Same gate as :func:`_require_read`, then the write check.

    A readable-but-not-writable caller gets a 403: they already know the
    project exists, so hiding it would only confuse.
    """
    project, role, profiles, principal = await _require_read(request)
    ok = _can_member_act(project, role, principal) if judgement else _can_write(
        project, role, principal
    )
    if not ok:
        raise HTTPException(status_code=403, detail="not permitted")
    return project, role, profiles, principal


# ---------------------------------------------------------------------------
# Derived values: progress ladder (§9.1), card rollup, health (§9.2)
# ---------------------------------------------------------------------------


def goal_progress_hook(project, goal_link: dict) -> Optional[dict]:
    """Rung 2 of the ladder — the linked goal's FG-29 metric progress.

    Overridable seam: returns ``None`` until the goals store reader is
    wired (a rung-2 answer must be the goal's own metric, never a guess),
    and the ladder falls through to the card rollup labelled as such.
    """
    return None


def _card_rollup_sync(bconn, project_id: str, principal) -> dict:
    tasks = kanban_db.list_tasks(bconn, project_id=project_id, principal=principal)
    rollup = {"total": len(tasks), "done": 0, "running": 0, "blocked": 0}
    for t in tasks:
        status = getattr(t, "status", "")
        if status in rollup:
            rollup[status] += 1
    return rollup


def _standing_headline(project, deliveries: list[dict]) -> str:
    """Rung 4: never a percentage — when it was last reviewed, and what
    this period delivered."""
    review_every = str(project.review_every or "").strip()
    parts: list[str] = []
    if review_every:
        parts.append(f"review every {review_every}")
    period_deliveries = [
        d for d in deliveries if d.get("delivered_at") and _in_period(project, d["delivered_at"])
    ]
    parts.append(f"{len(period_deliveries)} delivered this period")
    return " · ".join(parts)


def _in_period(project, ts: int) -> bool:
    """Crude period window from ``review_every`` (``30d`` / ``2w`` / ``1m``);
    defaults to 7d when unset or unparseable."""
    days = 7
    m = re.fullmatch(r"(\d+)\s*([dwm])", str(project.review_every or "").strip())
    if m:
        n = int(m.group(1))
        days = n if m.group(2) == "d" else n * 7 if m.group(2) == "w" else n * 30
    return ts >= int(time.time()) - days * 86400


def _derive_progress(
    project,
    outputs: list[dict],
    deliveries: list[dict],
    card_rollup: dict,
    goal_links: list[dict],
) -> dict:
    """One ``progress`` object on read, first rung that applies (§9.1).

    Rung 1 outranks rung 3 deliberately: cards-done counts the work, not
    the result. Whatever rung wins, the card rollup rides along — it is
    what tells you whether progress is currently moving.
    """
    cards = dict(card_rollup)

    if getattr(project, "cadence", "one_off") == "standing":
        return {
            "rung": "standing",
            "label": "standing",
            "headline": _standing_headline(project, deliveries),
            "cards": cards,
        }

    required = [o for o in outputs if o.get("required")]
    delivered_ids = {d["output_id"] for d in deliveries}

    def _advanced(o: dict) -> bool:
        return o.get("status") in ("accepted", "delivered") or o["id"] in delivered_ids

    if required and any(_advanced(o) for o in required):
        accepted_required = [
            o for o in required if o.get("status") == "accepted"
        ]
        return {
            "rung": "outputs",
            "label": "outputs",
            "headline": (
                f"{len(accepted_required)} of {len(required)} outputs accepted"
            ),
            "accepted": len(accepted_required),
            "required": len(required),
            "cards": cards,
        }

    if goal_links:
        metric = goal_progress_hook(project, goal_links[0])
        if metric is not None:
            return {
                "rung": "goal",
                "label": "goal",
                "headline": metric.get("headline", "goal progress"),
                "metric": metric,
                "cards": cards,
            }

    done, total = cards.get("done", 0), cards.get("total", 0)
    return {
        "rung": "cards",
        "label": "cards",
        "headline": f"{done} of {total} cards done",
        "cards": cards,
    }


def _full_health(conn, project, card_rollup: dict, profiles: list) -> str:
    """The complete §9.2 ladder, computed on read from runs + the cron
    store. The cron round-trip only happens for scheduled projects — a
    list page must not open every profile's store for nothing."""
    runs = projects_db.list_project_runs(conn, project.id, limit=10)
    cron_job = None
    if getattr(project, "cron_job_id", None):
        cron_job = projects_schedule.resolve_cron_job(project, profiles)
    return projects_schedule.derive_health(
        project,
        card_rollup=card_rollup,
        profiles=profiles,
        runs=runs,
        cron_job=cron_job,
    )


def _runs_brief(conn, project_id: str, *, limit: int = 5) -> list[dict]:
    """The last N runs as one-line rows (§12 detail). Cost stays fail-open
    and lives on the run-detail read, not here."""
    out = []
    for r in projects_db.list_project_runs(conn, project_id, limit=limit):
        duration = None
        if r.get("started_at") and r.get("ended_at"):
            duration = int(r["ended_at"]) - int(r["started_at"])
        out.append(
            {
                "run_no": r["run_no"],
                "status": r["status"],
                "trigger": r["trigger"],
                "started_at": r.get("started_at"),
                "ended_at": r.get("ended_at"),
                "duration_seconds": duration,
                "outcome": r.get("outcome"),
                "score_user": r.get("score_user"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _project_payload(project, *, extra: Optional[dict] = None) -> dict:
    d = project.to_dict()
    d.pop("folders", None)  # folder list rides with the detail read
    if extra:
        d.update(extra)
    return d


def _contacts_payload(contacts: list[dict], *, include_address: bool) -> list[dict]:
    """A viewer never sees ``address``: the field is dropped, not blanked,
    so a client cannot leak it back (§11 rule 3)."""
    out = []
    for c in contacts:
        row = dict(c)
        if not include_address:
            row.pop("address", None)
        out.append(row)
    return out


def _board_conn(project):
    """The project's board connection, closed on exit (FD hygiene mirrors
    ``projects_db.connect_closing``)."""
    return kanban_db.connect_closing(board=project.board_slug or None)


def _event_dict(ev) -> dict:
    return {
        "id": ev.id,
        "task_id": ev.task_id,
        "kind": ev.kind,
        "payload": ev.payload,
        "created_at": ev.created_at,
    }


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple]:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created, pid = raw.rsplit("|", 1)
        return int(created), pid
    except Exception:
        raise HTTPException(status_code=400, detail="invalid cursor")


def _encode_cursor(created_at: int, pid: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at}|{pid}".encode()).decode()


# ---------------------------------------------------------------------------
# GET / — the readable list
# ---------------------------------------------------------------------------


def _list_sync(
    principal,
    *,
    status: Optional[str],
    cadence: Optional[str],
    q: Optional[str],
    include_archived: bool,
    limit: int,
    cursor: Optional[tuple],
    enrolled: frozenset = frozenset(),
    health: Optional[str] = None,
) -> dict:
    with projects_db.connect_closing() as conn:
        rows = projects_db.list_projects(conn, include_archived=include_archived)
    # Newest first; the keyset cursor sorts on (created_at, id).
    rows.sort(key=lambda p: (p.created_at, p.id), reverse=True)

    items = []
    for p in rows:
        if cursor and (p.created_at, p.id) >= cursor:
            continue
        if status and p.status != status:
            continue
        if cadence and getattr(p, "cadence", None) != cadence:
            continue
        if q and q.lower() not in f"{p.name} {p.goal or ''}".lower():
            continue
        items.append(p)

    page = items[:limit]
    out = []
    with projects_db.connect_closing() as conn:
        for p in page:
            role = _member_role_sync(conn, p.id, principal.user_id)
            profiles = projects_db.get_project_profiles(conn, p.id)
            if not _can_read(p, role, principal, enrolled, profiles):
                continue
            outputs = projects_db.get_project_outputs(conn, p.id)
            deliveries = projects_db.get_output_deliveries(conn, project_id=p.id)
            links = projects_db.get_project_links(conn, p.id)
            members = projects_db.get_project_members(conn, p.id)
            with _board_conn(p) as bconn:
                rollup = _card_rollup_sync(bconn, p.id, principal)
            progress = _derive_progress(
                p, outputs, deliveries, rollup,
                [l for l in links if l["kind"] == "goal"],
            )
            item_health = _full_health(conn, p, rollup, profiles)
            if health and item_health != health:
                continue
            out.append(
                _project_payload(
                    p,
                    extra={
                        "progress": progress,
                        "member_count": len(members),
                        "health": item_health,
                    },
                )
            )

    next_cursor = None
    if len(items) > limit and out:
        last = page[len(out) - 1]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return {"items": out, "next_cursor": next_cursor}


@router.get("/")
async def list_projects(request: Request) -> dict[str, Any]:
    principal = await _principal_read(request)
    params = request.query_params
    try:
        limit = min(int(params.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
    except ValueError:
        limit = _DEFAULT_LIMIT
    cursor = _decode_cursor(params.get("cursor"))
    enrolled = frozenset(await _enrolled_profiles(principal.user_id))
    return await asyncio.to_thread(
        _list_sync,
        principal,
        status=params.get("status"),
        cadence=params.get("cadence"),
        q=params.get("q"),
        include_archived=params.get("archived") in ("1", "true"),
        limit=limit,
        cursor=cursor,
        enrolled=enrolled,
        health=params.get("health"),
    )


# ---------------------------------------------------------------------------
# POST / — create under the full §2.2 contract
# ---------------------------------------------------------------------------


@router.post("/")
async def create_project_route(request: Request) -> dict[str, Any]:
    principal = await _principal_write(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    missing = [
        field
        for field, present in (
            ("goal", bool(str(body.get("goal") or "").strip())),
            ("description", bool(str(body.get("description") or "").strip())),
            ("outputs", bool(body.get("outputs"))),
            ("host_profile", bool(str(body.get("host_profile") or "").strip())),
        )
        if not present
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"missing": missing,
                    "message": f"refused: missing mandatory {', '.join(missing)}"},
        )

    outputs = body.get("outputs") or []
    if not isinstance(outputs, list) or not outputs:
        raise HTTPException(
            status_code=422,
            detail={"missing": ["outputs"],
                    "message": "refused: a project declares at least one output"},
        )

    def _create_sync() -> dict:
        with projects_db.connect_closing() as conn:
            pid = projects_db.create_full_project(
                conn,
                goal=str(body["goal"]).strip(),
                description=str(body["description"]).strip(),
                name=body.get("name") or None,
                slug=body.get("slug") or None,
                folders=body.get("folders") or None,
                primary_path=body.get("primary_path"),
                icon=body.get("icon"),
                color=body.get("color"),
                board_slug=body.get("board_slug") or None,
                owner_user_id=principal.user_id,
                cadence=str(body.get("cadence") or "one_off"),
                autonomy=str(body.get("autonomy") or "supervised"),
            )
            # Host profile [4] + the creator as its lead.
            projects_db.add_project_profile(
                conn,
                project_id=pid,
                profile=str(body["host_profile"]).strip(),
                role="host",
                added_by=principal.user_id,
            )
            projects_db.add_project_member(
                conn,
                project_id=pid,
                user_id=principal.user_id,
                role="lead",
                added_by=principal.user_id,
            )
            for spec in outputs:
                if isinstance(spec, str):
                    spec = {"title": spec}
                projects_db.add_project_output(
                    conn,
                    project_id=pid,
                    title=str(spec.get("title") or "").strip(),
                    spec=spec.get("spec"),
                    kind=str(spec.get("kind") or "artifact"),
                    required=bool(spec.get("required", True)),
                    recurring=bool(spec.get("recurring", False)),
                )
            if (
                body.get("target_audience")
                or body.get("definition_of_done")
                or body.get("visibility")
            ):
                projects_db.update_project_fields(
                    conn,
                    pid,
                    {
                        k: body[k]
                        for k in ("target_audience", "definition_of_done",
                                  "visibility")
                        if body.get(k)
                    },
                )
            goal_link = body.get("goal_link")
            if goal_link and str(goal_link.get("ref") or "").strip():
                projects_db.add_project_link(
                    conn,
                    project_id=pid,
                    kind="goal",
                    profile=str(goal_link.get("profile") or "default"),
                    ref=str(goal_link["ref"]).strip(),
                    label=goal_link.get("label"),
                    added_by=principal.user_id,
                )
            project = projects_db.get_project(conn, pid)
            return _project_payload(project, extra={"created": True})

    try:
        return await asyncio.to_thread(_create_sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /doctor — diagnosable breaks across readable projects (§15.1)
#
# Registered BEFORE ``GET /{slug}`` so the literal path wins over the slug
# parameter. ``hermes projects doctor`` (step 9) calls this same surface.
# ---------------------------------------------------------------------------


def _doctor_sync(principal, enrolled: frozenset, slug: Optional[str]) -> dict:
    items = []
    with projects_db.connect_closing() as conn:
        rows = projects_db.list_projects(conn, include_archived=False)
        if slug:
            rows = [p for p in rows if p.slug == slug]
        for p in rows:
            role = _member_role_sync(conn, p.id, principal.user_id)
            profiles = projects_db.get_project_profiles(conn, p.id)
            if not _can_read(p, role, principal, enrolled, profiles):
                continue
            runs = projects_db.list_project_runs(conn, p.id, limit=10)
            cron_job = None
            if getattr(p, "cron_job_id", None):
                cron_job = projects_schedule.resolve_cron_job(p, profiles)
            findings = projects_schedule.doctor_findings(
                conn, p, profiles=profiles, runs=runs, cron_job=cron_job
            )
            if findings:
                items.append(
                    {
                        "slug": p.slug,
                        "name": p.name,
                        "cadence": p.cadence,
                        "findings": findings,
                    }
                )
    return {"items": items}


@router.get("/doctor")
async def doctor_route(request: Request) -> dict[str, Any]:
    principal = await _principal_read(request)
    slug = request.query_params.get("slug")
    enrolled = frozenset(await _enrolled_profiles(principal.user_id))
    return await asyncio.to_thread(_doctor_sync, principal, enrolled, slug)


# ---------------------------------------------------------------------------
# GET /{slug} — the whole record in one read
# ---------------------------------------------------------------------------


def _detail_sync(project, principal, *, include_address: bool) -> dict:
    with projects_db.connect_closing() as conn:
        pid = project.id
        outputs = projects_db.get_project_outputs(conn, pid)
        deliveries = projects_db.get_output_deliveries(conn, project_id=pid)
        members = projects_db.get_project_members(conn, pid)
        profiles = projects_db.get_project_profiles(conn, pid)
        contacts = projects_db.get_project_contacts(conn, pid)
        links = projects_db.get_project_links(conn, pid)

    deliveries_by_output: dict[str, list[dict]] = {}
    for d in deliveries:
        deliveries_by_output.setdefault(d["output_id"], []).append(d)
    outputs_out = [
        {**o, "deliveries": deliveries_by_output.get(o["id"], [])}
        for o in outputs
    ]

    links_by_kind: dict[str, list[dict]] = {}
    for link in links:
        row = dict(link)
        # A link is a pointer, never an authority (§11 rule 5): nothing but
        # the cached label leaves this step until the owning store's reader
        # resolves it under the caller's principal (steps 4 / 8b).
        row["resolved"] = None if row["kind"] != "url" else True
        links_by_kind.setdefault(row["kind"], []).append(row)

    with _board_conn(project) as bconn:
        rollup = _card_rollup_sync(bconn, project.id, principal)
        recent_task_ids = [
            t.id
            for t in kanban_db.list_tasks(
                bconn, project_id=project.id, principal=principal
            )[:20]
        ]
        events: list[dict] = []
        for tid in recent_task_ids:
            for ev in kanban_db.list_events(bconn, tid)[-5:]:
                events.append(_event_dict(ev))

    progress = _derive_progress(
        project, outputs, deliveries, rollup,
        links_by_kind.get("goal", []),
    )

    with projects_db.connect_closing() as conn:
        health = _full_health(conn, project, rollup, profiles)
        runs_brief = _runs_brief(conn, project.id)
        # ``next_run_at`` is a display cache (§3.2): refreshed on read,
        # the cron store stays authoritative.
        next_run_at = projects_schedule.refresh_next_run(conn, project)

    detail = _project_payload(project)
    detail.update(
        {
            "outputs": outputs_out,
            "members": members,
            "profiles": profiles,
            "contacts": _contacts_payload(contacts, include_address=include_address),
            "links": links_by_kind,
            "progress": progress,
            "health": health,
            "next_run_at": next_run_at,
            "runs": runs_brief,
            "card_rollup": rollup,
            "recent_events": events,
        }
    )
    return detail


@router.get("/{slug}")
async def get_project_detail(request: Request) -> dict[str, Any]:
    project, role, _profiles, principal = await _require_read(request)
    include_address = role not in (None, "viewer")
    return await asyncio.to_thread(
        _detail_sync, project, principal, include_address=include_address
    )


# ---------------------------------------------------------------------------
# PATCH /{slug} — record fields (lead / instance admin)
# ---------------------------------------------------------------------------


@router.patch("/{slug}")
async def patch_project(request: Request) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    base_fields = ("name", "goal", "description", "icon", "color", "board_slug")
    extra_fields = (
        "cadence", "due_at", "review_every", "target_audience", "score_rubric",
        "visibility", "definition_of_done", "max_in_progress",
        "budget_usd_per_run",
    )

    def _patch_sync() -> dict:
        with projects_db.connect_closing() as conn:
            base = {k: body[k] for k in base_fields if k in body}
            if base:
                projects_db.update_project(conn, project.id, **base)
            extra = {k: body[k] for k in extra_fields if k in body}
            if extra:
                projects_db.update_project_fields(conn, project.id, extra)
            if (
                "cadence" in body
                and project.cadence == "repeatable"
                and str(body["cadence"]).strip() != "repeatable"
                and project.cron_job_id
            ):
                # §3.1: leaving repeatable pauses and detaches the cron
                # job — never deletes it — and records who changed it.
                projects_schedule.detach_project_schedule(
                    conn,
                    project=project,
                    reason=(
                        f"cadence changed from repeatable to "
                        f"'{str(body['cadence']).strip()}'"
                    ),
                    changed_by=principal.user_id,
                )
            if "status" in body:
                projects_db.set_project_status(
                    conn, project.id, str(body["status"])
                )
            fresh = projects_db.get_project(conn, project.id)
            return _project_payload(fresh)

    try:
        return await asyncio.to_thread(_patch_sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# Outputs + deliveries + accept (§6.1)
# ---------------------------------------------------------------------------


@router.post("/{slug}/outputs")
async def add_output(request: Request) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()
    try:
        def _add_sync() -> dict:
            with projects_db.connect_closing() as conn:
                oid = projects_db.add_project_output(
                    conn,
                    project_id=project.id,
                    title=str(body.get("title") or "").strip(),
                    spec=body.get("spec"),
                    kind=str(body.get("kind") or "artifact"),
                    required=bool(body.get("required", True)),
                    recurring=bool(body.get("recurring", False)),
                )
                row = next(
                    o for o in projects_db.get_project_outputs(conn, project.id)
                    if o["id"] == oid
                )
                return dict(row)
        return await asyncio.to_thread(_add_sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/{slug}/outputs/{output_id}")
async def patch_output(request: Request, output_id: str) -> dict[str, Any]:
    project, role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()
    # Changing what counts as required is structural — lead and above only.
    if "required" in body and not _can_write(project, role, principal):
        raise HTTPException(status_code=403, detail="not permitted")

    def _patch_sync() -> dict:
        with projects_db.connect_closing() as conn:
            changed = projects_db.update_project_output(
                conn,
                output_id,
                title=body.get("title"),
                spec=body.get("spec"),
                kind=body.get("kind"),
                required=body.get("required"),
                recurring=body.get("recurring"),
                status=body.get("status"),
            )
            if not changed:
                raise KeyError(output_id)
            row = next(
                (
                    o
                    for o in projects_db.get_project_outputs(conn, project.id)
                    if o["id"] == output_id
                ),
                None,
            )
            if row is None:
                raise KeyError(output_id)
            return dict(row)

    try:
        return await asyncio.to_thread(_patch_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="output not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/{slug}/outputs/{output_id}")
async def delete_output(request: Request, output_id: str) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )

    def _delete_sync() -> None:
        with projects_db.connect_closing() as conn:
            projects_db.remove_project_output(conn, output_id)

    try:
        await asyncio.to_thread(_delete_sync)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"deleted": output_id}


@router.post("/{slug}/outputs/{output_id}/deliver")
async def deliver_output(request: Request, output_id: str) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    def _deliver_sync() -> dict:
        with projects_db.connect_closing() as conn:
            did = projects_db.record_output_delivery(
                conn,
                output_id=output_id,
                run_id=body.get("run_id"),
                task_id=body.get("task_id"),
                link_kind=body.get("link_kind"),
                link_ref=body.get("link_ref"),
                profile=body.get("profile"),
                label=body.get("label"),
                note=body.get("note"),
            )
            projects_db.update_project_output(conn, output_id, status="delivered")
            return {"delivery_id": did, "output_id": output_id,
                    "by": principal.user_id}

    try:
        return await asyncio.to_thread(_deliver_sync)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{slug}/outputs/{output_id}/accept")
async def accept_output(request: Request, output_id: str) -> dict[str, Any]:
    """Only a human accepts an output (§6.1) — a member judgement act.

    Accepting the last required output of a ``one_off`` project *offers*
    closure in the response; it never closes the project by itself."""
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )

    def _accept_sync() -> dict:
        with projects_db.connect_closing() as conn:
            if not projects_db.accept_project_output(
                conn, output_id, accepted_by=principal.user_id
            ):
                raise KeyError(output_id)
            outputs = projects_db.get_project_outputs(conn, project.id)
            required = [o for o in outputs if o.get("required")]
            all_accepted = bool(required) and all(
                o.get("status") == "accepted" for o in required
            )
            offers_closure = (
                all_accepted and getattr(project, "cadence", "one_off") == "one_off"
            )
            return {
                "accepted": output_id,
                "by": principal.user_id,
                "offers_closure": offers_closure,
            }

    try:
        return await asyncio.to_thread(_accept_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="output not found")


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.post("/{slug}/members")
async def add_member(request: Request) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(request)
    body = await request.json()
    user_id = str(body.get("user_id") or "").strip()
    role = str(body.get("role") or "member")
    if not user_id:
        raise HTTPException(status_code=422, detail="user_id is required")

    def _add_sync() -> dict:
        with projects_db.connect_closing() as conn:
            if not projects_db.add_project_member(
                conn,
                project_id=project.id,
                user_id=user_id,
                role=role,
                added_by=principal.user_id,
            ):
                raise ValueError(f"{user_id} is already a member")
            return {"user_id": user_id, "role": role}

    try:
        return await asyncio.to_thread(_add_sync)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{slug}/members/{user_id}")
async def remove_member(request: Request, user_id: str) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(request)

    def _remove_sync() -> None:
        with projects_db.connect_closing() as conn:
            members = projects_db.get_project_members(conn, project.id)
            leads = [m for m in members if m["role"] == "lead"]
            target = next((m for m in members if m["user_id"] == user_id), None)
            if target is None:
                raise KeyError(user_id)
            if target["role"] == "lead" and len(leads) <= 1:
                raise ValueError(
                    "refusing to remove the last lead; promote another first"
                )
            projects_db.remove_project_member(conn, project.id, user_id)

    try:
        await asyncio.to_thread(_remove_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="member not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"removed": user_id}


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@router.post("/{slug}/profiles")
async def add_profile(request: Request) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(request)
    body = await request.json()
    profile = str(body.get("profile") or "").strip()
    if not profile:
        raise HTTPException(status_code=422, detail="profile is required")

    def _add_sync() -> dict:
        with projects_db.connect_closing() as conn:
            if not projects_db.add_project_profile(
                conn,
                project_id=project.id,
                profile=profile,
                role=str(body.get("role") or "member"),
                added_by=principal.user_id,
            ):
                raise ValueError(f"profile {profile!r} is already attached")
            return {"profile": profile}

    try:
        return await asyncio.to_thread(_add_sync)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{slug}/profiles/{name}")
async def remove_profile(request: Request, name: str) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(request)

    def _remove_sync() -> None:
        with projects_db.connect_closing() as conn:
            profiles = projects_db.get_project_profiles(conn, project.id)
            if not any(p["profile"] == name for p in profiles):
                raise KeyError(name)
            if len(profiles) <= 1:
                raise ValueError(
                    "refusing to detach the last profile; a project without "
                    "profiles has nowhere to run"
                )
            projects_db.remove_project_profile(conn, project.id, name)

    try:
        await asyncio.to_thread(_remove_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="profile not attached")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"detached": name}


# ---------------------------------------------------------------------------
# Contacts ([10] — address is members-only PII)
# ---------------------------------------------------------------------------


@router.post("/{slug}/contacts")
async def add_contact(request: Request) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()

    def _add_sync() -> dict:
        with projects_db.connect_closing() as conn:
            cid = projects_db.add_project_contact(
                conn,
                project_id=project.id,
                name=str(body.get("name") or "").strip(),
                role=body.get("role"),
                org=body.get("org"),
                platform=body.get("platform"),
                address=body.get("address"),
                user_id=body.get("user_id"),
                notes=body.get("notes"),
                created_by=principal.user_id,
            )
            row = next(
                c for c in projects_db.get_project_contacts(conn, project.id)
                if c["id"] == cid
            )
            return dict(row)

    try:
        return await asyncio.to_thread(_add_sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/{slug}/contacts/{contact_id}")
async def patch_contact(request: Request, contact_id: str) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(request)
    body = await request.json()

    def _patch_sync() -> dict:
        with projects_db.connect_closing() as conn:
            if not projects_db.update_project_contact(conn, contact_id, **body):
                raise KeyError(contact_id)
            row = next(
                (
                    c
                    for c in projects_db.get_project_contacts(conn, project.id)
                    if c["id"] == contact_id
                ),
                None,
            )
            if row is None:
                raise KeyError(contact_id)
            return dict(row)

    try:
        return await asyncio.to_thread(_patch_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="contact not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/{slug}/contacts/{contact_id}")
async def delete_contact(request: Request, contact_id: str) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(request)

    def _delete_sync() -> None:
        with projects_db.connect_closing() as conn:
            if not projects_db.remove_project_contact(conn, contact_id):
                raise KeyError(contact_id)

    try:
        await asyncio.to_thread(_delete_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="contact not found")
    return {"deleted": contact_id}


# ---------------------------------------------------------------------------
# Links — pointers, never authorities (§11 rule 5)
# ---------------------------------------------------------------------------


@router.post("/{slug}/links")
async def add_link(request: Request) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()
    kind = str(body.get("kind") or "").strip()
    ref = str(body.get("ref") or "").strip()
    profile = str(body.get("profile") or "default").strip()
    if not kind or not ref:
        raise HTTPException(status_code=422, detail="kind and ref are required")

    def _add_sync() -> dict:
        with projects_db.connect_closing() as conn:
            if not projects_db.add_project_link(
                conn,
                project_id=project.id,
                kind=kind,
                profile=profile,
                ref=ref,
                label=body.get("label"),
                added_by=principal.user_id,
            ):
                raise ValueError("link already exists")
            return {"kind": kind, "profile": profile, "ref": ref,
                    "label": body.get("label")}

    try:
        return await asyncio.to_thread(_add_sync)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{slug}/links")
async def delete_link(request: Request) -> dict[str, Any]:
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()
    kind = str(body.get("kind") or "").strip()
    ref = str(body.get("ref") or "").strip()
    profile = str(body.get("profile") or "default").strip()

    def _delete_sync() -> None:
        with projects_db.connect_closing() as conn:
            if not projects_db.remove_project_link(
                conn,
                project_id=project.id,
                kind=kind,
                profile=profile,
                ref=ref,
            ):
                raise KeyError(kind)

    try:
        await asyncio.to_thread(_delete_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="link not found")
    return {"detached": {"kind": kind, "profile": profile, "ref": ref}}


# ---------------------------------------------------------------------------
# Board + cards (rule 4: reads stay principal-filtered)
# ---------------------------------------------------------------------------


@router.get("/{slug}/board")
async def project_board(request: Request) -> dict[str, Any]:
    """The project's cards through the one shared rollup (design §12) —
    ``build_board_view`` with ``project_id`` and the caller's principal, so
    another user's ``private:`` card stays invisible here too."""
    project, _role, _profiles, principal = await _require_read(request)

    def _board_sync() -> dict:
        with _board_conn(project) as bconn:
            return kanban_view.build_board_view(
                bconn, project_id=project.id, principal=principal
            )

    return await asyncio.to_thread(_board_sync)


@router.post("/{slug}/cards")
async def create_card(request: Request) -> dict[str, Any]:
    """Create a card on the project's board.

    Cards created through the Projects surface land in ``triage`` — a
    project asking for work is not the same as a human approving it
    (§10: promotion is not dispatch).

    The ``from_todo`` seam (§10, step 8b): ``{"from_todo": {"profile": …,
    "id": …}}`` promotes a to-do into a card — human-only, one-way, no
    reverse sync, no ``project_id`` on to-dos. The to-do is read under the
    caller's own principal (not visible → 404); the card inherits its
    title/description, a ``project_links(kind='todo')`` row records
    provenance, and the to-do moves to ``working`` with a history entry
    naming the card. If the stage move fails the card is rolled back — a
    half-promotion would strand the work in neither place.
    """
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()
    title = str(body.get("title") or "").strip()
    card_body = body.get("body")

    # ── The promotion seam (§10): validate the to-do BEFORE anything is
    # created, so a bad ref fails with nothing to roll back.
    from_todo = body.get("from_todo")
    todo = None
    todo_profile = "default"
    if isinstance(from_todo, dict) and from_todo:
        todo_id = str(from_todo.get("id") or "").strip()
        todo_profile = str(from_todo.get("profile") or "default").strip()
        if not todo_id:
            raise HTTPException(
                status_code=422, detail="from_todo needs an id"
            )
        from hermes_cli.todo_store import default_store

        try:
            todo = await default_store().get(principal, todo_id)
        except Exception:  # noqa: BLE001 - store unreachable = not visible
            todo = None
        if todo is None:
            raise HTTPException(
                status_code=404, detail="to-do not found or not visible"
            )
        title = title or todo.title
        card_body = card_body or todo.description
    if not title:
        raise HTTPException(status_code=422, detail="title is required")

    def _create_sync() -> dict:
        with _board_conn(project) as bconn:
            tid = kanban_db.create_task(
                bconn,
                title=title,
                body=card_body,
                assignee=body.get("assignee"),
                created_by=principal.user_id,
                triage=True,
                board=project.board_slug or None,
                project_id=project.id,
                owner_user_id=principal.user_id,
                # A project card is shared with the project's members; the
                # per-project read gate (§11) is the authority, so a NULL
                # visibility (invisible to every non-owner) would be wrong.
                visibility="shared",
            )
            if todo is not None:
                # Provenance pointer (§11 rule 5): the card came from this
                # to-do. INSERT OR IGNORE — re-promoting is a no-op here.
                with projects_db.connect_closing() as pconn:
                    projects_db.add_project_link(
                        pconn,
                        project_id=project.id,
                        kind="todo",
                        profile=todo_profile,
                        ref=todo.id,
                        label=todo.title,
                        added_by=principal.user_id,
                    )
        return {"task_id": tid, "project_id": project.id, "status": "triage"}

    try:
        result = await asyncio.to_thread(_create_sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if todo is not None:
        # The to-do moves to ``working`` AFTER the card exists, with a
        # history entry naming the card — the store's ``action:`` prefix
        # convention puts it on the same timeline as the stage changes.
        from hermes_cli.todo_store import TodoError, default_store

        store = default_store()
        actor = f"user:{principal.user_id}"
        try:
            await store.set_stage(
                principal, todo.id, "working", actor=actor
            )
            await store.record_outbound(
                principal,
                todo.id,
                event=f"card:{result['task_id']}",
                channel="promote",
                actor=actor,
            )
        except (TodoError, LookupError) as exc:
            # Roll the card back: promotion is one atomic idea, and a card
            # whose to-do refused to move would look dispatched when it
            # was not.
            def _rollback_sync() -> None:
                with _board_conn(project) as bconn:
                    kanban_db.delete_task(bconn, str(result["task_id"]))

            await asyncio.to_thread(_rollback_sync)
            raise HTTPException(
                status_code=409,
                detail=f"the to-do could not move to working: {exc}",
            )
        result["from_todo"] = {"profile": todo_profile, "id": todo.id}
    return result


@router.get("/{slug}/cards/{task_id}")
async def get_card(request: Request, task_id: str) -> dict[str, Any]:
    """One card, re-checked under the caller's principal: a ``private:``
    card owned by someone else is a 404 through the project surface too."""
    project, _role, _profiles, principal = await _require_read(request)

    def _get_sync() -> dict:
        with _board_conn(project) as bconn:
            task = kanban_db.get_task(bconn, task_id)
            if task is None or getattr(task, "project_id", None) != project.id:
                raise KeyError(task_id)
            visibility = getattr(task, "visibility", None) or ""
            owner = getattr(task, "owner_user_id", None)
            if (
                visibility.startswith("private:")
                and owner != principal.user_id
                and not _instance_admin(principal)
            ):
                raise KeyError(task_id)
            return kanban_view.task_dict(task)

    try:
        return await asyncio.to_thread(_get_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="card not found")


# ---------------------------------------------------------------------------
# Step 4: playbook, directives (guidance), runs, tools/autonomy (§4/§5/§6/§7)
# ---------------------------------------------------------------------------

from hermes_cli import projects_run  # noqa: E402


@router.get("/{slug}/playbook")
async def get_playbook_route(request: Request) -> dict[str, Any]:
    """The method: the active revision (with steps) + the revision list."""
    project, _role, _profiles, _principal = await _require_read(request)

    def _get_sync() -> dict:
        with projects_db.connect_closing() as conn:
            active = projects_db.get_playbook(conn, project.id)
            revs = projects_db.list_playbook_revs(conn, project.id)
        return {"active": active, "revisions": revs}

    return await asyncio.to_thread(_get_sync)


@router.post("/{slug}/playbook")
async def save_playbook_route(request: Request) -> dict[str, Any]:
    """Propose revision N+1 with ``active=0`` (§7.2).

    Open to the ``member`` role — the agent proposes the method; only a
    lead/admin may activate it. Cycle-checked at save time with the
    offending keys named (§7.1), and a step ``assignee`` must be one of
    the project's profiles.
    """
    project, _role, profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()
    steps = body.get("steps") or []

    def _save_sync() -> dict:
        profile_names = {p["profile"] for p in profiles}
        try:
            cleaned = projects_db.validate_playbook_steps(steps)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        for step in cleaned:
            assignee = step.get("assignee")
            if assignee and profile_names and assignee not in profile_names:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"step {step['key']!r}: assignee {assignee!r} is not "
                        f"one of the project's profiles {sorted(profile_names)}"
                    ),
                )
        with projects_db.connect_closing() as conn:
            rev = projects_db.save_playbook_rev(
                conn,
                project_id=project.id,
                body=str(body.get("body") or ""),
                steps=cleaned,
                created_by=principal.user_id,
                note=body.get("note"),
            )
        # A new revision is a proposal: nothing changes until a human
        # activates it, and a running run keeps its pinned rev either way.
        return {"rev": rev, "active": False}

    return await asyncio.to_thread(_save_sync)


@router.post("/{slug}/playbook/{rev}/activate")
async def activate_playbook_route(request: Request, rev: int) -> dict[str, Any]:
    """Human-only activation (§7.2): lead/admin, records ``activated_at``
    + ``note``. A mid-flight run is unaffected — it keeps its pinned rev."""
    project, _role, _profiles, _principal = await _require_write(request)
    body = await request.json() if request.headers.get("content-length") else {}

    def _activate_sync() -> dict:
        with projects_db.connect_closing() as conn:
            ok = projects_db.activate_playbook_rev(
                conn, project.id, rev, note=body.get("note")
            )
        if not ok:
            raise HTTPException(
                status_code=404, detail=f"playbook revision {rev} not found"
            )
        return {"rev": rev, "active": True}

    return await asyncio.to_thread(_activate_sync)


@router.get("/{slug}/directives")
async def list_directives_route(request: Request) -> dict[str, Any]:
    project, _role, _profiles, _principal = await _require_read(request)
    include_retired = request.query_params.get("include_retired") == "true"

    def _list_sync() -> dict:
        with projects_db.connect_closing() as conn:
            rows = projects_db.list_project_directives(
                conn, project.id, active_only=not include_retired
            )
        return {
            "directives": rows,
            # §5.1: guidance never applies mid-conversation.
            "applies_from": "next run",
        }

    return await asyncio.to_thread(_list_sync)


@router.post("/{slug}/directives")
async def add_directive_route(request: Request) -> dict[str, Any]:
    """Standing instruction or feedback (§5). Judgement act: ``member``+.
    The active set is capped — adding directive N+1 is a 409 *retire one
    first*. The response carries the one product sentence that must never
    be omitted: guidance applies from the next run."""
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()

    def _add_sync() -> dict:
        cfg = projects_run.projects_runtime_config()
        with projects_db.connect_closing() as conn:
            try:
                did = projects_db.add_project_directive(
                    conn,
                    project_id=project.id,
                    kind=body.get("kind", "directive"),
                    body=body.get("body", ""),
                    scope=body.get("scope", "project"),
                    target_ref=body.get("target_ref"),
                    rating=body.get("rating"),
                    author_user_id=principal.user_id,
                    max_active=cfg["guidance_max_directives"],
                )
            except ValueError as exc:
                status = 409 if "retire one first" in str(exc) else 422
                raise HTTPException(status_code=status, detail=str(exc))
        return {"id": did, "applies_from": "next run"}

    return await asyncio.to_thread(_add_sync)


@router.post("/{slug}/directives/{directive_id}/retire")
async def retire_directive_route(
    request: Request, directive_id: str
) -> dict[str, Any]:
    """Retire, never delete (§5.2): the historical record survives."""
    project, _role, _profiles, _principal = await _require_write(
        request, judgement=True
    )

    def _retire_sync() -> dict:
        with projects_db.connect_closing() as conn:
            ok = projects_db.retire_project_directive(conn, directive_id)
        if not ok:
            raise HTTPException(
                status_code=404, detail="directive not found or already retired"
            )
        return {"id": directive_id, "retired": True}

    return await asyncio.to_thread(_retire_sync)


# ---------------------------------------------------------------------------
# Runs (§6)
# ---------------------------------------------------------------------------


def _run_payload(conn, bconn, run: dict, *, principal) -> dict:
    """One run row joined with its cards' live board state."""
    cards = []
    for rc in projects_db.get_run_cards(conn, run["id"]):
        task = kanban_db.get_task(bconn, rc["task_id"])
        cards.append(
            {
                "task_id": rc["task_id"],
                "step_key": rc.get("step_key"),
                "status": task.status if task else None,
                "title": task.title if task else None,
            }
        )
    payload = dict(run)
    payload["cards"] = cards
    payload["cost"] = projects_run.run_cost(run.get("trace_id"))
    # Fail-open contract (§6): no ledger → "not recorded", never an error.
    payload["cost_recorded"] = payload["cost"] is not None
    if run.get("started_at"):
        end = run.get("ended_at") or int(time.time())
        payload["duration_seconds"] = max(0, end - int(run["started_at"]))
    return payload


@router.get("/{slug}/runs")
async def list_runs_route(request: Request) -> dict[str, Any]:
    """The record (§12): duration, outcome, deliveries and status."""
    project, _role, _profiles, _principal = await _require_read(request)

    def _list_sync() -> dict:
        with projects_db.connect_closing() as conn:
            runs = projects_db.list_project_runs(conn, project.id)
            deliveries = projects_db.get_output_deliveries(
                conn, project_id=project.id
            )
        by_run: dict = {}
        for d in deliveries:
            if d.get("run_id"):
                by_run.setdefault(d["run_id"], 0)
                by_run[d["run_id"]] += 1
        for r in runs:
            r["deliveries"] = by_run.get(r["id"], 0)
            if r.get("started_at"):
                end = r.get("ended_at") or int(time.time())
                r["duration_seconds"] = max(0, end - int(r["started_at"]))
        return {"runs": runs}

    return await asyncio.to_thread(_list_sync)


@router.get("/{slug}/runs/{run_no}")
async def get_run_route(request: Request, run_no: int) -> dict[str, Any]:
    """Run detail: cards, deliveries, cost read from the C8 trace (never
    stored — fail-open), and the retro once written."""
    project, _role, _profiles, principal = await _require_read(request)

    def _get_sync() -> dict:
        with projects_db.connect_closing() as conn:
            run = projects_db.get_project_run(conn, project.id, run_no)
            if run is None:
                raise KeyError(run_no)
            deliveries = projects_db.get_output_deliveries(conn, run_id=run["id"])
            with _board_conn(project) as bconn:
                payload = _run_payload(conn, bconn, run, principal=principal)
        payload["deliveries"] = deliveries
        return payload

    try:
        return await asyncio.to_thread(_get_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")


@router.post("/{slug}/runs")
async def start_run_route(request: Request) -> dict[str, Any]:
    """Start a run now (``trigger='manual'``); an optional ``playbook_rev``
    repeats an old method — "do exactly what worked last time" (§7.2)."""
    project, _role, _profiles, principal = await _require_write(
        request, judgement=True
    )
    body = await request.json() if request.headers.get("content-length") else {}

    def _start_sync() -> dict:
        with projects_db.connect_closing() as conn:
            with _board_conn(project) as bconn:
                try:
                    return projects_run.start_run(
                        conn,
                        bconn,
                        project=project,
                        trigger="manual",
                        triggered_by=principal.user_id,
                        playbook_rev=body.get("playbook_rev"),
                    )
                except ValueError as exc:
                    detail = str(exc)
                    status = 409 if "no playbook" in detail or "no profiles" in detail else 422
                    raise HTTPException(status_code=status, detail=detail)

    return await asyncio.to_thread(_start_sync)


@router.post("/{slug}/runs/{run_no}/continue")
async def continue_run_route(request: Request, run_no: int) -> dict[str, Any]:
    """The human passes a checkpoint or answers a budget stop (§12): the
    held successors move to ``todo`` and a ``waiting`` run resumes."""
    project, _role, _profiles, _principal = await _require_write(
        request, judgement=True
    )

    def _continue_sync() -> dict:
        with projects_db.connect_closing() as conn:
            run = projects_db.get_project_run(conn, project.id, run_no)
            if run is None:
                raise KeyError(run_no)
            with _board_conn(project) as bconn:
                try:
                    return projects_run.continue_run(
                        conn, bconn, project=project, run=run
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail=str(exc))

    try:
        return await asyncio.to_thread(_continue_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")


@router.post("/{slug}/runs/{run_no}/cancel")
async def cancel_run_route(request: Request, run_no: int) -> dict[str, Any]:
    """Stop promoting and archive the run's un-started cards; a running
    worker is never killed (§12)."""
    project, _role, _profiles, _principal = await _require_write(
        request, judgement=True
    )

    def _cancel_sync() -> dict:
        with projects_db.connect_closing() as conn:
            run = projects_db.get_project_run(conn, project.id, run_no)
            if run is None:
                raise KeyError(run_no)
            with _board_conn(project) as bconn:
                try:
                    return projects_run.cancel_run(
                        conn, bconn, project=project, run=run
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail=str(exc))

    try:
        return await asyncio.to_thread(_cancel_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")


@router.post("/{slug}/runs/{run_no}/retro")
async def run_retro_route(request: Request, run_no: int) -> dict[str, Any]:
    """Write or edit the retrospective. (``score_self`` lands with step 9b;
    ``score_user`` is human-only there.)"""
    project, _role, _profiles, _principal = await _require_write(
        request, judgement=True
    )
    body = await request.json()
    retro = str(body.get("retro") or "").strip()
    if not retro:
        raise HTTPException(status_code=422, detail="retro must not be empty")

    def _retro_sync() -> dict:
        with projects_db.connect_closing() as conn:
            run = projects_db.get_project_run(conn, project.id, run_no)
            if run is None:
                raise KeyError(run_no)
            projects_db.update_project_run(
                conn, run["id"], retro=retro, retro_at=int(time.time())
            )
            return projects_db.get_project_run_by_id(conn, run["id"])

    try:
        return await asyncio.to_thread(_retro_sync)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")


# ---------------------------------------------------------------------------
# Instruments: tools/skills narrowing (§4.1) + autonomy (§4)
# ---------------------------------------------------------------------------


@router.patch("/{slug}/tools")
async def patch_tools_route(request: Request) -> dict[str, Any]:
    """Set the project's ``toolsets``/``skills`` (§12).

    Names are validated at write time so an impossible request is refused
    loudly; the stored lists are still a *narrowing filter* — at spawn the
    effective set is intersected with what the host profile enables (§4.1),
    and the response shows that intersection.
    """
    project, _role, profiles, _principal = await _require_write(request)
    body = await request.json()

    def _patch_sync() -> dict:
        updates: dict = {}
        if "toolsets" in body:
            names = [str(t).strip() for t in (body.get("toolsets") or []) if str(t).strip()]
            unknown = [
                t for t in names
                if t.casefold() not in kanban_db.KNOWN_TOOLSET_NAMES
            ]
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown toolset(s): {', '.join(unknown)}",
                )
            updates["toolsets"] = ",".join(names)
        if "skills" in body:
            # One seam for resolution: the same host-profile loader the
            # run spawn uses (§4.1), so write-time validation and spawn
            # agree about what exists.
            known = projects_run._available_skill_names() or None
            names = [str(s).strip() for s in (body.get("skills") or []) if str(s).strip()]
            if known is not None:
                unknown = [s for s in names if s not in known]
                if unknown:
                    raise HTTPException(
                        status_code=422,
                        detail=f"unknown skill(s): {', '.join(unknown)}",
                    )
            updates["skills"] = ",".join(names)
        if not updates:
            raise HTTPException(
                status_code=422, detail="provide toolsets and/or skills"
            )
        with projects_db.connect_closing() as conn:
            projects_db.update_project_fields(conn, project.id, updates)
            fresh = projects_db.get_project(conn, project.slug)
            host = projects_run.host_profile_name(conn, project.id)
            enabled = projects_run._enabled_toolsets_for_profile(host or "")
            available = projects_run._available_skill_names()
        cfg = projects_run.projects_runtime_config()
        eff_ts, dropped_ts = projects_run.resolve_toolsets(
            projects_run.parse_csv_field(fresh.toolsets), enabled
        )
        eff_sk, dropped_sk, truncated = projects_run.resolve_skills(
            projects_run.parse_csv_field(fresh.skills), available,
            cfg["max_skills"],
        )
        return {
            "toolsets": projects_run.parse_csv_field(fresh.toolsets),
            "skills": projects_run.parse_csv_field(fresh.skills),
            "host_profile": host,
            "effective_toolsets": eff_ts,
            "dropped_toolsets": dropped_ts,
            "effective_skills": eff_sk,
            "dropped_skills": dropped_sk,
            "skills_truncated": truncated,
        }

    return await asyncio.to_thread(_patch_sync)


@router.patch("/{slug}/autonomy")
async def patch_autonomy_route(request: Request) -> dict[str, Any]:
    """A separate route so the audit line and the permission check are
    unmistakable (§12). Lead/admin only — ``_can_write``."""
    project, _role, _profiles, _principal = await _require_write(request)
    body = await request.json()
    autonomy = body.get("autonomy")
    if autonomy not in projects_db.VALID_AUTONOMY_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"autonomy must be one of "
                f"{sorted(projects_db.VALID_AUTONOMY_LEVELS)}"
            ),
        )

    def _patch_sync() -> dict:
        with projects_db.connect_closing() as conn:
            projects_db.update_project_fields(
                conn, project.id, {"autonomy": autonomy}
            )
            fresh = projects_db.get_project(conn, project.slug)
        return {"slug": fresh.slug, "autonomy": fresh.autonomy}

    return await asyncio.to_thread(_patch_sync)


# ---------------------------------------------------------------------------
# PUT/DELETE /{slug}/schedule — the host profile's cron job (§3.2)
# ---------------------------------------------------------------------------


@router.put("/{slug}/schedule")
async def put_schedule_route(request: Request) -> dict[str, Any]:
    """Create or update the cron job in the host profile's store. Lead
    only — a schedule is an automation decision, not a judgement act.
    §3.1 preconditions map to 409 naming what is missing; an invalid
    schedule string maps to 422."""
    project, _role, profiles, principal = await _require_write(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    def _put_sync() -> dict:
        with projects_db.connect_closing() as conn:
            return projects_schedule.set_project_schedule(
                conn,
                project=project,
                schedule=str(body.get("schedule") or ""),
                profiles=profiles,
                changed_by=principal.user_id,
            )

    try:
        return await asyncio.to_thread(_put_sync)
    except projects_schedule.SchedulePreconditionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/{slug}/schedule")
async def delete_schedule_route(request: Request) -> dict[str, Any]:
    """Remove the cron job and both halves of the link. Removing — not
    pausing — keeps the store honest; the cadence-change path is the one
    that pauses (§3.1)."""
    project, _role, _profiles, _principal = await _require_write(request)

    def _del_sync() -> dict:
        with projects_db.connect_closing() as conn:
            removed = projects_schedule.clear_project_schedule(
                conn, project=project
            )
            return {"scheduled": False, "removed": removed}

    return await asyncio.to_thread(_del_sync)


@router.get("/{slug}/doctor")
async def project_doctor_route(request: Request) -> dict[str, Any]:
    """One project's diagnosable breaks, with the health they imply
    (§9.2 / §15 failure mode 1)."""
    project, _role, profiles, principal = await _require_read(request)

    def _doc_sync() -> dict:
        with projects_db.connect_closing() as conn:
            runs = projects_db.list_project_runs(conn, project.id, limit=10)
        cron_job = None
        if getattr(project, "cron_job_id", None):
            cron_job = projects_schedule.resolve_cron_job(project, profiles)
        with _board_conn(project) as bconn:
            rollup = _card_rollup_sync(bconn, project.id, principal)
        with projects_db.connect_closing() as conn:
            findings = projects_schedule.doctor_findings(
                conn, project, profiles=profiles, runs=runs, cron_job=cron_job
            )
        health = projects_schedule.derive_health(
            project,
            card_rollup=rollup,
            profiles=profiles,
            runs=runs,
            cron_job=cron_job,
        )
        return {
            "slug": project.slug,
            "health": health,
            "findings": findings,
            "clean": not findings,
        }

    return await asyncio.to_thread(_doc_sync)
