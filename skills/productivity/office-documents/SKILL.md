---
name: office-documents
description: "Parse, read, update and create PDF and Excel files on the box."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [PDF, Excel, xlsx, Spreadsheets, Documents, Invoices, Reports]
    related_skills: [ocr-and-documents, nano-pdf, remember-file, powerpoint]
---

# Office Documents Skill

Work with PDF and Excel (`.xlsx`) files that live on the Hermes box — files
you produced, files imported from the user's Mac through Folder Bridge, or
files that arrived over chat/email and sit in the Files registry.

Everything runs through the terminal with three scripts under
`scripts/` — no model tool is involved:

| Script | Library | Does |
|--------|---------|------|
| `pdf_tool.py` | pypdf + reportlab | info, text, create, merge, select, rotate, stamp, meta, fill (forms) |
| `xlsx_tool.py` | openpyxl | info, dump, create, set, append, add-sheet |
| `registry_file.py` | hermes_cli.file_registry | list / get / put files in the Files registry (`/files`) |

## When to Use

- "Read this invoice PDF and tell me the total" → `pdf_tool.py text`
- "Build a spreadsheet of these invoices" → `xlsx_tool.py create`
- "Add September's rows to the tracker" → `xlsx_tool.py append` / `set`
- "Make a PDF summary / cover letter / table report" → `pdf_tool.py create`
- "Combine these PDFs" / "just pages 2-4" / "watermark DRAFT" → `merge` / `select` / `stamp`
- The user imported files via Folder Bridge and wants them processed →
  `registry_file.py list` + `get`, then the tools above, then `put` the result.

Not this skill: scanned PDFs needing OCR or table-heavy extraction
(`ocr-and-documents`), rewriting words inside an existing PDF (`nano-pdf`),
`.pptx` (`powerpoint`), ingesting a file into memory (`remember-file`).

## Prerequisites

The libraries are pinned in the `documents` extra of `pyproject.toml` and in
`tools/lazy_deps.py` (`documents.pypdf`, `documents.openpyxl`,
`documents.reportlab`); the scripts lazy-install them on first use. To
install up front:

```bash
cd <hermes-agent checkout>            # /opt/data/hermes-agent on the prod box
uv pip install --python .venv/bin/python -e ".[documents]"
```

`registry_file.py` additionally needs the Supabase app datastore and
storage env that `hermes memory rag remember-file` uses; run it with the
checkout's venv python from the checkout root.

## How to Run

```bash
S=skills/productivity/office-documents/scripts   # relative to the checkout
PY=.venv/bin/python
```

### PDF — read

```bash
$PY $S/pdf_tool.py info  invoice.pdf                 # pages, metadata, form fields
$PY $S/pdf_tool.py text  invoice.pdf                 # all pages, plain text
$PY $S/pdf_tool.py text  report.pdf --pages 0-2 --json
```

### PDF — create / update

```bash
# Text/markdown-ish (blank line = paragraph, "# " title, "## " heading)
$PY $S/pdf_tool.py create summary.pdf --text summary.md --title "Q3 Invoices" --author Hermes
printf '# Hello\n\nBody text.' | $PY $S/pdf_tool.py create hello.pdf --text -
# Table from CSV
$PY $S/pdf_tool.py create table.pdf --table rows.csv --title "Invoice list"
$PY $S/pdf_tool.py merge  all.pdf a.pdf b.pdf c.pdf
$PY $S/pdf_tool.py select pages.pdf in.pdf --pages 1-3
$PY $S/pdf_tool.py rotate out.pdf in.pdf --degrees 90 --pages 0
$PY $S/pdf_tool.py stamp  out.pdf in.pdf --text "DRAFT"
$PY $S/pdf_tool.py meta   out.pdf in.pdf --title "New title"
$PY $S/pdf_tool.py fill   out.pdf form.pdf --field Name=Leo --field Date=2026-09-25
```

### Excel — read

```bash
$PY $S/xlsx_tool.py info tracker.xlsx                 # sheets + dimensions
$PY $S/xlsx_tool.py dump tracker.xlsx                 # active sheet, values
$PY $S/xlsx_tool.py dump tracker.xlsx --sheet 2026 --range A1:F30 --json
$PY $S/xlsx_tool.py dump tracker.xlsx --formulas      # formula text instead of cached values
```

### Excel — create / update

```bash
$PY $S/xlsx_tool.py create invoices.xlsx --csv rows.csv --sheet Invoices
$PY $S/xlsx_tool.py create invoices.xlsx --json rows.json          # list of objects → header row + rows
$PY $S/xlsx_tool.py set    tracker.xlsx --sheet 2026 --cell B7=1250.5 --cell D7="=B7*C7"
$PY $S/xlsx_tool.py append tracker.xlsx --csv new_rows.csv --skip-header
$PY $S/xlsx_tool.py add-sheet tracker.xlsx --sheet Summary --csv summary.csv
# keep the original: any update accepts --out other.xlsx
```

Values are typed automatically (`42` → int, `3.5` → float, `true` → bool,
`=A1*2` → formula, empty → blank).

### Files registry (imported / produced files)

```bash
$PY $S/registry_file.py --as leo_owner list --query invoice
$PY $S/registry_file.py --as leo_owner get  <asset_id> --out /tmp/work/
$PY $S/registry_file.py --as leo_owner put  /tmp/work/summary.pdf --conversation "invoices/Q3 summary"
```

`get` verifies the download's sha256 against the registry row; `put`
uploads, registers the file on `/files` (surface `agent_home`) and
re-downloads to confirm the stored bytes match.

## Quick Reference

| Need | Command |
|------|---------|
| Page count / metadata | `pdf_tool.py info X.pdf` |
| Text of pages 0-1 | `pdf_tool.py text X.pdf --pages 0-1` |
| New PDF from text | `pdf_tool.py create OUT.pdf --text FILE` |
| New PDF table | `pdf_tool.py create OUT.pdf --table rows.csv` |
| Merge / subset / rotate | `merge` / `select --pages` / `rotate --degrees` |
| Sheet list | `xlsx_tool.py info X.xlsx` |
| Cells as JSON | `xlsx_tool.py dump X.xlsx --json` |
| New workbook | `xlsx_tool.py create OUT.xlsx --csv rows.csv` |
| Change cells | `xlsx_tool.py set X.xlsx --cell A1=v` |
| Add rows | `xlsx_tool.py append X.xlsx --csv rows.csv` |
| Fetch / publish via /files | `registry_file.py --as U get ID` / `put PATH` |

## Procedure

1. Locate the input: a local path, or `registry_file.py list` → `get` into a
   scratch dir (e.g. `/tmp/work/`).
2. Inspect before changing: `pdf_tool.py info|text`, `xlsx_tool.py info|dump`.
3. Do the change into a **new** file (`--out`, or a new output path) so the
   original stays intact; the scripts print a JSON line with bytes / pages /
   sheet dimensions you can quote back.
4. Re-read the output (`text` / `dump`) to confirm it says what you intended.
5. If the user should see it in agent-home, `registry_file.py put` it and
   report the `asset_id`; the file appears on `/files`.

## Pitfalls

- pypdf reads what the PDF encodes: scanned PDFs return empty text — switch
  to `ocr-and-documents`. Column order of extracted tables is not reliable.
- `xlsx_tool.py dump` shows values cached by the last Excel/Numbers save;
  formulas written by `set`/`append` show as `None` until a spreadsheet app
  recalculates. Use `--formulas` to read the formula text.
- openpyxl drops charts, images and macros (`.xlsm`) on save — copy the
  workbook first and warn the user if `info` shows such a workbook.
- `.numbers` (Apple) and legacy `.xls` are not supported; ask the user to
  export to `.xlsx` (Numbers → File → Export To → Excel).
- `fill` needs real AcroForm text fields (`info` lists them); flat PDFs have none.
- `stamp`/`create` use built-in Helvetica: non-Latin text will render as
  boxes — say so instead of shipping a broken PDF.
- Do not write back into the user's Mac folder: Folder Bridge is read-only;
  publish results via `registry_file.py put`.

## Verification

```bash
$PY $S/pdf_tool.py text OUT.pdf | head          # text made it in
$PY $S/xlsx_tool.py dump OUT.xlsx --range A1:D5 # cells as intended
$PY $S/registry_file.py --as leo_owner list --query OUT   # row exists, sha256 present
```

Every mutating command exits non-zero on failure and prints one JSON line on
success; quote `bytes`/`sha256`/`asset_id` from it in your reply.
