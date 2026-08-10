"""``hermes memory projection`` reaches its handler, and the map reads one schema.

The projection parser shipped registered but undispatched: ``hermes memory
projection fit`` parsed cleanly and then fell through to the provider-status
path, so the only documented way to draw the map printed the plugin list and
exited 0. A parser without a dispatch branch is invisible to every parser test,
which is why this asserts the *routing*.
"""

from __future__ import annotations

import argparse

import pytest

from hermes_cli.main import cmd_memory
from hermes_cli.memory_projection import register_projection_subparser


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="hermes memory")
    register_projection_subparser(parser.add_subparsers(dest="memory_command"))
    return parser.parse_args(argv)


@pytest.mark.parametrize("action", ["fit", "status"])
def test_projection_actions_reach_the_projection_handler(action, monkeypatch):
    args = _parse(["projection", action])
    assert args.memory_command == "projection"
    assert args.projection_command == action

    called: list[str] = []
    monkeypatch.setattr(
        "hermes_cli.memory_projection.cmd_memory_projection",
        lambda a: called.append(getattr(a, "projection_command", None)),
    )
    cmd_memory(args)
    assert called == [action]


def test_explorer_defers_the_schema_to_the_configured_mode(monkeypatch):
    """The dashboard must read the schema the agent writes to.

    Defaulting to ``prod`` pointed the page at an empty schema on a dev-mode
    deployment — no rows, no map, and no way to tell that apart from having no
    memories.
    """
    from hermes_cli import memory_explorer

    seen: list[object] = []

    def _fake_get_store(kind, mode=None, *, source=None, config=None):
        seen.append(mode)
        return object()

    monkeypatch.setattr("hermes_cli.datastore.get_store", _fake_get_store)
    monkeypatch.setattr(
        "plugins.memory.supabase_pgvector.store.PgvectorMemoryStore",
        lambda app_store, config=None: app_store,
    )

    memory_explorer._memory_store()
    memory_explorer._memory_store("dev")
    memory_explorer._memory_store("nonsense")

    assert seen == [None, "dev", None]
