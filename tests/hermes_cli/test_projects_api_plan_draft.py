"""POST/GET /{slug}/playbook/draft — the agent drafts a proposed plan.

Contracts: the model runs off the request thread and its result lands as
an ordinary *inactive* revision (activation stays human); the model's
loose output is normalised onto the playbook schema (keys, dependencies,
assignee) rather than refused; failures are reported, not raised; and a
second draft while one is running is a 409.
"""

from __future__ import annotations

import json
import time

import pytest

from hermes_cli import projects_api, projects_db

from tests.hermes_cli.test_projects_api import OWNER, _create, env  # noqa: F401


def _wait_done(client, slug, timeout=5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/registry/projects/{slug}/playbook/draft").json()
        if state["status"] in ("done", "failed"):
            return state
        time.sleep(0.02)
    raise AssertionError("draft never finished")


@pytest.fixture(autouse=True)
def _clear_drafts():
    projects_api._PLAN_DRAFTS.clear()
    yield
    projects_api._PLAN_DRAFTS.clear()


def test_draft_saves_an_inactive_revision(env, monkeypatch):
    client, _state = env
    slug = _create(env)["slug"]
    seen = {}

    def fake_model(prompt):
        seen["prompt"] = prompt
        return "```json\n" + json.dumps(
            {
                "body": "Compile, review, send.",
                "steps": [
                    {"key": "Compile Items", "title": "Compile the items"},
                    {
                        "key": "review",
                        "title": "Review the digest",
                        "depends_on": ["Compile Items"],
                        "checkpoint": True,
                        "assignee": "nobody-i-know",
                    },
                    {"title": "Send it", "depends_on": ["review", "ghost"]},
                ],
            }
        ) + "\n```"

    monkeypatch.setattr(projects_api, "_call_plan_model", fake_model)

    resp = client.post(f"/api/registry/projects/{slug}/playbook/draft")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "running"

    state = _wait_done(client, slug)
    assert state["status"] == "done", state
    assert state["rev"] == 1
    assert "The Monday digest email" in seen["prompt"]
    assert "Ship the Monday digest" in seen["prompt"]

    playbook = client.get(f"/api/registry/projects/{slug}/playbook").json()
    assert playbook["active"] is None
    (rev,) = playbook["revisions"]
    assert rev["active"] in (0, False)
    assert rev["note"] == "drafted by the agent"
    assert rev["body"] == "Compile, review, send."
    keys = [s["key"] for s in rev["steps"]]
    assert keys == ["compile-items", "review", "send-it"]
    by_key = {s["key"]: s for s in rev["steps"]}
    assert by_key["review"]["depends_on"] == ["compile-items"]
    assert by_key["review"]["checkpoint"] is True
    # Unknown assignee → the host profile; unknown dependency dropped.
    assert by_key["review"]["assignee"] == "default"
    assert by_key["send-it"]["depends_on"] == ["review"]


def test_draft_reports_model_failure(env, monkeypatch):
    client, _state = env
    slug = _create(env)["slug"]
    monkeypatch.setattr(
        projects_api, "_call_plan_model", lambda prompt: "Sorry, I cannot help."
    )
    assert client.post(f"/api/registry/projects/{slug}/playbook/draft").status_code == 200
    state = _wait_done(client, slug)
    assert state["status"] == "failed"
    assert "did not return a plan" in state["detail"]
    assert client.get(f"/api/registry/projects/{slug}/playbook").json()["revisions"] == []


def test_second_draft_while_running_is_refused(env, monkeypatch):
    client, _state = env
    slug = _create(env)["slug"]
    import threading

    gate = threading.Event()

    def slow_model(prompt):
        gate.wait(5)
        return json.dumps({"steps": [{"key": "a", "title": "A"}]})

    monkeypatch.setattr(projects_api, "_call_plan_model", slow_model)
    assert client.post(f"/api/registry/projects/{slug}/playbook/draft").status_code == 200
    again = client.post(f"/api/registry/projects/{slug}/playbook/draft")
    assert again.status_code == 409
    assert "already drafting" in again.json()["detail"]
    gate.set()
    assert _wait_done(client, slug)["status"] == "done"


def test_idle_status_and_archived_refusal(env):
    client, _state = env
    created = _create(env)
    slug = created["slug"]
    assert client.get(f"/api/registry/projects/{slug}/playbook/draft").json() == {
        "status": "idle"
    }
    with projects_db.connect_closing() as conn:
        assert projects_db.archive_project(conn, created["id"])
    assert client.post(f"/api/registry/projects/{slug}/playbook/draft").status_code == 409
