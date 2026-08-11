"""FG-27 E2E — two profiles on one Postgres must not share app data.

This is the gate for the whole profile-per-sub-goal model: the profiles share
a DSN, so if the derived schema or the ownership marker is wrong the two
profiles' rows interleave in one set of tables, and because those rows carry
no provenance column they cannot be separated afterwards.  Mocks cannot show
that — these run against a throwaway Postgres.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

from hermes_cli.datastore import (
    SchemaOwnershipError,
    SupabaseAppStore,
    app_schema,
    get_store,
    initialize_supabase_app,
    verify_schema_owner,
)
import hermes_cli.datastore as datastore


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    daemon = subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg27-{uuid.uuid4().hex[:12]}"
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
            ["docker", "rm", "--force", container],
            check=False,
            capture_output=True,
        )


@pytest.fixture
def hermes_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "hermes-root"
    (root / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    datastore._verified_schemas.clear()
    return root


def _use_profile(root: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


async def _seed(store: SupabaseAppStore, ref: str) -> None:
    connection = await store.connect()
    try:
        await initialize_supabase_app(connection)
        await connection.execute(
            """
            INSERT INTO artifact_definitions (kind, ref, definition)
            VALUES ('config', $1, '{}'::jsonb)
            """,
            ref,
        )
    finally:
        await connection.close()


async def _refs(store: SupabaseAppStore) -> list[str]:
    connection = await store.connect()
    try:
        rows = await connection.fetch(
            "SELECT ref FROM artifact_definitions ORDER BY ref"
        )
        return [row["ref"] for row in rows]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_two_profiles_sharing_one_dsn_keep_separate_app_data(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}

    _use_profile(hermes_root, "finance", monkeypatch)
    finance = get_store("supabase-app", "prod", config=config)
    await _seed(finance, "finance-only")

    _use_profile(hermes_root, "product", monkeypatch)
    product = get_store("supabase-app", "prod", config=config)
    await _seed(product, "product-only")

    assert finance.schema != product.schema
    assert await _refs(product) == ["product-only"]

    _use_profile(hermes_root, "finance", monkeypatch)
    assert await _refs(finance) == ["finance-only"]


@pytest.mark.asyncio
async def test_initialization_claims_the_schema_for_its_profile(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    home = _use_profile(hermes_root, "claimant", monkeypatch)
    store = get_store("supabase-app", "prod", config=config)

    connection = await store.connect()
    try:
        await initialize_supabase_app(connection)
        owner = await connection.fetchrow(
            f"SELECT profile_slug, hermes_home FROM {store.schema}.schema_owner"
        )
        # The dev schema is claimed too — promotion reads it on a prod store.
        dev_owner = await connection.fetchval(
            f"SELECT profile_slug FROM {app_schema('dev')}.schema_owner"
        )
    finally:
        await connection.close()

    assert owner["profile_slug"] == "claimant"
    assert owner["hermes_home"] == str(home.resolve())
    assert dev_owner == "claimant"

    # Reopening as the same profile is the ordinary case and must succeed.
    reopened = await store.connect()
    await reopened.close()


@pytest.mark.asyncio
async def test_another_profile_pointed_at_a_claimed_schema_fails_closed(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    _use_profile(hermes_root, "owner-profile", monkeypatch)
    owned = get_store("supabase-app", "prod", config=config)
    connection = await owned.connect()
    try:
        await initialize_supabase_app(connection)
    finally:
        await connection.close()

    # A misconfiguration — a second profile aimed at the first one's schema.
    _use_profile(hermes_root, "intruder", monkeypatch)
    datastore._verified_schemas.clear()
    intruder = SupabaseAppStore("prod", owned.schema, postgres_dsn)

    with pytest.raises(SchemaOwnershipError) as excinfo:
        await intruder.connect()

    message = str(excinfo.value)
    assert owned.schema in message
    assert "owner_profile" in message or "owner-profile" in message
    assert "intruder" in message

    # And the intruder's own schema is untouched by the refusal.
    _use_profile(hermes_root, "intruder", monkeypatch)
    own_store = get_store("supabase-app", "prod", config=config)
    assert own_store.schema != owned.schema
    await _seed(own_store, "intruder-only")
    assert await _refs(own_store) == ["intruder-only"]


@pytest.mark.asyncio
async def test_access_bootstrap_creates_the_profiles_schema_on_first_contact(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new profile's first Supabase call is usually the C1 access layer.

    Its DDL is unqualified, so it lands in the connection's pinned
    ``search_path`` — which does not exist until something creates the derived
    schema. Found on the real box: every access-backed command (``hermes
    changes``, member/owner commands, the file and inbound registries) failed
    with ``no schema has been selected to create in`` on a fresh named profile.
    """
    from hermes_cli.access import initialize_access

    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    home = _use_profile(hermes_root, "newcomer", monkeypatch)
    store = get_store("supabase-app", "prod", config=config)

    connection = await store.connect()
    try:
        assert await connection.fetchval(
            "SELECT to_regclass($1)", f"{store.schema}.principals"
        ) is None

        await initialize_access(connection)

        assert await connection.fetchval(
            "SELECT to_regclass($1)", f"{store.schema}.principals"
        ) is not None
        owner = await connection.fetchrow(
            f"SELECT profile_slug, hermes_home FROM {store.schema}.schema_owner"
        )
    finally:
        await connection.close()

    # Bootstrapping also claims the schema, so a second profile aimed here is
    # refused rather than silently sharing the principals table.
    assert owner["profile_slug"] == "newcomer"
    assert owner["hermes_home"] == str(home.resolve())

    _use_profile(hermes_root, "outsider", monkeypatch)
    datastore._verified_schemas.clear()
    with pytest.raises(SchemaOwnershipError):
        await SupabaseAppStore("prod", store.schema, postgres_dsn).connect()


@pytest.mark.asyncio
async def test_unclaimed_schema_is_accepted_for_pre_fg27_deployments(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A deployment that predates the marker has tables but no schema_owner
    # row; refusing it would brick a working install.
    connection = await asyncpg.connect(postgres_dsn)
    try:
        await connection.execute("CREATE SCHEMA IF NOT EXISTS app_legacy")
        await verify_schema_owner(connection, schema="app_legacy", dsn=postgres_dsn)
    finally:
        await connection.close()
