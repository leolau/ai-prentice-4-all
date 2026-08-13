"""A promoted skill has to be readable by the profiles it was promoted for.

Promotion writes ``skills-shared/`` into ``skills.external_dirs`` of the profile
that approved it — the one profile whose agents already had the skill locally.
Every other profile's config is untouched, so on a real box the shared library
was invisible exactly where sharing was the point. The library is resolved from
the Hermes root instead, so "promoted" means visible in every profile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.skill_utils import _external_dirs_cache_clear, get_external_skills_dirs
from hermes_cli.skill_promotion import shared_skills_dir


@pytest.fixture()
def two_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "hermes"
    for name in ("default", "other"):
        (root / "profiles" / name / "skills").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "default"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _external_dirs_cache_clear()
    yield root
    _external_dirs_cache_clear()


def _promote(root: Path, name: str) -> Path:
    shared = root / "skills-shared" / name
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\nname: x\n---\n\nbody\n", encoding="utf-8")
    return shared


def test_a_profile_that_never_approved_anything_still_reads_the_library(
    two_profiles: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    promoted = _promote(two_profiles, "email-triage")
    monkeypatch.setenv("HERMES_HOME", str(two_profiles / "profiles" / "other"))
    _external_dirs_cache_clear()

    assert promoted.parent.resolve() in get_external_skills_dirs()


def test_the_library_is_read_even_with_a_config_that_never_mentions_it(
    two_profiles: Path,
) -> None:
    promoted = _promote(two_profiles, "email-triage")
    config = two_profiles / "profiles" / "default" / "config.yaml"
    config.write_text("skills:\n  disabled:\n    - github-auth\n", encoding="utf-8")
    _external_dirs_cache_clear()

    assert promoted.parent.resolve() in get_external_skills_dirs()


def test_a_configured_dir_is_not_lost_when_the_library_is_added(
    two_profiles: Path,
) -> None:
    promoted = _promote(two_profiles, "email-triage")
    external = two_profiles / "vendor-skills"
    external.mkdir()
    config = two_profiles / "profiles" / "default" / "config.yaml"
    config.write_text(
        f"skills:\n  external_dirs:\n    - {external}\n", encoding="utf-8"
    )
    _external_dirs_cache_clear()

    dirs = get_external_skills_dirs()
    assert dirs == [external.resolve(), promoted.parent.resolve()]


def test_nothing_is_invented_when_no_skill_was_ever_promoted(
    two_profiles: Path,
) -> None:
    assert not shared_skills_dir().exists()
    assert get_external_skills_dirs() == []


def test_the_library_appears_once_when_the_config_also_names_it(
    two_profiles: Path,
) -> None:
    promoted = _promote(two_profiles, "email-triage")
    config = two_profiles / "profiles" / "default" / "config.yaml"
    config.write_text(
        f"skills:\n  external_dirs:\n    - {promoted.parent}\n", encoding="utf-8"
    )
    _external_dirs_cache_clear()

    assert get_external_skills_dirs() == [promoted.parent.resolve()]
