"""Live project surfaces for the redesigned Projects page.

``GET /{slug}/cards/{task_id}/activity`` — what a board-dispatched card's
worker is thinking, as it thinks it. Same frame contract as
``/runs/{n}/activity`` (``reasoning``, ``status``, ``tool.start``,
``tool.complete``, then ``end``; ``unavailable`` when there is nothing to
tail), fed by :mod:`hermes_cli.card_activity` instead of the in-process run
buffer, because the worker runs in another process.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from hermes_cli import card_activity, kanban_db
from hermes_cli.projects_api import (
    _ACTIVITY_TICK_SECONDS,
    _board_conn,
    _card_payload,
    _require_read,
    _sse,
)

router = APIRouter(prefix="/api/registry/projects")

_UNAVAILABLE = (
    "This task's worker is not publishing live reasoning, so there is "
    "nothing to stream."
)


def _parse_after(request: Request) -> int:
    raw_after = request.query_params.get("after")
    if raw_after is None:
        return 0
    try:
        after = int(raw_after)
    except ValueError:
        raise HTTPException(status_code=400, detail="after must be an integer")
    if after < 0:
        raise HTTPException(status_code=400, detail="after must be >= 0")
    return after


@router.get("/{slug}/cards/{task_id}/activity")
async def card_activity_route(request: Request, task_id: str) -> StreamingResponse:
    """A card's live reasoning and tool names (never arguments or results).

    ``?after=`` resumes from a sequence number. The stream ends once the
    worker marks its attempt finished or the card leaves ``running``.
    """
    project, _role, _profiles, principal = await _require_read(request)
    after = _parse_after(request)

    def _visible_sync() -> Optional[dict]:
        with _board_conn(project) as bconn:
            return _card_payload(bconn, project, task_id, principal=principal)

    if await asyncio.to_thread(_visible_sync) is None:
        raise HTTPException(status_code=404, detail="card not found")

    path = card_activity.store_path(project.board_slug or None)

    def _status_sync() -> Optional[str]:
        with _board_conn(project) as bconn:
            task = kanban_db.get_task(bconn, task_id)
            return task.status if task is not None else None

    async def _events():
        cursor = after
        while True:
            events, finished, known = await asyncio.to_thread(
                card_activity.read, task_id, cursor, path=path
            )
            if not known:
                yield _sse("unavailable", {"reason": _UNAVAILABLE})
                return
            for event in events:
                cursor = event["seq"]
                yield _sse(event["kind"], event)
            if finished or (await asyncio.to_thread(_status_sync)) != "running":
                # One last drain so a worker's final lines are not lost to
                # the race between its last write and the status flip.
                tail, _f, _k = await asyncio.to_thread(
                    card_activity.read, task_id, cursor, path=path
                )
                for event in tail:
                    cursor = event["seq"]
                    yield _sse(event["kind"], event)
                yield _sse("end", {"cursor": cursor})
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(_ACTIVITY_TICK_SECONDS)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
