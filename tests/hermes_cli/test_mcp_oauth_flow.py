"""Endpoint tests for the browser-driven MCP OAuth flow routes.

The routes under /api/mcp/servers/{name}/oauth/* let agent-home drive the
same headless re-auth the CLI exposes via `hermes mcp login` +
`hermes mcp oauth-paste`: /start spawns a probe whose redirect handler
captures the authorization URL into shared flow state, /status reports it,
/redirect feeds the pasted redirect through the drop-file path, and /cancel
aborts the waiter.
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

    # Flow state is module-level and must not leak between tests.
    web_server._MCP_OAUTH_FLOWS.clear()
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
    monkeypatch.setattr(app.state, "auth_required", False, raising=False)

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


@pytest.fixture
def canva_server(monkeypatch, client):
    """A configured auth:oauth server; the flow worker is stubbed to flip the
    state straight to waiting_user so no real OAuth happens."""
    from hermes_cli import web_server

    servers = {
        "canva": {"url": "https://mcp.canva.com/mcp", "auth": "oauth", "enabled": True},
        "local": {"command": "npx", "args": ["tool"], "enabled": True},
    }

    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers", lambda config=None: dict(servers)
    )

    def _fake_flow(name, cfg, profile, state):
        state["status"] = "waiting_user"
        state["authorization_url"] = "https://canva.example/oauth?client=x"

    monkeypatch.setattr(web_server, "_run_mcp_oauth_flow", _fake_flow)
    # The mcp SDK is not installed in every dev env; the flow worker is
    # stubbed, so availability just needs to read as on.
    monkeypatch.setattr("tools.mcp_oauth._OAUTH_AVAILABLE", True, raising=False)
    return client


def _wait_status(client, name: str, want: str, timeout: float = 3.0) -> dict:
    """Poll the status endpoint — the flow worker runs on a real thread, so
    state flips asynchronously."""
    import time

    deadline = time.time() + timeout
    while True:
        status = client.get(f"/api/mcp/servers/{name}/oauth/status").json()
        if status["status"] == want:
            return status
        if time.time() > deadline:
            raise AssertionError(f"flow stuck at {status!r}, wanted {want}")
        time.sleep(0.02)


class TestMcpOAuthFlow:
    def test_start_rejects_unknown_server(self, canva_server):
        resp = canva_server.post("/api/mcp/servers/nope/oauth/start")
        assert resp.status_code == 404

    def test_start_rejects_non_oauth_server(self, canva_server):
        resp = canva_server.post("/api/mcp/servers/local/oauth/start")
        assert resp.status_code == 400

    def test_start_then_status_reports_waiting_user(self, canva_server):
        resp = canva_server.post("/api/mcp/servers/canva/oauth/start")
        assert resp.status_code == 200
        status = _wait_status(canva_server, "canva", "waiting_user")
        assert status["authorization_url"] == "https://canva.example/oauth?client=x"

    def test_status_is_idle_with_no_flow(self, canva_server):
        status = canva_server.get("/api/mcp/servers/canva/oauth/status").json()
        assert status["status"] == "idle"

    def test_redirect_requires_a_waiting_flow(self, canva_server):
        resp = canva_server.post(
            "/api/mcp/servers/canva/oauth/redirect",
            json={"url": "http://127.0.0.1:9/callback?code=x"},
        )
        assert resp.status_code == 409

    def test_redirect_rejects_a_non_oauth_url(self, canva_server):
        canva_server.post("/api/mcp/servers/canva/oauth/start")
        _wait_status(canva_server, "canva", "waiting_user")
        resp = canva_server.post(
            "/api/mcp/servers/canva/oauth/redirect",
            json={"url": "https://example.com/nope"},
        )
        assert resp.status_code == 400

    def test_redirect_writes_the_drop_file(self, canva_server, monkeypatch):
        canva_server.post("/api/mcp/servers/canva/oauth/start")
        _wait_status(canva_server, "canva", "waiting_user")
        written = {}
        monkeypatch.setattr(
            "tools.mcp_oauth.write_drop_redirect",
            lambda name, url: written.setdefault(name, url),
        )
        resp = canva_server.post(
            "/api/mcp/servers/canva/oauth/redirect",
            json={"url": "http://127.0.0.1:9/callback?code=abc&state=s"},
        )
        assert resp.status_code == 200
        assert written == {"canva": "http://127.0.0.1:9/callback?code=abc&state=s"}

    def test_cancel_requires_an_active_flow(self, canva_server):
        resp = canva_server.post("/api/mcp/servers/canva/oauth/cancel")
        assert resp.status_code == 409

    def test_cancel_drops_an_error_redirect(self, canva_server, monkeypatch):
        canva_server.post("/api/mcp/servers/canva/oauth/start")
        _wait_status(canva_server, "canva", "waiting_user")
        written = {}
        monkeypatch.setattr(
            "tools.mcp_oauth.write_drop_redirect",
            lambda name, url: written.setdefault(name, url),
        )
        resp = canva_server.post("/api/mcp/servers/canva/oauth/cancel")
        assert resp.status_code == 200
        assert "error=access_denied" in written["canva"]

    def test_second_start_is_idempotent_while_waiting(self, canva_server):
        canva_server.post("/api/mcp/servers/canva/oauth/start")
        _wait_status(canva_server, "canva", "waiting_user")
        resp = canva_server.post("/api/mcp/servers/canva/oauth/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "waiting_user"
