"""Root-store migration for the Projects feature (design §2.1).

The legacy per-profile stores (``<root>/profiles/<name>/projects.db``) are
imported into the shared-root DB on first open: row-keyed idempotent, slug
collisions disambiguated with a ``-2`` suffix, each imported row stamped
with ``imported_from_profile``, and the per-profile files left in place,
untouched, so pointing ``HERMES_PROJECTS_DB`` back reverses the migration.
Imported rows land in ``needs_completion`` quarantine (L2): they violate
the mandatory-field invariants, so nothing may activate or schedule them
until a human fills in what is missing.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from hermes_cli import projects_db as pdb

# The pre-Projects-feature schema, verbatim shape: a legacy store has none of
# the ed.3 columns, so the import must copy only what exists and let the
# additive defaults fill the rest.
_OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    description   TEXT,
    icon          TEXT,
    color         TEXT,
    board_slug    TEXT,
    primary_path  TEXT,
    created_at    INTEGER NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS project_folders (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    label       TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, path)
);
CREATE TABLE IF NOT EXISTS project_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
CREATE TABLE IF NOT EXISTS project_profiles (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    profile     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    added_by    TEXT,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, profile)
);
CREATE TABLE IF NOT EXISTS project_links (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    profile     TEXT NOT NULL,
    ref         TEXT NOT NULL,
    label       TEXT,
    added_by    TEXT,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, kind, profile, ref)
);
"""


def _write_legacy_store(
    root,
    profile: str,
    projects: list[tuple[str, str, str]],
    *,
    folders: dict | None = None,
    links: list | None = None,
    profiles: list | None = None,
) -> None:
    """Create ``<root>/profiles/<profile>/projects.db`` with old-schema rows.

    ``projects`` is a list of ``(id, slug, name)``.
    """
    db = root / "profiles" / profile / "projects.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_OLD_SCHEMA)
        now = int(time.time())
        for pid, slug, name in projects:
            conn.execute(
                "INSERT INTO projects "
                "(id, slug, name, description, created_at, archived) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (pid, slug, name, f"brief for {name}", now),
            )
        for pid, path in (folders or {}).items():
            conn.execute(
                "INSERT INTO project_folders "
                "(project_id, path, label, is_primary, added_at) "
                "VALUES (?, ?, NULL, 1, ?)",
                (pid, path, now),
            )
        for row in links or []:
            conn.execute(
                "INSERT INTO project_links "
                "(project_id, kind, profile, ref, label, added_by, added_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (*row, now),
            )
        for pid, prof in profiles or []:
            conn.execute(
                "INSERT INTO project_profiles "
                "(project_id, profile, role, added_by, added_at) "
                "VALUES (?, ?, 'member', NULL, ?)",
                (pid, prof, now),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A fake shared root with two legacy profile stores, pinned via env."""
    root_dir = tmp_path / "hermes-root"
    _write_legacy_store(
        root_dir,
        "default",
        [("p_aaaa1111", "acme-rollout", "Acme rollout")],
        folders={"p_aaaa1111": "/www/acme"},
        links=[("p_aaaa1111", "todo", "default", "t_1", "a to-do")],
        profiles=[("p_aaaa1111", "default")],
    )
    _write_legacy_store(
        root_dir,
        "research",
        [("p_bbbb2222", "acme-rollout", "Acme rollout (research copy)")],
    )
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(root_dir / "projects.db"))
    return root_dir


def test_first_open_imports_every_profile_store(root):
    with pdb.connect_closing() as conn:
        projects = {p.slug: p for p in pdb.list_projects(conn)}

        # Both rows landed; the slug collision got a -2 suffix.
        assert set(projects) == {"acme-rollout", "acme-rollout-2"}
        by_name = {p.name: p for p in projects.values()}

        a = by_name["Acme rollout"]
        b = by_name["Acme rollout (research copy)"]
        assert a.imported_from_profile == "default"
        assert b.imported_from_profile == "research"
        assert a.slug == "acme-rollout"
        assert b.slug == "acme-rollout-2"

        # Folders, links and profiles followed their project.
        assert [f.path for f in a.folders] == ["/www/acme"]
        assert pdb.get_project_links(conn, a.id, kind="todo")[0]["ref"] == "t_1"
        assert [r["profile"] for r in pdb.get_project_profiles(conn, a.id)] == [
            "default"
        ]

    # Additive columns carry their defaults on imported rows — except the
    # status: an import lands in quarantine, not in a live state (L2).
    assert b.status == "needs_completion"
    assert b.cadence == "one_off"
    assert b.goal is None


def test_imported_project_cannot_leave_quarantine_until_completed(root):
    """L2: the exit gate names every missing mandatory field, and
    completing them is what unlocks activation."""
    with pdb.connect_closing() as conn:
        project = next(
            p for p in pdb.list_projects(conn) if p.slug == "acme-rollout-2"
        )
        # No goal, no outputs, no host profile — the gate names all three.
        with pytest.raises(ValueError) as exc:
            pdb.set_project_status(conn, project.id, "active")
        msg = str(exc.value)
        assert "needs completion" in msg
        for piece in ("a goal", "at least one output", "a host profile"):
            assert piece in msg

        # Complete it, and the same move lands.
        pdb.update_project(conn, project.id, goal="Ship the rollout")
        pdb.add_project_output(
            conn, project_id=project.id, title="The rollout plan"
        )
        pdb.add_project_profile(
            conn, project_id=project.id, profile="research", role="host"
        )
        assert pdb.set_project_status(conn, project.id, "active")
        assert pdb.get_project(conn, project.id).status == "active"


def test_import_is_idempotent(root):
    with pdb.connect_closing() as conn:
        first = pdb.list_projects(conn)
        # A second scan of the same legacy stores imports nothing.
        assert pdb.import_profile_stores(conn, root) == 0
        assert pdb.list_projects(conn) == first


def test_legacy_files_left_in_place(root):
    with pdb.connect_closing() as conn:
        pdb.list_projects(conn)
    # Untouched and unread after the migration — the reversibility property.
    assert (root / "profiles" / "default" / "projects.db").is_file()
    assert (root / "profiles" / "research" / "projects.db").is_file()


def test_reversibility_via_env_override(root):
    # Pointing HERMES_PROJECTS_DB back at a legacy store reads the old rows
    # exactly as they were — the migration never deleted anything.
    legacy = root / "profiles" / "default" / "projects.db"
    with pdb.connect_closing(db_path=legacy) as conn:
        assert [p.slug for p in pdb.list_projects(conn)] == ["acme-rollout"]


def test_corrupt_legacy_store_does_not_block_the_root(root, caplog):
    bad = root / "profiles" / "broken" / "projects.db"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"this is not a sqlite database")

    with pdb.connect_closing() as conn:
        projects = pdb.list_projects(conn)
    # The two good stores still imported; the bad one was skipped.
    assert {p.slug for p in projects} == {"acme-rollout", "acme-rollout-2"}


def test_no_profile_layout_is_a_noop(tmp_path, monkeypatch):
    # Docker/custom layout: no profiles/ dir — nothing to import, and the
    # root DB opens fine.
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    with pdb.connect_closing() as conn:
        assert pdb.list_projects(conn) == []
