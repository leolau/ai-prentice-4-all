"""Standards tests for the bundled incomings skill (SKILL.md only)."""

import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2] / "skills" / "productivity" / "incomings" / "SKILL.md"


def _read() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_frontmatter_name_and_description():
    content = _read()
    name = re.search(r"^name: (.+)$", content, re.MULTILINE)
    description = re.search(r"^description: (.+)$", content, re.MULTILINE)
    assert name and name.group(1).strip() == "incomings"
    assert description, "description is required"
    text = description.group(1).strip()
    assert len(text) <= 60, len(text)
    assert text.endswith(".")


def test_modern_sections_present():
    content = _read()
    for section in (
        "# Incomings Skill",
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ):
        assert section in content, f"missing {section}"


def test_teaches_the_verbs_and_paging():
    content = _read()
    for needle in (
        "hermes incomings list",
        "hermes incomings search",
        "hermes incomings show",
        "--cursor",
        "--json",
    ):
        assert needle in content, f"missing {needle}"


def test_names_the_native_terminal_tool():
    assert "`terminal`" in _read()


def test_covers_every_channel():
    lowered = _read().lower()
    for channel in ("whatsapp", "email", "calendar", "telegram"):
        assert channel in lowered, f"missing {channel}"
