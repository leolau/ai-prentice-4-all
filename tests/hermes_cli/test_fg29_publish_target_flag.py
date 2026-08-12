"""On ``goal publish`` a profile name is a target, not the profile to run in.

The pre-parser scans the whole argv for ``--profile``/``-p``, so
``hermes goal publish --profile finance`` silently ran *inside* ``finance`` and
published that profile's entity goal into every other profile — the opposite of
what it reads as, under owner authority, with no error. The target flag is
``--into`` now, and the old spelling must reach argparse (which rejects it)
rather than move the whole command into another profile.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    root = tmp_path / ".hermes"
    (root / "profiles" / "finance").mkdir(parents=True, exist_ok=True)
    (root / "active_profile").write_text("default", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(sys, "argv", list(argv))

    from hermes_cli.main import _apply_profile_override

    _apply_profile_override()
    return os.environ.get("HERMES_HOME"), list(sys.argv)


def test_the_publish_target_is_not_read_as_the_profile_to_run_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = ["hermes", "goal", "publish", "--profile", "finance"]
    home, remaining = _run(tmp_path, monkeypatch, argv)

    assert home is None or not home.endswith("finance")
    assert remaining == argv


def test_the_short_spelling_is_left_alone_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = ["hermes", "goal", "publish", "-p", "finance"]
    home, remaining = _run(tmp_path, monkeypatch, argv)

    assert home is None or not home.endswith("finance")
    assert remaining == argv


def test_a_profile_chosen_before_the_subcommand_still_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, remaining = _run(
        tmp_path, monkeypatch, ["hermes", "-p", "finance", "goal", "publish"]
    )

    assert home is not None and home.endswith("finance")
    assert remaining == ["hermes", "goal", "publish"]


def test_other_goal_subcommands_keep_the_global_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, remaining = _run(
        tmp_path, monkeypatch, ["hermes", "goal", "tree", "--profile", "finance"]
    )

    assert home is not None and home.endswith("finance")
    assert remaining == ["hermes", "goal", "tree"]


def _goal_parser():
    import argparse

    from hermes_cli.goal_tree_cmd import register_goal_tree_subparser

    parser = argparse.ArgumentParser()
    register_goal_tree_subparser(parser.add_subparsers(dest="command"))
    return parser


def test_into_names_the_target_profile() -> None:
    args = _goal_parser().parse_args(
        ["goal", "publish", "--into", "finance", "--into", "ops"]
    )
    assert args.profile == ["finance", "ops"]


def test_the_old_target_spelling_is_rejected_rather_than_obeyed() -> None:
    with pytest.raises(SystemExit):
        _goal_parser().parse_args(["goal", "publish", "--profile", "finance"])
