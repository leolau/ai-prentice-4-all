"""FG-27 Layer 2 — the resolved binding a new profile inherits.

The refusal these cover is the one Layer 3 and Layer 1 cannot make: at clone /
import time, before the profile exists. What makes it non-obvious is the
indirection — the live deployment writes ``dsn: ${DATABASE_URL}`` and keeps the
value in the profile's ``.env``, so comparing config *text* would report two
profiles as unrelated while they share one Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_cli.datastore_binding as binding_mod
from hermes_cli.datastore import SchemaOwnershipError, app_schema
from hermes_cli.datastore_binding import (
    BindingReport,
    SchemaClaim,
    describe_binding,
    redact_dsn,
    resolved_app_dsn,
)

DSN = "postgresql://hermes:secret@db.internal:6543/hermes_prod"


def _write_profile(home: Path, *, dsn: str = DSN, env: str | None = None) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"datastore:\n  supabase_app:\n    dsn: {dsn}\n", encoding="utf-8"
    )
    if env is not None:
        (home / ".env").write_text(env, encoding="utf-8")
    return home


def test_redacted_dsn_identifies_the_database_without_the_password() -> None:
    assert redact_dsn(DSN) == "db.internal:6543/hermes_prod"
    assert "secret" not in redact_dsn(DSN)
    assert redact_dsn("") == ""


def test_dsn_is_resolved_through_the_profiles_own_env(tmp_path: Path) -> None:
    """``${DATABASE_URL}`` in config, value in the copied ``.env``.

    This is how the live box is configured, and the reason the check resolves
    rather than string-compares: the literal in both profiles' config.yaml is
    identical *and* meaningless.
    """
    home = _write_profile(
        tmp_path / "cloned",
        dsn="${DATABASE_URL}",
        env=f"DATABASE_URL={DSN}\n",
    )
    assert resolved_app_dsn(home) == DSN


def test_a_profile_env_wins_over_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://shell@elsewhere:5432/other")
    home = _write_profile(
        tmp_path / "cloned", dsn="${DATABASE_URL}", env=f"DATABASE_URL={DSN}\n"
    )
    assert resolved_app_dsn(home) == DSN


def test_a_mode_override_is_preferred_over_the_base_dsn(tmp_path: Path) -> None:
    home = tmp_path / "override"
    home.mkdir()
    (home / "config.yaml").write_text(
        "datastore:\n"
        "  supabase_app:\n"
        "    dsn: postgresql://base@base:5432/base\n"
        "  overrides:\n"
        "    prod:\n"
        "      supabase_app:\n"
        f"        dsn: {DSN}\n",
        encoding="utf-8",
    )
    assert resolved_app_dsn(home, mode="prod") == DSN


def test_no_datastore_configured_resolves_to_nothing(tmp_path: Path) -> None:
    home = tmp_path / "bare"
    home.mkdir()
    assert resolved_app_dsn(home) == ""


def test_a_report_with_a_foreign_claim_refuses_and_names_the_holder() -> None:
    report = BindingReport(
        profile="finance",
        slug="finance",
        database="db.internal:6543/hermes_prod",
        schemas=(("prod", "app_prod_finance"),),
        claims=(SchemaClaim("app_prod_finance", "product", "/opt/data/product"),),
    )
    assert report.conflicts
    with pytest.raises(SchemaOwnershipError) as excinfo:
        report.raise_on_conflict()
    message = str(excinfo.value)
    assert "app_prod_finance" in message
    assert "product" in message
    assert "/opt/data/product" in message
    assert "split-profile" in message


def test_a_profiles_own_claim_is_not_a_conflict() -> None:
    report = BindingReport(
        profile="finance",
        slug="finance",
        database="db.internal:6543/hermes_prod",
        schemas=(("prod", "app_prod_finance"),),
        claims=(SchemaClaim("app_prod_finance", "finance", "/opt/data/finance"),),
    )
    assert not report.conflicts
    report.raise_on_conflict()
    assert any("already claimed by this profile" in line for line in report.lines())


def test_the_report_prints_the_resolved_database_and_schema() -> None:
    report = BindingReport(
        profile="finance",
        slug="finance",
        database="db.internal:6543/hermes_prod",
        schemas=(("prod", "app_prod_finance"), ("dev", "app_dev_finance")),
        claims=(),
    )
    printed = "\n".join(report.lines())
    assert "db.internal:6543/hermes_prod" in printed
    assert "app_prod_finance" in printed
    assert "app_dev_finance" in printed


def test_an_unreachable_database_is_reported_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Layer 1 still fails closed on first connect; creation must not need PG.

    A profile has to remain creatable while Postgres is down — otherwise a
    database outage takes the whole profile-management surface with it.
    """
    home = _write_profile(tmp_path / "source")

    async def _boom(dsn: str, schemas: list[str]) -> list[SchemaClaim]:
        raise OSError("connection refused")

    monkeypatch.setattr(binding_mod, "read_schema_claims", _boom)
    report = describe_binding("finance", source_home=home)

    assert report.unverified is not None
    assert not report.conflicts
    report.raise_on_conflict()
    assert any("fails closed" in line for line in report.lines())


def test_the_derived_schemas_come_from_the_new_name_not_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _write_profile(tmp_path / "source")
    seen: list[tuple[str, list[str]]] = []

    async def _claims(dsn: str, schemas: list[str]) -> list[SchemaClaim]:
        seen.append((dsn, schemas))
        return [SchemaClaim(schema, None, None) for schema in schemas]

    monkeypatch.setattr(binding_mod, "read_schema_claims", _claims)
    report = describe_binding("finance", source_home=home)

    assert report.database == "db.internal:6543/hermes_prod"
    assert dict(report.schemas) == {
        "prod": app_schema("prod", profile="finance"),
        "dev": app_schema("dev", profile="finance"),
    }
    # Sharing the *database* is intended; the check resolves the real DSN.
    assert seen == [(DSN, [schema for _, schema in report.schemas])]


def test_create_profile_refuses_a_claimed_schema_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal must precede the copy, or it leaves a half-made profile."""
    from hermes_cli import profiles as profiles_mod

    root = tmp_path / "root"
    source = _write_profile(root / "profiles" / "product")
    monkeypatch.setenv("HERMES_HOME", str(source))

    async def _claims(dsn: str, schemas: list[str]) -> list[SchemaClaim]:
        return [SchemaClaim(schema, "product", str(source)) for schema in schemas]

    monkeypatch.setattr(binding_mod, "read_schema_claims", _claims)
    monkeypatch.setattr(
        profiles_mod, "get_profile_dir", lambda name: root / "profiles" / name
    )

    with pytest.raises(SchemaOwnershipError):
        profiles_mod.create_profile("finance", clone_from="product", clone_config=True)

    assert not (root / "profiles" / "finance").exists()


def test_import_refuses_a_claimed_schema_before_moving_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An import inherits a DSN exactly as ``--clone`` does."""
    import tarfile

    from hermes_cli import profiles as profiles_mod

    root = tmp_path / "root"
    staged = _write_profile(tmp_path / "build" / "finance")
    archive = tmp_path / "finance.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staged, arcname="finance")

    monkeypatch.setenv("HERMES_HOME", str(root))

    async def _claims(dsn: str, schemas: list[str]) -> list[SchemaClaim]:
        return [SchemaClaim(schema, "product", "/opt/data/product") for schema in schemas]

    monkeypatch.setattr(binding_mod, "read_schema_claims", _claims)
    monkeypatch.setattr(
        profiles_mod, "get_profile_dir", lambda name: root / "profiles" / name
    )

    with pytest.raises(SchemaOwnershipError):
        profiles_mod.import_profile(str(archive))

    assert not (root / "profiles" / "finance").exists()


def test_creation_still_succeeds_when_the_schema_is_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hermes_cli import profiles as profiles_mod

    root = tmp_path / "root"
    source = _write_profile(root / "profiles" / "product")
    monkeypatch.setenv("HERMES_HOME", str(source))

    async def _claims(dsn: str, schemas: list[str]) -> list[SchemaClaim]:
        return [SchemaClaim(schema, None, None) for schema in schemas]

    monkeypatch.setattr(binding_mod, "read_schema_claims", _claims)
    monkeypatch.setattr(
        profiles_mod, "get_profile_dir", lambda name: root / "profiles" / name
    )
    printed: list[str] = []

    created = profiles_mod.create_profile(
        "finance",
        clone_from="product",
        clone_config=True,
        no_alias=True,
        report=printed.append,
    )

    assert created.is_dir()
    assert (created / "config.yaml").is_file()
    # The inherited database is stated, not implied.
    assert any("db.internal:6543/hermes_prod" in line for line in printed)
    assert any(app_schema("prod", profile="finance") in line for line in printed)
