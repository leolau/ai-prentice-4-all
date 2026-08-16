"""The Projects run engine (design §4–§7 / §17 step 4).

Behaviour contracts, not change detectors:

- the playbook is validated **at save time** with offending keys named
  (§7.1), and activation is a separate human crossing (§7.2);
- autonomy is enforced in the project's own promotion step: ``manual``
  never promotes, ``supervised`` holds checkpoint successors,
  ``autonomous`` holds nothing (§4);
- ``max_in_progress`` caps promotion by counting running + ready (§4);
- guidance compiles once, at spawn, outputs before instructions, capped,
  attributed, empty sections omitted with their headings (§5.2);
- toolsets/skills are a narrowing filter — intersection, drops recorded
  on the run, never a grant (§4.1);
- the run outcome is derived from deliveries: delivered / partial naming
  the missing / no_output (§6.1), and cost is read fail-open (§6).
"""

from __future__ import annotations

import pytest

from hermes_cli import kanban_db, projects_db, projects_run

GUIDE_CFG = {
    "max_skills": 5,
    "guidance_max_directives": 20,
    "guidance_max_chars": 4000,
    "brief_max_chars": 1200,
}


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Isolated projects + kanban stores with deterministic host seams."""
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.setattr(
        projects_run, "_enabled_toolsets_for_profile",
        lambda profile: ["research", "web"],
    )
    monkeypatch.setattr(
        projects_run, "_available_skill_names", lambda: ["digest", "email"]
    )
    monkeypatch.setattr(
        projects_run, "projects_runtime_config", lambda: dict(GUIDE_CFG)
    )
    return tmp_path


def _make_project(*, autonomy="supervised", toolsets=None, skills=None,
                  budget=None, max_in_progress=1):
    """A fully-mandatory, ACTIVE project with one host profile."""
    with projects_db.connect_closing() as conn:
        pid = projects_db.create_full_project(
            conn,
            goal="Ship the Monday digest — to every subscriber",
            description="A weekly digest compiled and emailed each Monday.",
            owner_user_id="leo",
            cadence="repeatable",
            autonomy=autonomy,
        )
        projects_db.add_project_profile(
            conn, project_id=pid, profile="default", role="host"
        )
        projects_db.add_project_member(
            conn, project_id=pid, user_id="leo", role="lead"
        )
        oid = projects_db.add_project_output(
            conn, project_id=pid, title="The Monday digest email",
            spec="html email to the list", required=True,
        )
        updates = {}
        if toolsets is not None:
            updates["toolsets"] = ",".join(toolsets)
        if skills is not None:
            updates["skills"] = ",".join(skills)
        if budget is not None:
            updates["budget_usd_per_run"] = budget
        if max_in_progress != 1:
            updates["max_in_progress"] = max_in_progress
        if updates:
            projects_db.update_project_fields(conn, pid, updates)
        projects_db.set_project_status(conn, pid, "active")
        project = projects_db.get_project(conn, pid)
    return project, oid


STEPS = [
    {"key": "gather", "title": "Collect arrivals", "mode": "card",
     "assignee": "default"},
    {"key": "draft", "title": "Draft summary", "mode": "card",
     "depends_on": ["gather"]},
    {"key": "approve", "title": "Owner reviews", "mode": "card",
     "depends_on": ["draft"], "checkpoint": True},
    {"key": "send", "title": "Send to the list", "mode": "card",
     "depends_on": ["approve"]},
]


def _save_playbook(pid, steps=None):
    with projects_db.connect_closing() as conn:
        return projects_db.save_playbook_rev(
            conn, project_id=pid, body="The weekly method",
            steps=steps if steps is not None else STEPS,
            created_by="leo",
        )


def _start(pid, **kwargs):
    with projects_db.connect_closing() as conn:
        with kanban_db.connect_closing() as bconn:
            project = projects_db.get_project(conn, pid)
            return projects_run.start_run(
                conn, bconn, project=project,
                triggered_by="leo", **kwargs,
            )


# ---------------------------------------------------------------------------
# §7.1 — validation at save time, offending keys named
# ---------------------------------------------------------------------------


def test_playbook_cycle_refused_at_save_naming_the_keys(stores):
    project, _ = _make_project()
    cyclic = [
        {"key": "a", "title": "A", "depends_on": ["b"]},
        {"key": "b", "title": "B", "depends_on": ["a"]},
    ]
    with pytest.raises(ValueError) as exc:
        _save_playbook(project.id, cyclic)
    # The offending keys are named on the cycle path.
    assert "a -> b -> a" in str(exc.value)
    assert "cycle" in str(exc.value)


def test_playbook_dangling_dependency_refused_naming_the_key(stores):
    project, _ = _make_project()
    steps = [{"key": "a", "title": "A", "depends_on": ["ghost"]}]
    with pytest.raises(ValueError) as exc:
        _save_playbook(project.id, steps)
    assert "ghost" in str(exc.value) and "'a'" in str(exc.value)


def test_playbook_unknown_mode_and_duplicate_keys_refused(stores):
    project, _ = _make_project()
    with pytest.raises(ValueError, match="mode"):
        _save_playbook(project.id, [{"key": "a", "title": "A", "mode": "agent"}])
    with pytest.raises(ValueError, match="duplicate"):
        _save_playbook(
            project.id,
            [{"key": "a", "title": "A"}, {"key": "a", "title": "A again"}],
        )


def test_playbook_revisions_are_proposals_until_activated(stores):
    project, _ = _make_project()
    rev1 = _save_playbook(project.id)
    rev2 = _save_playbook(project.id, [{"key": "x", "title": "X"}])
    assert (rev1, rev2) == (1, 2)
    with projects_db.connect_closing() as conn:
        assert projects_db.get_playbook(conn, project.id) is None  # none active
        assert projects_db.activate_playbook_rev(conn, project.id, rev2)
        active = projects_db.get_playbook(conn, project.id)
        assert active["rev"] == 2
        # Activating a different rev moves the flag — exactly one active.
        assert projects_db.activate_playbook_rev(conn, project.id, rev1)
        assert projects_db.get_playbook(conn, project.id)["rev"] == 1
        assert not projects_db.activate_playbook_rev(conn, project.id, 99)


# ---------------------------------------------------------------------------
# §7 — instantiation: cards, parent links, run mapping
# ---------------------------------------------------------------------------


def test_run_instantiates_cards_with_parent_links_and_mapping(stores):
    project, _ = _make_project(max_in_progress=4)
    _save_playbook(project.id)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    run = result["run"]
    assert run["run_no"] == 1
    assert run["status"] == "running"
    assert run["playbook_rev"] == 1
    assert set(result["cards"]) == {"gather", "draft", "approve", "send"}

    with kanban_db.connect_closing() as bconn:
        gather = result["cards"]["gather"]
        draft = result["cards"]["draft"]
        assert kanban_db.parent_ids(bconn, draft) == [gather]
        # Nothing is CREATED ready (§7.1); a parent-free todo may be
        # promoted to ready by the board's own recompute_ready — that is
        # the shipped engine doing its job, not the run.
        statuses = {
            tid: kanban_db.get_task(bconn, tid).status
            for tid in result["cards"].values()
        }
    assert statuses[gather] in ("todo", "ready")
    assert statuses[draft] == "todo"  # held by its parent link
    assert statuses[result["cards"]["approve"]] == "todo"
    # `send` succeeds a checkpoint: held in triage until a human continues.
    assert statuses[result["cards"]["send"]] == "triage"

    with projects_db.connect_closing() as conn:
        rc = projects_db.get_run_cards(conn, run["id"])
    assert {r["step_key"] for r in rc} == set(result["cards"])


def test_run_pins_its_playbook_rev_across_activation(stores):
    """'Repeat this run' = the run's own rev, not the active one (§7.2)."""
    project, _ = _make_project()
    _save_playbook(project.id)
    _save_playbook(project.id, [{"key": "only", "title": "The new method"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 2)
    result = _start(project.id, playbook_rev=1)  # repeat the OLD method
    assert set(result["cards"]) == {"gather", "draft", "approve", "send"}
    assert result["run"]["playbook_rev"] == 1


# ---------------------------------------------------------------------------
# §4 — autonomy: manual never promotes; max_in_progress caps
# ---------------------------------------------------------------------------


def test_manual_project_never_promotes_a_card(stores):
    project, _ = _make_project(autonomy="manual")
    _save_playbook(project.id)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    assert result["promoted"] == []
    with kanban_db.connect_closing() as bconn:
        for tid in result["cards"].values():
            assert kanban_db.get_task(bconn, tid).status == "triage"


def test_autonomous_project_holds_nothing(stores):
    project, _ = _make_project(autonomy="autonomous", max_in_progress=4)
    _save_playbook(project.id)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    with kanban_db.connect_closing() as bconn:
        statuses = {
            k: kanban_db.get_task(bconn, tid).status
            for k, tid in result["cards"].items()
        }
    # Even the checkpoint's successor moves — autonomous has no checkpoints.
    assert statuses["send"] == "todo"


def test_continue_releases_checkpoint_successors(stores):
    project, _ = _make_project(max_in_progress=4)
    _save_playbook(project.id)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    run = result["run"]
    with projects_db.connect_closing() as conn:
        with kanban_db.connect_closing() as bconn:
            fresh = projects_db.get_project(conn, project.id)
            out = projects_run.continue_run(
                conn, bconn, project=fresh, run=run
            )
    assert result["cards"]["send"] in out["promoted"]
    with kanban_db.connect_closing() as bconn:
        assert kanban_db.get_task(bconn, result["cards"]["send"]).status == "todo"


def test_max_in_progress_caps_promotion(stores):
    project, _ = _make_project(autonomy="autonomous", max_in_progress=1)
    steps = [{"key": f"s{i}", "title": f"Step {i}"} for i in range(3)]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
        # One card already running for the project: the cap is full.
        with kanban_db.connect_closing() as bconn:
            busy = kanban_db.create_task(
                bconn, title="busy", created_by="leo", triage=True,
                project_id=project.id, visibility="shared",
            )
            kanban_db.specify_triage_task(bconn, busy)
            bconn.execute("UPDATE tasks SET status = 'running' WHERE id = ?",
                          (busy,))
    result = _start(project.id)
    assert result["promoted"] == []  # cap: running+ready already at 1
    with kanban_db.connect_closing() as bconn:
        for tid in result["cards"].values():
            assert kanban_db.get_task(bconn, tid).status == "triage"


# ---------------------------------------------------------------------------
# §4.1 — narrowing filter, never a grant
# ---------------------------------------------------------------------------


def test_toolset_intersection_records_drops_on_the_run(stores):
    project, _ = _make_project(
        toolsets=["research", "terminal", "calendar"], skills=["digest", "ghost"]
    )
    _save_playbook(project.id, [{"key": "one", "title": "One"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    # host profile enables research+web: terminal/calendar cannot be granted.
    assert result["toolsets_effective"] == ["research"]
    assert result["toolsets_dropped"] == ["terminal", "calendar"]
    assert result["skills_effective"] == ["digest"]
    assert result["skills_dropped"] == ["ghost"]
    # A silently narrower run looks like a broken agent — the drop is
    # recorded on the run, in the summary prelude.
    assert "terminal" in result["run"]["summary"]
    assert "dropped" in result["run"]["summary"]


def test_empty_instruments_mean_whatever_the_host_does(stores):
    project, _ = _make_project()
    _save_playbook(project.id, [{"key": "one", "title": "One"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    assert result["toolsets_effective"] == ["research", "web"]
    assert result["toolsets_dropped"] == []
    assert result["run"]["summary"] in (None, "")


# ---------------------------------------------------------------------------
# §5 — guidance: compiled at spawn, outputs first, capped, attributed
# ---------------------------------------------------------------------------


def test_guidance_block_fixed_order_and_omitted_sections(stores):
    project, oid = _make_project()
    with projects_db.connect_closing() as conn:
        projects_db.add_project_directive(
            conn, project_id=project.id, kind="directive",
            body="Always cc legal", author_user_id="leo",
        )
        projects_db.add_project_link(
            conn, project_id=project.id, kind="sample", profile="default",
            ref="~/samples/digest-eml.md", label="last week's digest",
        )
        # a delivered output renders checked with its run
        projects_db.record_output_delivery(
            conn, output_id=oid, run_id=None, label="manual attach"
        )
    guidance = _compile(project, oid)
    assert guidance.startswith("## Project: Ship the Monday digest")
    assert "Run 1 · repeatable · autonomy: supervised" in guidance
    assert "### Outputs expected of this run" in guidance
    assert "### Standing instructions (newest first)" in guidance
    assert "1. Always cc legal [leo," in guidance
    assert "### Samples to match" in guidance
    assert "- last week's digest → ~/samples/digest-eml.md" in guidance
    # Outputs come before instructions — fixed order (§5.2).
    assert guidance.index("### Outputs") < guidance.index(
        "### Standing instructions"
    )
    # Empty sections are omitted WITH their headings — never 'None'.
    assert "### Audience" not in guidance
    assert "### What we learnt last run" not in guidance
    assert "None" not in guidance


def _compile(project, oid=None):
    with projects_db.connect_closing() as conn:
        outputs = projects_db.get_project_outputs(conn, project.id)
        directives = projects_db.list_project_directives(conn, project.id)
        deliveries = {
            d["output_id"]: [d]
            for d in projects_db.get_output_deliveries(
                conn, project_id=project.id
            )
        }
        samples = [
            link for link in projects_db.get_project_links(conn, project.id)
            if link["kind"] == "sample"
        ]
    return projects_run.compile_guidance(
        project,
        run_no=1,
        outputs=outputs,
        deliveries_by_output=deliveries,
        sample_links=samples,
        directives=directives,
        cfg=GUIDE_CFG,
    )


def test_directive_cap_refuses_the_21st_with_retire_one_first(stores):
    project, _ = _make_project()
    with projects_db.connect_closing() as conn:
        for i in range(20):
            projects_db.add_project_directive(
                conn, project_id=project.id, kind="directive",
                body=f"instruction {i}", author_user_id="leo",
            )
        with pytest.raises(ValueError, match="retire one first"):
            projects_db.add_project_directive(
                conn, project_id=project.id, kind="directive",
                body="one too many", author_user_id="leo",
            )


def test_retire_keeps_the_record_and_frees_a_slot(stores):
    project, _ = _make_project()
    with projects_db.connect_closing() as conn:
        did = projects_db.add_project_directive(
            conn, project_id=project.id, kind="directive",
            body="never touch prod", author_user_id="leo",
        )
        assert projects_db.retire_project_directive(conn, did)
        active = projects_db.list_project_directives(conn, project.id)
        all_rows = projects_db.list_project_directives(
            conn, project.id, active_only=False
        )
    assert active == []          # retired directives never compile
    assert len(all_rows) == 1    # but the record survives (§5.2)
    assert all_rows[0]["active"] == 0 and all_rows[0]["retired_at"]


def test_guidance_directives_are_capped_and_newest_first(stores):
    project, _ = _make_project()
    cfg = dict(GUIDE_CFG)
    cfg["guidance_max_directives"] = 2
    with projects_db.connect_closing() as conn:
        for i in range(3):
            projects_db.add_project_directive(
                conn, project_id=project.id, kind="directive",
                body=f"instruction {i}", author_user_id="leo",
            )
        directives = projects_db.list_project_directives(conn, project.id)
    block = projects_run.compile_guidance(
        project, run_no=1, outputs=[], deliveries_by_output={},
        sample_links=[], directives=directives, cfg=cfg,
    )
    assert "instruction 2" in block  # newest first, both kept
    assert "instruction 1" in block
    assert "instruction 0" not in block  # the oldest is cut at the cap
    assert block.index("instruction 2") < block.index("instruction 1")


def test_guidance_applies_at_spawn_not_mid_conversation(stores):
    """§5.1: a directive added while a run is in flight is in the NEXT
    run's block, never the current one — the block is compiled once."""
    project, _ = _make_project()
    _save_playbook(project.id, [{"key": "one", "title": "One"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    first = _start(project.id)
    assert "Always cc legal" not in first["guidance"]
    with projects_db.connect_closing() as conn:
        projects_db.add_project_directive(
            conn, project_id=project.id, kind="directive",
            body="Always cc legal", author_user_id="leo",
        )
    # The running run's compiled block is unchanged (frozen at spawn)…
    assert "Always cc legal" not in first["guidance"]
    # …and the NEXT run carries it.
    second = _start(project.id)
    assert "Always cc legal" in second["guidance"]
    assert second["run"]["run_no"] == 2


def test_budget_gate_stops_the_run_into_waiting(stores):
    project, _ = _make_project(budget=5.0)
    _save_playbook(project.id, [{"key": "one", "title": "One"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id, cost_reader=lambda trace: 12.5)
    assert result["budget_gate"] is not None
    assert "$12.50" in result["budget_gate"]
    assert result["run"]["status"] == "waiting"
    # Under budget → the run keeps going.
    project2, _ = _make_project(budget=50.0)
    _save_playbook(project2.id, [{"key": "one", "title": "One"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project2.id, 1)
    ok = _start(project2.id, cost_reader=lambda trace: 12.5)
    assert ok["budget_gate"] is None
    assert ok["run"]["status"] == "running"


def test_inline_steps_spawn_one_seeded_session(stores):
    project, _ = _make_project()
    steps = [
        {"key": "read", "title": "Read sources", "mode": "inline",
         "body": "read three sources"},
        {"key": "post", "title": "Post it", "mode": "card"},
    ]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    calls = []

    def fake_spawn(*, project, run, guidance, inline_steps, enabled_toolsets):
        calls.append((guidance, [s["key"] for s in inline_steps]))
        return {"session_id": "sess-1", "error": None}

    result = _start(project.id, spawn_inline=fake_spawn)
    assert len(calls) == 1  # ONE seeded session for the run itself (§6)
    assert calls[0][1] == ["read"]
    assert "## Project:" in calls[0][0]  # guidance seeds the session
    assert result["run"]["session_id"] == "sess-1"
    assert set(result["cards"]) == {"post"}  # only card-mode steps are cards


# ---------------------------------------------------------------------------
# §6.1 — outcome from deliveries; cost fail-open; cancel semantics
# ---------------------------------------------------------------------------


def test_outcome_delivered_partial_and_no_output(stores):
    project, oid = _make_project()
    with projects_db.connect_closing() as conn:
        oid2 = projects_db.add_project_output(
            conn, project_id=project.id, title="The archive copy",
            required=True,
        )
    _save_playbook(project.id, [{"key": "one", "title": "One"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    run = result["run"]

    # Nothing delivered yet → no_output.
    with projects_db.connect_closing() as conn:
        status, outcome = projects_run.derive_run_outcome(
            conn, project_id=project.id, run_id=run["id"]
        )
    assert status == "no_output"

    # One of two → partial, naming what is missing.
    with projects_db.connect_closing() as conn:
        projects_db.record_output_delivery(
            conn, output_id=oid, run_id=run["id"], label="email sent"
        )
        status, outcome = projects_run.derive_run_outcome(
            conn, project_id=project.id, run_id=run["id"]
        )
    assert status == "partial"
    assert "The archive copy" in outcome

    # Both → delivered.
    with projects_db.connect_closing() as conn:
        projects_db.record_output_delivery(
            conn, output_id=oid2, run_id=run["id"], label="archived"
        )
        status, outcome = projects_run.derive_run_outcome(
            conn, project_id=project.id, run_id=run["id"]
        )
    assert status == "delivered"
    assert "2" in outcome


def test_close_run_derives_outcome_and_keeps_the_prelude(stores):
    project, oid = _make_project(toolsets=["terminal"])  # → recorded drop
    _save_playbook(project.id, [{"key": "one", "title": "One"}])
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    run = result["run"]
    with projects_db.connect_closing() as conn:
        projects_db.record_output_delivery(
            conn, output_id=oid, run_id=run["id"], label="email sent"
        )
        closed = projects_run.close_run(
            conn, run=run, summary="Went well; digest sent at 09:02."
        )
    assert closed["status"] == "done"
    assert "delivered" in closed["outcome"]
    assert closed["ended_at"] is not None
    # The spawn-time drop prelude survives the agent's summary.
    assert "terminal" in closed["summary"]
    assert "Went well" in closed["summary"]


def test_cancel_archives_unstarted_cards_and_never_kills_running(stores):
    project, _ = _make_project(autonomy="autonomous")
    steps = [{"key": "a", "title": "A"}, {"key": "b", "title": "B"}]
    _save_playbook(project.id, steps)
    with projects_db.connect_closing() as conn:
        projects_db.activate_playbook_rev(conn, project.id, 1)
    result = _start(project.id)
    run = result["run"]
    with kanban_db.connect_closing() as bconn:
        # Simulate a worker mid-flight on card A.
        bconn.execute(
            "UPDATE tasks SET status = 'running' WHERE id = ?",
            (result["cards"]["a"],),
        )
    with projects_db.connect_closing() as conn:
        with kanban_db.connect_closing() as bconn:
            fresh = projects_db.get_project(conn, project.id)
            closed = projects_run.cancel_run(conn, bconn, project=fresh, run=run)
        with kanban_db.connect_closing() as bconn:
            a = kanban_db.get_task(bconn, result["cards"]["a"])
            b = kanban_db.get_task(bconn, result["cards"]["b"])
    assert closed["status"] == "cancelled"
    assert a.status == "running"      # never killed (§12)
    assert b.status == "archived"     # un-started card archived
    assert "1 card(s) left running" in closed["outcome"]


def test_run_cost_is_fail_open(stores):
    assert projects_run.run_cost(None) is None
    assert projects_run.run_cost("t-1") is None  # no ledger configured
    assert projects_run.run_cost("t-1", reader=lambda t: 3.5) == 3.5
    assert projects_run.run_cost("t-1", reader=lambda t: 1 / 0) is None


def test_run_requires_an_active_project_and_a_playbook(stores):
    project, _ = _make_project()
    with projects_db.connect_closing() as conn:
        projects_db.set_project_status(conn, project.id, "archived")
        archived = projects_db.get_project(conn, project.id)
        with kanban_db.connect_closing() as bconn:
            with pytest.raises(ValueError, match="archived"):
                projects_run.start_run(conn, bconn, project=archived)
    project2, _ = _make_project()  # active, no playbook
    with projects_db.connect_closing() as conn:
        with kanban_db.connect_closing() as bconn:
            fresh = projects_db.get_project(conn, project2.id)
            with pytest.raises(ValueError, match="no playbook"):
                projects_run.start_run(conn, bconn, project=fresh)
