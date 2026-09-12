"""Projects-side reconciliation triggered by kanban card settlement (FG-32).

``projects_run.promote_run_cards()`` — the only thing that moves a run's
``triage`` cards into ``todo`` under the project's ``max_in_progress`` cap
(§4.1) — is otherwise called from exactly two places: ``start_run()`` and
the human ``continue_run()`` action. Nothing re-invokes it when a card the
run is waiting on finishes, fails, or blocks and a slot frees up, so a run
whose cap is smaller than its playbook (the default, ``max_in_progress=1``)
promotes its first batch and then stalls forever with no human action —
independent of any process restart.

``on_card_settled()`` is the fix: called from ``kanban_db`` right after a
run-linked card leaves ``running``/``ready`` (done, blocked, or a
dependency wait), it looks up the owning run and, if still ``running``,
tops up its promoted cards via ``projects_run.refill_run_cards()`` — the
same promotion the run started with, minus forcing past a held checkpoint,
which stays ``continue_run()``'s human act.

This module is imported lazily from ``kanban_db.py`` (never at module load
time) so the generic kanban store keeps knowing nothing about Projects
beyond "does this task carry a ``project_id``" — the same layering the rest
of FG-32 already holds to (``kanban_db`` is the shared dispatch substrate;
Projects is a consumer, never a patch to it).
"""

from __future__ import annotations

import logging
from typing import List

from hermes_cli import projects_db

log = logging.getLogger(__name__)


def on_card_settled(bconn, task_id: str) -> List[str]:
    """Best-effort: top up the owning run's promoted cards, if any.

    Returns the task ids promoted (empty when the card is not
    project-linked, its run is not ``running``, or there is nothing left to
    promote). Never raises — callers are board state transitions that must
    not fail because Projects reconciliation did.
    """
    try:
        return _on_card_settled(bconn, task_id)
    except Exception:  # noqa: BLE001 — must never break a board transition
        log.warning(
            "projects: reconcile-on-settle failed for card %s", task_id,
            exc_info=True,
        )
        return []


def _on_card_settled(bconn, task_id: str) -> List[str]:
    from hermes_cli import projects_run

    with projects_db.connect_closing() as pconn:
        run_card = projects_db.get_run_card_by_task(pconn, task_id)
        if run_card is None:
            return []
        run = projects_db.get_project_run_by_id(pconn, run_card["run_id"])
        if run is None or run.get("status") != "running":
            return []
        project = projects_db.get_project(pconn, run["project_id"])
        if project is None:
            return []
        return projects_run.refill_run_cards(
            pconn, bconn, project=project, run=run
        )
