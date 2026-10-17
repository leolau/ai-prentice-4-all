"""Fortinet SSL-VPN connections for Hermes ("Private networks (VPN)").

A ``fortinet-vpn`` credential (``hermes_cli/credential_store.py``) holds the
gateway login and optional SMB share. This module turns one into a split
tunnel plus a read-only mount at ``/opt/data/vpn-files/<slug>`` by driving the
root helper ``deploy/hermes-vpn-helper`` (installed at
``/usr/local/sbin/hermes-vpn-helper``): directly when running as root (the
dashboard API), through ``sudo -n`` otherwise (``hermes vpn`` in an agent's
terminal). The helper validates everything again; nothing here is trusted by
it.

Shared by ``hermes_cli/vpn_api.py`` (``/api/vpn``) and
``hermes_cli/vpn_cmd.py`` (``hermes vpn``). Small non-secret bookkeeping
(slug assignment, last_used, last_check, last error) lives in
``$HERMES_HOME/vpn/state.json``.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from hermes_cli.credential_store import (
    Credential,
    CredentialError,
    default_credential_store,
)

KIND = "fortinet-vpn"
PROVIDER = "fortinet"
HELPER_PATH = "/usr/local/sbin/hermes-vpn-helper"
MOUNT_ROOT = "/opt/data/vpn-files"
DEFAULT_PORT = 443
DEFAULT_IDLE_MINUTES = 30
HELPER_TIMEOUT = 150
STATES = ("connected", "connecting", "disconnected", "error", "needs_cert")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HELPER_SPEC_KEYS = (
    "host", "username", "password", "realm", "trusted_cert", "share_path",
    "share_username", "share_password", "share_domain",
)


class VpnError(Exception):
    """Bad input for a VPN operation (HTTP 400 / CLI exit 1)."""


class VpnNotFound(VpnError):
    """No readable fortinet-vpn entry by that name (HTTP 404)."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Slugs + bookkeeping state
# ---------------------------------------------------------------------------


def slug_base(name: str) -> str:
    """``"Office files"`` -> ``"office-files"``; non-ASCII names hash."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:41].strip("-")
    if not text:
        text = "vpn-" + hashlib.sha256(name.encode()).hexdigest()[:8]
    if text.startswith("net-"):
        # ``net-*`` is reserved for network-folder mounts (vpn_folders.py).
        text = ("vpn-" + text)[:41].rstrip("-")
    return text


def _state_dir() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "vpn"


def _match_home_owner(path: Path) -> None:
    """Root (the dashboard) writes files the hermes CLI must still update."""
    if os.geteuid() != 0:
        return
    with contextlib.suppress(OSError):
        home = path.parent.parent.stat()
        os.chown(path, home.st_uid, home.st_gid)


@contextlib.contextmanager
def _locked_state() -> Iterator[Dict[str, Any]]:
    directory = _state_dir()
    fresh = not directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if fresh:
        os.chmod(directory, 0o700)
        _match_home_owner(directory)
    lock_path = directory / "state.lock"
    with open(lock_path, "a+", encoding="utf-8") as lock:
        _match_home_owner(lock_path)
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "state.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                doc = {}
        except (OSError, ValueError):
            doc = {}
        doc.setdefault("slugs", {})
        doc.setdefault("entries", {})
        before = json.dumps(doc, sort_keys=True)
        yield doc
        if json.dumps(doc, sort_keys=True) != before:
            fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".state-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
            _match_home_owner(path)


def _key(cred: Credential) -> str:
    return f"{cred.owner_user_id}/{cred.name}"


def slug_for(cred: Credential) -> str:
    """Stable, box-unique slug for an entry (assigned on first sight)."""
    key = _key(cred)
    with _locked_state() as doc:
        slugs: Dict[str, str] = doc["slugs"]
        if key in slugs:
            return slugs[key]
        taken = set(slugs.values())
        slug = slug_base(cred.name)
        if slug in taken:
            digest = hashlib.sha256(key.encode()).hexdigest()
            slug = f"{slug[:34].rstrip('-')}-{digest[:6]}"
            n = 2
            while slug in taken:
                slug = f"{slug[:34].rstrip('-')}-{digest[:4]}{n}"
                n += 1
        slugs[key] = slug
        return slug


def _record(slug: str, **fields: Any) -> Dict[str, Any]:
    with _locked_state() as doc:
        entry = doc["entries"].setdefault(slug, {})
        entry.update(fields)
        return dict(entry)


def _entry(slug: str) -> Dict[str, Any]:
    with _locked_state() as doc:
        return dict(doc["entries"].get(slug) or {})


def _drop(cred: Credential) -> Optional[str]:
    with _locked_state() as doc:
        slug = doc["slugs"].pop(_key(cred), None)
        if slug:
            doc["entries"].pop(slug, None)
        return slug


def mount_path(slug: str) -> str:
    return f"{MOUNT_ROOT}/{slug}"


def normalize_sha256(value: Any) -> str:
    digest = re.sub(r"[\s:]", "", str(value or "")).lower()
    if not _SHA256_RE.fullmatch(digest):
        raise VpnError("sha256 must be a 64-character hex certificate digest")
    return digest


# ---------------------------------------------------------------------------
# Helper invocation
# ---------------------------------------------------------------------------


def helper_spec(cred: Credential, slug: str) -> Dict[str, Any]:
    """The helper's ``up`` spec: only the keys it accepts, never extras."""
    payload = cred.payload
    spec: Dict[str, Any] = {"slug": slug}
    for key in _HELPER_SPEC_KEYS:
        if payload.get(key) not in (None, ""):
            spec[key] = payload[key]
    spec["port"] = int(payload.get("port") or DEFAULT_PORT)
    spec["routes"] = list(payload.get("routes") or [])
    idle = payload.get("idle_minutes")
    spec["idle_minutes"] = DEFAULT_IDLE_MINUTES if idle is None else int(idle)
    return spec


def _helper_argv(args: List[str]) -> List[str]:
    if os.geteuid() == 0:
        return [HELPER_PATH, *args]
    return ["sudo", "-n", HELPER_PATH, *args]


def _error_doc(message: str) -> Dict[str, Any]:
    return {"state": "error", "error": message}


def run_helper(
    command: str, slug: str, *, spec: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Run one helper command; returns its JSON doc (``state``/``error``…).

    Raises :class:`VpnError` only for input the helper rejected (exit 2).
    Everything else — helper missing, sudo refused, timeout — comes back as an
    ``error`` doc so callers render it like a failed connection.
    """
    if not _SLUG_RE.fullmatch(slug):
        raise VpnError("invalid VPN slug")
    if not os.path.exists(HELPER_PATH):
        return _error_doc(
            "VPN support is not installed on this server (hermes-vpn-helper missing)"
        )
    spool: Optional[str] = None
    try:
        if spec is not None:
            spool = tempfile.mkdtemp(prefix="hermes-vpn-")
            fd, spec_path = tempfile.mkstemp(dir=spool, prefix="spec-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)
            os.chmod(spec_path, 0o600)
            args = [command, spec_path]
        else:
            args = [command, slug]
        try:
            proc = subprocess.run(
                _helper_argv(args),
                capture_output=True,
                text=True,
                timeout=HELPER_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return _error_doc("The VPN helper timed out")
        except OSError as exc:
            return _error_doc(f"Could not run the VPN helper ({exc.strerror or exc})")
    finally:
        if spool:
            shutil.rmtree(spool, ignore_errors=True)
    doc: Dict[str, Any] = {}
    lines = (proc.stdout or "").strip().splitlines()
    if lines:
        with contextlib.suppress(ValueError):
            parsed = json.loads(lines[-1])
            if isinstance(parsed, dict):
                doc = parsed
    if proc.returncode == 2 and doc.get("bad_input"):
        raise VpnError(str(doc.get("error") or "invalid VPN settings"))
    if proc.returncode != 0 or not doc:
        stderr = (proc.stderr or "").strip()
        if "password is required" in stderr or "not allowed" in stderr:
            return _error_doc(
                "This server is not set up to run the VPN helper via sudo "
                "(install deploy/sudoers.d/hermes-vpn)"
            )
        return _error_doc(str(doc.get("error") or stderr[:200] or "VPN helper failed"))
    return doc


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def build_view(
    cred: Credential, slug: str, doc: Dict[str, Any], entry: Dict[str, Any]
) -> Dict[str, Any]:
    """The contract's VpnView. Never includes password/share_password."""
    payload = cred.payload
    state = doc.get("state") if doc.get("state") in STATES else "error"
    status: Dict[str, Any] = {
        "state": state,
        "since": doc.get("since") if state == "connected" else None,
        "local_ip": doc.get("local_ip") if state == "connected" else None,
        "error": doc.get("error") if state in ("error", "needs_cert") else None,
        "last_used": entry.get("last_used"),
        "last_check": entry.get("last_check"),
    }
    if state == "needs_cert":
        cert = doc.get("cert") or {}
        status["cert"] = {
            k: cert.get(k) for k in ("subject", "issuer", "not_after", "sha256")
        }
    idle = payload.get("idle_minutes")
    auto = payload.get("auto_connect")
    return {
        "name": cred.name,
        "slug": slug,
        "host": payload.get("host"),
        "port": int(payload.get("port") or DEFAULT_PORT),
        "realm": payload.get("realm") or None,
        "routes": list(payload.get("routes") or []),
        "share_path": payload.get("share_path") or None,
        "mount_path": mount_path(slug),
        "auto_connect": True if auto is None else bool(auto),
        "idle_minutes": DEFAULT_IDLE_MINUTES if idle is None else int(idle),
        "visibility": cred.visibility,
        "owner_user_id": cred.owner_user_id,
        "trusted_cert": payload.get("trusted_cert") or None,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Operations (principal-scoped; the store enforces visibility)
# ---------------------------------------------------------------------------


async def readable_vpns(principal: Any) -> List[Credential]:
    entries = await default_credential_store().list(principal)
    return [e for e in entries if e.kind == KIND and e.provider == PROVIDER]


async def find_vpn(principal: Any, name: str, *, loose: bool = False) -> Credential:
    """The caller's readable entry ``name``, preferring their own.

    ``loose`` (CLI) also matches case-insensitively and by slug.
    """
    entries = await readable_vpns(principal)
    own = getattr(principal, "user_id", None)

    def pick(matches: List[Credential]) -> Optional[Credential]:
        if not matches:
            return None
        mine = [m for m in matches if m.owner_user_id == own]
        if len(mine) == 1 or len(matches) == 1:
            return (mine or matches)[0]
        if loose:
            raise VpnError(f"more than one VPN is called '{name}'")
        return (mine or matches)[0]

    found = pick([e for e in entries if e.name == name])
    if found is None and loose:
        folded = name.strip().casefold()
        found = pick([e for e in entries if e.name.casefold() == folded])
        if found is None:
            found = pick([e for e in entries if slug_base(e.name) == slug_base(name)])
    if found is None:
        raise VpnNotFound(f"no VPN named '{name}'")
    return found


async def _helper(command: str, slug: str, spec: Optional[Dict[str, Any]] = None):
    return await asyncio.to_thread(run_helper, command, slug, spec=spec)


async def view_of(cred: Credential) -> Dict[str, Any]:
    slug = slug_for(cred)
    doc = await _helper("status", slug)
    return build_view(cred, slug, doc, _entry(slug))


async def list_views(principal: Any) -> List[Dict[str, Any]]:
    return [await view_of(cred) for cred in await readable_vpns(principal)]


async def _connect(cred: Credential) -> Dict[str, Any]:
    slug = slug_for(cred)
    doc = await _helper("up", slug, helper_spec(cred, slug))
    fields: Dict[str, Any] = {"last_error": doc.get("error")}
    if doc.get("state") == "connected":
        fields["last_used"] = _now()
    entry = _record(slug, **fields)
    return build_view(cred, slug, doc, entry)


async def connect(principal: Any, name: str, *, loose: bool = False) -> Dict[str, Any]:
    return await _connect(await find_vpn(principal, name, loose=loose))


async def disconnect(principal: Any, name: str, *, loose: bool = False) -> Dict[str, Any]:
    cred = await find_vpn(principal, name, loose=loose)
    slug = slug_for(cred)
    doc = await _helper("down", slug)
    return build_view(cred, slug, doc, _entry(slug))


async def test(principal: Any, name: str, *, loose: bool = False) -> Dict[str, Any]:
    """Connect if needed, then list the share; records ``last_check``."""
    cred = await find_vpn(principal, name, loose=loose)
    slug = slug_for(cred)
    doc = await _helper("status", slug)
    if doc.get("state") != "connected":
        await _connect(cred)
    doc = await _helper("check", slug)
    check = doc.pop("check", None)
    if not isinstance(check, dict):
        check = {
            "at": _now(),
            "ok": False,
            "message": doc.get("error") or "VPN is not connected",
            "file_count": None,
        }
    fields: Dict[str, Any] = {"last_check": check}
    if check.get("ok"):
        fields["last_used"] = _now()
    entry = _record(slug, **fields)
    return build_view(cred, slug, doc, entry)


async def trust(principal: Any, name: str, sha256: Any) -> Dict[str, Any]:
    """Store ``trusted_cert`` on the caller's own entry, then connect."""
    digest = normalize_sha256(sha256)
    cred = await find_vpn(principal, name)
    if cred.owner_user_id != getattr(principal, "user_id", None):
        raise VpnError(f"only the owner of '{name}' can trust its certificate")
    try:
        updated = await default_credential_store().put(
            principal,
            provider=PROVIDER,
            name=cred.name,
            kind=KIND,
            payload={**cred.payload, "trusted_cert": digest},
            services=list(cred.services),
            visibility=cred.visibility,
        )
    except CredentialError as exc:
        raise VpnError(str(exc)) from exc
    return await _connect(updated)


async def forget(cred: Credential) -> None:
    """Disconnect/unmount a VPN whose credential is being deleted."""
    with _locked_state() as doc:
        known = _key(cred) in doc["slugs"]
    if known:
        await _helper("down", slug_for(cred))
    _drop(cred)
