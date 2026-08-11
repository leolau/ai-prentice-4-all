"""The outgoing seam: propose, never send.

The module is small, so what is worth pinning is the three properties the whole
design rests on: the approval is **irreversible** (so C6's standing consent can
never answer it), the route is taken from the arrival rather than from the
caller (contract C4), and a to-do is never left un-finished because its draft
was bad.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from hermes_cli import todos_api
from hermes_cli.access import Principal
from hermes_cli.todo_outbound import (
    OutboundError,
    ProposedAction,
    command_for,
    parse_action,
    propose,
    target_from_arrival,
)
from hermes_cli.todo_store import Todo

PRINCIPAL = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]

ARRIVAL = {
    "id": "inb_1",
    "surface": "whatsapp",
    "account_id": "+85211112222",
    "conversation": "group:tender",
    "sender_id": "+85233334444",
    "subject": None,
}


def _todo(**kwargs: Any) -> Todo:
    fields: dict[str, Any] = {
        "id": "tsk_1",
        "owner_user_id": "leo",
        "visibility": "private:leo",
        "title": "Send Ada the signed quote",
        "description": "",
        "stage": "working",
        "status": "in_progress",
        "priority": "high",
        "origin": "triage",
        "current_state": "captured",
        "trigger_state": "captured",
        "completion_state": "done",
        "source_kind": "inbound",
        "source_ref": "inb_1",
    }
    fields.update(kwargs)
    return Todo(**fields)


@dataclass
class _Notification:
    id: str
    command: str


@dataclass
class _CreateResult:
    notification: _Notification
    created: bool
    auto_answered: bool
    deliver_now: bool


class _Notifications:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _CreateResult:
        self.calls.append(kwargs)
        return _CreateResult(
            notification=_Notification(id="ntf_1", command=kwargs["command"]),
            created=True,
            # An irreversible approval is never auto-answered; if the store
            # ever did, this test would be the one to notice.
            auto_answered=False,
            deliver_now=True,
        )


class _Store:
    def __init__(self) -> None:
        self.outbound: list[dict[str, Any]] = []
        self.raises: Optional[Exception] = None

    async def record_outbound(self, principal, todo_id, **kwargs: Any) -> None:
        if self.raises:
            raise self.raises
        self.outbound.append({"id": todo_id, **kwargs})


def test_the_route_comes_from_the_arrival_not_the_caller():
    """C4: the reply leaves by the account the message arrived on."""
    assert target_from_arrival(ARRIVAL) == {
        "channel": "whatsapp",
        "target": "group:tender",
        "account_id": "+85211112222",
    }


def test_a_group_message_is_answered_in_the_group():
    """Replying privately to a group question answers a different question."""
    action = parse_action({"body": "on it"}, arrival=ARRIVAL)
    assert action.target == "group:tender"

    direct = parse_action(
        {"body": "on it"},
        arrival={**ARRIVAL, "conversation": None},
    )
    assert direct.target == "+85233334444"


def test_an_unroutable_or_empty_draft_is_refused():
    """An approval the user cannot act on is worse than no proposal."""
    with pytest.raises(OutboundError):
        parse_action({"body": "hi"})  # no channel, no arrival
    with pytest.raises(OutboundError):
        parse_action({"body": "   "}, arrival=ARRIVAL)
    with pytest.raises(OutboundError):
        parse_action({"body": "hi", "channel": "email"}, arrival=None)


def test_the_caller_may_override_the_arrival_route():
    action = parse_action(
        {"body": "hi", "channel": "email", "target": "ada@example.com"},
        arrival=ARRIVAL,
    )
    assert (action.channel, action.target) == ("email", "ada@example.com")
    # The account still falls back to the arrival's: the same inbox, the
    # channel the user chose.
    assert action.account_id == "+85211112222"


def test_the_command_is_legible_and_quoted():
    command = command_for(
        "tsk_1",
        ProposedAction(
            channel="whatsapp",
            target="group:the tender",
            body="on it",
            account_id="+85211112222",
        ),
    )
    assert command.startswith("hermes todos send tsk_1 ")
    assert "--channel whatsapp" in command
    assert "'group:the tender'" in command  # shlex-quoted, so it stays one arg
    assert "--account +85211112222" in command


@pytest.mark.asyncio
async def test_a_proposal_is_an_irreversible_approval():
    """D6: standing consent may never send mail on the user's behalf."""
    store, notifications = _Store(), _Notifications()
    action = parse_action({"body": "Quote attached."}, arrival=ARRIVAL)
    proposal = await propose(store, notifications, PRINCIPAL, _todo(), action)

    call = notifications.calls[-1]
    assert call["kind"] == "approval"
    assert call["reversible"] is False
    assert call["dedupe_key"] == "todo-action:tsk_1"
    assert call["body"] == "Quote attached."
    assert proposal.auto_approved is False
    assert proposal.notification_id == "ntf_1"


@pytest.mark.asyncio
async def test_a_proposal_lands_on_the_to_dos_own_history():
    store, notifications = _Store(), _Notifications()
    await propose(
        store,
        notifications,
        PRINCIPAL,
        _todo(),
        parse_action({"body": "on it"}, arrival=ARRIVAL),
    )
    assert store.outbound == [
        {
            "id": "tsk_1",
            "event": "proposed",
            "channel": "whatsapp",
            "actor": "user:leo",
        }
    ]


@pytest.mark.asyncio
async def test_the_proposal_inherits_the_to_dos_visibility():
    """A draft reply is at least as private as the to-do that raised it."""
    store, notifications = _Store(), _Notifications()
    await propose(
        store,
        notifications,
        PRINCIPAL,
        _todo(visibility="private:leo"),
        parse_action({"body": "on it"}, arrival=ARRIVAL),
    )
    assert notifications.calls[-1]["visibility"] == "private:leo"


# -- the route ------------------------------------------------------------


class _ApiStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get(self, principal, todo_id, **kwargs):
        return _todo()

    async def set_stage(self, principal, todo_id, stage, **kwargs):
        self.calls.append({"op": "stage", "id": todo_id, "stage": stage, **kwargs})
        return _todo(stage=stage, outcome=kwargs.get("outcome"))


async def _async(value):
    return value


def _request(body: dict):
    class _Req:
        async def json(self):
            return body

    return _Req()


@pytest.fixture
def wired(monkeypatch):
    store = _ApiStore()
    monkeypatch.setattr(
        todos_api, "_resolve_principal", lambda request: _async(PRINCIPAL)
    )
    monkeypatch.setattr(todos_api, "_store", lambda mode=None: store)
    monkeypatch.setattr(todos_api, "_table_ready", lambda s: _async(True))
    monkeypatch.setattr(todos_api, "_source_item", lambda p, t: _async(ARRIVAL))
    return store


@pytest.mark.asyncio
async def test_completing_without_a_draft_proposes_nothing(wired, monkeypatch):
    called = False

    async def _never(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(todos_api, "_propose", _never)
    payload = await todos_api.complete_todo(
        _request({"outcome": "Sent by hand"}), "tsk_1"
    )
    assert wired.calls[-1]["stage"] == "done"
    assert wired.calls[-1]["outcome"] == "Sent by hand"
    assert "proposal" not in payload
    assert called is False


@pytest.mark.asyncio
async def test_completing_with_a_draft_returns_the_proposal(wired, monkeypatch):
    async def _fake(principal, todo, action):
        return {"notification_id": "ntf_1", "command": "hermes todos send tsk_1"}

    monkeypatch.setattr(todos_api, "_propose", _fake)
    payload = await todos_api.complete_todo(
        _request({"proposed_action": {"body": "Quote attached."}}), "tsk_1"
    )
    assert payload["stage"] == "done"
    assert payload["proposal"]["notification_id"] == "ntf_1"


@pytest.mark.asyncio
async def test_a_bad_draft_does_not_cost_the_user_their_completion(wired):
    """The work is done; only the proposal failed, and it says so."""
    payload = await todos_api.complete_todo(
        _request({"proposed_action": {"body": "   ", "channel": "email"}}),
        "tsk_1",
    )
    assert payload["stage"] == "done"
    assert "body to send" in payload["proposal"]["error"]
