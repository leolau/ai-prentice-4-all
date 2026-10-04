"""The owner-agreed scope as prompt text (hermes_cli/projects_clarify_context)
and its use in the plan-draft / change-request prompts."""

from __future__ import annotations

import pytest

from hermes_cli import projects_api, projects_changes_api, projects_db as pdb
from hermes_cli import projects_clarify_context as cc


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROJECTS_DB", str(tmp_path / "projects.db"))
    with pdb.connect_closing() as conn:
        return pdb.create_project(conn, name="Flash cards", goal="Teach fractions")


def _round(pid, questions, *, understanding=None, done=False):
    with pdb.connect_closing() as conn:
        rn = pdb.add_clarify_round(conn, pid, questions=questions,
                                   understanding=understanding, done=done,
                                   created_by="agent")
        return rn, pdb.list_clarifications(conn, pid, round_no=rn)


def _answer(pid, items):
    with pdb.connect_closing() as conn:
        pdb.answer_clarifications(conn, pid, items, user_id="leo")


def _confirm(pid, understanding=None):
    with pdb.connect_closing() as conn:
        return pdb.confirm_clarify_round(conn, pid, user_id="leo",
                                         understanding=understanding)


def _lines(pid, **kw):
    with pdb.connect_closing() as conn:
        return cc.clarify_context_lines(conn, pid, **kw)


# ---------------------------------------------------------------------------
# clarify_context_lines
# ---------------------------------------------------------------------------


def test_nothing_yet_is_empty(db):
    assert _lines(db) == []
    _round(db, [{"question": "Who reads it?"}])
    assert _lines(db) == []  # open questions only


def test_answers_before_confirmation_are_marked_unconfirmed(db):
    _, (a, b) = _round(db, [{"question": "Who reads it?"},
                            {"question": "How many cards?"}],
                       understanding="A deck for kids")
    _answer(db, [{"id": a["id"], "answer": "Year 4 pupils"},
                 {"id": b["id"], "skip": True}])
    lines = _lines(db)
    assert lines == [cc.PENDING_HEADING, "- Who reads it? → Year 4 pupils"]
    # The agent's unconfirmed understanding is not presented as agreed.
    assert "A deck for kids" not in "\n".join(lines)
    assert not cc.has_confirmed_scope(lines)


def test_confirmed_scope_then_clarifications_skipped_omitted(db):
    _, (a, b, c) = _round(db, [{"question": "Who reads it?"},
                               {"question": "How many cards?"},
                               {"question": "Printed or digital?"}],
                          understanding="agent draft")
    _answer(db, [{"id": a["id"], "answer": "Year 4 pupils"},
                 {"id": b["id"], "skip": True}])
    _confirm(db, understanding="20 printable fraction cards for Year 4.")
    lines = _lines(db)
    assert lines == [
        cc.SCOPE_HEADING,
        "20 printable fraction cards for Year 4.",
        "",
        cc.CLARIFICATIONS_HEADING,
        "- Who reads it? → Year 4 pupils",
    ]
    text = "\n".join(lines)
    assert "How many cards" not in text  # skipped
    assert "Printed or digital" not in text  # left open → skipped on confirm
    assert cc.has_confirmed_scope(lines)


def test_newest_round_wins_and_later_answers_are_unconfirmed(db):
    _, (a, b) = _round(db, [{"question": "Who reads it?"},
                            {"question": "Deadline?"}])
    _answer(db, [{"id": a["id"], "answer": "Teachers"},
                 {"id": b["id"], "answer": "Friday"}])
    _confirm(db, understanding="Scope v1")
    _, (c, d) = _round(db, [{"question": "who reads it"},
                            {"question": "Colour scheme?"}])
    _answer(db, [{"id": c["id"], "answer": "Pupils, not teachers"},
                 {"id": d["id"], "answer": "Pastel"}])
    lines = _lines(db)
    assert lines == [
        cc.SCOPE_HEADING,
        "Scope v1",
        "",
        cc.CLARIFICATIONS_HEADING,
        "- Deadline? → Friday",
        "",
        cc.PENDING_HEADING,
        "- who reads it → Pupils, not teachers",
        "- Colour scheme? → Pastel",
    ]
    _confirm(db, understanding="Scope v2")
    lines = _lines(db)
    assert lines[:2] == [cc.SCOPE_HEADING, "Scope v2"]
    assert cc.PENDING_HEADING not in lines
    assert "Teachers" not in "\n".join(lines)


def test_confirmation_without_understanding_keeps_qa(db):
    _, (a,) = _round(db, [{"question": "Who reads it?"}])
    _answer(db, [{"id": a["id"], "answer": "Pupils"}])
    _confirm(db)
    assert _lines(db) == [cc.CLARIFICATIONS_HEADING, "- Who reads it? → Pupils"]


def test_lines_and_block_are_capped(db):
    qs = [{"question": f"Question {i} " + "q" * 400} for i in range(7)]
    _, rows = _round(db, qs)
    _answer(db, [{"id": r["id"], "answer": "a" * 1500} for r in rows])
    _confirm(db, understanding="U" * 3500)
    lines = _lines(db, max_chars=2500)
    text = "\n".join(lines)
    assert len(text) <= 2500
    assert lines[-1] == cc.OMITTED_MARKER
    for ln in lines:
        if ln.startswith("- "):
            q, a = ln[2:].split(" → ")
            assert len(q) <= cc.QUESTION_MAX_CHARS
            assert len(a) <= cc.ANSWER_MAX_CHARS
    assert len(lines[1]) <= cc.UNDERSTANDING_MAX_CHARS
    # The default cap applies too.
    full = _lines(db)
    assert len("\n".join(full)) <= cc.DEFAULT_MAX_CHARS
    assert full[-1] == cc.OMITTED_MARKER


def test_multiline_answers_become_one_line(db):
    _, (a,) = _round(db, [{"question": "Who\nreads it?"}])
    _answer(db, [{"id": a["id"], "answer": "Pupils\n\nand parents"}])
    assert _lines(db) == [cc.PENDING_HEADING, "- Who reads it? → Pupils and parents"]


# ---------------------------------------------------------------------------
# Plan draft / change-request prompts
# ---------------------------------------------------------------------------


def _project(pid):
    with pdb.connect_closing() as conn:
        return pdb.get_project(conn, pid)


OUTPUTS = [{"id": "o1", "title": "Card deck", "required": 1}]


def test_plan_prompt_unchanged_without_clarifications(db):
    project = _project(db)
    default = projects_api._plan_draft_prompt(project, OUTPUTS)
    assert default == projects_api._plan_draft_prompt(project, OUTPUTS, clarify_lines=[])
    assert default.endswith(
        f"Cadence: {project.cadence}; autonomy: {project.autonomy}."
    )
    assert "scope" not in default.lower()


def test_plan_prompt_carries_the_agreed_scope_and_must_honour_it(db):
    _, (a,) = _round(db, [{"question": "Who reads it?"}])
    _answer(db, [{"id": a["id"], "answer": "Year 4 pupils"}])
    _confirm(db, understanding="20 printable cards.")
    prompt = projects_api._plan_draft_prompt(_project(db), OUTPUTS)
    assert prompt.index("Outputs the project must deliver:") < prompt.index(
        cc.SCOPE_HEADING
    )
    assert "20 printable cards." in prompt
    assert "- Who reads it? → Year 4 pupils" in prompt
    assert "must honour the agreed scope" in prompt


def test_plan_prompt_with_only_unconfirmed_answers(db):
    _, (a,) = _round(db, [{"question": "Who reads it?"}])
    _answer(db, [{"id": a["id"], "answer": "Year 4 pupils"}])
    prompt = projects_api._plan_draft_prompt(_project(db), OUTPUTS)
    assert cc.PENDING_HEADING in prompt
    assert "must honour" not in prompt
    assert "not confirmed a scope yet" in prompt


def test_plan_prompt_explicit_lines_skip_the_store(db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("store read")

    monkeypatch.setattr(cc, "clarify_context_lines", boom)
    prompt = projects_api._plan_draft_prompt(
        _project(db), OUTPUTS, clarify_lines=[cc.SCOPE_HEADING, "Given scope"]
    )
    assert "Given scope" in prompt and "must honour" in prompt


def test_change_request_prompt_carries_the_agreed_scope(db):
    _round(db, [], understanding=None, done=True)
    _confirm(db, understanding="Only fractions, no decimals.")
    prompt = projects_changes_api._change_prompt(
        _project(db), OUTPUTS, [], None,
        {"body": "Add decimals too"}, ["requirement"],
    )
    assert "Only fractions, no decimals." in prompt
    assert prompt.index("Only fractions") < prompt.index("Add decimals too")
