"""Tests for the agent-home app-channel platform plugin.

Covers topic resolution (find-or-create with the global unique-title
invariant), transcript delivery, plugin registration, and the web-push
fan-out (BFF config pull + a stubbed pywebpush — no network).
"""

import asyncio
import json
import sys
import types
from unittest import mock

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    # Explicit path — SessionDB()'s default is resolved at import time and
    # would land outside the per-test HERMES_HOME.
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


@pytest.fixture(autouse=True)
def _default_db_path(db, monkeypatch):
    """deliver.py opens SessionDB() with the default path — point it at this test's DB."""
    monkeypatch.setattr("hermes_state.DEFAULT_DB_PATH", db.db_path)


# ── Topic resolution ──────────────────────────────────────────────────


def test_resolve_topic_creates_agent_home_session(db):
    from plugins.platforms.app.deliver import resolve_topic

    session_id = resolve_topic(db, "Daily Reports")
    session = db.get_session(session_id)
    assert session is not None
    assert session["source"] == "agent_home"
    assert session["title"] == "Daily Reports"


def test_resolve_topic_reuses_existing_titled_session(db):
    from plugins.platforms.app.deliver import resolve_topic

    first = resolve_topic(db, "Daily Reports")
    second = resolve_topic(db, "Daily Reports")
    assert first == second


def test_resolve_topic_reuses_titled_session_from_any_source(db):
    from plugins.platforms.app.deliver import resolve_topic

    # Titles are globally unique — an existing titled session IS the topic
    # even if another surface created it.
    db.ensure_session("gw_session_1", source="telegram")
    db.set_session_title("gw_session_1", "Ops Alerts")
    assert resolve_topic(db, "Ops Alerts") == "gw_session_1"


def test_resolve_topic_passes_through_session_ids(db):
    from plugins.platforms.app.deliver import resolve_topic

    db.ensure_session("home_123_abcd", source="agent_home")
    assert resolve_topic(db, "home_123_abcd") == "home_123_abcd"


# ── Delivery ──────────────────────────────────────────────────────────


def test_deliver_to_topic_appends_assistant_message(db):
    from plugins.platforms.app import deliver as deliver_mod

    with mock.patch("plugins.platforms.app.push.send_push", _fake_send_push(0)):
        result = asyncio.run(deliver_mod.deliver_to_topic("Daily Reports", "All green today."))

    assert result["success"] is True
    session_id = result["session_id"]
    messages = db.get_messages(session_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "All green today."


def test_deliver_to_topic_rejects_empty_inputs():
    from plugins.platforms.app import deliver as deliver_mod

    assert "error" in asyncio.run(deliver_mod.deliver_to_topic("", "text"))
    assert "error" in asyncio.run(deliver_mod.deliver_to_topic("Topic", ""))


def _fake_send_push(pushed):
    async def send_push(session_id, title, body, url=""):
        return pushed

    return send_push


# ── Web Push fan-out ──────────────────────────────────────────────────


def _push_config(count=1):
    return {
        "vapid_private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
        "subscriptions": [
            {
                "endpoint": f"https://push.example/{i}",
                "keys": {"p256dh": f"k{i}", "auth": f"a{i}"},
            }
            for i in range(count)
        ],
    }


def test_send_push_without_secret_returns_zero(monkeypatch):
    from plugins.platforms.app.push import send_push

    monkeypatch.delenv("APP_PUSH_SECRET", raising=False)
    assert asyncio.run(send_push("s1", "t", "b")) == 0


def test_send_push_without_enrollment_returns_zero(monkeypatch):
    from plugins.platforms.app import push as push_mod

    monkeypatch.setenv("APP_PUSH_SECRET", "s3cret")
    monkeypatch.setattr(push_mod, "_fetch_push_config", _async_value(None))
    assert asyncio.run(push_mod.send_push("s1", "t", "b")) == 0


def test_send_push_fans_out_and_deep_links(monkeypatch):
    from plugins.platforms.app import push as push_mod

    monkeypatch.setenv("APP_PUSH_SECRET", "s3cret")
    monkeypatch.setattr(push_mod, "_fetch_push_config", _async_value(_push_config(2)))

    sent = []

    def fake_webpush(subscription_info=None, data=None, **kwargs):
        sent.append((subscription_info, json.loads(data)))

    with mock.patch.dict(sys.modules, {"pywebpush": _fake_pywebpush(fake_webpush)}):
        pushed = asyncio.run(push_mod.send_push("sess-1", "Daily Reports", "All green."))

    assert pushed == 2
    payload = sent[0][1]
    assert payload["url"] == "/chat?session=sess-1"
    assert payload["title"] == "Daily Reports"
    assert payload["tag"] == "sess-1"
    assert sent[0][0]["endpoint"] == "https://push.example/0"
    assert sent[1][0]["endpoint"] == "https://push.example/1"


def test_send_push_drops_expired_subscriptions(monkeypatch):
    from plugins.platforms.app import push as push_mod

    monkeypatch.setenv("APP_PUSH_SECRET", "s3cret")
    monkeypatch.setattr(push_mod, "_fetch_push_config", _async_value(_push_config(1)))

    dropped = []

    async def fake_drop(endpoint):
        dropped.append(endpoint)

    monkeypatch.setattr(push_mod, "_drop_subscription", fake_drop)

    class WebPushException(Exception):
        def __init__(self, status):
            super().__init__(f"HTTP {status}")
            self.response = types.SimpleNamespace(status_code=status)

    def fake_webpush(**kwargs):
        raise WebPushException(410)

    fake_pkg = _fake_pywebpush(fake_webpush)
    fake_pkg.WebPushException = WebPushException
    with mock.patch.dict(sys.modules, {"pywebpush": fake_pkg}):
        pushed = asyncio.run(push_mod.send_push("s", "t", "b"))

    assert pushed == 0
    assert dropped == ["https://push.example/0"]


def _async_value(value):
    async def _fetch():
        return value

    return _fetch


def _fake_pywebpush(webpush_fn):
    fake_pkg = types.ModuleType("pywebpush")
    fake_pkg.webpush = webpush_fn

    class WebPushException(Exception):
        pass

    fake_pkg.WebPushException = WebPushException
    return fake_pkg


# ── Plugin registration ───────────────────────────────────────────────


def test_register_wires_cron_and_standalone_delivery():
    from plugins.platforms.app.adapter import register

    captured = {}

    class FakeCtx:
        def register_platform(self, **kwargs):
            captured.update(kwargs)

    register(FakeCtx())
    assert captured["name"] == "app"
    assert captured["cron_deliver_env_var"] == "APP_HOME_CHANNEL"
    assert callable(captured["standalone_sender_fn"])
    assert captured["check_fn"]() is True


def test_env_enablement_requires_home_channel(monkeypatch):
    from plugins.platforms.app.adapter import _env_enablement

    monkeypatch.delenv("APP_HOME_CHANNEL", raising=False)
    assert _env_enablement() is None

    monkeypatch.setenv("APP_HOME_CHANNEL", "Daily Reports")
    seed = _env_enablement()
    assert seed["home_channel"] == "Daily Reports"


def test_standalone_send_delivers(db):
    from plugins.platforms.app.adapter import _standalone_send

    with mock.patch("plugins.platforms.app.push.send_push", _fake_send_push(0)):
        result = asyncio.run(_standalone_send(None, "Daily Reports", "Standalone hello"))

    assert result["success"] is True
    messages = db.get_messages(result["session_id"])
    assert messages[-1]["content"] == "Standalone hello"
