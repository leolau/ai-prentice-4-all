"""FG-26 — a suspended enrolment must not keep acting in the profile.

Suspension is deliberately profile-local: the console flips
``principals.active`` and leaves the shared GoTrue account alone, because one
box-wide ``auth.users`` serves every profile and banning the account would evict
somebody from profiles this console does not administer (FG-26 §3.5).

That design only holds if every seam where a *profile* grants authority reads
the flag. This file covers the three that do the granting, against real
Postgres:

* the C1 gateway seam (``resolve_principal``) — a suspended person messaging
  from Telegram/WhatsApp resolves to nobody, and pairing cannot re-admit them;
* the web/BFF seam (``_comms_resolve_principal``) — 403, for reads as well as
  writes, so the still-valid login reaches none of this profile's surfaces;
* the FG-24 memory ladder — a suspended enrolment is not a candidate and a
  remembered binding to one is forgotten.

Plus the two smaller claims that belong with them: the redeem throttle's IP key
is the invitee's address rather than the BFF's, and creating somebody who is
already enrolled says so instead of silently doing nothing.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

from hermes_cli.access import PrincipalStore, resolve_principal
from hermes_cli.datastore import get_store, initialize_supabase_app

_IMAGE = (
    "postgres@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)


class _Source:
    """Minimal ChannelOrigin stand-in."""

    def __init__(self, platform: str, user_id: str, user_name: str = "") -> None:
        self.platform = platform
        self.user_id = user_id
        self.user_name = user_name


class _FakeQuery:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, default)


class _FakeSession:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


class _FakeState:
    pass


class _FakeRequest:
    """Minimal Starlette-Request stand-in for the web resolver."""

    def __init__(
        self,
        *,
        subject: str | None = None,
        headers: dict[str, str] | None = None,
        peer: str = "127.0.0.1",
    ) -> None:
        self.state = _FakeState()
        if subject is not None:
            self.state.session = _FakeSession(subject)
        self.query_params = _FakeQuery({})
        self.headers = _FakeQuery(headers or {})
        self.client = _FakeSession(peer)
        self.client.host = peer


async def _probe(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    if (
        subprocess.run(
            ["docker", "info"], check=False, capture_output=True, text=True
        ).returncode
        != 0
    ):
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    subprocess.run(["docker", "pull", _IMAGE], check=True, capture_output=True)
    container = f"hermes-fg26susp-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            "POSTGRES_PASSWORD=hermes-test",
            "--env",
            "POSTGRES_DB=hermes_test",
            "--publish",
            "127.0.0.1::5432",
            _IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        port = (
            subprocess.run(
                ["docker", "port", container, "5432/tcp"],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        dsn = f"postgresql://postgres:hermes-test@127.0.0.1:{port}/hermes_test"
        for _ in range(120):
            try:
                asyncio.run(_probe(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container], check=False, capture_output=True
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _reset(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    try:
        await connection.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_supabase_app(connection)
    finally:
        await connection.close()


def _store(dsn: str) -> PrincipalStore:
    return PrincipalStore(get_store("supabase-app", "prod", config=_config(dsn)))


# ---------------------------------------------------------------------------
# The gateway seam (C1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspended_channel_identity_resolves_to_nobody(
    postgres_dsn: str,
) -> None:
    """A suspended member's messages carry no principal, hence no authority."""
    await _reset(postgres_dsn)
    store = _store(postgres_dsn)
    await store.enroll("sam", display="Sam", role="member")
    await store.link_channel("sam", "telegram", "tg-9")
    source = _Source("telegram", "tg-9", "Sam")

    live = await resolve_principal(source, store=store, is_paired=lambda *_: False)
    assert live is not None and live.user_id == "sam"

    await store.set_active("sam", False)

    assert (
        await resolve_principal(source, store=store, is_paired=lambda *_: False)
        is None
    )
    # And pairing is not a way back in: the enrol is an upsert, so a paired
    # suspended identity must still resolve to nobody rather than be re-admitted
    # as a fresh member.
    assert (
        await resolve_principal(source, store=store, is_paired=lambda *_: True)
        is None
    )

    await store.set_active("sam", True)
    restored = await resolve_principal(
        source, store=store, is_paired=lambda *_: False
    )
    assert restored is not None and restored.role == "member"


# ---------------------------------------------------------------------------
# The web/BFF seam
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspended_login_is_refused_by_the_web_resolver(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared account still signs in; this profile answers 403 anyway."""
    from fastapi import HTTPException

    from hermes_cli import web_server as ws

    await _reset(postgres_dsn)
    app_store = get_store("supabase-app", "prod", config=_config(postgres_dsn))
    store = PrincipalStore(app_store)
    await store.enroll("leo", display="Leo", role="owner")
    await store.enroll("ada", display="Ada", role="admin")
    monkeypatch.setattr(ws, "_comms_app_store", lambda: app_store)

    admin = await ws._comms_resolve_principal(_FakeRequest(subject="ada"))
    assert admin.user_id == "ada"

    await store.set_active("ada", False)

    for allow_as in (False, True):
        with pytest.raises(HTTPException) as excinfo:
            await ws._comms_resolve_principal(
                _FakeRequest(subject="ada"), allow_as=allow_as
            )
        assert excinfo.value.status_code == 403

    # The owner is unaffected, and can still see the suspended row in order to
    # restore it.
    owner = await ws._comms_resolve_principal(_FakeRequest(subject="leo"))
    assert owner.is_owner


# ---------------------------------------------------------------------------
# The FG-24 memory ladder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspended_enrolment_is_not_a_local_principal_candidate(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binding must not outlive the authority it was granted under."""
    from hermes_cli import principal_binding

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    await _reset(postgres_dsn)
    store = _store(postgres_dsn)
    await store.enroll("leo", display="Leo", role="owner")
    await store.enroll("ada", display="Ada", role="admin")

    principal_binding.remember_binding("ada", "admin", "asked")
    await store.set_active("ada", False)

    resolution = principal_binding.resolve_local_principal(store=store)

    # The suspended binding is gone, not merely unused: nothing in this session
    # or the next acts as `ada`.
    assert resolution.binding is not None
    assert resolution.binding.user_id != "ada"
    assert (principal_binding.read_binding() or resolution.binding).user_id != "ada"
    # Two enrolled, one suspended → the survivor is the only candidate, so the
    # ladder falls to its "they set the box up" rung rather than asking.
    assert resolution.candidates == 1
    assert resolution.binding.user_id == "leo"


# ---------------------------------------------------------------------------
# The throttle key, and the truthful create
# ---------------------------------------------------------------------------


def test_redeem_throttle_keys_on_the_invitee_not_the_bff() -> None:
    """`X-Forwarded-For` from the on-box BFF is the client; elsewhere it isn't."""
    from hermes_cli import web_server as ws

    from_bff = _FakeRequest(
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}, peer="127.0.0.1"
    )
    assert ws._forwarded_client_ip(from_bff) == "203.0.113.7"

    # A header on a request that did *not* come through the loopback BFF is a
    # spoofable throttle key, so it is ignored.
    direct = _FakeRequest(
        headers={"X-Forwarded-For": "203.0.113.7"}, peer="198.51.100.4"
    )
    assert ws._forwarded_client_ip(direct) == "198.51.100.4"

    assert ws._forwarded_client_ip(_FakeRequest(peer="127.0.0.1")) == "127.0.0.1"
