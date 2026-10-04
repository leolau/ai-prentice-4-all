"""Store-level tests for scope clarification rounds (hermes_cli/projects_db)."""

from __future__ import annotations

import pytest

from hermes_cli import projects_db as pdb


@pytest.fixture
def conn(tmp_path):
    c = pdb.connect(db_path=tmp_path / "projects.db")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def pid(conn):
    return pdb.create_project(conn, name="Clarify me")


Q = [
    {"question": "Who reads it?", "why": "tone", "category": "audience",
     "options": ["Teachers", "Parents", " "]},
    {"question": "What does done mean?", "category": "nonsense", "allow_multiple": True},
    {"question": "   "},
]


def test_empty_project_summary_is_not_started(conn, pid):
    s = pdb.clarify_summary(conn, pid)
    assert s["status"] == "not_started"
    assert s["round"] == 0 and s["understanding"] is None


def test_add_round_cleans_questions(conn, pid):
    rn = pdb.add_clarify_round(conn, pid, questions=Q, understanding="A card set",
                               done=False, created_by="u1", focus="audience")
    assert rn == 1
    qs = pdb.list_clarifications(conn, pid)
    assert [q["question"] for q in qs] == ["Who reads it?", "What does done mean?"]
    assert qs[0]["options"] == ["Teachers", "Parents"]
    assert qs[1]["category"] == "other" and qs[1]["allow_multiple"] is True
    assert all(q["status"] == "open" for q in qs)
    r = pdb.list_clarify_rounds(conn, pid)[0]
    assert r["status"] == "open" and r["focus"] == "audience" and r["done"] is False
    s = pdb.clarify_summary(conn, pid)
    assert s == {"status": "open", "round": 1, "open_count": 2, "answered_count": 0,
                 "understanding": None, "confirmed_at": None}


def test_caps_question_count(conn, pid):
    many = [{"question": f"q{i}"} for i in range(20)]
    pdb.add_clarify_round(conn, pid, questions=many, understanding=None, done=False,
                          created_by="u1")
    assert len(pdb.list_clarifications(conn, pid)) == pdb.CLARIFY_MAX_QUESTIONS


def test_round_without_questions_is_answered(conn, pid):
    pdb.add_clarify_round(conn, pid, questions=[], understanding="clear", done=True,
                          created_by="u1")
    r = pdb.list_clarify_rounds(conn, pid)[0]
    assert r["status"] == "answered" and r["done"] is True


def test_answer_skip_and_round_status(conn, pid):
    pdb.add_clarify_round(conn, pid, questions=Q, understanding=None, done=False,
                          created_by="u1")
    a, b = pdb.list_clarifications(conn, pid)
    assert pdb.answer_clarifications(conn, pid, [{"id": a["id"], "answer": " Teachers "}],
                                     user_id="u2") == 1
    assert pdb.clarify_summary(conn, pid)["status"] == "open"
    pdb.answer_clarifications(conn, pid, [{"id": b["id"], "skip": True}], user_id="u2")
    qa, qb = pdb.list_clarifications(conn, pid)
    assert qa["answer"] == "Teachers" and qa["answered_by"] == "u2"
    assert qb["status"] == "skipped" and qb["answer"] is None
    assert pdb.clarify_summary(conn, pid)["status"] == "answered"
    # An answer can be changed before the round is confirmed.
    pdb.answer_clarifications(conn, pid, [{"id": a["id"], "answer": "Parents"}], user_id="u2")
    assert pdb.list_clarifications(conn, pid)[0]["answer"] == "Parents"


def test_answer_validation(conn, pid):
    pdb.add_clarify_round(conn, pid, questions=Q, understanding=None, done=False,
                          created_by="u1")
    a = pdb.list_clarifications(conn, pid)[0]
    with pytest.raises(KeyError):
        pdb.answer_clarifications(conn, pid, [{"id": "clq_nope", "answer": "x"}], user_id="u")
    with pytest.raises(ValueError):
        pdb.answer_clarifications(conn, pid, [{"id": a["id"], "answer": "  "}], user_id="u")
    # A failed batch writes nothing.
    assert pdb.list_clarifications(conn, pid)[0]["status"] == "open"


def test_other_projects_questions_are_unknown(conn, pid):
    other_id = pdb.create_project(conn, name="Other one")
    pdb.add_clarify_round(conn, other_id, questions=Q, understanding=None, done=False,
                          created_by="u1")
    q = pdb.list_clarifications(conn, other_id)[0]
    with pytest.raises(KeyError):
        pdb.answer_clarifications(conn, pid, [{"id": q["id"], "answer": "x"}], user_id="u")


def test_confirm_skips_open_and_locks_round(conn, pid):
    assert pdb.confirm_clarify_round(conn, pid, user_id="u") is None
    pdb.add_clarify_round(conn, pid, questions=Q, understanding="draft", done=False,
                          created_by="u1")
    a, b = pdb.list_clarifications(conn, pid)
    pdb.answer_clarifications(conn, pid, [{"id": a["id"], "answer": "Teachers"}], user_id="u")
    r = pdb.confirm_clarify_round(conn, pid, user_id="u3", understanding="Edited summary")
    assert r["status"] == "confirmed" and r["understanding"] == "Edited summary"
    assert r["confirmed_by"] == "u3"
    assert pdb.list_clarifications(conn, pid)[1]["status"] == "skipped"
    with pytest.raises(ValueError):
        pdb.answer_clarifications(conn, pid, [{"id": a["id"], "answer": "x"}], user_id="u")
    s = pdb.clarify_summary(conn, pid)
    assert s["status"] == "confirmed" and s["understanding"] == "Edited summary"


def test_confirm_keeps_agent_understanding_when_not_edited(conn, pid):
    pdb.add_clarify_round(conn, pid, questions=[], understanding="agent says", done=True,
                          created_by="u1")
    assert pdb.confirm_clarify_round(conn, pid, user_id="u")["understanding"] == "agent says"


def test_new_round_after_confirm_keeps_last_confirmed_understanding(conn, pid):
    pdb.add_clarify_round(conn, pid, questions=[], understanding="v1", done=True,
                          created_by="u1")
    pdb.confirm_clarify_round(conn, pid, user_id="u")
    rn = pdb.add_clarify_round(conn, pid, questions=Q, understanding="v2?", done=False,
                               created_by="u1")
    assert rn == 2
    s = pdb.clarify_summary(conn, pid)
    assert s["status"] == "open" and s["round"] == 2 and s["understanding"] == "v1"
    assert [q["round_no"] for q in pdb.list_clarifications(conn, pid, round_no=2)] == [2, 2]


def test_rounds_cascade_with_project(conn, pid):
    pdb.add_clarify_round(conn, pid, questions=Q, understanding=None, done=False,
                          created_by="u1")
    conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
    conn.commit()
    assert pdb.list_clarifications(conn, pid) == []
    assert pdb.list_clarify_rounds(conn, pid) == []
