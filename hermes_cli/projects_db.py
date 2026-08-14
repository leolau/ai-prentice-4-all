"""Root-anchored first-class Project store.

A **Project** is the durable record of a piece of work that can be reviewed,
repeated and learnt from (see ``docs/design/projects-feature-design.md``).
It anchors:

- **Desktop session grouping** — a session belongs to a project when its
  ``cwd`` lives under one of the project's folders (longest-prefix match).
- **Kanban task worktrees** — a task linked to a project creates its worktree
  under the project's primary repo with a deterministic branch name, instead
  of the random ``wt/<task-id>`` fallback.

Scope: **shared root**, stored at ``kanban_home() / "projects.db"`` — the
same resolver the kanban board uses, so the two can never disagree about
which root they are on. A project's work IS kanban cards and its schedule IS
a ``hermes cron`` job, so the record must live where both live: above any one
profile. ``HERMES_PROJECTS_DB`` overrides the resolution for tests and odd
deployments (and is the reversibility lever for the root migration).

Legacy per-profile stores (``$HERMES_HOME/projects.db``) are imported into
the root DB on first open (:func:`import_profile_stores`); the per-profile
files are left in place, untouched and unread afterwards.

The schema is intentionally additive: column additions go through
:func:`_add_column_if_missing` so opening an old DB is always safe.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from hermes_cli.sqlite_util import add_column_if_missing as _add_column_if_missing, write_txn
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def projects_db_path() -> Path:
    """The shared-root projects DB path (``kanban_home() / "projects.db"``).

    Resolution order:

    1. ``HERMES_PROJECTS_DB`` env var when set and non-empty (explicit
       override for tests, unusual deployments, and the reversibility path
       for the root migration).
    2. ``kanban_db.kanban_home() / "projects.db"`` — the same resolver the
       board uses, so projects and cards always share a root.

    The kanban import is lazy: ``kanban_db`` imports this module inside a
    function, and a module-level import here would close the loop.
    """
    override = os.environ.get("HERMES_PROJECTS_DB", "").strip()
    if override:
        return Path(override).expanduser()
    from hermes_cli.kanban_db import kanban_home

    return kanban_home() / "projects.db"


def legacy_profile_projects_db_path() -> Path:
    """The old per-profile location (``$HERMES_HOME/projects.db``).

    Kept for the one-shot root import (:func:`import_profile_stores`) and
    for callers that need to *name* the legacy file (backups, diagnostics).
    Runtime reads and writes never touch it.
    """
    return get_hermes_home() / "projects.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_project_folders_path
    ON project_folders(path);

CREATE TABLE IF NOT EXISTS project_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Git repos found by scanning the filesystem (desktop "repo-first" discovery).
-- Cached here so the overview is instant after the first scan instead of
-- re-walking the disk every time the Projects view opens.
CREATE TABLE IF NOT EXISTS discovered_repos (
    root          TEXT PRIMARY KEY,
    label         TEXT,
    last_seen     INTEGER NOT NULL
);

-- Projects page (2026-08-12 plan): cross-profile membership and link table.
-- A project_links row is a pointer, never a copy — the authority stays in
-- the profile that owns it, and `profile` is what makes the pointer resolvable.
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

CREATE INDEX IF NOT EXISTS idx_project_links_kind
    ON project_links(project_id, kind, added_at DESC);

-- ── Projects feature (design §2.2) ───────────────────────────────────────────────

-- People. A member is a *person* (box-wide user id); a participant profile is
-- an *instrument* the work runs on (project_profiles above). Both lists are
-- needed and they are not the same list.
CREATE TABLE IF NOT EXISTS project_members (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',   -- lead | member | viewer
    added_by    TEXT,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

-- [10] contacts: people involved who are not users of this box. No principal,
-- no permissions, no global address book. `address` may be an outbound
-- destination and is therefore PII: returned to members only, never to a
-- viewer, and never compiled into a run prompt unless the step needs it.
CREATE TABLE IF NOT EXISTS project_contacts (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT,                       -- 'client', 'reviewer', 'the list' …
    org         TEXT,
    platform    TEXT,                       -- email | telegram | slack | phone | …
    address     TEXT,                       -- PII; see the note above
    user_id     TEXT,                       -- set when the contact also has a principal
    notes       TEXT,
    created_by  TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_contacts_project
    ON project_contacts(project_id);

-- [3] outputs: the deliverables, declared before the work. Mandatory: a
-- project has at least one row. This table is what gives progress a
-- denominator that means something.
CREATE TABLE IF NOT EXISTS project_outputs (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,          -- display order
    title         TEXT NOT NULL,             -- "The Monday digest email"
    spec          TEXT,                      -- what "good" looks like; free text
    kind          TEXT NOT NULL DEFAULT 'artifact',
                                             -- artifact | file | message | decision | report | code
    required      INTEGER NOT NULL DEFAULT 1,
    recurring     INTEGER NOT NULL DEFAULT 0, -- delivered once per run (repeatable projects)
    status        TEXT NOT NULL DEFAULT 'pending',
                                             -- pending | in_progress | delivered | accepted | dropped
    delivered_at  INTEGER,
    accepted_at   INTEGER,                   -- a human accepted it; only a human may
    accepted_by   TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_outputs_project
    ON project_outputs(project_id, seq);

-- One row per *delivery* of an output: which run produced it and what the
-- artefact is. `recurring` outputs accumulate one row per run.
CREATE TABLE IF NOT EXISTS project_output_deliveries (
    id           TEXT PRIMARY KEY,
    output_id    TEXT NOT NULL REFERENCES project_outputs(id) ON DELETE CASCADE,
    run_id       TEXT,                       -- project_runs.id, when a run produced it
    task_id      TEXT,                       -- the card that produced it, when any
    link_kind    TEXT,                       -- file | url | attachment | session | memory
    link_ref     TEXT,                       -- the pointer, resolved under the caller's principal
    profile      TEXT,                       -- which profile owns the referent
    label        TEXT,                       -- cached display label (link rot)
    note         TEXT,
    delivered_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_output_deliveries_output
    ON project_output_deliveries(output_id, delivered_at DESC);

-- The method: a versioned playbook. One row per revision; `steps` is a JSON
-- array of card templates (design §7). A new revision is created inactive
-- and activated by a human.
CREATE TABLE IF NOT EXISTS project_playbook (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rev          INTEGER NOT NULL,
    body         TEXT NOT NULL DEFAULT '',   -- prose: what this project does
    steps        TEXT NOT NULL DEFAULT '[]', -- JSON: ordered card templates
    active       INTEGER NOT NULL DEFAULT 0,
    created_by   TEXT,
    created_at   INTEGER NOT NULL,
    activated_at INTEGER,
    note         TEXT,                        -- why this revision exists (often a retro id)
    PRIMARY KEY (project_id, rev)
);

-- The method: guidance the user gives over time. A directive is a standing
-- instruction; feedback is a judgement about a run or a card. Both are
-- durable and compiled into future run prompts; neither is ever injected
-- into a live conversation.
CREATE TABLE IF NOT EXISTS project_directives (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,              -- directive | feedback
    body          TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'project',  -- project | run | card
    target_ref    TEXT,                       -- run_no or task_id when scope != project
    rating        TEXT,                       -- feedback only: good | bad (null for directive)
    author_user_id TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    retired_at    INTEGER,
    superseded_by TEXT                        -- another directive id
);
CREATE INDEX IF NOT EXISTS idx_project_directives_active
    ON project_directives(project_id, active, created_at DESC);

-- The record: one row per occurrence (design §6).
CREATE TABLE IF NOT EXISTS project_runs (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_no        INTEGER NOT NULL,
    trigger       TEXT NOT NULL,     -- schedule | manual | event | review
    triggered_by  TEXT,              -- user id, or 'cron:<job_id>'
    profile       TEXT NOT NULL,     -- the host profile the run executed under
    playbook_rev  INTEGER,           -- NULL for a run with no playbook (ad-hoc work)
    status        TEXT NOT NULL,     -- running | waiting | blocked | done | failed | cancelled
    started_at    INTEGER NOT NULL,
    ended_at      INTEGER,
    session_id    TEXT,              -- SessionDB id of the seeded session, when there is one
    trace_id      TEXT,              -- C8 trace; cost and steps are read from it
    outcome       TEXT,              -- one line, machine-set on close
    summary       TEXT,              -- the agent's account of the run
    retro         TEXT,              -- the retrospective; NULL until written
    retro_at      INTEGER,
    score_self    INTEGER,           -- [7] 1–5, the run's own claim, written with the retro
    score_user    INTEGER,           -- [7] 1–5, the human's; only a human may write it
    score_note    TEXT,              -- why that score (the part worth reading)
    scored_by     TEXT,
    scored_at     INTEGER,
    error         TEXT,
    UNIQUE (project_id, run_no)
);
CREATE INDEX IF NOT EXISTS idx_project_runs_recent
    ON project_runs(project_id, run_no DESC);

-- Which cards belong to which run. The mapping lives here so the shared
-- board learns nothing about Projects (no run_id column on tasks).
CREATE TABLE IF NOT EXISTS project_run_cards (
    run_id    TEXT NOT NULL REFERENCES project_runs(id) ON DELETE CASCADE,
    task_id   TEXT NOT NULL,
    step_key  TEXT,                  -- the playbook step it came from, when any
    PRIMARY KEY (run_id, task_id)
);
"""


# ---------------------------------------------------------------------------
# Slug + id helpers
# ---------------------------------------------------------------------------

# Lowercase alphanumerics, hyphens, underscores; 1-64 chars; no leading
# separator. Strict enough to stop traversal and path separators, loose enough
# for kebab-case names like ``hermes-agent``. Display formatting (spaces,
# emoji, capitalisation) lives in ``name``; the slug is just a stable handle.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")


def _slugify(name: str) -> str:
    """Derive a slug candidate from a human name (best-effort)."""
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-_")
    s = s[:64].strip("-_")
    return s or "project"


def normalize_slug(slug: Optional[str]) -> Optional[str]:
    """Lowercase + strip a slug; validate; return ``None`` for empty."""
    if slug is None:
        return None
    s = str(slug).strip().lower()
    if not s:
        return None
    if not _SLUG_RE.match(s):
        raise ValueError(
            f"invalid project slug {slug!r}: must be 1-64 chars, lowercase "
            f"alphanumerics / hyphens / underscores, not starting with "
            f"'-' or '_'"
        )
    return s


def _new_project_id() -> str:
    return "p_" + secrets.token_hex(4)


def _now() -> int:
    return int(time.time())


def _normalize_path(path: str) -> str:
    """Absolute, user-expanded, separator-normalized path (no trailing sep)."""
    p = os.path.abspath(os.path.expanduser(str(path).strip()))
    return p.rstrip("/\\") or p


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_INITIALIZED_PATHS: set[str] = set()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialize if needed) the shared-root projects DB.

    WAL with DELETE fallback for network filesystems (shared helper from
    ``hermes_state``). Schema init is idempotent (``CREATE TABLE IF NOT
    EXISTS`` + additive migrations) and cached per-path per-process. On the
    first open of the default (root) path per process, legacy per-profile
    stores are imported (:func:`import_profile_stores`).
    """
    path = db_path if db_path is not None else projects_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        from hermes_state import apply_wal_with_fallback

        apply_wal_with_fallback(conn, db_label="projects.db")
        conn.execute("PRAGMA foreign_keys=ON")
        if resolved not in _INITIALIZED_PATHS:
            conn.executescript(SCHEMA_SQL)
            _migrate_add_optional_columns(conn)
            if db_path is None:
                # One-shot-per-process import of the legacy per-profile
                # stores. Row-keyed idempotent; a corrupt legacy file must
                # never keep the root store from opening.
                try:
                    import_profile_stores(conn, path.parent)
                except Exception:
                    logger.warning(
                        "projects: legacy profile-store import failed", exc_info=True
                    )
            _INITIALIZED_PATHS.add(resolved)
    except Exception:
        conn.close()
        raise
    return conn


@contextlib.contextmanager
def connect_closing(db_path: Optional[Path] = None):
    """Open a projects DB connection and guarantee it is closed on exit.

    sqlite3's connection context manager only commits/rollbacks; it does NOT
    close the file descriptor. Long-lived processes (gateway, dashboard) route
    many project operations through ``connect()``; without closing, FDs to
    ``projects.db`` accumulate. Mirrors ``kanban_db.connect_closing``.
    """
    conn = connect(db_path=db_path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Columns added to `projects` after v1; re-applied idempotently on every
# open so a legacy DB upgrades in place. (name, full column DDL) pairs —
# SQLite's ADD COLUMN requires a constant default for NOT NULL, which the
# Projects-feature columns (design §2.2) all have.
_PROJECT_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("board_slug", "board_slug TEXT"),
    ("primary_path", "primary_path TEXT"),
    ("icon", "icon TEXT"),
    ("color", "color TEXT"),
    # Projects feature (2026-08) — the ed.3 additive column set.
    ("goal", "goal TEXT"),
    ("visibility", "visibility TEXT NOT NULL DEFAULT 'shared'"),
    ("owner_user_id", "owner_user_id TEXT"),
    ("status", "status TEXT NOT NULL DEFAULT 'active'"),
    ("cadence", "cadence TEXT NOT NULL DEFAULT 'one_off'"),
    ("schedule", "schedule TEXT"),
    ("review_every", "review_every TEXT"),
    ("autonomy", "autonomy TEXT NOT NULL DEFAULT 'supervised'"),
    ("max_in_progress", "max_in_progress INTEGER NOT NULL DEFAULT 1"),
    ("budget_usd_per_run", "budget_usd_per_run REAL"),
    ("definition_of_done", "definition_of_done TEXT"),
    ("target_audience", "target_audience TEXT"),
    ("score_rubric", "score_rubric TEXT"),
    ("toolsets", "toolsets TEXT"),
    ("skills", "skills TEXT"),
    ("due_at", "due_at INTEGER"),
    ("host_profile", "host_profile TEXT"),
    ("cron_job_id", "cron_job_id TEXT"),
    ("summary", "summary TEXT"),
    ("summary_at", "summary_at INTEGER"),
    ("last_reviewed_at", "last_reviewed_at INTEGER"),
    ("next_run_at", "next_run_at INTEGER"),
    ("imported_from_profile", "imported_from_profile TEXT"),
)


def _migrate_add_optional_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after v1 to legacy DBs (safe on every open)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    for col, ddl in _PROJECT_COLUMN_MIGRATIONS:
        if col not in cols:
            _add_column_if_missing(conn, "projects", col, ddl)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

# Field limits and vocabularies the store enforces (design §2.2). The router
# re-checks them for better error codes; enforcing them here means the CLI
# cannot bypass them.
GOAL_MAX_CHARS = 160
NAME_MAX_CHARS = 60
VALID_PROJECT_STATUSES = ("planning", "active", "paused", "done", "archived")
VALID_CADENCES = ("one_off", "repeatable", "standing")
VALID_AUTONOMY_LEVELS = ("manual", "supervised", "autonomous")
VALID_MEMBER_ROLES = ("lead", "member", "viewer")


def _validate_goal(goal: str) -> str:
    """Field [1]: the outcome sentence — non-empty, one sentence, ≤160 chars."""
    g = str(goal or "").strip()
    if not g:
        raise ValueError("project goal must not be empty")
    if len(g) > GOAL_MAX_CHARS:
        raise ValueError(
            f"project goal must be at most {GOAL_MAX_CHARS} chars "
            f"(got {len(g)}): it is one outcome sentence, not the brief"
        )
    return g


def _validate_name(name: str) -> str:
    """The short label — non-empty, ≤60 chars (fits a list row, board, slug)."""
    n = str(name or "").strip()
    if not n:
        raise ValueError("project name must not be empty")
    if len(n) > NAME_MAX_CHARS:
        raise ValueError(
            f"project name must be at most {NAME_MAX_CHARS} chars (got {len(n)})"
        )
    return n


def default_name_from_goal(goal: str) -> str:
    """Derive the short label from the goal's leading clause (design §2.2).

    Leading clause = text before the first ``—``, ``:`` or ``.``; failing
    that, the first six words. Truncated to ``NAME_MAX_CHARS``. Never tracks
    the goal afterwards — the two are edited independently.
    """
    g = str(goal or "").strip()
    for sep in ("—", ":", "."):
        idx = g.find(sep)
        if idx > 0:
            g = g[:idx]
            break
    else:
        words = g.split()
        if len(words) > 6:
            g = " ".join(words[:6])
    return g.strip()[:NAME_MAX_CHARS].strip()


@dataclass
class ProjectFolder:
    path: str
    label: Optional[str] = None
    is_primary: bool = False
    added_at: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "label": self.label,
            "is_primary": bool(self.is_primary),
            "added_at": self.added_at,
        }


@dataclass
class Project:
    id: str
    slug: str
    name: str
    created_at: int
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    board_slug: Optional[str] = None
    primary_path: Optional[str] = None
    archived: bool = False
    folders: List[ProjectFolder] = field(default_factory=list)
    # Projects-feature columns (design §2.2). Defaults match the additive
    # column defaults so legacy rows and new rows read identically.
    goal: Optional[str] = None
    visibility: str = "shared"
    owner_user_id: Optional[str] = None
    status: str = "active"
    cadence: str = "one_off"
    schedule: Optional[str] = None
    review_every: Optional[str] = None
    autonomy: str = "supervised"
    max_in_progress: int = 1
    budget_usd_per_run: Optional[float] = None
    definition_of_done: Optional[str] = None
    target_audience: Optional[str] = None
    score_rubric: Optional[str] = None
    toolsets: Optional[str] = None
    skills: Optional[str] = None
    due_at: Optional[int] = None
    host_profile: Optional[str] = None
    cron_job_id: Optional[str] = None
    summary: Optional[str] = None
    summary_at: Optional[int] = None
    last_reviewed_at: Optional[int] = None
    next_run_at: Optional[int] = None
    imported_from_profile: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "board_slug": self.board_slug,
            "primary_path": self.primary_path,
            "archived": bool(self.archived),
            "created_at": self.created_at,
            "folders": [f.to_dict() for f in self.folders],
            "goal": self.goal,
            "visibility": self.visibility,
            "owner_user_id": self.owner_user_id,
            "status": self.status,
            "cadence": self.cadence,
            "schedule": self.schedule,
            "review_every": self.review_every,
            "autonomy": self.autonomy,
            "max_in_progress": self.max_in_progress,
            "budget_usd_per_run": self.budget_usd_per_run,
            "definition_of_done": self.definition_of_done,
            "target_audience": self.target_audience,
            "score_rubric": self.score_rubric,
            "toolsets": self.toolsets,
            "skills": self.skills,
            "due_at": self.due_at,
            "host_profile": self.host_profile,
            "cron_job_id": self.cron_job_id,
            "summary": self.summary,
            "summary_at": self.summary_at,
            "last_reviewed_at": self.last_reviewed_at,
            "next_run_at": self.next_run_at,
            "imported_from_profile": self.imported_from_profile,
        }


def _row_get(row: sqlite3.Row, key: str, default=None):
    """Read ``row[key]`` when the column exists (legacy DB tolerance)."""
    return row[key] if key in row.keys() else default


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        created_at=row["created_at"],
        description=_row_get(row, "description"),
        icon=_row_get(row, "icon"),
        color=_row_get(row, "color"),
        board_slug=_row_get(row, "board_slug"),
        primary_path=_row_get(row, "primary_path"),
        archived=bool(_row_get(row, "archived", 0)),
        goal=_row_get(row, "goal"),
        visibility=_row_get(row, "visibility", "shared") or "shared",
        owner_user_id=_row_get(row, "owner_user_id"),
        status=_row_get(row, "status", "active") or "active",
        cadence=_row_get(row, "cadence", "one_off") or "one_off",
        schedule=_row_get(row, "schedule"),
        review_every=_row_get(row, "review_every"),
        autonomy=_row_get(row, "autonomy", "supervised") or "supervised",
        max_in_progress=int(_row_get(row, "max_in_progress", 1) or 1),
        budget_usd_per_run=_row_get(row, "budget_usd_per_run"),
        definition_of_done=_row_get(row, "definition_of_done"),
        target_audience=_row_get(row, "target_audience"),
        score_rubric=_row_get(row, "score_rubric"),
        toolsets=_row_get(row, "toolsets"),
        skills=_row_get(row, "skills"),
        due_at=_row_get(row, "due_at"),
        host_profile=_row_get(row, "host_profile"),
        cron_job_id=_row_get(row, "cron_job_id"),
        summary=_row_get(row, "summary"),
        summary_at=_row_get(row, "summary_at"),
        last_reviewed_at=_row_get(row, "last_reviewed_at"),
        next_run_at=_row_get(row, "next_run_at"),
        imported_from_profile=_row_get(row, "imported_from_profile"),
    )


def _load_folders(conn: sqlite3.Connection, project_id: str) -> List[ProjectFolder]:
    rows = conn.execute(
        "SELECT path, label, is_primary, added_at FROM project_folders "
        "WHERE project_id = ? ORDER BY is_primary DESC, added_at ASC",
        (project_id,),
    ).fetchall()
    return [
        ProjectFolder(
            path=r["path"],
            label=r["label"],
            is_primary=bool(r["is_primary"]),
            added_at=r["added_at"],
        )
        for r in rows
    ]


def _attach_folders(conn: sqlite3.Connection, project: Project) -> Project:
    project.folders = _load_folders(conn, project.id)
    return project


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def _unique_slug(conn: sqlite3.Connection, candidate: str) -> str:
    """Return ``candidate`` or ``candidate-2``, ``-3`` ... if taken."""
    base = candidate
    n = 1
    slug = base
    while conn.execute(
        "SELECT 1 FROM projects WHERE slug = ?", (slug,)
    ).fetchone() is not None:
        n += 1
        suffix = f"-{n}"
        slug = (base[: 64 - len(suffix)]).rstrip("-_") + suffix
    return slug


def create_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    slug: Optional[str] = None,
    folders: Optional[Iterable[str]] = None,
    primary_path: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    board_slug: Optional[str] = None,
    goal: Optional[str] = None,
    owner_user_id: Optional[str] = None,
) -> str:
    """Create a project and return its id.

    The low-level writer, kept for the legacy workspace surfaces (the
    desktop's session grouping, ``hermes project``). Full Projects-feature
    creates go through :func:`create_full_project`, which enforces the
    mandatory fields [1]–[4].

    ``folders`` are normalized to absolute paths. If ``primary_path`` is given
    it is added to the folder set (if not already present) and marked primary;
    otherwise the first folder becomes primary.
    """
    name = _validate_name(name)
    validated_goal = _validate_goal(goal) if goal is not None else None

    slug_candidate = normalize_slug(slug) if slug else _slugify(name)
    pid = _new_project_id()
    now = _now()

    folder_paths: List[str] = []
    for f in folders or []:
        norm = _normalize_path(f)
        if norm and norm not in folder_paths:
            folder_paths.append(norm)

    primary = _normalize_path(primary_path) if primary_path else None
    if primary and primary not in folder_paths:
        folder_paths.insert(0, primary)
    if primary is None and folder_paths:
        primary = folder_paths[0]

    with write_txn(conn):
        unique = _unique_slug(conn, slug_candidate)
        conn.execute(
            "INSERT INTO projects "
            "(id, slug, name, description, icon, color, board_slug, "
            " primary_path, created_at, archived, goal, owner_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (
                pid,
                unique,
                name,
                description,
                icon,
                color,
                normalize_slug(board_slug) if board_slug else None,
                primary,
                now,
                validated_goal,
                owner_user_id,
            ),
        )
        for path in folder_paths:
            conn.execute(
                "INSERT INTO project_folders "
                "(project_id, path, label, is_primary, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, path, None, 1 if path == primary else 0, now),
            )
    return pid


def create_full_project(
    conn: sqlite3.Connection,
    *,
    goal: str,
    description: str,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    folders: Optional[Iterable[str]] = None,
    primary_path: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    board_slug: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    cadence: str = "one_off",
    autonomy: str = "supervised",
) -> str:
    """Create a project under the full §2.2 contract and return its id.

    Mandatory: ``goal`` [1] (non-empty, ≤160) and ``description`` [2]
    (non-empty). ``name`` defaults from the goal's leading clause and is
    independently editable afterwards. The row lands in ``planning`` — it
    may only leave once the caller has written at least one output [3], one
    member and one profile [4] (:func:`set_project_status` enforces the
    gate). Outputs/members/profiles are written through their own helpers so
    the caller controls their rows.
    """
    validated_goal = _validate_goal(goal)
    desc = str(description or "").strip()
    if not desc:
        raise ValueError("project description must not be empty")
    if cadence not in VALID_CADENCES:
        raise ValueError(f"cadence must be one of {sorted(VALID_CADENCES)}")
    if autonomy not in VALID_AUTONOMY_LEVELS:
        raise ValueError(f"autonomy must be one of {sorted(VALID_AUTONOMY_LEVELS)}")

    label = _validate_name(name) if name else default_name_from_goal(validated_goal)
    if not label:
        raise ValueError("project name must not be empty")

    pid = create_project(
        conn,
        name=label,
        slug=slug,
        folders=folders,
        primary_path=primary_path,
        description=desc,
        icon=icon,
        color=color,
        board_slug=board_slug,
        goal=validated_goal,
        owner_user_id=owner_user_id,
    )
    with write_txn(conn):
        conn.execute(
            "UPDATE projects SET status = 'planning', cadence = ?, autonomy = ? "
            "WHERE id = ?",
            (cadence, autonomy, pid),
        )
    return pid


def list_projects(
    conn: sqlite3.Connection, *, include_archived: bool = False
) -> List[Project]:
    sql = "SELECT * FROM projects"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY created_at ASC"
    rows = conn.execute(sql).fetchall()
    return [_attach_folders(conn, _project_from_row(r)) for r in rows]


def get_project(
    conn: sqlite3.Connection, id_or_slug: str
) -> Optional[Project]:
    """Look up a project by id first, then by slug."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (id_or_slug,)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM projects WHERE slug = ?", (str(id_or_slug).lower(),)
        ).fetchone()
    if row is None:
        return None
    return _attach_folders(conn, _project_from_row(row))


def update_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    name: Optional[str] = None,
    goal: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    board_slug: Optional[str] = None,
) -> bool:
    """Patch top-level project fields. Only provided fields change.

    ``name`` and ``goal`` are independent fields (design decision 11):
    re-wording the goal never silently renames the project, and renaming
    never rewrites the goal.

    ``icon``, ``color``, and ``board_slug`` accept an empty string to clear
    (store NULL) — passing ``None`` leaves the field untouched, so callers that
    want to clear must send ``""``.
    """
    sets: List[str] = []
    params: List[object] = []
    if name is not None:
        sets.append("name = ?")
        params.append(_validate_name(name))
    if goal is not None:
        sets.append("goal = ?")
        params.append(_validate_goal(goal))
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if icon is not None:
        sets.append("icon = ?")
        params.append(icon or None)
    if color is not None:
        sets.append("color = ?")
        params.append(color or None)
    if board_slug is not None:
        sets.append("board_slug = ?")
        params.append(normalize_slug(board_slug) if board_slug.strip() else None)
    if not sets:
        return False
    params.append(project_id)
    with write_txn(conn):
        cur = conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params
        )
    return cur.rowcount > 0


def set_project_status(
    conn: sqlite3.Connection, project_id: str, status: str
) -> bool:
    """Move a project between statuses, enforcing the §2.2 exit gate.

    Leaving ``planning`` requires at least one output [3], one member and
    one profile [4] — declaring the deliverable after automating its
    production is how you get a run that succeeds at nothing. The error
    names every missing piece.
    """
    if status not in VALID_PROJECT_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_PROJECT_STATUSES)}")
    with write_txn(conn):
        row = conn.execute(
            "SELECT status FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return False
        if row["status"] == "planning" and status != "planning":
            missing: List[str] = []
            for table, label in (
                ("project_outputs", "at least one output"),
                ("project_members", "at least one member"),
                ("project_profiles", "at least one profile"),
            ):
                count = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = ?",
                    (project_id,),
                ).fetchone()["n"]
                if not count:
                    missing.append(label)
            if missing:
                raise ValueError(
                    f"project {project_id} cannot leave 'planning': missing "
                    + ", ".join(missing)
                )
        cur = conn.execute(
            "UPDATE projects SET status = ? WHERE id = ?", (status, project_id)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# project_members (people on the project; profiles live in project_profiles)
# ---------------------------------------------------------------------------


def add_project_member(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    user_id: str,
    role: str = "member",
    added_by: Optional[str] = None,
) -> bool:
    """Add a member. Returns False if already a member."""
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("member user_id must not be empty")
    if role not in VALID_MEMBER_ROLES:
        raise ValueError(f"member role must be one of {sorted(VALID_MEMBER_ROLES)}")
    now = _now()
    with write_txn(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO project_members "
            "(project_id, user_id, role, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, user_id, role, added_by, now),
        )
    return cur.rowcount > 0


def get_project_members(
    conn: sqlite3.Connection, project_id: str
) -> List[dict]:
    """Read ``project_members`` rows, leads first, then by added_at."""
    rows = conn.execute(
        "SELECT * FROM project_members WHERE project_id = ? "
        "ORDER BY CASE role WHEN 'lead' THEN 0 WHEN 'member' THEN 1 ELSE 2 END, "
        "added_at ASC",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_project_member(
    conn: sqlite3.Connection, project_id: str, user_id: str
) -> bool:
    with write_txn(conn):
        cur = conn.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )
    return cur.rowcount > 0


def add_folder(
    conn: sqlite3.Connection,
    project_id: str,
    path: str,
    *,
    label: Optional[str] = None,
    is_primary: bool = False,
) -> str:
    """Add a folder to a project. Returns the normalized path.

    When ``is_primary`` is set, the folder becomes the project's primary repo
    (the previous primary is demoted, and ``projects.primary_path`` updates).
    """
    norm = _normalize_path(path)
    if not norm:
        raise ValueError("folder path must not be empty")
    if get_project(conn, project_id) is None:
        raise ValueError(f"no such project: {project_id}")
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO project_folders "
            "(project_id, path, label, is_primary, added_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (project_id, norm, label, now),
        )
        if label is not None:
            conn.execute(
                "UPDATE project_folders SET label = ? "
                "WHERE project_id = ? AND path = ?",
                (label, project_id, norm),
            )
        if is_primary:
            _set_primary_locked(conn, project_id, norm)
        else:
            # First folder of an empty project becomes primary implicitly.
            existing_primary = conn.execute(
                "SELECT 1 FROM project_folders "
                "WHERE project_id = ? AND is_primary = 1",
                (project_id,),
            ).fetchone()
            if existing_primary is None:
                _set_primary_locked(conn, project_id, norm)
    return norm


def remove_folder(conn: sqlite3.Connection, project_id: str, path: str) -> bool:
    """Remove a folder from a project. Repoints primary if it was primary."""
    norm = _normalize_path(path)
    with write_txn(conn):
        was_primary = conn.execute(
            "SELECT is_primary FROM project_folders "
            "WHERE project_id = ? AND path = ?",
            (project_id, norm),
        ).fetchone()
        cur = conn.execute(
            "DELETE FROM project_folders WHERE project_id = ? AND path = ?",
            (project_id, norm),
        )
        if was_primary is not None and was_primary["is_primary"]:
            nxt = conn.execute(
                "SELECT path FROM project_folders WHERE project_id = ? "
                "ORDER BY added_at ASC LIMIT 1",
                (project_id,),
            ).fetchone()
            new_primary = nxt["path"] if nxt else None
            if new_primary:
                _set_primary_locked(conn, project_id, new_primary)
            else:
                conn.execute(
                    "UPDATE projects SET primary_path = NULL WHERE id = ?",
                    (project_id,),
                )
    return cur.rowcount > 0


def _set_primary_locked(
    conn: sqlite3.Connection, project_id: str, path: str
) -> None:
    """Set the primary folder (caller already holds a write txn)."""
    conn.execute(
        "UPDATE project_folders SET is_primary = 0 WHERE project_id = ?",
        (project_id,),
    )
    conn.execute(
        "UPDATE project_folders SET is_primary = 1 "
        "WHERE project_id = ? AND path = ?",
        (project_id, path),
    )
    conn.execute(
        "UPDATE projects SET primary_path = ? WHERE id = ?",
        (path, project_id),
    )


def set_primary(conn: sqlite3.Connection, project_id: str, path: str) -> bool:
    norm = _normalize_path(path)
    with write_txn(conn):
        exists = conn.execute(
            "SELECT 1 FROM project_folders WHERE project_id = ? AND path = ?",
            (project_id, norm),
        ).fetchone()
        if exists is None:
            return False
        _set_primary_locked(conn, project_id, norm)
    return True


def archive_project(conn: sqlite3.Connection, project_id: str) -> bool:
    with write_txn(conn):
        cur = conn.execute(
            "UPDATE projects SET archived = 1 WHERE id = ?", (project_id,)
        )
    return cur.rowcount > 0


def restore_project(conn: sqlite3.Connection, project_id: str) -> bool:
    with write_txn(conn):
        cur = conn.execute(
            "UPDATE projects SET archived = 0 WHERE id = ?", (project_id,)
        )
    return cur.rowcount > 0


def delete_project(conn: sqlite3.Connection, project_id: str) -> bool:
    """Hard-delete a project and its folders (cascade)."""
    with write_txn(conn):
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Active-project pointer (project_meta KV)
# ---------------------------------------------------------------------------


_ACTIVE_META_KEY = "active_id"


def set_active(conn: sqlite3.Connection, project_id: Optional[str]) -> None:
    """Set (or clear, when ``None``) the active project pointer."""
    with write_txn(conn):
        if project_id is None:
            conn.execute("DELETE FROM project_meta WHERE key = ?", (_ACTIVE_META_KEY,))
        else:
            conn.execute(
                "INSERT INTO project_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_ACTIVE_META_KEY, project_id),
            )


def get_active_id(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM project_meta WHERE key = ?", (_ACTIVE_META_KEY,)
    ).fetchone()
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# Discovered repos (filesystem scan cache)
# ---------------------------------------------------------------------------


def record_discovered_repos(
    conn: sqlite3.Connection,
    repos: Iterable[tuple[str, Optional[str]]],
    *,
    replace: bool = False,
) -> int:
    """Persist scanned git repo roots into the cache.

    ``repos`` is an iterable of ``(root, label)``. Roots are normalized; the
    label falls back to the basename. Returns the number of rows written.

    When ``replace`` is true, this is the authoritative result of a fresh disk
    scan: delete stale rows first so old eval/worktree noise disappears instead
    of living forever in the cache.
    """
    now = _now()
    rows = []
    for root, label in repos:
        norm = _normalize_path(root)
        if not norm:
            continue
        rows.append((norm, (label or os.path.basename(norm) or norm), now))

    with write_txn(conn):
        if replace:
            conn.execute("DELETE FROM discovered_repos")
        if rows:
            conn.executemany(
                "INSERT INTO discovered_repos (root, label, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(root) DO UPDATE SET label = excluded.label, "
                "last_seen = excluded.last_seen",
                rows,
            )
    return len(rows)


def list_discovered_repos(conn: sqlite3.Connection) -> List[dict]:
    """All cached discovered repo roots, most-recently-seen first."""
    rows = conn.execute(
        "SELECT root, label, last_seen FROM discovered_repos ORDER BY last_seen DESC"
    ).fetchall()
    return [
        {"root": r["root"], "label": r["label"], "last_seen": r["last_seen"]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Resolution + naming
# ---------------------------------------------------------------------------


def project_for_path(
    conn: sqlite3.Connection, path: str, *, include_archived: bool = False
) -> Optional[Project]:
    """Return the project owning ``path`` (longest-prefix folder match).

    A folder owns ``path`` when ``path`` equals the folder or is nested under
    it. The most specific (longest) folder wins, so nested projects resolve to
    the innermost one.
    """
    if not str(path or "").strip():
        return None
    target = _normalize_path(path)
    sql = (
        "SELECT pf.project_id AS pid, pf.path AS folder "
        "FROM project_folders pf JOIN projects p ON p.id = pf.project_id"
    )
    if not include_archived:
        sql += " WHERE p.archived = 0"
    best_pid: Optional[str] = None
    best_len = -1
    for row in conn.execute(sql).fetchall():
        folder = row["folder"]
        if target == folder or target.startswith(folder.rstrip("/\\") + os.sep) or \
                target.startswith(folder.rstrip("/\\") + "/"):
            if len(folder) > best_len:
                best_len = len(folder)
                best_pid = row["pid"]
    if best_pid is None:
        return None
    return get_project(conn, best_pid)


# Deterministic branch slug: lowercase, separators collapsed, capped.
_BRANCH_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


def branch_name_for(project: Project, task_id: str, *, title: str = "") -> str:
    """Deterministic branch name for a project-linked kanban task.

    Shape: ``<project-slug>/<task-id>`` (optionally ``-<title-slug>``). Stable
    and human-meaningful, replacing the random ``wt/<task-id>`` fallback.
    """
    slug = project.slug or _slugify(project.name)
    base = f"{slug}/{task_id}"
    if title:
        tslug = _BRANCH_SAFE_RE.sub("-", str(title).strip().lower()).strip("-")
        tslug = tslug[:40].strip("-")
        if tslug:
            base = f"{base}-{tslug}"
    return base


# ---------------------------------------------------------------------------
# project_links + project_profiles (the Projects page link table)
# ---------------------------------------------------------------------------

def add_project_link(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    kind: str,
    profile: str,
    ref: str,
    label: Optional[str] = None,
    added_by: Optional[str] = None,
) -> bool:
    """Write a ``project_links`` row. Returns False on PK conflict (already linked).

    A link is a pointer, never a copy — the authority stays in the profile
    that owns the referenced object. Re-inserting the same link is a no-op.
    """
    now = int(time.time())
    with write_txn(conn):
        cur = conn.execute(
            """INSERT OR IGNORE INTO project_links
               (project_id, kind, profile, ref, label, added_by, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, kind, profile, ref, label, added_by, now),
        )
    return cur.rowcount > 0


def get_project_links(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    kind: Optional[str] = None,
) -> List[dict]:
    """Read ``project_links`` rows, newest first."""
    sql = "SELECT * FROM project_links WHERE project_id = ?"
    params: list = [project_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY added_at DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def add_project_profile(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    profile: str,
    role: str = "member",
    added_by: Optional[str] = None,
) -> bool:
    """Add a profile to a project. Returns False if already a member."""
    now = int(time.time())
    with write_txn(conn):
        cur = conn.execute(
            """INSERT OR IGNORE INTO project_profiles
               (project_id, profile, role, added_by, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, profile, role, added_by, now),
        )
    return cur.rowcount > 0


def get_project_profiles(
    conn: sqlite3.Connection,
    project_id: str,
) -> List[dict]:
    """Read ``project_profiles`` rows."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM project_profiles WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    ]


# ---------------------------------------------------------------------------
# Root migration: import the legacy per-profile stores (design §2.1)
# ---------------------------------------------------------------------------

# The v1 columns a legacy store can carry. Anything newer (goal, status, …)
# cannot exist in a pre-repoint store, so there is nothing else to copy.
_LEGACY_PROJECT_COLUMNS = (
    "id",
    "slug",
    "name",
    "description",
    "icon",
    "color",
    "board_slug",
    "primary_path",
    "created_at",
    "archived",
)


def _legacy_store_candidates(root: Path, target: Path) -> List[tuple]:
    """(profile_name, db_path) for every per-profile projects.db under root.

    Only the profile layout has legacy stores to import: ``<root>/profiles/
    <name>/projects.db``. When ``HERMES_HOME`` equals the root (Docker /
    custom deployments) the legacy DB *is* the root DB — upgraded in place,
    nothing to import — and it is skipped via the target comparison.
    """
    candidates: List[tuple] = []
    profiles_dir = root / "profiles"
    if not profiles_dir.is_dir():
        return candidates
    target_resolved = target.resolve()
    for entry in sorted(profiles_dir.iterdir()):
        try:
            if not entry.is_dir():
                continue
            db = entry / "projects.db"
            if not db.is_file() or db.resolve() == target_resolved:
                continue
        except OSError:
            continue
        candidates.append((entry.name, db))
    return candidates


def _import_one_profile_store(
    conn: sqlite3.Connection, profile: str, db_path: Path
) -> int:
    """Copy one legacy per-profile store into the root store.

    Idempotent, keyed on ``(imported_from_profile, old id)``. Slug
    collisions get a ``-2``-style suffix; an id already owned by a row from
    elsewhere is remapped to a fresh id (children follow). Returns the
    number of projects imported by this call.
    """
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        logger.warning("projects: cannot open legacy store %s: %s", db_path, exc)
        return 0
    imported = 0
    try:
        src.row_factory = sqlite3.Row
        tables = {
            r[0]
            for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "projects" not in tables:
            return 0
        src_cols = {
            r["name"] for r in src.execute("PRAGMA table_info(projects)")
        }
        project_rows = src.execute(
            "SELECT * FROM projects ORDER BY created_at ASC"
        ).fetchall()
        if not project_rows:
            return 0

        id_map: dict = {}
        with write_txn(conn):
            for row in project_rows:
                old_id = row["id"]
                if conn.execute(
                    "SELECT 1 FROM projects "
                    "WHERE imported_from_profile = ? AND id = ?",
                    (profile, old_id),
                ).fetchone() is not None:
                    id_map[old_id] = old_id  # imported on an earlier pass
                    continue
                new_id = old_id
                if conn.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (new_id,)
                ).fetchone() is not None:
                    new_id = _new_project_id()
                    while conn.execute(
                        "SELECT 1 FROM projects WHERE id = ?", (new_id,)
                    ).fetchone() is not None:
                        new_id = _new_project_id()
                slug = _unique_slug(conn, str(row["slug"]).strip() or _slugify(row["name"]))
                conn.execute(
                    "INSERT INTO projects "
                    "(id, slug, name, description, icon, color, board_slug, "
                    " primary_path, created_at, archived, imported_from_profile) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id,
                        slug,
                        row["name"],
                        row["description"] if "description" in src_cols else None,
                        row["icon"] if "icon" in src_cols else None,
                        row["color"] if "color" in src_cols else None,
                        row["board_slug"] if "board_slug" in src_cols else None,
                        row["primary_path"] if "primary_path" in src_cols else None,
                        row["created_at"],
                        int(bool(row["archived"])),
                        profile,
                    ),
                )
                id_map[old_id] = new_id
                imported += 1

            if imported and "project_folders" in tables:
                for r in src.execute("SELECT * FROM project_folders"):
                    new_pid = id_map.get(r["project_id"])
                    if new_pid is None:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO project_folders "
                        "(project_id, path, label, is_primary, added_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            new_pid,
                            r["path"],
                            r["label"],
                            int(bool(r["is_primary"])),
                            r["added_at"],
                        ),
                    )
            if imported and "project_profiles" in tables:
                for r in src.execute("SELECT * FROM project_profiles"):
                    new_pid = id_map.get(r["project_id"])
                    if new_pid is None:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO project_profiles "
                        "(project_id, profile, role, added_by, added_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (new_pid, r["profile"], r["role"], r["added_by"], r["added_at"]),
                    )
            if imported and "project_links" in tables:
                for r in src.execute("SELECT * FROM project_links"):
                    new_pid = id_map.get(r["project_id"])
                    if new_pid is None:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO project_links "
                        "(project_id, kind, profile, ref, label, added_by, added_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_pid,
                            r["kind"],
                            r["profile"],
                            r["ref"],
                            r["label"],
                            r["added_by"],
                            r["added_at"],
                        ),
                    )
    except sqlite3.Error as exc:
        logger.warning("projects: skipping legacy store %s: %s", db_path, exc)
        return 0
    finally:
        with contextlib.suppress(Exception):
            src.close()
    return imported


def import_profile_stores(conn: sqlite3.Connection, root: Path) -> int:
    """Import every legacy per-profile projects.db under ``root``.

    Called on the first open of the root DB (see :func:`connect`). Row-keyed
    idempotent — a second run is a no-op. The per-profile files are left in
    place, untouched, so pointing ``HERMES_PROJECTS_DB`` back at one
    reverses the migration. Returns the number of projects imported.
    """
    imported = 0
    for profile, db_path in _legacy_store_candidates(Path(root), Path(root) / "projects.db"):
        try:
            n = _import_one_profile_store(conn, profile, db_path)
        except Exception as exc:  # never let one bad store stop the rest
            logger.warning("projects: skipping legacy store %s: %s", db_path, exc)
            continue
        if n:
            logger.info(
                "projects: imported %d project(s) from profile store %s", n, db_path
            )
        imported += n
    return imported
