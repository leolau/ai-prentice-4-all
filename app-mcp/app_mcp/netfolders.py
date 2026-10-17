"""Network folders for the Folder Bridge tools: SMB shares the Hermes server
mounts read-only through a user's Fortinet VPN at ``<root>/net-<slug>``.

Unlike browser folders (served by the Folder Bridge tab), these are served
here, server-side, so they work while ``/files/bridge`` is closed. app-mcp
runs as ``hermes`` without sudo: it reads already-mounted paths directly and
asks the Hermes dashboard (root, loopback) to mount a folder or import a file
through ``/api/internal/vpn/folders*`` with the bearer token
``APP_MCP_HERMES_TOKEN``. Result shapes match the browser's
(``agent-home/src/lib/folder-bridge/types.ts``). Paths are relative to the
folder root; ``..`` and symlinks are refused at every level.
"""
from __future__ import annotations

import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^net-[a-z0-9][a-z0-9-]{0,40}$")
MAX_DEPTH = 12
MAX_VISITED = 5000
SNIPPET_BYTES = 400
SEARCH_SECONDS = 20.0
TEXT_LIKE_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".csv", ".log", ".ts",
    ".tsx", ".js", ".jsx", ".py", ".rs", ".go", ".java", ".c", ".cpp", ".h",
    ".css", ".html", ".xml", ".sh",
}
NOT_CONFIGURED = (
    "Network folders are not available: app-mcp has no APP_MCP_HERMES_TOKEN "
    "(see docs/deployment/PRODUCTION.md)."
)


class NetFolderError(Exception):
    """A user-safe failure handed back to the agent as ``{ok: false, detail}``."""


def is_net_id(folder_id: Any) -> bool:
    return isinstance(folder_id, str) and folder_id.startswith("net-")


def mount_root() -> Path:
    return Path(os.environ.get("APP_MCP_NET_FOLDER_ROOT", "/opt/data/vpn-files"))


# ---------------------------------------------------------------------------
# Hermes internal API
# ---------------------------------------------------------------------------


class HermesClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 transport: Any = None) -> None:
        self.base_url = (base_url or os.environ.get("APP_MCP_HERMES_URL")
                         or "http://127.0.0.1:9119").rstrip("/")
        self.token = token if token is not None else os.environ.get("APP_MCP_HERMES_TOKEN", "")
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def _request(self, method: str, path: str, *, json: Any = None,
                       timeout: float = 30.0) -> Any:
        import httpx

        if not self.token:
            raise NetFolderError(NOT_CONFIGURED)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=timeout, transport=self.transport
            ) as client:
                resp = await client.request(
                    method, path, json=json,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except httpx.HTTPError as exc:
            raise NetFolderError(f"The Hermes server did not answer ({type(exc).__name__}).") from None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            if resp.status_code == 404:
                raise NetFolderError(detail or "Unknown network folder.")
            raise NetFolderError(detail or f"The Hermes server refused (HTTP {resp.status_code}).")
        return body

    async def list_folders(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/internal/vpn/folders")
        return list(body.get("folders") or [])

    async def ensure(self, folder_id: str) -> dict[str, Any]:
        body = await self._request("POST", "/api/internal/vpn/folders/ensure",
                                   json={"id": folder_id}, timeout=240.0)
        return dict(body.get("folder") or {})

    async def import_file(self, folder_id: str, path: str, approved: bool) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/internal/vpn/folders/import",
            json={"id": folder_id, "path": path, "approved": approved}, timeout=3600.0,
        )


def folder_record(view: dict[str, Any]) -> dict[str, Any]:
    """A network folder as ``folder_bridge_list_folders`` reports it."""
    return {
        "id": view.get("id"),
        "label": view.get("name"),
        "permission": "granted",
        "trusted": bool(view.get("trusted_for_import")),
        "kind": "network",
        "vpn": view.get("vpn"),
        "mounted": bool(view.get("mounted")),
    }


def is_mounted(path: Path, proc_mounts: str = "/proc/self/mounts") -> bool:
    target = os.path.abspath(path)
    try:
        with open(proc_mounts, encoding="utf-8") as fh:
            for line in fh:
                bits = line.split()
                if len(bits) > 1 and bits[1].replace("\\040", " ") == target:
                    return True
    except OSError:
        pass
    return False


async def ensure_root(client: HermesClient, folder_id: str, *, mounted=is_mounted) -> Path:
    """The folder's mount root, asking Hermes to mount it first if needed."""
    if not ID_RE.fullmatch(folder_id or ""):
        raise NetFolderError("Unknown network folder.")
    root = mount_root() / folder_id
    if mounted(root):
        return root
    view = await client.ensure(folder_id)
    status = view.get("status") or {}
    if not view.get("mounted"):
        raise NetFolderError(status.get("error") or "The network folder could not be mounted.")
    return root


# ---------------------------------------------------------------------------
# Filesystem operations (blocking; callers run them in a thread)
# ---------------------------------------------------------------------------


def _parts(path: str | None) -> list[str]:
    text = (path or "").replace("\\", "/")
    parts: list[str] = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." or "\x00" in p for p in parts):
        raise NetFolderError("Paths must stay inside the folder ('..' is not allowed).")
    return parts


def resolve(root: Path, path: str | None, *, want: str) -> Path:
    """``root/path`` with no symlink anywhere on the way; ``want`` is
    ``"dir"`` or ``"file"``."""
    parts = _parts(path)
    if want == "file" and not parts:
        raise NetFolderError("Empty file path.")
    current = root
    for part in parts:
        current = current / part
        try:
            st = os.lstat(current)
        except OSError:
            raise NetFolderError(f"Not found: {'/'.join(parts)}") from None
        if stat.S_ISLNK(st.st_mode):
            raise NetFolderError("Symbolic links are not followed in network folders.")
    real_root = os.path.realpath(root)
    real = os.path.realpath(current)
    if real != real_root and not real.startswith(real_root + os.sep):
        raise NetFolderError("Paths must stay inside the folder.")
    st = os.stat(current) if parts else os.stat(root)
    if want == "dir" and not stat.S_ISDIR(st.st_mode):
        raise NetFolderError(f"Not a directory: {'/'.join(parts)}")
    if want == "file" and not stat.S_ISREG(st.st_mode):
        raise NetFolderError(f"Not a file: {'/'.join(parts)}")
    return current


def iso(ts: float) -> str:
    return (datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def _join(base: str, name: str) -> str:
    return f"{base}/{name}" if base else name


def list_directory(root: Path, folder_id: str, path: str | None) -> dict[str, Any]:
    directory = resolve(root, path, want="dir")
    rel = "/".join(_parts(path))
    entries = []
    with os.scandir(directory) as it:
        for entry in sorted(it, key=lambda e: e.name):
            if entry.is_symlink():
                continue
            entry_path = _join(rel, entry.name)
            if entry.is_dir(follow_symlinks=False):
                entries.append({"name": entry.name, "path": entry_path, "kind": "directory"})
            elif entry.is_file(follow_symlinks=False):
                st = entry.stat(follow_symlinks=False)
                entries.append({"name": entry.name, "path": entry_path, "kind": "file",
                                "size": st.st_size, "modifiedAt": iso(st.st_mtime)})
    return {"ok": True, "entries": entries}


def name_matcher(query: str):
    needle = query.strip().lower()
    if not needle:
        return lambda name: True
    if not re.search(r"[*?]", needle):
        return lambda name: needle in name.lower()
    pattern = "".join(
        ".*" if part == "*" else "." if part == "?" else re.escape(part)
        for part in re.split(r"([*?])", needle)
    )
    compiled = re.compile(f"^{pattern}$", re.IGNORECASE | re.DOTALL)
    return lambda name: bool(compiled.match(name))


def path_matcher(query: str, folder_label: str):
    needle = query.strip()
    if not needle:
        return lambda rel: True
    if "/" in needle:
        full = name_matcher(needle.lstrip("/"))
        return lambda rel: full(f"{folder_label}/{rel}")
    seg = name_matcher(needle)
    return lambda rel: any(seg(s) for s in [folder_label, *rel.split("/")])


def _snippet(path: Path, name: str) -> str | None:
    if os.path.splitext(name)[1].lower() not in TEXT_LIKE_EXTENSIONS:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read(SNIPPET_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None


def search_folder(root: Path, folder_id: str, folder_label: str, query: str,
                  extensions: list[str], limit: int) -> list[dict[str, Any]]:
    matches_path = path_matcher(query, folder_label)
    want_ext = {e.lower() for e in extensions}
    matches: list[dict[str, Any]] = []
    visited = 0
    deadline = time.monotonic() + SEARCH_SECONDS

    def walk(directory: Path, rel: str, depth: int) -> None:
        nonlocal visited
        if depth > MAX_DEPTH:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if len(matches) >= limit or visited >= MAX_VISITED or time.monotonic() > deadline:
                return
            visited += 1
            if entry.is_symlink():
                continue
            entry_rel = _join(rel, entry.name)
            if entry.is_dir(follow_symlinks=False):
                walk(Path(entry.path), entry_rel, depth + 1)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if want_ext and os.path.splitext(entry.name)[1].lower() not in want_ext:
                continue
            if not matches_path(entry_rel):
                continue
            st = entry.stat(follow_symlinks=False)
            match = {"folderId": folder_id, "folderLabel": folder_label, "path": entry_rel,
                     "size": st.st_size, "modifiedAt": iso(st.st_mtime)}
            snippet = _snippet(Path(entry.path), entry.name)
            if snippet is not None:
                match["snippet"] = snippet
            matches.append(match)

    walk(root, "", 0)
    return matches


def read_file(root: Path, folder_id: str, path: str, max_bytes: int) -> dict[str, Any]:
    target = resolve(root, path, want="file")
    max_bytes = max(0, int(max_bytes))
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        size = os.fstat(fd).st_size
        buf = os.read(fd, max_bytes) if max_bytes else b""
    finally:
        os.close(fd)
    rel = "/".join(_parts(path))
    truncated = size > max_bytes
    probe = buf[: max(0, len(buf) - 4)] if truncated else buf
    base = {"ok": True, "folderId": folder_id, "path": rel, "encoding": "utf-8", "size": size}
    try:
        content = probe.decode("utf-8")
    except UnicodeDecodeError:
        return {**base, "content": "", "truncated": False, "binary": True}
    return {**base, "content": content, "truncated": truncated}


def file_metadata(root: Path, folder_id: str, path: str) -> dict[str, Any]:
    target = resolve(root, path, want="file")
    st = os.stat(target)
    return {"ok": True, "folderId": folder_id, "path": "/".join(_parts(path)),
            "size": st.st_size, "modifiedAt": iso(st.st_mtime)}
