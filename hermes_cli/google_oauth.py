"""Stdlib-only Google OAuth2 mechanics for the credential store.

Extracted from the proven ``google-workspace`` skill flow (PKCE, manual
code-paste against a localhost redirect) so the HTTP surface, the skill, and
the pollers all share one implementation. No Google client libraries: the
authorization URL, exchange, refresh, and userinfo calls are plain HTTPS.

Design doc: ``docs/design/unified-credential-store.md`` §7.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REDIRECT_URI = "http://localhost:1"
HTTP_TIMEOUT = 15.0

#: The skill's historical full-workspace scope set.
WORKSPACE_SCOPES: List[str] = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
]

#: Scopes granted per opt-in service. ``mail.google.com`` is the IMAP/SMTP
#: XOAUTH2 scope — the gmail.* API scopes do NOT authorize IMAP.
SCOPES_BY_SERVICE: Dict[str, List[str]] = {
    "email": ["https://mail.google.com/"],
    "calendar": ["https://www.googleapis.com/auth/calendar"],
    "workspace": WORKSPACE_SCOPES,
}


class GoogleOAuthError(Exception):
    """OAuth flow failure with a human-readable reason."""


def scopes_for_services(services: List[str]) -> List[str]:
    """Sorted union of scopes for the requested opt-in services."""
    union: set = set()
    for service in services:
        union.update(SCOPES_BY_SERVICE.get(service, []))
    return sorted(union)


def generate_pkce() -> Dict[str, str]:
    """Fresh state + PKCE verifier/challenge (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier).digest())
        .rstrip(b"=")
    )
    return {
        "state": secrets.token_urlsafe(24),
        "code_verifier": verifier.decode(),
        "code_challenge": challenge.decode(),
    }


def build_authorization_url(
    *,
    client_id: str,
    scopes: List[str],
    state: str,
    code_challenge: str,
    login_hint: Optional[str] = None,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def parse_code_or_url(text: str) -> Tuple[str, Optional[str]]:
    """Accept a bare code or the full redirect URL; return (code, state)."""
    text = (text or "").strip()
    if not text:
        raise GoogleOAuthError("empty authorization code")
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urllib.parse.urlparse(text)
        qs = urllib.parse.parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        state = (qs.get("state") or [None])[0]
        if not code:
            raise GoogleOAuthError("redirect URL carries no ?code= parameter")
        return code, state
    return text, None


def _post_form(url: str, data: Dict[str, str]) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:400]
        except OSError:
            pass
        raise GoogleOAuthError(
            f"token endpoint HTTP {exc.code}: {detail or exc.reason}"
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GoogleOAuthError(f"token endpoint unreachable: {exc}") from exc


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    code_verifier: str,
) -> Dict[str, Any]:
    """Exchange the pasted authorization code for a token document."""
    return _post_form(
        TOKEN_URI,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        },
    )


def refresh_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Dict[str, Any]:
    return _post_form(
        TOKEN_URI,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )


def fetch_userinfo_email(access_token: str) -> Optional[str]:
    """The consenting account's email, for naming the store entry."""
    req = urllib.request.Request(
        USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        email = data.get("email")
        return str(email) if email else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def authorized_user_payload(
    *,
    token_doc: Dict[str, Any],
    client_id: str,
    client_secret: str,
    scopes: List[str],
) -> Dict[str, Any]:
    """Normalize a token-endpoint response to the store's payload schema."""
    granted = token_doc.get("scope")
    return {
        "type": "authorized_user",
        "token": token_doc.get("access_token", ""),
        "refresh_token": token_doc.get("refresh_token", ""),
        "token_uri": TOKEN_URI,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": granted.split() if isinstance(granted, str) and granted
        else list(scopes),
    }


def xoauth2_string(user: str, access_token: str) -> str:
    """The SASL XOAUTH2 initial-response string for IMAP/SMTP."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def load_google_client() -> Tuple[str, str]:
    """The deployment's OAuth client (client_id, client_secret).

    Resolution order: ``$HERMES_HOME/google-workspace/client_secret.json``,
    the skill's legacy ``$HERMES_HOME/google_client_secret.json``, then env
    (``GOOGLE_CLIENT_ID``/``GOOGLE_CLIENT_SECRET``, falling back to the
    calendar poller's ``GCAL_CLIENT_ID``/``GCAL_CLIENT_SECRET``).
    """
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    for path in (
        home / "google-workspace" / "client_secret.json",
        home / "google_client_secret.json",
    ):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        installed = doc.get("installed") or doc.get("web") or doc
        client_id = str(installed.get("client_id") or "").strip()
        client_secret = str(installed.get("client_secret") or "").strip()
        if client_id and client_secret:
            return client_id, client_secret
    for id_env, secret_env in (
        ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
        ("GCAL_CLIENT_ID", "GCAL_CLIENT_SECRET"),
    ):
        client_id = os.getenv(id_env, "").strip()
        client_secret = os.getenv(secret_env, "").strip()
        if client_id and client_secret:
            return client_id, client_secret
    raise GoogleOAuthError(
        "no Google OAuth client configured: place client_secret.json under "
        "$HERMES_HOME/google-workspace/ or set GOOGLE_CLIENT_ID/SECRET"
    )
