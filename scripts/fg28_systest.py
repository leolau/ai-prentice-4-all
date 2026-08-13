#!/usr/bin/env python3
"""FG-28 system test: prove cross-profile refusals live on the systest box.

One process, two profiles (default + maintenance), schema-isolated principal
tables.  Exercises the real chokepoint — ``resolve_console_principal`` — against
the real Postgres ``principals`` tables in ``app_prod`` and
``app_prod_maintenance``.

Run on the box:

    cd /opt/data/hermes-agent
    sudo -u hermes -H env HERMES_HOME=/opt/data/hermes-home-staging \\
        ./.venv/bin/python /tmp/fg28_systest.py

The script is self-contained: it loads the dotenv from HERMES_HOME, builds a
``store_factory`` that resolves through the active scope (just like
``_comms_app_store`` in web_server.py), and runs six assertions.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

# ── Environment ─────────────────────────────────────────────────────────────
os.environ.setdefault("HERMES_HOME", "/opt/data/hermes-home-staging")

from hermes_cli.env_loader import load_hermes_dotenv

load_hermes_dotenv()

# ── Imports (after dotenv so DATABASE_URL is in env) ───────────────────────
from hermes_cli.config import load_config
from hermes_cli.access import PrincipalStore
from hermes_cli.console_scope import (
    OwnerFallbackRefused,
    ProfileScopeError,
    administered_profiles,
    resolve_console_principal,
)
from hermes_cli.datastore import get_store
from hermes_cli.profile_registry import get_profile_registry

# ── Test subjects ───────────────────────────────────────────────────────────
# leo_owner is enrolled in BOTH profiles (owner in default, owner in maintenance).
# The member below is enrolled ONLY in the default profile's principals table.
LEO_OWNER = "leo_owner"
MEMBER_DEFAULT_ONLY = "bae2aabf-43c5-42fd-85ac-54b5717f0a18"

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    results.append((name, passed, detail))
    print(line)


def store_factory(home):
    """Build a PrincipalStore over the currently-scoped profile's store.

    Mirrors ``_comms_app_store()`` in web_server.py: load_config() respects the
    context-local HERMES_HOME override that _scoped_to sets, and get_store
    derives the schema from active_profile_slug() — so the store points at the
    right schema (app_prod vs app_prod_maintenance) for whichever profile's
    scope is active.
    """
    config = load_config() or {}
    store = get_store("supabase-app", "prod", config=config)
    return PrincipalStore(store)


async def main() -> None:
    # ── Show what we're working with ────────────────────────────────────────
    registry = get_profile_registry()
    print("=== Profile Registry ===")
    for entry in registry:
        print(f"  {entry.name}: home={entry.hermes_home}, served={entry.served}")
    print()

    # ── Test 1: member (default-only) → maintenance → ProfileScopeError ─────
    print("--- T1: member enrolled only in default → resolve in maintenance")
    try:
        principal = await resolve_console_principal(
            MEMBER_DEFAULT_ONLY, "maintenance", store_factory=store_factory
        )
        record(
            "T1: cross-profile refusal (member→maintenance)",
            False,
            f"expected ProfileScopeError, got principal={principal}",
        )
    except ProfileScopeError as exc:
        record("T1: cross-profile refusal (member→maintenance)", True, str(exc))
    except Exception as exc:
        record(
            "T1: cross-profile refusal (member→maintenance)",
            False,
            f"expected ProfileScopeError, got {type(exc).__name__}: {exc}\n"
            + traceback.format_exc(),
        )

    # ── Test 2: no subject → maintenance → OwnerFallbackRefused ─────────────
    print("--- T2: no subject → maintenance → expect OwnerFallbackRefused")
    try:
        principal = await resolve_console_principal(
            "", "maintenance", store_factory=store_factory
        )
        record(
            "T2: no-subject refusal (→maintenance)",
            False,
            f"expected OwnerFallbackRefused, got principal={principal}",
        )
    except OwnerFallbackRefused as exc:
        record("T2: no-subject refusal (→maintenance)", True, str(exc))
    except Exception as exc:
        record(
            "T2: no-subject refusal (→maintenance)",
            False,
            f"expected OwnerFallbackRefused, got {type(exc).__name__}: {exc}\n"
            + traceback.format_exc(),
        )

    # ── Test 3: nonexistent profile → ProfileScopeError ─────────────────────
    print("--- T3: nonexistent profile → expect ProfileScopeError")
    try:
        principal = await resolve_console_principal(
            LEO_OWNER, "nonexistent", store_factory=store_factory
        )
        record(
            "T3: unknown-profile refusal",
            False,
            f"expected ProfileScopeError, got principal={principal}",
        )
    except ProfileScopeError as exc:
        record("T3: unknown-profile refusal", True, str(exc))
    except Exception as exc:
        record(
            "T3: unknown-profile refusal",
            False,
            f"expected ProfileScopeError, got {type(exc).__name__}: {exc}\n"
            + traceback.format_exc(),
        )

    # ── Test 4: member → default → resolved (positive control) ─────────────
    print("--- T4: member → default → expect resolved principal")
    try:
        principal = await resolve_console_principal(
            MEMBER_DEFAULT_ONLY, "default", store_factory=store_factory
        )
        ok = (
            principal is not None
            and getattr(principal, "user_id", "") == MEMBER_DEFAULT_ONLY
        )
        record(
            "T4: positive control (member→default)",
            ok,
            f"role={getattr(principal, 'role', '?')}, "
            f"active={getattr(principal, 'active', '?')}",
        )
    except Exception as exc:
        record(
            "T4: positive control (member→default)",
            False,
            f"unexpected {type(exc).__name__}: {exc}\n" + traceback.format_exc(),
        )

    # ── Test 5: leo_owner → maintenance → resolved (positive control) ──────
    # Same user IS in both profiles — the point is that the same subject
    # resolves to a DIFFERENT principal row (different schema, different role
    # record) depending on which profile scope is active.
    print("--- T5: leo_owner → maintenance → expect resolved principal")
    try:
        principal = await resolve_console_principal(
            LEO_OWNER, "maintenance", store_factory=store_factory
        )
        ok = (
            principal is not None
            and getattr(principal, "user_id", "") == LEO_OWNER
        )
        record(
            "T5: positive control (leo_owner→maintenance)",
            ok,
            f"role={getattr(principal, 'role', '?')}, "
            f"active={getattr(principal, 'active', '?')}",
        )
    except Exception as exc:
        record(
            "T5: positive control (leo_owner→maintenance)",
            False,
            f"unexpected {type(exc).__name__}: {exc}\n"
            + traceback.format_exc(),
        )

    # ── Test 6: administered_profiles(leo_owner) → both profiles ───────────
    print("--- T6: administered_profiles(leo_owner) → expect both")
    try:
        administered = await administered_profiles(
            LEO_OWNER, store_factory=store_factory
        )
        ok = "default" in administered and "maintenance" in administered
        record(
            "T6: administered_profiles shows both",
            ok,
            f"administered={administered}",
        )
    except Exception as exc:
        record(
            "T6: administered_profiles shows both",
            False,
            f"unexpected {type(exc).__name__}: {exc}\n"
            + traceback.format_exc(),
        )

    # ── Summary ─────────────────────────────────────────────────────────────
    print()
    print("=== Summary ===")
    total = len(results)
    passed = sum(1 for _, p, _ in results if p)
    print(f"{passed}/{total} passed")
    if passed != total:
        print("FAILURES:")
        for name, p, detail in results:
            if not p:
                print(f"  - {name}: {detail}")
        sys.exit(1)
    else:
        print("ALL PASSED — cross-profile refusals are live.")


if __name__ == "__main__":
    asyncio.run(main())
