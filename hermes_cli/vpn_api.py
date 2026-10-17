"""HTTP routes for "Private networks (VPN)" (Settings → In&Out in agent-home).

Entries are ``fortinet-vpn`` credentials managed through
``/api/credentials/fortinet/{name}``; this router connects, disconnects,
tests and trusts them (see ``hermes_cli/vpn.py``). Connection failures are
``200`` with ``status.state == "error"``; only unknown names (404) and bad
input (400) are HTTP errors. Responses never carry passwords.

Network folders (``/api/vpn/folders``, ``hermes_cli/vpn_folders.py``) are the
caller's own ``smb-folder`` entries; another user's folder is a 404. The
``/api/internal/vpn/folders*`` routes are for app-mcp only: bearer token
``APP_MCP_HERMES_TOKEN`` through the dashboard token-auth seam, refused when a
reverse proxy forwarded the request.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from hermes_cli import vpn, vpn_folders
from hermes_cli.dashboard_auth.base import DashboardAuthProvider, TokenPrincipal

router = APIRouter(prefix="/api/vpn")
internal_router = APIRouter(prefix="/api/internal/vpn")

#: Shared secret app-mcp presents as ``Authorization: Bearer …`` (in
#: ``app-mcp/app-mcp.env`` and the Hermes ``.env``).
APP_MCP_TOKEN_ENV = "APP_MCP_HERMES_TOKEN"
_MIN_TOKEN_CHARS = 32
INTERNAL_ROUTES = (
    "/api/internal/vpn/folders",
    "/api/internal/vpn/folders/ensure",
    "/api/internal/vpn/folders/import",
)


async def _principal(request: Request, *, allow_as: bool):
    from hermes_cli.credentials_api import _principal as resolve

    return await resolve(request, allow_as=allow_as)


async def _guard(coro) -> Any:
    try:
        return await coro
    except vpn.VpnNotFound as exc:
        raise HTTPException(404, str(exc))
    except vpn.VpnError as exc:
        raise HTTPException(400, str(exc))


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


# -- Network folders (declared before the /{name}/... VPN routes) ------------


@router.get("/folders")
async def list_folders(request: Request):
    principal = await _principal(request, allow_as=False)
    return {"folders": await _guard(vpn_folders.list_views(principal))}


@router.post("/folders", status_code=201)
async def create_folder(request: Request):
    principal = await _principal(request, allow_as=False)
    body = await _json_body(request)
    return await _guard(vpn_folders.create(principal, body))


@router.put("/folders/{name}")
async def update_folder(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    body = await _json_body(request)
    return await _guard(vpn_folders.update(principal, name, body))


@router.delete("/folders/{name}")
async def delete_folder(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    await _guard(vpn_folders.delete(principal, name))
    return {"ok": True}


@router.post("/folders/{name}/test")
async def test_folder(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    return await _guard(vpn_folders.test(principal, name))


@router.post("/folders/{name}/disconnect")
async def disconnect_folder(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    return await _guard(vpn_folders.disconnect(principal, name))


# -- VPNs ---------------------------------------------------------------------


@router.get("")
async def list_vpns(request: Request):
    principal = await _principal(request, allow_as=True)
    return {"vpns": await _guard(vpn.list_views(principal))}


@router.post("/{name}/connect")
async def connect_vpn(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    return {"vpn": await _guard(vpn.connect(principal, name))}


@router.post("/{name}/disconnect")
async def disconnect_vpn(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    return {"vpn": await _guard(vpn.disconnect(principal, name))}


@router.post("/{name}/test")
async def test_vpn(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    return {"vpn": await _guard(vpn.test(principal, name))}


@router.post("/{name}/trust")
async def trust_vpn(request: Request, name: str):
    principal = await _principal(request, allow_as=False)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, 'body must be {"sha256": "<hex>"}')
    return {"vpn": await _guard(vpn.trust(principal, name, body.get("sha256")))}


# ---------------------------------------------------------------------------
# Internal: app-mcp (runs as hermes, cannot sudo) asks the dashboard (root)
# to mount network folders and to import their files into the file store.
# ---------------------------------------------------------------------------


class AppMcpTokenProvider(DashboardAuthProvider):
    """Bearer-token provider for app-mcp's loopback calls; no login flow."""

    name = "app-mcp-internal"
    display_name = "app-mcp (service credential)"
    supports_token = True
    supports_session = False

    def verify_token(self, *, token: str) -> Optional[TokenPrincipal]:
        secret = os.environ.get(APP_MCP_TOKEN_ENV, "").strip()
        if len(secret) < _MIN_TOKEN_CHARS or not token:
            return None
        if hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
            return TokenPrincipal(principal="app-mcp", provider=self.name, scopes=("vpn-folders",))
        return None

    def start_login(self, *, redirect_uri: str):
        raise NotImplementedError("app-mcp-internal is a service credential")

    def complete_login(self, *, code: str, state: str, code_verifier: str, redirect_uri: str):
        raise NotImplementedError("app-mcp-internal is a service credential")

    def verify_session(self, *, access_token: str):
        return None

    def refresh_session(self, *, refresh_token: str):
        raise NotImplementedError("app-mcp-internal is a service credential")

    def revoke_session(self, *, refresh_token: str) -> None:
        return None


def install_internal_auth() -> None:
    """Register the app-mcp token provider and opt the internal routes into the
    token-auth seam. Idempotent. Without ``APP_MCP_HERMES_TOKEN`` (>= 32
    chars) every internal call fails closed with 401."""
    from hermes_cli.dashboard_auth.registry import get_provider, register_provider
    from hermes_cli.dashboard_auth.token_auth import register_token_route

    if get_provider(AppMcpTokenProvider.name) is None:
        register_provider(AppMcpTokenProvider())
    for path in INTERNAL_ROUTES:
        register_token_route(path)


def _require_app_mcp(request: Request) -> None:
    caller = getattr(request.state, "token_principal", None)
    if caller is None or getattr(caller, "provider", None) != AppMcpTokenProvider.name:
        raise HTTPException(401, "Unauthorized")
    if request.headers.get("x-forwarded-for") or request.headers.get("forwarded"):
        raise HTTPException(403, "internal endpoint is loopback-only")


@internal_router.get("/folders")
async def internal_list_folders(request: Request):
    _require_app_mcp(request)
    return {"folders": await _guard(vpn_folders.internal_list())}


@internal_router.post("/folders/ensure")
async def internal_ensure_folder(request: Request):
    _require_app_mcp(request)
    body = await _json_body(request)

    async def run():
        cred = await vpn_folders.find_by_id(body.get("id"))
        await vpn_folders.ensure(cred, agent=True)
        return await vpn_folders.internal_view(cred)

    return {"folder": await _guard(run())}


@internal_router.post("/folders/import")
async def internal_import_file(request: Request):
    _require_app_mcp(request)
    body = await _json_body(request)
    approved = body.get("approved") is True
    try:
        return await vpn_folders.import_file(body.get("id"), body.get("path"), approved=approved)
    except vpn.VpnNotFound as exc:
        raise HTTPException(404, str(exc))
    except vpn.VpnError as exc:
        return {"ok": False, "detail": str(exc)}
