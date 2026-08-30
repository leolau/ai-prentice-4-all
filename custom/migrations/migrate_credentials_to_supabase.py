"""One-shot box migration: legacy Google credentials -> unified store.

Imports, owner-bound and idempotent (existing entries are never overwritten):

* ``$HERMES_HOME/google-workspace/credentials/<email>.json`` (the calendar
  poller's Workspace-MCP layout) -> ``services=["calendar"]``, adding
  ``email`` only when the stored scopes already include
  ``https://mail.google.com/``.
* ``$HERMES_HOME/google_token.json`` (legacy skill token) ->
  ``services=["workspace"]``, entry named via Google userinfo when reachable.
* ``GCAL_*`` env-provisioned refresh tokens named in
  ``/opt/data/calendar/config.json`` -> same treatment as the workspace files.
* The OAuth client (legacy ``google_client_secret.json`` or ``GCAL_*`` env)
  -> ``$HERMES_HOME/google-workspace/client_secret.json``.

Legacy files are left in place (PR5 removes them after verification).

Run as the service user with the profile env, e.g.::

    sudo runuser -u hermes -- env HERMES_HOME=/opt/data/hermes-home-staging \
        /opt/data/hermes-agent/.venv/bin/python \
        -m custom.migrations.migrate_credentials_to_supabase
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hermes_constants import get_hermes_home

MAIL_SCOPE = "https://mail.google.com"


def _scopes_of(payload: dict) -> set:
    raw = payload.get("scopes") or payload.get("scope") or []
    if isinstance(raw, str):
        return {s for s in raw.split() if s}
    return {str(s) for s in raw}


def _payload_from_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


async def run() -> int:
    from hermes_cli.access import PrincipalStore
    from hermes_cli.credential_store import default_credential_store

    store = default_credential_store()
    await store.initialize()
    if store.backend != "supabase":
        print(f"backend is {store.backend!r}; nothing to migrate into Supabase.")
        return 0

    owner = await PrincipalStore(store._store).get_owner()
    if owner is None:
        print("ERROR: no enrolled owner principal; cannot bind ownership.")
        return 1
    print(f"owner: {owner.user_id}")

    home = get_hermes_home()
    imported = 0

    async def put_if_absent(name: str, payload: dict, services: list) -> None:
        nonlocal imported
        existing = await store.get(owner, "google", name)
        if existing is not None:
            print(f"  skip {name}: already in store")
            return
        await store.put(
            owner,
            provider="google",
            name=name,
            kind="google-oauth2",
            payload=payload,
            services=services,
            visibility=owner.private_visibility,
        )
        imported += 1
        print(f"  imported {name} services={services}")

    # 1. Workspace-MCP per-account files (calendar poller layout).
    ws_dir = home / "google-workspace" / "credentials"
    if ws_dir.is_dir():
        for path in sorted(ws_dir.glob("*.json")):
            payload = _payload_from_file(path)
            if not payload.get("refresh_token"):
                continue
            email = unquote(path.stem)
            scopes = _scopes_of(payload)
            services = ["calendar"]
            if MAIL_SCOPE in scopes:
                services.append("email")
            await put_if_absent(email, payload, sorted(services))

    # 2. GCAL env-provisioned accounts from the calendar config.
    cal_cfg = Path("/opt/data/calendar/config.json")
    if cal_cfg.exists():
        try:
            cfg = json.loads(cal_cfg.read_text())
        except (OSError, ValueError):
            cfg = {}
        for account in (cfg.get("calendar") or {}).get("accounts", []):
            env_name = str(account.get("refresh_token_env") or "")
            email = str(account.get("email") or "")
            refresh = os.getenv(env_name, "").strip() if env_name else ""
            if not (email and refresh):
                continue
            payload = {
                "type": "authorized_user",
                "refresh_token": refresh,
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": os.getenv("GCAL_CLIENT_ID", "").strip(),
                "client_secret": os.getenv("GCAL_CLIENT_SECRET", "").strip(),
                "scopes": ["https://www.googleapis.com/auth/calendar"],
            }
            if payload["client_id"] and payload["client_secret"]:
                await put_if_absent(email, payload, ["calendar"])

    # 3. Legacy skill token.
    legacy = home / "google_token.json"
    if legacy.exists():
        payload = _payload_from_file(legacy)
        if payload.get("refresh_token"):
            name = os.environ.get("GOOGLE_WORKSPACE_ACCOUNT", "").strip() or "default"
            await put_if_absent(name, payload, ["workspace"])

    # 4. Client secret into the store location.
    dest = home / "google-workspace" / "client_secret.json"
    if not dest.exists():
        src = home / "google_client_secret.json"
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            os.chmod(dest, 0o600)
            print("  copied client secret into google-workspace/")
        elif os.getenv("GCAL_CLIENT_ID", "").strip():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": os.getenv("GCAL_CLIENT_ID", "").strip(),
                            "client_secret": os.getenv("GCAL_CLIENT_SECRET", "").strip(),
                        }
                    },
                    indent=2,
                )
            )
            os.chmod(dest, 0o600)
            print("  wrote client secret from GCAL_* env")

    print(f"done: {imported} new entr{'y' if imported == 1 else 'ies'}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
