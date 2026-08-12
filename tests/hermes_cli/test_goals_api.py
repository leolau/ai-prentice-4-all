"""The entity-goal HTTP surface — who may write it, and what it promises.

The entity goal is the one row that enters the **stable** prompt tier of every
profile, so the only interesting property of these routes is authority: ``?as=``
narrows a read for an owner or admin (they can already see every row), and it
must not survive into a write, where it would hand an admin the owner's
authority and record the edit under the owner's name.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from fastapi import HTTPException

from hermes_cli import goals_api
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]
ADMIN = Principal(user_id="ada", display="Ada", role="admin")  # type: ignore[arg-type]


class _Request:
    def __init__(self, *, as_user: str = "", body: Optional[dict] = None) -> None:
        self.query_params = {"as": as_user} if as_user else {}
        self.headers: dict[str, str] = {}
        self._body = body or {}

    async def json(self) -> dict:
        return self._body


class _Goal:
    id = "goal_1"
    tier = "entity"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tier": self.tier, "title": "Improve outcomes"}


class _Tree:
    """Records what the route asked for, on behalf of whom."""

    def __init__(self) -> None:
        self.registry = self
        self._store = self
        self.dsn = "postgresql://stub"
        self.edits: list[str] = []
        self.created_for: list[str] = []

    async def set_entity_goal_text(self, principal, goal_id, **kwargs):
        self.edits.append(principal.user_id)
        return _Goal()

    async def entity_goal(self, principal):
        return _Goal()


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch) -> _Tree:
    tree = _Tree()
    monkeypatch.setattr(goals_api, "_tree", lambda: tree)
    monkeypatch.setattr(goals_api, "_ready", _ready_true)

    async def _ensure(tree_arg, principal):
        tree.created_for.append(principal.user_id)
        return _Goal(), False

    async def _sync(tree_arg, principal):
        return None

    import hermes_cli.goal_purpose as goal_purpose

    monkeypatch.setattr(goal_purpose, "ensure_default_entity_goal", _ensure)
    monkeypatch.setattr(goal_purpose, "sync_snapshot", _sync)
    return tree


async def _ready_true(_tree) -> bool:
    return True


def _bind(monkeypatch: pytest.MonkeyPatch, actor: Principal, requested: Principal):
    """Stand in for the C1 resolver: ``allow_as`` decides which one is returned."""

    async def _resolve(request, *, allow_as: bool = False):
        as_user = request.query_params.get("as") or request.headers.get(
            "X-Hermes-User-Id"
        )
        if allow_as and as_user and actor.role in ("owner", "admin"):
            return requested
        return actor

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )


@pytest.mark.asyncio
async def test_an_admin_cannot_become_the_owner_to_edit_the_entity_goal(
    wired: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind(monkeypatch, ADMIN, OWNER)
    with pytest.raises(HTTPException) as excinfo:
        await goals_api.update_entity_goal(
            _Request(as_user=OWNER.user_id, body={"title": "Admin's own goal"})
        )
    assert excinfo.value.status_code == 403
    assert wired.edits == []


@pytest.mark.asyncio
async def test_the_owner_edits_as_themselves(
    wired: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind(monkeypatch, OWNER, OWNER)
    payload = await goals_api.update_entity_goal(_Request(body={"title": "Ours"}))
    assert wired.edits == [OWNER.user_id]
    assert payload["effective"] == "next_session"


@pytest.mark.asyncio
async def test_an_admin_narrowing_a_read_does_not_create_the_default_goal(
    wired: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind(monkeypatch, ADMIN, OWNER)
    payload = await goals_api.get_entity_goal(_Request(as_user=OWNER.user_id))
    assert payload["goal"]["id"] == "goal_1"
    assert wired.created_for == []


@pytest.mark.asyncio
async def test_the_owner_gets_the_default_goal_created(
    wired: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind(monkeypatch, OWNER, OWNER)
    await goals_api.get_entity_goal(_Request())
    assert wired.created_for == [OWNER.user_id]


@pytest.mark.asyncio
async def test_an_over_budget_purpose_block_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _bind(monkeypatch, OWNER, OWNER)
    from agent import purpose_prompt

    def _raise(_snapshot):
        raise purpose_prompt.PurposeBudgetError("the purpose block is 5000 characters")

    monkeypatch.setattr(purpose_prompt, "build_stable_block", _raise)
    payload = await goals_api.purpose_state(_Request())
    assert payload["block"] == ""
    assert payload["chars"] == 0
    assert "5000 characters" in payload["refused"]
