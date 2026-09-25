"""app-mcp service entry point: WebSocket hub(s) + MCP tools.

Three listeners in this one process, all loopback except the two WS paths
Caddy fronts:

- **UI hub** (default 127.0.0.1:9221, path ``/app-mcp/ws``) — the browser
  bridge in agent-home connects here to report page/focus and execute
  ``app_act``/``app_act_destructive`` commands.
- **Folder hub** (same port, path ``/app-mcp/folders/ws``) — the Folder
  Bridge page (``/files/bridge``) connects here so ``folder_bridge_*`` tools
  can list/search/read within folders the user has approved via the File
  System Access API. A separate ``Hub`` instance: a different browser tab,
  a different command vocabulary, a different connection lifecycle (opt-in,
  only while that page is open) from the always-on UI bridge.
- **MCP** (default 127.0.0.1:9220, streamable HTTP at ``/mcp``) — consumed by
  the Hermes agent via ``mcp_servers: app:`` in its config. Tools register as
  ``mcp_app_*``; ``mcp_app_app_act_destructive`` and (recommended)
  ``mcp_app_folder_bridge_read_file`` / ``mcp_app_folder_bridge_search_files``
  / ``mcp_app_folder_bridge_import_file_approved``
  are gated by Hermes' ``approvals.tools`` so the user approves in-chat
  before anything sensitive runs — see README.md "MCP tools" for the full
  recommendation and why reads are gated too.

Config via env: ``APP_MCP_TICKET_SECRET`` (required — shared with the
agent-home BFF's ``AGENT_HOME_APP_MCP_SECRET``), ``APP_MCP_PORT``,
``APP_MCP_WS_PORT``, ``APP_MCP_COMMAND_TIMEOUT``,
``APP_MCP_FOLDER_COMMAND_TIMEOUT`` (folder search/read across a real
directory tree can legitimately take longer than a UI click).
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
from .help import help_for
from .hub import Hub, HubError
from .pages import PAGES
from .ticket import verify_ticket

log = logging.getLogger("app-mcp")

TICKET_SECRET = os.environ.get("APP_MCP_TICKET_SECRET", "")
MCP_PORT = int(os.environ.get("APP_MCP_PORT", "9220"))
WS_PORT = int(os.environ.get("APP_MCP_WS_PORT", "9221"))
COMMAND_TIMEOUT = float(os.environ.get("APP_MCP_COMMAND_TIMEOUT", "10"))
FOLDER_COMMAND_TIMEOUT = float(
    os.environ.get("APP_MCP_FOLDER_COMMAND_TIMEOUT", "30")
)

hub = Hub(timeout=COMMAND_TIMEOUT)
folder_hub = Hub(timeout=FOLDER_COMMAND_TIMEOUT)
mcp = FastMCP("app", host="127.0.0.1", port=MCP_PORT)

SAFE_ACTIONS = {"click", "type", "select", "focus", "read", "scroll", "navigate", "snapshot"}
DESTRUCTIVE_REFUSAL = (
    "That element looks destructive or externally visible. Use app_act_destructive "
    "instead — it goes through the user's approval flow."
)

# WS path -> which Hub owns it. A dict makes `_route_for_path` trivially
# unit testable without opening a real socket.
WS_ROUTES: dict[str, Hub] = {
    "/app-mcp/ws": hub,
    "/app-mcp/folders/ws": folder_hub,
}


# ---------------------------------------------------------------------------
# WebSocket side
# ---------------------------------------------------------------------------

def _route_for_path(path: str) -> Hub | None:
    """Which Hub owns this WS path, or None for an unknown one."""
    return WS_ROUTES.get(path.rstrip("/") or "/")


async def _ws_handler(conn: websockets.ServerConnection) -> None:
    request = conn.request
    parsed = urlparse(request.path if request else "")
    target = _route_for_path(parsed.path)
    if target is None:
        await conn.close(4004, "Unknown path")
        return
    ticket = parse_qs(parsed.query).get("ticket", [""])[0]
    user = verify_ticket(ticket, TICKET_SECRET)
    if user is None:
        await conn.close(4001, "Invalid or expired ticket")
        return
    target.attach(conn, user)
    log.info("app-mcp: session attached (path=%s user=%s)", parsed.path, user)
    try:
        async for raw in conn:
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            kind = msg.get("type")
            if kind == "state":
                target.update_state(msg.get("path"), msg.get("element"))
            elif kind == "result":
                target.resolve_result(msg.get("id"), msg)
    finally:
        target.detach(conn)
        log.info("app-mcp: session detached (path=%s user=%s)", parsed.path, user)


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
    touched. Use this to resolve references like "this page" or "the button
    I'm on" before acting."""
    return hub.state_summary()


@mcp.tool()
async def app_pages() -> dict[str, Any]:
    """The map of agent-home pages (route, name, purpose), with the user's
    current page marked. For what is ON the current page, use app_describe_page;
    for how a page or feature is used, app_page_help."""
    current = hub.state_summary().get("page")
    return {
        "current": current,
        "pages": [
            {**p, "current": p["path"] == current, "has_help": help_for(p["path"]) is not None}
            for p in PAGES
        ],
    }


@mcp.tool()
async def app_page_help(path: str | None = None) -> dict[str, Any]:
    """Usage guide for an agent-home page or feature, written for the user:
    what the screen is for, how to use it end to end, what each option means
    and who may do what. Defaults to the page the user is on; pass a path
    (e.g. "/projects") to explain another one. Use this to answer "how do I
    use …", "what does this page do", "what is the difference between …"."""
    target = path or hub.state_summary().get("page")
    text = help_for(target)
    if text is None:
        return {"ok": False, "path": target, "detail": "No guide for this page."}
    return {"ok": True, "path": target, "help": text}


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
    page = hub.state_summary().get("page")
    return {
        "ok": True,
        "page": page,
        "has_help": help_for(page) is not None,
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
    path, e.g. "/todos"); snapshot. Refuses elements that look destructive —
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
# Folder Bridge tools (registered as mcp_app_folder_bridge_* on the Hermes
# side). Each is a thin forward to `folder_hub` — the browser holds the
# FileSystemDirectoryHandle map and does the actual traversal/read; this
# service only brokers the request/response, exactly like `_run_action`
# above does for UI commands. See README.md for the approvals.tools
# recommendation before enabling this in a shared/production config.
# ---------------------------------------------------------------------------

NOT_CONNECTED_HINT = (
    "No Folder Bridge session connected. Tell the user to open Files -> "
    "Folder Bridge (/files/bridge) in their browser on their Mac and "
    "press Connect, then add at least one folder."
)


async def _folder_command(command: dict[str, Any]) -> dict[str, Any]:
    if not folder_hub.connected:
        return {"ok": False, "detail": NOT_CONNECTED_HINT}
    try:
        return await folder_hub.send_command(command)
    except HubError as err:
        return {"ok": False, "detail": str(err)}


@mcp.tool()
async def folder_bridge_state() -> dict[str, Any]:
    """Whether a Folder Bridge browser session is connected right now, and
    how long ago it last reported in. Use this before the other
    folder_bridge_* tools to know whether to ask the user to connect one
    first (Files -> Folder Bridge, /files/bridge, on their Mac)."""
    return folder_hub.state_summary()


@mcp.tool()
async def folder_bridge_list_folders() -> dict[str, Any]:
    """List every folder the user has approved in their current Folder
    Bridge session: folder id, label, and whether its permission is still
    active. Use the returned folder ids with the other folder_bridge_*
    tools."""
    return await _folder_command({"type": "listFolders"})


@mcp.tool()
async def folder_bridge_list_directory(
    folder_id: str, path: str | None = None
) -> dict[str, Any]:
    """List the contents of a directory inside one approved folder.
    `path` is relative to the folder root (omit or "" for the root)."""
    return await _folder_command(
        {"type": "listDirectory", "folderId": folder_id, "path": path or ""}
    )


@mcp.tool()
async def folder_bridge_search_files(
    folder_ids: list[str],
    query: str,
    extensions: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search by filename (and a short content snippet where feasible)
    across one or more approved folders. `folder_ids` from
    folder_bridge_list_folders; `extensions` optionally narrows to file
    types (e.g. [".md", ".pdf", ".txt"])."""
    return await _folder_command(
        {
            "type": "searchFiles",
            "folderIds": folder_ids,
            "query": query,
            "extensions": extensions or [],
            "limit": limit,
        }
    )


@mcp.tool()
async def folder_bridge_read_file(
    folder_id: str, path: str, max_bytes: int = 200_000
) -> dict[str, Any]:
    """Read a UTF-8 text file's content from an approved folder (capped at
    max_bytes). Binary files (PDF, .numbers/.xlsx, images, ...) are refused
    with `binary: true` — use folder_bridge_import_file for those, which
    copies the bytes intact into Files instead of decoding them. This is a
    read of the user's own local files — Hermes recommends gating this tool
    behind approvals.tools so the user sees and approves each read in chat."""
    return await _folder_command(
        {
            "type": "readFile",
            "folderId": folder_id,
            "path": path,
            "encoding": "utf-8",
            "maxBytes": max_bytes,
        }
    )


async def _import_file(folder_id: str, path: str, *, approved: bool) -> dict[str, Any]:
    return await _folder_command(
        {"type": "importFile", "folderId": folder_id, "path": path, "approved": approved}
    )


@mcp.tool()
async def folder_bridge_import_file(folder_id: str, path: str) -> dict[str, Any]:
    """Copy one file from an approved folder byte-for-byte into Files (the
    file store + registry), any type including PDF/.numbers/.xlsx. The
    browser uploads the bytes directly; the result is the registry row
    (asset id, filename, size, sha256, storage path, `verified` = the stored
    hash matches the original). Only works for folders the user has marked
    "trusted for import" on /files/bridge — otherwise it returns
    `needsApproval: true`; then use folder_bridge_import_file_approved,
    which asks the user in chat."""
    return await _import_file(folder_id, path, approved=False)


@mcp.tool()
async def folder_bridge_import_file_approved(folder_id: str, path: str) -> dict[str, Any]:
    """Same as folder_bridge_import_file, for a folder the user has NOT
    marked trusted: the user approves this specific file in chat first.
    Hermes must list this tool in approvals.tools — that prompt is the
    approval; the browser then accepts the import."""
    return await _import_file(folder_id, path, approved=True)


@mcp.tool()
async def folder_bridge_get_file_metadata(folder_id: str, path: str) -> dict[str, Any]:
    """Get a file's size, last-modified time and path without reading its
    content."""
    return await _folder_command(
        {"type": "getFileMetadata", "folderId": folder_id, "path": path}
    )


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
