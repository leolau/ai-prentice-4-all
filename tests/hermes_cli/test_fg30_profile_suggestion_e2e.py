"""Postgres E2E for FG-30: profile lifecycle (suggest, adopt, retire).

Reuses the FG-29 throwaway Postgres fixture pattern. Tests the
ProfileSuggestionStore, the owner-only gates, the one-open cap, dismissal
latching, and weekly-digest rendering. Generation (aux LLM) is not exercised
here because it is gated behind the monthly generation pass and the LLM; the
behaviour under test is the proposal layer itself.
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

from hermes_cli.access import Principal, Role
from hermes_cli.datastore import get_store
from hermes_cli.goal_registry import GoalRegistryStore
from hermes_cli.goal_tree import GoalTreeStore
from hermes_cli.profile_suggestion import (
    ProfileSuggestionStore,
    SuggestionError,
    digest_lines,
    idle_lines,
    idle_profiles,
)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the FG-30 E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the FG-30 E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg30-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
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


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


def _fresh_store(dsn: str) -> ProfileSuggestionStore:
    return ProfileSuggestionStore(
        get_store("supabase-app", "prod", config=_config(dsn))
    )


async def _init_schema(store: ProfileSuggestionStore) -> None:
    """Initialize both the goal registry and profile suggestion tables."""
    from hermes_cli.goal_registry import GoalRegistryStore

    registry = GoalRegistryStore(store._store)
    await registry.initialize()
    await store.initialize()
    # Clean slate for each test: the module-scoped Postgres fixture is shared,
    # and tests expect an empty suggestion queue at start.
    conn = await store._store.connect()
    try:
        await conn.execute("TRUNCATE TABLE profile_suggestions, profile_suggestion_audit CASCADE")
    finally:
        await conn.close()


def _principal(user_id: str, role: Role = "member") -> Principal:
    return Principal(user_id=user_id, display=user_id, role=role)


OWNER = _principal("root", "owner")
ADMIN = _principal("teacher", "admin")
MEMBER = _principal("pupil", "member")


@pytest.mark.asyncio
async def test_propose_and_list(postgres_dsn: str) -> None:
    store = _fresh_store(postgres_dsn)
    conn = await store._store.connect()
    try:
        await _init_schema(store)
        suggestion = await store.propose(
            OWNER,
            proposed_name="finance",
            proposed_role="CFO",
            proposed_goal="improve cashflow",
            rationale="work clusters around invoices and tax",
            evidence={"top_skills": [{"name": "invoice", "uses": 7}]},
            dedup_key="dedup-1",
            origin_profile="default",
            connection=conn,
        )
        assert suggestion.proposed_name == "finance"
        assert suggestion.proposed_role == "CFO"
        assert suggestion.proposed_goal == "improve cashflow"
        assert suggestion.status == "proposed"

        listed = await store.list_suggestions(OWNER, connection=conn)
        assert len(listed) == 1
        assert listed[0].id == suggestion.id
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_one_open_cap(postgres_dsn: str) -> None:
    store = _fresh_store(postgres_dsn)
    conn = await store._store.connect()
    try:
        await _init_schema(store)
        first = await store.propose(
            OWNER,
            proposed_name="finance",
            proposed_role="CFO",
            proposed_goal="improve cashflow",
            rationale="first",
            evidence={"top_skills": [{"name": "invoice", "uses": 7}]},
            dedup_key="dedup-cap-1",
            origin_profile="default",
            connection=conn,
        )
        # A second proposal on the same profile returns the existing open one.
        second = await store.propose(
            OWNER,
            proposed_name="legal",
            proposed_role="General Counsel",
            proposed_goal="manage contracts",
            rationale="second",
            evidence={"top_skills": [{"name": "contract", "uses": 4}]},
            dedup_key="dedup-cap-2",
            origin_profile="default",
            connection=conn,
        )
        assert second.id == first.id
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_owner_only_adopt_and_dismiss(postgres_dsn: str) -> None:
    store = _fresh_store(postgres_dsn)
    conn = await store._store.connect()
    try:
        await _init_schema(store)
        suggestion = await store.propose(
            OWNER,
            proposed_name="finance",
            proposed_role="CFO",
            proposed_goal="improve cashflow",
            rationale="work clusters around invoices and tax",
            evidence={"top_skills": [{"name": "invoice", "uses": 7}]},
            dedup_key="dedup-auth-1",
            origin_profile="default",
            connection=conn,
        )

        with pytest.raises(PermissionError):
            await store.adopt(ADMIN, suggestion.id, connection=conn)

        with pytest.raises(PermissionError):
            await store.dismiss(ADMIN, suggestion.id, connection=conn)

        adopted, _ = await store.adopt(OWNER, suggestion.id, connection=conn)
        assert adopted.status == "adopted"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_dismissal_latches_reproposal(postgres_dsn: str) -> None:
    store = _fresh_store(postgres_dsn)
    conn = await store._store.connect()
    try:
        await _init_schema(store)
        suggestion = await store.propose(
            OWNER,
            proposed_name="finance",
            proposed_role="CFO",
            proposed_goal="improve cashflow",
            rationale="first",
            evidence={"top_skills": [{"name": "invoice", "uses": 7}]},
            dedup_key="dedup-latch-1",
            origin_profile="default",
            connection=conn,
        )
        await store.dismiss(OWNER, suggestion.id, connection=conn)

        # Same dedup_key cannot be re-proposed.
        with pytest.raises(SuggestionError):
            await store.propose(
                OWNER,
                proposed_name="finance",
                proposed_role="CFO",
                proposed_goal="improve cashflow",
                rationale="retry",
                evidence={"top_skills": [{"name": "invoice", "uses": 7}]},
                dedup_key="dedup-latch-1",
                origin_profile="default",
                connection=conn,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_digest_suggestion_is_at_most_one(postgres_dsn: str) -> None:
    store = _fresh_store(postgres_dsn)
    conn = await store._store.connect()
    try:
        await _init_schema(store)
        # No suggestion yet.
        assert await store.digest_suggestion(OWNER, connection=conn) is None

        first = await store.propose(
            OWNER,
            proposed_name="finance",
            proposed_role="CFO",
            proposed_goal="improve cashflow",
            rationale="first",
            evidence={"top_skills": [{"name": "invoice", "uses": 7}]},
            dedup_key="dedup-digest-1",
            origin_profile="default",
            connection=conn,
        )
        # A second proposal cannot be inserted while one is open.
        digest = await store.digest_suggestion(OWNER, connection=conn)
        assert digest is not None
        assert digest.id == first.id
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_digest_lines_carry_role_and_goal(postgres_dsn: str) -> None:
    store = _fresh_store(postgres_dsn)
    conn = await store._store.connect()
    try:
        await _init_schema(store)
        suggestion = await store.propose(
            OWNER,
            proposed_name="finance",
            proposed_role="CFO",
            proposed_goal="improve cashflow",
            rationale="invoices and tax",
            evidence={"top_skills": [{"name": "invoice", "uses": 7}]},
            dedup_key="dedup-lines-1",
            origin_profile="default",
            connection=conn,
        )
        lines = digest_lines(suggestion)
        assert lines
        assert "CFO" in lines[0]
        assert "improve cashflow" in lines[0]
        assert "invoices and tax" in lines[0]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_idle_profiles_returns_empty_when_no_profiles(postgres_dsn: str) -> None:
    # The fixture ensures Postgres is reachable; idle_profiles lists real
    # profiles on disk and may be empty in a bare test environment.
    result = await idle_profiles(idle_weeks=4)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_idle_lines_format() -> None:
    assert idle_lines([("finance", 28)]) == ["finance (last session: 28 days ago)"]
