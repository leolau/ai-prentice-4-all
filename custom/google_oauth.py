"""Store-driven Google OAuth helpers for the custom pollers.

Credentials come from the unified credential store
(``resolve_for_service`` — entries opt in per service), with a read-only
fallback to the legacy ``$HERMES_HOME/google-workspace/credentials/<email>.json``
files until PR5 removes them. Pollers never write token files: refresh
rotation persists through the store's conditional single-writer update.
"""

import asyncio
import json
import os
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone

#: repo root, so hermes_cli is importable from the deployed tree.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAIL_SCOPE = 'https://mail.google.com'
CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar'

GoogleCredentials = namedtuple(
    'GoogleCredentials',
    ('client_id', 'client_secret', 'refresh_token', 'owner_user_id', 'name'),
)

_token_cache = {}


def _store():
    from hermes_cli.credential_store import default_credential_store

    return default_credential_store()


def store_accounts(service):
    """Opted-in store entries for ``service``; None when the store is unusable."""
    try:
        store = _store()
        entries = asyncio.run(store.resolve_for_service(service, provider='google'))
    except Exception as exc:  # noqa: BLE001 — pollers must keep running
        print(f"[google_oauth] credential store unavailable: {exc}")
        return None
    return [
        GoogleCredentials(
            str(e.payload.get('client_id') or ''),
            str(e.payload.get('client_secret') or ''),
            str(e.payload.get('refresh_token') or ''),
            e.owner_user_id,
            e.name,
        )
        for e in entries
    ]


def _legacy_workspace_file(email, required_scope):
    """The pre-store per-account file (calendar poller layout), read-only."""
    if not email:
        return None
    path = os.path.join(
        os.environ.get('WORKSPACE_MCP_CREDENTIALS_DIR',
                       os.path.join(os.environ.get('HERMES_HOME', os.path.expanduser('~')),
                                    'google-workspace', 'credentials')),
        f'{email}.json',
    )
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    scopes = data.get('scopes') or []
    if required_scope and scopes and required_scope not in scopes:
        print(f"[google_oauth] {email}: legacy credential lacks {required_scope}")
        return None
    client_id = str(data.get('client_id') or '')
    client_secret = str(data.get('client_secret') or '')
    refresh_token = str(data.get('refresh_token') or '')
    if not (client_id and client_secret and refresh_token):
        return None
    return GoogleCredentials(client_id, client_secret, refresh_token, '', email)


def credentials_for_email(service, email, required_scope=None):
    """One account's OAuth material for ``service``, or None."""
    accounts = store_accounts(service)
    if accounts is not None:
        for cred in accounts:
            if cred.name == email:
                return cred
        return None
    return _legacy_workspace_file(email, required_scope)


def get_access_token(cred):
    """A live access token, cached 2 minutes short of expiry."""
    key = (cred.owner_user_id, cred.name)
    cached = _token_cache.get(key)
    if cached:
        token, expiry = cached
        if datetime.now(timezone.utc) < expiry - timedelta(minutes=2):
            return token

    from hermes_cli.google_oauth import refresh_access_token

    doc = refresh_access_token(
        client_id=cred.client_id,
        client_secret=cred.client_secret,
        refresh_token=cred.refresh_token,
    )
    token = str(doc.get('access_token') or '')
    expiry = datetime.now(timezone.utc) + timedelta(
        seconds=int(doc.get('expires_in') or 3600)
    )
    if cred.owner_user_id:
        fragment = {'token': token}
        if doc.get('refresh_token'):
            fragment['refresh_token'] = doc['refresh_token']
        try:
            store = _store()
            asyncio.run(
                store.update_tokens(
                    'google',
                    cred.name,
                    owner_user_id=cred.owner_user_id,
                    old_refresh_token=cred.refresh_token,
                    payload_fragment=fragment,
                )
            )
        except Exception as exc:  # noqa: BLE001 — in-memory token still works
            print(f"[google_oauth] token writeback skipped: {exc}")
    _token_cache[key] = (token, expiry)
    return token


def invalidate(cred):
    _token_cache.pop((cred.owner_user_id, cred.name), None)


def xoauth2_string(user, access_token):
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"
