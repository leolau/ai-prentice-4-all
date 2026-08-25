"""Optional bridge from the skill scripts to the unified credential store.

The skill stays portable: when the repo's ``hermes_cli.credential_store`` is
importable (running inside a Hermes checkout / venv), tokens live in the
unified store (Supabase when configured, else the file backend) and every
read/write goes through it. When it is not importable (skill copied
standalone), callers fall back to the legacy ``google_token.json`` paths.

Design doc: ``docs/design/unified-credential-store.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path(os.environ.get("HERMES_AGENT_ROOT") or _SCRIPTS_DIR.parents[3])
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # repo-internal modules; absent when the skill runs standalone
    from hermes_cli import credential_store as _cs
    from hermes_cli import google_oauth as _go

    AVAILABLE = True
except Exception:  # noqa: BLE001 — standalone skill installs
    _cs = None
    _go = None
    AVAILABLE = False

from _hermes_home import get_hermes_home

LEGACY_TOKEN_PATH = get_hermes_home() / "google_token.json"
LEGACY_CLIENT_SECRET_PATH = get_hermes_home() / "google_client_secret.json"


def _run(coro):
    return asyncio.run(coro)


def backend_name() -> str:
    if not AVAILABLE:
        return "legacy"
    return str(_cs.default_credential_store().backend)


def _owner_principal():
    """The owner principal for skill-side service reads."""
    from hermes_cli.access import Principal

    store = _cs.default_credential_store()
    if store.backend == "supabase":
        from hermes_cli.access import PrincipalStore

        return _run(PrincipalStore(store._store).get_owner())
    return Principal(user_id="owner", display="owner", role="owner")


def google_entries(account: Optional[str] = None) -> List[Any]:
    """google-oauth2 entries visible to the owner, optionally filtered."""
    if not AVAILABLE:
        return []
    principal = _owner_principal()
    if principal is None:
        return []
    store = _cs.default_credential_store()
    entries = [
        e
        for e in _run(store.list(principal))
        if e.kind == "google-oauth2" and e.provider == "google"
    ]
    if account:
        entries = [e for e in entries if e.name == account]
    return entries


def pick_entry(account: Optional[str] = None) -> Optional[Any]:
    account = account or os.environ.get("GOOGLE_WORKSPACE_ACCOUNT") or None
    entries = google_entries(account)
    if account:
        return entries[0] if entries else None
    return entries[0] if len(entries) == 1 else (entries[0] if entries else None)


def _expiry_dt(payload: Dict[str, Any]) -> Optional[datetime]:
    raw = payload.get("expiry")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def refresh_entry(cred: Any) -> Dict[str, Any]:
    """Return a live payload, persisting a refresh via the single-writer update."""
    payload = dict(cred.payload)
    expiry = _expiry_dt(payload)
    if payload.get("token") and expiry and expiry > datetime.now(timezone.utc):
        return payload
    old_refresh = str(payload.get("refresh_token") or "")
    doc = _go.refresh_access_token(
        client_id=str(payload.get("client_id") or ""),
        client_secret=str(payload.get("client_secret") or ""),
        refresh_token=old_refresh,
    )
    fragment = {"token": doc.get("access_token", "")}
    if doc.get("refresh_token"):
        fragment["refresh_token"] = doc["refresh_token"]
    store = _cs.default_credential_store()
    won = _run(
        store.update_tokens(
            "google",
            cred.name,
            owner_user_id=cred.owner_user_id,
            old_refresh_token=old_refresh,
            payload_fragment=fragment,
        )
    )
    payload.update(fragment)
    if not won:
        # A concurrent writer rotated the token; re-read the authoritative row.
        fresh = pick_entry(cred.name)
        if fresh is not None:
            payload = dict(fresh.payload)
    return payload


def put_entry(name: str, payload: Dict[str, Any], services: List[str]) -> Any:
    principal = _owner_principal()
    store = _cs.default_credential_store()
    return _run(
        store.put(
            principal,
            provider="google",
            name=name,
            kind="google-oauth2",
            payload=payload,
            services=services,
        )
    )


def delete_entry(name: str) -> bool:
    principal = _owner_principal()
    store = _cs.default_credential_store()
    return _run(store.delete(principal, "google", name))


def materialized_token_file(account: Optional[str] = None) -> Optional[Path]:
    """A 0600 authorized_user file for google-auth/gws consumers, or None."""
    entry = pick_entry(account)
    if entry is None:
        return None
    payload = refresh_entry(entry)
    root = get_hermes_home() / "google-workspace-materialized"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    dest = root / (entry.name.replace("/", "_") + ".json")
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".tok-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(_cs_redacted_none(payload), fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def _cs_redacted_none(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    if not normalized.get("type"):
        normalized["type"] = "authorized_user"
    return normalized
