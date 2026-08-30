"""Topic resolution and delivery for the agent-home platform plugin.

A "topic" is one chat session in the session database. Delivering appends
the report to that session's transcript (an assistant message — exactly
what the agent-home ``/chat`` page renders) and then pushes a notification
to every enrolled device.

Topic names and session ids are both valid delivery targets. Session titles
are globally unique (``idx_sessions_title_unique``), so an existing titled
session IS the topic regardless of which surface created it; only topics
that don't exist yet are created with ``source="agent_home"`` so they show
up in the app's Chats list on their own.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from hermes_state import SessionDB

logger = logging.getLogger(__name__)


def resolve_topic(db: "SessionDB", name_or_id: str) -> str:
    """Resolve a delivery target to a session id, creating the topic if new."""
    if db.get_session(name_or_id):
        return name_or_id

    existing = db.get_session_by_title(name_or_id)
    if existing:
        return existing["id"]

    session_id = f"home_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    db.ensure_session(session_id, source="agent_home")
    try:
        db.set_session_title(session_id, name_or_id)
    except ValueError:
        # Lost the title race (unique index) — the winner is the topic.
        winner = db.get_session_by_title(name_or_id)
        if winner:
            return winner["id"]
        raise
    return session_id


async def deliver_to_topic(topic: str, content: str) -> Dict[str, Any]:
    """Deliver ``content`` into ``topic`` and notify enrolled devices."""
    topic = (topic or "").strip()
    if not topic:
        return {"error": "app delivery: empty topic"}
    if not content:
        return {"error": "app delivery: empty message"}

    from hermes_state import SessionDB

    db = SessionDB()
    try:
        session_id = resolve_topic(db, topic)
        db.append_message(session_id, "assistant", content)
        session = db.get_session(session_id) or {}
        title = session.get("title") or topic
    finally:
        db.close()

    from plugins.platforms.app.push import send_push

    preview = content.strip().splitlines()[0] if content.strip() else title
    pushed = await send_push(session_id, title=title, body=preview)
    logger.info(
        "app delivery: topic=%r session=%s pushed=%d", topic, session_id, pushed
    )
    return {
        "success": True,
        "platform": "app",
        "chat_id": topic,
        "session_id": session_id,
        "pushed": pushed,
    }
