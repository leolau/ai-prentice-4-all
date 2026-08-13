"""FG-26: activation must survive the dashboard auth gate.

The live box refused every activation with ``401 no_cookie``. The two invitation
endpoints are unauthenticated *by definition* — the invitee has no account to log
in with yet — but they were not in the shared ``PUBLIC_API_PATHS`` allowlist, so
the gate answered before the handler ever ran. Every unit test passed, because a
bare ``TestClient`` on a loopback host has no gate: the defect only exists on a
deployment that has one, which is every real deployment.

These tests therefore assert the gate's behaviour, not the handler's: reaching
the handler at all (any status other than the 401 envelope) is the pass
condition, and the neighbouring authenticated route must still be refused so a
regression cannot quietly open ``/api/comms/*`` with it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.dashboard_auth import clear_providers, register_provider
from hermes_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider

ACTIVATION_PATHS = (
    "/api/auth/invitations/redeem",
    "/api/auth/invitations/request",
)


@pytest.fixture
def gated_client():
    """A client for a *gated* deployment — the box's own shape."""
    clear_providers()
    register_provider(StubAuthProvider())
    prev = (
        getattr(web_server.app.state, "bound_host", None),
        getattr(web_server.app.state, "bound_port", None),
        getattr(web_server.app.state, "auth_required", None),
    )
    web_server.app.state.bound_host = "home.example.io"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True
    yield TestClient(web_server.app, base_url="https://home.example.io")
    clear_providers()
    (
        web_server.app.state.bound_host,
        web_server.app.state.bound_port,
        web_server.app.state.auth_required,
    ) = prev


@pytest.mark.parametrize("path", ACTIVATION_PATHS)
def test_activation_endpoints_are_public(path: str) -> None:
    assert path in PUBLIC_API_PATHS


@pytest.mark.parametrize("path", ACTIVATION_PATHS)
def test_gate_does_not_answer_for_the_invitee(gated_client, path: str) -> None:
    response = gated_client.post(path, json={"token": "nope", "password": "x"})

    # The handler may well refuse this token — that is its job. What must not
    # happen is the *gate* refusing first, which is what 401/no_cookie is.
    assert response.status_code != 401
    assert (response.json() or {}).get("reason") != "no_cookie"


def test_the_gate_still_guards_the_console(gated_client) -> None:
    response = gated_client.get("/api/comms/members")

    assert response.status_code == 401
    assert response.json()["reason"] in ("no_cookie", "session_expired")
