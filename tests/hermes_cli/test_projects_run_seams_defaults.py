"""Block 5 — the four run seams' DEFAULT resolutions (§16).

``test_projects_run.py`` exercises the run engine with H1–H4 swapped for
fakes, which is right for the engine's logic but leaves a hole: the seam
*defaults* could regress — a stub approval store, cost read from a stored
column, toolsets read from the calling process's config, a spawn that never
enters the host profile's home — and every engine test would stay green.

One contract per seam pins the default to the shipped infrastructure: the
FG-10 ``NotificationStore`` (H1), the C8 ``InteractionLedger`` trace summed
from ``kind='cost'`` events (H2), ``load_config_readonly`` inside the
profile's runtime scope (H3), and ``spawn_seeded_session`` carrying the
host profile's home (H4). These run the REAL seam functions — no seam
monkeypatching anywhere in this file.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import projects_run
from hermes_cli.datastore import SupabaseAppStore


# ---------------------------------------------------------------------------
# H1 — approvals default to the shipped FG-10 NotificationStore
# ---------------------------------------------------------------------------

def test_approval_store_default_is_the_shipped_notification_store():
    from hermes_cli.human_comms import NotificationStore

    store = SupabaseAppStore(mode="prod", schema="hermes", dsn="postgres://u@h/db")
    made = projects_run._approval_store(store, config={})

    # The seam resolves the real class — not a stub — and it exposes the
    # async create the raise path awaits.
    assert isinstance(made, NotificationStore)
    assert inspect.iscoroutinefunction(made.create)
    assert inspect.iscoroutinefunction(made.initialize)


def test_approval_store_default_refuses_a_non_supabase_store():
    # NotificationStore's constructor is the guard: no fail-open, a wrong
    # store class surfaces immediately instead of silently dropping the
    # approval.
    with pytest.raises(TypeError):
        projects_run._approval_store(object(), config={})


# ---------------------------------------------------------------------------
# H2 — cost defaults to the C8 ledger trace, summed from kind='cost' events
# ---------------------------------------------------------------------------

def test_cost_reader_default_walks_the_ledger_trace(monkeypatch):
    from hermes_cli.access import Principal

    store = SupabaseAppStore(mode="prod", schema="hermes", dsn="postgres://u@h/db")
    monkeypatch.setattr("hermes_cli.datastore.get_store", lambda *a, **k: store)

    # The ledger stores no cost column: adapters emit kind='cost' with the
    # amount in the summary. Non-cost events must not count.
    interactions = [
        SimpleNamespace(kind="model_call", summary="model=gpt tokens=500"),
        SimpleNamespace(kind="cost", summary="tokens=500 amount_usd=0.30"),
        SimpleNamespace(kind="cost", summary="amount_usd=0.12 tail"),
    ]
    seen = {}

    async def fake_get_trace(self, trace_id, principal, *, connection=None):
        seen["trace_id"] = trace_id
        seen["principal"] = principal
        return interactions, None

    monkeypatch.setattr(
        "hermes_cli.interactions.InteractionLedger.get_trace", fake_get_trace
    )

    principal = Principal(user_id="leo", display="leo", role="member")
    cost = projects_run._default_cost_reader("trace_9", principal)

    assert cost == pytest.approx(0.42)
    # The real reader path: trace id and the owner principal reach the ledger.
    assert seen["trace_id"] == "trace_9"
    assert seen["principal"] is principal


def test_cost_reader_default_is_none_without_a_configured_dsn(monkeypatch):
    from hermes_cli.access import Principal

    # Fail-open contract: observability unconfigured reads as "not recorded",
    # never an error.
    monkeypatch.setattr(
        "hermes_cli.datastore.get_store",
        lambda *a, **k: SupabaseAppStore(mode="prod", schema="hermes", dsn=""),
    )
    principal = Principal(user_id="leo", display="leo", role="member")
    assert projects_run._default_cost_reader("trace_9", principal) is None


# ---------------------------------------------------------------------------
# H3 — toolsets resolve inside the PROFILE home, never the caller's config
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, toolsets: list[str]) -> None:
    lines = ["toolsets:"] + [f"  - {t}" for t in toolsets]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_toolsets_default_reads_the_profile_home_not_the_caller(
    tmp_path, monkeypatch
):
    # The caller's home (per-test HERMES_HOME) carries a toolset the profile
    # does not grant — it must never leak into the resolution.
    caller_home = Path(os.environ["HERMES_HOME"])
    _write_yaml(caller_home / "config.yaml", ["caller_only"])

    profiles_root = tmp_path / "profiles"
    worker_home = profiles_root / "worker"
    worker_home.mkdir(parents=True)
    _write_yaml(worker_home / "config.yaml", ["web", "file"])
    monkeypatch.setattr(
        "hermes_cli.profiles._get_profiles_root", lambda: profiles_root
    )

    got = projects_run._enabled_toolsets_for_profile("worker")
    assert got == ["web", "file"]
    assert "caller_only" not in got

    # Unknown profile → fail closed: no grant.
    assert projects_run._enabled_toolsets_for_profile("ghost") == []


# ---------------------------------------------------------------------------
# H4 — the default spawn runs inside the host profile's home
# ---------------------------------------------------------------------------

def test_spawn_default_passes_the_host_profile_home(tmp_path, monkeypatch):
    profiles_root = tmp_path / "profiles"
    (profiles_root / "worker").mkdir(parents=True)
    monkeypatch.setattr(
        "hermes_cli.profiles._get_profiles_root", lambda: profiles_root
    )

    captured: dict = {}

    def fake_spawn(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return SimpleNamespace(error=None, timed_out=False)

    monkeypatch.setattr(
        "agent.seeded_session.spawn_seeded_session", fake_spawn
    )

    project = SimpleNamespace(slug="monday-digest", owner_user_id="leo")
    result = projects_run._default_spawn_inline(
        project=project,
        run={"run_no": 3, "profile": "worker"},
        guidance="Ship the digest.",
        inline_steps=[{"key": "draft", "title": "Draft it", "body": None}],
        enabled_toolsets=["web"],
    )

    assert result["error"] is None
    assert result["session_id"].startswith("proj-run-3-")
    # The run executes in the HOST profile's home — its memory, secrets and
    # soul — matching the profile the run row records.
    assert captured["profile_home"] == str(profiles_root / "worker")
    assert captured["origin"] == "projects:monday-digest:run-3"
    assert captured["enabled_toolsets"] == ["web"]
    # Spawned under a copied context so the run cannot corrupt the caller.
    assert captured["context"] is not None


def test_spawn_default_leaves_profile_home_unset_when_the_run_has_none(
    tmp_path, monkeypatch
):
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    monkeypatch.setattr(
        "hermes_cli.profiles._get_profiles_root", lambda: profiles_root
    )

    captured: dict = {}
    monkeypatch.setattr(
        "agent.seeded_session.spawn_seeded_session",
        lambda prompt, **kwargs: captured.update(kwargs)
        or SimpleNamespace(error=None, timed_out=False),
    )

    projects_run._default_spawn_inline(
        project=SimpleNamespace(slug="monday-digest", owner_user_id="leo"),
        run={"run_no": 1, "profile": None},
        guidance="Ship it.",
        inline_steps=[],
    )
    assert captured["profile_home"] is None
