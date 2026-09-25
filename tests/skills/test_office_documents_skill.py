"""Standards + behaviour tests for the bundled office-documents skill.

The script tests exercise pypdf/reportlab/openpyxl for real (no mocks) and
are skipped when the ``documents`` extra is not installed.
"""

import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "productivity" / "office-documents"
SKILL = SKILL_DIR / "SKILL.md"
SCRIPTS = SKILL_DIR / "scripts"


def _read() -> str:
    return SKILL.read_text(encoding="utf-8")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _have(*mods: str) -> bool:
    return all(importlib.util.find_spec(m) is not None for m in mods)


def test_frontmatter_name_and_description():
    content = _read()
    name = re.search(r"^name: (.+)$", content, re.MULTILINE)
    description = re.search(r"^description: (.+)$", content, re.MULTILINE)
    assert name and name.group(1).strip() == "office-documents"
    assert description, "description is required"
    text = description.group(1).strip().strip('"')
    assert len(text) <= 80, len(text)
    assert text.endswith(".")


def test_modern_sections_present():
    content = _read()
    for section in (
        "# Office Documents Skill",
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ):
        assert section in content, f"missing {section}"


def test_teaches_every_script_verb():
    content = _read()
    for needle in (
        "pdf_tool.py info", "pdf_tool.py text", "pdf_tool.py create", "pdf_tool.py merge",
        "xlsx_tool.py info", "xlsx_tool.py dump", "xlsx_tool.py create",
        "xlsx_tool.py set", "xlsx_tool.py append",
        "registry_file.py", " get ", " put ",
    ):
        assert needle in content, f"missing {needle}"


def test_pins_agree_between_pyproject_and_lazy_deps():
    """The extra and the lazy-deps allowlist must resolve the same versions."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    extra = re.search(r"^documents = \[(.+?)\]", pyproject, re.MULTILINE)
    assert extra, "documents extra missing from pyproject.toml"
    pinned = set(re.findall(r'"([^"]+)"', extra.group(1)))

    sys.path.insert(0, str(ROOT))
    from tools.lazy_deps import LAZY_DEPS

    lazy = {
        spec
        for key, specs in LAZY_DEPS.items()
        if key.startswith("documents.")
        for spec in specs
    }
    assert lazy == pinned
    assert LAZY_DEPS["documents.pypdf"] == LAZY_DEPS["rag.pypdf"]


@pytest.mark.skipif(not _have("pypdf", "reportlab"), reason="documents extra not installed")
class TestPdfTool:
    def test_create_text_merge_select_roundtrip(self, tmp_path, capsys):
        pdf = _load("pdf_tool")
        src = tmp_path / "body.md"
        src.write_text("# Q3 Invoices\n\nTotal due: 1,234.50\n\n## Notes\n\nPaid on time.", encoding="utf-8")
        a = tmp_path / "a.pdf"
        pdf.main(["create", str(a), "--text", str(src), "--author", "Hermes"])
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out["pages"] == 1 and out["bytes"] > 0

        rows = tmp_path / "rows.csv"
        with rows.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([["Invoice", "Amount"], ["INV-1", "10.00"], ["INV-2", "20.00"]])
        b = tmp_path / "b.pdf"
        pdf.main(["create", str(b), "--table", str(rows), "--title", "Table"])
        capsys.readouterr()

        merged = tmp_path / "m.pdf"
        pdf.main(["merge", str(merged), str(a), str(b)])
        assert json.loads(capsys.readouterr().out.strip())["pages"] == 2

        pdf.main(["text", str(merged), "--json"])
        pages = json.loads(capsys.readouterr().out)
        assert "1,234.50" in pages[0]["text"]
        assert "INV-2" in pages[1]["text"]

        only = tmp_path / "p1.pdf"
        pdf.main(["select", str(only), str(merged), "--pages", "1"])
        capsys.readouterr()
        pdf.main(["text", str(only)])
        text = capsys.readouterr().out
        assert "INV-1" in text and "1,234.50" not in text

    def test_meta_and_info(self, tmp_path, capsys):
        pdf = _load("pdf_tool")
        src = tmp_path / "t.txt"
        src.write_text("hello", encoding="utf-8")
        a = tmp_path / "a.pdf"
        pdf.main(["create", str(a), "--text", str(src)])
        b = tmp_path / "b.pdf"
        pdf.main(["meta", str(b), str(a), "--title", "Renamed", "--subject", "S"])
        capsys.readouterr()
        pdf.main(["info", str(b)])
        info = json.loads(capsys.readouterr().out)
        assert info["metadata"]["Title"] == "Renamed"
        assert info["metadata"]["Subject"] == "S"
        assert info["pages"] == 1

    def test_bad_page_range_exits(self, tmp_path, capsys):
        pdf = _load("pdf_tool")
        src = tmp_path / "t.txt"
        src.write_text("x", encoding="utf-8")
        a = tmp_path / "a.pdf"
        pdf.main(["create", str(a), "--text", str(src)])
        with pytest.raises(SystemExit):
            pdf.main(["text", str(a), "--pages", "3"])


@pytest.mark.skipif(not _have("openpyxl"), reason="documents extra not installed")
class TestXlsxTool:
    def test_create_set_append_dump(self, tmp_path, capsys):
        xl = _load("xlsx_tool")
        rows = tmp_path / "rows.csv"
        with rows.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([["Item", "Qty", "Price"], ["Pen", "2", "1.5"]])
        book = tmp_path / "b.xlsx"
        xl.main(["create", str(book), "--csv", str(rows), "--sheet", "Items"])
        out = json.loads(capsys.readouterr().out.strip())
        assert out["sheets"] == {"Items": "A1:C2"}

        xl.main(["set", str(book), "--cell", "D1=Total", "--cell", "D2==B2*C2", "--cell", "E2=true"])
        capsys.readouterr()
        more = tmp_path / "more.json"
        more.write_text(json.dumps([{"Item": "Ink", "Qty": 1, "Price": 9.25}]), encoding="utf-8")
        xl.main(["append", str(book), "--json", str(more), "--skip-header"])
        capsys.readouterr()

        xl.main(["dump", str(book), "--formulas", "--json"])
        data = json.loads(capsys.readouterr().out)
        assert data[0] == ["Item", "Qty", "Price", "Total", None]
        assert data[1] == ["Pen", 2, 1.5, "=B2*C2", True]
        assert data[2] == ["Ink", 1, 9.25, None, None]

        xl.main(["add-sheet", str(book), "--sheet", "Summary"])
        capsys.readouterr()
        xl.main(["info", str(book)])
        info = json.loads(capsys.readouterr().out)
        assert [s["name"] for s in info["sheets"]] == ["Items", "Summary"]

    def test_out_keeps_original(self, tmp_path, capsys):
        xl = _load("xlsx_tool")
        book = tmp_path / "b.xlsx"
        xl.main(["create", str(book)])
        before = book.read_bytes()
        copy = tmp_path / "c.xlsx"
        xl.main(["set", str(book), "--cell", "A1=changed", "--out", str(copy)])
        capsys.readouterr()
        assert book.read_bytes() == before
        xl.main(["dump", str(copy), "--json"])
        assert json.loads(capsys.readouterr().out) == [["changed"]]

    def test_unknown_sheet_exits(self, tmp_path, capsys):
        xl = _load("xlsx_tool")
        book = tmp_path / "b.xlsx"
        xl.main(["create", str(book)])
        with pytest.raises(SystemExit):
            xl.main(["dump", str(book), "--sheet", "Nope"])


def test_registry_script_parses_and_verifies_hash():
    reg = _load("registry_file")
    assert reg.SURFACE == "agent_home"
    assert reg._sha256(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    with pytest.raises(SystemExit):
        reg.main(["list"])  # --as is required
