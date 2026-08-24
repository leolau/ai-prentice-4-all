"""agent-home platform adapter (Hermes plugin).

Outbound-only channel into the agent-home PWA. A delivery:

1. resolves the topic (a session id or a session title — see ``deliver.py``),
2. appends the message as an assistant row in that session's transcript,
   which is exactly what the app's ``/chat`` page renders, and
3. pushes a Web Push notification to every enrolled device; tapping it
   opens ``/chat?session=<id>``.

There is no inbound stream — the app talks to the agent through the normal
chat API, so ``connect()`` just marks the adapter running.

Configuration in config.yaml::

    platforms:
      app:
        enabled: true

Environment variables:

    APP_HOME_CHANNEL         Default topic for cron / notification delivery
    APP_HOME_CHANNEL_NAME    Human label for the home channel
"""

import logging
import os
from typing import Any, Dict, List, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult

logger = logging.getLogger(__name__)


class AppHomeAdapter(BasePlatformAdapter):
    """Delivery-only adapter; no inbound connection to maintain."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config=config, platform=Platform("app"))

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        self._running = True
        logger.info("[%s] app channel ready (delivery-only)", self.name)
        return True

    async def disconnect(self) -> None:
        self._running = False

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        from plugins.platforms.app.deliver import deliver_to_topic

        result = await deliver_to_topic(chat_id, content)
        if result.get("error"):
            return SendResult(success=False, error=result["error"])
        return SendResult(
            success=True,
            message_id=result["session_id"],
            raw_response=result,
        )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "topic"}


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Out-of-process delivery for cron / send_message fallbacks.

    Used by ``tools/send_message_tool._send_via_adapter`` when the gateway
    runner is not in this process. ``thread_id`` and ``media_files`` are
    accepted for signature parity — a topic transcript takes text only.
    """
    from plugins.platforms.app.deliver import deliver_to_topic

    return await deliver_to_topic(chat_id, message)


def _env_enablement() -> Optional[dict]:
    """Seed PlatformConfig from APP_HOME_CHANNEL so env-only setups work.

    The platform is always "connectable" (no credentials), but it only
    auto-enables once a home channel is configured — otherwise every box
    would grow a channel nobody asked for.
    """
    topic = os.getenv("APP_HOME_CHANNEL", "").strip()
    if not topic:
        return None
    seed: dict = {"home_channel": topic}
    name = os.getenv("APP_HOME_CHANNEL_NAME", "").strip()
    if name:
        seed["home_channel_name"] = name
    return seed


def check_requirements() -> bool:
    """The channel works without pywebpush (messages still land in the
    topic); push only needs VAPID keys, which are generated on demand."""
    return True


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system at startup."""
    ctx.register_platform(
        name="app",
        label="Agent Home",
        adapter_factory=lambda cfg: AppHomeAdapter(cfg),
        check_fn=check_requirements,
        env_enablement_fn=_env_enablement,
        # Cron home-channel delivery — `deliver=app` jobs route to
        # APP_HOME_CHANNEL when no explicit topic is given.
        cron_deliver_env_var="APP_HOME_CHANNEL",
        # Out-of-process cron delivery (cron scheduler runs standalone).
        standalone_sender_fn=_standalone_send,
        install_hint="pip install pywebpush   # optional — enables push notifications",
        pii_safe=True,
    )
