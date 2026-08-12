"""A skill's *name* decides where promotion writes, so it is untrusted input.

The name comes from the skill's own frontmatter, and the self-improvement loop
authors skills without a human in the loop. Promotion copies a directory into
the shared library and deletes whatever it replaces, so a name that escapes the
library is a delete-and-overwrite anywhere the hermes user can write. These
tests use the real filesystem for that reason — the property is *what ends up on
disk*, which a mock cannot show.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.skill_promotion import (
    PromotionError,
    SkillPromotionStore,
    contained_shared_path,
    shared_skills_dir,
)


@pytest.fixture()
def hermes_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "profiles" / "default"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return tmp_path


@pytest.mark.parametrize(
    "name",
    ["../victim", "../../etc", "nested/skill", "", "   ", ".", "..", "/absolute"],
)
def test_a_name_that_is_not_one_directory_in_the_library_is_refused(
    hermes_root: Path, name: str
) -> None:
    with pytest.raises(PromotionError):
        contained_shared_path(name)


def test_a_plain_name_resolves_inside_the_library(hermes_root: Path) -> None:
    assert contained_shared_path("email-triage") == shared_skills_dir() / "email-triage"


def test_install_does_not_delete_a_directory_outside_the_library(
    hermes_root: Path,
) -> None:
    victim = shared_skills_dir().parent / "victim"
    victim.mkdir(parents=True)
    (victim / "important.txt").write_text("keep me", encoding="utf-8")

    source = hermes_root / "profiles" / "default" / "skills" / "innocent"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: ../victim\n---\nbody\n", encoding="utf-8"
    )

    store = SkillPromotionStore.__new__(SkillPromotionStore)
    with pytest.raises(PromotionError):
        SkillPromotionStore._install(store, source / "SKILL.md", "../victim")
    assert (victim / "important.txt").read_text(encoding="utf-8") == "keep me"


def test_uninstall_does_not_delete_a_directory_outside_the_library(
    hermes_root: Path,
) -> None:
    victim = shared_skills_dir().parent / "victim"
    victim.mkdir(parents=True)
    (victim / "important.txt").write_text("keep me", encoding="utf-8")

    store = SkillPromotionStore.__new__(SkillPromotionStore)
    with pytest.raises(PromotionError):
        SkillPromotionStore._uninstall(store, "../victim")
    assert (victim / "important.txt").exists()
