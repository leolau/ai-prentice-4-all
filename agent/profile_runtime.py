"""One canonical per-turn profile scope, shared by every multi-profile surface.

A profile *is* a ``HERMES_HOME``: its ``config.yaml``, ``SOUL.md``, memory,
sessions and ``.env`` all hang off that directory. A process that serves more
than one profile — the multiplexing gateway, and the dashboard driving an
agent-home chat turn for a chosen profile — must therefore redirect two seams
together for the duration of a turn:

1. ``set_hermes_home_override`` — ``get_hermes_home()`` (config, SOUL, memory,
   sessions) resolves to the profile's home.
2. ``set_secret_scope`` — :func:`agent.secret_scope.get_secret` reads that
   profile's ``.env`` instead of the process-global ``os.environ``, which in a
   multi-profile process may hold a *different* profile's values.

Both are contextvars, so the scope propagates into the agent's worker thread
via ``copy_context()`` and is entered per turn rather than per process. Loading
the ``.env`` here does **not** mutate ``os.environ`` — the mapping is isolated,
which is what keeps spawned subprocesses (MCP servers, kanban workers) from
inheriting another profile's credentials.

Two known limits, identical on every caller, so a fix lands once:

- ``tools.skills_tool`` / ``tools.skill_manager_tool`` bind ``SKILLS_DIR`` at
  import time, so skills still resolve to the process's own home.
- A credential read that still calls ``os.getenv`` directly bypasses the secret
  scope silently; only migrated ``get_secret`` call sites are covered.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union


@contextmanager
def profile_runtime_scope(profile_home: Union[Path, str]) -> Iterator[Path]:
    """Scope config/SOUL/memory/sessions AND credentials to one profile.

    Yields the resolved profile home. Restores the previous scope on exit,
    including on exception, so a failed turn cannot leave the process pointed
    at another profile.
    """
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import (
        build_profile_secret_scope,
        set_secret_scope,
        reset_secret_scope,
    )

    home = Path(profile_home)
    home_token = set_hermes_home_override(str(home))
    secret_token = set_secret_scope(build_profile_secret_scope(home))
    try:
        yield home
    finally:
        reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


__all__ = ["profile_runtime_scope"]
