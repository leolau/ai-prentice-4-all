"""``--clone-all`` against a source it cannot fully read.

Found on ``hermes-systest``: two paths inside the hermes home were owned by
root, left by an earlier root-run command, so ``shutil.copytree`` died partway
through with a traceback and left a half-made profile on disk — which then
made every retry fail with "profile already exists".

The copy is either whole or it does not start.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cli import profiles
from hermes_cli.profiles import (
    _clone_all_copytree_ignore,
    create_profile,
    unreadable_clone_sources,
)

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads everything, so nothing can be unreadable"
)


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    profile = tmp_path / "profiles" / "work"
    (profile / "sessions").mkdir(parents=True)
    profile.joinpath("config.yaml").write_text("model: x")
    profile.joinpath("secret.bak").write_text("unreadable by the running user")
    profile.joinpath("sessions", "old.json").write_text("history")
    return profile


def test_an_unreadable_file_is_named_before_anything_is_copied(source: Path) -> None:
    source.joinpath("secret.bak").chmod(0o000)

    blocked = unreadable_clone_sources(source, _clone_all_copytree_ignore(source))

    assert blocked == [source / "secret.bak"]


def test_a_file_the_clone_would_skip_anyway_is_not_a_blocker(source: Path) -> None:
    """``sessions/`` never travels, so its permissions cannot stop a clone."""
    source.joinpath("sessions", "old.json").chmod(0o000)

    assert unreadable_clone_sources(source, _clone_all_copytree_ignore(source)) == []


def test_clone_all_refuses_by_name_instead_of_dying_mid_copy(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source.joinpath("secret.bak").chmod(0o000)
    destination = tmp_path / "profiles" / "clone"
    monkeypatch.setattr(
        profiles,
        "get_profile_dir",
        lambda name: source if name == "work" else destination,
    )

    with pytest.raises(PermissionError) as raised:
        create_profile(
            name="clone",
            clone_from="work",
            clone_all=True,
            no_alias=True,
            verify_datastore=False,
        )

    assert "secret.bak" in str(raised.value)
    # The half-made profile was the second half of the defect: it exists, so
    # the retry refuses, and it looks like a profile that merely cloned badly.
    assert not destination.exists()


def test_a_readable_source_still_clones(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "profiles" / "clone"
    monkeypatch.setattr(
        profiles,
        "get_profile_dir",
        lambda name: source if name == "work" else destination,
    )

    create_profile(
        name="clone",
        clone_from="work",
        clone_all=True,
        no_alias=True,
        verify_datastore=False,
    )

    assert (destination / "config.yaml").read_text() == "model: x"
    assert not (destination / "sessions" / "old.json").exists()
