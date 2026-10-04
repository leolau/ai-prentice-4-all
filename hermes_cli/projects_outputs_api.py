"""Produced files of a project — the Outputs tab's read (Projects redesign).

``GET /api/registry/projects/{slug}/artifacts`` lists every file the
project's runs and cards produced, newest first, so nobody has to open a
run page or a card to find one:

* **deliverable** — a delivery recorded against a declared output;
* **draft** — a file a card produced (``metadata.artifacts`` on a card run,
  a board attachment, a document link or workspace path in a card's result)
  that is not attached to any output;
* **note** — the same, but a working note (``notes.md``, logs, ``.txt``…).

``output_id`` is null for anything not attached to a declared output, which
is what the "files produced but not linked" warning reads.

``GET …/artifacts/{artifact_id}/content`` streams a produced file that lives
on this box (a card's workspace or the board's attachment store). It only
serves a path a card of *this* project claimed, and only from under the
board's managed roots — an agent naming ``~/.hermes/.env`` gets a 404.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from hermes_cli import kanban_db, projects_db
from hermes_cli.projects_api import _board_conn, _require_read

router = APIRouter(prefix="/api/registry/projects")

ARTIFACT_KINDS = ("deliverable", "draft", "note")

_NOTE_EXTS = {"txt", "log", "json", "jsonl", "yaml", "yml", "toml", "ini"}
_NOTE_WORDS = re.compile(
    r"(^|[^a-z])(notes?|log|logs|scratch|memo|worklog|todo)([^a-z]|$)", re.I
)
_FILE_EXTS = (
    "md|markdown|txt|pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|csv|tsv|json|jsonl|"
    "ya?ml|html?|png|jpe?g|gif|webp|svg|mp3|mp4|wav|zip|py|ts|tsx|js|ipynb|log"
)
_PATH_RE = re.compile(rf"(?<![\w/:.])(/[\w.\-/]*?[\w\-]\.(?:{_FILE_EXTS}))(?![\w])", re.I)
_MD_LINK_RE = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^)\s]+)\)")
_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_DOC_HOSTS = (
    "docs.google.com",
    "drive.google.com",
    "dropbox.com",
    "sharepoint.com",
    "onedrive.live.com",
    "1drv.ms",
    "box.com",
    "notion.so",
    "notion.site",
)
_URL_FILE_RE = re.compile(rf"\.(?:{_FILE_EXTS})$", re.I)
_DENY_NAMES = {".env", "auth.json", "credentials.json", ".netrc"}

_GOOGLE_KINDS = {
    "document": ("gdoc", "application/vnd.google-apps.document", "Google Doc"),
    "spreadsheets": ("gsheet", "application/vnd.google-apps.spreadsheet", "Google Sheet"),
    "presentation": ("gslides", "application/vnd.google-apps.presentation", "Google Slides"),
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def ext_of(name: Optional[str]) -> Optional[str]:
    """Lower-case extension without the dot (``MOU.v2.DOCX`` → ``docx``)."""
    if not name:
        return None
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if "." not in base.strip("."):
        return None
    ext = base.rsplit(".", 1)[-1].lower()
    return ext if 0 < len(ext) <= 8 and ext.isalnum() else None


def mime_of(name: Optional[str], declared: Optional[str] = None) -> Optional[str]:
    if declared:
        return declared
    if not name:
        return None
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    if ext_of(name) == "md":
        return "text/markdown"
    return None


def classify(name: Optional[str], *, attached: bool) -> str:
    """deliverable when attached to a declared output; otherwise a working
    note for logs/notes/plain text, else a draft."""
    if attached:
        return "deliverable"
    base = (name or "").rstrip("/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    if ext_of(base) in _NOTE_EXTS or _NOTE_WORDS.search(stem.replace("_", " ")):
        return "note"
    return "draft"


def _url_meta(url: str) -> tuple[str, Optional[str], Optional[str]]:
    """(title, ext, mime) for a document URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    segs = [unquote(s) for s in parsed.path.split("/") if s]
    if host == "docs.google.com" and segs and segs[0] in _GOOGLE_KINDS:
        ext, mime, label = _GOOGLE_KINDS[segs[0]]
        return label, ext, mime
    tail = segs[-1] if segs else host
    return tail or url, ext_of(tail), mime_of(tail)


def is_document_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in _DOC_HOSTS):
        return True
    return bool(_URL_FILE_RE.search(parsed.path or ""))


def links_in_text(text: Optional[str]) -> list[tuple[str, Optional[str]]]:
    """Document URLs in a card's result/summary as ``(url, link text)``,
    first mention first, deduplicated."""
    if not text:
        return []
    out: list[tuple[str, Optional[str]]] = []
    seen: set[str] = set()
    for label, url in _MD_LINK_RE.findall(text):
        url = url.rstrip(".,;:")
        if url not in seen and is_document_url(url):
            seen.add(url)
            out.append((url, label.strip() or None))
    for url in _URL_RE.findall(text):
        url = url.rstrip(".,;:")
        if url not in seen and is_document_url(url):
            seen.add(url)
            out.append((url, None))
    return out


def paths_in_text(text: Optional[str]) -> list[str]:
    """Absolute file paths a card's result mentions, deduplicated."""
    if not text:
        return []
    out: list[str] = []
    for match in _PATH_RE.findall(text):
        p = match.strip()
        if p not in out:
            out.append(p)
    return out


def assign_versions(deliveries: Iterable[dict]) -> dict[str, int]:
    """delivery id → version. Per output, each run is one version (v1 for the
    first run that delivered, v2 for the next…); a hand delivery outside a
    run is a version of its own."""
    by_output: dict[str, list[dict]] = {}
    for d in deliveries:
        by_output.setdefault(d["output_id"], []).append(d)
    versions: dict[str, int] = {}
    for rows in by_output.values():
        rows.sort(key=lambda d: (d.get("delivered_at") or 0, d["id"]))
        slot: dict[str, int] = {}
        for d in rows:
            key = f"run:{d['run_id']}" if d.get("run_id") else f"d:{d['id']}"
            if key not in slot:
                slot[key] = len(slot) + 1
            versions[d["id"]] = slot[key]
    return versions


def sort_newest(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda a: (-(a.get("created_at") or 0), a["id"]))


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _content_href(slug: str, artifact_id: str) -> str:
    return (
        f"/api/projects/{quote(slug, safe='')}/artifacts/"
        f"{quote(artifact_id, safe='')}/content"
    )


def _managed_roots(task, board: Optional[str]) -> list[Path]:
    roots: list[Path] = []
    try:
        roots.append(kanban_db.workspaces_root(board=board))
        roots.append(kanban_db.attachments_root(board=board))
    except Exception:
        pass
    if getattr(task, "workspace_path", None):
        roots.append(Path(task.workspace_path))
    return roots


def _servable(path_str: str, roots: list[Path]) -> Optional[Path]:
    """The resolved file when it exists under a managed root, else None."""
    try:
        path = Path(path_str).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not path.is_file() or path.name in _DENY_NAMES:
        return None
    for root in roots:
        try:
            base = root.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if base == Path(base.anchor):
            continue
        if path.is_relative_to(base):
            return path
    return None


def _location(path_str: str, task) -> str:
    ws = getattr(task, "workspace_path", None)
    parent = os.path.dirname(path_str)
    if ws and (path_str == ws or path_str.startswith(ws.rstrip("/") + "/")):
        rel = os.path.dirname(os.path.relpath(path_str, ws))
        return "card workspace" + (f" / {rel}" if rel and rel != "." else "")
    return parent or "/"


def _claimed_files(task, runs) -> list[dict]:
    """Files a card claimed: ``metadata.artifacts`` on its runs, then
    absolute paths its result/summaries mention. Ordered, deduplicated."""
    out: list[dict] = []
    seen: set[str] = set()

    def _add(path: str, at: Optional[int], by: Optional[str]) -> None:
        p = str(path or "").strip()
        if not p or p in seen:
            return
        seen.add(p)
        out.append({"path": p, "created_at": at, "created_by": by})

    for run in runs:
        meta = run.metadata if isinstance(run.metadata, dict) else {}
        items = meta.get("artifacts")
        if isinstance(items, (list, tuple)):
            for item in items:
                if isinstance(item, str):
                    _add(item, run.ended_at or run.started_at, run.profile)
        for p in paths_in_text(run.summary):
            _add(p, run.ended_at or run.started_at, run.profile)
    for p in paths_in_text(task.result):
        _add(p, task.completed_at or task.created_at, task.assignee)
    return out


def _card_links(task, runs) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    sources: list[tuple[Optional[str], Optional[int], Optional[str]]] = [
        (r.summary, r.ended_at or r.started_at, r.profile) for r in runs
    ]
    sources.append((task.result, task.completed_at or task.created_at, task.assignee))
    for text, at, by in sources:
        for url, label in links_in_text(text):
            if url in seen:
                continue
            seen.add(url)
            out.append({"url": url, "label": label, "created_at": at, "created_by": by})
    return out


def _card_context(project, principal) -> dict:
    """Run/card maps for the project: visible tasks, their runs, and the
    project run each card belongs to."""
    with projects_db.connect_closing() as conn:
        outputs = projects_db.get_project_outputs(conn, project.id)
        deliveries = projects_db.get_output_deliveries(conn, project_id=project.id)
        runs = projects_db.list_project_runs(conn, project.id, limit=1000)
        run_of_task: dict[str, dict] = {}
        for run in runs:
            for rc in projects_db.get_run_cards(conn, run["id"]):
                run_of_task.setdefault(rc["task_id"], run)
    tasks: dict[str, Any] = {}
    task_runs: dict[str, list] = {}
    attachments: dict[str, list] = {}
    try:
        with _board_conn(project) as bconn:
            for task in kanban_db.list_tasks(
                bconn, project_id=project.id, principal=principal,
                include_archived=True,
            ):
                tasks[task.id] = task
                task_runs[task.id] = kanban_db.list_runs(bconn, task.id)
                attachments[task.id] = kanban_db.list_attachments(bconn, task.id)
    except Exception:
        # A board that cannot be read degrades to deliveries only.
        tasks, task_runs, attachments = {}, {}, {}
    return {
        "outputs": {o["id"]: o for o in outputs},
        "deliveries": deliveries,
        "runs": {r["id"]: r for r in runs},
        "run_of_task": run_of_task,
        "tasks": tasks,
        "task_runs": task_runs,
        "attachments": attachments,
    }


def _preferred_claim(entries: list[tuple[str, dict]]) -> tuple[str, dict]:
    """The card that owns a path's claim when several cards name the same
    file: the one whose workspace contains it (the producer), then the
    earliest claim."""

    def _rank(entry: tuple[str, dict]) -> tuple[int, int]:
        _aid, item = entry
        ws = getattr(item["task"], "workspace_path", None)
        owns = 0 if ws and item["path"].startswith(ws.rstrip("/") + "/") else 1
        return (owns, item.get("created_at") or 0)

    return min(entries, key=_rank)


def _files_by_id(project, ctx: dict) -> dict[str, dict]:
    """Every local file the project's cards produced, keyed by artifact id,
    with the resolved path when it can be served. One row per physical
    path: a follow-on card that names another card's workspace file must
    not re-list it."""
    board = project.board_slug or None
    claims: dict[str, list[tuple[str, dict]]] = {}
    out: dict[str, dict] = {}
    for tid, task in ctx["tasks"].items():
        roots = _managed_roots(task, board)
        for f in _claimed_files(task, ctx["task_runs"].get(tid, [])):
            aid = f"f:{tid}:{_short_hash(f['path'])}"
            claims.setdefault(f["path"], []).append(
                (aid, {**f, "task": task, "serve": _servable(f["path"], roots)})
            )
        for att in ctx["attachments"].get(tid, []):
            aid = f"att:{att.id}"
            out[aid] = {
                "path": att.stored_path,
                "filename": att.filename,
                "content_type": att.content_type,
                "created_at": att.created_at,
                "created_by": att.uploaded_by,
                "task": task,
                "serve": _servable(att.stored_path, roots),
            }
    for entries in claims.values():
        aid, item = _preferred_claim(entries)
        out[aid] = item
    return out


def collect_artifacts(project, principal) -> list[dict]:
    ctx = _card_context(project, principal)
    return build_artifacts(project, ctx)


def build_artifacts(project, ctx: dict) -> list[dict]:
    slug = project.slug
    files = _files_by_id(project, ctx)
    path_to_id = {f["path"]: aid for aid, f in files.items() if aid.startswith("f:")}
    versions = assign_versions(ctx["deliveries"])
    attached_ids: set[str] = set()
    attached_urls: set[str] = set()
    items: list[dict] = []

    def _run_fields(run: Optional[dict]) -> dict:
        return {
            "run_id": run["id"] if run else None,
            "run_no": run["run_no"] if run else None,
        }

    def _card_fields(task) -> dict:
        return {
            "card_id": task.id if task else None,
            "card_title": task.title if task else None,
        }

    for d in ctx["deliveries"]:
        output = ctx["outputs"].get(d["output_id"])
        kind = d.get("link_kind")
        ref = d.get("link_ref")
        task = ctx["tasks"].get(d.get("task_id") or "")
        run = ctx["runs"].get(d.get("run_id") or "") or (
            ctx["run_of_task"].get(task.id) if task else None
        )
        href: Optional[str] = None
        location: Optional[str] = None
        name = d.get("label") or ref
        file_id: Optional[str] = None
        if kind == "url" and ref:
            href = ref
            attached_urls.add(ref)
            location = urlparse(ref).hostname
            if not d.get("label"):
                name = _url_meta(ref)[0]
        elif kind == "workspace" and ref:
            file_id = path_to_id.get(ref)
        elif kind == "attachment" and ref:
            file_id = f"att:{ref}" if f"att:{ref}" in files else None
        elif kind == "file" and ref:
            location = "Files"
        if file_id:
            attached_ids.add(file_id)
            f = files[file_id]
            task = task or f["task"]
            run = run or ctx["run_of_task"].get(f["task"].id)
            name = d.get("label") or f.get("filename") or os.path.basename(f["path"])
            location = _location(f["path"], f["task"])
            if f["serve"] is not None:
                href = _content_href(slug, f"d:{d['id']}")
        fname = (
            f["filename"] if file_id and files[file_id].get("filename")
            else (os.path.basename(ref) if ref and kind != "url" else ref)
        )
        ext = ext_of(fname) if kind != "url" else (ext_of(ref) or _url_meta(ref)[1] if ref else None)
        title = d.get("label") or (
            os.path.basename(str(name)) if kind != "url" and name else name
        ) or (output["title"] if output else "Delivery")
        items.append({
            "id": f"d:{d['id']}",
            "title": title,
            "kind": "deliverable",
            "ext": ext,
            "mime": mime_of(fname) if kind != "url" else (_url_meta(ref)[2] if ref else None),
            "href": href,
            "location": location,
            "source": "delivery",
            "link_kind": kind,
            "link_ref": ref,
            **_run_fields(run),
            **_card_fields(task),
            "output_id": d["output_id"],
            "output_title": output["title"] if output else None,
            "version": versions.get(d["id"]),
            "created_at": d.get("delivered_at") or 0,
            "created_by": d.get("profile"),
        })

    for aid, f in files.items():
        if aid in attached_ids:
            continue
        task = f["task"]
        name = f.get("filename") or os.path.basename(f["path"])
        is_att = aid.startswith("att:")
        items.append({
            "id": aid,
            "title": name,
            "kind": classify(name, attached=False),
            "ext": ext_of(name),
            "mime": mime_of(name, f.get("content_type")),
            "href": _content_href(slug, aid) if f["serve"] is not None else None,
            "location": "card attachments" if is_att else _location(f["path"], task),
            "source": "attachment" if is_att else "card_file",
            "link_kind": "attachment" if is_att else "workspace",
            "link_ref": aid.split(":", 1)[1] if is_att else f["path"],
            **_run_fields(ctx["run_of_task"].get(task.id)),
            **_card_fields(task),
            "output_id": None,
            "output_title": None,
            "version": None,
            "created_at": f.get("created_at") or task.created_at or 0,
            "created_by": f.get("created_by"),
        })

    seen_link_urls: set[str] = set()
    for tid, task in ctx["tasks"].items():
        for link in _card_links(task, ctx["task_runs"].get(tid, [])):
            if link["url"] in attached_urls or link["url"] in seen_link_urls:
                continue
            seen_link_urls.add(link["url"])
            title, ext, mime = _url_meta(link["url"])
            label = link["label"] or title
            items.append({
                "id": f"u:{tid}:{_short_hash(link['url'])}",
                "title": label,
                "kind": classify(label, attached=False),
                "ext": ext,
                "mime": mime,
                "href": link["url"],
                "location": urlparse(link["url"]).hostname,
                "source": "link",
                "link_kind": "url",
                "link_ref": link["url"],
                **_run_fields(ctx["run_of_task"].get(tid)),
                **_card_fields(task),
                "output_id": None,
                "output_title": None,
                "version": None,
                "created_at": link["created_at"] or task.created_at or 0,
                "created_by": link["created_by"],
            })

    return sort_newest(items)


def resolve_content(project, principal, artifact_id: str) -> Optional[Path]:
    """The servable path behind ``artifact_id``, or None."""
    ctx = _card_context(project, principal)
    files = _files_by_id(project, ctx)
    if artifact_id.startswith("d:"):
        did = artifact_id[2:]
        delivery = next((d for d in ctx["deliveries"] if d["id"] == did), None)
        if delivery is None:
            return None
        ref = delivery.get("link_ref")
        if delivery.get("link_kind") == "workspace":
            match = next(
                (f for aid, f in files.items() if aid.startswith("f:") and f["path"] == ref),
                None,
            )
        elif delivery.get("link_kind") == "attachment":
            match = files.get(f"att:{ref}")
        else:
            match = None
        return match["serve"] if match else None
    match = files.get(artifact_id)
    return match["serve"] if match else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{slug}/artifacts")
async def list_artifacts(request: Request) -> list[dict[str, Any]]:
    project, _role, _profiles, principal = await _require_read(request)
    return await asyncio.to_thread(collect_artifacts, project, principal)


@router.get("/{slug}/artifacts/{artifact_id}/content")
async def artifact_content(request: Request, artifact_id: str):
    project, _role, _profiles, principal = await _require_read(request)
    path = await asyncio.to_thread(resolve_content, project, principal, artifact_id)
    if path is None:
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        str(path),
        media_type=mime_of(path.name) or "application/octet-stream",
        filename=path.name,
        content_disposition_type="inline",
    )
