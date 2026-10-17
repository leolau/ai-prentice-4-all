"""HTTP routes for "Private networks (VPN)" (Settings → In&Out in agent-home).

Entries are ``fortinet-vpn`` credentials managed through
``/api/credentials/fortinet/{name}``; this router connects, disconnects,
tests and trusts them (see ``hermes_cli/vpn.py``). Connection failures are
``200`` with ``status.state == "error"``; only unknown names (404) and bad
input (400) are HTTP errors. Responses never carry passwords.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from hermes_cli import vpn

router = APIRouter(prefix="/api/vpn")


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
