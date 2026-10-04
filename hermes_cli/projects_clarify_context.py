"""The owner-agreed scope as prompt text.

Turns the scope-clarification store (``project_clarify_rounds`` /
``project_clarifications``) into bounded lines for the plan draft, each
run's guidance block, change-request drafts and the Ask snapshot.

* The latest **confirmed** understanding comes first, under
  "Agreed scope (confirmed by the owner)".
* Answered questions from rounds up to that confirmation follow under
  "Clarifications"; answers from later (or, before any confirmation, all)
  rounds go under "Owner's answers so far (not yet confirmed)".
* Skipped and open questions are omitted; when the same question was asked
  in several rounds the newest answer wins.
* Everything is read-only and built when a prompt is compiled, so a running
  conversation is never mutated.
"""

from __future__ import annotations

import sqlite3
from typing import Any, List, Optional

from hermes_cli import projects_db

SCOPE_HEADING = "### Agreed scope (confirmed by the owner)"
CLARIFICATIONS_HEADING = "### Clarifications"
PENDING_HEADING = "### Owner's answers so far (not yet confirmed)"
OMITTED_MARKER = "…[further clarifications omitted]"

DEFAULT_MAX_CHARS = 3_000
UNDERSTANDING_MAX_CHARS = 1_500
QUESTION_MAX_CHARS = 300
ANSWER_MAX_CHARS = 600


def _one_line(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def _bounded(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def _question_key(text: str) -> str:
    return " ".join(str(text or "").lower().split()).rstrip("?？.。 ")


def clarify_context(conn: sqlite3.Connection, project_id: str) -> dict:
    """The agreed scope as data.

    ``{"understanding", "round_no", "confirmed_at", "confirmed": [pair],
    "pending": [pair]}`` where a pair is ``{"round_no", "question",
    "answer"}``, oldest first. ``round_no``/``understanding`` are ``None``
    before any confirmation."""
    rounds = projects_db.list_clarify_rounds(conn, project_id)
    confirmed = [r for r in rounds if r["status"] == "confirmed"]
    latest = confirmed[-1] if confirmed else None
    cutoff = int(latest["round_no"]) if latest else 0

    newest: dict[str, dict] = {}
    for q in projects_db.list_clarifications(conn, project_id):
        if q.get("status") != "answered" or not str(q.get("answer") or "").strip():
            continue
        key = _question_key(q.get("question") or "")
        if not key:
            continue
        # list_clarifications is oldest-round-first: a later entry wins.
        newest.pop(key, None)
        newest[key] = {
            "round_no": int(q["round_no"]),
            "question": str(q["question"]),
            "answer": str(q["answer"]),
        }
    pairs = sorted(newest.values(), key=lambda p: p["round_no"])
    understanding = (
        (str(latest.get("understanding") or "").strip() or None) if latest else None
    )
    return {
        "understanding": understanding,
        "round_no": cutoff or None,
        "confirmed_at": latest.get("confirmed_at") if latest else None,
        "confirmed": [p for p in pairs if p["round_no"] <= cutoff],
        "pending": [p for p in pairs if p["round_no"] > cutoff],
    }


def pair_line(pair: dict) -> str:
    q = _one_line(pair["question"], QUESTION_MAX_CHARS)
    a = _one_line(pair["answer"], ANSWER_MAX_CHARS)
    return f"- {q} → {a}"


def render_lines(ctx: dict, *, max_chars: int = DEFAULT_MAX_CHARS) -> List[str]:
    """Render :func:`clarify_context` data; ``[]`` when there is nothing."""
    head: List[str] = []
    if ctx.get("understanding"):
        room = max(80, min(UNDERSTANDING_MAX_CHARS, max_chars - len(SCOPE_HEADING) - 1))
        head += [SCOPE_HEADING, _bounded(ctx["understanding"], room)]
    groups = [
        (CLARIFICATIONS_HEADING, ctx.get("confirmed") or []),
        (PENDING_HEADING, ctx.get("pending") or []),
    ]
    if not head and not any(pairs for _, pairs in groups):
        return []

    lines = list(head)
    total = len("\n".join(lines))
    omitted = False
    for heading, pairs in groups:
        if not pairs or omitted:
            continue
        section = (["", heading] if lines else [heading])
        added = False
        for pair in pairs:
            line = pair_line(pair)
            extra = [*section, line] if not added else [line]
            cost = len("\n".join(extra)) + (1 if lines else 0)
            reserve = len(OMITTED_MARKER) + 1
            if total + cost + reserve > max_chars:
                omitted = True
                if not lines:
                    lines.append(heading)
                break
            lines += extra
            total = len("\n".join(lines))
            added = True
    if omitted:
        lines.append(OMITTED_MARKER)
    return lines


def clarify_context_lines(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> List[str]:
    """The agreed scope + clarifications as prompt lines (``[]`` if none).

    The block is at most ``max_chars`` characters; Q→A lines that do not
    fit are replaced by :data:`OMITTED_MARKER`."""
    return render_lines(clarify_context(conn, project_id), max_chars=max_chars)


def has_confirmed_scope(lines: Optional[List[str]]) -> bool:
    """Whether rendered lines carry an owner-confirmed section."""
    lines = lines or []
    return SCOPE_HEADING in lines or CLARIFICATIONS_HEADING in lines
