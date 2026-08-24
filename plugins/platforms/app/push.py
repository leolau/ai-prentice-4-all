"""Web Push delivery for the agent-home platform plugin.

The enrollment state (VAPID keys + device subscriptions) is owned by the
agent-home BFF — it's the surface the browser talks to, and it persists
them in Supabase Storage. This sender pulls both over loopback with a
shared-secret header and fans the notification out via ``pywebpush``.

Env vars:

    APP_PUSH_BFF_URL    BFF base URL (default http://127.0.0.1:3100)
    APP_PUSH_SECRET     Shared secret for /api/notifications/config
                        (same value as agent-home's AGENT_HOME_APP_PUSH_SECRET)

``pywebpush`` is imported lazily — the plugin degrades to "store the
message, skip the push" when it isn't installed or enrollment is absent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NOTIFICATION_BODY_LIMIT = 280
BFF_TIMEOUT_SECONDS = 10.0
DEFAULT_BFF_URL = "http://127.0.0.1:3100"


def _bff_url() -> str:
    return os.getenv("APP_PUSH_BFF_URL", DEFAULT_BFF_URL).rstrip("/")


def _secret() -> str:
    return os.getenv("APP_PUSH_SECRET", "").strip()


async def _fetch_push_config() -> Optional[Dict[str, Any]]:
    """Pull {vapid_private_key, subscriptions} from the agent-home BFF."""
    secret = _secret()
    if not secret:
        logger.debug("app push skipped: APP_PUSH_SECRET not configured")
        return None

    import httpx

    try:
        async with httpx.AsyncClient(timeout=BFF_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{_bff_url()}/api/notifications/config",
                headers={"x-app-push-secret": secret},
            )
        if resp.status_code == 404:
            return None  # storage not configured on this box
        if resp.status_code >= 300:
            logger.warning("app push config fetch failed: HTTP %s", resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("app push config fetch failed: %s", exc)
        return None


async def _drop_subscription(endpoint: str) -> None:
    """Ask the BFF to forget a dead subscription (404/410 from the push service)."""
    secret = _secret()
    if not secret:
        return
    import httpx

    try:
        async with httpx.AsyncClient(timeout=BFF_TIMEOUT_SECONDS) as client:
            await client.request(
                "DELETE",
                f"{_bff_url()}/api/notifications/subscribe",
                headers={"x-app-push-secret": secret},
                json={"endpoint": endpoint},
            )
    except Exception as exc:
        logger.debug("app push: dropping dead subscription failed: %s", exc)


async def send_push(session_id: str, title: str, body: str, url: str = "") -> int:
    """Push a notification for ``session_id`` to every enrolled device.

    Returns the number of subscriptions the push was accepted by. Missing
    enrollment or zero subscriptions are not errors — the message is already
    persisted in the topic transcript; the push is the courtesy notification.
    """
    config = await _fetch_push_config()
    if not config:
        return 0
    private_key = config.get("vapid_private_key")
    subscriptions: List[Dict[str, Any]] = config.get("subscriptions") or []
    if not private_key or not subscriptions:
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("app push skipped: pywebpush not installed")
        return 0

    payload = json.dumps(
        {
            "title": title,
            "body": body if len(body) <= NOTIFICATION_BODY_LIMIT
            else body[: NOTIFICATION_BODY_LIMIT - 1] + "…",
            "url": url or f"/chat?session={session_id}",
            "icon": "/icons/icon-192.png",
            "badge": "/icons/icon-192.png",
            "tag": session_id,
        }
    )

    delivered = 0
    for sub in subscriptions:
        endpoint = sub.get("endpoint")
        keys = sub.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            continue
        subscription_info: Dict[str, Any] = {
            "endpoint": endpoint,
            "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
        }
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": "mailto:hermes@localhost"},
            )
            delivered += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # The browser unsubscribed (or the push service expired the
                # endpoint) — drop it so we stop paying for the failure.
                await _drop_subscription(endpoint)
            else:
                logger.warning("app push failed (HTTP %s): %s", status, exc)
        except Exception as exc:  # network errors, bad subscription, ...
            logger.warning("app push failed: %s", exc)
    return delivered
