"""FG-28 console scope — the per-request chokepoint for the multi-profile
admin console.

Under the one-process decision (FG-28 §"Architecture decision"), every
console-routed request enters its target profile's scope (HERMES_HOME + secret
scope) at ONE chokepoint before any resolution runs, and the principal is
re-resolved there from the target profile's own ``principals`` table. The
profile picker is a routing hint, never a grant: no row in the target profile
is a 409 regardless of what the caller holds elsewhere, and a suspended
enrolment is a 403. Owner-fallback is refused on these routes — no verified
subject is a 401, never the enrolled owner — because in one process the hop
looks like a function call rather than a service call, so a missing identity is
not conspicuous enough to be caught any other way.

This module is the **fastapi-free, unit-testable core**. ``web_server`` wires it
as a dependency that reads ``?profile=<name>`` (defaulting to the active profile
when absent, so single-profile consoles are byte-identical). The hard
security property — authority re-derived in the target profile's scope, not
asserted by the routing layer — lives here, behind a ``store_factory`` the
tests inject with a synthetic :class:`PrincipalStore`.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Protocol

from hermes_cli.profile_registry import ProfileRegistryEntry, get_profile_registry


class _PrincipalLike(Protocol):
    """The slice of :class:`PrincipalStore` the console scope depends on.

    Kept as a Protocol so the unit tests inject a synthetic store without
    needing a live Supabase connection (the real Postgres path is exercised
    by the FG-28 E2E negative matrix on the box).
    """

    async def get(self, user_id: str) -> Optional[Any]: ...
    async def get_owner(self) -> Optional[Any]: ...
    async def resolve_alias(self, subject: str) -> Optional[str]: ...


#: Builds a :data:`_PrincipalLike` for the profile currently scoped by
#: :func:`_scoped_to`. The ``profile_home`` argument lets tests return a
#: per-profile synthetic store; production ignores it and resolves through
#: ``_comms_app_store()`` (which honours the active scope).
StoreFactory = Callable[[Path], "_PrincipalLike"]


class ProfileScopeError(Exception):
    """A console request named a profile it cannot be scoped to.

    Maps to 403 on the console routes: the picker is a routing hint, never a
    grant — authority is re-derived inside the target profile's scope.
    """


class OwnerFallbackRefused(Exception):
    """A console-routed request carried no verified subject.

    Refused (401) rather than falling back to the enrolled owner. This single
    behaviour is the difference between a scoped console and a root console
    with a dropdown (FG-28 §"The most dangerous hole").
    """


@contextmanager
def _scoped_to(profile_home: Path) -> Iterator[Path]:
    """Enter a profile's runtime scope (config + secrets) for the block.

    Wraps :func:`profile_runtime_scope` so the rest of this module never
    names the secret-scope seam directly — the pair
    ``set_hermes_home_override`` + ``set_secret_scope`` is the whole
    mechanism, and centralising it here is what makes "which profile am I in"
    a property of the request rather than a parameter threaded through handlers.
    """
    from agent.profile_runtime import profile_runtime_scope

    with profile_runtime_scope(profile_home) as home:
        yield home


async def _principal_for_subject(
    store: "_PrincipalLike", subject: str
) -> Optional[Any]:
    """Resolve the principal for ``subject`` in the *currently scoped* profile.

    Mirrors :func:`_comms_resolve_principal`'s subject → alias → ``get``
    chain, but with no owner-fallback and no ``?as=`` narrowing: the console
    chokepoint resolves exactly one identity (the caller's) in exactly one
    profile (the target's).
    """
    user_id = (await store.resolve_alias(subject)) or subject
    return await store.get(user_id)


async def administered_profiles(
    subject: str,
    *,
    store_factory: StoreFactory,
    registry: Optional[List[ProfileRegistryEntry]] = None,
) -> List[str]:
    """Profiles where ``subject`` holds an active ``admin``/``owner`` row.

    The cross-profile read the switcher, the picker, and the account-verb gate
    all need. Iterates the served profile set (:func:`get_profile_registry`),
    enters each profile's scope, and asks that profile's own ``principals``
    table — never a shared authority store, so there is nothing here to keep
    in sync. Returns profile names the caller may administer, in registry
    order (default first).
    """
    if not subject:
        return []
    entries = registry if registry is not None else get_profile_registry()
    administered: List[str] = []
    for entry in entries:
        if not entry.hermes_home.is_dir():
            continue
        with _scoped_to(entry.hermes_home):
            store = store_factory(entry.hermes_home)
            principal = await _principal_for_subject(store, subject)
            if principal is None:
                continue
            if not getattr(principal, "active", True):
                continue
            if getattr(principal, "role", "") in ("owner", "admin"):
                administered.append(entry.name)
    return administered


async def resolve_console_principal(
    subject: str,
    target_profile: str,
    *,
    store_factory: StoreFactory,
    registry: Optional[List[ProfileRegistryEntry]] = None,
) -> Any:
    """Re-resolve the caller's principal in the target profile's scope.

    The chokepoint. ``target_profile`` is the ``?profile=`` hint; the caller
    (web_server) is responsible for defaulting it to the active profile when
    absent so single-profile consoles are byte-identical.

    Authority is re-derived, never asserted:

    - no verified subject → :class:`OwnerFallbackRefused` (401), never the
      owner fallback — the hole that would turn a scoped console into a root
      console with a dropdown;
    - subject not enrolled in the target profile → :class:`ProfileScopeError`
      (409), matching :func:`_comms_resolve_principal`'s authenticated-but-
      unenrolled refusal;
    - suspended enrolment → :class:`ProfileScopeError` (403), matching
      FG-26 §3.5's profile-local suspension.

    The ``admin``/``owner`` *role* gate is intentionally NOT enforced here — it
    stays with the route's ``require_member_admin`` so this core composes with
    read-only endpoints that may widen the role later. What this core enforces
    is the **scope + identity** property the one-process decision costs: the
    right profile, the right subject, and no owner-fallback.
    """
    entries = registry if registry is not None else get_profile_registry()
    entry = next((e for e in entries if e.name == target_profile), None)
    if entry is None or not entry.hermes_home.is_dir():
        raise ProfileScopeError(f"unknown target profile: {target_profile!r}")

    with _scoped_to(entry.hermes_home):
        store = store_factory(entry.hermes_home)
        if not subject:
            raise OwnerFallbackRefused(
                "console-routed request carried no verified subject; "
                "owner-fallback is refused on these routes"
            )
        principal = await _principal_for_subject(store, subject)
        if principal is None:
            raise ProfileScopeError(
                f"subject {subject!r} is not enrolled in profile "
                f"{target_profile!r}"
            )
        if not getattr(principal, "active", True):
            raise ProfileScopeError(
                f"subject {subject!r} is suspended in profile "
                f"{target_profile!r}"
            )
        return principal
