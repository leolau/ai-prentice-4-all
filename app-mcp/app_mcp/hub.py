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
        # monotonic() of the last progress ping per in-flight command; makes
        # `timeout` a silence deadline so long-running commands (a large
        # file import uploads over HTTP for minutes) don't hit a fixed total
        # cap they can never beat.
        self._progress: dict[str, float] = {}
        # (bytes_sent, bytes_total) from the same pings, when the browser
        # includes them (currently only importFile) — lets folder_bridge_state
        # report real upload progress instead of just "still alive".
        self._progress_bytes: dict[str, tuple[int, int]] = {}

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
        for msg_id in self._pending:
            self._progress.pop(msg_id, None)
            self._progress_bytes.pop(msg_id, None)
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

    def note_progress(
        self, msg_id: Any, sent: Any = None, total: Any = None
    ) -> None:
        """Refresh the silence deadline for an in-flight command.

        The browser pings `{type: "progress", id}` while a command runs;
        each ping resets the clock so `send_command` only fails after
        `timeout` seconds with no sign of life. `sent`/`total` are optional
        byte counts (currently sent only by importFile) surfaced through
        `state_summary()` for real upload progress, not just liveness.
        """
        if isinstance(msg_id, str) and msg_id in self._pending:
            self._progress[msg_id] = time.monotonic()
            if isinstance(sent, (int, float)) and isinstance(total, (int, float)):
                self._progress_bytes[msg_id] = (int(sent), int(total))

    def resolve_result(self, msg_id: Any, payload: dict[str, Any]) -> None:
        if not isinstance(msg_id, str):
            return
        fut = self._pending.pop(msg_id, None)
        self._progress_bytes.pop(msg_id, None)
        if fut is not None and not fut.done():
            clean = {k: v for k, v in payload.items() if k not in ("type", "id")}
            fut.set_result(clean)

    # -- outbound -------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def state_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "connected": self.connected,
            "user": self._user,
            "page": self._path,
            "element": self._element,
            "state_age_s": round(time.time() - self._state_at, 1) if self._state_at else None,
        }
        # Surface the most advanced in-flight upload, if any — a single
        # browser session runs one Folder Bridge command at a time, so
        # "most bytes sent" is effectively "the current one".
        if self._progress_bytes:
            sent, total = max(self._progress_bytes.values(), key=lambda st: st[0])
            summary["active_upload"] = {
                "bytes_sent": sent,
                "bytes_total": total,
                "percent": round(100 * sent / total, 1) if total else None,
            }
        return summary

    async def send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send one command to the browser and await its structured result."""
        conn = self._conn
        if conn is None:
            raise HubError("No app session connected — the user's app is not reachable.")
        msg_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = fut
        self._progress[msg_id] = time.monotonic()
        try:
            await conn.send(json.dumps({"type": "cmd", "id": msg_id, "command": command}))
            while True:
                try:
                    # shield() so a slice timeout doesn't cancel the future —
                    # the browser may still be working and pinging progress.
                    return await asyncio.wait_for(
                        asyncio.shield(fut), timeout=self.timeout
                    )
                except asyncio.TimeoutError:
                    last = self._progress.get(msg_id, 0.0)
                    if time.monotonic() - last < self.timeout:
                        continue
                    raise HubError(
                        "Timed out waiting for the app to execute the action."
                    ) from None
        finally:
            self._pending.pop(msg_id, None)
            self._progress.pop(msg_id, None)
