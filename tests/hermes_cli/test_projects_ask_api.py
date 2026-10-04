"""POST /{slug}/ask — read-only Q&A about a project.

Contracts: read permission only (no access is a 404); the snapshot is
bounded, principal-filtered and carries no secrets or event payload
internals; citations are checked against the snapshot; the route never
writes either store; a per-principal rate limit; model failures and
timeouts are friendly 5xx responses; the model gets its own one-shot
message list with no tools.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db, projects_api, projects_ask_api, projects_db
from hermes_cli.access import Principal

from tests.hermes_cli.test_projects_api import (  # noqa: F401
    MEMBER_P,
    OWNER,
    STRANGER,
    _activate,
    _create,
    _member,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    state = {"actor": OWNER, "enrolled": set(), "subject": ""}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set(state["enrolled"])

    async def _subject(request):
        return state["subject"]

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)
    monkeypatch.setattr(projects_api, "_interactive_subject", _subject)

    app = FastAPI()
    app.include_router(projects_api.router)
    app.include_router(projects_ask_api.router)
    return TestClient(app), state


@pytest.fixture(autouse=True)
def _reset():
    projects_ask_api.reset_state()
    yield
    projects_ask_api.reset_state()


class FakeModel:
    def __init__(self, reply="All good.", exc=None, delay=0.0):
        self.reply = reply
        self.exc = exc
        self.delay = delay
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        if self.delay:
            time.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.reply(messages) if callable(self.reply) else self.reply


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(projects_ask_api, "ask_model", fake)
    return fake


def _seed(env) -> dict:
    """A project with a requirement, an active plan, a run with a card,
    a second card, and events."""
    client, _state = env
    project = _create(env)
    _activate(env, project)
    slug, pid = project["slug"], project["id"]
    resp = client.post(
        f"/api/registry/projects/{slug}/directives",
        json={"body": "Use a 2.5% revenue split"},
    )
    assert resp.status_code == 200, resp.text
    with projects_db.connect_closing() as conn:
        rev = projects_db.save_playbook_rev(
            conn,
            project_id=pid,
            body="method",
            steps=[
                {"key": "draft", "title": "Draft the MOUs"},
                {"key": "review", "title": "Human review", "checkpoint": True},
            ],
        )
        projects_db.activate_playbook_rev(conn, pid, rev)
        run = projects_db.open_project_run(
            conn, project_id=pid, trigger="manual", profile="default"
        )
    with kanban_db.connect_closing() as bconn:
        t1 = kanban_db.create_task(
            bconn, title="Draft the first MOU", project_id=pid, assignee="writer"
        )
        t2 = kanban_db.create_task(bconn, title="Check the clauses", project_id=pid)
    with projects_db.connect_closing() as conn:
        projects_db.link_run_card(conn, run["id"], t1, "draft")
    return {
        "slug": slug,
        "project": project,
        "run": run,
        "cards": [t1, t2],
        "directive": resp.json()["id"],
    }


def _ask(client, slug, question="What's left?", **extra):
    return client.post(
        f"/api/registry/projects/{slug}/ask", json={"question": question, **extra}
    )


def _db_dump(path) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------


def _snapshot(seed, principal=OWNER):
    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, seed["slug"])
    return projects_ask_api.build_snapshot(project, principal)


def test_snapshot_carries_brief_requirements_plan_runs_cards_outputs_events(env):
    seed = _seed(env)
    text, index = _snapshot(seed)
    assert "Ship the Monday digest" in text
    assert "A weekly digest compiled" in text
    assert f"[requirement:{seed['directive']}] R1: Use a 2.5% revenue split" in text
    assert "[plan:draft] step 1: Draft the MOUs" in text
    assert "checkpoint" in text
    assert f"[run:{seed['run']['run_no']}] run {seed['run']['run_no']}: running" in text
    assert f"[card:{seed['cards'][0]}] Draft the first MOU" in text
    assert "writer" in text
    assert "The Monday digest email" in text
    assert "not delivered yet" in text
    assert "RECENT EVENTS" in text and "[event:" in text
    kinds = {v["kind"] for v in index.values()}
    assert kinds == {"requirement", "plan", "run", "card", "output", "event"}
    assert index[f"card:{seed['cards'][0]}"]["label"] == 'card "Draft the first MOU"'
    assert index[f"requirement:{seed['directive']}"]["label"] == "requirement R1"


def test_snapshot_marks_a_stalled_run(env):
    seed = _seed(env)
    with kanban_db.connect_closing() as bconn:
        bconn.execute(
            "UPDATE tasks SET status = 'blocked' WHERE id = ?", (seed["cards"][0],)
        )
        bconn.commit()
    text, _ = _snapshot(seed)
    assert "STALLED" in text


def test_snapshot_is_capped(env, monkeypatch):
    seed = _seed(env)
    with kanban_db.connect_closing() as bconn:
        for i in range(60):
            kanban_db.create_task(
                bconn, title=f"Card {i} " + "x" * 300, project_id=seed["project"]["id"]
            )
    monkeypatch.setattr(projects_ask_api, "SNAPSHOT_MAX_CHARS", 3_000)
    text, index = _snapshot(seed)
    assert len(text) <= 3_000
    assert text.endswith("(snapshot truncated)")
    # Every citable tag is still present in the (cut) text.
    assert all(f"[{tag}]" in text for tag in index)


def test_snapshot_bounds_card_count_and_line_length(env):
    seed = _seed(env)
    with kanban_db.connect_closing() as bconn:
        for i in range(50):
            kanban_db.create_task(bconn, title="y" * 500, project_id=seed["project"]["id"])
    text, index = _snapshot(seed)
    assert len(text) <= projects_ask_api.SNAPSHOT_MAX_CHARS
    assert sum(1 for k in index if k.startswith("card:")) <= projects_ask_api._MAX_CARDS
    assert all(len(line) < 600 for line in text.splitlines())


def test_snapshot_redacts_secrets_and_drops_event_payload_internals(env):
    seed = _seed(env)
    secret = "sk-ant-api03-" + "A" * 40
    with kanban_db.connect_closing() as bconn:
        kanban_db.create_task(
            bconn, title=f"Use key {secret}", project_id=seed["project"]["id"]
        )
        kanban_db._append_event(
            bconn,
            seed["cards"][0],
            "tool_call",
            {"tool": "terminal", "args": {"command": "cat ~/.env"}, "status": "ok"},
        )
        bconn.commit()
    text, _ = _snapshot(seed)
    assert secret not in text
    assert "cat ~/.env" not in text
    assert "args" not in text
    assert "tool_call (status=ok)" in text


def test_snapshot_never_shows_another_users_private_card(env):
    seed = _seed(env)
    with kanban_db.connect_closing() as bconn:
        kanban_db.create_task(
            bconn,
            title="Mallory's secret plan",
            project_id=seed["project"]["id"],
            owner_user_id="mallory",
            visibility="private:mallory",
            triage=True,
        )
    _member(seed["project"]["id"], "ada", "member")
    text, _ = _snapshot(seed, principal=MEMBER_P)
    assert "Mallory's secret plan" not in text
    assert "Draft the first MOU" in text


def test_snapshot_never_includes_contact_addresses(env):
    client, _ = env
    seed = _seed(env)
    resp = client.post(
        f"/api/registry/projects/{seed['slug']}/contacts",
        json={"name": "Dr Tse", "platform": "email", "address": "tse@example.com"},
    )
    assert resp.status_code == 200, resp.text
    text, _ = _snapshot(seed)
    assert "tse@example.com" not in text


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------


INDEX = {
    "card:t_1": {"kind": "card", "id": "t_1", "label": 'card "Draft"'},
    "run:3": {"kind": "run", "id": "3", "label": "run 3"},
    "requirement:d_1": {"kind": "requirement", "id": "d_1", "label": "requirement R1"},
}


def test_parse_answer_keeps_known_citations_in_order_and_dedupes():
    out = projects_ask_api.parse_answer(
        "Run 3 is drafting [run:3]. The card is half done [card:t_1] [run:3].", INDEX
    )
    assert [s["id"] for s in out["sources"]] == ["3", "t_1"]
    assert out["answer"] == "Run 3 is drafting. The card is half done."
    assert "suggested_requirement" not in out


def test_parse_answer_drops_unknown_ids_and_kinds():
    out = projects_ask_api.parse_answer(
        "Yes [card:t_999] [secret:x] per [requirement:d_1].", INDEX
    )
    assert [s["kind"] for s in out["sources"]] == ["requirement"]
    assert "t_999" not in out["answer"]


def test_parse_answer_extracts_the_suggested_requirement():
    out = projects_ask_api.parse_answer(
        "Governing law is not set [card:t_1].\nREQUIREMENT: Use Hong Kong governing law in all MOUs [card:t_1]",
        INDEX,
    )
    assert out["suggested_requirement"] == "Use Hong Kong governing law in all MOUs"
    assert "REQUIREMENT" not in out["answer"]
    assert out["answer"] == "Governing law is not set."


def test_parse_answer_handles_cjk_punctuation():
    out = projects_ask_api.parse_answer("已完成 [card:t_1]。", INDEX)
    assert out["answer"] == "已完成。"


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def test_ask_answers_with_checked_sources(env, model):
    client, _ = env
    seed = _seed(env)
    model.reply = (
        f"Run {seed['run']['run_no']} is drafting [run:{seed['run']['run_no']}] "
        f"[card:{seed['cards'][0]}] [card:nope]."
    )
    resp = _ask(client, seed["slug"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == f"Run {seed['run']['run_no']} is drafting."
    assert [s["kind"] for s in body["sources"]] == ["run", "card"]
    assert body["sources"][1] == {
        "kind": "card",
        "id": seed["cards"][0],
        "label": 'card "Draft the first MOU"',
    }


def test_ask_sends_one_fresh_toolless_message_list(env, model):
    client, _ = env
    seed = _seed(env)
    resp = _ask(
        client,
        seed["slug"],
        "Why is it slow?",
        history=[{"q": f"q{i}", "a": f"a{i}"} for i in range(10)],
    )
    assert resp.status_code == 200
    (messages,) = model.calls
    assert messages[0]["role"] == "system"
    assert "same language" in messages[0]["content"]
    assert "PROJECT SNAPSHOT" in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "Why is it slow?"}
    # History is trimmed to the last six turns.
    qs = [m["content"] for m in messages if m["role"] == "user"][1:-1]
    assert qs == [f"q{i}" for i in range(4, 10)]
    assert all(set(m) == {"role", "content"} for m in messages)


def test_ask_never_writes_either_store(env, model, tmp_path):
    client, _ = env
    seed = _seed(env)
    before = (_db_dump(tmp_path / "projects.db"), _db_dump(tmp_path / "kanban.db"))
    assert _ask(client, seed["slug"]).status_code == 200
    model.exc = RuntimeError("boom")
    assert _ask(client, seed["slug"]).status_code == 502
    after = (_db_dump(tmp_path / "projects.db"), _db_dump(tmp_path / "kanban.db"))
    assert before == after


def test_ask_is_open_to_readers_and_404_without_access(env, model):
    client, state = env
    seed = _seed(env)
    viewer = Principal(user_id="vic", display="Vic", role="member")  # type: ignore[arg-type]
    _member(seed["project"]["id"], "vic", "viewer")
    state["actor"] = viewer
    assert _ask(client, seed["slug"]).status_code == 200
    state["actor"] = STRANGER
    resp = _ask(client, seed["slug"])
    assert resp.status_code == 404
    assert _ask(client, "no-such-project").status_code == 404
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    "question, status",
    [("", 422), ("   ", 422), ("x" * 2001, 422)],
)
def test_ask_validates_the_question(env, model, question, status):
    client, _ = env
    seed = _seed(env)
    resp = _ask(client, seed["slug"], question)
    assert resp.status_code == status
    assert resp.json()["detail"]
    assert model.calls == []


def test_ask_accepts_a_2000_char_question(env, model):
    client, _ = env
    seed = _seed(env)
    assert _ask(client, seed["slug"], "x" * 2000).status_code == 200


def test_ask_rejects_bad_bodies(env, model):
    client, _ = env
    seed = _seed(env)
    url = f"/api/registry/projects/{seed['slug']}/ask"
    assert client.post(url, content=b"not json").status_code == 400
    assert client.post(url, json=["q"]).status_code == 400
    assert client.post(url, json={"question": "q", "history": "x"}).status_code == 422


def test_ask_is_rate_limited_per_principal(env, model):
    client, state = env
    seed = _seed(env)
    for _ in range(projects_ask_api.RATE_LIMIT):
        assert _ask(client, seed["slug"]).status_code == 200
    resp = _ask(client, seed["slug"])
    assert resp.status_code == 429
    assert "wait a minute" in resp.json()["detail"]
    # Another principal has its own budget.
    _member(seed["project"]["id"], "ada", "member")
    state["actor"] = MEMBER_P
    assert _ask(client, seed["slug"]).status_code == 200


def test_rate_window_slides():
    projects_ask_api._rate_check("u", now=0.0)
    for _ in range(projects_ask_api.RATE_LIMIT - 1):
        projects_ask_api._rate_check("u", now=1.0)
    with pytest.raises(Exception):
        projects_ask_api._rate_check("u", now=2.0)
    projects_ask_api._rate_check("u", now=61.5)


def test_same_idempotency_key_replays_without_a_second_model_call(env, model):
    client, _ = env
    seed = _seed(env)
    url = f"/api/registry/projects/{seed['slug']}/ask"
    headers = {"Idempotency-Key": "k-1"}
    first = client.post(url, json={"question": "q"}, headers=headers)
    second = client.post(url, json={"question": "q"}, headers=headers)
    assert first.json() == second.json()
    assert len(model.calls) == 1
    client.post(url, json={"question": "q"}, headers={"Idempotency-Key": "k-2"})
    assert len(model.calls) == 2


def test_a_failed_ask_can_be_retried_with_the_same_key(env, model):
    client, _ = env
    seed = _seed(env)
    url = f"/api/registry/projects/{seed['slug']}/ask"
    headers = {"Idempotency-Key": "k-1"}
    model.exc = RuntimeError("provider down")
    assert client.post(url, json={"question": "q"}, headers=headers).status_code == 502
    model.exc = None
    resp = client.post(url, json={"question": "q"}, headers=headers)
    assert resp.status_code == 200


def test_model_failure_is_a_friendly_502(env, model):
    client, _ = env
    seed = _seed(env)
    model.exc = RuntimeError("HTTP 500 from upstream: stack trace here")
    resp = _ask(client, seed["slug"])
    assert resp.status_code == 502
    assert resp.json()["detail"] == "The agent couldn't answer just now. Try again in a moment."


def test_no_model_configured_is_a_503(env, model):
    client, _ = env
    seed = _seed(env)
    model.exc = projects_ask_api.AskModelUnavailable("No LLM provider configured")
    resp = _ask(client, seed["slug"])
    assert resp.status_code == 503
    assert "no model is configured" in resp.json()["detail"]


def test_empty_model_answer_is_a_502(env, model):
    client, _ = env
    seed = _seed(env)
    model.reply = "   [card:zzz]  "
    assert _ask(client, seed["slug"]).status_code == 502


def test_timeout_is_a_friendly_504(env, model, monkeypatch):
    client, _ = env
    seed = _seed(env)
    monkeypatch.setattr(projects_ask_api, "ASK_TIMEOUT_SECONDS", 0.05)
    model.delay = 0.3
    resp = _ask(client, seed["slug"])
    assert resp.status_code == 504
    assert "too long" in resp.json()["detail"]


def test_real_model_call_maps_missing_provider(monkeypatch):
    """The production call wraps call_llm's "no provider" RuntimeError."""
    import agent.auxiliary_client as aux

    def _boom(*a, **k):
        raise RuntimeError("No LLM provider configured for task=compression")

    seen = {}

    def _cfg(task):
        seen["task"] = task
        return {}

    monkeypatch.setattr(aux, "call_llm", _boom)
    monkeypatch.setattr(aux, "_get_auxiliary_task_config", _cfg)
    with pytest.raises(projects_ask_api.AskModelUnavailable):
        projects_ask_api._call_ask_model([{"role": "user", "content": "q"}])
    assert seen["task"] == "projects_ask"


def test_real_model_call_passes_no_tools(monkeypatch):
    import agent.auxiliary_client as aux

    captured = {}

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def _call(task, **kwargs):
        captured["task"] = task
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(aux, "call_llm", _call)
    monkeypatch.setattr(aux, "_get_auxiliary_task_config", lambda task: {})
    assert projects_ask_api._call_ask_model([{"role": "user", "content": "q"}]) == "ok"
    assert captured["task"] == "compression"
    assert "tools" not in captured
    assert captured["timeout"] == projects_ask_api.ASK_TIMEOUT_SECONDS


def test_route_is_mounted_on_the_web_server():
    from hermes_cli import web_server

    paths = {getattr(r, "path", "") for r in web_server.app.routes}
    assert "/api/registry/projects/{slug}/ask" in paths


# ---------------------------------------------------------------------------
# The agreed scope in the snapshot ([scope:<round>] citations)
# ---------------------------------------------------------------------------


def _clarify(pid, qa, *, understanding=None, confirm=True):
    with projects_db.connect_closing() as conn:
        rn = projects_db.add_clarify_round(
            conn, pid, questions=[{"question": q} for q, _ in qa],
            understanding="agent guess", done=False, created_by="agent",
        )
        rows = projects_db.list_clarifications(conn, pid, round_no=rn)
        items = [
            {"id": r["id"], "skip": True} if a is None else {"id": r["id"], "answer": a}
            for r, (_, a) in zip(rows, qa)
        ]
        if items:
            projects_db.answer_clarifications(conn, pid, items, user_id="leo")
        if confirm:
            projects_db.confirm_clarify_round(
                conn, pid, user_id="leo", understanding=understanding
            )
    return rn


def test_snapshot_without_scope_has_no_scope_section(env):
    seed = _seed(env)
    text, index = _snapshot(seed)
    assert "AGREED SCOPE" not in text and "CLARIFICATIONS" not in text
    assert not any(k.startswith("scope:") for k in index)


def test_snapshot_carries_the_agreed_scope_with_citations(env):
    seed = _seed(env)
    pid = seed["project"]["id"]
    rn = _clarify(pid, [("Which law?", "Hong Kong law"), ("Deadline?", None)],
                  understanding="Four MOUs under HK law.")
    text, index = _snapshot(seed)
    assert f"AGREED SCOPE (confirmed by the owner) [scope:{rn}]: Four MOUs under HK law." in text
    assert f"- [scope:{rn}] Which law? → Hong Kong law" in text
    assert "Deadline?" not in text  # skipped
    assert "agent guess" not in text
    assert index[f"scope:{rn}"] == {"kind": "scope", "id": str(rn),
                                    "label": f"agreed scope, round {rn}"}
    assert text.index("AGREED SCOPE") < text.index("REQUIREMENTS")


def test_snapshot_marks_unconfirmed_answers(env):
    seed = _seed(env)
    rn = _clarify(seed["project"]["id"], [("Which law?", "HK law")], confirm=False)
    text, index = _snapshot(seed)
    assert "AGREED SCOPE" not in text
    assert "OWNER'S ANSWERS SO FAR (not yet confirmed):" in text
    assert f"- [scope:{rn}] Which law? → HK law" in text
    assert f"scope:{rn}" in index


def test_parse_answer_accepts_scope_citations():
    index = {"scope:2": {"kind": "scope", "id": "2", "label": "agreed scope, round 2"}}
    out = projects_ask_api.parse_answer("Only HK law is in scope [scope:2] [scope:9].", index)
    assert out["sources"] == [index["scope:2"]]
    assert out["answer"] == "Only HK law is in scope."
    assert "scope" in projects_ask_api.SOURCE_KINDS


def test_ask_returns_a_scope_source(env, model):
    client, _ = env
    seed = _seed(env)
    rn = _clarify(seed["project"]["id"], [], understanding="HK law only.")
    model.reply = f"The agreed scope is HK law only [scope:{rn}]."
    resp = _ask(client, seed["slug"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["sources"] == [
        {"kind": "scope", "id": str(rn), "label": f"agreed scope, round {rn}"}
    ]
