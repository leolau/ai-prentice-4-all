"""Tests for the shared-root Projects store (hermes_cli/projects_db).

Root anchoring + the legacy import live in test_projects_root_store.py;
this file covers the record CRUD and the §2.2 store-level constraints.
"""

from __future__ import annotations

import pytest

from hermes_cli import projects_db as pdb


@pytest.fixture
def conn(tmp_path):
    c = pdb.connect(db_path=tmp_path / "projects.db")
    try:
        yield c
    finally:
        c.close()


def test_record_and_list_discovered_repos(conn):
    n = pdb.record_discovered_repos(conn, [("/www/alpha", "alpha"), ("/www/beta", None)])
    assert n == 2

    rows = {r["root"]: r["label"] for r in pdb.list_discovered_repos(conn)}
    assert rows["/www/alpha"] == "alpha"
    # Label defaults to the basename when not given.
    assert rows["/www/beta"] == "beta"


def test_record_discovered_repos_upserts(conn):
    pdb.record_discovered_repos(conn, [("/www/alpha", "old")])
    pdb.record_discovered_repos(conn, [("/www/alpha", "new")])

    rows = pdb.list_discovered_repos(conn)
    assert len(rows) == 1
    assert rows[0]["label"] == "new"


def test_record_discovered_repos_replace_drops_stale_rows(conn):
    pdb.record_discovered_repos(conn, [("/www/alpha", "alpha"), ("/www/beta", "beta")])
    pdb.record_discovered_repos(conn, [("/www/alpha", "fresh")], replace=True)

    rows = {r["root"]: r["label"] for r in pdb.list_discovered_repos(conn)}
    assert rows == {"/www/alpha": "fresh"}


def test_create_get_list(conn):
    pid = pdb.create_project(conn, name="Hermes Agent", folders=["/tmp/hermes"])
    proj = pdb.get_project(conn, pid)

    assert proj is not None
    assert proj.slug == "hermes-agent"
    assert proj.name == "Hermes Agent"
    # First folder becomes primary.
    assert proj.primary_path == "/tmp/hermes"
    assert [f.path for f in proj.folders] == ["/tmp/hermes"]
    assert proj.folders[0].is_primary is True

    # Lookup by slug too.
    assert pdb.get_project(conn, "hermes-agent").id == pid
    assert len(pdb.list_projects(conn)) == 1


def test_slug_collision_disambiguates(conn):
    pdb.create_project(conn, name="Hermes Agent")
    pdb.create_project(conn, name="Hermes Agent")
    slugs = sorted(p.slug for p in pdb.list_projects(conn))

    assert slugs == ["hermes-agent", "hermes-agent-2"]


def test_empty_name_rejected(conn):
    with pytest.raises(ValueError):
        pdb.create_project(conn, name="   ")


def test_add_remove_folder_and_primary_repoint(conn):
    pid = pdb.create_project(conn, name="P", folders=["/a"])
    pdb.add_folder(conn, pid, "/b")
    pdb.add_folder(conn, pid, "/c", is_primary=True)

    proj = pdb.get_project(conn, pid)
    assert proj.primary_path == "/c"
    assert {f.path for f in proj.folders} == {"/a", "/b", "/c"}

    # Removing the primary repoints to the oldest remaining folder.
    pdb.remove_folder(conn, pid, "/c")
    proj = pdb.get_project(conn, pid)
    assert proj.primary_path == "/a"

    # Removing the last folder clears the primary.
    pdb.remove_folder(conn, pid, "/a")
    pdb.remove_folder(conn, pid, "/b")
    proj = pdb.get_project(conn, pid)
    assert proj.primary_path is None
    assert proj.folders == []


def test_set_primary_requires_existing_folder(conn):
    pid = pdb.create_project(conn, name="P", folders=["/a"])
    assert pdb.set_primary(conn, pid, "/nope") is False
    assert pdb.set_primary(conn, pid, "/a") is True


def test_paths_normalized(conn):
    pid = pdb.create_project(conn, name="P", folders=["/a/b/../c/"])
    proj = pdb.get_project(conn, pid)
    # Trailing slash stripped, .. collapsed.
    assert proj.primary_path == "/a/c"


def test_project_for_path_longest_prefix(conn):
    outer = pdb.create_project(conn, name="Outer", folders=["/www"])
    inner = pdb.create_project(conn, name="Inner", folders=["/www/app"])

    assert pdb.project_for_path(conn, "/www/app/src/x.py").id == inner
    assert pdb.project_for_path(conn, "/www/other").id == outer
    assert pdb.project_for_path(conn, "/elsewhere") is None
    # Segment-wise prefix only: /www/app must not match /www/application.
    assert pdb.project_for_path(conn, "/www/application").id == outer


def test_project_for_path_skips_archived(conn):
    pid = pdb.create_project(conn, name="P", folders=["/www/app"])
    pdb.archive_project(conn, pid)

    assert pdb.project_for_path(conn, "/www/app/src") is None
    # Archived hidden from the default list but visible with include_archived.
    assert pdb.list_projects(conn) == []
    assert len(pdb.list_projects(conn, include_archived=True)) == 1

    pdb.restore_project(conn, pid)
    assert pdb.project_for_path(conn, "/www/app/src").id == pid


def test_active_pointer(conn):
    pid = pdb.create_project(conn, name="P")
    assert pdb.get_active_id(conn) is None

    pdb.set_active(conn, pid)
    assert pdb.get_active_id(conn) == pid

    pdb.set_active(conn, None)
    assert pdb.get_active_id(conn) is None


def test_branch_name_for_is_deterministic():
    proj = pdb.Project(id="p_1", slug="web-app", name="Web App", created_at=0)

    assert pdb.branch_name_for(proj, "t_abc") == "web-app/t_abc"
    assert pdb.branch_name_for(proj, "t_abc", title="Add login!") == "web-app/t_abc-add-login"
    # Stable across calls.
    assert pdb.branch_name_for(proj, "t_abc") == pdb.branch_name_for(proj, "t_abc")


def test_per_profile_isolation(tmp_path):
    # Two distinct DB paths stand in for two profiles' HERMES_HOME.
    a = pdb.connect(db_path=tmp_path / "a" / "projects.db")
    b = pdb.connect(db_path=tmp_path / "b" / "projects.db")
    try:
        pdb.create_project(a, name="Only In A", folders=["/a"])

        assert [p.slug for p in pdb.list_projects(a)] == ["only-in-a"]
        assert pdb.list_projects(b) == []
    finally:
        a.close()
        b.close()


def test_db_path_root_anchored():
    # The store resolves through the same resolver the kanban board uses.
    # (In tests HERMES_HOME is a custom layout, so kanban_home() ==
    # HERMES_HOME and the path still lands under the isolated home.)
    from hermes_cli.kanban_db import kanban_home

    assert pdb.projects_db_path() == kanban_home() / "projects.db"


def test_db_path_env_override(tmp_path, monkeypatch):
    # HERMES_PROJECTS_DB is the explicit override — and the reversibility
    # lever for the root migration.
    target = tmp_path / "elsewhere" / "projects.db"
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(target))
    assert pdb.projects_db_path() == target


def test_legacy_profile_path_still_named():
    # The old per-profile location stays nameable (backups, diagnostics).
    assert pdb.legacy_profile_projects_db_path().name == "projects.db"


# ---------------------------------------------------------------------------
# §2.2 store-level constraints
# ---------------------------------------------------------------------------


def test_name_capped_at_60_chars(conn):
    with pytest.raises(ValueError, match="name"):
        pdb.create_project(conn, name="x" * 61)


def test_goal_capped_at_160_chars(conn):
    with pytest.raises(ValueError, match="goal"):
        pdb.create_project(conn, name="P", goal="g" * 161)


def test_create_full_project_requires_goal_and_description(conn):
    with pytest.raises(ValueError, match="goal"):
        pdb.create_full_project(conn, goal="   ", description="brief")
    with pytest.raises(ValueError, match="description"):
        pdb.create_full_project(conn, goal="Ship the thing", description="  ")


def test_create_full_project_lands_in_planning(conn):
    pid = pdb.create_full_project(
        conn, goal="Acme is live on prod with the team trained", description="brief"
    )
    proj = pdb.get_project(conn, pid)
    assert proj.status == "planning"
    assert proj.cadence == "one_off"
    assert proj.autonomy == "supervised"


def test_name_defaults_from_goal_leading_clause(conn):
    # Em-dash, colon and period split the leading clause; otherwise the
    # first six words.
    pid = pdb.create_full_project(
        conn, goal="Land Q3 revenue — Acme rollout and training", description="d"
    )
    assert pdb.get_project(conn, pid).name == "Land Q3 revenue"

    pid = pdb.create_full_project(
        conn, goal="Send the Monday digest: weekly summary mail", description="d"
    )
    assert pdb.get_project(conn, pid).name == "Send the Monday digest"

    pid = pdb.create_full_project(
        conn,
        goal="One two three four five six seven eight nine ten",
        description="d",
    )
    assert pdb.get_project(conn, pid).name == "One two three four five six"


def test_goal_and_name_edit_independently(conn):
    pid = pdb.create_full_project(conn, goal="First goal sentence", description="d")
    # Re-wording the goal never renames the project…
    pdb.update_project(conn, pid, goal="Second goal sentence")
    proj = pdb.get_project(conn, pid)
    assert proj.goal == "Second goal sentence"
    assert proj.name == "First goal sentence"
    # …and renaming never rewrites the goal.
    pdb.update_project(conn, pid, name="New Label")
    proj = pdb.get_project(conn, pid)
    assert proj.name == "New Label"
    assert proj.goal == "Second goal sentence"
    # The store re-validates on patch.
    with pytest.raises(ValueError, match="goal"):
        pdb.update_project(conn, pid, goal="g" * 161)


def _seed_planning_row(conn, pid):
    """Write the §2.2 gate rows (output + member + profile) directly."""
    import time

    from hermes_cli.sqlite_util import write_txn

    now = int(time.time())
    with write_txn(conn):
        conn.execute(
            "INSERT INTO project_outputs "
            "(id, project_id, seq, title, created_at) VALUES (?, ?, 1, 'Out', ?)",
            ("o_1", pid, now),
        )
    pdb.add_project_member(conn, project_id=pid, user_id="u1", role="lead")
    pdb.add_project_profile(conn, project_id=pid, profile="default")


def test_leaving_planning_refused_until_gate_rows_exist(conn):
    pid = pdb.create_full_project(conn, goal="Gate test", description="d")

    with pytest.raises(ValueError, match="output") as exc:
        pdb.set_project_status(conn, pid, "active")
    # Every missing piece is named.
    assert "member" in str(exc.value) and "profile" in str(exc.value)

    _seed_planning_row(conn, pid)
    assert pdb.set_project_status(conn, pid, "active") is True
    assert pdb.get_project(conn, pid).status == "active"


def test_status_vocabulary_validated(conn):
    pid = pdb.create_project(conn, name="P")
    with pytest.raises(ValueError, match="status"):
        pdb.set_project_status(conn, pid, "shipping")


def test_member_roles_validated_and_deduplicated(conn):
    pid = pdb.create_project(conn, name="P")
    with pytest.raises(ValueError, match="role"):
        pdb.add_project_member(conn, project_id=pid, user_id="u1", role="boss")

    assert pdb.add_project_member(conn, project_id=pid, user_id="u1", role="lead") is True
    assert pdb.add_project_member(conn, project_id=pid, user_id="u1") is False
    members = pdb.get_project_members(conn, pid)
    assert [m["user_id"] for m in members] == ["u1"]
    assert members[0]["role"] == "lead"

    assert pdb.remove_project_member(conn, pid, "u1") is True
    assert pdb.get_project_members(conn, pid) == []
