"""Postgres E2E for FG-29: the goal tree, the publish, and skill promotion.

Real everything: a throwaway Postgres (two profile schemas, so the cross-profile
publish is an actual crossing rather than a stub), the suite's per-test
``HERMES_HOME`` for the skills on disk, and the real ``skills.external_dirs``
config path for the shared library. Mocks are avoided deliberately — the
properties under test are *isolation*, *authorisation* and *what ends up on
disk*, and none of those can be demonstrated against a fake.

What each group proves:

* tree — a parent must outlive its child, cycles and over-deep chains are
  refused at write time, and an orphan operational goal still ladders upward.
* publish — the copy carries provenance, is read-only where it lands, goes stale
  when the source is edited, and re-publishing clears it. The audit says who
  published which revision into which profile.
* measure — a parent rolls up the mean of its *measurable* children and names
  the ones it had to leave out.
* promotion — two ordered approvals with distinct authority, exact reviewed
  bytes, consent for private-derived skills, a hard cap that evicts only for a
  strictly stronger entrant, and a shared tier the curator cannot write.
* conflict — antagonistic siblings reach the owner, and nothing is resolved.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from agent.skill_utils import is_external_skill_path
from hermes_cli.access import Principal, Role
from hermes_cli.datastore import connect_for_publish, get_store
from hermes_cli.goal_conflicts import (
    alert_owner,
    detect_conflicts,
    record_decision_for_pair,
    weekly_digest,
)
from hermes_cli.goal_purpose import (
    DEFAULT_ENTITY_GOAL_TITLE,
    build_snapshot,
    ensure_default_entity_goal,
)
from hermes_cli.goal_registry import GOALS_TABLE, GoalRegistryStore
from hermes_cli.goal_tree import (
    GoalCycleError,
    GoalDepthError,
    GoalTierError,
    GoalTreeError,
    GoalTreeStore,
)
from hermes_cli.goals import GoalMetric
from hermes_cli.skill_promotion import (
    PromotionBodyChangedError,
    PromotionConsentError,
    PromotionError,
    PromotionSettings,
    SkillPromotionStore,
    body_hash,
    shared_skills_dir,
)

NOON = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the FG-29 E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the FG-29 E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg29-{uuid.uuid4().hex[:12]}"
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


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _fresh_tree(dsn: str) -> GoalTreeStore:
    """Drop and recreate both profile schemas, then initialise the registry."""
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_dev CASCADE")
        await conn.execute("DROP SCHEMA IF EXISTS app_dev_school CASCADE")
    finally:
        await conn.close()
    registry = GoalRegistryStore(get_store("supabase-app", "dev", config=_config(dsn)))
    await registry.initialize()
    return GoalTreeStore(registry)


def _principal(user_id: str, role: Role = "member") -> Principal:
    return Principal(user_id=user_id, display=user_id, role=role)


OWNER = _principal("root", "owner")
ADMIN = _principal("teacher", "admin")
MEMBER = _principal("pupil", "member")


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_goals_default_to_operational_and_stay_out_of_prompts(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    goal = await tree.registry.create_goal(MEMBER, "Book the flight")
    assert goal.tier == "operational"

    snapshot = await build_snapshot(tree, OWNER, profile="default")
    assert snapshot.stable == ()
    assert snapshot.participants == {}


@pytest.mark.asyncio
async def test_a_parent_must_outlive_its_child(postgres_dsn: str) -> None:
    tree = await _fresh_tree(postgres_dsn)
    entity = await tree.registry.create_goal(
        OWNER, "Keep the school open", visibility="shared", tier="entity"
    )
    operational = await tree.registry.create_goal(
        OWNER, "Send the newsletter", visibility="shared"
    )

    # Downward is fine.
    await tree.set_parent(OWNER, operational.id, entity.id)

    # Upward is refused: an entity goal cannot hang under a session-lived one.
    with pytest.raises(GoalTierError):
        await tree.set_parent(OWNER, entity.id, operational.id)


@pytest.mark.asyncio
async def test_cycles_and_over_deep_chains_are_refused_at_write_time(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    entity = await tree.registry.create_goal(
        OWNER, "Entity", visibility="shared", tier="entity"
    )
    profile = await tree.registry.create_goal(
        OWNER, "Profile", visibility="shared", tier="profile"
    )
    participant = await tree.registry.create_goal(
        OWNER, "Participant", visibility="shared", tier="participant"
    )
    operational = await tree.registry.create_goal(
        OWNER, "Operational", visibility="shared"
    )
    await tree.set_parent(OWNER, profile.id, entity.id)
    await tree.set_parent(OWNER, participant.id, profile.id)
    await tree.set_parent(OWNER, operational.id, participant.id)

    # A goal cannot be its own parent (DB constraint via the tier check first).
    with pytest.raises((GoalTierError, GoalCycleError, asyncpg.PostgresError)):
        await tree.set_parent(OWNER, entity.id, entity.id)

    # Four tiers is the whole ladder; a fifth level has no lifetime to describe
    # it. Attaching a second operational goal under the deepest one overflows.
    deeper = await tree.registry.create_goal(OWNER, "Deeper", visibility="shared")
    with pytest.raises((GoalDepthError, GoalTierError)):
        await tree.set_parent(OWNER, deeper.id, operational.id)


@pytest.mark.asyncio
async def test_only_the_owner_may_move_a_goal_into_a_prompt_tier(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    goal = await tree.registry.create_goal(ADMIN, "Fill Y7", visibility="shared")

    with pytest.raises(PermissionError):
        await tree.set_tier(ADMIN, goal.id, "profile")

    promoted = await tree.set_tier(OWNER, goal.id, "profile")
    assert promoted.tier == "profile"


@pytest.mark.asyncio
async def test_an_orphan_operational_goal_still_ladders_upward(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    entity = await tree.registry.create_goal(
        OWNER, "Keep the school open", visibility="shared", tier="entity"
    )
    profile = await tree.registry.create_goal(
        OWNER, "Fill Y7", visibility="shared", tier="profile"
    )
    await tree.set_parent(OWNER, profile.id, entity.id)

    # Created in passing during a session, attached to nothing.
    orphan = await tree.registry.create_goal(OWNER, "Chase a deposit", visibility="shared")
    chain = [goal.id for goal in await tree.ladder(OWNER, orphan.id)]
    assert chain == [orphan.id, profile.id, entity.id]


@pytest.mark.asyncio
async def test_the_default_first_goal_exists_and_is_editable(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    goal, created = await ensure_default_entity_goal(tree, OWNER)
    assert created is True
    assert goal.title == DEFAULT_ENTITY_GOAL_TITLE

    again, created_again = await ensure_default_entity_goal(tree, OWNER)
    assert created_again is False
    assert again.id == goal.id

    edited = await tree.set_entity_goal_text(
        OWNER, goal.id, title="Keep the school open", description="Enrolment over 420"
    )
    assert edited.title == "Keep the school open"
    assert edited.source_rev == goal.source_rev + 1

    # A member cannot rewrite what sits in every session's system prompt.
    with pytest.raises(PermissionError):
        await tree.set_entity_goal_text(MEMBER, goal.id, title="Something else")


# ---------------------------------------------------------------------------
# The publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_carries_provenance_goes_stale_and_is_read_only(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    source = await tree.registry.create_goal(
        OWNER,
        "Keep the school open",
        description="Enrolment above 420 without borrowing",
        visibility="shared",
        tier="entity",
    )

    # Only the owner publishes.
    with pytest.raises(PermissionError):
        await tree.publish_entity_goal(ADMIN, profiles=["school"])

    results = await tree.publish_entity_goal(OWNER, profiles=["school"])
    assert [(r.profile, r.created, r.revision) for r in results] == [
        ("school", True, source.source_rev)
    ]

    store = get_store("supabase-app", "dev", config=_config(postgres_dsn))
    conn = await connect_for_publish(store, profile="school")
    try:
        row = await conn.fetchrow(
            f"SELECT * FROM {GOALS_TABLE} WHERE published_from_goal_id = $1",
            source.id,
        )
        assert row is not None
        assert row["title"] == "Keep the school open"
        assert row["description"] == "Enrolment above 420 without borrowing"
        assert row["published_from_profile"] == "default"
        assert row["published_rev"] == source.source_rev
        assert row["stale"] is False
        copy_id = str(row["id"])

        # The copy is read-only where it landed: the registry's update path
        # refuses a row that came from another profile.
        school_registry = GoalRegistryStore(store)
        with pytest.raises(PermissionError):
            await school_registry._update_goal_field(
                OWNER, copy_id, "title", "Local rewrite", connection=conn
            )
    finally:
        await conn.close()

    # Editing the source marks the copy behind, without touching its bytes.
    await tree.set_entity_goal_text(OWNER, source.id, title="Keep the school open (v2)")
    conn = await connect_for_publish(store, profile="school")
    try:
        row = await conn.fetchrow(
            f"SELECT title, stale FROM {GOALS_TABLE} WHERE published_from_goal_id = $1",
            source.id,
        )
        assert row["stale"] is True
        assert row["title"] == "Keep the school open"
    finally:
        await conn.close()

    # Re-publishing refreshes the text and clears the flag.
    again = await tree.publish_entity_goal(OWNER, profiles=["school"])
    assert again[0].created is False
    conn = await connect_for_publish(store, profile="school")
    try:
        row = await conn.fetchrow(
            f"SELECT title, stale FROM {GOALS_TABLE} WHERE published_from_goal_id = $1",
            source.id,
        )
        assert row["stale"] is False
        assert row["title"] == "Keep the school open (v2)"
    finally:
        await conn.close()

    # And the audit says who did it, into where, at which revision.
    trail = await tree.publish_audit(source.id)
    assert [entry["action"] for entry in trail] == ["created", "refreshed"]
    assert {entry["target_profile"] for entry in trail} == {"school"}
    assert {entry["actor_user_id"] for entry in trail} == {"root"}


@pytest.mark.asyncio
async def test_a_published_copy_is_what_the_receiving_profile_sees_as_purpose(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    source = await tree.registry.create_goal(
        OWNER, "Keep the school open", visibility="shared", tier="entity"
    )
    await tree.publish_entity_goal(OWNER, profiles=["school"])

    store = get_store("supabase-app", "dev", config=_config(postgres_dsn))
    school = GoalTreeStore(GoalRegistryStore(store))
    conn = await connect_for_publish(store, profile="school")
    try:
        goals = await school.registry.list_goals(OWNER, status="active", connection=conn)
        entity = [goal for goal in goals if goal.tier == "entity"]
        assert len(entity) == 1
        assert entity[0].is_published_copy
        assert entity[0].published_from_profile == "default"
        assert entity[0].published_from_goal_id == source.id
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# The shared measure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_uses_measurable_children_and_names_the_rest(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    entity = await tree.registry.create_goal(
        OWNER, "Keep the school open", visibility="shared", tier="entity"
    )
    measured = await tree.registry.create_goal(
        OWNER, "Fill Y7", visibility="shared", tier="profile"
    )
    unmeasured = await tree.registry.create_goal(
        OWNER, "Improve reputation", visibility="shared", tier="profile"
    )
    await tree.set_parent(OWNER, measured.id, entity.id)
    await tree.set_parent(OWNER, unmeasured.id, entity.id)

    await tree.registry.add_metric(
        OWNER, measured.id, GoalMetric("enrolled", target=100, unit="pupils")
    )
    await tree.set_primary_metric(OWNER, measured.id, "enrolled")
    await tree.registry.set_metric_value(OWNER, measured.id, "enrolled", 60)

    rolled = await tree.rollup(OWNER, entity.id)
    assert rolled is not None
    assert rolled.progress == pytest.approx(0.6)
    assert rolled.unmeasured_children == (unmeasured.id,)
    # The unmeasured child is excluded, not counted as zero: 0.6, not 0.3.
    assert rolled.progress != pytest.approx(0.3)

    # An at_most metric normalises in its own direction.
    cost = await tree.registry.create_goal(
        OWNER, "Hold costs", visibility="shared", tier="profile"
    )
    await tree.set_parent(OWNER, cost.id, entity.id)
    await tree.registry.add_metric(
        OWNER, cost.id, GoalMetric("spend", target=1000, direction="at_most")
    )
    await tree.set_primary_metric(OWNER, cost.id, "spend")
    await tree.registry.set_metric_value(OWNER, cost.id, "spend", 500)
    cost_rollup = await tree.rollup(OWNER, cost.id)
    assert cost_rollup is not None and cost_rollup.progress is not None
    assert cost_rollup.progress > 0.5


@pytest.mark.asyncio
async def test_a_metric_must_exist_before_it_can_be_the_primary_one(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    goal = await tree.registry.create_goal(
        OWNER, "Fill Y7", visibility="shared", tier="profile"
    )
    with pytest.raises(GoalTreeError):
        await tree.set_primary_metric(OWNER, goal.id, "enrolled")


@pytest.mark.asyncio
async def test_long_lived_goals_without_a_measure_are_reported(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    entity = await tree.registry.create_goal(
        OWNER, "Keep the school open", visibility="shared", tier="entity"
    )
    operational = await tree.registry.create_goal(
        OWNER, "Book the flight", visibility="shared"
    )
    stale = await tree.unmeasured_long_lived(
        OWNER, now=datetime.now(timezone.utc) + timedelta(days=30)
    )
    ids = {goal.id for goal in stale}
    assert entity.id in ids
    # An operational goal is not expected to carry a quarterly measure.
    assert operational.id not in ids


# ---------------------------------------------------------------------------
# Skill promotion
# ---------------------------------------------------------------------------


def _write_skill(name: str, body: str = "Do the thing.") -> None:
    from hermes_constants import get_skills_dir

    target = get_skills_dir() / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _seed_usage(name: str, *, uses: int, age_days: int) -> None:
    from tools.skill_usage import load_usage, save_usage

    now = datetime.now(timezone.utc)
    data = load_usage()
    data[name] = {
        "created_by": "agent",
        "use_count": uses,
        "view_count": 0,
        "last_used_at": now.isoformat(),
        "last_viewed_at": None,
        "patch_count": 0,
        "last_patched_at": None,
        "created_at": (now - timedelta(days=age_days)).isoformat(),
        "state": "active",
        "pinned": False,
        "archived_at": None,
    }
    save_usage(data)


async def _promotions(dsn: str, **settings) -> tuple[GoalTreeStore, SkillPromotionStore]:
    tree = await _fresh_tree(dsn)
    store = SkillPromotionStore(
        tree.registry._store, settings=PromotionSettings(**settings)
    )
    await store.initialize()
    return tree, store


@pytest.mark.asyncio
async def test_promotion_needs_two_ordered_approvals_from_two_authorities(
    postgres_dsn: str,
) -> None:
    _tree, promotions = await _promotions(postgres_dsn)
    _write_skill("enrolment-report")
    _seed_usage("enrolment-report", uses=20, age_days=60)

    candidate = await promotions.propose(
        ADMIN, "enrolment-report", rationale="every profile writes this by hand"
    )
    assert candidate.state == "proposed"
    assert candidate.score > 0

    # A member is not a reviewer at either stage.
    with pytest.raises(PermissionError):
        await promotions.approve_in_profile(MEMBER, candidate.id)

    # The owner cannot skip the origin profile's review.
    with pytest.raises(PromotionError):
        await promotions.approve_for_entity(OWNER, candidate.id)

    reviewed = await promotions.approve_in_profile(ADMIN, candidate.id, note="ok here")
    assert reviewed.state == "profile_approved"
    assert reviewed.profile_reviewer == "teacher"

    # An admin may review for the profile but may NOT accept for the entity.
    with pytest.raises(PermissionError):
        await promotions.approve_for_entity(ADMIN, candidate.id)

    approved, displaced = await promotions.approve_for_entity(OWNER, candidate.id)
    assert approved.state == "approved"
    assert approved.owner_reviewer == "root"
    assert displaced is None

    installed = shared_skills_dir() / "enrolment-report" / "SKILL.md"
    assert installed.exists()
    assert body_hash(installed.read_text(encoding="utf-8")) == approved.body_sha256

    trail = [row["action"] for row in await promotions.audit_trail(candidate.id)]
    assert trail == ["proposed", "profile_approved", "approved"]


@pytest.mark.asyncio
async def test_the_shared_tier_is_read_only_to_autonomous_curation(
    postgres_dsn: str,
) -> None:
    """The audited path is the only way in — by construction, not by convention.

    Promotion registers the shared library under ``skills.external_dirs``, which
    is exactly the marker ``agent.skill_utils.is_external_skill_path`` uses to
    tell the curator a skill is not its to rewrite or archive.
    """
    _tree, promotions = await _promotions(postgres_dsn)
    _write_skill("timetable-clash")
    _seed_usage("timetable-clash", uses=20, age_days=60)
    candidate = await promotions.propose(ADMIN, "timetable-clash")
    await promotions.approve_in_profile(ADMIN, candidate.id)
    await promotions.approve_for_entity(OWNER, candidate.id)

    promoted = shared_skills_dir() / "timetable-clash" / "SKILL.md"
    assert is_external_skill_path(promoted) is True

    # And a skill that already lives there cannot be re-promoted from there.
    with pytest.raises(PromotionError):
        await promotions.propose(ADMIN, "not-a-skill-at-all")


@pytest.mark.asyncio
async def test_approval_refuses_bytes_nobody_reviewed(postgres_dsn: str) -> None:
    _tree, promotions = await _promotions(postgres_dsn)
    _write_skill("marking-rubric", body="Original text.")
    _seed_usage("marking-rubric", uses=20, age_days=60)
    candidate = await promotions.propose(ADMIN, "marking-rubric")
    await promotions.approve_in_profile(ADMIN, candidate.id)

    _write_skill("marking-rubric", body="Rewritten after review.")
    with pytest.raises(PromotionBodyChangedError):
        await promotions.approve_for_entity(OWNER, candidate.id)
    assert not (shared_skills_dir() / "marking-rubric").exists()

    trail = [row["action"] for row in await promotions.audit_trail(candidate.id)]
    assert "body_changed" in trail


@pytest.mark.asyncio
async def test_a_private_derived_skill_needs_recorded_consent(
    postgres_dsn: str,
) -> None:
    _tree, promotions = await _promotions(postgres_dsn)
    _write_skill("pupil-notes")
    _seed_usage("pupil-notes", uses=20, age_days=60)
    candidate = await promotions.propose(
        ADMIN, "pupil-notes", derived_from_private=True
    )
    assert candidate.has_consent is False
    with pytest.raises(PromotionConsentError):
        await promotions.approve_in_profile(ADMIN, candidate.id)
    assert "consent_refused" in [
        row["action"] for row in await promotions.audit_trail(candidate.id)
    ]

    await promotions.reject(ADMIN, candidate.id, reason="no consent")
    consented = await promotions.propose(
        ADMIN,
        "pupil-notes",
        derived_from_private=True,
        consent_user_ids=["pupil"],
    )
    assert consented.has_consent is True
    assert (
        await promotions.approve_in_profile(ADMIN, consented.id)
    ).state == "profile_approved"


@pytest.mark.asyncio
async def test_below_threshold_candidates_are_stored_but_not_in_the_digest(
    postgres_dsn: str,
) -> None:
    _tree, promotions = await _promotions(postgres_dsn, threshold=0.9)
    _write_skill("barely-used")
    _seed_usage("barely-used", uses=1, age_days=1)
    candidate = await promotions.propose(ADMIN, "barely-used")
    assert candidate.score < 0.9

    assert candidate.id in {
        item.id for item in await promotions.list_candidates(OWNER)
    }
    assert candidate.id not in {
        item.id for item in await promotions.digest_candidates(OWNER)
    }


@pytest.mark.asyncio
async def test_the_cap_evicts_only_for_a_strictly_stronger_entrant(
    postgres_dsn: str,
) -> None:
    _tree, promotions = await _promotions(postgres_dsn, max_shared_skills=2)

    async def promote(name: str, *, uses: int, age_days: int):
        _write_skill(name)
        _seed_usage(name, uses=uses, age_days=age_days)
        candidate = await promotions.propose(ADMIN, name)
        await promotions.approve_in_profile(ADMIN, candidate.id)
        return await promotions.approve_for_entity(OWNER, candidate.id)

    await promote("strong-a", uses=20, age_days=60)
    await promote("weak-b", uses=2, age_days=1)
    residents = {item.skill_name for item in await promotions.shared_skills()}
    assert residents == {"strong-a", "weak-b"}

    # At the cap, a weaker newcomer is refused rather than queued forever.
    _write_skill("weaker-c")
    _seed_usage("weaker-c", uses=1, age_days=0)
    weaker = await promotions.propose(ADMIN, "weaker-c")
    await promotions.approve_in_profile(ADMIN, weaker.id)
    with pytest.raises(PromotionError):
        await promotions.approve_for_entity(OWNER, weaker.id)
    assert not (shared_skills_dir() / "weaker-c").exists()

    # A strictly stronger one displaces the weakest resident, whose origin copy
    # survives untouched.
    _candidate, displaced = await promote("strong-d", uses=30, age_days=90)
    assert displaced is not None and displaced.skill_name == "weak-b"
    residents = {item.skill_name for item in await promotions.shared_skills()}
    assert residents == {"strong-a", "strong-d"}
    assert not (shared_skills_dir() / "weak-b").exists()

    from hermes_constants import get_skills_dir

    assert (get_skills_dir() / "weak-b" / "SKILL.md").exists()

    demote_trail = [
        row["action"]
        for row in await promotions.audit_trail(
            [
                item
                for item in await promotions.list_candidates(
                    OWNER, states=("approved",)
                )
                if item.skill_name == "weak-b"
            ][0].id
        )
    ]
    assert "demoted" in demote_trail


@pytest.mark.asyncio
async def test_owner_demotion_keeps_the_origin_copy(postgres_dsn: str) -> None:
    _tree, promotions = await _promotions(postgres_dsn)
    _write_skill("attendance-chase")
    _seed_usage("attendance-chase", uses=20, age_days=60)
    candidate = await promotions.propose(ADMIN, "attendance-chase")
    await promotions.approve_in_profile(ADMIN, candidate.id)
    await promotions.approve_for_entity(OWNER, candidate.id)

    with pytest.raises(PermissionError):
        await promotions.demote(ADMIN, "attendance-chase", reason="no")

    demoted = await promotions.demote(
        OWNER, "attendance-chase", reason="superseded by the MIS export"
    )
    assert demoted is not None
    assert not (shared_skills_dir() / "attendance-chase").exists()

    from hermes_constants import get_skills_dir

    assert (get_skills_dir() / "attendance-chase" / "SKILL.md").exists()
    assert await promotions.shared_skills() == []
    assert len(await promotions.shared_skills(include_demoted=True)) == 1


# ---------------------------------------------------------------------------
# Sibling conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_antagonistic_siblings_reach_the_owner_and_nothing_is_resolved(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    entity = await tree.registry.create_goal(
        OWNER, "Keep the school open", visibility="shared", tier="entity"
    )
    cash = await tree.registry.create_goal(
        OWNER, "Hold three months of cash", visibility="shared", tier="profile"
    )
    quality = await tree.registry.create_goal(
        OWNER, "Raise teaching quality", visibility="shared", tier="profile"
    )
    await tree.set_parent(OWNER, cash.id, entity.id)
    await tree.set_parent(OWNER, quality.id, entity.id)

    await tree.registry.add_metric(OWNER, cash.id, GoalMetric("cash_months", target=3))
    await tree.set_primary_metric(OWNER, cash.id, "cash_months")
    await tree.registry.add_metric(
        OWNER, quality.id, GoalMetric("lesson_score", target=4)
    )
    await tree.set_primary_metric(OWNER, quality.id, "lesson_score")

    conn = await tree.registry._connect()
    try:
        base = datetime.now(timezone.utc) - timedelta(days=6)
        for index, (cash_value, quality_value) in enumerate(
            [(3.0, 2.0), (2.5, 2.5), (2.0, 3.0), (1.5, 3.5)]
        ):
            when = base + timedelta(days=index)
            await conn.execute(
                """
                INSERT INTO goal_progress (goal_id, metric_name, ts, value, note)
                VALUES ($1, 'cash_months', $2, $3, '')
                """,
                cash.id,
                when,
                cash_value,
            )
            await conn.execute(
                """
                INSERT INTO goal_progress (goal_id, metric_name, ts, value, note)
                VALUES ($1, 'lesson_score', $2, $3, '')
                """,
                quality.id,
                when,
                quality_value,
            )
    finally:
        await conn.close()

    conflicts = await detect_conflicts(tree, OWNER)
    antagonism = [c for c in conflicts if c.kind == "antagonistic_metrics"]
    assert antagonism, [c.kind for c in conflicts]
    conflict = antagonism[0]
    assert {conflict.left.id, conflict.right.id} == {cash.id, quality.id}
    assert "opposite" in conflict.evidence
    assert conflict.window_start is not None and conflict.window_end is not None

    # Nothing was changed by detecting it.
    for goal_id in (cash.id, quality.id):
        current = await tree.registry.get_goal(OWNER, goal_id)
        assert current is not None
        assert current.status == "active"
        assert current.priority in ("low", "medium", "high", "critical")

    # The owner is told, once per tension, through the existing notifications.
    from hermes_cli.human_comms import NotificationStore

    notifications = NotificationStore(tree.registry._store)
    await notifications.initialize()
    first = await alert_owner(notifications, OWNER, [conflict])
    assert len(first) == 1
    second = await alert_owner(notifications, OWNER, [conflict])
    assert second == first  # deduped: one tension is one notification

    # The owner's decision is recorded against both goals, and still changes
    # nothing automatically.
    await record_decision_for_pair(
        tree, OWNER, cash.id, quality.id, decision="cash wins until March"
    )
    for goal_id in (cash.id, quality.id):
        notes = [row["note"] for row in await tree.registry.list_progress(OWNER, goal_id)]
        assert any("cash wins until March" in note for note in notes)


@pytest.mark.asyncio
async def test_shared_resources_and_stated_blockage_are_detected(
    postgres_dsn: str,
) -> None:
    tree = await _fresh_tree(postgres_dsn)
    entity = await tree.registry.create_goal(
        OWNER, "Entity", visibility="shared", tier="entity"
    )
    left = await tree.registry.create_goal(
        OWNER, "Refurbish the hall", visibility="shared", tier="profile"
    )
    right = await tree.registry.create_goal(
        OWNER, "Run the summer school", visibility="shared", tier="profile"
    )
    await tree.set_parent(OWNER, left.id, entity.id)
    await tree.set_parent(OWNER, right.id, entity.id)

    # FG-09's own table, created from FG-09's own DDL: conflict detection reads
    # the resource commitments that already exist rather than inventing a
    # second way to record them.
    from hermes_cli.goal_management import _SCHEMA_SQL as GOAL_LINKS_SCHEMA

    conn = await tree.registry._connect()
    try:
        await conn.execute(GOAL_LINKS_SCHEMA)
        for goal_id in (left.id, right.id):
            await conn.execute(
                """
                INSERT INTO goal_links
                    (goal_id, resource_kind, resource_ref, owner_user_id,
                     visibility)
                VALUES ($1, 'tool', 'main-hall-booking', 'root', 'shared')
                """,
                goal_id,
            )
    finally:
        await conn.close()

    await tree.registry.record_progress(
        OWNER,
        left.id,
        note=f"blocked by {right.id}: the hall is booked all August",
    )

    kinds = {conflict.kind for conflict in await detect_conflicts(tree, OWNER)}
    assert "resource_contention" in kinds
    assert "stated_blockage" in kinds


@pytest.mark.asyncio
async def test_the_weekly_digest_gathers_both_flows(postgres_dsn: str) -> None:
    tree, promotions = await _promotions(postgres_dsn, threshold=0.1)
    await tree.registry.create_goal(
        OWNER, "Keep the school open", visibility="shared", tier="entity"
    )
    _write_skill("digest-me")
    _seed_usage("digest-me", uses=20, age_days=60)
    await promotions.propose(ADMIN, "digest-me", rationale="useful everywhere")

    later = datetime.now(timezone.utc) + timedelta(days=30)
    title, lines = await weekly_digest(tree, promotions, OWNER, now=later)
    assert title == "Weekly entity review"
    body = "\n".join(lines)
    assert "digest-me" in body
    assert "Long-lived goals with no measure" in body
