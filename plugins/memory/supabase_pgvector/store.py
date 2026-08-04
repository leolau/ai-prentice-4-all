"""Live queryable memory tier backed by Supabase Postgres + pgvector.

This is the *live* half of the D2 hybrid memory model. Where the curated tier
(``tools/memory_tool.py``) is a frozen ``MEMORY.md``/``USER.md`` snapshot loaded
once at session start, this store is read and written **mid-turn via tool
calls** — never spliced into the system prompt — so a fact learned this turn is
recallable immediately without disturbing the cached prompt prefix.

Every row is visibility-scoped by contract **C2**
(:mod:`hermes_cli.access`): it carries ``owner_user_id`` + ``visibility``
(``shared`` or ``private:<user_id>``), reads are filtered by
:func:`~hermes_cli.access.scope_filter`, and Postgres **row-level security**
(:func:`~hermes_cli.access.apply_scope_rls`) is the database-level backstop.
All connections are obtained through contract **C3**
(:class:`hermes_cli.datastore.SupabaseAppStore`), so the ``app_dev`` / ``app_prod``
schema follows the resolved mode. Concurrency across many ``(user, task)``
sessions rides Postgres MVCC — each write is its own transaction on its own
connection, so there is no single-writer bottleneck.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from hermes_cli.access import (
    Principal,
    SHARED,
    apply_scope_rls,
    normalize_visibility,
    scope_filter,
)

from .embedding import DEFAULT_DIM, HASHING_MODEL_ID, Embedder, get_embedder

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore


#: Name of the scoped table holding live memory rows (RLS applied to it).
MEMORY_TABLE = "memories"


def _schema_sql(dim: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS {MEMORY_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    text TEXT NOT NULL,
    embedding vector({dim}) NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT '{HASHING_MODEL_ID}',
    source_session TEXT,
    topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used TIMESTAMPTZ,
    uses INTEGER NOT NULL DEFAULT 0
);

-- Rows written before provenance existed were produced by the hashing
-- embedder, which is exactly what the column default states. Backfilling them
-- as 'hashing' is a statement of fact, not a guess.
ALTER TABLE {MEMORY_TABLE}
    ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL
    DEFAULT '{HASHING_MODEL_ID}';

CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_embedding_idx
    ON {MEMORY_TABLE} USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_visibility_idx
    ON {MEMORY_TABLE} (visibility);
CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_topic_idx
    ON {MEMORY_TABLE} (topic);
CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_embedding_model_idx
    ON {MEMORY_TABLE} (embedding_model);
"""


class EmbeddingSpaceMismatch(RuntimeError):
    """The stored vectors were not produced by the configured embedder.

    Raised instead of writing or querying, because both would be wrong in a
    way that leaves no trace: an insert of the wrong width fails with a
    Postgres type error nobody can act on, and a query ranks the new model's
    vector against the old model's column, returning plausible-looking rows in
    a meaningless order. The message names the fix (``hermes memory reembed``)
    rather than the symptom.
    """


@dataclass(frozen=True)
class EmbeddingSpace:
    """What is actually in the vector column right now."""

    column_dim: Optional[int]
    rows_by_model: dict

    @property
    def models(self) -> List[str]:
        return sorted(self.rows_by_model)

    def rows_outside(self, model_id: str) -> int:
        return sum(
            count
            for model, count in self.rows_by_model.items()
            if model != model_id
        )


@dataclass(frozen=True)
class MemoryRecord:
    """One row of live memory, optionally with a similarity ``score``."""

    id: str
    owner_user_id: str
    visibility: str
    kind: str
    text: str
    topic: Optional[str]
    source_session: Optional[str]
    created_at: Optional[datetime]
    score: Optional[float] = None

    def as_dict(self) -> dict:
        data = {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "kind": self.kind,
            "text": self.text,
            "topic": self.topic,
            "source_session": self.source_session,
        }
        if self.score is not None:
            data["score"] = round(self.score, 6)
        return data


def _encode_vector(vector: List[float]) -> str:
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


def _decode_vector(text: str) -> List[float]:
    inner = text.strip().lstrip("[").rstrip("]")
    if not inner:
        return []
    return [float(part) for part in inner.split(",")]


def _resolve_visibility(principal: Principal, visibility: Optional[str]) -> str:
    """Map a requested ``shared``/``private`` intent onto a concrete C2 tag.

    ``private`` (without a user) becomes the caller's own
    ``private:<user_id>`` — a principal can only create rows private to
    *itself*. A fully-qualified ``private:<u>`` or ``shared`` is validated and
    passed through.
    """
    if visibility is None or visibility == "private":
        return principal.private_visibility
    return normalize_visibility(visibility)


class PgvectorMemoryStore:
    """Async CRUD + semantic recall over the C2-scoped ``memories`` table.

    The store never opens a raw connection itself — it always routes through
    the injected contract-C3 :class:`SupabaseAppStore`, whose ``mode`` selects
    the ``app_dev`` / ``app_prod`` schema.
    """

    def __init__(
        self,
        store: "SupabaseAppStore",
        *,
        embedder: Optional[Embedder] = None,
        config: Optional[dict] = None,
    ) -> None:
        self._store = store
        # An explicit embedder wins (tests, callers that already resolved one);
        # otherwise `memory.embedding` in config.yaml decides, defaulting to the
        # credential-free hashing embedder.
        self._embedder = embedder or get_embedder(DEFAULT_DIM, config=config)

    @property
    def mode(self) -> str:
        return self._store.mode

    @property
    def dim(self) -> int:
        return self._embedder.dim

    @property
    def model_id(self) -> str:
        """The vector space this store reads and writes."""
        return self._embedder.model_id

    async def _prepare_connection(
        self, connection: "asyncpg.Connection"
    ) -> "asyncpg.Connection":
        """Make ``vector`` usable on ``connection`` (own or caller-injected).

        pgvector may be installed in a schema other than the app schema (a
        standard self-hosted Supabase installs it into ``public``). The
        ``vector`` type, its cast, the ``<=>`` operator, and the ``hnsw``
        ``vector_cosine_ops`` opclass only resolve when that schema is on the
        search path, and the asyncpg codec must be registered against the
        schema the type actually lives in — otherwise the connection fails
        with ``type "vector" does not exist`` / ``operator class ... does not
        exist`` even though the extension is present. This runs on *every*
        connection the store touches — including one handed in by a caller
        such as :class:`GoalManagementService` — so no code path can end up
        with the app schema pinned but the vector schema missing. Idempotent.
        """
        await connection.execute(
            f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"'
        )
        await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # Keep the app schema first so scoped tables/RLS still land there.
        vector_schema = await connection.fetchval(
            """
            SELECT n.nspname
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector'
            """
        ) or self._store.schema
        if vector_schema != self._store.schema:
            await connection.execute(
                "SELECT set_config('search_path', $1, false)",
                f'"{self._store.schema}", "{vector_schema}"',
            )
        await connection.set_type_codec(
            "vector",
            schema=vector_schema,
            encoder=_encode_vector,
            decoder=_decode_vector,
            format="text",
        )
        return connection

    async def _connect(self) -> "asyncpg.Connection":
        connection = await self._store.connect()
        return await self._prepare_connection(connection)

    async def initialize(
        self,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Create the ``memories`` table, vector index, and RLS policy.

        Idempotent — safe to call at the start of every session.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            await conn.execute(_schema_sql(self.dim))
            await apply_scope_rls(conn, MEMORY_TABLE)
            await self._assert_space_usable(conn)
        finally:
            if own:
                await conn.close()

    async def _column_dim(self, conn: "asyncpg.Connection") -> Optional[int]:
        """Width the ``embedding`` column is actually declared with.

        ``CREATE TABLE IF NOT EXISTS`` cannot widen an existing column, so a
        table created under a 256-dim embedder keeps ``vector(256)`` no matter
        what the configuration now says. pgvector encodes the width in
        ``atttypmod``.
        """
        return await conn.fetchval(
            """
            SELECT a.atttypmod
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = $1
              AND a.attname = 'embedding'
              AND n.nspname = current_schema()
            """,
            MEMORY_TABLE,
        )

    async def describe_space(
        self,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> EmbeddingSpace:
        """Report the column width and the row count per embedding model.

        Read with RLS bypassed on purpose: this counts rows to decide whether a
        migration is needed, and answering "how many rows are in the old space"
        with only the caller's own rows would understate the work and leave
        other users' rows stranded in an unqueryable space.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            rows = await conn.fetch(
                f"SELECT embedding_model, COUNT(*) AS n "
                f"FROM {MEMORY_TABLE} GROUP BY embedding_model"
            )
            return EmbeddingSpace(
                column_dim=await self._column_dim(conn),
                rows_by_model={
                    str(row["embedding_model"]): int(row["n"]) for row in rows
                },
            )
        finally:
            if own:
                await conn.close()

    async def _assert_space_usable(self, conn: "asyncpg.Connection") -> None:
        """Fail loudly when the column cannot hold this embedder's vectors.

        Checked once per session at initialize() rather than per write, so the
        provider reports itself unavailable with an actionable message instead
        of every memory_write failing with a Postgres type error.
        """
        column_dim = await self._column_dim(conn)
        if column_dim is None or column_dim <= 0 or column_dim == self.dim:
            return
        raise EmbeddingSpaceMismatch(
            f"{MEMORY_TABLE}.embedding is vector({column_dim}) but the "
            f"configured embedder ({self.model_id}) produces {self.dim} "
            "dimensions. Existing vectors cannot be compared with new ones. "
            "Run 'hermes memory reembed' to rewrite every row into the new "
            "space, or restore the previous memory.embedding settings."
        )

    async def reembed(
        self,
        *,
        batch_size: int = 16,
        progress: Optional[object] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> int:
        """Re-embed every row with the configured embedder. Returns row count.

        The whole rewrite is one transaction: a half-migrated column is the
        state this feature exists to prevent, so either every row is in the new
        space or none is. When the width changes, the column is replaced
        (pgvector cannot alter ``vector(N)`` in place) and the HNSW index is
        rebuilt afterwards, since an index built over the old width would
        otherwise be silently dropped with the column.

        Embedding happens *before* the transaction opens: a model that needs
        300 ms per row would otherwise hold a write transaction open across
        minutes of CPU while live sessions block on it.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            rows = await conn.fetch(
                f"SELECT id, text FROM {MEMORY_TABLE} ORDER BY created_at"
            )
            if not rows:
                await self._replace_vector_column(conn)
                return 0

            ids: List[object] = []
            vectors: List[List[float]] = []
            for start in range(0, len(rows), max(1, batch_size)):
                chunk = rows[start : start + max(1, batch_size)]
                embedded = self._embedder.embed_batch(
                    [str(row["text"]) for row in chunk]
                )
                ids.extend(row["id"] for row in chunk)
                vectors.extend(embedded)
                if callable(progress):
                    progress(len(ids), len(rows))

            async with conn.transaction():
                await self._replace_vector_column(conn)
                await conn.executemany(
                    f"UPDATE {MEMORY_TABLE} "
                    "SET embedding = $2, embedding_model = $3 WHERE id = $1",
                    [
                        (row_id, vector, self.model_id)
                        for row_id, vector in zip(ids, vectors)
                    ],
                )
            return len(ids)
        finally:
            if own:
                await conn.close()

    async def _replace_vector_column(self, conn: "asyncpg.Connection") -> None:
        """Make ``embedding`` a ``vector(self.dim)`` column, index included."""
        column_dim = await self._column_dim(conn)
        if column_dim == self.dim:
            return
        await conn.execute(
            f"ALTER TABLE {MEMORY_TABLE} DROP COLUMN embedding"
        )
        await conn.execute(
            f"ALTER TABLE {MEMORY_TABLE} "
            f"ADD COLUMN embedding vector({self.dim})"
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_embedding_idx "
            f"ON {MEMORY_TABLE} USING hnsw (embedding vector_cosine_ops)"
        )

    async def write(
        self,
        principal: Principal,
        text: str,
        *,
        kind: str = "fact",
        topic: Optional[str] = None,
        visibility: Optional[str] = None,
        source_session: Optional[str] = None,
        dedup_threshold: float = 0.0,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> MemoryRecord:
        """Persist one memory row owned by ``principal`` and return it.

        ``visibility`` defaults to the caller's own ``private:<user_id>`` tier;
        pass ``"shared"`` to write org-visible knowledge. The row's embedding is
        computed from ``text`` via the configured embedder.

        With ``dedup_threshold`` above 0, a near-identical memory the caller
        already owns is returned instead of inserting another copy. Automatic
        capture re-states the same fact every time the user rephrases a standing
        request, and a store full of paraphrases makes recall return five
        versions of one thing instead of five things.
        """
        clean = (text or "").strip()
        if not clean:
            raise ValueError("Cannot write empty memory text")
        resolved_visibility = _resolve_visibility(principal, visibility)
        embedding = self._embedder.embed(clean)

        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            duplicate = await self._find_duplicate(
                conn,
                principal=principal,
                visibility=resolved_visibility,
                text=clean,
                embedding=embedding,
                threshold=float(dedup_threshold),
            )
            if duplicate is not None:
                return duplicate
            row = await conn.fetchrow(
                f"""
                INSERT INTO {MEMORY_TABLE}
                    (owner_user_id, visibility, kind, text, embedding,
                     embedding_model, topic, source_session)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, owner_user_id, visibility, kind, text, topic,
                          source_session, created_at
                """,
                principal.user_id,
                resolved_visibility,
                kind,
                clean,
                embedding,
                self.model_id,
                topic,
                source_session,
            )
            return _row_to_record(row)
        finally:
            if own:
                await conn.close()

    async def _find_duplicate(
        self,
        conn: "asyncpg.Connection",
        *,
        principal: Principal,
        visibility: str,
        text: str,
        embedding: List[float],
        threshold: float,
    ) -> Optional[MemoryRecord]:
        """Return the caller's existing near-identical memory, if any.

        Dedup is opt-in per write. Some writers *need* repeats: task discovery
        counts how many times an intent recurs before proposing a task, so
        collapsing identical rows would make a standing request never cross its
        threshold. Only callers that state a threshold get dedup.

        Within those callers, exact text is deduplicated first — it is the same
        fact by definition, and it costs one indexed comparison. Vector
        similarity is only consulted for a non-degenerate embedding: the
        hashing embedder maps
        token-less text (any Chinese sentence) to the zero vector, whose cosine
        distance to every row is undefined, and treating that as "identical to
        everything" would silently discard every such memory after the first.
        """
        if threshold <= 0.0:
            return None
        exact = await conn.fetchrow(
            f"""
            SELECT id, owner_user_id, visibility, kind, text, topic,
                   source_session, created_at, NULL::float AS score
            FROM {MEMORY_TABLE}
            WHERE owner_user_id = $1 AND visibility = $2 AND text = $3
            LIMIT 1
            """,
            principal.user_id,
            visibility,
            text,
        )
        if exact is not None:
            return _row_to_record(exact)
        if not any(component != 0.0 for component in embedding):
            return None
        near = await conn.fetchrow(
            f"""
            SELECT id, owner_user_id, visibility, kind, text, topic,
                   source_session, created_at,
                   1 - (embedding <=> $4) AS score
            FROM {MEMORY_TABLE}
            WHERE owner_user_id = $1 AND visibility = $2
              AND embedding_model = $3
            ORDER BY embedding <=> $4
            LIMIT 1
            """,
            principal.user_id,
            visibility,
            self.model_id,
            embedding,
        )
        if near is None:
            return None
        record = _row_to_record(near)
        if record.score is not None and record.score >= threshold:
            return record
        return None

    async def get(
        self,
        principal: Principal,
        memory_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[MemoryRecord]:
        """Return one memory when ``principal`` may read it (contract C2)."""
        predicate = scope_filter(principal, start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            row = await conn.fetchrow(
                f"""
                SELECT id, owner_user_id, visibility, kind, text, topic,
                       source_session, created_at, NULL::float AS score
                FROM {MEMORY_TABLE}
                WHERE id = $1 AND {predicate.sql}
                """,
                memory_id,
                *predicate.params,
            )
            return _row_to_record(row) if row is not None else None
        finally:
            if own:
                await conn.close()

    async def query(
        self,
        principal: Principal,
        query_text: str,
        *,
        top_k: int = 10,
        kind: Optional[str] = None,
        topic: Optional[str] = None,
        min_score: float = 0.0,
        record_use: bool = False,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[MemoryRecord]:
        """Return the ``top_k`` rows most similar to ``query_text``.

        Results are scoped to what ``principal`` may read (contract C2): a
        non-owner sees ``shared`` rows plus its own ``private`` rows; the owner
        sees everything. Ranking is cosine similarity on the embedding.

        Rows embedded by a *different* model are excluded rather than ranked.
        Their distance to this query is arithmetically valid and semantically
        meaningless, so including them would mix real matches with noise that
        looks exactly like a match — the failure mode this tier must not have.
        ``min_score`` drops weak neighbours: an HNSW search always returns
        ``top_k`` rows, so without a floor an unrelated question still recalls
        the least-unrelated memories.
        """
        top_k = max(1, min(int(top_k), 100))
        embedding = self._embedder.embed(query_text or "")

        params: List[object] = [embedding]
        clauses: List[str] = []
        next_index = 2
        clauses.append(f"embedding_model = ${next_index}")
        params.append(self.model_id)
        next_index += 1
        if kind:
            clauses.append(f"kind = ${next_index}")
            params.append(kind)
            next_index += 1
        if topic:
            clauses.append(f"topic = ${next_index}")
            params.append(topic)
            next_index += 1

        predicate = scope_filter(principal, start_index=next_index)
        clauses.append(predicate.sql)
        params.extend(predicate.params)

        where = " AND ".join(clauses)
        sql = f"""
            SELECT id, owner_user_id, visibility, kind, text, topic,
                   source_session, created_at,
                   1 - (embedding <=> $1) AS score
            FROM {MEMORY_TABLE}
            WHERE {where}
            ORDER BY embedding <=> $1
            LIMIT {top_k}
        """

        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            rows = await conn.fetch(sql, *params)
            records = [_row_to_record(row) for row in rows]
            floor = float(min_score)
            if floor > 0.0:
                records = [
                    record
                    for record in records
                    if record.score is not None and record.score >= floor
                ]
            if record_use and records:
                await self._record_use(conn, [record.id for record in records])
            return records
        finally:
            if own:
                await conn.close()

    async def _record_use(
        self, conn: "asyncpg.Connection", memory_ids: List[str]
    ) -> None:
        """Count a recall against the rows it surfaced.

        Only automatic recall records use, so ``uses``/``last_used`` mean "this
        row was put in front of the model" rather than "someone searched". P5's
        promotion/demotion decisions rest on that distinction.
        """
        await conn.execute(
            f"UPDATE {MEMORY_TABLE} "
            "SET uses = uses + 1, last_used = NOW() "
            "WHERE id = ANY($1::uuid[])",
            list(memory_ids),
        )


def _row_to_record(row: "asyncpg.Record") -> MemoryRecord:
    score = row.get("score") if hasattr(row, "get") else None
    return MemoryRecord(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        kind=str(row["kind"]),
        text=str(row["text"]),
        topic=row["topic"],
        source_session=row["source_session"],
        created_at=row["created_at"],
        score=float(score) if score is not None else None,
    )


__all__ = [
    "MEMORY_TABLE",
    "EmbeddingSpace",
    "EmbeddingSpaceMismatch",
    "MemoryRecord",
    "PgvectorMemoryStore",
    "SHARED",
]
