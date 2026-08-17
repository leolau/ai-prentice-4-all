"""The ``from_todo`` promotion seam (design §10, §17 step 8b).

Behaviour contracts, not change detectors: a promotion creates a ``triage``
card inheriting the to-do's title/description, records a
``project_links(kind='todo')`` provenance row, moves the to-do to
``working`` with a history entry naming the card — and rolls the card back
*together with the provenance row* when the stage move fails. A foreign
``from_todo.profile`` is refused at the door (E2): the seam only honours
the profile serving the request. An invisible to-do is a 404, never a 403.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db, projects_api, projects_db
from hermes_cli.access import Principal
from hermes_cli.todo_store import Todo, TodoError

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]


class _FakeStore:
    """Stands in for the Supabase-backed TodoStore: records what the seam
    asks of it, with dials for the failure paths."""

    def __init__(self, todo=None, *, set_stage_error=None):
        self.todo = todo
        self.set_stage_error = set_stage_error
        self.stage_calls: list[tuple] = []
        self.outbound_calls: list[tuple] = []

    async def get(self, principal, todo_id):
        if self.todo is not None and self.todo.id == todo_id:
            return self.todo
        return None

    async def set_stage(self, principal, todo_id, stage, actor=None):
        if self.set_stage_error is not None:
            raise self.set_stage_error
        self.stage_calls.append((todo_id, stage, actor))

    async def record_outbound(self, principal, todo_id, event=None,
                              channel=None, actor=None):
        self.outbound_calls.append((todo_id, event, channel, actor))


def _make_todo(todo_id="td_1") -> Todo:
    return Todo(
        id=todo_id,
        owner_user_id="leo",
        visibility="private:leo",
        title="Draft the rollout plan",
        description="Everything the card should inherit as its body.",
        stage="new",
        status="open",
        priority="normal",
        origin="inbox",
        current_state="new",
        trigger_state="new",
        completion_state="done",
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated projects + kanban stores, a wired app, and a fake store."""
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))

    state: dict = {"actor": OWNER, "store": _FakeStore(_make_todo())}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set()

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)
    monkeypatch.setattr(
        "hermes_cli.todo_store.default_store", lambda mode=None: state["store"]
    )

    app = FastAPI()
    app.include_router(projects_api.router)
    client = TestClient(app)
    return client, state


def _create_project(env) -> dict:
    client, _state = env
    resp = client.post(
        "/api/registry/projects/",
        json={
            "goal": "Ship the Monday digest — to every subscriber",
            "description": "A weekly digest compiled and emailed each Monday.",
            "host_profile": "default",
            "outputs": [{"title": "The Monday digest email"}],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _promote(env, slug: str, payload: dict):
    client, _state = env
    return client.post(f"/api/registry/projects/{slug}/cards", json=payload)


def test_promote_creates_triage_card_inheriting_the_todo(env):
    project = _create_project(env)
    resp = _promote(
        env, project["slug"],
        {"from_todo": {"profile": "default", "id": "td_1"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "triage"  # promotion is not dispatch
    assert body["from_todo"] == {"profile": "default", "id": "td_1"}

    with kanban_db.connect_closing() as bconn:
        task = kanban_db.get_task(bconn, body["task_id"])
    assert task.title == "Draft the rollout plan"
    assert task.body == "Everything the card should inherit as its body."
    assert task.project_id == project["id"]


def test_promote_records_provenance_link_and_moves_todo(env):
    project = _create_project(env)
    _client, state = env
    resp = _promote(env, project["slug"], {"from_todo": {"id": "td_1"}})
    assert resp.status_code == 200, resp.text
    tid = resp.json()["task_id"]

    with projects_db.connect_closing() as conn:
        links = projects_db.get_project_links(conn, project["id"])
    todo_links = [l for l in links if l["kind"] == "todo"]
    assert [(l["profile"], l["ref"]) for l in todo_links] == [("default", "td_1")]

    store = state["store"]
    assert store.stage_calls == [("td_1", "working", "user:leo")]
    assert store.outbound_calls == [("td_1", f"card:{tid}", "promote", "user:leo")]


def test_promote_explicit_title_wins_over_todo(env):
    project = _create_project(env)
    resp = _promote(
        env, project["slug"],
        {"title": "Rewritten heading", "from_todo": {"id": "td_1"}},
    )
    assert resp.status_code == 200
    with kanban_db.connect_closing() as bconn:
        task = kanban_db.get_task(bconn, resp.json()["task_id"])
    assert task.title == "Rewritten heading"
    # The body still inherits — only the supplied fields win.
    assert task.body == "Everything the card should inherit as its body."


def test_promote_invisible_todo_is_404(env):
    project = _create_project(env)
    resp = _promote(
        env, project["slug"],
        {"from_todo": {"profile": "default", "id": "td_missing"}},
    )
    assert resp.status_code == 404


def test_promote_without_id_is_422(env):
    project = _create_project(env)
    resp = _promote(env, project["slug"], {"from_todo": {"profile": "default"}})
    assert resp.status_code == 422


def test_promote_stage_failure_rolls_the_card_back(env):
    project = _create_project(env)
    _client, state = env
    state["store"] = _FakeStore(_make_todo(), set_stage_error=TodoError("stuck"))
    resp = _promote(env, project["slug"], {"from_todo": {"id": "td_1"}})
    assert resp.status_code == 409

    # The board is exactly as empty as before the attempt…
    with kanban_db.connect_closing() as bconn:
        tasks = kanban_db.list_tasks(bconn, project_id=project["id"])
    assert tasks == []
    # …and the rollback takes the provenance row with the card (E2): a
    # retried promotion must not collide with a stranded link.
    with projects_db.connect_closing() as conn:
        assert projects_db.get_project_links(conn, project["id"], kind="todo") == []


def test_promote_foreign_profile_is_refused_before_touching_the_todo(env):
    """E2: the seam only honours the profile serving this request."""
    project = _create_project(env)
    _client, state = env
    resp = _promote(
        env, project["slug"],
        {"from_todo": {"profile": "work", "id": "td_1"}},
    )
    assert resp.status_code == 422
    assert "profile" in resp.json()["detail"]

    # Refused at the door: the to-do store was never asked anything…
    store = state["store"]
    assert store.stage_calls == []
    assert store.outbound_calls == []
    # …and nothing landed on the board or in the links.
    with kanban_db.connect_closing() as bconn:
        assert kanban_db.list_tasks(bconn, project_id=project["id"]) == []
    with projects_db.connect_closing() as conn:
        assert projects_db.get_project_links(conn, project["id"], kind="todo") == []


def test_promote_honours_the_serving_profile_when_named(env):
    """E2: a request served by ``work`` may promote a ``work`` to-do."""
    project = _create_project(env)
    client, _state = env
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/cards",
        params={"profile": "work"},
        json={"from_todo": {"profile": "work", "id": "td_1"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["from_todo"] == {"profile": "work", "id": "td_1"}
    with projects_db.connect_closing() as conn:
        links = projects_db.get_project_links(conn, project["id"], kind="todo")
    assert [(l["profile"], l["ref"]) for l in links] == [("work", "td_1")]


def test_plain_card_create_still_requires_title(env):
    """from_todo is what makes the title optional — never anything else."""
    project = _create_project(env)
    resp = _promote(env, project["slug"], {})
    assert resp.status_code == 422
