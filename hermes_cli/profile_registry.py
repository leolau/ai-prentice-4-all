"""FG-28 control-plane profile registry — the list of profiles this box
serves and how to reach each one, for the multi-profile admin console.

This is a **derived view**, not a second source of truth. It composes the
existing chokepoints — :func:`profiles_to_serve` for the served set,
:func:`list_profiles` for per-profile detail, :func:`app_schema` for the
derived schema — and adds the four things a multi-profile console needs that
no single profile knows on its own:

- **served** — is this profile in the multiplexed served set?
- **base_url** — the path prefix the one-gateway consolidation routes it under
  (``/p/<profile>/``; the default profile owns the listener and has none);
- **schema** — the derived ``app_prod[_<profile>]`` name, for display. The
  authority is the ``schema_owner`` claim FG-27 verifies on connect;
- **health** — a live probe (:func:`probe_registry_health`) that reads the
  ``schema_owner`` marker via :func:`describe_binding`, so the console
  switcher can badge a profile whose schema is unclaimed or claimed by
  another profile *before* routing an admin turn there.

It holds **no authority data**: who may administer which profile stays in each
profile's own ``principals`` table, so there is nothing here to keep in sync
and no second place to get wrong. See FG-28 §"Profile registry" and
§"Architecture decision (2026-08-13)".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import List

from hermes_cli.datastore import app_schema
from hermes_cli.profiles import list_profiles, profiles_to_serve

#: Health states a registry entry can be in. ``HEALTH_UNKNOWN`` is the
#: pre-probe value; :func:`probe_registry_health` replaces it.
HEALTH_UNKNOWN = "unknown"
HEALTH_OK = "ok"
#: No app datastore configured — the profile is core-only (SQLite in its own
#: HERMES_HOME) and cannot host the ``principals`` table a console administers.
HEALTH_CORE_ONLY = "core-only"
#: DB configured but not contactable. FG-27 Layer 1 still fails closed on the
#: first agent turn; the registry reports it rather than guessing.
HEALTH_UNREACHABLE = "unreachable"
#: Schema exists with no ``schema_owner`` marker — adopted on first connect.
#: A brand-new profile that has not run a turn yet.
HEALTH_UNCLAIMED = "unclaimed"
#: FG-27's backstop: this profile's derived schema is owned by another
#: profile. A console turn scoped here would fail closed on connect; badge it
#: now rather than let an admin hit the error mid-action.
HEALTH_CLAIMED_BY_OTHER = "claimed-by-other"


@dataclass(frozen=True)
class ProfileRegistryEntry:
    """One profile as the control plane sees it."""

    name: str
    hermes_home: Path
    is_default: bool
    #: Whether :func:`profiles_to_serve` includes this profile under multiplex.
    served: bool
    #: Path prefix the one-gateway consolidation routes this profile under.
    #: ``""`` for the default profile (it owns the listener) and for profiles
    #: not in the served set.
    base_url: str
    #: Derived production schema name (display only). Authority is the
    #: ``schema_owner`` marker FG-27 verifies on connect.
    schema: str
    has_env: bool
    gateway_running: bool
    health: str = HEALTH_UNKNOWN
    #: Human-readable detail for a non-:data:`HEALTH_OK` state (e.g. the
    #: claiming profile slug, or the connection error). Empty until probed.
    health_detail: str = ""


def _base_url_for(name: str, is_default: bool, served: bool) -> str:
    """The path prefix the one-gateway consolidation routes a profile under.

    The default profile owns the single shared listener and serves the root;
    secondary profiles hang off ``/p/<profile>/`` (see ``gateway/run.py`` and
    ``gateway/platforms/webhook.py`` — port-binding platforms are a hard
    startup error for a secondary profile, and the default serves the rest
    under that prefix). A profile not in the served set is not reachable
    through the gateway at all.
    """
    if not served or is_default:
        return ""
    return f"/p/{name}/"


def get_profile_registry() -> List[ProfileRegistryEntry]:
    """Return every profile this box knows about, as the console sees it.

    Composes :func:`list_profiles` (per-profile detail) with
    :func:`profiles_to_serve` (the multiplexed served set) and :func:`app_schema`
    (the derived schema). Makes **no database calls**: ``health`` is
    :data:`HEALTH_UNKNOWN` until :func:`probe_registry_health` is asked. Use
    this for the switcher list and for ``hermes profile registry list``.
    """
    # Always read the full multiplexed set: the registry's purpose is to show
    # every profile the gateway *would* serve, not just the active one.
    serve = {name for name, _home in profiles_to_serve(multiplex=True)}

    entries: List[ProfileRegistryEntry] = []
    for p in list_profiles():
        served = p.name in serve
        entries.append(
            ProfileRegistryEntry(
                name=p.name,
                hermes_home=p.path,
                is_default=p.is_default,
                served=served,
                base_url=_base_url_for(p.name, p.is_default, served),
                schema=app_schema("prod", profile=p.name),
                has_env=p.has_env,
                gateway_running=p.gateway_running,
            )
        )
    return entries


def probe_registry_health(entry: ProfileRegistryEntry) -> ProfileRegistryEntry:
    """Replace ``entry.health`` with a live probe of its app-datastore binding.

    Reuses :func:`describe_binding` (FG-27 Layer 2), which resolves the
    profile's DSN through its own ``.env`` and reads the ``schema_owner``
    marker — the same lookup a clone performs at creation time, so the
    registry cannot disagree with the fail-closed check on connect.
    """
    from hermes_cli.datastore_binding import describe_binding

    if not entry.hermes_home.is_dir():
        return replace(
            entry, health=HEALTH_UNREACHABLE, health_detail="profile home missing"
        )

    report = describe_binding(entry.name, source_home=entry.hermes_home)

    if not report.configured:
        return replace(entry, health=HEALTH_CORE_ONLY, health_detail="no app datastore")
    if report.unverified is not None:
        return replace(
            entry, health=HEALTH_UNREACHABLE, health_detail=report.unverified
        )

    conflicts = report.conflicts
    if conflicts:
        claim = conflicts[0]
        detail = f"schema {claim.schema} owned by profile {claim.claimed_by!r}"
        return replace(entry, health=HEALTH_CLAIMED_BY_OTHER, health_detail=detail)

    # No conflicts. Distinguish "all-mine" from "unclaimed" (adopted on first
    # connect) so the switcher can warn a brand-new profile has not been
    # initialised yet — its first admin turn would create the schema.
    unclaimed = [c for c in report.claims if c.claimed_by is None]
    if unclaimed:
        schemas = ", ".join(c.schema for c in unclaimed)
        return replace(
            entry, health=HEALTH_UNCLAIMED, health_detail=f"no owner marker: {schemas}"
        )
    return replace(entry, health=HEALTH_OK, health_detail="")


def probe_registry(entries: List[ProfileRegistryEntry]) -> List[ProfileRegistryEntry]:
    """Probe health for every entry in a registry list."""
    return [probe_registry_health(e) for e in entries]
