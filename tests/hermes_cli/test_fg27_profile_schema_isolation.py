"""FG-27 Layer 3 — profile-derived app schema names (real ``HERMES_HOME``).

Every profile resolves the same Supabase DSN, so the schema name is the only
thing separating two profiles' rows.  These exercise the real resolution path
(``HERMES_HOME`` → ``get_active_profile_name`` → ``app_schema``) against
temporary profile directories rather than patching the resolver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.datastore import (
    active_profile_slug,
    app_schema,
    get_store,
)


@pytest.fixture
def hermes_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a throwaway Hermes root laid out like a real install."""
    root = tmp_path / "hermes-root"
    (root / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root


def _use_profile(root: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_default_profile_keeps_the_historical_schema_names(
    hermes_root: Path,
) -> None:
    # Existing single-profile deployments must not need a migration.
    assert active_profile_slug() == "default"
    assert app_schema("prod") == "app_prod"
    assert app_schema("dev") == "app_dev"


def test_named_profile_derives_its_own_schema(
    hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_profile(hermes_root, "finance", monkeypatch)

    assert active_profile_slug() == "finance"
    assert app_schema("prod") == "app_prod_finance"
    assert app_schema("dev") == "app_dev_finance"


def test_two_profiles_on_one_dsn_do_not_share_a_schema(
    hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"datastore": {"supabase_app": {"dsn": "postgresql://example/db"}}}

    _use_profile(hermes_root, "finance", monkeypatch)
    finance = get_store("supabase-app", "prod", config=config)
    _use_profile(hermes_root, "product", monkeypatch)
    product = get_store("supabase-app", "prod", config=config)

    assert finance.dsn == product.dsn
    assert finance.schema != product.schema
    assert (finance.schema, product.schema) == (
        "app_prod_finance",
        "app_prod_product",
    )


def test_hyphenated_profile_is_identifier_safe_and_distinct(
    hermes_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``-`` is legal in a profile name but not in an unquoted identifier;
    # folding it to ``_`` must not collide with the underscore spelling.
    _use_profile(hermes_root, "p5-chinese", monkeypatch)
    hyphen = app_schema("prod")
    _use_profile(hermes_root, "p5_chinese", monkeypatch)
    underscore = app_schema("prod")

    assert "-" not in hyphen
    assert hyphen != underscore
    assert underscore == "app_prod_p5_chinese"


def test_long_profile_name_stays_within_the_identifier_limit() -> None:
    schema = app_schema("prod", profile="x" * 90)

    assert len(schema) <= 63
    assert schema.startswith("app_prod_")
    # Truncation alone would map every long name onto one schema.
    assert schema != app_schema("prod", profile="x" * 91)
