"""Real-Postgres E2E for the FG-22 memory explorer dashboard (V1–V4).

Tests the HTTP endpoints in ``hermes_cli/memory_explorer.py`` against a
throwaway pgvector container, covering:

* **V1** — ``/summary`` + ``/rows`` (counts, RLS, empty schema, ``?as=``)
* **V2** — ``/projection`` (RLS, staleness, idempotent PCA fit, mixed-model)
* **V3** — ``POST /projection/query`` (no-persist, rate limit, PCA fallback)
* **V4** — ``/documents`` + ``/rows?kind=chunk`` (RLS, chunk rows on map)

The fixture shape mirrors ``test_memory_vector_space_e2e.py``: a throwaway
pgvector container, a ``HashingEmbedder`` (no model download), and direct
SQL resets between tests.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import List

import asyncpg
import pytest

from hermes_cli.access import ACCESS_SCHEMA_SQL, Principal
from hermes_cli.datastore import get_store
from plugins.memory.supabase_pgvector.embedding import HashingEmbedder
from plugins.memory.supabase_pgvector.rag import RagStore
from plugins.memory.supabase_pgvector.store import PgvectorMemoryStore

_PGVECTOR_IMAGE = (
    "pgvector/pgvector@sha256:"
    "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)

ALICE = Principal(user_id="alice", display="Alice", role="member")
BOB = Principal(user_id="bob", display="Bob", role="member")
OWNER = Principal(user_id="root", display="Root Owner", role="owner")
_UNENROLLED = Principal(user_id="stranger", display="Stranger", role="member")


# ---------------------------------------------------------------------------
# Throwaway pgvector container (module-scoped)
# ---------------------------------------------------------------------------

async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the memory explorer E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the memory explorer E2E test")

    subprocess.run(["docker", "pull", _PGVECTOR_IMAGE], check=True, capture_output=True)
    container = f"hermes-fg22-{uuid.uuid4().hex[:12]}"
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_dev CASCADE")
    finally:
        await conn.close()


def _make_store(dsn: str, embedder=None) -> PgvectorMemoryStore:
    if embedder is None:
        embedder = HashingEmbedder(dim=256)
    return PgvectorMemoryStore(
        get_store("supabase-app", "dev", config=_config(dsn)),
        embedder=embedder,
    )


async def _enroll_principals(store: PgvectorMemoryStore) -> None:
    """Insert test principals so RLS role lookups resolve."""
    conn = await store.connect()
    try:
        await conn.execute(ACCESS_SCHEMA_SQL)
        for p in (OWNER, ALICE, BOB):
            await conn.execute(
                "INSERT INTO principals (user_id, display, role) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (user_id) DO UPDATE SET display = EXCLUDED.display",
                p.user_id, p.display, p.role,
            )
    finally:
        await conn.close()


def _patch_store(monkeypatch, store: PgvectorMemoryStore) -> None:
    """Patch ``memory_explorer._memory_store`` to return the test store."""
    from hermes_cli import memory_explorer
    monkeypatch.setattr(memory_explorer, "_memory_store", lambda mode="prod": store)


def _make_client(monkeypatch, principal: Principal):
    """Create a TestClient whose requests resolve to ``principal``."""
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        from starlette.testclient import TestClient

    from hermes_cli import web_server

    async def _fake_resolve(request, *, allow_as=False):
        return principal

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_resolve)

    c = TestClient(web_server.app)
    c.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return c


def _patch_projection_store(monkeypatch, store: PgvectorMemoryStore) -> None:
    """Patch ``memory_projection._store`` to return the test store."""
    from hermes_cli import memory_projection
    monkeypatch.setattr(memory_projection, "_store", lambda mode=None: store)


# ---------------------------------------------------------------------------
# V1: Summary + Rows
# ---------------------------------------------------------------------------

class TestV1SummaryRows:
    """V1 — ``/summary`` and ``/rows`` endpoint contracts."""

    @pytest.mark.asyncio
    async def test_summary_on_uninitialized_schema_returns_zeros(
        self, postgres_dsn, monkeypatch
    ):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await _enroll_principals(store)

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        resp = client.get("/api/memory/explorer/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["totals"]["memories"] == 0
        assert data["totals"]["documents"] == 0
        assert data["totals"]["chunks"] == 0
        assert data["by_owner"] == {}
        assert data["by_topic"] == {}

    @pytest.mark.asyncio
    async def test_summary_counts_match_rows(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "alice fact about tenders", topic="work")
        await store.write(ALICE, "alice shared note", visibility="shared")
        await store.write(BOB, "bob private fact", visibility="private")

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        summary = client.get("/api/memory/explorer/summary").json()
        rows = client.get("/api/memory/explorer/rows?limit=200").json()

        # Alice sees her 2 private + 1 shared from herself + 0 from Bob.
        assert summary["totals"]["memories"] == rows["total"]
        # Bob's private memory is not visible to Alice.
        assert all(r["owner_user_id"] != "bob" for r in rows["rows"])

    @pytest.mark.asyncio
    async def test_member_rows_exclude_other_member_private(
        self, postgres_dsn, monkeypatch
    ):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "alice secret", visibility="private")
        await store.write(BOB, "bob secret", visibility="private")

        _patch_store(monkeypatch, store)
        alice_client = _make_client(monkeypatch, ALICE)

        rows = alice_client.get("/api/memory/explorer/rows").json()
        assert all(r["owner_user_id"] != "bob" for r in rows["rows"])
        assert rows["total"] >= 1

    @pytest.mark.asyncio
    async def test_owner_sees_all_via_elevated_reads(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "alice secret", visibility="private")

        _patch_store(monkeypatch, store)
        owner_client = _make_client(monkeypatch, OWNER)

        rows = owner_client.get("/api/memory/explorer/rows").json()
        alice_rows = [r for r in rows["rows"] if r["owner_user_id"] == "alice"]
        assert len(alice_rows) == 1
        assert alice_rows[0]["elevated"] is True


# ---------------------------------------------------------------------------
# V2: Projection map
# ---------------------------------------------------------------------------

class TestV2Projection:
    """V2 — ``/projection`` endpoint, PCA fit, RLS, staleness."""

    @pytest.mark.asyncio
    async def test_projection_on_uninitialized_returns_empty(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await _enroll_principals(store)

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        resp = client.get("/api/memory/explorer/projection")
        assert resp.status_code == 200
        data = resp.json()
        assert data["algorithm"] is None
        assert data["points"] == []
        assert data["stale"] is True

    @pytest.mark.asyncio
    async def test_projection_points_obey_rls(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "alice private memory", visibility="private")
        await store.write(BOB, "bob private memory", visibility="private")

        # Fit the projection (operator-only, bypasses RLS).
        _patch_projection_store(monkeypatch, store)
        from hermes_cli.memory_projection import cmd_projection_fit
        cmd_projection_fit(SimpleNamespace(mode="dev", algorithm="pca", sample=20000))

        _patch_store(monkeypatch, store)

        alice_client = _make_client(monkeypatch, ALICE)
        alice_proj = alice_client.get("/api/memory/explorer/projection").json()
        assert all(
            p["owner_user_id"] != "bob" for p in alice_proj["points"]
        ), "Alice must not see Bob's projection points"

        bob_client = _make_client(monkeypatch, BOB)
        bob_proj = bob_client.get("/api/memory/explorer/projection").json()
        assert all(
            p["owner_user_id"] != "alice" for p in bob_proj["points"]
        ), "Bob must not see Alice's projection points"

    @pytest.mark.asyncio
    async def test_stale_after_post_fit_write(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "first memory")
        _patch_projection_store(monkeypatch, store)
        from hermes_cli.memory_projection import cmd_projection_fit
        cmd_projection_fit(SimpleNamespace(mode="dev", algorithm="pca", sample=20000))

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)
        proj = client.get("/api/memory/explorer/projection").json()
        assert proj["stale"] is False

        # Write a new memory after the fit.
        await store.write(ALICE, "second memory after fit")
        proj2 = client.get("/api/memory/explorer/projection").json()
        assert proj2["stale"] is True
        assert proj2["unprojected_count"] >= 1

    @pytest.mark.asyncio
    async def test_fit_is_idempotent(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "memory one")
        await store.write(ALICE, "memory two")

        _patch_projection_store(monkeypatch, store)
        from hermes_cli.memory_projection import cmd_projection_fit

        args = SimpleNamespace(mode="dev", algorithm="pca", sample=20000)
        cmd_projection_fit(args)
        cmd_projection_fit(args)  # second fit must not error

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)
        proj = client.get("/api/memory/explorer/projection").json()
        assert len(proj["points"]) == 2
        assert proj["algorithm"] == "pca"


# ---------------------------------------------------------------------------
# V3: Query placement
# ---------------------------------------------------------------------------

class TestV3QueryPlacement:
    """V3 — ``POST /projection/query`` contracts."""

    @pytest.mark.asyncio
    async def test_query_persists_nothing(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "the tender closes on 14 March")

        _patch_projection_store(monkeypatch, store)
        from hermes_cli.memory_projection import cmd_projection_fit
        cmd_projection_fit(SimpleNamespace(mode="dev", algorithm="pca", sample=20000))

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        # Count rows before query.
        rows_before = client.get("/api/memory/explorer/rows").json()
        count_before = rows_before["total"]

        # Place a query.
        resp = client.post(
            "/api/memory/explorer/projection/query",
            json={"text": "when does the tender close"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert "x" in result
        assert "y" in result
        assert "nearest" in result

        # Row count must be unchanged — the query endpoint never persists.
        rows_after = client.get("/api/memory/explorer/rows").json()
        assert rows_after["total"] == count_before

    @pytest.mark.asyncio
    async def test_rate_limit_enforced(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "a memory to project")

        _patch_projection_store(monkeypatch, store)
        from hermes_cli.memory_projection import cmd_projection_fit
        cmd_projection_fit(SimpleNamespace(mode="dev", algorithm="pca", sample=20000))

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        # First request succeeds.
        r1 = client.post(
            "/api/memory/explorer/projection/query",
            json={"text": "first query"},
        )
        assert r1.status_code == 200

        # Second request within 3 seconds is rate-limited.
        r2 = client.post(
            "/api/memory/explorer/projection/query",
            json={"text": "second query"},
        )
        assert r2.status_code == 429

    @pytest.mark.asyncio
    async def test_pca_works_without_umap(self, postgres_dsn, monkeypatch):
        """PCA is the default and requires no extra dependencies."""
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        await store.write(ALICE, "memory for pca projection")

        _patch_projection_store(monkeypatch, store)
        from hermes_cli.memory_projection import cmd_projection_fit
        cmd_projection_fit(SimpleNamespace(mode="dev", algorithm="pca", sample=20000))

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        proj = client.get("/api/memory/explorer/projection").json()
        assert proj["algorithm"] == "pca"
        assert len(proj["points"]) == 1

        # Query placement returns coordinates (not degraded).
        q = client.post(
            "/api/memory/explorer/projection/query",
            json={"text": "memory for pca projection"},
        ).json()
        assert q["x"] is not None
        assert q["y"] is not None


# ---------------------------------------------------------------------------
# V4: RAG chunks + documents
# ---------------------------------------------------------------------------

class TestV4RagChunks:
    """V4 — ``/documents``, ``/rows?kind=chunk``, chunk points on the map."""

    @pytest.mark.asyncio
    async def test_documents_returns_only_visible(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        rag = RagStore(store)
        await rag.initialize()

        await rag.ingest(
            ALICE, source_kind="file", source_ref="alice_doc.txt",
            title="Alice's Document", text="This is Alice's document text.",
        )
        await rag.ingest(
            BOB, source_kind="file", source_ref="bob_doc.txt",
            title="Bob's Document", text="This is Bob's document text.",
        )

        _patch_store(monkeypatch, store)
        alice_client = _make_client(monkeypatch, ALICE)

        resp = alice_client.get("/api/memory/explorer/documents")
        assert resp.status_code == 200
        data = resp.json()
        # Alice sees only her own document (private by default).
        assert all(d["owner_user_id"] != "bob" for d in data["documents"])
        assert any(d["title"] == "Alice's Document" for d in data["documents"])

    @pytest.mark.asyncio
    async def test_rows_kind_chunk_returns_chunk_fields(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        rag = RagStore(store)
        await rag.initialize()

        await rag.ingest(
            ALICE, source_kind="file", source_ref="spec.txt",
            title="Tender Spec", text="The tender closes on 14 March 2026.",
        )

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        resp = client.get("/api/memory/explorer/rows?kind=chunk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        row = data["rows"][0]
        assert row["kind"] == "chunk"
        assert "document_id" in row
        assert "document_title" in row
        assert "ordinal" in row
        assert row["document_title"] == "Tender Spec"

    @pytest.mark.asyncio
    async def test_chunk_rows_obey_rls(self, postgres_dsn, monkeypatch):
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        rag = RagStore(store)
        await rag.initialize()

        await rag.ingest(
            ALICE, source_kind="file", source_ref="alice_secret.txt",
            title="Alice Secret", text="Alice's private document content.",
        )
        await rag.ingest(
            BOB, source_kind="file", source_ref="bob_secret.txt",
            title="Bob Secret", text="Bob's private document content.",
        )

        _patch_store(monkeypatch, store)
        alice_client = _make_client(monkeypatch, ALICE)

        rows = alice_client.get(
            "/api/memory/explorer/rows?kind=chunk"
        ).json()
        assert all(r["owner_user_id"] != "bob" for r in rows["rows"])

    @pytest.mark.asyncio
    async def test_chunks_on_projection_map(self, postgres_dsn, monkeypatch):
        """After fit, chunk points appear on the projection with kind='chunk'."""
        await _reset(postgres_dsn)
        store = _make_store(postgres_dsn)
        await store.initialize()
        await _enroll_principals(store)

        rag = RagStore(store)
        await rag.initialize()

        await store.write(ALICE, "a memory to anchor the projection")
        await rag.ingest(
            ALICE, source_kind="file", source_ref="doc.txt",
            title="Test Doc", text="Chunk text for the projection.",
        )

        _patch_projection_store(monkeypatch, store)
        from hermes_cli.memory_projection import cmd_projection_fit
        cmd_projection_fit(SimpleNamespace(mode="dev", algorithm="pca", sample=20000))

        _patch_store(monkeypatch, store)
        client = _make_client(monkeypatch, ALICE)

        proj = client.get("/api/memory/explorer/projection").json()
        kinds = {p["kind"] for p in proj["points"]}
        assert "chunk" in kinds, "Chunk points must appear on the projection"
        assert "memory" in kinds, "Memory points must also be present"
