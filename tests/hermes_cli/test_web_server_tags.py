"""Endpoint tests for the session tag API routes.

Real ``SessionDB`` (SQLite under a throwaway ``HERMES_HOME``) exercises the DB
boundary; the C1 principal resolver is stubbed so the tests stay hermetic.
Tests focus on the tag CRUD endpoints and tag filtering on ``GET /api/sessions``.
"""

from __future__ import annotations

import time
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
def client(monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    owner = SimpleNamespace(
        user_id="root", display="Root Owner", role="owner", is_owner=True
    )

    async def _fake_principal(request, *, allow_as=False):
        return owner

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def _seed_session(client, sid, title=None):
    """Insert a session directly via the DB so tag endpoints have a target."""
    from hermes_state import SessionDB

    db = SessionDB()
    db._conn.execute(
        "INSERT OR IGNORE INTO sessions "
        "(id, source, started_at, title) VALUES (?, ?, ?, ?)",
        (sid, "agent_home", time.time(), title),
    )
    db._conn.commit()
    db.close()


class TestCreateTag:
    def test_create_tag_endpoint(self, client):
        res = client.post("/api/sessions/tags", json={"name": "bug", "color": "red"})
        assert res.status_code == 200
        tag = res.json()["tag"]
        assert tag["name"] == "bug"
        assert tag["color"] == "red"
        assert tag["id"]
        # Verify it appears in list_tags with session_count = 0
        tags = client.get("/api/sessions/tags").json()["tags"]
        assert len(tags) == 1
        assert tags[0]["session_count"] == 0

    def test_create_tag_missing_name(self, client):
        res = client.post("/api/sessions/tags", json={})
        assert res.status_code == 400

    def test_create_tag_idempotent(self, client):
        first = client.post("/api/sessions/tags", json={"name": "bug", "color": "red"})
        second = client.post("/api/sessions/tags", json={"name": "Bug", "color": "green"})
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["tag"]["id"] == second.json()["tag"]["id"]
        # Only one tag row
        tags = client.get("/api/sessions/tags").json()["tags"]
        assert len(tags) == 1


class TestListTags:
    def test_list_tags_empty(self, client):
        res = client.get("/api/sessions/tags")
        assert res.status_code == 200
        assert res.json() == {"tags": []}

    def test_list_tags_with_entries(self, client):
        _seed_session(client, "s1")
        _seed_session(client, "s2")
        # Add tags via the API
        client.post("/api/sessions/s1/tags", json={"name": "bug"})
        client.post("/api/sessions/s2/tags", json={"name": "feature"})
        res = client.get("/api/sessions/tags")
        assert res.status_code == 200
        data = res.json()
        assert len(data["tags"]) == 2
        by_name = {t["name"]: t for t in data["tags"]}
        assert by_name["bug"]["session_count"] == 1
        assert by_name["feature"]["session_count"] == 1


class TestAddTag:
    def test_add_tag_endpoint(self, client):
        _seed_session(client, "s1")
        res = client.post("/api/sessions/s1/tags", json={"name": "bug", "color": "red"})
        assert res.status_code == 200
        tag = res.json()["tag"]
        assert tag["name"] == "bug"
        assert tag["color"] == "red"

    def test_add_tag_missing_name(self, client):
        _seed_session(client, "s1")
        res = client.post("/api/sessions/s1/tags", json={})
        assert res.status_code == 400

    def test_add_tag_idempotent(self, client):
        _seed_session(client, "s1")
        client.post("/api/sessions/s1/tags", json={"name": "bug"})
        res = client.post("/api/sessions/s1/tags", json={"name": "bug"})
        assert res.status_code == 200  # not 409


class TestGetSessionTags:
    def test_get_tags(self, client):
        _seed_session(client, "s1")
        client.post("/api/sessions/s1/tags", json={"name": "bug"})
        client.post("/api/sessions/s1/tags", json={"name": "feature"})
        res = client.get("/api/sessions/s1/tags")
        assert res.status_code == 200
        tags = res.json()["tags"]
        assert len(tags) == 2
        names = [t["name"] for t in tags]
        assert "bug" in names
        assert "feature" in names

    def test_get_tags_empty(self, client):
        _seed_session(client, "s1")
        res = client.get("/api/sessions/s1/tags")
        assert res.status_code == 200
        assert res.json() == {"tags": []}


class TestRemoveTag:
    def test_remove_tag_endpoint(self, client):
        _seed_session(client, "s1")
        add_res = client.post("/api/sessions/s1/tags", json={"name": "bug"})
        tag_id = add_res.json()["tag"]["id"]
        res = client.delete(f"/api/sessions/s1/tags/{tag_id}")
        assert res.status_code == 200
        assert res.json()["ok"] is True
        # Verify removal
        tags = client.get("/api/sessions/s1/tags").json()["tags"]
        assert len(tags) == 0


class TestDeleteTag:
    def test_delete_tag_endpoint(self, client):
        _seed_session(client, "s1")
        add_res = client.post("/api/sessions/s1/tags", json={"name": "bug"})
        tag_id = add_res.json()["tag"]["id"]
        res = client.delete(f"/api/sessions/tags/{tag_id}")
        assert res.status_code == 200
        assert res.json()["ok"] is True
        # Tag is gone
        tags = client.get("/api/sessions/tags").json()["tags"]
        assert len(tags) == 0

    def test_delete_nonexistent(self, client):
        res = client.delete("/api/sessions/tags/nonexistent-id")
        assert res.status_code == 404


class TestSessionsTagFilter:
    def test_sessions_filtered_by_include_tag(self, client):
        _seed_session(client, "s1", title="Bug report")
        _seed_session(client, "s2", title="Feature request")
        client.post("/api/sessions/s1/tags", json={"name": "bug"})
        res = client.get("/api/sessions?tags=bug")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        ids = [s["id"] for s in sessions]
        assert "s1" in ids
        assert "s2" not in ids

    def test_sessions_filtered_by_exclude_tag(self, client):
        _seed_session(client, "s1", title="Bug")
        _seed_session(client, "s2", title="Feature")
        client.post("/api/sessions/s1/tags", json={"name": "bug"})
        res = client.get("/api/sessions?exclude_tags=bug")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        ids = [s["id"] for s in sessions]
        assert "s2" in ids
        assert "s1" not in ids

    def test_sessions_tag_filter_and_mode(self, client):
        _seed_session(client, "s1", title="Both")
        _seed_session(client, "s2", title="Only bug")
        client.post("/api/sessions/s1/tags", json={"name": "bug"})
        client.post("/api/sessions/s1/tags", json={"name": "urgent"})
        client.post("/api/sessions/s2/tags", json={"name": "bug"})
        res = client.get("/api/sessions?tags=bug,urgent&tag_match=all")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        ids = [s["id"] for s in sessions]
        assert "s1" in ids
        assert "s2" not in ids


class TestSuggestTags:
    def test_suggest_tags_returns_suggestions(self, client, monkeypatch):
        _seed_session(client, "s1")
        # Insert some messages so the suggest endpoint has content to analyze
        from hermes_state import SessionDB

        db = SessionDB()
        for i in range(6):
            db._conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) "
                "VALUES (?, ?, ?, ?)",
                ("s1", "user" if i % 2 == 0 else "assistant",
                 f"message {i} about debugging", time.time() + i),
            )
        db._conn.execute("UPDATE sessions SET message_count = 6 WHERE id = 's1'")
        db._conn.commit()
        db.close()

        # Stub the LLM call so the test is hermetic
        async def _fake_llm(prompt: str):
            return [
                {"tag_name": "debugging", "reason": "conversation about debugging", "confidence": 0.9},
                {"tag_name": "bug", "reason": "bug-related", "confidence": 0.7},
            ]

        from hermes_cli import web_server

        monkeypatch.setattr(web_server, "_llm_tag_suggest", _fake_llm)

        res = client.post("/api/sessions/s1/tags/suggest", json={})
        assert res.status_code == 200
        suggestions = res.json()["suggestions"]
        assert len(suggestions) == 2
        assert suggestions[0]["tag_name"] == "debugging"
        assert "is_new" in suggestions[0]

    def test_suggest_tags_empty_session(self, client):
        _seed_session(client, "s1")
        res = client.post("/api/sessions/s1/tags/suggest", json={})
        assert res.status_code == 200
        assert res.json()["suggestions"] == []
