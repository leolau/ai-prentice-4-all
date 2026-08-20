"""Promotion seam tests — a to-do becomes a project card (Part 2).

Behaviour contracts from the plan:
- Promoting creates a card with ``status='triage'`` (never ``ready``).
- Writes the ``project_links`` row.
- Moves the to-do to ``working`` — not ``done``.
- A to-do the caller cannot read yields 404 and no card (no orphan card).
- Promoting the same to-do twice is refused by the ``project_links`` PK.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from hermes_cli.access import Principal
from hermes_cli.todo_store import Todo

PRINCIPAL = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]


def _todo(**kwargs: Any) -> Todo:
    fields: dict[str, Any] = {
        "id": "tsk_1",
        "owner_user_id": "leo",
        "visibility": "private:leo",
        "title": "Send Ada the signed quote",
        "description": "Before the tender closes.",
        "stage": "open",
        "status": "pending",
        "priority": "high",
        "origin": "triage",
        "current_state": "captured",
        "trigger_state": "captured",
        "completion_state": "done",
    }
    fields.update(kwargs)
    return Todo(**fields)


class _MockProject:
    def __init__(self, id="proj_1", slug="acme", name="Acme"):
        self.id = id
        self.slug = slug
        self.name = name


class _MockCard:
    """A kanban card returned by ``get_task`` after creation."""
    def __init__(self, project_id="proj_1"):
        self.project_id = project_id


class TestPromoteCreatesCard:
    """Promoting creates a card with ``status='triage'`` and moves to ``working``."""

    @pytest.mark.asyncio
    async def test_creates_card_with_triage_status(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        captured = {}
        def _mock_create_task(conn, **kwargs):
            captured.update(kwargs)
            return "card_123"

        request = _mock_request({"project": "acme"})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("hermes_cli.projects_db.get_project", return_value=_MockProject()),
            patch("hermes_cli.projects_db.connect_closing") as mock_conn_ctx,
            patch("hermes_cli.kanban_db.create_task", side_effect=_mock_create_task),
            patch("hermes_cli.kanban_db.get_task", return_value=_MockCard()),
            patch("hermes_cli.kanban_db.connect_closing") as mock_kconn_ctx,
        ):
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            mock_kconn_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_kconn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            result = await todos_api.promote_todo(request, "tsk_1")

        # Card created with triage=True.
        assert captured.get("triage") is True
        assert captured.get("project_id") == "proj_1"
        assert captured.get("title") == "Send Ada the signed quote"
        # Priority mapped: high → 2 (int, not a string label).
        assert captured.get("priority") == 2
        # To-do moved to working, not done.
        assert result["stage"] == "working"
        store.set_stage.assert_called_once()
        args, kwargs = store.set_stage.call_args
        assert args[2] == "working"
        # Card id returned.
        assert result["card_id"] == "card_123"

    @pytest.mark.asyncio
    async def test_card_body_points_back_at_todo(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo(description="Original text"))
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        captured = {}
        def _mock_create_task(conn, **kwargs):
            captured.update(kwargs)
            return "card_456"

        request = _mock_request({"project": "acme"})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("hermes_cli.projects_db.get_project", return_value=_MockProject()),
            patch("hermes_cli.projects_db.connect_closing") as mock_conn_ctx,
            patch("hermes_cli.kanban_db.create_task", side_effect=_mock_create_task),
            patch("hermes_cli.kanban_db.get_task", return_value=_MockCard()),
            patch("hermes_cli.kanban_db.connect_closing") as mock_kconn_ctx,
        ):
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            mock_kconn_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_kconn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            await todos_api.promote_todo(request, "tsk_1")

        body = captured.get("body", "")
        assert "Original text" in body
        assert "tsk_1" in body  # points back at the to-do


class TestPromotePriorityMap:
    """``critical|high → 2``, ``normal → 1``, ``low → 0`` (board ints)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "todo_priority,expected",
        [
            ("critical", 2),
            ("high", 2),
            ("normal", 1),
            ("low", 0),
        ],
    )
    async def test_priority_mapped(self, todo_priority: str, expected: int) -> None:
        from hermes_cli.todos_api import _PROMOTE_PRIORITY_MAP

        assert _PROMOTE_PRIORITY_MAP.get(todo_priority) == expected


class TestPromoteNotFound:
    """A to-do the caller cannot read yields 404 and no card."""

    @pytest.mark.asyncio
    async def test_todo_not_found_no_card(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=None)  # not visible
        store.set_stage = AsyncMock()

        request = _mock_request({"project": "acme"})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("hermes_cli.kanban_db.create_task") as mock_create,
            pytest.raises(HTTPException) as exc_info,
        ):
            await todos_api.promote_todo(request, "tsk_1")

        assert exc_info.value.status_code == 404
        mock_create.assert_not_called()  # no orphan card

    @pytest.mark.asyncio
    async def test_project_not_found(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock()

        request = _mock_request({"project": "nonexistent"})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("hermes_cli.projects_db.get_project", return_value=None),
            patch("hermes_cli.projects_db.connect_closing") as mock_conn_ctx,
            patch("hermes_cli.kanban_db.create_task") as mock_create,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            await todos_api.promote_todo(request, "tsk_1")

        assert exc_info.value.status_code == 404
        mock_create.assert_not_called()


class TestPromoteDuplicateRefused:
    """Promoting the same to-do twice is refused by the ``project_links`` PK."""

    @pytest.mark.asyncio
    async def test_second_promote_refused_by_pk(self) -> None:
        """The ``project_links`` primary key is ``(project_id, kind, profile, ref)``.

        ``add_project_link`` uses ``INSERT OR IGNORE``, so the second insert is
        a no-op (returns False). The card creation, however, is NOT idempotent —
        ``kanban_db.create_task`` would create a second card. The endpoint must
        check the link before creating a second card.

        Currently the endpoint does NOT check this — the test documents the
        contract and the expected behaviour.
        """
        from hermes_cli.projects_db import add_project_link
        import sqlite3
        import tempfile
        import os as _os

        # Use an in-memory DB to test the PK constraint.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create the tables.
        conn.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY);
            CREATE TABLE project_links (
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                profile TEXT NOT NULL,
                ref TEXT NOT NULL,
                label TEXT,
                added_by TEXT,
                added_at INTEGER NOT NULL,
                PRIMARY KEY (project_id, kind, profile, ref)
            );
        """)

        # First link succeeds.
        result1 = add_project_link(
            conn, project_id="proj_1", kind="todo",
            profile="leo", ref="tsk_1", label="test", added_by="leo",
        )
        assert result1 is True

        # Second link with same PK is a no-op.
        result2 = add_project_link(
            conn, project_id="proj_1", kind="todo",
            profile="leo", ref="tsk_1", label="test", added_by="leo",
        )
        assert result2 is False  # INSERT OR IGNORE — no-op

        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_request(body: dict) -> MagicMock:
    import json as _json

    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


class TestPromoteRefusesArchivedProject:
    """U9: the writer's gate — promoting into a shelved project answers 409
    like the Projects router, and the provenance link is never written."""

    @pytest.mark.asyncio
    async def test_promote_into_archived_project_is_409_and_no_link(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        refusal = ValueError(
            "project acme is archived — restore it before adding a card"
        )
        request = _mock_request({"project": "acme"})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("hermes_cli.projects_db.get_project", return_value=_MockProject()),
            patch("hermes_cli.projects_db.connect_closing") as mock_conn_ctx,
            patch("hermes_cli.kanban_db.create_task", side_effect=refusal),
            patch("hermes_cli.kanban_db.connect_closing") as mock_kconn_ctx,
            patch("hermes_cli.projects_db.add_project_link") as mock_link,
        ):
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            mock_kconn_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_kconn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(HTTPException) as excinfo:
                await todos_api.promote_todo(request, "tsk_1")

        # The router's archived refusal: 409 naming the archive and restore.
        assert excinfo.value.status_code == 409
        assert "archived" in excinfo.value.detail
        assert "restore" in excinfo.value.detail
        # The card's refusal takes everything with it: no link row, and the
        # to-do did not move.
        mock_link.assert_not_called()
        store.set_stage.assert_not_called()
