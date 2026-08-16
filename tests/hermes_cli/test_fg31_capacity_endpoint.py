"""FG-31 — the headroom endpoint, driven over HTTP.

A collector with green unit tests proves nothing about the screen: FG-28's three
route defects all lived in the wiring rather than the function under test. So
this instantiates the real route and asserts the payload the agent-home card
actually consumes, including that an unauthenticated caller gets nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    return tmp_path


@pytest.fixture
def client(monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    member = SimpleNamespace(
        user_id="a1b2c3", display="Mia", role="member", is_owner=False
    )

    async def _fake_principal(request, *, allow_as=False):
        return member

    async def _no_idle(*args, **kwargs):
        return []

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)
    monkeypatch.setattr(
        "hermes_cli.profile_suggestion.idle_profiles", _no_idle, raising=False
    )
    # The app object is module-level and other suites leave the dashboard auth
    # gate armed on it; state this test's precondition rather than inherit one.
    monkeypatch.setattr(app.state, "auth_required", False, raising=False)

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def test_capacity_endpoint_returns_a_verdict_and_its_binding_constraint(client):
    resp = client.get("/api/capacity")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["state"] in {"comfortable", "watch", "constrained"}
    assert payload["headline"]
    assert payload["summary"]
    # The card reads these keys directly; a rename here is a blank screen.
    indicators = payload["indicators"]
    for key in (
        "active_conversations",
        "per_profile",
        "cap_here",
        "cap_box_wide",
        "available_mb",
        "write_lock_waits_per_hour",
        "turn_p50_s",
        "turn_p95_s",
        "profile_count",
    ):
        assert key in indicators, key
    assert isinstance(payload["bounds"], list)
    assert isinstance(payload["recommendations"], list)
    if payload["state"] != "comfortable":
        assert payload["binding_constraint"] is not None
        assert "hardware_helps" in payload["binding_constraint"]


def test_capacity_endpoint_is_readable_by_a_plain_member(client):
    """Whoever notices "it feels slow" is rarely the owner, and the payload
    carries counts rather than anyone's content."""
    payload = client.get("/api/capacity").json()
    assert "per_profile" in payload["indicators"]
    # Nothing here identifies a person or a conversation.
    assert "sessions" not in payload
    assert "principals" not in payload


def test_capacity_endpoint_requires_a_session(monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.web_server import app

    from fastapi import HTTPException

    async def _refuse(request, *, allow_as=False):
        raise HTTPException(status_code=401, detail="unauthenticated")

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _refuse)
    monkeypatch.setattr(app.state, "auth_required", False, raising=False)
    resp = TestClient(app).get("/api/capacity")
    assert resp.status_code in (401, 403)
