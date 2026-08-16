"""The Projects HTTP surface (design §12, API part 1 / §17 step 3).

Behaviour contracts, not change detectors: the mandatory-field refusals,
the §11 permission matrix (reads are 404, writes are 403, the judgement
acts stay open to members), the §9.1 progress ladder, and the rule that a
project view never leaks another user's ``private:`` card.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db, projects_api, projects_db
from hermes_cli.access import Principal

# Box-wide identities. Roles here are the *instance* roles; the per-project
# membership matrix is seeded through the store below.
OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]
MEMBER_P = Principal(user_id="ada", display="Ada", role="member")  # type: ignore[arg-type]
STRANGER = Principal(user_id="eve", display="Eve", role="member")  # type: ignore[arg-type]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated projects + kanban stores, a wired app, and an actor dial."""
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))

    state = {"actor": OWNER, "enrolled": set()}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set(state["enrolled"])

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)

    app = FastAPI()
    app.include_router(projects_api.router)
    client = TestClient(app)
    return client, state


def _create(env, *, body=None, actor=None) -> dict:
    """Create a fully-mandatory project through the API; return its payload."""
    client, state = env
    prev = state["actor"]
    if actor is not None:
        state["actor"] = actor
    try:
        payload = {
            "goal": "Ship the Monday digest — to every subscriber",
            "description": "A weekly digest compiled and emailed each Monday.",
            "host_profile": "default",
            "outputs": [{"title": "The Monday digest email"}],
        }
        if body:
            payload.update(body)
        resp = client.post("/api/registry/projects/", json=payload)
        assert resp.status_code == 200, resp.text
        return resp.json()
    finally:
        state["actor"] = prev


def _activate(env, project: dict) -> None:
    """Move a fresh (planning) project to active: outputs+member+profile
    rows already exist after create, so the gate opens."""
    client, _state = env
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}", json={"status": "active"}
    )
    assert resp.status_code == 200, resp.text


def _member(project_id: str, user_id: str, role: str) -> None:
    with projects_db.connect_closing() as conn:
        projects_db.add_project_member(
            conn, project_id=project_id, user_id=user_id, role=role
        )


# ---------------------------------------------------------------------------
# Create — the §1.1 mandatory contract
# ---------------------------------------------------------------------------


def test_create_refused_without_mandatory_fields_and_each_is_named(env):
    client, _state = env
    resp = client.post("/api/registry/projects/", json={})
    assert resp.status_code == 422
    missing = resp.json()["detail"]["missing"]
    assert set(missing) == {"goal", "description", "outputs", "host_profile"}


def test_create_partial_mandatory_names_only_what_is_missing(env):
    client, _state = env
    resp = client.post(
        "/api/registry/projects/",
        json={"goal": "Ship it", "description": "The brief."},
    )
    assert resp.status_code == 422
    assert set(resp.json()["detail"]["missing"]) == {"outputs", "host_profile"}


def test_create_defaults_name_from_goal_and_writes_membership(env):
    project = _create(env)
    assert project["name"] == "Ship the Monday digest"
    assert project["status"] == "planning"
    assert project["owner_user_id"] == "leo"

    with projects_db.connect_closing() as conn:
        members = projects_db.get_project_members(conn, project["id"])
        profiles = projects_db.get_project_profiles(conn, project["id"])
        outputs = projects_db.get_project_outputs(conn, project["id"])
    assert [(m["user_id"], m["role"]) for m in members] == [("leo", "lead")]
    assert [(p["profile"], p["role"]) for p in profiles] == [("default", "host")]
    assert [o["title"] for o in outputs] == ["The Monday digest email"]
    assert outputs[0]["required"] == 1


def test_create_refuses_goal_over_160_chars(env):
    client, _state = env
    resp = client.post(
        "/api/registry/projects/",
        json={
            "goal": "x" * 161,
            "description": "d",
            "host_profile": "default",
            "outputs": [{"title": "o"}],
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# §11 read gate — 404, never 403
# ---------------------------------------------------------------------------


def test_non_member_read_is_404_not_403(env):
    project = _create(env)
    client, state = env
    state["actor"] = STRANGER
    resp = client.get(f"/api/registry/projects/{project['slug']}")
    assert resp.status_code == 404


def test_member_and_viewer_can_read(env):
    project = _create(env)
    _member(project["id"], "ada", "viewer")
    client, state = env
    state["actor"] = MEMBER_P
    resp = client.get(f"/api/registry/projects/{project['slug']}")
    assert resp.status_code == 200


def test_shared_project_readable_through_enrollment_only(env):
    project = _create(env, body={"visibility": "shared"})
    client, state = env

    state["actor"] = STRANGER
    state["enrolled"] = {"default"}  # listed in project_profiles
    assert client.get(f"/api/registry/projects/{project['slug']}").status_code == 200

    state["enrolled"] = {"elsewhere"}
    assert client.get(f"/api/registry/projects/{project['slug']}").status_code == 404


def test_private_project_not_readable_through_enrollment(env):
    project = _create(env, body={"visibility": "private"})
    client, state = env
    state["actor"] = STRANGER
    state["enrolled"] = {"default"}
    assert client.get(f"/api/registry/projects/{project['slug']}").status_code == 404


# ---------------------------------------------------------------------------
# §11 write gate — lead/admin write; members get the judgement acts
# ---------------------------------------------------------------------------


def test_viewer_read_but_never_writes(env):
    project = _create(env)
    _member(project["id"], "ada", "viewer")
    client, state = env
    state["actor"] = MEMBER_P
    assert client.patch(
        f"/api/registry/projects/{project['slug']}", json={"goal": "New goal."}
    ).status_code == 403
    assert client.post(
        f"/api/registry/projects/{project['slug']}/links",
        json={"kind": "url", "ref": "https://example.com"},
    ).status_code == 403


def test_member_judgement_acts_but_not_record_writes(env):
    project = _create(env)
    _member(project["id"], "ada", "member")
    client, state = env
    state["actor"] = MEMBER_P

    assert client.patch(
        f"/api/registry/projects/{project['slug']}", json={"goal": "New goal."}
    ).status_code == 403

    assert client.post(
        f"/api/registry/projects/{project['slug']}/links",
        json={"kind": "url", "ref": "https://example.com"},
    ).status_code == 200
    assert client.post(
        f"/api/registry/projects/{project['slug']}/contacts",
        json={"name": "Ada's client"},
    ).status_code == 200
    assert client.post(
        f"/api/registry/projects/{project['slug']}/outputs",
        json={"title": "An extra output"},
    ).status_code == 200


def test_patch_goal_and_name_are_independent(env):
    project = _create(env)
    client, _state = env
    slug = project["slug"]

    resp = client.patch(f"/api/registry/projects/{slug}", json={"goal": "A new outcome."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal"] == "A new outcome."
    assert body["name"] == project["name"]  # re-wording never renames

    resp = client.patch(f"/api/registry/projects/{slug}", json={"name": "Renamed"})
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["goal"] == "A new outcome."  # renaming never rewrites


def test_member_toggling_required_is_refused(env):
    """Proposing an output is a judgement act; changing what counts as
    required is structural — lead and above only."""
    project = _create(env)
    _member(project["id"], "ada", "member")
    client, state = env
    with projects_db.connect_closing() as conn:
        oid = projects_db.get_project_outputs(conn, project["id"])[0]["id"]
    state["actor"] = MEMBER_P
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/outputs/{oid}",
        json={"required": False},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Contacts — address is members-only PII
# ---------------------------------------------------------------------------


def test_viewer_responses_omit_contact_address_members_do_not(env):
    project = _create(env)
    _member(project["id"], "ada", "viewer")
    client, _state = env
    assert client.post(
        f"/api/registry/projects/{project['slug']}/contacts",
        json={"name": "The client", "address": "client@example.com"},
    ).status_code == 200

    # Lead sees the address.
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert detail["contacts"][0]["address"] == "client@example.com"

    # Viewer: the field is dropped, not blanked.
    client, state = env
    state["actor"] = MEMBER_P
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert "address" not in detail["contacts"][0]
    assert detail["contacts"][0]["name"] == "The client"


# ---------------------------------------------------------------------------
# Outputs lifecycle — deliver, human-only accept, closure offer (§6.1)
# ---------------------------------------------------------------------------


def test_accept_flow_and_closure_offer(env):
    project = _create(env)
    _activate(env, project)
    client, _state = env
    slug = project["slug"]
    with projects_db.connect_closing() as conn:
        oid = projects_db.get_project_outputs(conn, project["id"])[0]["id"]

    resp = client.post(f"/api/registry/projects/{slug}/outputs/{oid}/deliver", json={})
    assert resp.status_code == 200

    with projects_db.connect_closing() as conn:
        row = projects_db.get_project_outputs(conn, project["id"])[0]
    assert row["status"] == "delivered"
    assert row["delivered_at"]

    resp = client.post(f"/api/registry/projects/{slug}/outputs/{oid}/accept")
    assert resp.status_code == 200
    body = resp.json()
    assert body["by"] == "leo"
    # one_off with every required output accepted → closure is offered,
    # never forced.
    assert body["offers_closure"] is True
    with projects_db.connect_closing() as conn:
        assert projects_db.get_project(conn, project["id"]).status == "active"


def test_agent_cannot_accept_directly_through_status_patch(env):
    """``accepted`` is reserved for the human-only accept route."""
    project = _create(env)
    client, _state = env
    with projects_db.connect_closing() as conn:
        oid = projects_db.get_project_outputs(conn, project["id"])[0]["id"]
    resp = client.patch(
        f"/api/registry/projects/{project['slug']}/outputs/{oid}",
        json={"status": "accepted"},
    )
    assert resp.status_code == 422


def test_delete_last_required_output_refused_optional_is_not(env):
    project = _create(env)
    client, _state = env
    slug = project["slug"]
    with projects_db.connect_closing() as conn:
        required_oid = projects_db.get_project_outputs(conn, project["id"])[0]["id"]
        optional_oid = projects_db.add_project_output(
            conn, project_id=project["id"], title="Nice-to-have", required=False
        )

    assert client.delete(
        f"/api/registry/projects/{slug}/outputs/{required_oid}"
    ).status_code == 409
    assert client.delete(
        f"/api/registry/projects/{slug}/outputs/{optional_oid}"
    ).status_code == 200


# ---------------------------------------------------------------------------
# §9.1 the progress ladder
# ---------------------------------------------------------------------------


def test_progress_rung1_outranks_cards_when_a_delivery_exists(env):
    project = _create(env)
    _activate(env, project)
    client, _state = env
    slug = project["slug"]
    with projects_db.connect_closing() as conn:
        oid = projects_db.get_project_outputs(conn, project["id"])[0]["id"]
    client.post(f"/api/registry/projects/{slug}/outputs/{oid}/deliver", json={})

    detail = client.get(f"/api/registry/projects/{slug}").json()
    progress = detail["progress"]
    assert progress["rung"] == "outputs"
    assert "of 1 outputs accepted" in progress["headline"]
    # The card rollup rides along at every rung.
    assert progress["cards"]["total"] == 0


def test_progress_falls_to_rung3_labelled_cards(env):
    project = _create(env)
    client, _state = env
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    progress = detail["progress"]
    assert progress["rung"] == "cards"
    assert progress["label"] == "cards"
    assert "cards done" in progress["headline"]


def test_progress_rung2_hook_consulted_when_a_goal_is_linked(env, monkeypatch):
    project = _create(env)
    client, _state = env
    assert client.post(
        f"/api/registry/projects/{project['slug']}/links",
        json={"kind": "goal", "ref": "g_1", "profile": "default"},
    ).status_code == 200

    seen = {}

    def _hook(proj, link):
        seen["link"] = link
        return {"headline": "40% to the metric"}

    monkeypatch.setattr(projects_api, "goal_progress_hook", _hook)
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert detail["progress"]["rung"] == "goal"
    assert seen["link"]["ref"] == "g_1"

    # Hook returns None → the ladder falls through to the labelled rollup.
    monkeypatch.setattr(projects_api, "goal_progress_hook", lambda p, l: None)
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    assert detail["progress"]["rung"] == "cards"


def test_standing_progress_never_a_percentage(env):
    project = _create(env, body={"cadence": "standing"})
    client, _state = env
    with projects_db.connect_closing() as conn:
        projects_db.update_project_fields(
            conn, project["id"], {"review_every": "30d"}
        )
    detail = client.get(f"/api/registry/projects/{project['slug']}").json()
    progress = detail["progress"]
    assert progress["rung"] == "standing"
    assert "%" not in progress["headline"]
    assert "delivered this period" in progress["headline"]


# ---------------------------------------------------------------------------
# Rule 4 — board reads stay principal-filtered through the project surface
# ---------------------------------------------------------------------------


def test_board_and_card_reads_hide_other_users_private_cards(env):
    project = _create(env)
    _activate(env, project)
    client, _state = env
    slug = project["slug"]

    # One shared card, one private card owned by somebody else (seeded
    # through the store: the API would never create it this way).
    assert client.post(
        f"/api/registry/projects/{slug}/cards", json={"title": "Shared card"}
    ).status_code == 200
    with kanban_db.connect_closing() as bconn:
        private_tid = kanban_db.create_task(
            bconn,
            title="Someone else's private card",
            project_id=project["id"],
            owner_user_id="mallory",
            visibility="private:mallory",
            triage=True,
        )

    # As a non-owner member the private card must be invisible.
    _member(project["id"], "ada", "lead")
    client, state = env
    state["actor"] = MEMBER_P
    board = client.get(f"/api/registry/projects/{slug}/board").json()
    titles = [t["title"] for c in board["columns"] for t in c["tasks"]]
    assert "Shared card" in titles
    assert "Someone else's private card" not in titles

    assert client.get(
        f"/api/registry/projects/{slug}/cards/{private_tid}"
    ).status_code == 404


def test_card_create_lands_in_triage_on_the_project(env):
    project = _create(env)
    _activate(env, project)
    client, _state = env
    resp = client.post(
        f"/api/registry/projects/{project['slug']}/cards",
        json={"title": "Draft the outline"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triage"

    with kanban_db.connect_closing() as bconn:
        task = kanban_db.get_task(bconn, body["task_id"])
    assert task.status == "triage"
    assert task.project_id == project["id"]


# ---------------------------------------------------------------------------
# Members / profiles guardrails
# ---------------------------------------------------------------------------


def test_last_lead_cannot_be_removed(env):
    project = _create(env)
    client, _state = env
    resp = client.delete(f"/api/registry/projects/{project['slug']}/members/leo")
    assert resp.status_code == 409

    _member(project["id"], "ada", "lead")
    assert (
        client.delete(f"/api/registry/projects/{project['slug']}/members/leo").status_code
        == 200
    )


def test_last_profile_cannot_be_detached(env):
    project = _create(env)
    client, _state = env
    assert (
        client.delete(
            f"/api/registry/projects/{project['slug']}/profiles/default"
        ).status_code
        == 409
    )
    assert client.post(
        f"/api/registry/projects/{project['slug']}/profiles",
        json={"profile": "research"},
    ).status_code == 200
    assert (
        client.delete(
            f"/api/registry/projects/{project['slug']}/profiles/default"
        ).status_code
        == 200
    )


def test_links_delete_detaches_the_pointer(env):
    project = _create(env)
    client, _state = env
    slug = project["slug"]
    assert client.post(
        f"/api/registry/projects/{slug}/links",
        json={"kind": "reference", "ref": "/docs/spec.md", "label": "Spec"},
    ).status_code == 200
    # Duplicate link is a 409 — the PK is the dedupe.
    assert client.post(
        f"/api/registry/projects/{slug}/links",
        json={"kind": "reference", "ref": "/docs/spec.md"},
    ).status_code == 409
    assert client.request(
        "DELETE",
        f"/api/registry/projects/{slug}/links",
        json={"kind": "reference", "ref": "/docs/spec.md", "profile": "default"},
    ).status_code == 200
    detail = client.get(f"/api/registry/projects/{slug}").json()
    assert detail["links"].get("reference", []) == []
