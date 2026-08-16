"""``hermes projects`` — the operator surface (design §14, §17 step 9).

Behaviour contracts: the CLI drives the ``projects_api`` router in process,
so every assertion here exercises the real permission gate end to end. The
acting principal is resolved by the CLI's own seam (patched here, exactly as
a short-lived CLI process owns it), never by bypassing the router.
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from hermes_cli import kanban_db, projects_api, projects_cli
from hermes_cli.access import Principal

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    """Isolated projects + kanban stores and a CLI whose principal seam is
    the enrolled owner. ``_Api`` patches the router's resolution seams
    itself; the tests only stand in for the PrincipalStore lookup."""
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))

    async def _resolve(actor):
        assert actor is None
        return OWNER

    async def _enrolled(user_id):
        return set()

    monkeypatch.setattr(projects_cli, "_resolve_principal", _resolve)
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)

    def run(*argv: str) -> int:
        parser = argparse.ArgumentParser(prog="hermes")
        subparsers = parser.add_subparsers(dest="command")
        projects_cli.register_projects_subparser(subparsers)
        args = parser.parse_args(["projects", *argv])
        return args.func(args)

    return run, capsys


def _create(run, capsys, tmp_path) -> str:
    """Create the fixture project and return its slug."""
    brief = tmp_path / "brief.md"
    brief.write_text("A weekly digest compiled and emailed each Monday.")
    assert run(
        "create", "Ship the Monday digest — to every subscriber",
        "--description", str(brief),
        "--output", "The Monday digest email",
        "--host-profile", "default",
        "--name", "Monday digest",
        "--json",
    ) == 0
    return json.loads(capsys.readouterr().out)["slug"]


# ---------------------------------------------------------------------------
# create — the §2.2 mandatory contract
# ---------------------------------------------------------------------------


def test_create_refuses_an_unreadable_description(env, tmp_path, capsys):
    run, _capsys = env
    code = run(
        "create", "Ship the Monday digest",
        "--description", str(tmp_path / "missing.md"),
        "--output", "The digest email",
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "--description must be a readable file path" in err


def test_create_happy_path_starts_in_planning(env, tmp_path, capsys):
    run, _capsys = env
    slug = _create(run, capsys, tmp_path)
    assert slug == "monday-digest"


def test_list_and_show_read_the_record_back(env, tmp_path, capsys):
    run, _capsys = env
    slug = _create(run, capsys, tmp_path)

    assert run("list") == 0
    out = capsys.readouterr().out
    assert slug in out
    assert "▣" in out  # one_off glyph
    assert "Ship the Monday digest" in out

    assert run("show", slug, "--json") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["goal"].startswith("Ship the Monday digest")
    assert shown["status"] == "planning"
    assert shown["outputs"][0]["title"] == "The Monday digest email"


def test_show_on_an_unknown_slug_fails_as_a_404(env, capsys):
    run, _capsys = env
    assert run("show", "no-such-project") == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# outputs + guidance — the judgement verbs
# ---------------------------------------------------------------------------


def test_outputs_add_and_list(env, tmp_path, capsys):
    run, _capsys = env
    slug = _create(run, capsys, tmp_path)

    assert run("outputs", slug, "add", "Sample archive", "--optional") == 0
    out = capsys.readouterr().out
    assert "Added output 'Sample archive'" in out

    assert run("outputs", slug) == 0
    out = capsys.readouterr().out
    assert "The Monday digest email" in out
    assert "Sample archive (optional)" in out


def test_guidance_add_says_next_run(env, tmp_path, capsys):
    run, _capsys = env
    slug = _create(run, capsys, tmp_path)

    assert run("guidance", slug, "add", "Never email before 9am") == 0
    out = capsys.readouterr().out
    assert "applies from the next run" in out

    assert run("guidance", slug) == 0
    out = capsys.readouterr().out
    assert "Never email before 9am" in out


# ---------------------------------------------------------------------------
# cards — including the §10 promotion seam
# ---------------------------------------------------------------------------


def test_card_add_from_todo_promotes(env, tmp_path, monkeypatch, capsys):
    from tests.hermes_cli.test_projects_api_promote import (
        _FakeStore,
        _make_todo,
    )

    run, _capsys = env
    monkeypatch.setattr(
        "hermes_cli.todo_store.default_store",
        lambda mode=None: _FakeStore(_make_todo()),
    )
    slug = _create(run, capsys, tmp_path)

    assert run("card", "add", slug, "Rollout card",
               "--from-todo", "td_1") == 0
    out = capsys.readouterr().out
    assert "promoted from to-do" in out

    from hermes_cli import projects_db

    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, slug)
    with kanban_db.connect_closing(board=project.board_slug) as bconn:
        tasks = kanban_db.list_tasks(bconn, project_id=project.id)
    assert len(tasks) == 1
    card = tasks[0]
    assert card.status == "triage"  # promotion is not dispatch
    assert card.title == "Rollout card"  # the explicit title wins
    assert card.body == "Everything the card should inherit as its body."


# ---------------------------------------------------------------------------
# run + doctor
# ---------------------------------------------------------------------------


def test_score_verb_goes_through_the_human_gate(env, tmp_path, capsys):
    """The CLI claims the operator surface — the same seam it patches for
    principals lets `score` through the §8.1 human-only gate."""
    from hermes_cli import projects_db

    run, _capsys = env
    slug = _create(run, capsys, tmp_path)
    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, slug)
        projects_db.open_project_run(
            conn, project_id=project.id, trigger="manual", profile="default"
        )

    assert run("score", slug, "1", "3", "--note", "too formal") == 0
    out = capsys.readouterr().out
    assert "Scored run 1" in out and "3/5" in out and "too formal" in out

    assert run("show", slug) == 0
    assert "score: 3.0 (last 1 runs)" in capsys.readouterr().out


def test_run_dry_without_a_playbook_explains_itself(env, tmp_path, capsys):
    run, _capsys = env
    slug = _create(run, capsys, tmp_path)

    assert run("run", slug, "--dry-run") == 1
    err = capsys.readouterr().err
    assert "no playbook" in err


# ---------------------------------------------------------------------------
# retro proposals + the member activation crossing (§8.2, step 10)
# ---------------------------------------------------------------------------


def test_retro_proposals_land_inactive_and_activate(env, tmp_path, monkeypatch, capsys):
    import io

    from hermes_cli import projects_db

    run, _capsys = env
    slug = _create(run, capsys, tmp_path)
    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, slug)
        projects_db.open_project_run(
            conn, project_id=project.id, trigger="manual", profile="default"
        )

    monkeypatch.setattr(
        sys, "stdin", io.StringIO("Compiled and sent; the tone was too formal.")
    )
    assert run(
        "retro", slug, "1", "--write",
        "--propose", "directive=Never email before 9am",
        "--propose", "skill=Keep the digest conversational.",
    ) == 0
    out = capsys.readouterr().out
    assert "Retro saved for run 1" in out
    assert "proposed directive" in out and "inactive" in out
    assert "recorded skill candidate" in out

    # The proposal shows up in the guidance list, awaiting a member.
    assert run("guidance", slug) == 0
    out = capsys.readouterr().out
    assert "Never email before 9am" in out
    assert "inactive until a member activates" in out
    directive_id = [
        token for token in out.replace("(", " ").replace(")", " ").split()
        if token.startswith("dir_")
    ][0]

    # Any member crosses it — and it applies from the next run.
    assert run("guidance", slug, "activate", directive_id) == 0
    out = capsys.readouterr().out
    assert "Activated instruction" in out and "next run" in out

    assert run("guidance", slug) == 0
    out = capsys.readouterr().out
    assert "Never email before 9am" in out
    assert "inactive until a member activates" not in out


def test_retro_refuses_a_malformed_proposal(env, tmp_path, monkeypatch, capsys):
    import io

    from hermes_cli import projects_db

    run, _capsys = env
    slug = _create(run, capsys, tmp_path)
    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, slug)
        projects_db.open_project_run(
            conn, project_id=project.id, trigger="manual", profile="default"
        )

    monkeypatch.setattr(sys, "stdin", io.StringIO("A retro."))
    assert run("retro", slug, "1", "--write", "--propose", "memory=x") == 2
    assert "kind one of" in capsys.readouterr().err


def test_doctor_healthy_box(env, capsys):
    run, _capsys = env
    assert run("doctor") == 0
    assert "healthy" in capsys.readouterr().out
