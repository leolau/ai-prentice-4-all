"""Register channel messages in the durable inbound registry.

The sibling of :mod:`gateway.inbound_files`, for the message rather than its
attachments. Every channel the gateway speaks — Telegram, Discord, Slack,
iMessage, the API server, the CLI — funnels through one chokepoint that knows
the platform, the receiving account, the conversation, the sender and the
resolved internal principal. Downstream, all of that has collapsed into a
session transcript, so this is the only place a scoped, listable row can be
written from.

Registering is not remembering, and not answering: nothing here embeds
anything, and every failure is swallowed so a message the gateway could reply
to is never lost to a bookkeeping error.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from gateway.inbound_files import (
    _principal_for,
    _resolve_owner_principal,
    surface_for,
)

logger = logging.getLogger(__name__)

#: Registry rows are for reading, and a pasted logfile is not a message anyone
#: will read in an inbox. Bodies are clipped rather than dropped so the item
#: still exists and is still findable.
MAX_BODY_CHARS = 20_000


def _conversation_of(source: Any) -> str:
    """The conversation key, thread included when the channel has threads."""
    chat_id = str(getattr(source, "chat_id", "") or "")
    thread_id = str(getattr(source, "thread_id", "") or "")
    return f"{chat_id}#{thread_id}" if thread_id else chat_id


def _body_of(event: Any) -> str:
    for attribute in ("text", "content", "body", "message"):
        value = getattr(event, attribute, None)
        if isinstance(value, str) and value.strip():
            return value[:MAX_BODY_CHARS]
    return ""


async def register_event_item(
    event: Any,
    source: Any,
    *,
    registry: Optional[Any] = None,
    principal_store: Optional[Any] = None,
) -> Optional[Any]:
    """Record ``event`` as an inbound item; returns the row or ``None``.

    Never raises. Mirrors :func:`gateway.inbound_files.register_event_files`
    down to the unenrolled-sender fallback: on a personal deployment a message
    from an external contact was still *received by* the owner, so it is scoped
    to the owner rather than dropped for having no principal of its own.
    """
    try:
        message_id = str(getattr(event, "message_id", "") or "")
        if not message_id:
            # Without a stable external id the upsert key is meaningless and
            # every redelivery would create a new row.
            return None

        principal = _principal_for(source)
        if principal is None and principal_store is not None:
            principal = await _resolve_owner_principal(principal_store)
        if principal is None:
            logger.debug(
                "inbound registry: message from an unenrolled sender left "
                "unregistered"
            )
            return None

        from hermes_cli.inbound_registry import register_arrival

        surface = surface_for(getattr(source, "platform", None))
        return await register_arrival(
            principal,
            surface=surface,
            external_id=message_id,
            account_id=str(getattr(source, "account_id", "") or ""),
            conversation=_conversation_of(source) or None,
            conversation_name=str(getattr(source, "chat_name", "") or "")
            or None,
            sender_id=str(getattr(source, "user_id", "") or "") or None,
            sender_name=str(getattr(source, "user_name", "") or "") or None,
            body=_body_of(event),
            occurred_at=datetime.now(timezone.utc),
            has_attachments=bool(getattr(event, "media_urls", None)),
            registry=registry,
        )
    except Exception as exc:  # noqa: BLE001 - bookkeeping is never fatal
        logger.warning("inbound registry: could not register message (%s)", exc)
        return None
