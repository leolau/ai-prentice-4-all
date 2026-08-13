"""FG-28 console routes — the wiring the unit tests cannot see.

:mod:`hermes_cli.console_scope` is right in isolation; what shipped wrong was
the *pairing* at the routes. A route that re-derives the caller in the target
profile but leaves the store on the acting profile checks authority in one
profile and takes effect in another — and a route that re-derives nothing falls
back to the target profile's **owner**, which is the one refusal FG-28 exists to
turn on.

Both are properties of the route table rather than of a handler, so they are
asserted here structurally: every ``/api/comms`` route that touches
profile-local rows must declare :func:`require_console_scope`, and every one
that binds an identity must go through the console seam rather than the legacy
resolver. That kills the bug class instead of the two instances of it.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.routing import APIRoute

from hermes_cli import web_server as ws
from hermes_cli.access import Principal
from hermes_cli.profile_registry import ProfileRegistryEntry

#: Helpers that read or write this profile's rows. A handler calling one of
#: these resolves ``HERMES_HOME`` at call time, so it is only in the target
#: profile if the scope dependency is holding it open.
_PROFILE_LOCAL_HELPERS = (
    "_comms_member_service",
    "_comms_user_service",
    "_comms_app_store",
    "_comms_console_actor",
)

#: The legacy resolver: owner-fallback by design (the machine-operator surface).
#: Correct without ``?profile=``, forbidden with it — inside another profile's
#: scope its fallback resolves to *that* profile's owner.
_LEGACY_RESOLVER = "_comms_resolve_principal"

#: The user-management console surface — the routes a profile switcher drives.
#: Deliberately a prefix set rather than a list of paths, so a route added to
#: the console tomorrow is covered without anyone remembering to add it here.
#:
#: The rest of ``/api/comms`` (goals, traces, changes, notifications, memory)
#: serves the *acting* profile and ignores ``?profile=``; making those
#: cross-profile is an open FG-28 item, and until it lands the switcher must not
#: append the parameter to them — silently serving the acting profile's rows
#: under another profile's name is the failure mode there.
_CONSOLE_PREFIXES = (
    "/api/comms/members",
    "/api/comms/directory",
    "/api/comms/whoami",
)


def _console_routes() -> list[tuple[APIRoute, str]]:
    """Every user-management console route paired with its handler's source."""
    out: list[tuple[APIRoute, str]] = []
    for route in ws.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(_CONSOLE_PREFIXES):
            continue
        out.append((route, inspect.getsource(route.endpoint)))
    return out


def _declares_console_scope(route: APIRoute) -> bool:
    return any(
        dep.call is ws.require_console_scope for dep in route.dependant.dependencies
    )


def test_every_profile_local_comms_route_declares_the_scope_dependency() -> None:
    offenders = [
        route.path
        for route, src in _console_routes()
        if any(helper + "(" in src for helper in _PROFILE_LOCAL_HELPERS)
        and not _declares_console_scope(route)
    ]
    assert offenders == [], (
        "these routes touch profile-local rows without holding the target "
        f"profile's scope, so ?profile= would take effect elsewhere: {offenders}"
    )


def test_no_comms_route_binds_an_identity_through_the_legacy_resolver_alone() -> None:
    offenders = [
        route.path
        for route, src in _console_routes()
        if _LEGACY_RESOLVER + "(" in src
    ]
    assert offenders == [], (
        "these routes bind an identity with the owner-fallback resolver; on a "
        f"?profile= request that resolves the target profile's owner: {offenders}"
    )


# -- the effect actually landing in the target profile ----------------------


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


class _Req:
    """The slice of ``Request`` the console seam reads."""

    def __init__(self, profile: str = "", *, subject: str = "hr-admin") -> None:
        self.query_params = {"profile": profile} if profile else {}
        self.state = SimpleNamespace()
        self._subject = subject


@pytest.fixture
def two_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A default + hr registry, with the caller an admin of hr only."""
    default_home = tmp_path / "default"
    hr_home = tmp_path / "hr"
    for home in (default_home, hr_home):
        home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    entries = [_entry("default", default_home), _entry("hr", hr_home)]
    monkeypatch.setattr(
        "hermes_cli.profile_registry.get_profile_registry", lambda **kw: entries
    )
    monkeypatch.setattr("hermes_cli.console_scope.get_profile_registry", lambda **kw: entries)
    monkeypatch.setattr(ws, "_comms_session_subject", lambda request: request._subject)

    async def _resolve(subject, target, *, store_factory, registry=None):
        # Stands in for the real re-resolution: admin of hr, nothing elsewhere.
        if target != "hr" or subject != "hr-admin":
            from hermes_cli.console_scope import ProfileScopeError

            raise ProfileScopeError(f"{subject!r} is not enrolled in {target!r}")
        return Principal("hr-admin", "HR Admin", "admin")

    monkeypatch.setattr("hermes_cli.console_scope.resolve_console_principal", _resolve)

    seen: dict[str, Any] = {}

    def _service(actor=None):
        from hermes_constants import get_hermes_home

        seen["home"] = Path(str(get_hermes_home()))
        return SimpleNamespace(actor=actor)

    monkeypatch.setattr(ws, "_comms_user_service", _service)
    return default_home, hr_home, seen


async def _through_scope(route_path: str, request: _Req):
    """Run ``_comms_member_service`` the way the route table would.

    Enters the route's own declared dependency (if any) around the call, so a
    missing ``Depends(require_console_scope)`` shows up here as the wrong home
    rather than as a passing test.
    """
    route = next(
        r
        for r in ws.app.routes
        if isinstance(r, APIRoute) and r.path == route_path
    )
    if not _declares_console_scope(route):
        return await ws._comms_member_service(request)
    agen = ws.require_console_scope(request)
    await agen.__anext__()
    try:
        return await ws._comms_member_service(request)
    finally:
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()


@pytest.mark.parametrize(
    "path",
    [
        "/api/comms/members",
        "/api/comms/members/{user_id}/deactivate",
        "/api/comms/members/{user_id}/activate",
        "/api/comms/members/{user_id}",
        "/api/comms/members/{user_id}/channels",
    ],
)
def test_a_write_route_builds_its_service_in_the_target_profile(two_homes, path) -> None:
    default_home, hr_home, seen = two_homes
    asyncio.run(_through_scope(path, _Req("hr")))
    assert seen["home"] == hr_home, (
        f"{path} authorised the caller in 'hr' but built its service in "
        f"{seen['home'].name!r} — authority in one profile, effect in another"
    )
    assert seen["home"] != default_home


def test_a_console_routed_request_with_no_subject_is_401_not_the_owner(
    two_homes,
) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_through_scope("/api/comms/members/{user_id}", _Req("hr", subject="")))
    assert excinfo.value.status_code == 401


def test_a_caller_with_no_row_in_the_target_profile_is_403(two_homes) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            _through_scope("/api/comms/members", _Req("hr", subject="stranger"))
        )
    assert excinfo.value.status_code == 403


def test_no_profile_param_leaves_the_legacy_path_byte_identical(two_homes) -> None:
    default_home, _hr_home, seen = two_homes

    async def _legacy(request, *, allow_as=False):
        return Principal("op", "Operator", "owner")

    ws_legacy = ws._comms_resolve_principal
    try:
        ws._comms_resolve_principal = _legacy
        actor, _service = asyncio.run(ws._comms_member_service(_Req()))
    finally:
        ws._comms_resolve_principal = ws_legacy
    assert actor.user_id == "op"
    assert seen["home"] == default_home
