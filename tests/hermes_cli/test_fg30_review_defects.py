"""FG-30 review: the properties the shipped E2E suite could not see.

Every test here fails against the FG-30 implementation as merged in #250. They
are grouped by the property that was claimed and not held:

* the dismissal latch (the key was hashed over volatile counts, so a dismissed
  suggestion returned next month under a different key),
* the monthly clock (declared as a constant, never read),
* the evidence bar (the profile description counted as a signal, so any profile
  with one used skill cleared it),
* adoption's inheritance (``clone_config=True`` copies the parent's ``.env``
  and its un-promoted local skills — both listed as *not* inherited),
* owner-only retire/merge (unguarded),
* the console routes' identity (resolved as the *owner* rather than as the
  caller, so "owner only" gated nothing).

The store's CRUD is covered by ``test_fg30_profile_suggestion_e2e.py`` on real
Postgres; nothing here needs a database.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest
import yaml

from hermes_cli import profile_suggestion as ps


# ---------------------------------------------------------------------------
# The dismissal latch
# ---------------------------------------------------------------------------


def _evidence(*, uses: int, people: int, description: str) -> dict:
    return {
        "top_skills": [
            {"name": "invoice", "uses": uses},
            {"name": "cashflow", "uses": uses + 3},
        ],
        "participants": [
            {"user_id": f"u{i}", "display": f"U{i}", "role": "member"}
            for i in range(people)
        ],
        "current_description": description,
    }


def test_dedup_key_survives_the_evidence_moving() -> None:
    """The same cluster keeps its key when counts, people and prose change.

    This is the whole latch: the key is what refuses a re-proposal after a
    dismissal, and skill use counts change every week by construction.
    """
    first = ps._make_dedup_key("default", _evidence(uses=7, people=1, description="a"))
    later = ps._make_dedup_key(
        "default", _evidence(uses=41, people=3, description="rewritten")
    )
    assert first == later


def test_dedup_key_changes_when_the_cluster_changes() -> None:
    a = ps._make_dedup_key("default", _evidence(uses=7, people=1, description="a"))
    b = ps._make_dedup_key(
        "default",
        {"top_skills": [{"name": "contract", "uses": 7}, {"name": "tax", "uses": 2}]},
    )
    assert a != b


def test_evidence_identity_drops_volatile_measures() -> None:
    identity = ps.evidence_identity(_evidence(uses=7, people=2, description="prose"))
    assert identity == {"skills": ["cashflow", "invoice"], "orphan_goals": []}
    assert "uses" not in repr(identity)


# ---------------------------------------------------------------------------
# The evidence bar
# ---------------------------------------------------------------------------


def test_description_alone_is_not_evidence() -> None:
    """Every profile has a description, so counting it made the bar vacuous."""
    assert not ps._evidence_strong_enough({"current_description": "anything"})
    assert not ps._evidence_strong_enough(
        {"current_description": "anything", "top_skills": [{"name": "invoice", "uses": 1}]}
    )


def test_a_cluster_plus_corroboration_clears_the_bar() -> None:
    assert ps._evidence_strong_enough(
        {
            "top_skills": [{"name": "invoice", "uses": 9}, {"name": "tax", "uses": 4}],
            "orphan_goals": [{"id": "g1", "title": "file the return", "tier": "operational"}],
        }
    )


def test_skills_without_corroboration_do_not_clear_the_bar() -> None:
    assert not ps._evidence_strong_enough(
        {"top_skills": [{"name": "invoice", "uses": 9}, {"name": "tax", "uses": 4}]}
    )


# ---------------------------------------------------------------------------
# The roster stays local (FG-30 §4.2 T3 Q1)
# ---------------------------------------------------------------------------


def test_the_roster_is_not_sent_to_the_aux_llm() -> None:
    """``participants`` corroborates locally but never leaves the box.

    Every active principal's ``user_id``, ``display`` and ``role`` would
    otherwise be serialised into the prompt sent to a third-party model
    each monthly pass, just to name a profile — which naming does not
    need. The evidence bar still counts the roster; the prompt does not.
    """
    evidence = {
        "top_skills": [{"name": "invoice", "uses": 9}, {"name": "tax", "uses": 4}],
        "orphan_goals": [{"id": "g1", "title": "file the return", "tier": "operational"}],
        "participants": [
            {"user_id": "u1", "display": "Ada", "role": "owner"},
            {"user_id": "u2", "display": "Bob", "role": "member"},
        ],
        "current_description": "build the product",
    }

    for_prompt = ps._evidence_for_prompt(evidence)

    assert "participants" not in for_prompt
    assert "Ada" not in repr(for_prompt) and "Bob" not in repr(for_prompt)
    # The local signal is untouched — the bar still corroborates on it.
    assert evidence["participants"] == evidence["participants"]


def test_the_roster_still_corroborates_locally() -> None:
    """Dropping it from the prompt must not weaken the evidence bar."""
    with_roster = {
        "top_skills": [{"name": "invoice", "uses": 9}, {"name": "tax", "uses": 4}],
        "participants": [
            {"user_id": "u1", "display": "Ada", "role": "owner"},
            {"user_id": "u2", "display": "Bob", "role": "member"},
        ],
    }
    assert ps._evidence_strong_enough(with_roster)


# ---------------------------------------------------------------------------
# The monthly clock
# ---------------------------------------------------------------------------


class _ClockConn:
    """Just enough of an asyncpg connection to answer the clock's one query."""

    def __init__(self, last: Optional[datetime]) -> None:
        self._last = last
        self.queries: list[str] = []

    async def fetchval(self, query: str, *args: object) -> Optional[datetime]:
        self.queries.append(query)
        return self._last


NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_generation_is_due_when_nothing_was_ever_proposed() -> None:
    assert await ps._generation_due(_ClockConn(None), "default", now=NOW)


@pytest.mark.asyncio
async def test_generation_is_not_due_a_week_after_the_last_proposal() -> None:
    """The one-open cap does not bound cadence: a dismissal frees the slot."""
    recent = NOW - timedelta(days=7)
    assert not await ps._generation_due(_ClockConn(recent), "default", now=NOW)


@pytest.mark.asyncio
async def test_generation_is_due_after_the_interval() -> None:
    old = NOW - timedelta(days=31)
    assert await ps._generation_due(_ClockConn(old), "default", now=NOW)


@pytest.mark.asyncio
async def test_the_clock_reads_the_last_proposal_of_any_status() -> None:
    """A dismissal must not reset the clock, so the query cannot filter status."""
    conn = _ClockConn(NOW - timedelta(days=2))
    await ps._generation_due(conn, "default", now=NOW)
    assert "status" not in conn.queries[0]


# ---------------------------------------------------------------------------
# Adoption's inheritance
# ---------------------------------------------------------------------------


@pytest.fixture()
def parent_home(tmp_path: Path) -> Path:
    home = tmp_path / "parent"
    (home / "skills" / "local-only").mkdir(parents=True)
    (home / "skills" / "local-only" / "SKILL.md").write_text("# local\n")
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "sonnet"}}), encoding="utf-8"
    )
    (home / ".env").write_text("ANTHROPIC_API_KEY=parent-secret\n", encoding="utf-8")
    return home


def test_adoption_inherits_config_but_not_credentials_or_local_skills(
    parent_home: Path, tmp_path: Path
) -> None:
    adopted = tmp_path / "adopted"
    adopted.mkdir()

    ps.inherit_profile_config(adopted, parent_home=parent_home)

    config = yaml.safe_load((adopted / "config.yaml").read_text())
    assert config["model"]["default"] == "sonnet"
    assert not (adopted / ".env").exists(), "the parent's credentials were copied"
    assert not (adopted / "skills" / "local-only").exists(), (
        "un-promoted local skills were copied instead of going through promotion"
    )


def test_adoption_reaches_promoted_skills_through_the_shared_tier(
    parent_home: Path, tmp_path: Path
) -> None:
    adopted = tmp_path / "adopted"
    adopted.mkdir()

    ps.inherit_profile_config(adopted, parent_home=parent_home)

    config = yaml.safe_load((adopted / "config.yaml").read_text())
    external = [str(entry) for entry in config.get("skills", {}).get("external_dirs", [])]
    assert any(entry.endswith("skills-shared") for entry in external), external


def _code_of(func: object) -> str:
    """The function's body with its docstring removed."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(statement) for statement in body)


def test_adopt_does_not_clone_the_parent_profile() -> None:
    code = _code_of(ps.ProfileSuggestionStore.adopt)
    assert "clone_config" not in code
    assert "clone_all" not in code
    assert "clone_from" not in code


# ---------------------------------------------------------------------------
# The goal-tree calls have to be calls that exist
# ---------------------------------------------------------------------------


def test_every_goal_registry_call_is_a_real_method() -> None:
    """The seeding and completion paths called an API that does not exist.

    ``GoalRegistryStore()`` takes a required store and has ``set_status``, not
    ``update_goal`` — so adoption's sub-goal seeding and retirement's goal
    completion both raised ``TypeError``/``AttributeError`` on the first line
    and were swallowed by their own ``except Exception: log.warning``. Two of
    this FG's headline behaviours did nothing, quietly, and no test noticed
    because no test reached them. Asserted over the module rather than per call
    site, so the next one cannot be added silently.
    """
    from hermes_cli.goal_registry import GoalRegistryStore

    source = Path(ps.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id == "GoalRegistryStore":
            assert node.args or node.keywords, "GoalRegistryStore needs its store"
            checked += 1
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "registry"
        ):
            assert hasattr(GoalRegistryStore, target.attr), (
                f"GoalRegistryStore has no method {target.attr!r}"
            )
            checked += 1
    assert checked, "the goal-registry call sites moved; re-point this test"


# ---------------------------------------------------------------------------
# Channel-less is not the same as gateway-stopped
# ---------------------------------------------------------------------------


def test_a_profile_with_no_platform_token_is_channel_less(tmp_path: Path) -> None:
    home = tmp_path / "adopted"
    home.mkdir()
    (home / ".env").write_text("# nothing yet\nANTHROPIC_API_KEY=k\n", encoding="utf-8")
    assert not ps.profile_has_channel(home)


def test_a_profile_with_a_platform_token_has_a_channel(tmp_path: Path) -> None:
    home = tmp_path / "committed"
    home.mkdir()
    (home / ".env").write_text('TELEGRAM_BOT_TOKEN="123:abc"\n', encoding="utf-8")
    assert ps.profile_has_channel(home)


def test_an_empty_token_is_not_a_channel(tmp_path: Path) -> None:
    home = tmp_path / "half-committed"
    home.mkdir()
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    assert not ps.profile_has_channel(home)


# ---------------------------------------------------------------------------
# Owner-only lifecycle verbs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retire_refuses_a_non_owner() -> None:
    from hermes_cli.access import Principal

    member = Principal(user_id="pupil", display="pupil", role="member")
    with pytest.raises(PermissionError):
        await ps.retire_profile("whatever", member, promotions=None)


@pytest.mark.asyncio
async def test_merge_refuses_a_non_owner() -> None:
    from hermes_cli.access import Principal

    admin = Principal(user_id="teacher", display="teacher", role="admin")
    with pytest.raises(PermissionError):
        await ps.merge_profiles("a", "b", admin, promotions=None)


# ---------------------------------------------------------------------------
# The console routes act as the caller, not as the owner
# ---------------------------------------------------------------------------


ROUTE_HANDLERS = (
    "list_profile_suggestions_endpoint",
    "adopt_profile_suggestion_endpoint",
    "dismiss_profile_suggestion_endpoint",
)


@pytest.mark.parametrize("handler_name", ROUTE_HANDLERS)
def test_suggestion_routes_bind_the_requesting_principal(handler_name: str) -> None:
    """A route that resolves ``get_owner()`` runs every caller as the owner.

    "Owner only" then gates nothing — ``principal.is_owner`` is true by
    construction — and the C5 audit attributes a member's adoption to the owner.
    This is the FG-28 owner-fallback hazard in a new route family, so it is
    asserted over the route table rather than one endpoint at a time.
    """
    from hermes_cli import web_server

    handler = getattr(web_server, handler_name)
    code = _code_of(handler)
    assert "get_owner" not in code, "the route resolves the owner, not the caller"
    assert "_comms_resolve_principal(request)" in code
    assert "request" in inspect.signature(handler).parameters


# ---------------------------------------------------------------------------
# Commit-to-channel (FG-30 §4.2 T2)
# ---------------------------------------------------------------------------


def _profile_tree(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    """Build a fake profile tree under the (conftest-isolated) HERMES_HOME.

    `get_default_hermes_root()` reads `HERMES_HOME`, which the autouse
    `_hermetic_environment` fixture points at a temp dir — so profiles live
    under `<HERMES_HOME>/profiles/<name>` and the default profile *is*
    `<HERMES_HOME>`. Returns (root, {name: dir}) with `finance` (the profile
    to commit) and `other` (a holder of a colliding token).
    """
    from hermes_constants import get_hermes_home

    root = Path(get_hermes_home())
    profiles = root / "profiles"
    finance = profiles / "finance"
    other = profiles / "other"
    finance.mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)
    (finance / ".env").write_text("# empty\n", encoding="utf-8")
    return root, {"default": root, "finance": finance, "other": other}


def test_find_token_collision_names_the_holder(tmp_path: Path) -> None:
    """The pre-write refusal names the profile that already holds the token.

    The runtime `EX_CONFIG` stop is a backstop, not a UX — discovering a
    collision as "the service will not start" is a bad way to learn you pasted
    the wrong token. ``find_token_collision`` reads every other profile's
    ``.env`` and returns the holder's name.
    """
    _root, dirs = _profile_tree(tmp_path)
    (dirs["other"] / ".env").write_text('TELEGRAM_BOT_TOKEN="shared"\n', encoding="utf-8")

    holder = ps.find_token_collision("telegram", "shared", skip_profile="finance")
    assert holder == "other"
    # An unused token has no holder.
    assert ps.find_token_collision("telegram", "fresh", skip_profile="finance") is None


def test_find_token_collision_is_per_platform(tmp_path: Path) -> None:
    """Two platforms may use the same-shaped string — only the same platform's
    token key is compared, so a Discord token that happens to equal a Telegram
    token in another profile does not trip the Telegram collision check."""
    _root, dirs = _profile_tree(tmp_path)
    (dirs["other"] / ".env").write_text('DISCORD_BOT_TOKEN="xyz"\n', encoding="utf-8")

    assert ps.find_token_collision("telegram", "xyz", skip_profile="finance") is None
    assert ps.find_token_collision("discord", "xyz", skip_profile="finance") == "other"


def test_commit_channel_refuses_a_collision_before_writing(tmp_path: Path) -> None:
    """The collision is refused *before* the target's ``.env`` is touched.

    No half-written state: the token is never written, so the profile stays
    channel-less and `hermes doctor` keeps reporting it as adoptable. The
    exception names the holder.
    """
    _root, dirs = _profile_tree(tmp_path)
    (dirs["other"] / ".env").write_text('TELEGRAM_BOT_TOKEN="dup"\n', encoding="utf-8")
    finance_env = dirs["finance"] / ".env"
    finance_env.write_text("# empty\n", encoding="utf-8")

    with pytest.raises(ps.ChannelCollisionError) as exc_info:
        ps.commit_channel(
            "finance", platform="telegram", token="dup", start_service=False
        )
    assert exc_info.value.holder == "other"
    assert "other" in str(exc_info.value)
    # The target's .env was NOT modified — still channel-less.
    assert "dup" not in finance_env.read_text(encoding="utf-8")
    assert not ps.profile_has_channel(dirs["finance"])


def test_commit_channel_writes_into_the_profiles_own_env_and_gains_a_channel(
    tmp_path: Path,
) -> None:
    """After a successful commit the profile moves to the ok line.

    This is the assertion §4.2 T2 calls worth writing: the token lands in the
    profile's *own* `.env` (never the process environment, #219/#220 — verified
    by reading the file, not the process), and `profile_has_channel` then
    reads it as configured. `start_service=False` keeps the test off the
    service manager, which a CI box does not have.
    """
    _root, dirs = _profile_tree(tmp_path)
    import os

    finance_env = dirs["finance"] / ".env"
    finance_env.write_text("# empty\n", encoding="utf-8")
    # The another-profile's .env is the wrong destination (#219/#220): the
    # override must route the write into finance's own file, never the
    # default profile's. The default home here is the root itself.
    default_env = dirs["default"] / ".env"

    result = ps.commit_channel(
        "finance", platform="telegram", token="123:abc-fresh", start_service=False
    )

    assert result["channel_less"] is False
    # The target profile's file carries the token; the default profile's does not.
    written = finance_env.read_text(encoding="utf-8")
    assert "123:abc-fresh" in written
    assert "TELEGRAM_BOT_TOKEN" in written
    assert not default_env.exists() or "123:abc-fresh" not in default_env.read_text(
        encoding="utf-8", errors="replace"
    )
    # The doctor's read now says the profile has a channel.
    assert ps.profile_has_channel(dirs["finance"])


def test_commit_channel_unknown_platform_is_refused(tmp_path: Path) -> None:
    """Channels with complex credentials (WhatsApp, Signal) have their own
    wizards and are not committable here — refused with a pointer, not a
    half-configured .env."""
    _root, dirs = _profile_tree(tmp_path)

    with pytest.raises(ValueError):
        ps.commit_channel(
            "finance", platform="whatsapp", token="x", start_service=False
        )
