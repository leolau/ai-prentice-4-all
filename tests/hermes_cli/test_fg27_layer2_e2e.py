"""FG-27 Layer 2 E2E — clone refusal, whole-schema migration, RLS after a rename.

Three things mocks cannot show, all against a throwaway Postgres:

* a ``--clone`` whose derived schema is already claimed is refused *before* the
  profile directory exists;
* ``hermes datastore split-profile`` moves a schema with its row counts intact,
  and refuses the two cases where it would guess;
* the C2 row-level-security policies survive ``ALTER SCHEMA … RENAME`` — they
  are written with unqualified table names against a pinned ``search_path``, so
  a rename must not silently drop anyone's isolation.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

import hermes_cli.datastore as datastore
from hermes_cli.access import (
    Principal,
    apply_scope_rls,
    bind_principal,
    initialize_access,
)
from hermes_cli.datastore import (
    SchemaOwnershipError,
    app_schema,
    get_store,
    initialize_supabase_app,
)
from hermes_cli.datastore_cmd import (
    _split_profile,
    datastore_split_profile_command,
)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg27l2-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            "POSTGRES_PASSWORD=hermes-test",
            "--env",
            "POSTGRES_DB=hermes_test",
            "--publish",
            "127.0.0.1::5432",
            image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
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
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container], check=False, capture_output=True
        )


@pytest.fixture
def hermes_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "hermes-root"
    (root / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    datastore._verified_schemas.clear()
    return root


def _profile_home(root: Path, name: str, dsn: str, *, env_ref: bool = False) -> Path:
    """Write a profile home whose config resolves to ``dsn``.

    ``env_ref`` reproduces the live deployment's indirection: the DSN lives in
    the profile's ``.env`` and config.yaml only names it.
    """
    home = root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    if env_ref:
        (home / "config.yaml").write_text(
            "datastore:\n  supabase_app:\n    dsn: ${DATABASE_URL}\n", encoding="utf-8"
        )
        (home / ".env").write_text(f"DATABASE_URL={dsn}\n", encoding="utf-8")
    else:
        (home / "config.yaml").write_text(
            f"datastore:\n  supabase_app:\n    dsn: {dsn}\n", encoding="utf-8"
        )
    return home


def _patch_profile_dirs(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_cli import profiles as profiles_mod

    monkeypatch.setattr(
        profiles_mod, "get_profile_dir", lambda name: root / "profiles" / name
    )


async def _visible_as_sam(connection: asyncpg.Connection, schema: str) -> list[str]:
    """Read ``rls_probe`` as a ``NOBYPASSRLS`` role bound to ``sam``.

    ``postgres`` is ``BYPASSRLS``, so a policy checked as the connecting
    superuser proves nothing at all.
    """
    await connection.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fg27_reader')
            THEN CREATE ROLE fg27_reader NOLOGIN; END IF;
        END $$;
        GRANT USAGE ON SCHEMA {schema} TO fg27_reader;
        GRANT SELECT ON {schema}.rls_probe TO fg27_reader;
        """
    )
    async with connection.transaction():
        await bind_principal(connection, Principal("sam", "Sam", "member"))
        await connection.execute("SET LOCAL ROLE fg27_reader")
        rows = await connection.fetch("SELECT id FROM rls_probe ORDER BY id")
        return [row["id"] for row in rows]


async def _claim(dsn: str, home: Path) -> None:
    """Let a profile at ``home`` create and claim its own schemas."""
    import os

    previous = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    datastore._verified_schemas.clear()
    try:
        store = get_store(
            "supabase-app", "prod", config={"datastore": {"supabase_app": {"dsn": dsn}}}
        )
        connection = await store.connect()
        try:
            await initialize_supabase_app(connection)
            await initialize_access(connection)
        finally:
            await connection.close()
    finally:
        if previous is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous
        datastore._verified_schemas.clear()


def test_clone_onto_a_claimed_schema_is_refused_before_the_profile_exists(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clone source's ``.env`` carries the DSN — resolution, not text.

    ``product`` already owns ``app_prod_finance`` (a misnamed schema, or a
    profile that was renamed).  Cloning ``product`` into ``finance`` would put
    two profiles' principals in one table.
    """
    from hermes_cli import profiles as profiles_mod

    _patch_profile_dirs(hermes_root, monkeypatch)
    source = _profile_home(hermes_root, "product", postgres_dsn, env_ref=True)
    monkeypatch.setenv("HERMES_HOME", str(source))

    # Claim the schema the new profile *would* derive, under another profile.
    squatter = _profile_home(hermes_root, "finance-old", postgres_dsn)
    asyncio.run(_claim(postgres_dsn, squatter))

    async def _rename_to_finance() -> None:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            for mode in ("prod", "dev"):
                await connection.execute(
                    f"ALTER SCHEMA {app_schema(mode, profile='finance-old')} "
                    f"RENAME TO {app_schema(mode, profile='finance')}"
                )
        finally:
            await connection.close()

    asyncio.run(_rename_to_finance())

    with pytest.raises(SchemaOwnershipError) as excinfo:
        profiles_mod.create_profile(
            "finance", clone_from="product", clone_config=True, no_alias=True
        )

    assert app_schema("prod", profile="finance") in str(excinfo.value)
    assert not (hermes_root / "profiles" / "finance").exists()


def test_split_profile_moves_a_whole_schema_and_verifies_the_rows(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_profile_dirs(hermes_root, monkeypatch)
    old = _profile_home(hermes_root, "legacy", postgres_dsn)
    new = _profile_home(hermes_root, "renamed", postgres_dsn)
    asyncio.run(_claim(postgres_dsn, old))

    async def _seed() -> None:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            await connection.execute(
                f"""
                INSERT INTO {app_schema('prod', profile='legacy')}.principals
                    (user_id, display, role)
                VALUES ('leo', 'Leo', 'owner'), ('sam', 'Sam', 'member')
                """
            )
        finally:
            await connection.close()

    asyncio.run(_seed())

    monkeypatch.setenv("HERMES_HOME", str(new))
    datastore._verified_schemas.clear()
    args = argparse.Namespace(
        from_profile="legacy", to_profile="renamed", mode="both", dry_run=False
    )
    assert datastore_split_profile_command(args) == 0

    async def _after() -> tuple[list[str], object, str | None]:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            users = [
                row["user_id"]
                for row in await connection.fetch(
                    f"SELECT user_id FROM "
                    f"{app_schema('prod', profile='renamed')}.principals "
                    f"ORDER BY user_id"
                )
            ]
            gone = await connection.fetchval(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1",
                app_schema("prod", profile="legacy"),
            )
            owner = await connection.fetchval(
                f"SELECT profile_slug FROM "
                f"{app_schema('prod', profile='renamed')}.schema_owner"
            )
            return users, gone, owner
        finally:
            await connection.close()

    users, gone, owner = asyncio.run(_after())
    assert users == ["leo", "sam"]
    assert gone is None
    # Re-claimed, so the new profile can actually connect to it.
    assert owner == "renamed"


def test_split_profile_refuses_a_schema_a_third_profile_owns(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interleaved rows carry no provenance, so the move would be a guess."""
    _patch_profile_dirs(hermes_root, monkeypatch)
    other = _profile_home(hermes_root, "otherowner", postgres_dsn)
    asyncio.run(_claim(postgres_dsn, other))

    # Pretend the schema derived for 'claimant' is the one being moved, while
    # its marker names a different profile entirely.
    async def _relabel() -> None:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            for mode in ("prod", "dev"):
                await connection.execute(
                    f"ALTER SCHEMA {app_schema(mode, profile='otherowner')} "
                    f"RENAME TO {app_schema(mode, profile='claimant')}"
                )
        finally:
            await connection.close()

    asyncio.run(_relabel())

    target = _profile_home(hermes_root, "target", postgres_dsn)
    monkeypatch.setenv("HERMES_HOME", str(target))
    datastore._verified_schemas.clear()
    args = argparse.Namespace(
        from_profile="claimant", to_profile="target", mode="prod", dry_run=False
    )
    assert datastore_split_profile_command(args) == 1

    async def _untouched() -> object:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            return await connection.fetchval(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1",
                app_schema("prod", profile="claimant"),
            )
        finally:
            await connection.close()

    assert asyncio.run(_untouched()) == 1


def test_split_profile_refuses_to_merge_onto_a_populated_schema(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_profile_dirs(hermes_root, monkeypatch)
    source = _profile_home(hermes_root, "src", postgres_dsn)
    destination = _profile_home(hermes_root, "dst", postgres_dsn)
    asyncio.run(_claim(postgres_dsn, source))
    asyncio.run(_claim(postgres_dsn, destination))

    async def _seed_destination() -> None:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            await connection.execute(
                f"""
                INSERT INTO {app_schema('prod', profile='dst')}.principals
                    (user_id, display, role)
                VALUES ('resident', 'Resident', 'owner')
                """
            )
        finally:
            await connection.close()

    asyncio.run(_seed_destination())

    monkeypatch.setenv("HERMES_HOME", str(destination))
    datastore._verified_schemas.clear()
    args = argparse.Namespace(
        from_profile="src", to_profile="dst", mode="prod", dry_run=False
    )
    assert datastore_split_profile_command(args) == 1

    async def _both_intact() -> tuple[object, object]:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            return (
                await connection.fetchval(
                    "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1",
                    app_schema("prod", profile="src"),
                ),
                await connection.fetchval(
                    f"SELECT user_id FROM {app_schema('prod', profile='dst')}.principals"
                ),
            )
        finally:
            await connection.close()

    assert asyncio.run(_both_intact()) == (1, "resident")


def test_a_dry_run_changes_nothing(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_profile_dirs(hermes_root, monkeypatch)
    source = _profile_home(hermes_root, "dryfrom", postgres_dsn)
    destination = _profile_home(hermes_root, "dryto", postgres_dsn)
    asyncio.run(_claim(postgres_dsn, source))

    monkeypatch.setenv("HERMES_HOME", str(destination))
    datastore._verified_schemas.clear()
    args = argparse.Namespace(
        from_profile="dryfrom", to_profile="dryto", mode="both", dry_run=True
    )
    assert datastore_split_profile_command(args) == 0

    async def _still_there() -> tuple[object, object]:
        connection = await asyncpg.connect(postgres_dsn)
        try:
            return (
                await connection.fetchval(
                    "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1",
                    app_schema("prod", profile="dryfrom"),
                ),
                await connection.fetchval(
                    "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1",
                    app_schema("prod", profile="dryto"),
                ),
            )
        finally:
            await connection.close()

    assert asyncio.run(_still_there()) == (1, None)


@pytest.mark.asyncio
async def test_row_level_security_survives_a_schema_rename(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A moved schema must not quietly lose C2 isolation.

    The policies are created with unqualified table names against a pinned
    ``search_path``, so they follow the table rather than the schema name — but
    that is a property worth pinning down, because the failure mode of a rename
    dropping RLS is one member reading everyone's rows with nothing in the logs.
    """
    _patch_profile_dirs(hermes_root, monkeypatch)
    home = _profile_home(hermes_root, "rlsbefore", postgres_dsn)
    monkeypatch.setenv("HERMES_HOME", str(home))
    datastore._verified_schemas.clear()

    store = get_store(
        "supabase-app",
        "prod",
        config={"datastore": {"supabase_app": {"dsn": postgres_dsn}}},
    )
    connection = await store.connect()
    try:
        await initialize_access(connection)
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rls_probe (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                visibility TEXT NOT NULL
            )
            """
        )
        await apply_scope_rls(connection, "rls_probe")
        await connection.execute(
            """
            INSERT INTO rls_probe (id, owner_user_id, visibility) VALUES
                ('mine', 'sam', 'private:sam'),
                ('theirs', 'leo', 'private:leo')
            """
        )
        before = await _visible_as_sam(connection, store.schema)
    finally:
        await connection.close()

    assert before == ["mine"]

    moved = _profile_home(hermes_root, "rlsafter", postgres_dsn)
    monkeypatch.setenv("HERMES_HOME", str(moved))
    datastore._verified_schemas.clear()
    await _split_profile(
        source_profile="rlsbefore",
        target_profile="rlsafter",
        modes=("prod", "dev"),
        dry_run=False,
    )

    datastore._verified_schemas.clear()
    after_store = get_store(
        "supabase-app",
        "prod",
        config={"datastore": {"supabase_app": {"dsn": postgres_dsn}}},
    )
    connection = await after_store.connect()
    try:
        after = await _visible_as_sam(connection, after_store.schema)
        policies = await connection.fetchval(
            "SELECT COUNT(*) FROM pg_policies WHERE schemaname = $1 "
            "AND tablename = 'rls_probe'",
            after_store.schema,
        )
    finally:
        await connection.close()

    assert after == ["mine"]
    assert policies == 1
