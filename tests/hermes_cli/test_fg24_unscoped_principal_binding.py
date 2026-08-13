"""FG-24 — a session with no channel identity must not write everyone's block.

The hole these lock down, confirmed live on ``hermes-systest`` before the fix:
an unscoped ``MemoryStore``'s ``target='memory'`` file is *the same file* every
resolved principal renders as the profile-wide **shared** block, so the CLI, a
cron job or the digest silently wrote into everybody's prompt — while an
enrolled member asking for that write was refused and audited.

The owner's decision (2026-08-12) was to resolve the principal rather than fall
back: login subject, else the setup/pairing binding, else ask once and remember.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.access import Principal
from hermes_cli.principal_binding import (
    LocalBinding,
    binding_path,
    forget_binding,
    read_binding,
    remember_binding,
    resolve_local_principal,
)
from tools.memory_tool import MEMORY_AUDIT_LOG, MemoryStore


class FakeStore:
    """A principals directory without a database."""

    def __init__(self, principals: list[Principal], aliases: dict[str, str] | None = None):
        self.principals = principals
        self.aliases = aliases or {}
        self.list_calls = 0

    async def list_principals(
        self, *, active: bool | None = None
    ) -> list[Principal]:
        self.list_calls += 1
        return [
            p for p in self.principals if active is None or p.active == active
        ]

    async def resolve_alias(self, subject: str) -> str | None:
        return self.aliases.get(subject)


OWNER = Principal(user_id="leo-owner", display="Leo", role="owner")
MEMBER = Principal(user_id="sam-member", display="Sam", role="member")
ADMIN = Principal(user_id="ada-admin", display="Ada", role="admin")


@pytest.fixture(autouse=True)
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# The resolution ladder
# ---------------------------------------------------------------------------


def test_sole_enrolled_principal_is_the_setup_user() -> None:
    """One principal means no ambiguity: that person set the box up."""
    store = FakeStore([OWNER])

    resolution = resolve_local_principal(store=store)

    assert resolution.binding == LocalBinding("leo-owner", "owner", "setup")
    assert resolution.ambiguous is False
    # And it is remembered, so the second session costs no lookup decision.
    assert read_binding() == LocalBinding("leo-owner", "owner", "setup")


def test_login_subject_wins_over_asking() -> None:
    store = FakeStore([OWNER, MEMBER], aliases={"gotrue-sub-1": "sam-member"})

    def never_ask(_candidates: list[Principal]) -> Principal | None:
        raise AssertionError("must not ask when the login subject resolves")

    resolution = resolve_local_principal(
        login_subject="gotrue-sub-1", ask=never_ask, store=store
    )

    assert resolution.binding == LocalBinding("sam-member", "member", "login")


def test_login_subject_without_alias_is_the_principal_id() -> None:
    """Anyone enrolled after the auth provider existed *is* their subject."""
    store = FakeStore([OWNER, MEMBER])

    resolution = resolve_local_principal(login_subject="sam-member", store=store)

    assert resolution.binding == LocalBinding("sam-member", "member", "login")


def test_several_principals_and_no_binding_asks_once_and_remembers() -> None:
    store = FakeStore([OWNER, MEMBER, ADMIN])
    asked: list[list[str]] = []

    def ask(candidates: list[Principal]) -> Principal:
        asked.append([p.user_id for p in candidates])
        return candidates[1]

    first = resolve_local_principal(ask=ask, store=store)
    assert first.binding == LocalBinding("sam-member", "member", "asked")
    assert asked == [["leo-owner", "sam-member", "ada-admin"]]

    # "Once" is the point: the second session reads the remembered answer.
    second = resolve_local_principal(ask=ask, store=store)
    assert second.binding == LocalBinding("sam-member", "member", "asked")
    assert len(asked) == 1


def test_declining_the_question_leaves_it_unresolved_and_ambiguous() -> None:
    store = FakeStore([OWNER, MEMBER])

    resolution = resolve_local_principal(ask=lambda _c: None, store=store)

    assert resolution.binding is None
    assert resolution.candidates == 2
    assert resolution.ambiguous is True
    assert read_binding() is None


def test_no_ask_callback_never_blocks_a_background_service() -> None:
    """A cron job / poller has no terminal: it must not be asked, and must not
    be silently attributed to whoever is listed first."""
    store = FakeStore([OWNER, MEMBER])

    resolution = resolve_local_principal(store=store)

    assert resolution.binding is None
    assert resolution.ambiguous is True


def test_role_change_is_picked_up_not_frozen_into_the_file() -> None:
    """A demoted person must not keep shared-block authority via the binding."""
    remember_binding("ada-admin", "admin", "asked")
    store = FakeStore([OWNER, Principal(user_id="ada-admin", display="Ada", role="member")])

    resolution = resolve_local_principal(store=store)

    assert resolution.binding == LocalBinding("ada-admin", "member", "asked")
    assert read_binding() == LocalBinding("ada-admin", "member", "asked")


def test_binding_to_an_unenrolled_person_is_forgotten() -> None:
    remember_binding("gone-member", "member", "asked")
    store = FakeStore([OWNER, MEMBER])

    resolution = resolve_local_principal(store=store)

    assert resolution.binding is None
    assert resolution.ambiguous is True
    assert read_binding() is None


def test_unreadable_directory_keeps_the_single_user_deployment_working() -> None:
    """No database configured is the single-user case: pre-FG-24 behaviour."""

    class Broken:
        async def list_principals(self):
            raise RuntimeError("no datastore configured")

    resolution = resolve_local_principal(store=Broken())

    assert resolution.binding is None
    assert resolution.candidates == 0
    # Not ambiguous — nothing to be ambiguous *between*, so writes stay allowed.
    assert resolution.ambiguous is False


def test_a_remembered_binding_survives_an_unreachable_directory() -> None:
    remember_binding("leo-owner", "owner", "setup")

    class Broken:
        async def list_principals(self):
            raise RuntimeError("database is down")

    resolution = resolve_local_principal(store=Broken())

    assert resolution.binding == LocalBinding("leo-owner", "owner", "setup")


def test_corrupt_binding_file_is_ignored_not_fatal(hermes_home: Path) -> None:
    binding_path().write_text("{not json", encoding="utf-8")

    assert read_binding() is None
    assert resolve_local_principal(store=FakeStore([OWNER])).binding is not None


def test_forget_binding_reports_whether_anything_was_removed() -> None:
    assert forget_binding() is False
    remember_binding("leo-owner", "owner", "setup")
    assert forget_binding() is True
    assert read_binding() is None


def test_binding_refuses_a_traversing_user_id() -> None:
    with pytest.raises(ValueError):
        remember_binding("../../etc", "owner", "setup")


def test_binding_is_per_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Which participation a local session acts in is a profile-level answer."""
    remember_binding("leo-owner", "owner", "setup")
    other = tmp_path / "other-profile"
    (other / "memories").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(other))

    assert read_binding() is None


def test_binding_file_records_how_it_was_decided() -> None:
    remember_binding("leo-owner", "owner", "login")

    payload = json.loads(binding_path().read_text(encoding="utf-8"))

    assert payload == {"user_id": "leo-owner", "role": "owner", "source": "login"}


# ---------------------------------------------------------------------------
# Failing closed in the store
# ---------------------------------------------------------------------------


def _audit_kinds(home: Path) -> list[str]:
    log = home / "audit" / MEMORY_AUDIT_LOG
    if not log.exists():
        return []
    return [json.loads(line)["kind"] for line in log.read_text().splitlines() if line]


def test_unresolved_ambiguous_session_cannot_write_any_target(hermes_home: Path) -> None:
    store = MemoryStore(unresolved_principal=True)
    store.load_from_disk()

    for target in ("memory", "user", "shared"):
        result = store.add(target, "A fact that belongs to somebody.")
        assert result["success"] is False
        assert "no resolved principal" in result["error"]
        assert "local-principal" in result["error"]

    assert not (hermes_home / "memories" / "MEMORY.md").exists()
    assert _audit_kinds(hermes_home) == ["memory_unresolved_write_denied"] * 3


def test_unresolved_session_still_reads_what_the_profile_shares(hermes_home: Path) -> None:
    """Refusing writes must not blind a background job to shared context."""
    (hermes_home / "memories" / "MEMORY.md").write_text(
        "- The entity ships on Fridays.", encoding="utf-8"
    )

    store = MemoryStore(unresolved_principal=True)
    store.load_from_disk()

    assert "ships on Fridays" in store.format_for_system_prompt("memory")


def test_a_resolved_session_is_unaffected(hermes_home: Path) -> None:
    store = MemoryStore(user_id="leo-owner", role="owner", unresolved_principal=True)
    store.load_from_disk()

    assert store.add("memory", "Resolved facts still save.")["success"] is True
    assert _audit_kinds(hermes_home) == []


def test_agent_init_never_asks_a_background_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """No terminal ⇒ no prompt: a poller must not block on a question."""
    import agent.agent_init as agent_init
    import hermes_cli.principal_binding as pb

    seen: dict[str, object] = {}

    def fake_resolve(**kwargs):
        seen.update(kwargs)
        return pb.LocalResolution(binding=None, candidates=2)

    monkeypatch.setattr(pb, "resolve_local_principal", fake_resolve)
    monkeypatch.setattr(pb, "can_ask", lambda: False)

    resolution = agent_init._resolve_memory_principal()

    assert seen == {"ask": None}
    assert resolution.ambiguous is True


def test_agent_init_treats_a_resolution_failure_as_single_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory is optional; a broken directory must not fail closed *or* crash."""
    import agent.agent_init as agent_init
    import hermes_cli.principal_binding as pb

    def boom(**_kwargs):
        raise RuntimeError("directory exploded")

    monkeypatch.setattr(pb, "resolve_local_principal", boom)
    monkeypatch.setattr(pb, "can_ask", lambda: False)

    resolution = agent_init._resolve_memory_principal()

    assert resolution.binding is None
    assert resolution.ambiguous is False


def test_the_single_user_deployment_still_writes(hermes_home: Path) -> None:
    """`unresolved_principal=False` is the pre-FG-24 path, byte-for-byte."""
    store = MemoryStore()
    store.load_from_disk()

    assert store.add("memory", "One person, one brain.")["success"] is True
    assert (hermes_home / "memories" / "MEMORY.md").exists()
