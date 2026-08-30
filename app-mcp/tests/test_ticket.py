import base64
import hashlib
import hmac
import time

from app_mcp.ticket import verify_ticket

SECRET = "shared-secret"


def make_ticket(user: str, expires_ms: int, secret: str = SECRET) -> str:
    payload = f"{user}.{expires_ms}"
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    return f"{payload}.{sig.decode()}"


def test_accepts_a_fresh_valid_ticket():
    now = time.time() * 1000
    ticket = make_ticket("leo_owner", int(now + 60_000))
    assert verify_ticket(ticket, SECRET, now_ms=now) == "leo_owner"


def test_rejects_an_expired_ticket():
    now = time.time() * 1000
    ticket = make_ticket("leo_owner", int(now - 1))
    assert verify_ticket(ticket, SECRET, now_ms=now) is None


def test_rejects_a_forged_signature():
    now = time.time() * 1000
    ticket = make_ticket("leo_owner", int(now + 60_000), secret="other-secret")
    assert verify_ticket(ticket, SECRET, now_ms=now) is None


def test_rejects_tampered_payload():
    now = time.time() * 1000
    ticket = make_ticket("mallory", int(now + 60_000)).rsplit(".", 1)[0]
    # Swap the user but keep the signature minted for someone else.
    good = make_ticket("leo_owner", int(now + 60_000)).split(".")
    assert verify_ticket(f"mallory.{good[1]}.{good[2]}", SECRET, now_ms=now) is None
    assert ticket.startswith("mallory.")


def test_rejects_malformed_tickets():
    now = time.time() * 1000
    assert verify_ticket("", SECRET, now_ms=now) is None
    assert verify_ticket("no-dots", SECRET, now_ms=now) is None
    assert verify_ticket("a.b", SECRET, now_ms=now) is None
    assert verify_ticket("a.notanumber.sig", SECRET, now_ms=now) is None
    assert verify_ticket("a.b.c.d", SECRET, now_ms=now) is None


def test_rejects_when_secret_missing():
    now = time.time() * 1000
    ticket = make_ticket("leo_owner", int(now + 60_000))
    assert verify_ticket(ticket, "", now_ms=now) is None
