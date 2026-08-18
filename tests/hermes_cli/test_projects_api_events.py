"""The events tail and the rolling summary (design §12, §17 step 11).

Behaviour contracts: ``GET /{slug}/events?since=`` returns only the
``task_events`` rows belonging to the caller-visible cards of *this*
project, oldest first, with a ``latest_event_id`` that always names the
current head — even when the window is empty or overflowed. The tail is a
project-scoped join (E4), so a project with more than ~999 visible cards
still serves it. ``POST /{slug}/summarise`` is the one write entry point
for the rolling "where this stands" (§2.2): it stamps ``summary_at`` and
shows up on the detail read.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db, projects_api
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]

_PREFIX = "/api/registry/projects"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))

    async def _resolve(request, *, allow_as=True):
        return OWNER

    async def _enrolled(user_id):
        return set()

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)

    app = FastAPI()
    app.include_router(projects_api.router)
    return TestClient(app)


def _create(client, *, goal, board_slug=None) -> dict:
    body = {
        "goal": goal,
        "description": "The weekly digest, compiled and sent each Monday.",
        "host_profile": "default",
        "outputs": [{"title": "The Monday digest email"}],
    }
    if board_slug:
        body["board_slug"] = board_slug
    resp = client.post(f"{_PREFIX}/", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _add_card(client, slug, title):
    resp = client.post(f"{_PREFIX}/{slug}/cards", json={"title": title})
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


# ---------------------------------------------------------------------------
# GET /{slug}/events — the live-update tail
# ---------------------------------------------------------------------------


def test_events_empty_board(env):
    project = _create(env, goal="Ship the Monday digest to every subscriber")
    resp = env.get(f"{_PREFIX}/{project['slug']}/events")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["events"] == []
    assert data["latest_event_id"] == 0
    assert data["since"] == 0


def test_events_tail_tracks_this_projects_cards(env):
    project = _create(env, goal="Ship the Monday digest to every subscriber")
    slug = project["slug"]
    card = _add_card(env, slug, "Draft the digest")

    resp = env.get(f"{_PREFIX}/{slug}/events")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["events"], "creating a card fires at least one event"
    assert all(ev["task_id"] == card for ev in data["events"])
    ids = [ev["id"] for ev in data["events"]]
    assert ids == sorted(ids), "the tail is oldest-first"
    assert data["latest_event_id"] == max(ids)


def test_events_since_cursor_returns_only_new(env):
    project = _create(env, goal="Ship the Monday digest to every subscriber")
    slug = project["slug"]
    _add_card(env, slug, "Draft the digest")
    head = env.get(f"{_PREFIX}/{slug}/events").json()
    latest = head["latest_event_id"]
    assert latest > 0

    resp = env.get(f"{_PREFIX}/{slug}/events", params={"since": latest})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["events"] == []
    # The head is reported even when the window is empty — a poller keeps
    # its cursor without a second read.
    assert data["latest_event_id"] == latest
    assert data["since"] == latest

    _add_card(env, slug, "Send the digest")
    grown = env.get(f"{_PREFIX}/{slug}/events", params={"since": latest}).json()
    assert grown["events"], "new card events appear after the cursor"
    assert all(ev["id"] > latest for ev in grown["events"])


def test_events_stay_scoped_across_a_shared_board(env):
    # Two projects on one board: each tail sees only its own cards.
    alpha = _create(env, goal="Ship the Monday digest", board_slug="shared")
    beta = _create(env, goal="Refresh the welcome email", board_slug="shared")
    card_a = _add_card(env, alpha["slug"], "Draft the digest")
    card_b = _add_card(env, beta["slug"], "Rewrite the opening")

    seen_a = env.get(f"{_PREFIX}/{alpha['slug']}/events").json()
    seen_b = env.get(f"{_PREFIX}/{beta['slug']}/events").json()
    assert {ev["task_id"] for ev in seen_a["events"]} == {card_a}
    assert {ev["task_id"] for ev in seen_b["events"]} == {card_b}


def test_events_since_validation(env):
    project = _create(env, goal="Ship the Monday digest to every subscriber")
    slug = project["slug"]
    assert env.get(f"{_PREFIX}/{slug}/events", params={"since": "abc"}).status_code == 400
    assert env.get(f"{_PREFIX}/{slug}/events", params={"since": "-1"}).status_code == 400


def test_events_tail_survives_more_than_999_cards(env):
    """E4: the old seam bounded the read with an id list — past SQLite's
    ~999 bound-variable cap that was a 500. The project-scoped join does
    not care how many cards the project has."""
    project = _create(env, goal="Ship the Monday digest to every subscriber")
    now = int(time.time())
    with kanban_db.connect_closing() as bconn:
        with kanban_db.write_txn(bconn):
            bconn.executemany(
                "INSERT INTO tasks (id, title, status, created_at, project_id) "
                "VALUES (?, ?, 'triage', ?, ?)",
                [
                    (f"t_{i:04d}", f"Card {i}", now, project["id"])
                    for i in range(1_200)
                ],
            )
            bconn.executemany(
                "INSERT INTO task_events (task_id, kind, created_at) "
                "VALUES (?, 'created', ?)",
                [(f"t_{i:04d}", now) for i in range(1_200)],
            )

    resp = env.get(f"{_PREFIX}/{project['slug']}/events")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["events"]) == 200  # the tail stays bounded
    assert data["latest_event_id"] == 1_200  # …but the head is the true head
    assert all(ev["task_id"].startswith("t_") for ev in data["events"])


def test_events_unknown_project_404(env):
    assert env.get(f"{_PREFIX}/nope/events").status_code == 404


# ---------------------------------------------------------------------------
# POST /{slug}/summarise — the rolling "where this stands"
# ---------------------------------------------------------------------------


def test_summarise_writes_and_stamps(env):
    project = _create(env, goal="Ship the Monday digest to every subscriber")
    slug = project["slug"]
    resp = env.post(
        f"{_PREFIX}/{slug}/summarise",
        json={"summary": "Run 14 waiting on your answer about the tone."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"] == "Run 14 waiting on your answer about the tone."
    assert data["summary_at"] > 0

    detail = env.get(f"{_PREFIX}/{slug}").json()
    assert detail["summary"] == data["summary"]
    assert detail["summary_at"] == data["summary_at"]

    # Re-summarising overwrites — the summary is rolling, never appended.
    again = env.post(f"{_PREFIX}/{slug}/summarise", json={"summary": "Sent."})
    assert again.status_code == 200
    assert again.json()["summary"] == "Sent."
    assert again.json()["summary_at"] >= data["summary_at"]


def test_summarise_validation(env):
    project = _create(env, goal="Ship the Monday digest to every subscriber")
    slug = project["slug"]
    assert env.post(f"{_PREFIX}/{slug}/summarise", json={}).status_code == 422
    assert (
        env.post(f"{_PREFIX}/{slug}/summarise", json={"summary": "   "}).status_code
        == 422
    )
    too_long = env.post(
        f"{_PREFIX}/{slug}/summarise", json={"summary": "x" * 4001}
    )
    assert too_long.status_code == 422
    assert "4000" in too_long.json()["detail"]


def test_summarise_unknown_project_404(env):
    resp = env.post(f"{_PREFIX}/nope/summarise", json={"summary": "Anything."})
    assert resp.status_code == 404
