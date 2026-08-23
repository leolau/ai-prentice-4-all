"""Ticket verification for the app-mcp WebSocket.

The agent-home BFF signs a short-lived ticket ``<user_id>.<expiry_ms>.<sig>``
with HMAC-SHA256 over ``<user_id>.<expiry_ms>``, base64url without padding
(Node's ``digest("base64url")``). This module verifies it with the same
shared secret. A ticket grants exactly one connection attempt inside its
window — nothing more.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time


def verify_ticket(ticket: str, secret: str, now_ms: float | None = None) -> str | None:
    """Return the user_id when the ticket is authentic and unexpired, else None."""
    if not ticket or not secret:
        return None
    parts = ticket.split(".")
    if len(parts) != 3:
        return None
    user_id, expires_raw, sig = parts
    if not user_id or not expires_raw:
        return None
    payload = f"{user_id}.{expires_raw}".encode("utf-8")
    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    ).rstrip(b"=")
    if not hmac.compare_digest(expected.decode("ascii"), sig):
        return None
    try:
        expires = int(expires_raw)
    except ValueError:
        return None
    now = now_ms if now_ms is not None else time.time() * 1000
    if now > expires:
        return None
    return user_id
