"""FG-26 — resolve the rows a departing principal owns (transfer or purge).

Deleting an enrolment is the easy half. The hard half is everything that
points at it: memories, files, GTS goals/tasks, todos, tags, notifications and
per-item grants all carry ``owner_user_id`` (and a ``private:<user_id>``
visibility tag), and **none of them have a foreign key to ``principals``** —
``ON DELETE CASCADE`` reaches ``channel_identities`` and ``principal_aliases``
and stops there. Drop the principal row on its own and those rows survive with
an ``owner_user_id`` nobody answers to: unreadable under C2 (no principal can
match ``private:<gone>``), unattributable in the UI, and undeletable through
any normal surface.

So a hard delete must state what happens to that data, which is why the API
requires an explicit ``strategy``:

``transfer``
    Every owned row moves to a named successor, including rewriting
    ``private:<departing>`` to ``private:<successor>`` so private rows stay
    private *to somebody who exists*. Nothing is destroyed.

``purge``
    Rows private to the departing principal are deleted; rows they owned but
    had **shared** are re-pointed at the actor performing the purge. Shared
    rows are team knowledge somebody else may be relying on — deleting them
    because their author left is a data-loss surprise, and leaving them is the
    dangling-owner bug — so they change hands and the purge stays a statement
    about *that person's private data*.

Both strategies discover their tables from ``information_schema`` in the
profile's own schema rather than from a hard-coded list, because the list is
exactly the thing that goes stale: a table added by a later FG would silently
be skipped and leak dangling owners. Append-only audit ledgers (C5 ``changes``,
C8 ``interactions``) are deliberately untouched — they record who did what and
must keep saying so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

#: What to do with the rows a hard-deleted principal owns.
DeleteStrategy = Literal["transfer", "purge"]
DELETE_STRATEGIES: tuple[DeleteStrategy, ...] = ("transfer", "purge")

#: Column that marks row ownership across the app schema (C2).
OWNER_COLUMN = "owner_user_id"

#: Tables whose ``owner_user_id`` is an audit fact rather than ownership of
#: content, and which must therefore keep pointing at the departing principal.
AUDIT_TABLES: frozenset[str] = frozenset({"changes", "interactions",
                                          "interaction_rollups",
                                          "memory_audit"})


@dataclass
class OwnershipOutcome:
    """What a :func:`resolve_owned_rows` run did, per table."""

    strategy: DeleteStrategy
    transferred: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def rows_transferred(self) -> int:
        return sum(self.transferred.values())

    @property
    def rows_deleted(self) -> int:
        return sum(self.deleted.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "rows_transferred": self.rows_transferred,
            "rows_deleted": self.rows_deleted,
            "transferred": dict(self.transferred),
            "deleted": dict(self.deleted),
        }


async def owned_tables(
    connection: "asyncpg.Connection",
    schema: str,
) -> list[tuple[str, bool]]:
    """Return ``(table, has_visibility)`` for every owned table in ``schema``.

    Discovered rather than listed, so a table a later feature group adds is
    swept without anybody remembering to update this module.
    """
    rows = await connection.fetch(
        """
        SELECT c.table_name,
               EXISTS (
                   SELECT 1 FROM information_schema.columns v
                   WHERE v.table_schema = c.table_schema
                     AND v.table_name = c.table_name
                     AND v.column_name = 'visibility'
               ) AS has_visibility
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = $1
          AND c.column_name = $2
          AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name
        """,
        schema,
        OWNER_COLUMN,
    )
    return [
        (str(row["table_name"]), bool(row["has_visibility"]))
        for row in rows
        if str(row["table_name"]) not in AUDIT_TABLES
    ]


async def resolve_owned_rows(
    connection: "asyncpg.Connection",
    *,
    schema: str,
    user_id: str,
    strategy: DeleteStrategy,
    successor_user_id: str,
) -> OwnershipOutcome:
    """Transfer or purge everything ``user_id`` owns in ``schema``.

    ``successor_user_id`` receives the rows: the named transfer target under
    ``transfer``, and the purging actor (for shared rows only) under ``purge``.
    Runs in one transaction so a partial sweep cannot leave half the tables
    dangling. Returns per-table counts for the audit record.
    """
    if strategy not in DELETE_STRATEGIES:
        raise ValueError(
            f"strategy must be one of {DELETE_STRATEGIES}; got {strategy!r}"
        )
    if not successor_user_id:
        raise ValueError("successor_user_id is required")
    if successor_user_id == user_id:
        raise ValueError("The successor cannot be the principal being removed")

    outcome = OwnershipOutcome(strategy=strategy)
    private_tag = f"private:{user_id}"
    successor_tag = f"private:{successor_user_id}"

    async with connection.transaction():
        for table, has_visibility in await owned_tables(connection, schema):
            qualified = f'{_quote(schema)}.{_quote(table)}'
            if strategy == "purge" and has_visibility:
                deleted = await connection.execute(
                    f"DELETE FROM {qualified} "
                    f"WHERE {OWNER_COLUMN} = $1 AND visibility = $2",
                    user_id,
                    private_tag,
                )
                count = _affected(deleted)
                if count:
                    outcome.deleted[table] = count
            elif strategy == "purge":
                # No visibility column ⇒ no private/shared distinction to make;
                # the row is the person's own content, so purge means delete.
                deleted = await connection.execute(
                    f"DELETE FROM {qualified} WHERE {OWNER_COLUMN} = $1",
                    user_id,
                )
                count = _affected(deleted)
                if count:
                    outcome.deleted[table] = count

            if has_visibility:
                moved = await connection.execute(
                    f"""
                    UPDATE {qualified}
                    SET {OWNER_COLUMN} = $2,
                        visibility = CASE WHEN visibility = $3
                                          THEN $4 ELSE visibility END
                    WHERE {OWNER_COLUMN} = $1
                    """,
                    user_id,
                    successor_user_id,
                    private_tag,
                    successor_tag,
                )
            else:
                moved = await connection.execute(
                    f"UPDATE {qualified} SET {OWNER_COLUMN} = $2 "
                    f"WHERE {OWNER_COLUMN} = $1",
                    user_id,
                    successor_user_id,
                )
            count = _affected(moved)
            if count:
                outcome.transferred[table] = count

        grants = await _resolve_grants(
            connection,
            schema=schema,
            user_id=user_id,
            strategy=strategy,
            successor_user_id=successor_user_id,
        )
        if grants:
            key = "item_grants"
            if strategy == "purge":
                outcome.deleted[key] = grants
            else:
                outcome.transferred[key] = grants

    return outcome


async def dangling_owner_ids(
    connection: "asyncpg.Connection",
    schema: str,
) -> dict[str, int]:
    """Return ``{table: rows}`` whose ``owner_user_id`` names no principal.

    The invariant a hard delete must preserve, expressed as a query so a test
    can assert it directly instead of enumerating tables by hand.
    """
    result: dict[str, int] = {}
    for table, _ in await owned_tables(connection, schema):
        count = await connection.fetchval(
            f"""
            SELECT COUNT(*) FROM {_quote(schema)}.{_quote(table)} t
            WHERE t.{OWNER_COLUMN} IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {_quote(schema)}.principals p
                  WHERE p.user_id = t.{OWNER_COLUMN}
              )
            """
        )
        if count:
            result[table] = int(count)
    return result


async def _resolve_grants(
    connection: "asyncpg.Connection",
    *,
    schema: str,
    user_id: str,
    strategy: DeleteStrategy,
    successor_user_id: str,
) -> int:
    """Move or drop the FG-19 per-item grants that name the departing user.

    A grant to somebody who is gone confers access to nobody and blocks the
    single-assignee invariant, so it never survives untouched: ``purge`` drops
    it, ``transfer`` hands it to the successor (dropping it if that would
    duplicate a grant the successor already holds).
    """
    exists = await connection.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = $1 AND table_name = 'item_grants'
        )
        """,
        schema,
    )
    if not exists:
        return 0
    table = f"{_quote(schema)}.item_grants"
    if strategy == "purge":
        result = await connection.execute(
            f"DELETE FROM {table} WHERE user_id = $1", user_id
        )
        return _affected(result)
    await connection.execute(
        f"""
        DELETE FROM {table} g
        WHERE g.user_id = $1
          AND EXISTS (
              SELECT 1 FROM {table} other
              WHERE other.user_id = $2
                AND other.item_kind = g.item_kind
                AND other.item_id = g.item_id
          )
        """,
        user_id,
        successor_user_id,
    )
    result = await connection.execute(
        f"UPDATE {table} SET user_id = $2 WHERE user_id = $1",
        user_id,
        successor_user_id,
    )
    return _affected(result)


def _quote(identifier: str) -> str:
    """Quote a discovered identifier for interpolation into SQL.

    The names come from ``information_schema`` in a schema this process already
    owns, but they are still interpolated text — quoting (and rejecting an
    embedded quote outright) keeps that seam closed.
    """
    if '"' in identifier or not identifier:
        raise ValueError(f"Refusing to quote identifier: {identifier!r}")
    return f'"{identifier}"'


def _affected(status: str) -> int:
    """Rows affected, parsed out of asyncpg's command status string."""
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0
