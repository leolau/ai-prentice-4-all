"""The lead conversation is the caller's, and it is the same one every time.

Agent-home's lead chat used to pin its session id in ``localStorage``, which
made "one long-running session" one session *per browser*. The id now comes
from ``GET /api/sessions/lead``, derived from the principal, so a phone and a
desktop name the same conversation — and an in-flight turn started on one is
findable from the other.

Real ``SessionDB`` under a throwaway ``HERMES_HOME``; only the principal
resolver is stubbed, and it is stubbed per-test so "each person gets their
own" is actually exercised rather than assumed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except (ImportError, AttributeError):
        pass
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    return tmp_path


@pytest.fixture
def as_principal(monkeypatch):
    """Sign the test client in as a named person; returns the client factory."""
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    def _client(user_id: str):
        person = SimpleNamespace(
            user_id=user_id, display=user_id, role="owner", is_owner=True
        )

        async def _fake_principal(request, *, allow_as=False):
            return person

        monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)
        c = TestClient(app)
        c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
        return c

    return _client


def test_lead_session_is_stable_for_one_person(as_principal):
    client = as_principal("leo")
    first = client.get("/api/sessions/lead")
    second = client.get("/api/sessions/lead")

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["session_id"] == second.json()["session_id"]


def test_two_people_do_not_share_a_lead_conversation(as_principal):
    mine = as_principal("leo").get("/api/sessions/lead").json()["session_id"]
    theirs = as_principal("sam").get("/api/sessions/lead").json()["session_id"]

    assert mine != theirs


def test_lead_session_id_does_not_carry_the_user_id(as_principal):
    """A session id reaches the filesystem and the logs; the identity must not."""
    sid = as_principal("leo@example.com").get("/api/sessions/lead").json()["session_id"]

    assert "leo" not in sid
    assert "@" not in sid


def test_lead_session_row_is_readable_as_a_conversation(as_principal):
    """The id is not just computed — the row exists, so history loads answer."""
    client = as_principal("leo")
    sid = client.get("/api/sessions/lead").json()["session_id"]

    res = client.get(f"/api/sessions/{sid}/messages")
    assert res.status_code == 200


def _compact_into_continuation(root: str, continuation: str) -> None:
    """Do to the database what context compression does: fork a child that
    holds the messages, so the root resolves forward to it."""
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        db.create_session(continuation, source="agent_home", parent_session_id=root)
        db.append_message(continuation, role="user", content="after compaction")
    finally:
        db.close()


def test_active_run_found_through_a_compacted_conversation(as_principal):
    """The second device holds the *root* id; the turn runs under the resume id.

    Comparing the two directly reports "nothing is running" for exactly the
    conversation old enough to have compacted — the long-lived lead chat.
    """
    from hermes_cli import web_server

    client = as_principal("leo")
    sid = client.get("/api/sessions/lead").json()["session_id"]
    _compact_into_continuation(sid, f"{sid}-cont")

    web_server._CHAT_RUNS["run-1"] = {
        "session_id": f"{sid}-cont",
        "done": False,
        "buffer": [],
        "subs": [],
    }
    try:
        assert client.get(f"/api/sessions/{sid}/active_run").json()["run_id"] == "run-1"
    finally:
        web_server._CHAT_RUNS.pop("run-1", None)


def test_a_finished_turn_is_not_reported_as_running(as_principal):
    from hermes_cli import web_server

    client = as_principal("leo")
    sid = client.get("/api/sessions/lead").json()["session_id"]
    _compact_into_continuation(sid, f"{sid}-cont")

    web_server._CHAT_RUNS["run-1"] = {
        "session_id": f"{sid}-cont",
        "done": True,
        "buffer": [],
        "subs": [],
    }
    try:
        assert client.get(f"/api/sessions/{sid}/active_run").json()["run_id"] is None
    finally:
        web_server._CHAT_RUNS.pop("run-1", None)


def test_lead_session_needs_a_principal(_isolate_hermes_home):
    """Unauthenticated, the endpoint hands out no conversation at all."""
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli.web_server import app

    res = TestClient(app).get("/api/sessions/lead")
    assert res.status_code in (401, 403)


def test_lead_session_is_scoped_to_a_profile(as_principal):
    """Profiles are independent islands; a lead chat is asked for inside one."""
    client = as_principal("leo")
    res = client.get("/api/sessions/lead?profile=no-such-profile")

    assert res.status_code == 404


def test_attach_refuses_another_conversations_run(as_principal):
    from hermes_cli import web_server

    client = as_principal("leo")
    sid = client.get("/api/sessions/lead").json()["session_id"]

    web_server._CHAT_RUNS["run-2"] = {
        "session_id": "somebody-elses-session",
        "done": False,
        "buffer": [],
        "subs": [],
    }
    try:
        res = client.post(
            f"/api/sessions/{sid}/chat/stream/attach", json={"run_id": "run-2"}
        )
        assert res.status_code == 404
    finally:
        web_server._CHAT_RUNS.pop("run-2", None)
