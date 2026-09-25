import asyncio
import json

import pytest

from app_mcp.hub import Hub, HubError


class FakeConn:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


def test_state_summary_tracks_reports():
    async def scenario():
        hub = Hub()
        assert hub.state_summary()["connected"] is False
        conn = FakeConn()
        hub.attach(conn, "leo_owner")
        hub.update_state("/todos", {"role": "button", "name": "Filter"})
        return hub.state_summary()

    summary = asyncio.run(scenario())
    assert summary["connected"] is True
    assert summary["page"] == "/todos"
    assert summary["element"] == {"role": "button", "name": "Filter"}


def test_command_round_trip_resolves_with_browser_result():
    async def scenario():
        hub = Hub(timeout=1)
        conn = FakeConn()
        hub.attach(conn, "leo_owner")

        async def browser():
            for _ in range(100):
                if conn.sent:
                    break
                await asyncio.sleep(0.01)
            msg = json.loads(conn.sent[0])
            assert msg["type"] == "cmd"
            assert msg["command"] == {"type": "snapshot"}
            hub.resolve_result(msg["id"], {"ok": True, "detail": "3 elements", "elements": []})

        _, result = await asyncio.gather(browser(), hub.send_command({"type": "snapshot"}))
        return result

    assert asyncio.run(scenario())["ok"] is True


def test_command_without_connection_raises_user_safe_error():
    async def scenario():
        hub = Hub()
        with pytest.raises(HubError, match="No app session connected"):
            await hub.send_command({"type": "snapshot"})

    asyncio.run(scenario())


def test_command_times_out_when_browser_never_answers():
    async def scenario():
        hub = Hub(timeout=0.05)
        hub.attach(FakeConn(), "leo_owner")
        with pytest.raises(HubError, match="Timed out"):
            await hub.send_command({"type": "click", "elementId": 1})

    asyncio.run(scenario())


def test_progress_pings_extend_the_deadline():
    async def scenario():
        hub = Hub(timeout=0.1)
        conn = FakeConn()
        hub.attach(conn, "leo_owner")

        async def browser():
            for _ in range(100):
                if conn.sent:
                    break
                await asyncio.sleep(0.01)
            msg_id = json.loads(conn.sent[0])["id"]
            # Stay alive past the raw timeout via progress pings, then answer.
            for _ in range(5):
                await asyncio.sleep(0.06)
                hub.note_progress(msg_id)
            hub.resolve_result(msg_id, {"ok": True})

        _, result = await asyncio.gather(browser(), hub.send_command({"type": "importFile"}))
        return result

    assert asyncio.run(scenario())["ok"] is True


def test_silence_after_a_ping_still_times_out():
    async def scenario():
        hub = Hub(timeout=0.1)
        conn = FakeConn()
        hub.attach(conn, "leo_owner")

        async def browser():
            for _ in range(100):
                if conn.sent:
                    break
                await asyncio.sleep(0.01)
            hub.note_progress(json.loads(conn.sent[0])["id"])  # one ping, then silence

        async def call():
            with pytest.raises(HubError, match="Timed out"):
                await hub.send_command({"type": "importFile"})

        await asyncio.gather(browser(), call())

    asyncio.run(scenario())


def test_progress_for_unknown_id_is_ignored():
    hub = Hub(timeout=0.05)
    hub.note_progress("not-a-real-id")
    hub.note_progress(None)
    assert not hub._progress


def test_detach_fails_inflight_commands():
    async def scenario():
        hub = Hub(timeout=5)
        conn = FakeConn()
        hub.attach(conn, "leo_owner")

        async def dropper():
            for _ in range(100):
                if conn.sent:
                    break
                await asyncio.sleep(0.01)
            hub.detach(conn)

        _, result = await asyncio.gather(dropper(), hub.send_command({"type": "snapshot"}))
        return result

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert "disconnected" in result["detail"]


def test_newer_connection_supersedes_the_stale_one():
    async def scenario():
        hub = Hub(timeout=5)
        first, second = FakeConn(), FakeConn()
        hub.attach(first, "leo_owner")
        hub.attach(second, "leo_owner")
        connected_before = hub.state_summary()["connected"]
        hub.detach(first)  # stale connection going away must not kill the live one
        return connected_before, hub.state_summary()["connected"]

    before, after = asyncio.run(scenario())
    assert before is True
    assert after is True
