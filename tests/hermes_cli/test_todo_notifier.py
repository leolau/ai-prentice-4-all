"""One to-do, one interruption — and only when it is worth having.

The notifier is two decisions with a database between them: *has this to-do
already been announced* (``notified_at``, single-winner) and *does it deserve
a push right now* (priority bar + C6 quiet hours). These tests pin both,
plus the ordering that makes a crash between them recoverable.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from hermes_cli.todo_notifier import (
    Announcement,
    announce,
    announce_pending,
    digest_lines,
    format_body,
)
from hermes_cli.todo_store import Todo

NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)


def make_todo(**overrides) -> Todo:
    fields = {
        "id": "tsk_1",
        "owner_user_id": "leo",
        "visibility": "private:leo",
        "title": "Send Ada the signed quote",
        "description": "Ada needs it before the tender closes.",
        "stage": "open",
        "status": "pending",
        "priority": "high",
        "origin": "triage",
        "current_state": "captured",
        "trigger_state": "captured",
        "completion_state": "done",
    }
    fields.update(overrides)
    return Todo(**fields)


@dataclass
class _FakeNotification:
    id: str = "ntf_1"


@dataclass
class _FakeCreateResult:
    notification: _FakeNotification
    created: bool = True
    auto_answered: bool = False
    deliver_now: bool = True


class _FakeNotifications:
    def __init__(self, deliver_now: bool = True) -> None:
        self.deliver_now = deliver_now
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCreateResult(
            _FakeNotification(f"ntf_{len(self.calls)}"),
            deliver_now=self.deliver_now,
        )


class _FakeStore:
    """A store where only the first ``mark_notified`` per to-do wins."""

    def __init__(self, pending: Optional[list] = None) -> None:
        self.pending = pending or []
        self.stamped: list[str] = []
        self.order: list[str] = []

    async def mark_notified(self, principal, todo_id, **kwargs) -> bool:
        self.order.append("mark")
        if todo_id in self.stamped:
            return False
        self.stamped.append(todo_id)
        return True

    async def pending_notification(self, principal, *, limit=50, now=None):
        return list(self.pending)


@pytest.mark.asyncio
async def test_an_open_todo_becomes_one_proactive_ask():
    store, ntf = _FakeStore(), _FakeNotifications()
    result = await announce(store, ntf, "leo", make_todo())

    assert len(ntf.calls) == 1
    call = ntf.calls[0]
    # Never an approval: the agent is not asking permission to act, it is
    # saying something is waiting. Approvals are the outgoing seam's business.
    assert call["kind"] == "proactive_ask"
    assert call["dedupe_key"] == "todo:tsk_1"
    assert call["visibility"] == "private:leo"
    assert result.notified is True
    assert result.notification_id == "ntf_1"


@pytest.mark.asyncio
async def test_the_notification_is_written_before_the_stamp():
    """A crash between the two must lose a duplicate, never the announcement.

    The notification's own dedupe_key collapses the retry; a stamp written
    first would leave a to-do marked as announced that never was.
    """
    store, ntf = _FakeStore(), _FakeNotifications()
    order: list[str] = []
    original = ntf.create

    async def _tracked(**kwargs):
        order.append("notify")
        return await original(**kwargs)

    ntf.create = _tracked
    store.order = order
    await announce(store, ntf, "leo", make_todo())
    assert order == ["notify", "mark"]


@pytest.mark.asyncio
async def test_only_the_first_caller_owns_the_announcement():
    store, ntf = _FakeStore(), _FakeNotifications()
    todo = make_todo()
    first = await announce(store, ntf, "leo", todo)
    second = await announce(store, ntf, "leo", todo)

    assert (first.notified, second.notified) == (True, False)
    assert second.should_push is False


@pytest.mark.asyncio
async def test_quiet_hours_hold_the_push_not_the_todo():
    store, ntf = _FakeStore(), _FakeNotifications(deliver_now=False)
    result = await announce(store, ntf, "leo", make_todo())

    # The row exists and the page shows it; only the interruption is withheld.
    assert result.notified is True
    assert result.should_push is False


@pytest.mark.parametrize(
    "priority,pushes",
    [("critical", True), ("high", True), ("normal", False), ("low", False)],
)
def test_only_urgent_todos_interrupt(priority, pushes):
    announcement = Announcement(
        todo_id="tsk_1",
        title="t",
        body="b",
        priority=priority,
        notified=True,
        deliver_now=True,
    )
    assert announcement.should_push is pushes


@pytest.mark.asyncio
async def test_the_sweep_announces_every_waiting_todo():
    pending = [make_todo(id=f"tsk_{i}") for i in range(3)]
    store, ntf = _FakeStore(pending), _FakeNotifications()
    results = await announce_pending(store, ntf, "leo")

    assert [r.todo_id for r in results] == ["tsk_0", "tsk_1", "tsk_2"]
    assert len(ntf.calls) == 3


@pytest.mark.asyncio
async def test_one_bad_row_does_not_lose_the_rest_of_the_sweep():
    pending = [make_todo(id="tsk_0"), make_todo(id="tsk_1")]
    store, ntf = _FakeStore(pending), _FakeNotifications()

    async def _create(**kwargs):
        if kwargs["dedupe_key"] == "todo:tsk_0":
            raise RuntimeError("notifications table is unreachable")
        return _FakeCreateResult(_FakeNotification("ntf_2"))

    ntf.create = _create
    results = await announce_pending(store, ntf, "leo")
    assert [r.todo_id for r in results] == ["tsk_1"]


def test_the_body_reads_without_the_original_message():
    body = format_body(
        make_todo(
            due_at=datetime(2026, 8, 14, 23, 59, tzinfo=timezone.utc),
            source_note="email:<abc@mail>",
        )
    )
    assert "Ada needs it" in body
    assert "Due Fri 14 Aug" in body
    assert "From email:<abc@mail>" in body


def test_a_bare_todo_still_has_a_body():
    assert format_body(make_todo(description="", source_kind=None)) == ""


def test_a_long_description_is_truncated_for_a_push():
    body = format_body(make_todo(description="x" * 900, source_kind=None))
    assert len(body) == 500


def test_the_digest_rolls_up_what_did_not_interrupt():
    todos = [
        make_todo(id=f"tsk_{i}", title=f"Thing {i}", priority="normal")
        for i in range(7)
    ]
    todos[0] = make_todo(
        id="tsk_0",
        title="Thing 0",
        due_at=NOW + timedelta(days=3),
    )
    lines = digest_lines(todos)
    assert lines[0] == "[high] Thing 0 (due 14 Aug)"
    assert lines[-1] == "...and 2 more"
    assert len(lines) == 6


def test_a_short_digest_has_no_overflow_line():
    lines = digest_lines([make_todo()])
    assert lines == ["[high] Send Ada the signed quote"]
