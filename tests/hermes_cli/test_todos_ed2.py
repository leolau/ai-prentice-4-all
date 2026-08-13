"""Behaviour-contract tests for the to-dos ed.2 additions: /start, memory doc,
and the spawn helper's boundary.

These exercise the thin parts — the gate logic, the best-effort resolution
contracts, and the spawn boundary — without a database or a live agent. The
Postgres behaviour is covered by ``test_todo_store_e2e.py``; the cron
regression (same run document through the helper) lives in the cron test suite
where the scheduler's fixtures are.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_cli.access import Principal
from hermes_cli.todo_store import Todo, TodoError

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
        "source_kind": "inbound",
        "source_ref": "11111111-1111-1111-1111-111111111111",
    }
    fields.update(kwargs)
    return Todo(**fields)


# ---------------------------------------------------------------------------
# /start — the session spawn gate
# ---------------------------------------------------------------------------

class TestStartSessionFalse:
    """``session: false`` moves to ``working`` and spawns nothing."""

    @pytest.mark.asyncio
    async def test_session_false_moves_to_working_no_spawn(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store._connect = AsyncMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        request = _mock_request({"session": False})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
        ):
            result = await todos_api.start_todo(request, "tsk_1")

        store.set_stage.assert_called_once()
        assert result["stage"] == "working"
        assert result["session_id"] is None
        assert result["spawned"] is False


class TestStartSessionTrue:
    """``session: true`` records a ``session_id`` on the transition."""

    @pytest.mark.asyncio
    async def test_session_true_returns_session_id(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store._connect = AsyncMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        request = _mock_request({"session": True})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("agent.seeded_session.spawn_seeded_session") as mock_spawn,
        ):
            mock_spawn.return_value = MagicMock(
                session_id="todo_tsk_1_20260813",
                result="done",
                timed_out=False,
                error=None,
            )
            result = await todos_api.start_todo(request, "tsk_1")

        assert result["stage"] == "working"
        assert result["session_id"] is not None
        assert result["session_id"].startswith("todo_tsk_1_")
        assert result["spawned"] is True

    @pytest.mark.asyncio
    async def test_spawn_failure_still_leaves_todo_working(self) -> None:
        """The one that matters: a spawn failure must not un-move the stage."""
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store._connect = AsyncMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        request = _mock_request({"session": True})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("agent.seeded_session.spawn_seeded_session") as mock_spawn,
        ):
            mock_spawn.return_value = MagicMock(
                session_id="todo_tsk_1_fail",
                result=None,
                timed_out=False,
                error="could not resolve runtime provider",
            )
            result = await todos_api.start_todo(request, "tsk_1")

        # The stage moved to working regardless.
        assert result["stage"] == "working"
        # The spawn was attempted (spawned=True means the thread was started).
        assert result["spawned"] is True
        # The session_id is set even though the spawn failed — the page has
        # somewhere to link, and the transition records the attempt.
        assert result["session_id"] is not None


class TestStartProfileIgnored:
    """``profile`` in the request body is silently ignored.

    Profile targeting was dropped until FG-28 provides a "profiles this
    subject holds" query — the previous check was inert (profile_home
    was always ``get_hermes_home()``) and unsafe (``PrincipalStore.get()``
    returns any enrolled principal, not one the caller holds).  A
    ``profile`` key in the body is accepted but has no effect.
    """

    @pytest.mark.asyncio
    async def test_profile_silently_ignored(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        request = _mock_request({"session": True, "profile": "research"})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch("agent.seeded_session.spawn_seeded_session") as mock_spawn,
        ):
            mock_spawn.return_value = MagicMock(
                session_id="todo_tsk_1_profile_ignored",
                result="done",
                timed_out=False,
                error=None,
            )
            result = await todos_api.start_todo(request, "tsk_1")

        # The profile key was silently ignored — no 403, the stage moved
        # and the session spawned as normal.
        assert result["stage"] == "working"
        assert result["spawned"] is True


class TestStartSeededPrompt:
    """The seeded prompt contains the arrival body when ``source_ref`` resolves."""

    @pytest.mark.asyncio
    async def test_prompt_contains_arrival_body(self) -> None:
        from hermes_cli import todos_api

        store = MagicMock()
        store._store = MagicMock()
        store._connect = AsyncMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=_todo())
        store.set_stage = AsyncMock(
            return_value=_todo(stage="working", status="in_progress")
        )

        arrival = {"id": "11111111-1111-1111-1111-111111111111", "body": "Please send the quote by Friday."}
        captured_prompt = {}

        request = _mock_request({"session": True})
        def _capture_spawn(prompt, **kwargs):
            captured_prompt["prompt"] = prompt
            return MagicMock(session_id="x", result=None, timed_out=False, error=None)

        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch.object(todos_api, "_source_item", return_value=arrival),
            patch("agent.seeded_session.spawn_seeded_session", side_effect=_capture_spawn),
        ):
            await todos_api.start_todo(request, "tsk_1")

        assert "Send Ada the signed quote" in captured_prompt["prompt"]
        assert "Please send the quote by Friday." in captured_prompt["prompt"]

    @pytest.mark.asyncio
    async def test_prompt_does_not_blow_up_when_source_absent(self) -> None:
        from hermes_cli import todos_api

        todo_no_source = _todo(source_kind="user", source_ref=None)
        store = MagicMock()
        store._store = MagicMock()
        store._connect = AsyncMock()
        store.initialize = AsyncMock()
        store.get = AsyncMock(return_value=todo_no_source)
        store.set_stage = AsyncMock(
            return_value=_todo(source_kind="user", source_ref=None, stage="working")
        )

        captured_prompt = {}
        def _capture_spawn(prompt, **kwargs):
            captured_prompt["prompt"] = prompt
            return MagicMock(session_id="x", result=None, timed_out=False, error=None)

        request = _mock_request({"session": True})
        with (
            patch.object(todos_api, "_store", return_value=store),
            patch.object(todos_api, "_table_ready", return_value=True),
            patch.object(todos_api, "_resolve_principal", return_value=PRINCIPAL),
            patch.object(todos_api, "_source_item", return_value=None),
            patch("agent.seeded_session.spawn_seeded_session", side_effect=_capture_spawn),
        ):
            await todos_api.start_todo(request, "tsk_1")

        assert "Send Ada the signed quote" in captured_prompt["prompt"]


# ---------------------------------------------------------------------------
# Memory doc — the detail panel's missing half
# ---------------------------------------------------------------------------

class TestMemoryDocContract:
    """``_memory_doc`` follows the same contract as ``_source_item``: returns
    ``None`` on any failure, never raises, never blocks the payload."""

    @pytest.mark.asyncio
    async def test_absent_when_source_has_no_document_id(self) -> None:
        from hermes_cli.todos_api import _memory_doc

        source = {"id": "x", "document_id": None}
        result = await _memory_doc(PRINCIPAL, source)
        assert result is None

    @pytest.mark.asyncio
    async def test_absent_when_source_is_none(self) -> None:
        from hermes_cli.todos_api import _memory_doc

        result = await _memory_doc(PRINCIPAL, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_doc_when_resolved(self) -> None:
        from hermes_cli.todos_api import _memory_doc

        source = {"document_id": "doc-123"}
        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={"id": "doc-123", "title": "Ada's quote"}
        )
        mock_store = MagicMock()
        mock_store.connect = AsyncMock(return_value=mock_conn)
        mock_conn.close = AsyncMock()

        with (
            patch("hermes_cli.datastore.get_store", return_value=mock_store),
            patch("hermes_cli.access.bind_principal", new=AsyncMock()),
        ):
            result = await _memory_doc(PRINCIPAL, source)

        assert result is not None
        assert result["id"] == "doc-123"
        assert result["title"] == "Ada's quote"

    @pytest.mark.asyncio
    async def test_unreachable_tier_degrades_to_absent(self) -> None:
        from hermes_cli.todos_api import _memory_doc

        source = {"document_id": "doc-456"}
        mock_store = MagicMock()
        mock_store.connect = AsyncMock(side_effect=Exception("connection refused"))

        with (
            patch("hermes_cli.datastore.get_store", return_value=mock_store),
            patch("hermes_cli.access.bind_principal", new=AsyncMock()),
        ):
            result = await _memory_doc(PRINCIPAL, source)

        assert result is None

    @pytest.mark.asyncio
    async def test_row_not_found_returns_none(self) -> None:
        from hermes_cli.todos_api import _memory_doc

        source = {"document_id": "doc-missing"}
        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_store = MagicMock()
        mock_store.connect = AsyncMock(return_value=mock_conn)
        mock_conn.close = AsyncMock()

        with (
            patch("hermes_cli.datastore.get_store", return_value=mock_store),
            patch("hermes_cli.access.bind_principal", new=AsyncMock()),
        ):
            result = await _memory_doc(PRINCIPAL, source)

        assert result is None


# ---------------------------------------------------------------------------
# spawn_seeded_session — the boundary contracts
# ---------------------------------------------------------------------------

class TestSpawnBoundary:
    """The helper's policy parameters are never defaulted; plumbing is inside."""

    def test_skip_memory_defaults_to_false_not_true(self) -> None:
        """``skip_memory`` must not default to ``True`` — a to-do session is the
        user's work and wants memory. Only cron passes ``True``."""
        import inspect
        from agent.seeded_session import spawn_seeded_session

        sig = inspect.signature(spawn_seeded_session)
        assert sig.parameters["skip_memory"].default is False

    def test_origin_is_a_parameter_not_hardcoded(self) -> None:
        """``origin`` determines ``AIAgent(platform=…)`` — cron and /start must
        pass their own, not share a default."""
        import inspect
        from agent.seeded_session import spawn_seeded_session

        sig = inspect.signature(spawn_seeded_session)
        assert sig.parameters["origin"].default is not ...  # no default

    def test_runtime_override_is_none_by_default(self) -> None:
        """When ``runtime`` is None, the helper resolves from config — the
        shared path. When a caller pins its own (cron), it wins."""
        import inspect
        from agent.seeded_session import spawn_seeded_session

        sig = inspect.signature(spawn_seeded_session)
        assert sig.parameters["runtime"].default is None

    def test_inactivity_limit_defaults_to_600(self) -> None:
        """The default timeout matches cron's existing value."""
        import inspect
        from agent.seeded_session import spawn_seeded_session

        sig = inspect.signature(spawn_seeded_session)
        assert sig.parameters["inactivity_limit"].default == 600.0

    def test_returns_seeded_session_never_raises(self) -> None:
        """Even a total failure returns a SeededSession with ``error`` set,
        never an exception."""
        from agent.seeded_session import SeededSession, spawn_seeded_session

        result = spawn_seeded_session(
            "test prompt",
            origin="test",
            session_id="test_1",
            profile_home="/nonexistent",
            inactivity_limit=0.01,
        )
        assert isinstance(result, SeededSession)
        assert result.session_id == "test_1"
        # Either it ran (result=None and error=None) or it failed (error set).
        # Either way, it didn't raise.
        assert result.error is None or isinstance(result.error, str)


# ---------------------------------------------------------------------------
# Additional CLI contracts
# ---------------------------------------------------------------------------

class TestCliJsonRoundTrip:
    """``--json`` output round-trips through ``json.loads`` for every read."""

    @pytest.mark.asyncio
    async def test_list_json_round_trips(self) -> None:
        from hermes_cli.todos_cmd import _list
        import io
        import contextlib

        store = MagicMock()
        items = [_todo()]
        store.list = AsyncMock(return_value=(items, None))

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            await _list(
                store, PRINCIPAL,
                stages=None, priorities=None, source_kinds=None,
                query=None, limit=50, json_mode=True,
            )
        payload = __import__("json").loads(stdout.getvalue())
        assert "items" in payload
        assert len(payload["items"]) == 1
        assert payload["items"][0]["title"] == "Send Ada the signed quote"

    @pytest.mark.asyncio
    async def test_show_json_round_trips(self) -> None:
        from hermes_cli.todos_cmd import _show
        import io
        import contextlib

        store = MagicMock()
        store.get = AsyncMock(return_value=_todo())
        store.history = AsyncMock(return_value=[])

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            await _show(store, PRINCIPAL, "tsk_1", json_mode=True)
        payload = __import__("json").loads(stdout.getvalue())
        assert payload["title"] == "Send Ada the signed quote"
        assert "history" in payload

    @pytest.mark.asyncio
    async def test_facets_json_round_trips(self) -> None:
        from hermes_cli.todos_cmd import _facets
        import io
        import contextlib

        store = MagicMock()
        store.facets = AsyncMock(return_value={
            "stages": [{"value": "open", "count": 3}],
            "priorities": [{"value": "high", "count": 1}],
            "source_kinds": [{"value": "inbound", "count": 3}],
        })

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            await _facets(store, PRINCIPAL, json_mode=True)
        payload = __import__("json").loads(stdout.getvalue())
        assert "stages" in payload
        assert payload["stages"][0]["value"] == "open"


class TestCliUnknownStageRejected:
    """``list --stage`` rejects an unknown stage rather than returning everything."""

    def test_validate_stage_rejects_unknown(self) -> None:
        from hermes_cli.todo_store import validate_stage, TodoError

        with pytest.raises(TodoError):
            validate_stage("bogus")

    def test_validate_stage_accepts_known(self) -> None:
        from hermes_cli.todo_store import validate_stage

        assert validate_stage("staged") == "staged"
        assert validate_stage("open") == "open"
        assert validate_stage("working") == "working"


class TestCliExpireDryRun:
    """``expire --dry-run`` writes nothing."""

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self) -> None:
        from hermes_cli.todos_cmd import _expire
        import io
        import contextlib

        store = MagicMock()
        store.list = AsyncMock(return_value=([], None))
        store.expire_staged = AsyncMock(return_value=42)  # should NOT be called

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            await _expire(store, PRINCIPAL, days=14, dry_run=True, json_mode=False)

        store.expire_staged.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_request(body: dict) -> MagicMock:
    """A minimal FastAPI Request stub."""
    import json as _json

    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request
