"""Real-Postgres E2E for FG-21 P3: downward-only role reads, grants, audit.

Every guarantee here is a *negative* one — who cannot read whom — so none of it
can be trusted against a fake store. The row that must stay invisible is
invisible because of a ``USING`` clause and a correlated sub-select against
``principals``, and the only way to know those are right is to ask a real
Postgres under a role that cannot bypass RLS.

The matrix asserted twice, deliberately: once through the app-layer filter
(``scope_filter``), and once with **no app filter at all** under a
``NOBYPASSRLS`` role, so a future refactor that drops the filter is caught by the
database and a future one that drops the policy is caught by the filter.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from hermes_cli.access import (
    ACCESS_SCHEMA_SQL,
    ITEM_GRANTS_TABLE,
    Principal,
    Role,
    bind_elevated_reads,
    bind_principal,
)
from hermes_cli.datastore import StoreMode, get_store
from plugins.memory.supabase_pgvector.embedding import HashingEmbedder
from plugins.memory.supabase_pgvector.store import (
    MEMORY_AUDIT_TABLE,
    MEMORY_TABLE,
    PgvectorMemoryStore,
)

_PGVECTOR_IMAGE = (
    "pgvector/pgvector@sha256:"
    "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)

OWNER = Principal(user_id="root", display="Root", role="owner")
ADMIN = Principal(user_id="ada", display="Ada", role="admin")
ADMIN2 = Principal(user_id="abe", display="Abe", role="admin")
MEMBER = Principal(user_id="mia", display="Mia", role="member")
MEMBER2 = Principal(user_id="moe", display="Moe", role="member")
VIEWER = Principal(user_id="vic", display="Vic", role="viewer")

EVERYONE = (OWNER, ADMIN, ADMIN2, MEMBER, MEMBER2, VIEWER)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the pgvector E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the pgvector E2E test")

    subprocess.run(
        ["docker", "pull", _PGVECTOR_IMAGE], check=True, capture_output=True
    )
    container = f"hermes-p3-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
            _PGVECTOR_IMAGE,
        ],
        check=True, capture_output=True, text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True, capture_output=True, text=True,
        )
        port = port_result.stdout.strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:hermes-test@127.0.0.1:{port}/hermes_test"
        for _ in range(60):
            try:
                asyncio.run(_probe_postgres(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway pgvector Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False, capture_output=True,
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


def _store(
    dsn: str,
    *,
    role_reads: bool,
    mode: StoreMode = "dev",
) -> PgvectorMemoryStore:
    return PgvectorMemoryStore(
        get_store("supabase-app", mode, config=_config(dsn)),
        embedder=HashingEmbedder(dim=256),
        role_reads=role_reads,
    )


async def _fresh(dsn: str, *, role_reads: bool) -> PgvectorMemoryStore:
    """A clean schema with every principal enrolled and one memory each."""
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_dev CASCADE")
    finally:
        await conn.close()

    store = _store(dsn, role_reads=role_reads)
    conn = await store._connect()
    try:
        await conn.execute(ACCESS_SCHEMA_SQL)
        for principal in EVERYONE:
            await conn.execute(
                "INSERT INTO principals (user_id, display, role) "
                "VALUES ($1, $2, $3)",
                principal.user_id,
                principal.display,
                principal.role,
            )
    finally:
        await conn.close()

    await store.initialize()
    for principal in EVERYONE:
        await store.write(
            principal, f"{principal.user_id} private note", visibility="private"
        )
    await store.write(OWNER, "the shared handbook", visibility="shared")
    return store


async def _visible(store: PgvectorMemoryStore, principal: Principal) -> set[str]:
    records = await store.query(principal, "note handbook", top_k=50)
    return {record.text for record in records}


# ---------------------------------------------------------------------------
# App-layer matrix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_role_reads_off_keeps_every_private_tier_private(
    postgres_dsn: str,
) -> None:
    """The default. An admin is *not* a reader of members by merely existing."""
    store = await _fresh(postgres_dsn, role_reads=False)

    assert await _visible(store, ADMIN) == {
        "ada private note",
        "the shared handbook",
    }
    assert await _visible(store, MEMBER) == {
        "mia private note",
        "the shared handbook",
    }


@pytest.mark.asyncio
async def test_admin_reads_down_but_never_sideways_or_up(
    postgres_dsn: str,
) -> None:
    """The decision, in one assertion: down only.

    ``ada`` (admin) sees members and viewers, does **not** see the peer admin
    ``abe``, and does **not** see the owner.
    """
    store = await _fresh(postgres_dsn, role_reads=True)

    assert await _visible(store, ADMIN) == {
        "ada private note",
        "mia private note",
        "moe private note",
        "vic private note",
        "the shared handbook",
    }


@pytest.mark.asyncio
async def test_member_never_reads_a_peer_member(postgres_dsn: str) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)

    visible = await _visible(store, MEMBER)
    assert "moe private note" not in visible
    assert "ada private note" not in visible
    # A member still ranks above a viewer, which is what the ladder says.
    assert visible == {
        "mia private note",
        "vic private note",
        "the shared handbook",
    }


@pytest.mark.asyncio
async def test_viewer_reads_nobody(postgres_dsn: str) -> None:
    """The bottom rung has nothing below it, so elevation adds nothing."""
    store = await _fresh(postgres_dsn, role_reads=True)

    assert await _visible(store, VIEWER) == {
        "vic private note",
        "the shared handbook",
    }


@pytest.mark.asyncio
async def test_a_claimed_role_the_database_disagrees_with_reads_nothing(
    postgres_dsn: str,
) -> None:
    """An impostor principal is not a principal.

    The elevated clause is correlated against ``principals``, so a session that
    asserts ``role='admin'`` for a user_id nobody enrolled reads its own tier
    and shared \u2014 the rows of enrolled members stay invisible because the
    *subject* side of the comparison comes from the table, not the claim.
    """
    store = await _fresh(postgres_dsn, role_reads=True)
    ghost = Principal(user_id="ghost", display="Ghost", role="admin")

    visible = await _visible(store, ghost)
    assert "mia private note" in visible  # enrolled member, ranks below admin
    assert "ada private note" not in visible

    # And a *subject* who is not enrolled cannot be read by anyone below owner:
    # there is no row in `principals` to rank, so the clause fails closed.
    await store.write(ghost, "ghost private note", visibility="private")
    assert "ghost private note" not in await _visible(store, ADMIN)


# ---------------------------------------------------------------------------
# Provenance + audit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_elevated_rows_are_labelled_and_own_rows_are_not(
    postgres_dsn: str,
) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)

    records = await store.query(ADMIN, "note handbook", top_k=50)
    by_text = {record.text: record for record in records}

    assert by_text["mia private note"].elevated is True
    assert by_text["mia private note"].provenance == "from mia's memory"
    assert by_text["mia private note"].as_dict()["provenance"] == (
        "from mia's memory"
    )
    assert by_text["ada private note"].elevated is False
    assert by_text["ada private note"].provenance == ""
    assert "provenance" not in by_text["the shared handbook"].as_dict()
    # A shared row is shared: reading it is not an elevated read even though
    # the admin outranks the owner-of-the-row's role.
    assert by_text["the shared handbook"].elevated is False


@pytest.mark.asyncio
async def test_the_member_read_can_see_who_read_them(postgres_dsn: str) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)
    await store.query(ADMIN, "tender deadline note", top_k=50, session_id="s-1")

    mine = await store.read_audit(MEMBER)
    assert [entry.reader_user_id for entry in mine] == ["ada"]
    entry = mine[0]
    assert entry.reader_role == "admin"
    assert entry.subject_user_id == "mia"
    assert entry.session_id == "s-1"
    assert entry.query == "tender deadline note"
    assert len(entry.memory_ids) == 1

    # The reader sees their own elevated reads; an unrelated peer sees none of
    # it — the ledger is scoped like the rows it describes.
    assert {e.subject_user_id for e in await store.read_audit(ADMIN)} == {
        "mia", "moe", "vic",
    }
    assert await store.read_audit(ADMIN2) == []


@pytest.mark.asyncio
async def test_reading_only_your_own_memory_writes_no_audit_noise(
    postgres_dsn: str,
) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)
    await store.query(MEMBER, "mia private note", top_k=1)

    conn = await store._connect()
    try:
        rows = await conn.fetchval(
            f"SELECT COUNT(*) FROM {MEMORY_AUDIT_TABLE} "
            "WHERE reader_user_id = 'mia' AND subject_user_id = 'mia'"
        )
    finally:
        await conn.close()
    assert rows == 0


@pytest.mark.asyncio
async def test_owner_bypass_reads_are_audited_too(postgres_dsn: str) -> None:
    """Even with role reads off, the owner sees everything — and is recorded.

    The pre-P3 owner bypass left no trace, which is tolerable with one user and
    not with several.
    """
    store = await _fresh(postgres_dsn, role_reads=False)
    await store.query(OWNER, "note handbook", top_k=50)

    assert [entry.reader_user_id for entry in await store.read_audit(MEMBER)] == [
        "root"
    ]


# ---------------------------------------------------------------------------
# Per-memory grants — the sideways case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_grant_shares_exactly_one_row_and_can_be_revoked(
    postgres_dsn: str,
) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)
    shared = await store.write(MEMBER, "the bid is due friday", visibility="private")
    await store.write(MEMBER, "unrelated private thing", visibility="private")

    before = await store.query(MEMBER2, "the bid is due friday", top_k=50)
    assert "the bid is due friday" not in {record.text for record in before}

    assert await store.share(MEMBER, shared.id, MEMBER2.user_id) is True

    granted = await store.query(MEMBER2, "the bid is due friday", top_k=50)
    texts = {record.text for record in granted}
    assert "the bid is due friday" in texts
    assert "unrelated private thing" not in texts
    assert "mia private note" not in texts

    assert await store.unshare(MEMBER, shared.id, MEMBER2.user_id) is True
    after = await store.query(MEMBER2, "the bid is due friday", top_k=50)
    assert "the bid is due friday" not in {record.text for record in after}


@pytest.mark.asyncio
async def test_only_the_owner_of_a_row_may_share_it(postgres_dsn: str) -> None:
    """An elevated reader is not a redistributor.

    ``ada`` can read ``mia``'s memory by rank; letting her grant it onward would
    make a scoped read an unbounded one, with no way for ``mia`` to take it back.
    """
    store = await _fresh(postgres_dsn, role_reads=True)
    row = await store.write(MEMBER, "mia's phone number", visibility="private")

    # The grantee is mia's *peer*, whom the ladder deliberately does not reach,
    # so a successful re-share would be the only way the row could appear.
    assert await store.share(ADMIN, row.id, MEMBER2.user_id) is False
    assert "mia's phone number" not in {
        record.text
        for record in await store.query(MEMBER2, "mia's phone number", top_k=50)
    }


# ---------------------------------------------------------------------------
# RLS backstop: no app-layer filter at all
# ---------------------------------------------------------------------------

async def _rls_visible(
    store: PgvectorMemoryStore,
    principal: Principal,
    *,
    elevation: bool,
) -> set[str]:
    """Read with NO app filter, under a role that cannot bypass RLS."""
    conn = await store._connect()
    try:
        await conn.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname='app_reader'
                ) THEN CREATE ROLE app_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_dev TO app_reader;
            GRANT SELECT ON app_dev.memories TO app_reader;
            GRANT SELECT ON app_dev.principals TO app_reader;
            -- The policy's grant clause reads item_grants, exactly as
            -- ensure_app_role() grants it in a real deployment.
            GRANT SELECT ON app_dev.item_grants TO app_reader;
            """
        )
        async with conn.transaction():
            await bind_principal(conn, principal)
            await bind_elevated_reads(conn, elevation)
            await conn.execute("SET LOCAL ROLE app_reader")
            rows = await conn.fetch(f"SELECT text FROM {MEMORY_TABLE}")
            return {row["text"] for row in rows}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_enforces_the_same_downward_only_matrix(
    postgres_dsn: str,
) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)

    assert await _rls_visible(store, ADMIN, elevation=True) == {
        "ada private note",
        "mia private note",
        "moe private note",
        "vic private note",
        "the shared handbook",
    }
    # Peer admin and the owner stay invisible at the database level.
    assert await _rls_visible(store, ADMIN2, elevation=True) == {
        "abe private note",
        "mia private note",
        "moe private note",
        "vic private note",
        "the shared handbook",
    }
    assert await _rls_visible(store, VIEWER, elevation=True) == {
        "vic private note",
        "the shared handbook",
    }


@pytest.mark.asyncio
async def test_rls_without_the_elevation_binding_reads_plain_c2(
    postgres_dsn: str,
) -> None:
    """Two independent gates: the policy AND the per-transaction binding.

    A connection that installs the elevated policy but never binds elevation
    reads exactly what C2 always allowed — the fail-closed direction, so a code
    path that forgets the binding under-reads instead of over-reading.
    """
    store = await _fresh(postgres_dsn, role_reads=True)

    assert await _rls_visible(store, ADMIN, elevation=False) == {
        "ada private note",
        "the shared handbook",
    }


@pytest.mark.asyncio
async def test_rls_ignores_elevation_when_the_policy_was_never_installed(
    postgres_dsn: str,
) -> None:
    """An instance with role reads off cannot be talked into them by a GUC."""
    store = await _fresh(postgres_dsn, role_reads=False)

    assert await _rls_visible(store, ADMIN, elevation=True) == {
        "ada private note",
        "the shared handbook",
    }


@pytest.mark.asyncio
async def test_rls_scopes_the_audit_ledger_to_reader_and_subject(
    postgres_dsn: str,
) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)
    await store.query(ADMIN, "note handbook", top_k=50)

    conn = await store._connect()
    try:
        await conn.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname='app_reader'
                ) THEN CREATE ROLE app_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_dev TO app_reader;
            GRANT SELECT ON app_dev.memory_access_audit TO app_reader;
            """
        )

        async def rows_for(principal: Principal) -> set[str]:
            async with conn.transaction():
                await bind_principal(conn, principal)
                await conn.execute("SET LOCAL ROLE app_reader")
                rows = await conn.fetch(
                    f"SELECT subject_user_id FROM {MEMORY_AUDIT_TABLE}"
                )
                return {row["subject_user_id"] for row in rows}

        assert await rows_for(MEMBER) == {"mia"}
        assert await rows_for(ADMIN) == {"mia", "moe", "vic"}
        assert await rows_for(ADMIN2) == set()
        assert await rows_for(OWNER) == {"mia", "moe", "vic"}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_revoked_grant_is_kept_as_history_not_deleted(
    postgres_dsn: str,
) -> None:
    store = await _fresh(postgres_dsn, role_reads=True)
    row = await store.write(MEMBER, "a fact worth sharing", visibility="private")
    await store.share(MEMBER, row.id, MEMBER2.user_id)
    await store.unshare(MEMBER, row.id, MEMBER2.user_id)

    conn = await store._connect()
    try:
        status = await conn.fetchval(
            f"SELECT status FROM {ITEM_GRANTS_TABLE} "
            "WHERE item_kind = 'memory' AND user_id = $1",
            MEMBER2.user_id,
        )
    finally:
        await conn.close()
    assert status == "revoked"


@pytest.mark.asyncio
async def test_roles_ranked_by_the_database_match_the_python_ladder(
    postgres_dsn: str,
) -> None:
    """The ladder is defined once, in Python, and rendered into SQL.

    Asserted against a real Postgres because a drift between the two would be a
    silent access-control bug rather than a failure: the app filter and the
    policy would disagree about who reads whom.
    """
    from hermes_cli.access import ROLE_RANK, _role_rank_sql

    conn = await asyncpg.connect(postgres_dsn, ssl=False)
    try:
        for role, rank in ROLE_RANK.items():
            assert await conn.fetchval(
                f"SELECT {_role_rank_sql('$1::text')}", role
            ) == rank
        assert await conn.fetchval(
            f"SELECT {_role_rank_sql('$1::text')}", "not-a-role"
        ) == max(ROLE_RANK.values())
    finally:
        await conn.close()
