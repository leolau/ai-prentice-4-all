"""FG-28: TestClient HTTP-layer test for the console-scope wiring.

Three cases that prove the wiring (not the core, which has its own unit tests in
``test_fg28_console_scope.py``).  The core functions are HTTP-free; this file
exercises the real FastAPI dependency + handler path to assert the status-code
mapping lives where the design puts it:

  1. ``?profile=nonexistent`` → **403** from ``require_console_scope``
     (the yield-dependency rejects unknown profiles before the handler runs).
  2. ``?profile=engineers`` with no interactive session → **401** from
     ``_comms_member_service`` (owner-fallback is refused on scoped routes;
     the TestClient carries the internal ``_SESSION_TOKEN`` which bypasses the
     dashboard auth gate but does NOT attach ``request.state.session``, so
     ``_comms_session_subject`` returns ``""``).
  3. No ``?profile=`` with no DB → **200** ``{"configured": False}`` (legacy
     path: ``_comms_resolve_principal`` calls ``_comms_app_store`` →
     ``get_store`` raises ``RuntimeError`` (unconfigured DSN), which
     ``_comms_resolve_principal`` wraps as ``_CommsNotConfigured``, which the
     handler catches).

These tests run against the module-level ``app`` singleton with a monkeypatched
``HERMES_HOME`` that has a default + ``engineers`` profile but no Supabase
datastore configured — so the legacy path's ``_CommsNotConfigured`` is
exercised without any live DB.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fg28_client(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with default + engineers profile, no DB configured."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import profiles
    from hermes_cli.web_server import (
        _SESSION_HEADER_NAME,
        _SESSION_TOKEN,
        app,
    )
    import hermes_state

    default_home = tmp_path / "hermes-home"
    default_home.mkdir(parents=True)
    (default_home / "config.yaml").write_text("{}\n", encoding="utf-8")

    profiles_root = default_home / "profiles"
    engineers_home = profiles_root / "engineers"
    engineers_home.mkdir(parents=True)
    (engineers_home / "config.yaml").write_text("{}\n", encoding="utf-8")
    (engineers_home / ".env").write_text("", encoding="utf-8")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", default_home / "state.db")

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def test_unknown_profile_returns_403(fg28_client):
    """``?profile=nonexistent`` → 403 from ``require_console_scope`` dependency.

    The dependency checks ``get_profile_registry()`` and rejects unknown
    profiles before the handler runs, so no DB or session is needed.
    """
    resp = fg28_client.get("/api/comms/members", params={"profile": "nonexistent"})
    assert resp.status_code == 403
    assert "unknown target profile" in resp.json()["detail"]


def test_no_subject_returns_401(fg28_client):
    """``?profile=engineers`` with no interactive session → 401.

    The ``require_console_scope`` dependency enters the engineers profile's
    scope (the profile exists in the registry and its home dir is real).
    Then ``_comms_member_service`` checks ``_comms_session_subject(request)``
    which returns ``""`` (the TestClient's ``_SESSION_TOKEN`` bypasses the
    dashboard auth gate but does not attach ``request.state.session``), so the
    chokepoint refuses owner-fallback → 401.
    """
    resp = fg28_client.get("/api/comms/members", params={"profile": "engineers"})
    assert resp.status_code == 401
    assert "owner-fallback" in resp.json()["detail"].lower()


def test_legacy_path_returns_configured_false(fg28_client):
    """No ``?profile=`` with no DB → 200 ``{"configured": False}`` (legacy path).

    The legacy path (no ``?profile=``) calls ``_comms_resolve_principal``, which
    calls ``_comms_app_store`` → ``get_store``. With no Supabase datastore
    configured, ``get_store`` raises ``RuntimeError``, which
    ``_comms_resolve_principal`` wraps as ``_CommsNotConfigured`` (L3178-3181).
    The handler catches ``_CommsNotConfigured`` and returns
    ``{"configured": False, "members": [], "total": 0}``.
    """
    resp = fg28_client.get("/api/comms/members")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["members"] == []
    assert data["total"] == 0
