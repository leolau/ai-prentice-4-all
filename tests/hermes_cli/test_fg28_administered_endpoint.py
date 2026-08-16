"""FG-28 — `GET /api/profiles/administered` — the switcher's feed, tested as a
route handler.

The chokepoint core (:mod:`hermes_cli.console_scope`) is covered in
``test_fg28_console_scope.py``; this file holds the route that wraps it. Two
properties land here that nowhere else can see:

1. **Owner-fallback is refused on this route too** — even though the route
   declares no ``require_console_scope`` (it has no single target profile),
   it sits on the console path and naming one profile's owner as "the set of
   profiles you may administer" is the exact escalation §"The most dangerous
   hole" describes. A request with no verified subject is 401, never the
   owner.
2. **The route filters the registry by :func:`administered_profiles` and
   augments each entry with :func:`probe_registry_health`** — so a switcher
   built on it cannot render one profile's rows under another's name, and
   cannot route to a profile whose schema is claimed by another without
   badging it first.

The syntheticProtocol store is enough: the cross-profile iteration lives in
``administered_profiles`` and is tested for real on the box by
``scripts/fg28_systest.py``. Here we stub only the seam that pairs the route
to it, which is where the route's own risk lives.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from hermes_cli import web_server as ws
from hermes_cli.profile_registry import ProfileRegistryEntry


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
    """The slice of ``Request`` the route reads (no FastAPI app needed)."""

    def __init__(self, *, subject: str = "cto") -> None:
        self.state = SimpleNamespace()
        self._subject = subject

    @property
    def query_params(self) -> dict:
        return {}


@pytest.fixture
def three_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """default + engineers + hr, with the caller an admin of default+engineers."""
    default_home = tmp_path / "default"
    engineers_home = tmp_path / "engineers"
    hr_home = tmp_path / "hr"
    for home in (default_home, engineers_home, hr_home):
        home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    entries = [
        _entry("default", default_home),
        _entry("engineers", engineers_home),
        _entry("hr", hr_home),
    ]
    monkeypatch.setattr(
        "hermes_cli.profile_registry.get_profile_registry", lambda **kw: entries
    )
    # The route reads the subject directly, bypassing the legacy resolver.
    monkeypatch.setattr(ws, "_comms_session_subject", lambda request: request._subject)

    async def _administered(subject, *, store_factory, registry=None):
        # Stands in for the cross-profile re-derivation: CTO admins default
        # and engineers, not hr. Mirrors the live box's principal distribution.
        if subject == "cto":
            return ["default", "engineers"]
        return []

    monkeypatch.setattr(
        "hermes_cli.console_scope.administered_profiles", _administered
    )
    return entries


def _run(endpoint, request):
    return asyncio.run(endpoint(request))


def test_a_request_with_no_subject_is_401(three_homes) -> None:
    """Owner-fallback refused: no subject → 401, not the enrolled owner.

    This route sits on the console path even though it has no target profile
    to scope to, so the owner-fallback refusal the FG-28 architecture hinges
    on must apply here too. Naming one profile's owner as "the set of
    profiles you may administer" is the same hole §"The most dangerous hole"
    describes.
    """
    with pytest.raises(HTTPException) as excinfo:
        _run(ws.list_administered_profiles_endpoint, _Req(subject=""))
    assert excinfo.value.status_code == 401
    assert "owner-fallback" in excinfo.value.detail


def test_returns_only_profiles_the_caller_administers(three_homes) -> None:
    """The route filters the registry by `administered_profiles`.

    CTO holds an admin row in default and engineers, not in hr — so hr must
    not appear, even though it is in the registry and the gateway serves it.
    The picker this feeds is a routing hint, never a grant.
    """
    resp = _run(ws.list_administered_profiles_endpoint, _Req(subject="cto"))
    names = [p["name"] for p in resp["profiles"]]
    assert names == ["default", "engineers"], names
    assert "hr" not in names


def test_each_entry_carries_registry_metadata(three_homes) -> None:
    """The route augments administered names with the registry's metadata.

    The switcher renders base_url, schema, served and the is_default marker —
    without them it could not label "this is your current brain" vs "switch to
    engineers under /p/engineers/".
    """
    resp = _run(ws.list_administered_profiles_endpoint, _Req(subject="cto"))
    by_name = {p["name"]: p for p in resp["profiles"]}
    assert by_name["default"]["is_default"] is True
    assert by_name["default"]["base_url"] == ""
    assert by_name["engineers"]["is_default"] is False
    assert by_name["engineers"]["base_url"] == "/p/engineers/"
    assert by_name["engineers"]["schema"] == "app_prod_engineers"
    assert by_name["engineers"]["served"] is True


def test_each_entry_carries_probed_health(three_homes, monkeypatch: pytest.MonkeyPatch) -> None:
    """`probe_registry_health` runs on each administered entry.

    A `claimed-by-other` profile would fail closed on connect — the switcher
    has to badge it before the user opens a turn there. The probe is the
    single source the switcher trusts, so the route must run it rather than
    hand the switcher the pre-probe `HEALTH_UNKNOWN`.
    """
    def _probe(entry: ProfileRegistryEntry) -> ProfileRegistryEntry:
        from dataclasses import replace
        if entry.name == "engineers":
            return replace(
                entry,
                health="claimed-by-other",
                health_detail="schema app_prod_engineers owned by hr",
            )
        from hermes_cli.profile_registry import HEALTH_OK
        return replace(entry, health=HEALTH_OK, health_detail="")

    monkeypatch.setattr(
        "hermes_cli.profile_registry.probe_registry_health", _probe
    )

    resp = _run(ws.list_administered_profiles_endpoint, _Req(subject="cto"))
    by_name = {p["name"]: p for p in resp["profiles"]}
    assert by_name["default"]["health"] == "ok"
    assert by_name["engineers"]["health"] == "claimed-by-other"
    assert (
        by_name["engineers"]["health_detail"]
        == "schema app_prod_engineers owned by hr"
    )


def test_comms_not_configured_is_503_not_500(
    three_homes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unconfigured datastore surfaces as 503, not a stack trace.

    `administered_profiles` reads through `_comms_app_store`, which raises
    `_CommsNotConfigured` when no Supabase app datastore is configured. The
    route translates that into 503 — a deployment state the switcher renders
    rather than a server error it cannot.
    """

    async def _raise(subject, *, store_factory, registry=None):
        raise ws._CommsNotConfigured("no app datastore")

    monkeypatch.setattr(
        "hermes_cli.console_scope.administered_profiles", _raise
    )
    with pytest.raises(HTTPException) as excinfo:
        _run(ws.list_administered_profiles_endpoint, _Req(subject="cto"))
    assert excinfo.value.status_code == 503
    assert "no app datastore" in excinfo.value.detail


def test_administered_with_no_admin_rows_is_empty(
    three_homes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller with admin rows nowhere administers nothing.

    The endpoint never reads the legacy owner — a non-admin sees an empty
    list rather than the box-wide owner's profile set.
    """

    async def _empty(subject, *, store_factory, registry=None):
        return []

    monkeypatch.setattr(
        "hermes_cli.console_scope.administered_profiles", _empty
    )
    resp = _run(ws.list_administered_profiles_endpoint, _Req(subject="someone"))
    assert resp == {"profiles": []}