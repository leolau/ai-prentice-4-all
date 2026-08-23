"""app-mcp service entry point: WebSocket hub + MCP tools.

Two listeners, both loopback:

- **WS hub** (default 127.0.0.1:9221) — the browser bridge in agent-home
  connects at ``/app-mcp/ws?ticket=…``. Caddy fronts that one path on the
  public origin; nothing else of this service is exposed.
- **MCP** (default 127.0.0.1:9220, streamable HTTP at ``/mcp``) — consumed by
  the Hermes agent via ``mcp_servers: app:`` in its config. Tools register as
  ``mcp_app_*``; ``mcp_app_app_act_destructive`` is gated by Hermes'
  ``approvals.tools`` so destructive UI actions surface the approval card.

Config via env: ``APP_MCP_TICKET_SECRET`` (required — shared with the
agent-home BFF's ``AGENT_HOME_APP_MCP_SECRET``), ``APP_MCP_PORT``,
``APP_MCP_WS_PORT``, ``APP_MCP_COMMAND_TIMEOUT``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from mcp.server.fastmcp import FastMCP

from .guard import looks_destructive
from .hub import Hub, HubError
from .pages import PAGES
from .ticket import verify_ticket

log = logging.getLogger("app-mcp")

TICKET_SECRET = os.environ.get("APP_MCP_TICKET_SECRET", "")
MCP_PORT = int(os.environ.get("APP_MCP_PORT", "9220"))
WS_PORT = int(os.environ.get("APP_MCP_WS_PORT", "9221"))
COMMAND_TIMEOUT = float(os.environ.get("APP_MCP_COMMAND_TIMEOUT", "10"))

hub = Hub(timeout=COMMAND_TIMEOUT)
mcp = FastMCP("app", host="127.0.0.1", port=MCP_PORT)

SAFE_ACTIONS = {"click", "type", "select", "focus", "read", "scroll", "navigate", "snapshot"}
DESTRUCTIVE_REFUSAL = (
    "That element looks destructive or externally visible. Use app_act_destructive "
    "instead — it goes through the user's approval flow."
)


# ---------------------------------------------------------------------------
# WebSocket side
# ---------------------------------------------------------------------------

async def _ws_handler(conn: websockets.ServerConnection) -> None:
    request = conn.request
    parsed = urlparse(request.path if request else "")
    if parsed.path.rstrip("/") != "/app-mcp/ws":
        await conn.close(4004, "Unknown path")
        return
    ticket = parse_qs(parsed.query).get("ticket", [""])[0]
    user = verify_ticket(ticket, TICKET_SECRET)
    if user is None:
        await conn.close(4001, "Invalid or expired ticket")
        return
    hub.attach(conn, user)
    log.info("app-mcp: browser session attached (user=%s)", user)
    try:
        async for raw in conn:
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            kind = msg.get("type")
            if kind == "state":
                hub.update_state(msg.get("path"), msg.get("element"))
            elif kind == "result":
                hub.resolve_result(msg.get("id"), msg)
    finally:
        hub.detach(conn)
        log.info("app-mcp: browser session detached (user=%s)", user)


# ---------------------------------------------------------------------------
# Helpers shared by the tools
# ---------------------------------------------------------------------------

async def _element_name(
    element_id: int | None, name: str | None, selector: str | None
) -> str | None:
    """Best-effort accessible name of the target, for the destructive guard."""
    if name:
        return name
    if element_id is None and not selector:
        return None
    try:
        snap = await hub.send_command({"type": "snapshot"})
    except HubError:
        return None
    if not snap.get("ok"):
        return None
    for entry in snap.get("elements") or []:
        if element_id is not None and entry.get("id") == element_id:
            return entry.get("name")
        if selector and entry.get("selector") == selector:
            return entry.get("name")
    return None


def _build_command(
    action: str,
    element_id: int | None,
    name: str | None,
    selector: str | None,
    value: str | None,
    path: str | None,
) -> dict[str, Any]:
    if action == "snapshot":
        return {"type": "snapshot"}
    if action == "navigate":
        return {"type": "navigate", "path": path or "/"}
    command: dict[str, Any] = {"type": action}
    if element_id is not None:
        command["elementId"] = element_id
    if name:
        command["name"] = name
    if selector:
        command["selector"] = selector
    if action in ("type", "select"):
        command["value"] = value or ""
    return command


async def _run_action(
    action: str,
    element_id: int | None,
    name: str | None,
    selector: str | None,
    value: str | None,
    path: str | None,
    enforce_guard: bool,
) -> dict[str, Any]:
    if action not in SAFE_ACTIONS:
        return {"ok": False, "detail": f"Unknown action '{action}'. One of: {sorted(SAFE_ACTIONS)}"}
    if enforce_guard and action in ("click", "select"):
        target_name = await _element_name(element_id, name, selector)
        if looks_destructive(target_name):
            return {"ok": False, "detail": DESTRUCTIVE_REFUSAL, "element": target_name}
    try:
        return await hub.send_command(
            _build_command(action, element_id, name, selector, value, path)
        )
    except HubError as err:
        return {"ok": False, "detail": str(err)}


# ---------------------------------------------------------------------------
# MCP tools (registered as mcp_app_* on the Hermes side)
# ---------------------------------------------------------------------------

@mcp.tool()
async def app_state() -> dict[str, Any]:
    """Which page of agent-home the user is on and which UI element they last
    touched. Use this to resolve references like \"this page\" or \"the button
    I'm on\" before acting."""
    return hub.state_summary()


@mcp.tool()
async def app_pages() -> dict[str, Any]:
    """The map of agent-home pages (route, name, purpose), with the user's
    current page marked. For what is ON the current page, use app_describe_page."""
    current = hub.state_summary().get("page")
    return {
        "current": current,
        "pages": [{**p, "current": p["path"] == current} for p in PAGES],
    }


@mcp.tool()
async def app_describe_page() -> dict[str, Any]:
    """Detailed description of every interactive element on the page the user
    is on right now: id, role, accessible name, value/checked/disabled and a
    selector. Use the ids with app_act / app_act_destructive."""
    try:
        result = await hub.send_command({"type": "snapshot"})
    except HubError as err:
        return {"ok": False, "detail": str(err)}
    if not result.get("ok"):
        return result
    elements = result.get("elements") or []
    return {
        "ok": True,
        "page": hub.state_summary().get("page"),
        "element_count": len(elements),
        "elements": elements,
    }


@mcp.tool()
async def app_act(
    action: str,
    element_id: int | None = None,
    name: str | None = None,
    selector: str | None = None,
    value: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Perform a SAFE action on the user's app UI and report the outcome.
    Actions: click, type, select, focus, read, scroll (target one element by
    element_id from app_describe_page, or by name/selector); navigate (pass
    path, e.g. \"/todos\"); snapshot. Refuses elements that look destructive —
    those need app_act_destructive."""
    return await _run_action(action, element_id, name, selector, value, path, enforce_guard=True)


@mcp.tool()
async def app_act_destructive(
    action: str,
    element_id: int | None = None,
    name: str | None = None,
    selector: str | None = None,
    value: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Perform a DESTRUCTIVE or externally-visible action on the user's app UI
    (delete, archive, send, submit…). This tool is gated by the user's
    approval flow — the user sees exactly what will happen and approves or
    denies before anything runs. Same parameters as app_act."""
    return await _run_action(action, element_id, name, selector, value, path, enforce_guard=False)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def _main() -> None:
    if not TICKET_SECRET:
        raise SystemExit(
            "app-mcp: APP_MCP_TICKET_SECRET is not set — refusing to start "
            "(the browser bridge would be unauthenticated)."
        )
    async with websockets.serve(_ws_handler, "127.0.0.1", WS_PORT):
        log.info("app-mcp: WS hub on 127.0.0.1:%s, MCP on 127.0.0.1:%s/mcp", WS_PORT, MCP_PORT)
        await mcp.run_streamable_http_async()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
