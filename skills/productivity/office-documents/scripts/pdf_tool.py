#!/usr/bin/env python3
"""Parse, read, update and create PDFs on the box (pypdf + reportlab).

Usage:
    python pdf_tool.py info   IN.pdf                       # pages, metadata, form fields (JSON)
    python pdf_tool.py text   IN.pdf [--pages 0-2,5] [--json]
    python pdf_tool.py create OUT.pdf --text FILE.txt|- [--title T] [--author A]
    python pdf_tool.py create OUT.pdf --table rows.csv [--title T]
    python pdf_tool.py merge  OUT.pdf A.pdf B.pdf ...
    python pdf_tool.py select OUT.pdf IN.pdf --pages 0-2,5  # extract / reorder pages
    python pdf_tool.py rotate OUT.pdf IN.pdf --degrees 90 [--pages 0,2]
    python pdf_tool.py stamp  OUT.pdf IN.pdf --text "DRAFT" [--pages 0]
    python pdf_tool.py meta   OUT.pdf IN.pdf --title T --author A --subject S
    python pdf_tool.py fill   OUT.pdf IN.pdf --field Name=Leo --field Date=2026-09-25

Page ranges are 0-based, inclusive ("0-2,5"). Text edits of existing glyphs
are not supported here — see the nano-pdf skill for that.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path


def _ensure(feature: str) -> None:
    try:
        from tools.lazy_deps import ensure

        ensure(feature, prompt=False)
    except ImportError:
        pass  # running outside the Hermes checkout — rely on the venv


def _reader(path: str):
    _ensure("documents.pypdf")
    from pypdf import PdfReader

    return PdfReader(path)


def _writer():
    from pypdf import PdfWriter

    return PdfWriter()


def parse_pages(spec: str | None, count: int) -> list[int]:
    if not spec:
        return list(range(count))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.extend(range(int(lo), int(hi) + 1))
        else:
            pages.append(int(part))
    bad = [p for p in pages if p < 0 or p >= count]
    if bad:
        raise SystemExit(f"page(s) out of range for a {count}-page PDF: {bad}")
    return pages


def cmd_info(args: argparse.Namespace) -> None:
    reader = _reader(args.input)
    meta = {k.lstrip("/"): str(v) for k, v in (reader.metadata or {}).items()}
    fields = reader.get_form_text_fields() or {}
    print(
        json.dumps(
            {
                "file": args.input,
                "pages": len(reader.pages),
                "encrypted": reader.is_encrypted,
                "metadata": meta,
                "form_fields": fields,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def cmd_text(args: argparse.Namespace) -> None:
    reader = _reader(args.input)
    pages = parse_pages(args.pages, len(reader.pages))
    out = [{"page": i, "text": reader.pages[i].extract_text() or ""} for i in pages]
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    for item in out:
        print(f"--- page {item['page']} ---")
        print(item["text"])


def _story_from_text(text: str, styles):
    from reportlab.platypus import Paragraph, Spacer
    from xml.sax.saxutils import escape

    story = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        block = block.strip("\n")
        if not block.strip():
            continue
        if block.startswith("# "):
            story.append(Paragraph(escape(block[2:]), styles["Title"]))
        elif block.startswith("## "):
            story.append(Paragraph(escape(block[3:]), styles["Heading2"]))
        else:
            story.append(Paragraph(escape(block).replace("\n", "<br/>"), styles["BodyText"]))
        story.append(Spacer(1, 8))
    return story


def _story_from_csv(path: str, styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise SystemExit(f"{path} is empty")
    table = Table(rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDDDDD")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return [table]


def cmd_create(args: argparse.Namespace) -> None:
    _ensure("documents.reportlab")
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate

    styles = getSampleStyleSheet()
    if args.text is not None:
        text = sys.stdin.read() if args.text == "-" else Path(args.text).read_text(encoding="utf-8")
        story = _story_from_text(text, styles)
    elif args.table:
        story = _story_from_csv(args.table, styles)
    else:
        raise SystemExit("create needs --text FILE|- or --table rows.csv")
    if args.title:
        from reportlab.platypus import Paragraph, Spacer

        story = [Paragraph(args.title, styles["Title"]), Spacer(1, 12)] + story
    doc = SimpleDocTemplate(
        args.output,
        pagesize=A4 if args.pagesize == "a4" else letter,
        title=args.title or "",
        author=args.author or "",
    )
    doc.build(story)
    _report(args.output)


def cmd_merge(args: argparse.Namespace) -> None:
    _ensure("documents.pypdf")
    writer = _writer()
    for src in args.inputs:
        writer.append(src)
    with open(args.output, "wb") as fh:
        writer.write(fh)
    _report(args.output)


def cmd_select(args: argparse.Namespace) -> None:
    reader = _reader(args.input)
    writer = _writer()
    for i in parse_pages(args.pages, len(reader.pages)):
        writer.add_page(reader.pages[i])
    with open(args.output, "wb") as fh:
        writer.write(fh)
    _report(args.output)


def cmd_rotate(args: argparse.Namespace) -> None:
    reader = _reader(args.input)
    writer = _writer()
    targets = set(parse_pages(args.pages, len(reader.pages)))
    for i, page in enumerate(reader.pages):
        if i in targets:
            page.rotate(args.degrees)
        writer.add_page(page)
    with open(args.output, "wb") as fh:
        writer.write(fh)
    _report(args.output)


def cmd_stamp(args: argparse.Namespace) -> None:
    _ensure("documents.reportlab")
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    reader = _reader(args.input)
    from pypdf import PdfReader

    writer = _writer()
    targets = set(parse_pages(args.pages, len(reader.pages)))
    for i, page in enumerate(reader.pages):
        if i in targets:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            c.setFont("Helvetica-Bold", args.size)
            c.setFillColor(Color(0.6, 0.6, 0.6, alpha=0.4))
            c.saveState()
            c.translate(w / 2, h / 2)
            c.rotate(45)
            c.drawCentredString(0, 0, args.text)
            c.restoreState()
            c.save()
            buf.seek(0)
            page.merge_page(PdfReader(buf).pages[0])
        writer.add_page(page)
    with open(args.output, "wb") as fh:
        writer.write(fh)
    _report(args.output)


def cmd_meta(args: argparse.Namespace) -> None:
    reader = _reader(args.input)
    writer = _writer()
    writer.append(reader)
    meta = {k: v for k, v in (reader.metadata or {}).items()}
    for key, value in (("/Title", args.title), ("/Author", args.author), ("/Subject", args.subject)):
        if value is not None:
            meta[key] = value
    writer.add_metadata(meta)
    with open(args.output, "wb") as fh:
        writer.write(fh)
    _report(args.output)


def cmd_fill(args: argparse.Namespace) -> None:
    reader = _reader(args.input)
    writer = _writer()
    writer.append(reader)
    values: dict[str, str] = {}
    for item in args.field:
        if "=" not in item:
            raise SystemExit(f"--field expects NAME=VALUE, got {item!r}")
        name, value = item.split("=", 1)
        values[name] = value
    known = reader.get_form_text_fields() or {}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise SystemExit(f"unknown form field(s) {unknown}; known: {sorted(known)}")
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    with open(args.output, "wb") as fh:
        writer.write(fh)
    _report(args.output)


def _report(path: str) -> None:
    from pypdf import PdfReader

    size = Path(path).stat().st_size
    pages = len(PdfReader(path).pages)
    print(json.dumps({"output": path, "bytes": size, "pages": pages}))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info"); s.add_argument("input"); s.set_defaults(fn=cmd_info)

    s = sub.add_parser("text"); s.add_argument("input"); s.add_argument("--pages")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_text)

    s = sub.add_parser("create"); s.add_argument("output")
    s.add_argument("--text", help="text/markdown-ish file, or - for stdin")
    s.add_argument("--table", help="CSV file rendered as a table")
    s.add_argument("--title"); s.add_argument("--author")
    s.add_argument("--pagesize", choices=["a4", "letter"], default="a4")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("merge"); s.add_argument("output"); s.add_argument("inputs", nargs="+")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("select"); s.add_argument("output"); s.add_argument("input")
    s.add_argument("--pages", required=True); s.set_defaults(fn=cmd_select)

    s = sub.add_parser("rotate"); s.add_argument("output"); s.add_argument("input")
    s.add_argument("--degrees", type=int, required=True, choices=[90, 180, 270])
    s.add_argument("--pages"); s.set_defaults(fn=cmd_rotate)

    s = sub.add_parser("stamp"); s.add_argument("output"); s.add_argument("input")
    s.add_argument("--text", required=True); s.add_argument("--pages")
    s.add_argument("--size", type=int, default=48); s.set_defaults(fn=cmd_stamp)

    s = sub.add_parser("meta"); s.add_argument("output"); s.add_argument("input")
    s.add_argument("--title"); s.add_argument("--author"); s.add_argument("--subject")
    s.set_defaults(fn=cmd_meta)

    s = sub.add_parser("fill"); s.add_argument("output"); s.add_argument("input")
    s.add_argument("--field", action="append", default=[], help="NAME=VALUE (repeatable)")
    s.set_defaults(fn=cmd_fill)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
