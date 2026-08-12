"""``hermes datastore`` — inspect and migrate a profile's app-schema binding (FG-27).

Two commands, both about the boundary that separates two profiles' rows on one
shared Postgres:

``show``
    Print the resolved ``(database, schema)`` pair for this profile and who owns
    those schemas. The DSN is indirect on a real deployment
    (``dsn: ${DATABASE_URL}``), so "which database am I on" is otherwise not
    answerable by reading config.

``split-profile``
    Move a whole schema onto another profile's derived name and re-claim it —
    the migration for data written before FG-27 derived schemas, or for a
    profile that was renamed. It **refuses** to disentangle rows two profiles
    both wrote: those rows carry no provenance column, so any split would be a
    guess, and a plausible-looking wrong answer is worse than a refusal.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Dict, List, Optional, Tuple

from hermes_cli.datastore import (
    MODES,
    SchemaOwnershipError,
    StoreMode,
    app_schema,
    get_store,
    profile_schema_slug,
)
from hermes_cli.datastore_binding import (
    read_schema_claims,
    redact_dsn,
)

#: Tables whose row counts are compared before and after a move.  Read from the
#: catalog rather than a hard-coded list so a schema carrying tables this
#: Hermes does not know about is still verified.
_COUNT_QUERY = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = $1 AND table_type = 'BASE TABLE'
ORDER BY table_name
"""


def _modes(requested: str) -> Tuple[StoreMode, ...]:
    if requested == "both":
        return MODES
    if requested == "prod":
        return ("prod",)
    return ("dev",)


def datastore_show_command(args: argparse.Namespace) -> int:
    """Print this profile's resolved app datastore binding and its owner."""
    from hermes_cli.profiles import get_active_profile_name

    profile = get_active_profile_name()
    store = get_store("supabase-app", "prod")
    if not store.dsn:
        print(
            "App datastore: not configured — this profile is core-only "
            "(SQLite in its own HERMES_HOME)."
        )
        return 0

    database = redact_dsn(store.dsn)
    schemas = [(mode, app_schema(mode)) for mode in MODES]
    print(f"Profile:       {profile}")
    print(f"App datastore: {database}")
    try:
        claims = asyncio.run(
            read_schema_claims(store.dsn, [schema for _, schema in schemas])
        )
    except Exception as error:  # noqa: BLE001 - reported, not fatal
        for mode, schema in schemas:
            print(f"  {mode}: schema {schema}")
        print(f"  Ownership could not be read: {error}", file=sys.stderr)
        return 1
    by_schema = {claim.schema: claim for claim in claims}
    slug = profile_schema_slug(profile)
    for mode, schema in schemas:
        claim = by_schema.get(schema)
        if claim is None or claim.claimed_by is None:
            owner = "unclaimed (adopted on first connect)"
        elif claim.claimed_by == slug:
            owner = f"claimed by this profile ({claim.claimed_home})"
        else:
            owner = f"CLAIMED BY {claim.claimed_by!r} at {claim.claimed_home}"
        print(f"  {mode}: schema {schema} — {owner}")
    return 0


async def _table_counts(connection, schema: str) -> Dict[str, int]:
    tables = [row["table_name"] for row in await connection.fetch(_COUNT_QUERY, schema)]
    counts: Dict[str, int] = {}
    for table in tables:
        counts[table] = int(
            await connection.fetchval(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
        )
    return counts


def _count(counts: Dict[str, int], table: str) -> str:
    return "absent" if table not in counts else str(counts[table])


async def _schema_exists(connection, schema: str) -> bool:
    return bool(
        await connection.fetchval(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1",
            schema,
        )
    )


async def _claimed_by(connection, schema: str) -> Optional[str]:
    marker = await connection.fetchval(
        "SELECT to_regclass($1)", f"{schema}.schema_owner"
    )
    if marker is None:
        return None
    row = await connection.fetchrow(
        f"SELECT profile_slug FROM {schema}.schema_owner LIMIT 1"
    )
    return None if row is None else str(row["profile_slug"])


async def _split_one(
    connection,
    *,
    source_schema: str,
    target_schema: str,
    source_profile: str,
    target_profile: str,
    target_home: str,
    dry_run: bool,
) -> List[str]:
    """Move one schema, verifying the row counts survived it.

    Returns the report lines.  Raises :class:`SchemaOwnershipError` for the
    cases that must not be attempted.
    """
    if not await _schema_exists(connection, source_schema):
        return [f"  {source_schema} → {target_schema}: source schema does not exist, skipped"]

    owner = await _claimed_by(connection, source_schema)
    source_slug = profile_schema_slug(source_profile)
    if owner is not None and owner != source_slug:
        raise SchemaOwnershipError(
            f"Schema {source_schema!r} is claimed by profile {owner!r}, not by "
            f"{source_profile!r}. Refusing to move it: if both profiles wrote "
            f"here their rows are interleaved in one set of tables, and those "
            f"rows carry no provenance column — there is no way to tell whose "
            f"each one is, so any split would be a guess. Move the schema for "
            f"the profile that actually owns it (--from-profile {owner}), or "
            f"reconstruct the second profile's data by hand."
        )

    if await _schema_exists(connection, target_schema):
        target_counts = await _table_counts(connection, target_schema)
        populated = {t: n for t, n in target_counts.items() if n and t != "schema_owner"}
        if populated:
            raise SchemaOwnershipError(
                f"Schema {target_schema!r} already exists and holds rows "
                f"({', '.join(f'{t}={n}' for t, n in sorted(populated.items()))}). "
                f"Refusing to move {source_schema!r} onto it: that would merge "
                f"two profiles' rows into one set of tables with no provenance "
                f"column to separate them again. Move the existing "
                f"{target_schema!r} aside first."
            )
        if not dry_run:
            await connection.execute(f"DROP SCHEMA {target_schema} CASCADE")

    before = await _table_counts(connection, source_schema)
    lines = [
        f"  {source_schema} → {target_schema}: "
        f"{len(before)} tables, {sum(before.values())} rows"
    ]
    if dry_run:
        lines.append("    (dry run — nothing moved)")
        return lines

    await connection.execute(f"ALTER SCHEMA {source_schema} RENAME TO {target_schema}")
    after = await _table_counts(connection, target_schema)
    if after != before:
        differing: List[str] = sorted(set(before) | set(after))
        detail = ", ".join(
            f"{table}: {_count(before, table)} → {_count(after, table)}"
            for table in differing
            if before.get(table) != after.get(table)
        )
        raise SchemaOwnershipError(
            f"Row counts changed while moving {source_schema!r} to "
            f"{target_schema!r} ({detail}). Rolled back."
        )

    await connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {target_schema}.schema_owner (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            profile_slug TEXT NOT NULL,
            hermes_home TEXT NOT NULL,
            claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    await connection.execute(
        f"""
        INSERT INTO {target_schema}.schema_owner (singleton, profile_slug, hermes_home)
        VALUES (TRUE, $1, $2)
        ON CONFLICT (singleton) DO UPDATE
            SET profile_slug = EXCLUDED.profile_slug,
                hermes_home = EXCLUDED.hermes_home,
                claimed_at = NOW();
        """,
        profile_schema_slug(target_profile),
        target_home,
    )
    lines.append(f"    verified: {len(after)} tables, {sum(after.values())} rows")
    lines.append(f"    claimed for profile {target_profile!r}")
    return lines


async def _split_profile(
    *,
    source_profile: str,
    target_profile: str,
    modes: Tuple[StoreMode, ...],
    dry_run: bool,
) -> List[str]:
    from hermes_cli.profiles import get_profile_dir

    store = get_store("supabase-app", "prod")
    if not store.dsn:
        raise SchemaOwnershipError(
            "No app datastore is configured for this profile, so there is no "
            "schema to move (set datastore.supabase_app.dsn first)."
        )

    from tools.lazy_deps import ensure

    ensure("datastore.supabase")

    import asyncpg

    target_home = str(get_profile_dir(target_profile).resolve())
    connection = await asyncpg.connect(store.dsn)
    lines = [f"Database: {redact_dsn(store.dsn)}"]
    try:
        transaction = connection.transaction()
        await transaction.start()
        try:
            for mode in modes:
                lines.extend(
                    await _split_one(
                        connection,
                        source_schema=app_schema(mode, profile=source_profile),
                        target_schema=app_schema(mode, profile=target_profile),
                        source_profile=source_profile,
                        target_profile=target_profile,
                        target_home=target_home,
                        dry_run=dry_run,
                    )
                )
        except BaseException:
            await transaction.rollback()
            raise
        if dry_run:
            await transaction.rollback()
        else:
            await transaction.commit()
    finally:
        await connection.close()
    return lines


def datastore_split_profile_command(args: argparse.Namespace) -> int:
    """Move a whole schema from one profile's name onto another's."""
    source_profile = args.from_profile
    target_profile = args.to_profile
    if source_profile == target_profile:
        print(
            "--from-profile and --to-profile are the same profile; nothing to move.",
            file=sys.stderr,
        )
        return 2
    try:
        lines = asyncio.run(
            _split_profile(
                source_profile=source_profile,
                target_profile=target_profile,
                modes=_modes(args.mode),
                dry_run=bool(args.dry_run),
            )
        )
    except SchemaOwnershipError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - the CLI reports, never traces
        print(f"Could not move the schema: {error}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    if not args.dry_run:
        print(
            f"Profile {target_profile!r} now owns the moved schema(s). Nothing "
            f"remains under {source_profile!r}'s names."
        )
    return 0


def register_datastore_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``hermes datastore`` and its sub-actions."""
    parser = subparsers.add_parser(
        "datastore",
        help="Inspect and migrate this profile's app schema (FG-27)",
        description=(
            "Every profile on this deployment shares one Supabase instance, so "
            "the app schema is the whole boundary between two profiles' rows. "
            "These commands show which (database, schema) a profile resolves "
            "to, and move a schema between profile names."
        ),
    )
    sub = parser.add_subparsers(dest="datastore_command", required=True)

    show = sub.add_parser(
        "show",
        help="Show the resolved (database, schema) pair and its claiming profile",
    )
    show.set_defaults(func=datastore_show_command)

    split = sub.add_parser(
        "split-profile",
        help="Move a whole schema onto another profile's derived schema name",
        description=(
            "Moves an entire schema and verifies the row counts survived the "
            "move. Refuses to disentangle rows two profiles both wrote: they "
            "carry no provenance column, so a split would be a guess."
        ),
    )
    split.add_argument(
        "--from-profile",
        required=True,
        metavar="PROFILE",
        help="The profile whose schema is being moved (its data must be only its own)",
    )
    split.add_argument(
        "--to-profile",
        required=True,
        metavar="PROFILE",
        help="The profile that should own the schema afterwards",
    )
    split.add_argument(
        "--mode",
        choices=("prod", "dev", "both"),
        default="both",
        help="Which schema(s) to move (default: both)",
    )
    split.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would move, and roll back without changing anything",
    )
    split.set_defaults(func=datastore_split_profile_command)
