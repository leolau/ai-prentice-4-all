"""Postgres E2E + RLS coverage for the unified credential store.

Same throwaway-Postgres pattern as ``test_access_e2e.py``: the C2 contract on
the ``credentials`` table — a member sees own + shared rows only, the owner
sees all, and the conditional single-writer token update loses to a rotated
refresh token. Skips when Docker is unavailable.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from hermes_cli.access import PrincipalStore
from hermes_cli.credential_store import SupabaseCredentialStore
from hermes_cli.datastore import get_store, initialize_supabase_app

PAYLOAD = {
    "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "shh",
    "refresh_token": "1//abc",
    "token_uri": "https://oauth2.googleapis.com/token",
}


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if subprocess.run(["docker", "info"], check=False, capture_output=True).returncode:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")
    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-creds-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432", image,
        ],
        check=True,
        capture_output=True,
        text=True,
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
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container], check=False,
            capture_output=True,
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _setup(dsn: str) -> tuple[SupabaseCredentialStore, object, object, object]:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE; DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_supabase_app(conn)
    finally:
        await conn.close()
    app_store = get_store("supabase-app", "prod", config=_config(dsn))
    store = SupabaseCredentialStore(app_store)
    await store.initialize()
    principals = PrincipalStore(app_store)
    owner = await principals.enroll("own-1", display="Owner", role="owner")
    alice = await principals.enroll("alice", display="Alice")
    bob = await principals.enroll("bob", display="Bob")
    return store, owner, alice, bob


@pytest.mark.asyncio
async def test_c2_visibility_on_credentials(postgres_dsn: str) -> None:
    store, owner, alice, bob = await _setup(postgres_dsn)

    await store.put(
        alice, provider="google", name="alice@x.co",
        kind="google-oauth2", payload=PAYLOAD, services=["email"],
    )
    await store.put(
        alice, provider="google", name="shared@x.co",
        kind="google-oauth2", payload=PAYLOAD, visibility="shared",
    )
    await store.put(
        bob, provider="google", name="bob@x.co",
        kind="google-oauth2", payload=PAYLOAD,
    )

    alice_rows = {c.name for c in await store.list(alice)}
    assert alice_rows == {"alice@x.co", "shared@x.co"}

    bob_rows = {c.name for c in await store.list(bob)}
    assert bob_rows == {"bob@x.co", "shared@x.co"}

    owner_rows = {c.name for c in await store.list(owner)}
    assert owner_rows == {"alice@x.co", "shared@x.co", "bob@x.co"}

    assert await store.get(bob, "google", "alice@x.co") is None
    assert await store.get(owner, "google", "alice@x.co") is not None


@pytest.mark.asyncio
async def test_rls_blocks_direct_member_select(postgres_dsn: str) -> None:
    """Even a hand-rolled query under a member's GUC binding sees nothing.

    Runs under a non-superuser role so FORCE RLS is actually enforced
    (superusers bypass RLS), mirroring test_access_e2e.py.
    """
    store, owner, alice, bob = await _setup(postgres_dsn)
    await store.put(
        alice, provider="google", name="alice@x.co",
        kind="google-oauth2", payload=PAYLOAD,
    )
    from hermes_cli.access import bind_principal

    conn = await store._connect()
    try:
        await conn.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_reader')
                THEN CREATE ROLE app_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_prod TO app_reader;
            GRANT SELECT ON app_prod.credentials TO app_reader;
            """
        )
        async with conn.transaction():
            await bind_principal(conn, bob)
            await conn.execute("SET LOCAL ROLE app_reader")
            rows = await conn.fetch(
                "SELECT name FROM credentials WHERE provider = 'google'"
            )
        assert [r["name"] for r in rows] == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_resolve_for_service_and_conditional_update(
    postgres_dsn: str,
) -> None:
    store, owner, alice, bob = await _setup(postgres_dsn)
    await store.put(
        alice, provider="google", name="alice@x.co",
        kind="google-oauth2", payload=PAYLOAD, services=["email", "calendar"],
    )
    await store.put(
        bob, provider="google", name="bob@x.co",
        kind="google-oauth2", payload=dict(PAYLOAD, refresh_token="1//bob"),
        services=["calendar"],
    )

    email_entries = await store.resolve_for_service("email")
    assert [c.name for c in email_entries] == ["alice@x.co"]
    cal = {c.name for c in await store.resolve_for_service("calendar")}
    assert cal == {"alice@x.co", "bob@x.co"}
    # Full payloads, in-process only.
    assert email_entries[0].payload["refresh_token"] == "1//abc"

    lost = await store.update_tokens(
        "google", "alice@x.co", owner_user_id="alice",
        old_refresh_token="STALE", payload_fragment={"token": "t2"},
    )
    assert lost is False
    won = await store.update_tokens(
        "google", "alice@x.co", owner_user_id="alice",
        old_refresh_token="1//abc",
        payload_fragment={"token": "t2", "refresh_token": "1//rot"},
    )
    assert won is True
    entry = await store.get(alice, "google", "alice@x.co")
    assert entry.payload["refresh_token"] == "1//rot"


@pytest.mark.asyncio
async def test_redacted_view_hides_secrets(postgres_dsn: str) -> None:
    store, owner, alice, _bob = await _setup(postgres_dsn)
    entry = await store.put(
        alice, provider="google", name="alice@x.co",
        kind="google-oauth2", payload=PAYLOAD,
    )
    redacted = entry.redacted()
    assert "refresh_token" not in redacted["payload"]
    assert "client_secret" not in redacted["payload"]
    assert redacted["payload"]["client_id"] == PAYLOAD["client_id"]
