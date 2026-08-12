"""FG-28 console scope — the per-request chokepoint, unit-tested.

These exercise the fastapi-free core (:mod:`hermes_cli.console_scope`) with a
synthetic :class:`PrincipalStore`, so the security properties hold without a
live Supabase. The real-Postgres negative matrix is the FG-28 E2E on the box
(item 10); here we assert the *invariants* the chokepoint must satisfy
regardless of the store: authority re-derived in the target profile's scope,
owner-fallback refused, the picker never a grant.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hermes_cli.access import Principal
from hermes_cli.console_scope import (
    OwnerFallbackRefused,
    ProfileScopeError,
    administered_profiles,
    resolve_console_principal,
)
from hermes_cli.profile_registry import ProfileRegistryEntry

# The caller's GoTrue subject — one shared account, enrolled per-profile.
SUBJECT = "cto-sub-123"
OTHER_SUBJECT = "cfo-sub-456"


class _FakeStore:
    """Synthetic PrincipalStore answering from a ``{user_id: Principal}`` map."""

    def __init__(self, principals: dict[str, Principal]) -> None:
        self._principals = principals

    async def get(self, user_id: str) -> Principal | None:
        return self._principals.get(user_id)

    async def get_owner(self) -> Principal | None:
        return next(
            (p for p in self._principals.values() if p.role == "owner"), None
        )

    async def resolve_alias(self, subject: str) -> str | None:
        # No alias table in the test — the subject IS the user_id.
        return None


def _entry(name: str, home: Path) -> ProfileRegistryEntry:
    return ProfileRegistryEntry(
        name=name,
        hermes_home=home,
        is_default=(name == "default"),
        served=True,
        base_url="" if name == "default" else f"/p/{name}/",
        schema="app_prod" if name == "default" else f"app_prod_{name}",
        has_env=False,
        gateway_running=False,
    )


def _store_factory(per_home: dict[Path, _FakeStore]):
    """A StoreFactory that picks the synthetic store by home path."""

    def _build(home: Path) -> _FakeStore:
        return per_home.get(home, _FakeStore({}))

    return _build


@pytest.fixture
def two_profiles(
    tmp_path: Path,
) -> tuple[list[ProfileRegistryEntry], dict[Path, _FakeStore]]:
    """A default + engineers + hr registry with per-profile synthetic stores.

    CTO (SUBJECT) is owner of default, admin of engineers, and NOT enrolled
    in hr. CFO (OTHER_SUBJECT) is admin of hr only. That is the FG-28 motif:
    one shared account, several per-profile enrolments, separate authority.
    """
    root = tmp_path / "hermes-root"
    engineers_home = root / "profiles" / "engineers"
    hr_home = root / "profiles" / "hr"
    for home in (root, engineers_home, hr_home):
        home.mkdir(parents=True, exist_ok=True)

    entries = [
        _entry("default", root),
        _entry("engineers", engineers_home),
        _entry("hr", hr_home),
    ]
    per_home = {
        root: _FakeStore({SUBJECT: Principal(SUBJECT, "CTO", "owner")}),
        engineers_home: _FakeStore({SUBJECT: Principal(SUBJECT, "CTO", "admin")}),
        hr_home: _FakeStore({OTHER_SUBJECT: Principal(OTHER_SUBJECT, "CFO", "admin")}),
    }
    return entries, per_home


def test_administered_profiles_lists_only_where_caller_is_admin_or_owner(
    two_profiles,
) -> None:
    entries, per_home = two_profiles
    factory = _store_factory(per_home)
    # CTO administers default (owner) and engineers (admin), but not hr (not
    # enrolled there at all). CFO administers hr only.
    cto = asyncio.run(administered_profiles(SUBJECT, store_factory=factory, registry=entries))
    assert cto == ["default", "engineers"]
    cfo = asyncio.run(administered_profiles(OTHER_SUBJECT, store_factory=factory, registry=entries))
    assert cfo == ["hr"]


def test_administered_profiles_empty_subject_returns_nothing(two_profiles) -> None:
    entries, per_home = two_profiles
    assert asyncio.run(
        administered_profiles("", store_factory=_store_factory(per_home), registry=entries)
    ) == []


def test_resolve_console_principal_re_derives_in_target_scope(two_profiles) -> None:
    entries, per_home = two_profiles
    factory = _store_factory(per_home)
    # CTO is enrolled in engineers as admin — resolving there returns that
    # principal (re-derived from engineers' own principals, not default's).
    principal = asyncio.run(
        resolve_console_principal(SUBJECT, "engineers", store_factory=factory, registry=entries)
    )
    assert principal.user_id == SUBJECT
    assert principal.role == "admin"


def test_resolve_console_principal_refuses_owner_fallback(two_profiles) -> None:
    # The hole FG-28 closes: a console-routed request with no verified
    # subject must NOT fall back to the enrolled owner. It raises, and the
    # web_server maps that to 401.
    entries, per_home = two_profiles
    factory = _store_factory(per_home)
    with pytest.raises(OwnerFallbackRefused):
        asyncio.run(
            resolve_console_principal("", "engineers", store_factory=factory, registry=entries)
        )


def test_resolve_console_principal_refuses_caller_not_enrolled_in_target(
    two_profiles,
) -> None:
    # The picker is not a grant: CTO holds no row in hr, so scoping a turn
    # there is refused — even if the console offered the profile by mistake.
    entries, per_home = two_profiles
    factory = _store_factory(per_home)
    with pytest.raises(ProfileScopeError):
        asyncio.run(
            resolve_console_principal(SUBJECT, "hr", store_factory=factory, registry=entries)
        )


def test_resolve_console_principal_refuses_suspended_enrolment(two_profiles) -> None:
    entries, per_home = two_profiles
    # A suspended enrolment is a 403 (FG-26 §3.5 profile-local suspension),
    # not a silent fall-through to the owner.
    engineers_home = entries[1].hermes_home
    per_home = dict(per_home)
    per_home[engineers_home] = _FakeStore(
        {SUBJECT: Principal(SUBJECT, "CTO", "admin", active=False)}
    )
    factory = _store_factory(per_home)
    with pytest.raises(ProfileScopeError):
        asyncio.run(
            resolve_console_principal(SUBJECT, "engineers", store_factory=factory, registry=entries)
        )


def test_resolve_console_principal_refuses_unknown_target(two_profiles) -> None:
    entries, per_home = two_profiles
    factory = _store_factory(per_home)
    with pytest.raises(ProfileScopeError):
        asyncio.run(
            resolve_console_principal(
                SUBJECT, "nonexistent", store_factory=factory, registry=entries
            )
        )
