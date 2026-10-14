"""Chats are private to the person who started them.

Real ``SessionDB`` under a throwaway ``HERMES_HOME``; only the C1 principal
resolver (Postgres) and the login subject are stubbed. A login limited to its
own chats must not list, read, search, continue, rename, tag, export, or delete
anyone else's conversation; the owner and a no-login operator caller see all.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

OWNER = SimpleNamespace(user_id="root", display="Owner", role="owner", is_owner=True)
MIA = SimpleNamespace(user_id="mia", display="Mia", role="member", is_owner=False)
TOM = SimpleNamespace(user_id="tom", display="Tom", role="member", is_owner=False)


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
def acting(monkeypatch):
    """``acting["as"]`` is the logged-in principal (``None`` → no login)."""
    from hermes_cli import web_server

    state = {"as": OWNER}

    async def _fake_principal(request, *, allow_as=False):
        return state["as"] or OWNER

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)
    monkeypatch.setattr(
        web_server,
        "_comms_session_subject",
        lambda request: state["as"].user_id if state["as"] else "",
    )
    return state


@pytest.fixture
def client(acting):
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


@pytest.fixture
def chats():
    """Mia's chat, Tom's chat (with a compression continuation), and a legacy
    agent-home chat with no owner recorded."""
    import hermes_state

    db = hermes_state.SessionDB()
    try:
        db.ensure_session("s-mia", source="agent_home", user_id="mia")
        db.append_message("s-mia", "user", "mia budget question")
        db.ensure_session("s-tom", source="agent_home", user_id="tom")
        db.append_message("s-tom", "user", "tom salary secret")
        db.end_session("s-tom", "compression")
        db.create_session("s-tom-2", source="agent_home", parent_session_id="s-tom")
        db.append_message("s-tom-2", "user", "tom salary continued")
        db.ensure_session("s-legacy", source="agent_home")
        db.append_message("s-legacy", "user", "owner legacy salary note")
    finally:
        db.close()


def _listed(client):
    resp = client.get("/api/sessions", params={"limit": 50})
    assert resp.status_code == 200
    return {s["id"] for s in resp.json()["sessions"]}, resp.json()["total"]


def test_child_rows_inherit_the_conversation_owner(chats):
    import hermes_state

    db = hermes_state.SessionDB()
    try:
        assert db.get_session("s-tom-2")["user_id"] == "tom"
        # A pre-existing continuation with no owner still resolves via its root.
        db._conn.execute("UPDATE sessions SET user_id = NULL WHERE id = 's-tom-2'")
        db._conn.commit()
        assert db.session_owner_user_id("s-tom-2") == "tom"
        assert db.session_owner_user_id("s-legacy") is None
    finally:
        db.close()


def test_member_lists_only_their_own_chats(client, acting, chats):
    acting["as"] = MIA
    ids, total = _listed(client)
    assert ids == {"s-mia"} and total == 1

    acting["as"] = OWNER
    ids, _ = _listed(client)
    assert {"s-mia", "s-legacy"} <= ids
    assert ids & {"s-tom", "s-tom-2"}


def test_operator_caller_without_login_sees_everything(client, acting, chats):
    acting["as"] = None
    ids, _ = _listed(client)
    assert {"s-mia", "s-legacy"} <= ids


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/sessions/{sid}", None),
        ("get", "/api/sessions/{sid}/messages", None),
        ("get", "/api/sessions/{sid}/export", None),
        ("get", "/api/sessions/{sid}/tags", None),
        ("post", "/api/sessions/{sid}/tags", {"name": "peek"}),
        ("patch", "/api/sessions/{sid}", {"title": "mine now"}),
        ("delete", "/api/sessions/{sid}", None),
        ("get", "/api/sessions/{sid}/latest-descendant", None),
        ("get", "/api/sessions/{sid}/active_run", None),
        ("post", "/api/sessions/{sid}/chat", {"message": "hi"}),
        ("post", "/api/sessions/{sid}/chat/stream", {"message": "hi"}),
    ],
)
@pytest.mark.parametrize("sid", ["s-tom", "s-tom-2", "s-legacy"])
def test_member_cannot_touch_someone_elses_chat(client, acting, chats, method, path, body, sid):
    acting["as"] = MIA
    kwargs = {"json": body} if body is not None else {}
    resp = getattr(client, method)(path.format(sid=sid), **kwargs)
    assert resp.status_code == 404, resp.text

    import hermes_state

    db = hermes_state.SessionDB()
    try:
        assert db.get_session(sid) is not None
        assert db.get_session_title(sid) in (None, "")
    finally:
        db.close()


def test_member_reads_own_chat_and_owner_reads_any(client, acting, chats):
    acting["as"] = MIA
    assert client.get("/api/sessions/s-mia/messages").status_code == 200
    acting["as"] = OWNER
    resp = client.get("/api/sessions/s-tom/messages")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "s-tom-2"


def test_search_hides_other_peoples_messages(client, acting, chats):
    acting["as"] = MIA
    found = client.get("/api/sessions/search", params={"q": "salary"}).json()["results"]
    assert found == []
    by_id = client.get("/api/sessions/search", params={"q": "s-tom"}).json()["results"]
    assert by_id == []

    acting["as"] = OWNER
    found = client.get("/api/sessions/search", params={"q": "salary"}).json()["results"]
    assert {r["lineage_root"] for r in found} == {"s-tom", "s-legacy"}


def test_bulk_delete_only_removes_own_chats(client, acting, chats):
    acting["as"] = MIA
    resp = client.post("/api/sessions/bulk-delete", json={"ids": ["s-mia", "s-tom", "s-legacy"]})
    assert resp.json()["deleted"] == 1
    acting["as"] = OWNER
    ids, _ = _listed(client)
    assert "s-mia" not in ids and "s-legacy" in ids


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/sessions/stats", None),
        ("get", "/api/sessions/empty/count", None),
        ("delete", "/api/sessions/empty", None),
        ("post", "/api/sessions/prune", {"older_than_days": 1}),
        ("get", "/api/profiles/sessions", None),
    ],
)
def test_profile_wide_session_ops_are_owner_only(client, acting, chats, method, path, body):
    acting["as"] = MIA
    kwargs = {"json": body} if body is not None else {}
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_new_and_lead_chats_are_stamped_with_their_creator(client, acting):
    import hermes_state

    acting["as"] = MIA
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    lead = client.get("/api/sessions/lead").json()["session_id"]

    db = hermes_state.SessionDB()
    try:
        assert db.get_session(sid)["user_id"] == "mia"
        assert db.get_session(lead)["user_id"] == "mia"
    finally:
        db.close()
    ids, _ = _listed(client)
    assert {sid, lead} <= ids

    acting["as"] = TOM
    ids, _ = _listed(client)
    assert not ids & {sid, lead}
