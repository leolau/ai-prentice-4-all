"""``hermes memory sharing`` argument contract.

The store behaviour behind these commands is covered against a real Postgres in
``tests/plugins/memory/test_memory_role_sharing_e2e.py``. What matters here is
the *surface*: a caller names who they are and never what rank they hold.
"""

from __future__ import annotations

import argparse

import pytest

from hermes_cli.memory_sharing import register_sharing_subparser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes memory")
    register_sharing_subparser(parser.add_subparsers(dest="memory_command"))
    return parser


def test_no_flag_lets_a_caller_claim_a_role() -> None:
    """The role comes from `principals`; the CLI must offer no way to assert one.

    A ``--role owner`` here would make the whole downward-read boundary
    argument-parseable, so its absence is the security property under test.
    """
    with pytest.raises(SystemExit):
        _parser().parse_args(
            ["sharing", "--as", "mia", "--role", "owner", "audit"]
        )


def test_acting_principal_is_required() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["sharing", "audit"])


def test_audit_and_share_route_with_their_arguments() -> None:
    parser = _parser()
    audit = parser.parse_args(["sharing", "--as", "mia", "audit", "--limit", "5"])
    assert (audit.sharing_command, audit.acting_as, audit.limit) == (
        "audit",
        "mia",
        5,
    )

    share = parser.parse_args(
        ["sharing", "--as", "mia", "share", "0d1f", "moe"]
    )
    assert share.sharing_command == "share"
    assert (share.memory_id, share.user_id, share.revoke) == ("0d1f", "moe", False)

    revoke = parser.parse_args(
        ["sharing", "--as", "mia", "share", "0d1f", "moe", "--revoke"]
    )
    assert revoke.revoke is True
