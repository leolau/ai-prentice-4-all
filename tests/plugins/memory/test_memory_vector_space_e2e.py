"""Real-Postgres E2E for the P2 embedding-space rules.

Switching ``memory.embedding`` is a data migration, not a config change, and its
failure mode is invisible: a column holding two models' vectors returns
plausible rows in a meaningless order and looks perfectly healthy. So the
guarantees here are all about what happens at the seam — provenance stamped per
row, a mismatch that shouts instead of ranking nonsense, a re-embed that is
all-or-nothing, and recall that refuses to compare across spaces.

Runs against a throwaway pgvector Postgres (same image and fixture shape as
``test_supabase_pgvector_e2e.py``) because none of this is observable without a
real ``vector(N)`` column: the width lives in ``atttypmod``, the HNSW index has
to survive a column replacement, and the transaction boundary is the whole
point.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import List

import asyncpg
import pytest

from hermes_cli.access import Principal
from hermes_cli.datastore import StoreMode, get_store
from plugins.memory.supabase_pgvector.embedding import HashingEmbedder
from plugins.memory.supabase_pgvector.store import (
    MEMORY_TABLE,
    EmbeddingSpaceMismatch,
    PgvectorMemoryStore,
)

_PGVECTOR_IMAGE = (
    "pgvector/pgvector@sha256:"
    "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


class _StubSemanticEmbedder:
    """A second, deliberately *different* embedding space.

    Not a semantic model — a real one would make the test depend on a 2 GB
    download and a CPU budget. What matters for these invariants is only that
    it is a different ``model_id`` at a different ``dim``, which is exactly the
    condition a bge-m3 cutover creates.
    """

    def __init__(self, *, dim: int = 8, model_id: str = "stub/semantic-8") -> None:
        self._dim = dim
        self._model_id = model_id

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, text: str) -> List[float]:
        vector = [0.0] * self._dim
        for index, word in enumerate(str(text).lower().split()):
            vector[(len(word) + index) % self._dim] += 1.0
        norm = sum(component * component for component in vector) ** 0.5
        if norm > 0.0:
            vector = [component / norm for component in vector]
        return vector

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(text) for text in texts]


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

    subprocess.run(["docker", "pull", _PGVECTOR_IMAGE], check=True, capture_output=True)
    container = f"hermes-p2-{uuid.uuid4().hex[:12]}"
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


def _store(dsn: str, embedder, mode: StoreMode = "dev") -> PgvectorMemoryStore:
    return PgvectorMemoryStore(
        get_store("supabase-app", mode, config=_config(dsn)), embedder=embedder
    )


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_dev CASCADE")
    finally:
        await conn.close()


ALICE = Principal(user_id="alice", display="Alice", role="member")


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_row_records_the_model_that_embedded_it(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()
    await store.write(ALICE, "the tender closes on 14 March")

    space = await store.describe_space()
    assert space.column_dim == 256
    assert space.rows_by_model == {"hashing": 1}
    assert space.rows_outside("hashing") == 0


@pytest.mark.asyncio
async def test_rows_written_before_provenance_existed_are_labelled_hashing(
    postgres_dsn: str,
) -> None:
    """The pre-P2 column had no model. Its rows *are* hashing rows.

    Simulated by dropping the column and re-initializing, which is the shape of
    an existing deployment: the backfill must claim what is true, not guess.
    """
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()
    await store.write(ALICE, "a memory from before the migration")

    conn = await store._connect()
    try:
        await conn.execute(
            f"ALTER TABLE {MEMORY_TABLE} DROP COLUMN embedding_model"
        )
    finally:
        await conn.close()

    await store.initialize()  # idempotent re-init performs the backfill
    space = await store.describe_space()
    assert space.rows_by_model == {"hashing": 1}


# ---------------------------------------------------------------------------
# Mismatch fails loudly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_switching_model_without_reembedding_refuses_to_start(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    hashing = _store(postgres_dsn, HashingEmbedder(dim=256))
    await hashing.initialize()
    await hashing.write(ALICE, "the tender closes on 14 March")

    semantic = _store(postgres_dsn, _StubSemanticEmbedder(dim=8))
    with pytest.raises(EmbeddingSpaceMismatch) as raised:
        await semantic.initialize()

    message = str(raised.value)
    # The message has to name the remedy: an operator reading it at 2am cannot
    # infer "re-embed" from "expected 256 dimensions, got 8".
    assert "reembed" in message
    assert "vector(256)" in message and "8" in message


@pytest.mark.asyncio
async def test_recall_excludes_rows_from_another_model(postgres_dsn: str) -> None:
    """Cross-space rows are omitted, not ranked.

    Their cosine distance is a real number and a meaningless one, so ranking
    them mixes genuine matches with noise indistinguishable from a match.
    """
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=8))
    await store.initialize()
    await store.write(ALICE, "hashing era memory about tenders")

    other = _store(postgres_dsn, _StubSemanticEmbedder(dim=8, model_id="other/model-8"))
    # Same width, different model: the column accepts both, so nothing but the
    # provenance filter stands between the two spaces.
    await other.initialize()
    await other.write(ALICE, "semantic era memory about tenders")

    hashing_hits = await store.query(ALICE, "tenders", top_k=50)
    assert [row.text for row in hashing_hits] == ["hashing era memory about tenders"]

    other_hits = await other.query(ALICE, "tenders", top_k=50)
    assert [row.text for row in other_hits] == ["semantic era memory about tenders"]


# ---------------------------------------------------------------------------
# Re-embedding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reembed_migrates_every_row_into_the_new_space(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    hashing = _store(postgres_dsn, HashingEmbedder(dim=256))
    await hashing.initialize()
    texts = [
        "the tender closes on 14 March",
        "invoice 4417 was paid in full",
        "招標截止日期是三月十四日",
    ]
    for text in texts:
        await hashing.write(ALICE, text, topic="tenders")

    semantic = _store(postgres_dsn, _StubSemanticEmbedder(dim=8))
    migrated = await semantic.reembed()
    assert migrated == len(texts)

    space = await semantic.describe_space()
    assert space.column_dim == 8
    assert space.rows_by_model == {"stub/semantic-8": len(texts)}

    # The rows themselves survive verbatim — a migration that loses text is a
    # data loss dressed as an upgrade.
    hits = await semantic.query(ALICE, "the tender closes on 14 March", top_k=50)
    assert {row.text for row in hits} == set(texts)
    # And the store now starts cleanly against the migrated column.
    await semantic.initialize()


@pytest.mark.asyncio
async def test_reembed_rebuilds_the_vector_index(postgres_dsn: str) -> None:
    """Replacing the column drops its index; recall must not silently seq-scan."""
    await _reset(postgres_dsn)
    hashing = _store(postgres_dsn, HashingEmbedder(dim=256))
    await hashing.initialize()
    await hashing.write(ALICE, "a memory to migrate")

    semantic = _store(postgres_dsn, _StubSemanticEmbedder(dim=8))
    await semantic.reembed()

    conn = await asyncpg.connect(postgres_dsn, ssl=False)
    try:
        index = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'app_dev' AND indexname = $1",
            f"{MEMORY_TABLE}_embedding_idx",
        )
    finally:
        await conn.close()
    assert index is not None and "hnsw" in index.lower()


@pytest.mark.asyncio
async def test_reembed_on_an_empty_store_still_moves_the_column(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    hashing = _store(postgres_dsn, HashingEmbedder(dim=256))
    await hashing.initialize()

    semantic = _store(postgres_dsn, _StubSemanticEmbedder(dim=8))
    assert await semantic.reembed() == 0

    space = await semantic.describe_space()
    assert space.column_dim == 8
    await semantic.initialize()  # no mismatch afterwards


@pytest.mark.asyncio
async def test_a_failed_reembed_leaves_the_old_space_intact(
    postgres_dsn: str,
) -> None:
    """All-or-nothing: a half-migrated column is the state P2 exists to prevent."""
    await _reset(postgres_dsn)
    hashing = _store(postgres_dsn, HashingEmbedder(dim=256))
    await hashing.initialize()
    await hashing.write(ALICE, "the tender closes on 14 March")

    class _BrokenEmbedder(_StubSemanticEmbedder):
        def embed_batch(self, texts: List[str]) -> List[List[float]]:
            raise RuntimeError("embedding service went away mid-migration")

    broken = _store(postgres_dsn, _BrokenEmbedder(dim=8))
    with pytest.raises(RuntimeError):
        await broken.reembed()

    # Nothing moved: the column, the rows and their provenance are as they were,
    # so the old configuration still works and the migration can be retried.
    space = await hashing.describe_space()
    assert space.column_dim == 256
    assert space.rows_by_model == {"hashing": 1}
    hits = await hashing.query(ALICE, "the tender closes on 14 March")
    assert hits and hits[0].text == "the tender closes on 14 March"


# ---------------------------------------------------------------------------
# Usage tracking, thresholds, dedup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_automatic_recall_counts_a_use_and_a_manual_query_does_not(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()
    written = await store.write(ALICE, "the tender closes on 14 March")

    async def usage() -> tuple[int, object]:
        conn = await store._connect()
        try:
            row = await conn.fetchrow(
                f"SELECT uses, last_used FROM {MEMORY_TABLE} WHERE id = $1",
                uuid.UUID(written.id),
            )
        finally:
            await conn.close()
        return int(row["uses"]), row["last_used"]

    assert await usage() == (0, None)

    await store.query(ALICE, "when does the tender close")
    uses, last_used = await usage()
    assert (uses, last_used) == (0, None), "a manual search is not a use"

    await store.query(ALICE, "when does the tender close", record_use=True)
    uses, last_used = await usage()
    assert uses == 1 and last_used is not None


@pytest.mark.asyncio
async def test_min_score_drops_weak_neighbours(postgres_dsn: str) -> None:
    """HNSW always returns top_k rows; without a floor, noise is always recalled."""
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()
    await store.write(ALICE, "invoice 4417 was paid in full")

    assert await store.query(ALICE, "invoice 4417 paid", min_score=0.3)
    # Unrelated text still scores above zero on an incidental shared token
    # ("in"), which is precisely why an unfiltered recall is never empty.
    weak = await store.query(ALICE, "photosynthesis in alpine moss")
    assert weak and weak[0].score is not None and 0.0 < weak[0].score < 0.3
    assert await store.query(
        ALICE, "photosynthesis in alpine moss", min_score=0.3
    ) == []


@pytest.mark.asyncio
async def test_duplicate_writes_collapse_onto_the_existing_row(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()

    first = await store.write(ALICE, "the tender closes on 14 March", dedup_threshold=0.97)
    again = await store.write(ALICE, "the tender closes on 14 March", dedup_threshold=0.97)
    assert again.id == first.id

    reordered = await store.write(
        ALICE, "on 14 March the tender closes", dedup_threshold=0.97
    )
    # Same tokens, so the hashing vector is identical — one fact, one row.
    assert reordered.id == first.id

    unrelated = await store.write(
        ALICE, "invoice 4417 was paid in full", dedup_threshold=0.97
    )
    assert unrelated.id != first.id

    space = await store.describe_space()
    assert space.rows_by_model == {"hashing": 2}


@pytest.mark.asyncio
async def test_writers_that_count_repeats_are_not_deduplicated(
    postgres_dsn: str,
) -> None:
    """Dedup is opt-in because repetition is data for some writers.

    Task discovery decides a standing request is a task by counting how often
    the same intent recurs. Collapsing those rows would keep the count at one
    forever, so a request the user made ten times would never be proposed —
    a feature quietly disabled by an unrelated improvement.
    """
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()

    for _ in range(3):
        await store.write(
            ALICE, "send the weekly status report", kind="intent_signal"
        )

    space = await store.describe_space()
    assert space.rows_by_model == {"hashing": 3}


@pytest.mark.asyncio
async def test_dedup_does_not_swallow_distinct_zero_vector_memories(
    postgres_dsn: str,
) -> None:
    """The hashing embedder maps any Chinese sentence to the zero vector.

    Its cosine distance to every row is undefined; treating that as "identical
    to everything" would keep the first such memory and silently discard the
    rest — the exact class of invisible loss this phase is about.
    """
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()

    first = await store.write(ALICE, "招標截止日期是三月十四日", dedup_threshold=0.97)
    second = await store.write(ALICE, "發票已經全額付款", dedup_threshold=0.97)

    assert second.id != first.id
    space = await store.describe_space()
    assert space.rows_by_model == {"hashing": 2}


@pytest.mark.asyncio
async def test_dedup_is_per_owner_not_global(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = _store(postgres_dsn, HashingEmbedder(dim=256))
    await store.initialize()
    bob = Principal(user_id="bob", display="Bob", role="member")

    alice_row = await store.write(
        ALICE, "the tender closes on 14 March", dedup_threshold=0.97
    )
    bob_row = await store.write(
        bob, "the tender closes on 14 March", dedup_threshold=0.97
    )

    # Two people knowing the same thing is two memories; collapsing them would
    # hand one user's row to another.
    assert bob_row.id != alice_row.id
    assert bob_row.owner_user_id == "bob"
