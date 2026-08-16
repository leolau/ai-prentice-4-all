"""FG-30 §4.3 — the suggestion queue route, driven over HTTP.

The eight tests that shipped with the queue never instantiated a route, which is
exactly how all nine #253 defects stayed green: every one of them lived in a
route's wiring rather than in the function under test. So this drives the real
endpoint and asserts the two properties the screen depends on —

* an adopted suggestion still leaves a trace (the F1 property, asserted nowhere
  before this), and
* the trail carries no ``evidence``, because that blob holds §4.2 T3's
  ``participants`` roster and every enrolled principal may read this screen.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

NOW = datetime(2026, 8, 16, tzinfo=timezone.utc)

ROSTER = {
    "participants": [
        {"user_id": "leo", "display": "Leo Lau", "role": "owner"},
        {"user_id": "mia", "display": "Mia", "role": "member"},
    ],
    "top_skills": [{"name": "cashflow-report", "uses": 12}],
}


def _row(sid: str, status: str) -> Dict[str, Any]:
    return {
        "id": sid,
        "proposed_name": f"finance-{sid}",
        "proposed_role": "CFO",
        "proposed_goal": "improve cashflow",
        "parent_goal_id": None,
        "rationale": "three weeks of cashflow work",
        "evidence": ROSTER,
        "dedup_key": f"key-{sid}",
        "origin_profile": "default",
        "status": status,
        "reviewed_by": None if status == "proposed" else "leo",
        "reviewed_at": None if status == "proposed" else NOW,
        "created_at": NOW,
    }


class _FakeConn:
    """Enough asyncpg surface for the queue's two reads, recording both."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows
        self.fetches: List[Tuple[str, tuple]] = []
        self.closed = 0

    async def execute(self, query: str, *args: object) -> None:
        return None

    async def fetch(self, query: str, *args: object) -> List[Dict[str, Any]]:
        self.fetches.append((query, args))
        wanted = set(args[0]) if args else set()
        rows = [r for r in self._rows if r["status"] in wanted]
        if len(args) > 1 and isinstance(args[1], int):
            rows = rows[: args[1]]
        return rows

    async def close(self) -> None:
        self.closed += 1


def _app_store():
    """A real store object (the constructor type-checks it); never connected."""
    from hermes_cli.datastore import SupabaseAppStore

    return SupabaseAppStore(mode="prod", schema="app_prod", dsn="postgres://unused")


@pytest.fixture
def conn() -> _FakeConn:
    return _FakeConn(
        [_row("open-1", "proposed")]
        + [_row(f"old-{i}", "adopted" if i % 2 else "dismissed") for i in range(40)]
    )


@pytest.fixture
def client(monkeypatch, conn: _FakeConn):
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.profile_suggestion import ProfileSuggestionStore
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    member = SimpleNamespace(
        user_id="mia", display="Mia", role="member", is_owner=False
    )

    async def _fake_principal(request, *, allow_as=False):
        return member

    async def _fake_connect(self) -> _FakeConn:
        return conn

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)
    monkeypatch.setattr(web_server, "_comms_app_store", _app_store)
    monkeypatch.setattr(ProfileSuggestionStore, "_connect", _fake_connect)
    # The app object is module-level; other suites leave the auth gate armed on
    # it, so state this test's precondition rather than inherit one.
    monkeypatch.setattr(app.state, "auth_required", False, raising=False)

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def test_a_reviewed_suggestion_still_has_a_trace(client) -> None:
    """The F1 property: after an adopt the card does not simply vanish."""
    payload = client.get("/api/profiles/suggestions").json()

    assert [s["id"] for s in payload["suggestions"]] == ["open-1"]
    assert payload["reviewed"], "the reviewed trail must reach the renderer"
    assert {s["status"] for s in payload["reviewed"]} <= {"adopted", "dismissed"}


def test_the_reviewed_trail_carries_no_evidence(client) -> None:
    """Every ``evidence`` blob holds the roster (§4.2 T3): user_id, display name
    and role of every active principal. The trail renders status, role and goal,
    so shipping it would be a disclosure with no reader."""
    payload = client.get("/api/profiles/suggestions").json()

    for entry in payload["reviewed"]:
        assert "evidence" not in entry
        assert "participants" not in str(entry)
        assert "Mia" not in str(entry) and "leo" not in str(entry.get("proposed_goal"))
    # The open card is a decision being asked for, so it keeps its evidence.
    assert payload["suggestions"][0]["evidence"]["participants"]


def test_the_reviewed_trail_is_capped_in_sql(client, conn: _FakeConn) -> None:
    """Not a slice of a full fetch: the trail grows for the life of the profile."""
    from hermes_cli.profile_suggestion import REVIEWED_HISTORY_LIMIT

    payload = client.get("/api/profiles/suggestions").json()
    assert len(payload["reviewed"]) == REVIEWED_HISTORY_LIMIT

    reviewed_query = [q for q, args in conn.fetches if len(args) > 1]
    assert reviewed_query, "the reviewed read must pass a LIMIT parameter"
    assert "LIMIT" in reviewed_query[0]


def test_the_queue_uses_one_connection_for_both_reads(client, conn: _FakeConn) -> None:
    """Two reads, one connection — the split is free, a second connect is not."""
    client.get("/api/profiles/suggestions")
    assert len(conn.fetches) == 2
    assert conn.closed == 1


def test_the_route_does_not_leak_internals_on_failure(monkeypatch) -> None:
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.profile_suggestion import ProfileSuggestionStore
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    member = SimpleNamespace(
        user_id="mia", display="Mia", role="member", is_owner=False
    )

    async def _fake_principal(request, *, allow_as=False):
        return member

    async def _boom(self, principal, **kwargs):
        raise RuntimeError("postgres://user:pa55w0rd@10.0.0.4:5432/app_prod")

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)
    monkeypatch.setattr(web_server, "_comms_app_store", _app_store)
    monkeypatch.setattr(ProfileSuggestionStore, "queue", _boom)
    monkeypatch.setattr(app.state, "auth_required", False, raising=False)

    c = TestClient(app, raise_server_exceptions=False)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    resp = c.get("/api/profiles/suggestions")

    assert resp.status_code == 500
    assert "pa55w0rd" not in resp.text and "10.0.0.4" not in resp.text


def test_the_route_requires_a_session(monkeypatch) -> None:
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from fastapi import HTTPException

    from hermes_cli import web_server
    from hermes_cli.web_server import app

    async def _refuse(request, *, allow_as=False):
        raise HTTPException(status_code=401, detail="unauthenticated")

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _refuse)
    monkeypatch.setattr(app.state, "auth_required", False, raising=False)
    resp = TestClient(app).get("/api/profiles/suggestions")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_queue_asks_for_open_and_reviewed_separately(conn: _FakeConn) -> None:
    """The split is a property of the query, not of the UI's re-bucketing."""
    from hermes_cli.profile_suggestion import (
        OPEN_STATE,
        REVIEWED_STATES,
        ProfileSuggestionStore,
    )

    store = ProfileSuggestionStore(_app_store())
    principal = SimpleNamespace(user_id="mia", role="member", is_owner=False)
    open_rows, reviewed = await store.queue(principal, connection=conn)  # type: ignore[arg-type]

    statuses: List[Optional[tuple]] = [args[0] for _q, args in conn.fetches]
    assert list(OPEN_STATE) in statuses and list(REVIEWED_STATES) in statuses
    assert all(s.status == "proposed" for s in open_rows)
    assert all(s.status != "proposed" for s in reviewed)
