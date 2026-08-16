"""Erasing, deleting and cloning curated memory in FG-24's layout.

Three maintenance paths were written against the pre-FG-24 tier — two files,
``memories/MEMORY.md`` and ``memories/USER.md`` — and never revisited when the
tier became per participation and per person:

* ``hermes memory reset`` deleted those two files under the words "permanently
  erase" and "a blank slate", while every ``memories/users/<uid>/MEMORY.md``
  and ``persons/<uid>/USER.md`` — the ones a session actually reads — survived.
* deleting a member resolved their Postgres rows and left the files, so a
  person removed from the console kept being described to the agent by name.
* ``--clone-all`` copied ``memories/`` whole, handing one profile's record of
  its people to a profile that enrols different ones.

These run against a real temporary ``HERMES_HOME`` with real files: the claim
is about what is on disk afterwards, and a mocked filesystem cannot make it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

import pytest

from tools import memory_tool
from tools.memory_tool import curated_memory_files, person_memory_files_on_box


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A profile home under ``<root>/profiles/work``, populated for two people."""
    root = tmp_path / "hermes"
    profile = root / "profiles" / "work"
    (profile / "memories" / "users" / "ana").mkdir(parents=True)
    (profile / "memories" / "users" / "ben").mkdir(parents=True)
    (root / "persons" / "ana").mkdir(parents=True)
    (root / "persons" / "ben").mkdir(parents=True)
    (profile / "memories" / "MEMORY.md").write_text("shared notes")
    (profile / "memories" / "users" / "ana" / "MEMORY.md").write_text("about ana here")
    (profile / "memories" / "users" / "ben" / "MEMORY.md").write_text("about ben here")
    (root / "persons" / "ana" / "USER.md").write_text("who ana is")
    (root / "persons" / "ben" / "USER.md").write_text("who ben is")
    monkeypatch.setattr(memory_tool, "get_hermes_home", lambda: profile)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: profile)
    return profile


def _paths(files: List[memory_tool.CuratedFile]) -> set[Path]:
    return {f.path for f in files}


# ---------------------------------------------------------------------------
# The enumeration every erase path now shares
# ---------------------------------------------------------------------------


def test_a_persons_erase_reaches_the_files_a_session_reads(home: Path) -> None:
    """The regression: the old list was two filenames, and both were the wrong
    two on a box where people have their own memory."""
    files = _paths(curated_memory_files(user_id="ana", target="all"))

    assert home / "memories" / "users" / "ana" / "MEMORY.md" in files
    assert home.parent.parent / "persons" / "ana" / "USER.md" in files


def test_erasing_your_own_memory_does_not_reach_anybody_elses(home: Path) -> None:
    files = _paths(curated_memory_files(user_id="ana", target="all"))

    assert home / "memories" / "users" / "ben" / "MEMORY.md" not in files
    assert home.parent.parent / "persons" / "ben" / "USER.md" not in files


def test_the_shared_block_needs_the_authority_that_writes_it(home: Path) -> None:
    """A member erasing their own memory must not take the profile's with it."""
    shared = home / "memories" / "MEMORY.md"

    assert shared not in _paths(curated_memory_files(user_id="ana", target="all"))
    assert shared in _paths(
        curated_memory_files(user_id="ana", target="all", include_shared=True)
    )


def test_all_principals_covers_the_profile_but_not_who_people_are(home: Path) -> None:
    """Person-level identity is shared across profiles, so one profile's owner
    is not the person who gets to erase it."""
    files = _paths(
        curated_memory_files(user_id="ana", target="all", all_principals=True)
    )

    assert home / "memories" / "users" / "ben" / "MEMORY.md" in files
    assert home.parent.parent / "persons" / "ben" / "USER.md" not in files


def test_target_narrows_to_one_tier(home: Path) -> None:
    memory_only = _paths(curated_memory_files(user_id="ana", target="memory"))
    user_only = _paths(curated_memory_files(user_id="ana", target="user"))

    assert memory_only == {home / "memories" / "users" / "ana" / "MEMORY.md"}
    assert user_only == {home.parent.parent / "persons" / "ana" / "USER.md"}


def test_a_user_id_that_climbs_out_of_the_tree_is_refused(home: Path) -> None:
    with pytest.raises(ValueError):
        curated_memory_files(user_id="../../etc", target="all")


# ---------------------------------------------------------------------------
# `hermes memory reset` — the command whose words were the worst of it
# ---------------------------------------------------------------------------


class _Args:
    memory_command = "reset"
    target = "all"
    yes = True

    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def _bind(monkeypatch: pytest.MonkeyPatch, user_id: str, role: str) -> None:
    from hermes_cli.principal_binding import LocalBinding, LocalResolution

    monkeypatch.setattr(
        "hermes_cli.principal_binding.resolve_local_principal",
        lambda **_kwargs: LocalResolution(
            binding=LocalBinding(user_id=user_id, role=role, source="setup"),
            candidates=2,
        ),
    )


def test_reset_erases_the_callers_memory_and_nobody_elses(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hermes_cli.main import cmd_memory

    _bind(monkeypatch, "ana", "member")
    cmd_memory(_Args(all_principals=False))

    assert not (home / "memories" / "users" / "ana" / "MEMORY.md").exists()
    assert not (home.parent.parent / "persons" / "ana" / "USER.md").exists()
    assert (home / "memories" / "users" / "ben" / "MEMORY.md").exists()
    assert (home / "memories" / "MEMORY.md").exists()


def test_reset_says_what_it_left_instead_of_promising_a_blank_slate(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect was the sentence as much as the files: an owner who ran this
    believed the box had forgotten."""
    from hermes_cli.main import cmd_memory

    _bind(monkeypatch, "ana", "member")
    cmd_memory(_Args(all_principals=False))
    out = capsys.readouterr().out

    assert "Left in place:" in out
    assert "blank slate" not in out


def test_reset_leaves_no_directory_claiming_a_person_is_still_known(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An emptied ``users/<uid>/`` is a person this profile still appears to
    know: it is what --all-principals enumerates and what a purge reads to
    decide an identity file is still in use."""
    from hermes_cli.main import cmd_memory

    _bind(monkeypatch, "ana", "member")
    cmd_memory(_Args(all_principals=False))

    assert not (home / "memories" / "users" / "ana").exists()
    assert not (home.parent.parent / "persons" / "ana").exists()
    assert memory_tool._participation_user_ids() == ["ben"]


def test_only_owner_or_admin_can_erase_memory_about_other_people(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hermes_cli.main import cmd_memory

    _bind(monkeypatch, "ana", "member")
    cmd_memory(_Args(all_principals=True))
    out = capsys.readouterr().out

    assert "only an owner or admin" in out
    assert (home / "memories" / "users" / "ben" / "MEMORY.md").exists()


def test_an_owner_can_clear_the_profile_but_not_who_people_are(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hermes_cli.main import cmd_memory

    _bind(monkeypatch, "ana", "owner")
    cmd_memory(_Args(all_principals=True))

    assert not (home / "memories" / "users" / "ben" / "MEMORY.md").exists()
    assert not (home / "memories" / "MEMORY.md").exists()
    assert (home.parent.parent / "persons" / "ben" / "USER.md").exists()


# ---------------------------------------------------------------------------
# What a hard delete leaves behind
# ---------------------------------------------------------------------------


def test_the_delete_help_describes_what_it_does_to_memory() -> None:
    """Found by reading ``--help`` on the box: the description still said
    "Nothing cascades to memories" after purge had started erasing them."""
    from hermes_cli.member import DELETE_DESCRIPTION

    assert "Nothing cascades to memories" not in DELETE_DESCRIPTION
    assert "deleted under both strategies" in DELETE_DESCRIPTION
    assert "erases the curated memory files" in DELETE_DESCRIPTION


def test_a_purged_person_stops_being_described_to_the_agent(home: Path) -> None:
    from hermes_cli.members import _erase_memory_files

    erased = _erase_memory_files("ana")

    assert not (home / "memories" / "users" / "ana").exists()
    assert not (home.parent.parent / "persons" / "ana").exists()
    assert len(erased) == 2
    # And nobody else was touched.
    assert (home / "memories" / "users" / "ben" / "MEMORY.md").exists()
    assert (home / "memories" / "MEMORY.md").exists()


def test_identity_survives_a_delete_from_one_of_several_profiles(
    home: Path,
) -> None:
    """``persons/<uid>/`` is instance-wide: while the person still participates
    somewhere else, removing them here must not erase who they are there."""
    other = home.parent / "ops" / "memories" / "users" / "ana"
    other.mkdir(parents=True)
    (other / "MEMORY.md").write_text("about ana in ops")
    from hermes_cli.members import _erase_memory_files

    _erase_memory_files("ana")

    assert not (home / "memories" / "users" / "ana").exists()
    assert (other / "MEMORY.md").exists()
    assert (home.parent.parent / "persons" / "ana" / "USER.md").exists()


def test_the_box_wide_inventory_finds_every_profile(home: Path) -> None:
    other = home.parent / "ops" / "memories" / "users" / "ana"
    other.mkdir(parents=True)
    (other / "MEMORY.md").write_text("about ana in ops")

    found = _paths(person_memory_files_on_box("ana"))

    assert other / "MEMORY.md" in found
    assert home / "memories" / "users" / "ana" / "MEMORY.md" in found
    assert home.parent.parent / "persons" / "ana" / "USER.md" in found


# ---------------------------------------------------------------------------
# What a clone carries
# ---------------------------------------------------------------------------


def test_clone_all_leaves_each_persons_memory_behind(tmp_path: Path) -> None:
    """A clone's principals come from enrolment, so copying the source's record
    of *its* people hands facts to a profile they may never join."""
    from hermes_cli.profiles import _clone_all_copytree_ignore

    source = tmp_path / "profiles" / "work"
    (source / "memories" / "users" / "ana").mkdir(parents=True)
    (source / "persons" / "ana").mkdir(parents=True)
    (source / "skills").mkdir()
    (source / "memories" / "MEMORY.md").write_text("profile notes")
    (source / "memories" / "users" / "ana" / "MEMORY.md").write_text("about ana")
    (source / "persons" / "ana" / "USER.md").write_text("who ana is")
    (source / "skills" / "SKILL.md").write_text("a skill")
    (source / "config.yaml").write_text("model: x")

    destination = tmp_path / "clone"
    shutil.copytree(source, destination, ignore=_clone_all_copytree_ignore(source))

    assert not (destination / "memories" / "users").exists()
    assert not (destination / "persons").exists()
    # Everything --clone-all exists for still travels.
    assert (destination / "memories" / "MEMORY.md").read_text() == "profile notes"
    assert (destination / "skills" / "SKILL.md").exists()
    assert (destination / "config.yaml").exists()


def test_a_directory_called_users_elsewhere_still_travels(tmp_path: Path) -> None:
    """The exclusion is the memory path, not the word: a skill or data
    directory that happens to be called ``users`` is the clone's to keep."""
    from hermes_cli.profiles import _clone_all_copytree_ignore

    source = tmp_path / "profiles" / "work"
    (source / "skills" / "users").mkdir(parents=True)
    (source / "skills" / "users" / "SKILL.md").write_text("about users")

    destination = tmp_path / "clone"
    shutil.copytree(source, destination, ignore=_clone_all_copytree_ignore(source))

    assert (destination / "skills" / "users" / "SKILL.md").exists()
