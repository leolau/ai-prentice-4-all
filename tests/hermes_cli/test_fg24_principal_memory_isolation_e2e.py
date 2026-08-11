"""FG-24 E2E — principal-scoped memory through the real C1 resolution path.

Curated memory is file-backed (no schema change), but *which* files a session
reads is decided by the identity the C1 seam resolves out of the profile's
Postgres schema (FG-27).  Mocking that resolution would test nothing: the whole
isolation claim rests on it.  So these run against a throwaway Postgres, enrol
real principals in two profiles' derived schemas, resolve an inbound channel
identity through ``bind_channel_principal``, and build the memory store from
whatever came back.

The test that matters: person A's working memory in profile X is unreachable
from profile Y and from person B.
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

import hermes_cli.datastore as datastore
from gateway.config import Platform
from gateway.inbound import bind_channel_principal
from gateway.session import SessionSource
from hermes_cli.access import PrincipalStore, Role
from hermes_cli.datastore import get_store
from tools.memory_tool import MemoryStore


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    daemon = subprocess.run(["docker", "info"], check=False, capture_output=True, text=True)
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg24-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
            image,
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
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "--force", container], check=False, capture_output=True)


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


async def _enroll(dsn: str, user_id: str, *, handle: str, role: Role) -> None:
    """Enrol a principal + channel identity in the *current* profile's schema."""
    store = PrincipalStore(get_store("supabase-app", "prod", config={
        "datastore": {"supabase_app": {"dsn": dsn}},
    }))
    connection = await store._store.connect()
    try:
        await store.enroll(user_id, display=user_id, role=role, connection=connection)
        await store.link_channel(user_id, Platform.TELEGRAM.value, handle, connection=connection)
    finally:
        await connection.close()


async def _resolve(dsn: str, *, handle: str):
    """Resolve a channel identity the way the gateway does (contract C1)."""
    store = PrincipalStore(get_store("supabase-app", "prod", config={
        "datastore": {"supabase_app": {"dsn": dsn}},
    }))
    source = SessionSource(
        platform=Platform.TELEGRAM, chat_id="c1", user_id=handle, user_name=handle
    )
    principal = await bind_channel_principal(source, store=store, auto_enroll_if_paired=False)
    return principal, source


def _session_store(source: SessionSource) -> MemoryStore:
    """Build the memory store exactly as ``agent/agent_init`` does for a turn."""
    store = MemoryStore(
        user_id=source.internal_user_id,
        role=source.internal_user_role,
    )
    store.load_from_disk()
    return store


@pytest.mark.asyncio
async def test_working_memory_is_unreachable_across_profiles_and_people(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --- profile "cto": the founder (owner) and an engineer (member) ---------
    _use_profile(hermes_root, "cto", monkeypatch)
    await _enroll(postgres_dsn, "u_founder", handle="111", role="owner")
    await _enroll(postgres_dsn, "u_eng", handle="222", role="member")

    founder_principal, founder_source = await _resolve(postgres_dsn, handle="111")
    assert founder_principal is not None and founder_principal.role == "owner"
    founder_cto = _session_store(founder_source)
    founder_cto.add("memory", "deploys run from the release branch")
    founder_cto.add("user", "the founder prefers terse answers")
    founder_cto.add("shared", "the repo is ai-prentice-4-all")

    _, eng_source = await _resolve(postgres_dsn, handle="222")
    eng = _session_store(eng_source)
    eng.add("memory", "the engineer owns the CI pipeline")

    # --- profile "cfo": the same person, a different participation ----------
    _use_profile(hermes_root, "cfo", monkeypatch)
    datastore._verified_schemas.clear()
    await _enroll(postgres_dsn, "u_founder", handle="111", role="owner")
    _, cfo_source = await _resolve(postgres_dsn, handle="111")
    founder_cfo = _session_store(cfo_source)
    founder_cfo.add("memory", "VAT returns are quarterly")

    # The two profiles resolved to different schemas (FG-27) and different
    # participation files, while the person is the same.
    assert cfo_source.internal_user_id == founder_source.internal_user_id == "u_founder"
    reloaded_cfo = _session_store(cfo_source)
    cfo_memory = reloaded_cfo.format_for_system_prompt("memory") or ""
    assert "VAT returns are quarterly" in cfo_memory
    assert "release branch" not in cfo_memory
    assert "CI pipeline" not in cfo_memory
    # Person-level identity followed them; the other profile's shared block did not.
    assert "prefers terse answers" in (reloaded_cfo.format_for_system_prompt("user") or "")
    assert reloaded_cfo.format_for_system_prompt("shared") is None

    # Back in "cto": the founder sees their own participation and the shared
    # block, and never the engineer's memory.
    _use_profile(hermes_root, "cto", monkeypatch)
    datastore._verified_schemas.clear()
    _, back_source = await _resolve(postgres_dsn, handle="111")
    back = _session_store(back_source)
    back_memory = back.format_for_system_prompt("memory") or ""
    assert "release branch" in back_memory
    assert "VAT" not in back_memory
    assert "CI pipeline" not in back_memory
    assert "ai-prentice-4-all" in (back.format_for_system_prompt("shared") or "")

    # And the engineer sees only their own + shared.
    _, eng_again = await _resolve(postgres_dsn, handle="222")
    eng_store = _session_store(eng_again)
    eng_memory = eng_store.format_for_system_prompt("memory") or ""
    assert "CI pipeline" in eng_memory
    assert "release branch" not in eng_memory
    assert "ai-prentice-4-all" in (eng_store.format_for_system_prompt("shared") or "")


@pytest.mark.asyncio
async def test_shared_write_authority_follows_the_role_in_the_database(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _use_profile(hermes_root, "school", monkeypatch)
    await _enroll(postgres_dsn, "u_head", handle="900", role="owner")
    await _enroll(postgres_dsn, "u_deputy", handle="901", role="admin")
    await _enroll(postgres_dsn, "u_teacher", handle="902", role="member")
    await _enroll(postgres_dsn, "u_parent", handle="903", role="viewer")

    outcomes: dict[str, bool] = {}
    for handle, user_id in (("900", "u_head"), ("901", "u_deputy"),
                            ("902", "u_teacher"), ("903", "u_parent")):
        _, source = await _resolve(postgres_dsn, handle=handle)
        assert source.internal_user_id == user_id
        store = _session_store(source)
        outcomes[user_id] = store.add("shared", f"{user_id} wrote this")["success"]

    assert outcomes == {
        "u_head": True, "u_deputy": True, "u_teacher": False, "u_parent": False,
    }
    shared = (home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert "u_head wrote this" in shared and "u_deputy wrote this" in shared
    assert "u_teacher" not in shared and "u_parent" not in shared

    # The refusals are audited (C5-shaped rows), one per denied attempt.
    from tools.memory_tool import MEMORY_AUDIT_LOG

    log = (home / "audit" / MEMORY_AUDIT_LOG).read_text(encoding="utf-8").splitlines()
    assert len(log) == 2
    assert "u_teacher" in log[0] and "u_parent" in log[1]


@pytest.mark.asyncio
async def test_unresolved_identity_gets_no_principal_scope(
    postgres_dsn: str, hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unenrolled sender resolves to nothing, so it can read no one's memory."""
    home = _use_profile(hermes_root, "school", monkeypatch)
    await _enroll(postgres_dsn, "u_head", handle="900", role="owner")
    _, owner_source = await _resolve(postgres_dsn, handle="900")
    _session_store(owner_source).add("memory", "the head's private working note")

    principal, stranger = await _resolve(postgres_dsn, handle="666")
    assert principal is None and stranger.internal_user_id is None

    store = _session_store(stranger)
    assert store.user_id is None
    block = store.format_for_system_prompt("memory") or ""
    assert "private working note" not in block
    # Unscoped => the profile's own (pre-FG-24) files, and no shared target.
    assert store._path_for("memory") == home / "memories" / "MEMORY.md"
    assert store.add("shared", "x")["success"] is False
