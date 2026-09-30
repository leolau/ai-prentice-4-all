"""Postgres E2E for the seminar survey queue and its public endpoints.

Covers what only a real database can get wrong: the table migration, the
request -> invite -> submit -> slides lifecycle keyed by a hashed token,
single-winner claims under ``SKIP LOCKED``, retry/give-up, the resend
cooldown, and the unauthenticated survey endpoints end to end.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.invitations import RedeemThrottle
from hermes_cli.seminar_survey import TABLE, Job, SurveySettings, SurveyStore

CHAT = "239908434235494@lid"
ANSWERS = {
    "rating": 4,
    "grades": ["P3"],
    "comment": "Useful",
    "share": "no",
    "next_step": "updates",
    "name": "Parent",
    "stay_in_touch": False,
}


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-seminar-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
            image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        port = port_result.stdout.strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:hermes-test@127.0.0.1:{port}/hermes_test"
        for _ in range(60):
            try:
                asyncio.run(_probe_postgres(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            capture_output=True,
        )


async def _store(
    dsn: str, settings: SurveySettings | None = None
) -> SurveyStore:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_prod CASCADE")
        await initialize_supabase_app(conn)
    finally:
        await conn.close()
    config = {"datastore": {"supabase_app": {"dsn": dsn}}}
    store = SurveyStore(
        get_store("supabase-app", "prod", config=config),
        settings or SurveySettings(),
    )
    await store.initialize()
    return store


async def _sql(dsn: str, query: str, *args: object) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute(query, *args)
    finally:
        await conn.close()


async def _row(dsn: str) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        row = await conn.fetchrow(f"SELECT * FROM app_prod.{TABLE}")
    finally:
        await conn.close()
    assert row is not None
    return row


def test_request_invite_submit_slides_lifecycle(postgres_dsn: str) -> None:
    async def scenario() -> None:
        store = await _store(postgres_dsn)
        assert await store.signup(CHAT, "Parent") == "queued"
        assert await store.signup(CHAT, "Parent") == "unchanged"

        invite = await store.claim()
        assert invite is not None and invite.kind == "invite"
        assert invite.chat_id == CHAT and invite.token
        assert await store.claim() is None  # leased, not claimable twice

        # The link works as soon as it is minted; the raw token is not stored.
        state = await store.survey_state(invite.token)
        assert state is not None and not state.submitted
        row = await _row(postgres_dsn)
        assert invite.token.encode() not in bytes(row["token_hash"])

        await store.mark_sent(invite, "W-invite")
        assert (await _row(postgres_dsn))["status"] == "invited"
        assert await store.claim() is None

        assert await store.submit("not-a-token", ANSWERS) == "not_found"
        assert await store.submit(invite.token, ANSWERS) == "submitted"
        assert await store.submit(invite.token, {**ANSWERS, "rating": 1}) == (
            "already_submitted"
        )
        row = await _row(postgres_dsn)
        assert row["status"] == "submitted"
        assert '"rating": 4' in row["answers"]

        complete = await store.claim()
        assert complete == Job("complete", invite.signup_id, CHAT)
        assert complete is not None
        await store.mark_sent(complete, "W-slides")
        row = await _row(postgres_dsn)
        assert row["status"] == "completed"
        assert row["completion_message_id"] == "W-slides"
        assert row["completed_at"] is not None
        assert await store.claim() is None
        state = await store.survey_state(invite.token)
        assert state is not None and state.submitted

    asyncio.run(scenario())


def test_concurrent_workers_claim_each_request_once(postgres_dsn: str) -> None:
    async def scenario() -> None:
        store = await _store(postgres_dsn)
        for n in range(5):
            await store.signup(f"{n}@lid")
        jobs = await asyncio.gather(*(store.claim() for _ in range(8)))
        claimed = [j.chat_id for j in jobs if j is not None]
        assert sorted(claimed) == [f"{n}@lid" for n in range(5)]

    asyncio.run(scenario())


def test_failed_send_retries_then_gives_up(postgres_dsn: str) -> None:
    async def scenario() -> None:
        store = await _store(postgres_dsn, SurveySettings(max_attempts=2))
        await store.signup(CHAT)
        first = await store.claim()
        assert first is not None
        await store.mark_failed(first, "bridge down")
        row = await _row(postgres_dsn)
        assert row["status"] == "queued" and row["last_error"] == "bridge down"
        assert await store.claim() is None  # backing off

        await _sql(
            postgres_dsn,
            f"UPDATE app_prod.{TABLE} SET next_attempt_at = NOW()",
        )
        second = await store.claim()
        assert second is not None and second.token != first.token
        assert await store.survey_state(first.token or "") is None
        await store.mark_failed(second, "bridge down")
        assert (await _row(postgres_dsn))["status"] == "failed"

        # Asking again revives a failed request.
        assert await store.signup(CHAT) == "requeued"
        assert await store.claim() is not None

    asyncio.run(scenario())


def test_expired_lease_is_reclaimed(postgres_dsn: str) -> None:
    async def scenario() -> None:
        store = await _store(postgres_dsn)
        await store.signup(CHAT)
        assert await store.claim() is not None
        await _sql(
            postgres_dsn,
            f"UPDATE app_prod.{TABLE} SET lease_until = NOW() - INTERVAL '1 second'",
        )
        again = await store.claim()
        assert again is not None and again.kind == "invite"

    asyncio.run(scenario())


def test_asking_again_after_cooldown_resends(postgres_dsn: str) -> None:
    async def scenario() -> None:
        store = await _store(postgres_dsn)
        await store.signup(CHAT)
        invite = await store.claim()
        assert invite is not None and invite.token
        await store.mark_sent(invite, "W1")
        assert await store.signup(CHAT) == "unchanged"
        await _sql(
            postgres_dsn,
            f"UPDATE app_prod.{TABLE} SET invited_at = NOW() - INTERVAL '1 hour'",
        )
        assert await store.signup(CHAT) == "requeued"
        resend = await store.claim()
        assert resend is not None and resend.kind == "invite"
        await store.mark_sent(resend, "W2")
        assert await store.submit(resend.token or "", ANSWERS) == "submitted"
        done = await store.claim()
        assert done is not None and done.kind == "complete"
        await store.mark_sent(done, "W3")

        assert await store.signup(CHAT) == "unchanged"
        await _sql(
            postgres_dsn,
            f"UPDATE app_prod.{TABLE} SET completed_at = NOW() - INTERVAL '1 hour'",
        )
        assert await store.signup(CHAT) == "requeued"
        slides = await store.claim()
        assert slides is not None and slides.kind == "complete"

    asyncio.run(scenario())


def test_public_survey_endpoints(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = asyncio.run(_store(postgres_dsn))

    async def invite() -> str:
        await store.signup(CHAT)
        job = await store.claim()
        assert job is not None and job.token
        await store.mark_sent(job, "W1")
        return job.token

    token = asyncio.run(invite())
    monkeypatch.setattr(web_server, "_SEMINAR_STORE", store)
    monkeypatch.setattr(
        web_server, "_SEMINAR_THROTTLE", RedeemThrottle(max_attempts=20)
    )
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    client = TestClient(web_server.app)
    state = client.post("/api/seminar/survey/state", json={"token": token})
    assert state.status_code == 200 and state.json() == {"submitted": False}
    assert client.post(
        "/api/seminar/survey/state", json={"token": "nope"}
    ).status_code == 404

    bad = client.post(
        "/api/seminar/survey/submit",
        json={"token": token, "answers": {**ANSWERS, "rating": 9}},
    )
    assert bad.status_code == 400

    ok = client.post(
        "/api/seminar/survey/submit", json={"token": token, "answers": ANSWERS}
    )
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "already_submitted": False}
    again = client.post(
        "/api/seminar/survey/submit", json={"token": token, "answers": ANSWERS}
    )
    assert again.json() == {"ok": True, "already_submitted": True}
    assert client.post(
        "/api/seminar/survey/state", json={"token": token}
    ).json() == {"submitted": True}
    job = asyncio.run(store.claim())
    assert job is not None and job.kind == "complete"
