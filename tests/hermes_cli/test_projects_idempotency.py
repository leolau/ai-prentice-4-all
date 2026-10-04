"""Server-side idempotency for project writes (``projects_idempotency``).

The contract the UI's Retry relies on: one key, one execution. A repeat
replays the stored answer, a concurrent repeat never runs the route twice,
a reused key on another request is refused, keys are per principal and
expire, and requests without the header (and streams) are untouched.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from hermes_cli import projects_api, projects_db, projects_idempotency, projects_run
from hermes_cli.access import Principal
from hermes_cli.projects_idempotency import (
    IN_PROGRESS_DETAIL,
    IdempotencyStore,
    ProjectsIdempotencyMiddleware,
)

PREFIX = "/api/registry/projects"


class _Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def harness(tmp_path):
    """A tiny app with counting routes under the projects prefix, in two
    routers (the second stands in for another workstream's module)."""
    calls: dict[str, int] = {}
    gate = threading.Event()
    gate.set()
    actor = {"id": "leo"}
    clock = _Clock()

    def _count(name: str) -> int:
        calls[name] = calls.get(name, 0) + 1
        return calls[name]

    main = APIRouter(prefix=PREFIX)

    @main.post("/{slug}/act")
    async def act(slug: str, request: Request):
        body = await request.body()
        n = _count("act")
        return {"slug": slug, "n": n, "echo": body.decode()}

    @main.post("/{slug}/slow")
    def slow(slug: str):
        n = _count("slow")
        gate.wait(5)
        return {"n": n}

    @main.post("/{slug}/refuse")
    async def refuse(slug: str):
        n = _count("refuse")
        return JSONResponse({"detail": f"refused {n}"}, status_code=409)

    @main.patch("/{slug}")
    async def patch(slug: str):
        return {"n": _count("patch")}

    @main.delete("/{slug}")
    async def delete(slug: str):
        return {"n": _count("delete")}

    @main.post("/{slug}/stream")
    async def stream_post(slug: str):
        _count("stream")

        async def gen():
            for i in range(3):
                yield f"data: {i}\n\n".encode()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @main.get("/{slug}/events/stream")
    async def stream_get(slug: str):
        async def gen():
            yield b"data: hello\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    other = APIRouter(prefix=PREFIX)

    @other.post("/{slug}/changes")
    async def changes(slug: str):
        return {"n": _count("changes")}

    outside = APIRouter(prefix="/api/todos")

    @outside.post("")
    async def todo():
        return {"n": _count("todo")}

    async def _principal(request):
        return actor["id"]

    store = IdempotencyStore(tmp_path / "idem.db", clock=clock)
    app = FastAPI()
    app.include_router(main)
    app.include_router(other)
    app.include_router(outside)
    app.add_middleware(
        ProjectsIdempotencyMiddleware,
        store=store,
        wait_seconds=0.3,
        poll_seconds=0.02,
        principal_resolver=_principal,
    )
    return {
        "client": TestClient(app),
        "app": app,
        "calls": calls,
        "gate": gate,
        "actor": actor,
        "clock": clock,
        "store": store,
    }


def _key(k: str) -> dict:
    return {"Idempotency-Key": k}


def test_replay_returns_the_stored_answer_and_does_not_execute_again(harness):
    client = harness["client"]
    first = client.post(f"{PREFIX}/p/act", json={"a": 1}, headers=_key("k1"))
    second = client.post(f"{PREFIX}/p/act", json={"a": 1}, headers=_key("k1"))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"slug": "p", "n": 1, "echo": '{"a":1}'}
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert second.headers["content-type"].startswith("application/json")
    assert harness["calls"]["act"] == 1


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_every_mutating_method_is_covered(harness, method):
    client = harness["client"]
    a = getattr(client, method)(f"{PREFIX}/p", headers=_key("m"))
    b = getattr(client, method)(f"{PREFIX}/p", headers=_key("m"))
    assert a.json() == b.json() == {"n": 1}
    assert b.headers["idempotent-replayed"] == "true"


def test_routes_in_other_router_modules_are_covered(harness):
    client = harness["client"]
    client.post(f"{PREFIX}/p/changes", headers=_key("c"))
    again = client.post(f"{PREFIX}/p/changes", headers=_key("c"))
    assert again.headers["idempotent-replayed"] == "true"
    assert harness["calls"]["changes"] == 1


def test_paths_outside_the_projects_prefix_are_untouched(harness):
    client = harness["client"]
    client.post("/api/todos", headers=_key("t"))
    again = client.post("/api/todos", headers=_key("t"))
    assert "idempotent-replayed" not in again.headers
    assert harness["calls"]["todo"] == 2


def test_without_the_header_every_request_executes(harness):
    client = harness["client"]
    a = client.post(f"{PREFIX}/p/act", json={})
    b = client.post(f"{PREFIX}/p/act", json={})
    assert (a.json()["n"], b.json()["n"]) == (1, 2)
    assert "idempotent-replayed" not in b.headers


def test_concurrent_same_key_requests_execute_once(harness):
    client, gate, calls = harness["client"], harness["gate"], harness["calls"]
    gate.clear()
    results: list = []

    def fire():
        results.append(client.post(f"{PREFIX}/p/slow", headers=_key("race")))

    threads = [threading.Thread(target=fire) for _ in range(4)]
    for t in threads:
        t.start()
    # The three repeats wait 0.3 s for the original, then answer 409.
    time.sleep(0.8)
    gate.set()
    for t in threads:
        t.join(10)
    assert calls["slow"] == 1
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409, 409, 409]
    for r in results:
        if r.status_code == 409:
            assert r.json() == {"detail": IN_PROGRESS_DETAIL}
    # Once it has landed, a retry replays the original.
    again = client.post(f"{PREFIX}/p/slow", headers=_key("race"))
    assert again.status_code == 200 and again.json() == {"n": 1}
    assert again.headers["idempotent-replayed"] == "true"


def test_a_repeat_waits_for_a_quick_original_and_replays_it(harness):
    client, gate, calls = harness["client"], harness["gate"], harness["calls"]
    gate.clear()
    out: dict = {}
    t = threading.Thread(
        target=lambda: out.setdefault("a", client.post(f"{PREFIX}/p/slow", headers=_key("w")))
    )
    t.start()
    time.sleep(0.1)
    threading.Timer(0.05, gate.set).start()
    b = client.post(f"{PREFIX}/p/slow", headers=_key("w"))
    t.join(10)
    assert calls["slow"] == 1
    assert out["a"].status_code == 200 and b.status_code == 200
    assert b.json() == out["a"].json()
    assert b.headers["idempotent-replayed"] == "true"


@pytest.mark.parametrize(
    "variant",
    [
        lambda c: c.post(f"{PREFIX}/p/act", json={"a": 2}, headers=_key("mm")),
        lambda c: c.post(f"{PREFIX}/q/act", json={"a": 1}, headers=_key("mm")),
        lambda c: c.post(f"{PREFIX}/p/act?x=1", json={"a": 1}, headers=_key("mm")),
        lambda c: c.patch(f"{PREFIX}/p", headers=_key("mm")),
    ],
    ids=["body", "path", "query", "method"],
)
def test_same_key_for_a_different_request_is_422(harness, variant):
    client = harness["client"]
    client.post(f"{PREFIX}/p/act", json={"a": 1}, headers=_key("mm"))
    resp = variant(client)
    assert resp.status_code == 422
    assert "different request" in resp.json()["detail"]
    assert harness["calls"]["act"] == 1
    assert "patch" not in harness["calls"]


def test_keys_are_scoped_per_principal(harness):
    client, actor = harness["client"], harness["actor"]
    a = client.post(f"{PREFIX}/p/act", json={}, headers=_key("shared"))
    actor["id"] = "ada"
    b = client.post(f"{PREFIX}/p/act", json={}, headers=_key("shared"))
    assert (a.json()["n"], b.json()["n"]) == (1, 2)
    assert "idempotent-replayed" not in b.headers
    # A different body under the other principal is not a mismatch either.
    actor["id"] = "eve"
    c = client.post(f"{PREFIX}/p/act", json={"other": True}, headers=_key("shared"))
    assert c.status_code == 200


def test_keys_expire_after_24_hours(harness):
    client, clock = harness["client"], harness["clock"]
    client.post(f"{PREFIX}/p/act", json={}, headers=_key("old"))
    clock.now += 24 * 60 * 60 - 5
    still = client.post(f"{PREFIX}/p/act", json={}, headers=_key("old"))
    assert still.headers["idempotent-replayed"] == "true"
    clock.now += 10
    fresh = client.post(f"{PREFIX}/p/act", json={}, headers=_key("old"))
    assert "idempotent-replayed" not in fresh.headers
    assert harness["calls"]["act"] == 2


def test_expired_rows_are_purged_lazily(harness):
    client, clock, store = harness["client"], harness["clock"], harness["store"]
    client.post(f"{PREFIX}/p/act", json={}, headers=_key("a"))
    clock.now += 25 * 60 * 60
    client.post(f"{PREFIX}/p/act", json={}, headers=_key("b"))
    with store._connect() as conn:
        keys = {r["key"] for r in conn.execute("SELECT key FROM idempotency_keys")}
    assert keys == {"b"}


def test_a_refusal_releases_the_key_so_retry_runs_again(harness):
    client = harness["client"]
    a = client.post(f"{PREFIX}/p/refuse", headers=_key("r"))
    b = client.post(f"{PREFIX}/p/refuse", headers=_key("r"))
    assert a.json() == {"detail": "refused 1"}
    assert b.json() == {"detail": "refused 2"}
    assert "idempotent-replayed" not in b.headers


def test_a_crashing_route_releases_the_key(harness, tmp_path):
    router = APIRouter(prefix=PREFIX)
    calls = {"n": 0}

    @router.post("/{slug}/boom")
    async def boom(slug: str):
        calls["n"] += 1
        raise RuntimeError("boom")

    async def _p(request):
        return "leo"

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(
        ProjectsIdempotencyMiddleware,
        store=IdempotencyStore(tmp_path / "boom.db"),
        principal_resolver=_p,
    )
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(f"{PREFIX}/p/boom", headers=_key("x")).status_code == 500
    assert client.post(f"{PREFIX}/p/boom", headers=_key("x")).status_code == 500
    assert calls["n"] == 2


def test_an_abandoned_claim_is_taken_over_after_the_lease(harness):
    store, clock = harness["store"], harness["clock"]
    assert store.claim("leo", "z", "POST", "/x", "h").outcome == "owner"
    assert store.claim("leo", "z", "POST", "/x", "h").outcome == "in_flight"
    clock.now += store.lease_seconds + 1
    assert store.claim("leo", "z", "POST", "/x", "h").outcome == "owner"


def test_streaming_responses_pass_through_and_are_not_recorded(harness):
    client = harness["client"]
    with client.stream("POST", f"{PREFIX}/p/stream", headers=_key("s")) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        chunks = b"".join(resp.iter_bytes())
    assert chunks == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
    with client.stream("POST", f"{PREFIX}/p/stream", headers=_key("s")) as resp:
        assert "idempotent-replayed" not in resp.headers
        assert b"".join(resp.iter_bytes()) == chunks
    assert harness["calls"]["stream"] == 2
    # Reads are never touched, header or not.
    with client.stream("GET", f"{PREFIX}/p/events/stream", headers=_key("g")) as resp:
        assert b"".join(resp.iter_bytes()) == b"data: hello\n\n"
        assert "idempotent-replayed" not in resp.headers


def test_an_overlong_key_is_refused(harness):
    resp = harness["client"].post(f"{PREFIX}/p/act", headers=_key("k" * 500))
    assert resp.status_code == 422
    assert "act" not in harness["calls"]


def test_an_unresolvable_principal_passes_through(tmp_path):
    router = APIRouter(prefix=PREFIX)

    @router.post("/{slug}/act")
    async def act(slug: str):
        return {"ok": True}

    async def _fail(request):
        raise RuntimeError("no principal")

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(
        ProjectsIdempotencyMiddleware,
        store=IdempotencyStore(tmp_path / "x.db"),
        principal_resolver=_fail,
    )
    resp = TestClient(app).post(f"{PREFIX}/p/act", headers=_key("k"))
    assert resp.status_code == 200 and resp.json() == {"ok": True}


def test_the_dashboard_app_mounts_the_middleware():
    from hermes_cli import web_server

    # By name: other suites reload modules, which replaces class identity.
    assert any(
        getattr(m.cls, "__name__", "") == "ProjectsIdempotencyMiddleware"
        for m in web_server.app.user_middleware
    )


def test_store_lives_beside_the_projects_db_unless_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "root" / "projects.db"))
    monkeypatch.delenv("HERMES_IDEMPOTENCY_DB", raising=False)
    assert projects_idempotency.idempotency_db_path() == (
        tmp_path / "root" / "projects_idempotency.db"
    )
    monkeypatch.setenv("HERMES_IDEMPOTENCY_DB", str(tmp_path / "elsewhere.db"))
    assert projects_idempotency.idempotency_db_path() == tmp_path / "elsewhere.db"


# ---------------------------------------------------------------------------
# The real routes: two POST /runs with one key → one run
# ---------------------------------------------------------------------------

OWNER = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]
ADA = Principal(user_id="ada", display="Ada", role="member")  # type: ignore[arg-type]

STEPS = [
    {"key": "gather", "title": "Collect arrivals"},
    {"key": "send", "title": "Send to the list", "depends_on": ["gather"]},
]


class _FakeApprovalStore:
    async def initialize(self):
        pass

    async def create(self, **kwargs):
        return object()


@pytest.fixture
def real(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.delenv("HERMES_IDEMPOTENCY_DB", raising=False)
    monkeypatch.setattr("hermes_cli.datastore.get_store", lambda *a, **k: object())
    monkeypatch.setattr(
        projects_run, "_approval_store", lambda app_store, *, config: _FakeApprovalStore()
    )
    monkeypatch.setattr(
        projects_run, "_enabled_toolsets_for_profile", lambda profile: ["research"]
    )
    monkeypatch.setattr(projects_run, "_available_skill_names", lambda profile: [])
    state = {"actor": OWNER}

    async def _resolve(request, *, allow_as=True):
        return state["actor"]

    async def _enrolled(user_id):
        return set()

    async def _subject(request):
        return "leo"

    monkeypatch.setattr(
        "hermes_cli.web_server._comms_resolve_principal", _resolve, raising=False
    )
    monkeypatch.setattr(projects_api, "_enrolled_profiles", _enrolled)
    monkeypatch.setattr(projects_api, "_interactive_subject", _subject)

    app = FastAPI()
    app.include_router(projects_api.router)
    # The default store and principal resolver — exactly as web_server mounts it.
    app.add_middleware(ProjectsIdempotencyMiddleware)
    client = TestClient(app)

    resp = client.post(
        PREFIX,
        json={
            "goal": "Ship the Monday digest",
            "description": "A weekly digest.",
            "host_profile": "default",
            "outputs": [{"title": "The digest"}],
        },
    )
    assert resp.status_code == 200, resp.text
    project = resp.json()
    with projects_db.connect_closing() as conn:
        projects_db.add_project_member(
            conn, project_id=project["id"], user_id="ada", role="member"
        )
        projects_db.set_project_status(conn, project["id"], "active")
    slug = project["slug"]
    resp = client.post(f"{PREFIX}/{slug}/playbook", json={"body": "m", "steps": STEPS})
    assert resp.status_code == 200, resp.text
    rev = resp.json()["rev"]
    resp = client.post(f"{PREFIX}/{slug}/playbook/{rev}/activate", json={})
    assert resp.status_code == 200, resp.text
    return client, slug, state


def test_two_run_now_posts_with_one_key_create_one_run(real):
    client, slug, _state = real
    a = client.post(f"{PREFIX}/{slug}/runs", json={}, headers=_key("run-1"))
    b = client.post(f"{PREFIX}/{slug}/runs", json={}, headers=_key("run-1"))
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert b.headers["idempotent-replayed"] == "true"
    assert a.json() == b.json()
    runs = client.get(f"{PREFIX}/{slug}/runs").json()["runs"]
    assert [r["run_no"] for r in runs] == [1]


def test_without_a_key_the_second_run_now_is_still_refused_by_the_route(real):
    client, slug, _state = real
    assert client.post(f"{PREFIX}/{slug}/runs", json={}).status_code == 200
    # Today's behaviour, unchanged: the one-open-run gate answers 409.
    assert client.post(f"{PREFIX}/{slug}/runs", json={}).status_code == 409


def test_a_refused_write_is_not_replayed_for_another_principal(real):
    client, slug, state = real
    a = client.post(f"{PREFIX}/{slug}/runs", json={}, headers=_key("same"))
    assert a.status_code == 200
    state["actor"] = ADA
    b = client.post(f"{PREFIX}/{slug}/runs", json={}, headers=_key("same"))
    # Ada's key is her own: the route runs and refuses (run already open).
    assert b.status_code == 409
    assert "idempotent-replayed" not in b.headers
