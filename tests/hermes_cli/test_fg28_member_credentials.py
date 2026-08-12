"""FG-28 item 4 — admin-credential reads resolve through the profile scope.

The service-role key is the credential an admin console turn uses to call
GoTrue (ban/delete/reset). Reading it via ``os.environ`` would, under
one-process multiplexing, silently return the default profile's key for every
turn; routing it through :func:`get_secret` makes a scoped turn resolve its own
profile's key and an unscoped turn refuse (fail closed) rather than
authenticate as another profile. Single-profile deployments are byte-identical
— ``get_secret`` falls back to ``os.environ`` when multiplexing is off.
"""

from __future__ import annotations

import pytest

from agent.secret_scope import (
    reset_secret_scope,
    set_multiplex_active,
    set_secret_scope,
)
from hermes_cli.members import _resolve_service_role_key, _resolve_url


@pytest.fixture(autouse=True)
def _reset_multiplex() -> None:
    # Multiplex is a process-global; restore it so one test's flag cannot leak
    # into another (the same care the secret_scope module takes internally).
    set_multiplex_active(False)
    yield
    set_multiplex_active(False)


def test_service_role_key_falls_back_to_env_when_single_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Multiplex off → get_secret reads os.environ, byte-identical to the
    # pre-migration behaviour.
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "env-key")
    assert _resolve_service_role_key() == "env-key"


def test_service_role_key_resolves_from_scope_when_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The scope is authoritative — it must win over os.environ, so a turn
    # scoped to profile B never authenticates as profile A even if A's .env
    # is what loaded into os.environ at process start.
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "env-key")
    token = set_secret_scope({"SUPABASE_SERVICE_ROLE_KEY": "scoped-key"})
    try:
        assert _resolve_service_role_key() == "scoped-key"
    finally:
        reset_secret_scope(token)


def test_service_role_key_fails_closed_when_multiplex_on_and_unscoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Multiplexing on, no scope: the only honest answer is "absent" — return
    # "" so load_admin_client() -> None -> 503, refusing the action rather
    # than returning another profile's key. This is the asymmetry the doc
    # names: an unmigrated os.getenv silently leaks, get_secret fails closed.
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "env-key")
    set_multiplex_active(True)
    assert _resolve_service_role_key() == ""


def test_url_resolves_from_scope_when_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://env.example")
    token = set_secret_scope({"SUPABASE_URL": "https://scoped.example"})
    try:
        assert _resolve_url({}) == "https://scoped.example"
    finally:
        reset_secret_scope(token)
