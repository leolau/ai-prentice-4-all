"""Unit coverage for the unified credential store (file backend) and the
stdlib Google OAuth helpers. No network, no database."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from pathlib import Path

import pytest

from hermes_cli.access import Principal
from hermes_cli.credential_store import (
    CREDENTIAL_KINDS,
    CredentialError,
    FileCredentialStore,
    redact_payload,
    validate_payload,
    validate_services,
)
from hermes_cli.google_oauth import (
    GoogleOAuthError,
    authorized_user_payload,
    generate_pkce,
    parse_code_or_url,
    scopes_for_services,
    xoauth2_string,
)

GOOGLE_PAYLOAD = {
    "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "shh",
    "refresh_token": "1//abc",
    "token_uri": "https://oauth2.googleapis.com/token",
    "scopes": ["https://mail.google.com/"],
}

OWNER = Principal(user_id="owner-1", display="Owner", role="owner")
ALICE = Principal(user_id="alice", display="Alice", role="member")
BOB = Principal(user_id="bob", display="Bob", role="member")


@pytest.fixture
def store(tmp_path: Path) -> FileCredentialStore:
    return FileCredentialStore(root=tmp_path / "credentials")


def run(coro):
    import asyncio

    return asyncio.run(coro)


# -- kind registry ---------------------------------------------------------


def test_unknown_kind_rejected():
    with pytest.raises(CredentialError):
        validate_payload("nope", {})


def test_missing_required_rejected():
    with pytest.raises(CredentialError):
        validate_payload("google-oauth2", {"client_id": "x"})


def test_unknown_field_rejected():
    with pytest.raises(CredentialError):
        validate_payload("google-oauth2", {**GOOGLE_PAYLOAD, "bogus": 1})


def test_authorized_user_type_accepted():
    # Regression: google/complete stores authorized_user payloads, which
    # carry "type"; the kind spec must accept it.
    validate_payload("google-oauth2", {**GOOGLE_PAYLOAD, "type": "authorized_user"})


def test_reserved_kinds_validate():
    validate_payload("telegram-token", {"bot_token": "123:ABC"})
    validate_payload("password", {"username": "u", "password": "p"})
    validate_payload("whatsapp-session", {"session": "{}"})


def test_redaction_strips_secret_fields():
    for kind, spec in CREDENTIAL_KINDS.items():
        payload = {k: "v" for k in spec.required}
        redacted = redact_payload(kind, payload)
        assert not set(redacted) & set(spec.secret_fields)
        assert set(redacted) == set(spec.required) - set(spec.secret_fields)


def test_validate_services():
    assert validate_services(["email", "calendar"]) == ["calendar", "email"]
    assert validate_services(None) == []
    with pytest.raises(CredentialError):
        validate_services(["coffee"])


# -- file backend ----------------------------------------------------------


def test_put_get_roundtrip_and_perms(store, tmp_path):
    entry = run(
        store.put(
            ALICE,
            provider="google",
            name="alice@gmail.com",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
            services=["email"],
        )
    )
    assert entry.owner_user_id == "alice"
    assert entry.visibility == "private:alice"
    got = run(store.get(ALICE, "google", "alice@gmail.com"))
    assert got is not None and got.payload["refresh_token"] == "1//abc"
    on_disk = next((tmp_path / "credentials").rglob("*.json"))
    assert stat.S_IMODE(on_disk.stat().st_mode) == 0o600
    assert stat.S_IMODE(on_disk.parent.stat().st_mode) == 0o700


def test_visibility_member_cannot_read_other_private(store):
    run(
        store.put(
            ALICE,
            provider="google",
            name="a@x.co",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
        )
    )
    assert run(store.get(BOB, "google", "a@x.co")) is None
    assert run(store.get(OWNER, "google", "a@x.co")) is not None
    bob_rows = run(store.list(BOB))
    assert bob_rows == []
    owner_rows = run(store.list(OWNER))
    assert len(owner_rows) == 1


def test_shared_visible_to_members(store):
    run(
        store.put(
            ALICE,
            provider="google",
            name="shared@x.co",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
            visibility="shared",
        )
    )
    assert run(store.get(BOB, "google", "shared@x.co")) is not None


def test_private_for_another_user_rejected(store):
    with pytest.raises(CredentialError):
        run(
            store.put(
                ALICE,
                provider="google",
                name="x@x.co",
                kind="google-oauth2",
                payload=GOOGLE_PAYLOAD,
                visibility="private:bob",
            )
        )


def test_patch_toggles(store):
    run(
        store.put(
            ALICE,
            provider="google",
            name="a@x.co",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
        )
    )
    patched = run(
        store.patch(ALICE, "google", "a@x.co", services=["email", "calendar"])
    )
    assert patched.services == ["calendar", "email"]
    with pytest.raises(CredentialError):
        run(store.patch(ALICE, "google", "a@x.co", services=["nope"]))


def test_delete(store):
    run(
        store.put(
            ALICE,
            provider="google",
            name="a@x.co",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
        )
    )
    assert run(store.delete(ALICE, "google", "a@x.co")) is True
    assert run(store.delete(ALICE, "google", "a@x.co")) is False


def test_update_tokens_conditional(store):
    run(
        store.put(
            ALICE,
            provider="google",
            name="a@x.co",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
        )
    )
    won = run(
        store.update_tokens(
            "google",
            "a@x.co",
            owner_user_id="alice",
            old_refresh_token="WRONG",
            payload_fragment={"token": "new"},
        )
    )
    assert won is False
    won = run(
        store.update_tokens(
            "google",
            "a@x.co",
            owner_user_id="alice",
            old_refresh_token="1//abc",
            payload_fragment={"token": "new", "refresh_token": "1//def"},
        )
    )
    assert won is True
    entry = run(store.get(ALICE, "google", "a@x.co"))
    assert entry.payload["refresh_token"] == "1//def"
    assert entry.payload["token"] == "new"


def test_resolve_for_service(store):
    run(
        store.put(
            ALICE,
            provider="google",
            name="mail@x.co",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
            services=["email"],
        )
    )
    run(
        store.put(
            BOB,
            provider="google",
            name="cal@x.co",
            kind="google-oauth2",
            payload=GOOGLE_PAYLOAD,
            services=["calendar"],
        )
    )
    email_entries = run(store.resolve_for_service("email"))
    assert [e.name for e in email_entries] == ["mail@x.co"]
    cal_entries = run(store.resolve_for_service("calendar"))
    assert [e.name for e in cal_entries] == ["cal@x.co"]


def test_invalid_name_rejected(store):
    with pytest.raises(CredentialError):
        run(
            store.put(
                ALICE,
                provider="google",
                name="../escape",
                kind="google-oauth2",
                payload=GOOGLE_PAYLOAD,
            )
        )


# -- google oauth helpers --------------------------------------------------


def test_parse_code_or_url_bare():
    assert parse_code_or_url("4/0ABC") == ("4/0ABC", None)


def test_parse_code_or_url_redirect():
    code, state = parse_code_or_url(
        "http://localhost:1/?code=4/0ABC&state=st-1&scope=x"
    )
    assert (code, state) == ("4/0ABC", "st-1")


def test_parse_code_or_url_missing_code():
    with pytest.raises(GoogleOAuthError):
        parse_code_or_url("http://localhost:1/?error=denied")


def test_scopes_union_includes_imap_scope():
    scopes = scopes_for_services(["email", "calendar"])
    assert "https://mail.google.com/" in scopes
    assert "https://www.googleapis.com/auth/calendar" in scopes
    assert scopes_for_services(["workspace"]) != scopes


def test_pkce_challenge_is_s256_of_verifier():
    pkce = generate_pkce()
    expect = (
        base64.urlsafe_b64encode(
            hashlib.sha256(pkce["code_verifier"].encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert pkce["code_challenge"] == expect


def test_xoauth2_string_format():
    assert xoauth2_string("u@x.co", "tok") == "user=u@x.co\x01auth=Bearer tok\x01\x01"


def test_authorized_user_payload_prefers_granted_scopes():
    doc = authorized_user_payload(
        token_doc={"access_token": "at", "refresh_token": "rt", "scope": "a b"},
        client_id="cid",
        client_secret="sec",
        scopes=["requested"],
    )
    assert doc["scopes"] == ["a", "b"]
    assert doc["type"] == "authorized_user"
    assert doc["refresh_token"] == "rt"
