"""FG-27 Layer 2 — show a profile's ``(database, schema)`` binding before it is used.

Every profile on this deployment resolves the *same* Supabase DSN by design
(one instance per box), so the schema name is the only thing separating two
profiles' rows.  Layer 3 derives that schema from the profile name and Layer 1
refuses to connect to a schema another profile has claimed — but both act after
the profile exists.  A clone or an import can therefore be created, aliased and
handed to a user before anyone learns that its schema is somebody else's.

This module answers the question at creation time instead:

* which database and schema will this new profile resolve to, and
* has another profile already claimed that schema?

The second question is what makes it a refusal rather than a print.  Creating
the profile anyway would produce a directory that looks healthy and fails only
on first agent turn, in ``SupabaseAppStore.connect()``, with no mention of the
clone that caused it.

The DSN is resolved, never read as a literal: on the live deployment
``config.yaml`` holds ``dsn: ${DATABASE_URL}`` with the value in the profile's
``.env``, so a string comparison of config text would see two profiles as
unrelated when they share one Postgres.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from hermes_constants import (
    reset_hermes_home_override,
    set_hermes_home_override,
)
from hermes_cli.datastore import (
    MODES,
    SchemaOwnershipError,
    StoreMode,
    app_schema,
    profile_schema_slug,
)

logger = logging.getLogger(__name__)

_ENV_REF = re.compile(r"\$\{([^}]+)\}")


@dataclass(frozen=True)
class SchemaClaim:
    """Who owns one schema on the target database, as recorded by Layer 1."""

    schema: str
    #: ``None`` when the schema has no ``schema_owner`` marker: either it does
    #: not exist yet, or it predates FG-27 and will be adopted on first connect.
    claimed_by: Optional[str]
    claimed_home: Optional[str]

    def conflicts_with(self, slug: str) -> bool:
        """True when this schema belongs to a profile other than ``slug``."""
        return self.claimed_by is not None and self.claimed_by != slug


@dataclass(frozen=True)
class BindingReport:
    """What a new profile will resolve to, and whether it may have it."""

    profile: str
    slug: str
    #: Redacted ``host:port/database`` — never the credentials.
    database: str
    schemas: Tuple[Tuple[str, str], ...]
    claims: Tuple[SchemaClaim, ...]
    #: Why the claims are unknown, when they are.  An unreachable database is
    #: reported, not fatal: Layer 1 still fails closed at first connect, and a
    #: profile must remain creatable while Postgres is down.
    unverified: Optional[str] = None

    @property
    def configured(self) -> bool:
        """True when there is an app datastore to bind to at all."""
        return bool(self.database)

    @property
    def conflicts(self) -> Tuple[SchemaClaim, ...]:
        """Claims held by a different profile."""
        return tuple(claim for claim in self.claims if claim.conflicts_with(self.slug))

    def lines(self) -> List[str]:
        """Return operator-facing lines describing the binding."""
        if not self.configured:
            return [
                "App datastore: not configured — this profile is core-only "
                "(SQLite in its own HERMES_HOME)."
            ]
        out = [f"App datastore: {self.database}"]
        for mode, schema in self.schemas:
            claim = next((c for c in self.claims if c.schema == schema), None)
            if claim is None or claim.claimed_by is None:
                suffix = "unclaimed" if self.unverified is None else "claim unknown"
            elif claim.claimed_by == self.slug:
                suffix = "already claimed by this profile"
            else:
                suffix = f"CLAIMED BY PROFILE {claim.claimed_by!r}"
            out.append(f"  {mode}: schema {schema} ({suffix})")
        if self.unverified is not None:
            out.append(
                f"  Schema ownership was not verified: {self.unverified}. "
                f"The first agent turn verifies it and fails closed."
            )
        return out

    def raise_on_conflict(self) -> None:
        """Refuse a binding that lands on another profile's schema."""
        conflicts = self.conflicts
        if not conflicts:
            return
        detail = ", ".join(
            f"{claim.schema} (profile {claim.claimed_by!r} at "
            f"{claim.claimed_home or 'unknown home'})"
            for claim in conflicts
        )
        raise SchemaOwnershipError(
            f"Profile {self.profile!r} would resolve to a schema another "
            f"profile already owns on {self.database}: {detail}. Refusing to "
            f"create it: the two profiles' rows would interleave in one set of "
            f"tables with no provenance column to separate them again. Choose a "
            f"different profile name, or move the existing schema aside with "
            f"'hermes datastore split-profile'."
        )


def redact_dsn(dsn: str) -> str:
    """Return ``host:port/database`` for ``dsn``, with credentials removed.

    Used for display and for deciding whether two profiles share a database.
    """
    if not dsn:
        return ""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return "unparseable DSN"
    host = parts.hostname or "?"
    port = parts.port or 5432
    database = (parts.path or "").lstrip("/") or "?"
    return f"{host}:{port}/{database}"


def _profile_env(home: Path) -> Dict[str, str]:
    """Return the environment a profile resolves ``${VAR}`` refs against.

    A profile's own ``.env`` overrides the shell (``load_hermes_dotenv`` loads
    it with ``override=True``), so the same precedence applies here.
    """
    env: Dict[str, str] = dict(os.environ)
    env_file = home / ".env"
    if not env_file.is_file():
        return env
    try:
        from dotenv import dotenv_values

        for key, value in dotenv_values(str(env_file)).items():
            if value is not None:
                env[key] = value
    except Exception as err:  # noqa: BLE001 - a bad .env is not fatal here
        logger.debug("Could not read %s for DSN resolution: %s", env_file, err)
    return env


def _expand(value: object, env: Dict[str, str]) -> str:
    if not isinstance(value, str):
        return ""
    return _ENV_REF.sub(lambda m: env.get(m.group(1), ""), value)


def _raw_dsn(config: Dict[str, Any], mode: StoreMode) -> object:
    datastore = config.get("datastore")
    if not isinstance(datastore, dict):
        return ""
    overrides = datastore.get("overrides")
    if isinstance(overrides, dict):
        per_mode = overrides.get(mode)
        if isinstance(per_mode, dict):
            app = per_mode.get("supabase_app")
            if isinstance(app, dict) and app.get("dsn"):
                return app["dsn"]
    app = datastore.get("supabase_app")
    if isinstance(app, dict):
        return app.get("dsn", "")
    return ""


def resolved_app_dsn(home: Path, *, mode: StoreMode = "prod") -> str:
    """Return the app DSN a profile at ``home`` actually connects with.

    Reads that home's raw ``config.yaml`` and expands ``${VAR}`` references
    against its own ``.env`` — the indirection the live deployment uses, and
    the reason this cannot compare config text.
    """
    from hermes_cli.config import read_raw_config

    token = set_hermes_home_override(home)
    try:
        config = read_raw_config()
    except Exception as err:  # noqa: BLE001 - an unreadable config binds nothing
        logger.debug("Could not read config at %s: %s", home, err)
        return ""
    finally:
        reset_hermes_home_override(token)
    if not isinstance(config, dict):
        return ""
    return _expand(_raw_dsn(config, mode), _profile_env(home)).strip()


async def read_schema_claims(dsn: str, schemas: List[str]) -> List[SchemaClaim]:
    """Return the ownership marker of each schema on ``dsn``.

    Deliberately a raw connection rather than :meth:`SupabaseAppStore.connect`:
    that path verifies ownership and would raise on exactly the schema this
    needs to *describe*.
    """
    from tools.lazy_deps import ensure

    ensure("datastore.supabase")

    import asyncpg

    connection = await asyncpg.connect(dsn)
    try:
        claims: List[SchemaClaim] = []
        for schema in schemas:
            marker = await connection.fetchval(
                "SELECT to_regclass($1)", f"{schema}.schema_owner"
            )
            if marker is None:
                claims.append(SchemaClaim(schema, None, None))
                continue
            row = await connection.fetchrow(
                f"SELECT profile_slug, hermes_home FROM {schema}.schema_owner LIMIT 1"
            )
            if row is None:
                claims.append(SchemaClaim(schema, None, None))
                continue
            claims.append(
                SchemaClaim(schema, str(row["profile_slug"]), str(row["hermes_home"]))
            )
        return claims
    finally:
        await connection.close()


def _run_blocking(coro: Any, *, timeout: float) -> Any:
    """Run ``coro`` from sync code whether or not a loop is already running.

    Profile creation is synchronous and is reached both from the CLI (no loop)
    and from the dashboard's async web server, where blocking the live loop
    would deadlock it.
    """

    async def _with_timeout() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout())

    result: List[Any] = []
    error: List[BaseException] = []

    def _worker() -> None:
        try:
            result.append(asyncio.run(_with_timeout()))
        except BaseException as err:  # noqa: BLE001 - re-raised below
            error.append(err)

    thread = threading.Thread(target=_worker, name="datastore-binding", daemon=True)
    thread.start()
    thread.join(timeout + 1.0)
    if error:
        raise error[0]
    if not result:
        raise TimeoutError("Schema ownership lookup did not finish")
    return result[0]


def describe_binding(
    profile: str,
    *,
    source_home: Path,
    timeout: float = 8.0,
) -> BindingReport:
    """Describe the ``(database, schema)`` binding profile ``profile`` will get.

    ``source_home`` is the home whose datastore configuration the new profile
    inherits — the clone source, or the staged import tree.  The schemas are
    derived from the *new* name, which is the whole point: sharing the database
    is intended, sharing a schema is not.
    """
    dsn = resolved_app_dsn(source_home)
    slug = profile_schema_slug(profile)
    schemas = tuple((mode, app_schema(mode, profile=profile)) for mode in MODES)
    if not dsn:
        return BindingReport(profile, slug, "", schemas, ())

    database = redact_dsn(dsn)
    try:
        claims = _run_blocking(
            read_schema_claims(dsn, [schema for _, schema in schemas]),
            timeout=timeout,
        )
    except Exception as err:  # noqa: BLE001 - Layer 1 still fails closed later
        logger.debug("Schema ownership lookup failed for %s: %s", database, err)
        return BindingReport(
            profile,
            slug,
            database,
            schemas,
            (),
            unverified=f"{type(err).__name__}: {err}".strip(),
        )
    return BindingReport(profile, slug, database, schemas, tuple(claims))
