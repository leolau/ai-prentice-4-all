"""FG-28 task 1: does a multiplexed process resolve one profile's credentials
for another, through paths that read ``os.environ`` directly?

These drive the real files-on-disk paths — a profile home with its own ``.env``
and ``config.yaml``, entered through ``profile_runtime_scope`` — rather than the
already-migrated ``get_secret`` seams covered by
``test_multiplex_credential_isolation.py``. Each class below is a leak that was
reproduced before it was closed.
"""
import importlib
import os
import subprocess
import sys

import pytest

from agent.profile_runtime import profile_runtime_scope


def _ss():
    """The live ``agent.secret_scope`` module.

    Resolved per call rather than bound at import: other suites reload shared
    modules, and production code imports this one inside the function bodies, so
    a module-level alias here can end up setting the multiplex flag on an object
    nothing reads.
    """
    return importlib.import_module("agent.secret_scope")


@pytest.fixture(autouse=True)
def _multiplex_off():
    _ss().set_multiplex_active(False)
    yield
    _ss().set_multiplex_active(False)


def _make_profile(root, tag, config_yaml):
    home = root / f"profile-{tag}"
    home.mkdir(parents=True)
    (home / ".env").write_text(
        f"TELEGRAM_BOT_TOKEN=111:bot-{tag}\n"
        f"ANTHROPIC_API_KEY=key-{tag}\n"
        f"DATABASE_URL=postgresql://u:pw@127.0.0.1:5432/db-{tag}\n"
        f"SUPABASE_SERVICE_ROLE_KEY=srk-{tag}\n"
    )
    (home / "config.yaml").write_text(config_yaml)
    return home


_DATASTORE_CONFIG = (
    "datastore:\n"
    "  supabase_app:\n"
    "    dsn: ${DATABASE_URL}\n"
    "    service_role_key: ${SUPABASE_SERVICE_ROLE_KEY}\n"
)

_TELEGRAM_CONFIG = (
    "platforms:\n"
    "  telegram:\n"
    "    enabled: true\n"
    "    token: ${TELEGRAM_BOT_TOKEN}\n"
)


class TestConfigEnvRefExpansion:
    """``${VAR}`` in a profile's config.yaml resolves against that profile."""

    def _load_datastore(self, home):
        from hermes_cli.config import load_config

        with profile_runtime_scope(home):
            cfg = load_config()
        return (cfg.get("datastore") or {}).get("supabase_app") or {}

    def test_each_profile_expands_its_own_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@127.0.0.1:5432/db-process")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srk-process")
        _ss().set_multiplex_active(True)

        a = _make_profile(tmp_path, "a", _DATASTORE_CONFIG)
        b = _make_profile(tmp_path, "b", _DATASTORE_CONFIG)

        app_a = self._load_datastore(a)
        app_b = self._load_datastore(b)

        assert app_a["dsn"].endswith("/db-a")
        assert app_a["service_role_key"] == "srk-a"
        assert app_b["dsn"].endswith("/db-b")
        assert app_b["service_role_key"] == "srk-b"

    def test_cache_is_keyed_on_the_scope_not_only_the_file(self, tmp_path, monkeypatch):
        """An unscoped load of a profile's config must not poison its turn.

        ``load_config`` caches the *expanded* result per config path, so without
        the scope in the cache key an unscoped read (``hermes status`` listing
        every profile) would leave the process-env expansion in the cache and
        the profile's next turn would be served those values.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@127.0.0.1:5432/db-process")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srk-process")
        _ss().set_multiplex_active(True)

        a = _make_profile(tmp_path, "a", _DATASTORE_CONFIG)

        from hermes_cli.config import load_config
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override

        token = set_hermes_home_override(str(a))
        try:
            unscoped = load_config()
        finally:
            reset_hermes_home_override(token)
        assert (unscoped["datastore"]["supabase_app"]["dsn"]).endswith("/db-process")

        assert self._load_datastore(a)["dsn"].endswith("/db-a")

    def test_unscoped_expansion_still_reads_environ(self, tmp_path, monkeypatch):
        """Single-profile behaviour is unchanged: no scope, no new failure."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@127.0.0.1:5432/db-process")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srk-process")
        a = _make_profile(tmp_path, "a", _DATASTORE_CONFIG)

        from hermes_cli.config import load_config
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override

        token = set_hermes_home_override(str(a))
        try:
            cfg = load_config()
        finally:
            reset_hermes_home_override(token)
        assert cfg["datastore"]["supabase_app"]["dsn"].endswith("/db-process")

    def test_unset_reference_keeps_its_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        _ss().set_multiplex_active(True)
        a = _make_profile(tmp_path, "a", "datastore:\n  supabase_app:\n    dsn: ${NOT_SET_ANYWHERE}\n")
        assert self._load_datastore(a)["dsn"] == "${NOT_SET_ANYWHERE}"


class TestGatewayPlatformTokens:
    """A profile's adapter is built with that profile's bot token."""

    def _telegram_token(self, home):
        from gateway.config import Platform, load_gateway_config

        with profile_runtime_scope(home):
            cfg = load_gateway_config()
            platform = cfg.platforms.get(Platform.TELEGRAM)
            return getattr(platform, "token", None)

    def test_env_override_does_not_win_over_the_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:bot-process")
        _ss().set_multiplex_active(True)

        a = _make_profile(tmp_path, "a", _TELEGRAM_CONFIG)
        b = _make_profile(tmp_path, "b", _TELEGRAM_CONFIG)

        assert self._telegram_token(a) == "111:bot-a"
        assert self._telegram_token(b) == "111:bot-b"

    def test_env_override_still_applies_without_a_scope(self, tmp_path, monkeypatch):
        """The override is the documented single-profile behaviour; keep it."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:bot-process")
        a = _make_profile(tmp_path, "a", _TELEGRAM_CONFIG)

        from gateway.config import Platform, load_gateway_config
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override

        token = set_hermes_home_override(str(a))
        try:
            cfg = load_gateway_config()
        finally:
            reset_hermes_home_override(token)
        assert cfg.platforms[Platform.TELEGRAM].token == "999:bot-process"


class TestAnthropicTokenResolution:
    """The provider fallback path reads the scope, not the process env."""

    def test_resolve_reads_the_active_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key-process")
        monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        _ss().set_multiplex_active(True)

        from agent.anthropic_adapter import resolve_anthropic_token

        tok = _ss().set_secret_scope({"ANTHROPIC_API_KEY": "key-a"})
        try:
            assert resolve_anthropic_token() == "key-a"
        finally:
            _ss().reset_secret_scope(tok)

    def test_resolve_fails_closed_unscoped(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key-process")
        monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        _ss().set_multiplex_active(True)

        from agent.anthropic_adapter import resolve_anthropic_token

        with pytest.raises(_ss().UnscopedSecretError):
            resolve_anthropic_token()


class TestDotenvNeverReachesTheProcessEnvironment:
    """Nothing may write a profile's .env into os.environ while multiplexing."""

    def test_load_hermes_dotenv_is_refused(self, tmp_path, monkeypatch):
        from hermes_cli.env_loader import load_hermes_dotenv

        a = _make_profile(tmp_path, "a", _DATASTORE_CONFIG)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _ss().set_multiplex_active(True)

        assert load_hermes_dotenv(hermes_home=str(a)) == []
        assert "ANTHROPIC_API_KEY" not in os.environ

    def test_load_hermes_dotenv_still_loads_single_profile(self, tmp_path, monkeypatch):
        from hermes_cli.env_loader import load_hermes_dotenv

        a = _make_profile(tmp_path, "a", _DATASTORE_CONFIG)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        loaded = load_hermes_dotenv(hermes_home=str(a))
        assert loaded == [a / ".env"]
        assert os.environ["ANTHROPIC_API_KEY"] == "key-a"


class TestSubprocessInheritance:
    """Documents the remaining gap: a child process inherits the process env.

    ``profile_runtime_scope`` deliberately does not mutate ``os.environ``, so a
    subprocess started inside profile A's turn inherits whatever the *process*
    was started with — the default profile's credentials, not A's. Asserted so
    the property is stated rather than assumed, and so closing it (passing a
    scoped env to every spawn) has a test to flip.
    """

    def test_child_sees_the_process_env_not_the_scope(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key-process")
        _ss().set_multiplex_active(True)
        a = _make_profile(tmp_path, "a", _DATASTORE_CONFIG)

        with profile_runtime_scope(a):
            out = subprocess.run(
                [sys.executable, "-c", "import os;print(os.environ.get('ANTHROPIC_API_KEY'))"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        assert out == "key-process"
