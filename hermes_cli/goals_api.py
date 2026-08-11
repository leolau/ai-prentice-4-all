"""HTTP routes for the entity goal — what agent-home settings edits.

Small on purpose. The whole goal tree has a CLI and the FG-09 front-ends; the
one thing that has to be reachable from the settings page is the **entity
goal**, because it is the first thing a new owner meets and the one goal that
appears in every profile's prompt.

Two properties the page depends on, both enforced here rather than in the UI:

* Only the owner may write it. The entity goal is what every sub-goal ladders
  into and it enters the stable prompt tier of every profile; it is not a
  per-user setting.
* Saving it refreshes the purpose snapshot but **cannot** change a conversation
  already running. The response says so, so the page can tell the truth about
  when the edit lands instead of implying it is live.

Mounted by ``web_server.py`` beside the to-dos router.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/registry/goals")


async def _resolve_principal(request: Request):
    from hermes_cli.web_server import _comms_resolve_principal

    return await _comms_resolve_principal(request, allow_as=True)


def _tree():
    from hermes_cli.goal_purpose import default_tree_store

    return default_tree_store()


async def _ready(tree) -> bool:
    """Whether the FG-29 columns exist yet.

    A box that has the FG-04 ``goals`` table but not this migration has no
    entity goal, and an empty settings field is the truthful answer rather than
    a 500 that reads like a bug.
    """
    if not tree.registry._store.dsn:
        return False
    conn = await tree.registry._connect()
    try:
        return (
            await conn.fetchval(
                """SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'goals' AND column_name = 'tier'""",
            )
            is not None
        )
    except Exception as exc:  # noqa: BLE001 - an absent table is an empty page
        logger.debug("goals api: not ready (%s)", exc)
        return False
    finally:
        await conn.close()


def _payload(goal, *, created: bool = False) -> dict[str, Any]:
    from hermes_cli.goal_tree import prompt_slot

    return {
        "goal": goal.as_dict(),
        "created": created,
        "prompt_tier": prompt_slot(goal.tier),
        # The page must not imply the edit is live: the running session's
        # system prompt was built and cached before this write.
        "effective": "next_session",
    }


@router.get("/entity")
async def get_entity_goal(request: Request) -> dict[str, Any]:
    """The entity goal, creating the default first one for the owner.

    A GET that can create looks odd until you consider what the alternative
    is: a new owner opening settings and being shown an empty box with no
    explanation of what belongs in it. The default goal *is* the explanation,
    and creating it is idempotent.
    """
    principal = await _resolve_principal(request)
    tree = _tree()
    if not await _ready(tree):
        return {"goal": None, "created": False, "effective": "next_session"}

    from hermes_cli.goal_purpose import ensure_default_entity_goal

    if not principal.is_owner:
        goal = await tree.entity_goal(principal)
        if goal is None:
            return {"goal": None, "created": False, "effective": "next_session"}
        return _payload(goal)
    goal, created = await ensure_default_entity_goal(tree, principal)
    return _payload(goal, created=created)


@router.patch("/entity")
async def update_entity_goal(request: Request) -> dict[str, Any]:
    """Edit the entity goal's title/description. Owner only.

    Bumps the source revision, which is what makes every published copy in
    every other profile report itself stale — the copies are not reached into
    from here, they notice next time they are read.
    """
    principal = await _resolve_principal(request)
    if not principal.is_owner:
        raise HTTPException(
            status_code=403, detail="Only the owner may edit the entity goal"
        )
    tree = _tree()
    if not await _ready(tree):
        raise HTTPException(status_code=503, detail="Goal registry is not available")

    body = await request.json()
    title: Optional[str] = None
    if body.get("title") is not None:
        title = str(body["title"]).strip()
        if not title:
            raise HTTPException(status_code=400, detail="title cannot be empty")
    description: Optional[str] = (
        str(body["description"]) if body.get("description") is not None else None
    )
    if title is None and description is None:
        raise HTTPException(status_code=400, detail="nothing to update")

    from hermes_cli.goal_purpose import ensure_default_entity_goal, sync_snapshot

    goal, _created = await ensure_default_entity_goal(tree, principal)
    try:
        goal = await tree.set_entity_goal_text(
            principal, goal.id, title=title, description=description
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await sync_snapshot(tree, principal)
    return _payload(goal)


@router.get("/purpose")
async def purpose_state(request: Request) -> dict[str, Any]:
    """What the prompt will actually contain next session, and what it costs.

    The settings page shows the rendered block rather than a description of it:
    the one question an owner has about text in the system prompt is "what will
    it actually say", and the second is "how big is it".
    """
    await _resolve_principal(request)
    from agent.purpose_prompt import PurposeSnapshot, build_stable_block, load_snapshot

    snapshot = load_snapshot() or PurposeSnapshot()
    block = build_stable_block(snapshot)
    return {
        "profile": snapshot.profile,
        "block": block,
        "chars": len(block),
        "stable_goals": [
            {"title": goal.title, "tier": goal.tier, "stale": goal.stale}
            for goal in snapshot.stable
        ],
        "effective": "next_session",
    }


__all__ = ["router"]
