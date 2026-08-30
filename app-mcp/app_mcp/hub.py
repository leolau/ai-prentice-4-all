"""The browser-session hub: state tracking + command round-trips.

One browser session at a time matters (single-owner box): the newest
connection wins. The hub keeps the last reported UI state and brokers
command/result pairs over the WebSocket with per-command futures and a
timeout, so an MCP tool always resolves — either with the browser's answer
or with a structured failure.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Protocol


class WsLike(Protocol):
    """Anything with an async send — keeps the hub testable without websockets."""

    async def send(self, message: str) -> None: ...


class HubError(RuntimeError):
    """A user-safe failure an MCP tool can hand straight back to the agent."""


class Hub:
    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._conn: WsLike | None = None
        self._user: str | None = None
        self._path: str | None = None
        self._element: dict[str, Any] | None = None
        self._state_at: float = 0.0
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    # -- connection lifecycle ------------------------------------------------

    def attach(self, conn: WsLike, user: str | None) -> None:
        if self._conn is not None and self._conn is not conn:
            # Newest connection wins; drop the stale one's pending work.
            self._fail_pending("Superseded by a newer app session")
        self._conn = conn
        self._user = user

    def detach(self, conn: WsLike) -> None:
        if self._conn is conn:
            self._conn = None
            self._user = None
            self._fail_pending("The app disconnected mid-action")

    def _fail_pending(self, detail: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result({"ok": False, "detail": detail, "state": None})
        self._pending.clear()

    # -- inbound --------------------------------------------------------------

    def update_state(self, path: Any, element: Any) -> None:
        if isinstance(path, str) and path:
            self._path = path
        if isinstance(element, dict):
            self._element = element
        elif element is None:
            self._element = None
        self._state_at = time.time()

    def resolve_result(self, msg_id: Any, payload: dict[str, Any]) -> None:
        if not isinstance(msg_id, str):
            return
        fut = self._pending.pop(msg_id, None)
        if fut is not None and not fut.done():
            clean = {k: v for k, v in payload.items() if k not in ("type", "id")}
            fut.set_result(clean)

    # -- outbound -------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def state_summary(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "user": self._user,
            "page": self._path,
            "element": self._element,
            "state_age_s": round(time.time() - self._state_at, 1) if self._state_at else None,
        }

    async def send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send one command to the browser and await its structured result."""
        conn = self._conn
        if conn is None:
            raise HubError("No app session connected — the user's app is not reachable.")
        msg_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = fut
        try:
            await conn.send(json.dumps({"type": "cmd", "id": msg_id, "command": command}))
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise HubError("Timed out waiting for the app to execute the action.") from None
        finally:
            self._pending.pop(msg_id, None)
