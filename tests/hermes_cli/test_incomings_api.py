"""The incomings HTTP surface, exercised without a database.

The routes are thin by design — parse, scope, delegate — so what is worth
pinning is exactly the thin part: that a box whose registry was never
initialised answers with an empty inbox instead of a 500, that filters reach
the registry in the shape it expects, that the limit is capped, and that an
invisible item is indistinguishable from a missing one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from hermes_cli import incomings_api
from hermes_cli.access import Principal
from hermes_cli.inbound_registry import InboundItem, InboundPage

PRINCIPAL = Principal(user_id="leo", display="Leo", role="owner")  # type: ignore[arg-type]


def _item(**kwargs: Any) -> InboundItem:
    fields: dict[str, Any] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "owner_user_id": "leo",
        "visibility": "private:leo",
        "surface": "email",
        "account_id": "leo@example.com",
        "external_id": "<abc@mail>",
        "kind": "message",
        "conversation": "thread-1",
        "conversation_name": None,
        "sender_id": "ada@example.com",
        "sender_name": "Ada",
        "subject": "Invoice 42",
        "body": "the tender is due friday",
        "occurred_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "ends_at": None,
        "registered_at": None,
        "updated_at": None,
        "importance": None,
        "has_attachments": False,
        "metadata": {},
        "document_id": None,
        "remembered_at": None,
        "remembered_by": None,
    }
    fields.update(kwargs)
    return InboundItem(**fields)


class _Registry:
    """A registry stub that records how the route called it."""

    def __init__(self, *, exists: bool = True, items: Optional[list] = None) -> None:
        self.exists = exists
        self.items = items if items is not None else [_item()]
        self.calls: list[dict[str, Any]] = []

    async def table_exists(self) -> bool:
        return self.exists

    async def list(self, principal, **kwargs):
        self.calls.append(kwargs)
        return InboundPage(items=list(self.items), next_cursor="cur_2")

    async def get(self, principal, item_id):
        return next((i for i in self.items if str(i.id) == item_id), None)

    async def facets(self, principal):
        return {
            "surfaces": [{"value": "email", "count": 1}],
            "importance": [],
        }

    async def attachments(self, principal, item_id):
        return [{"id": "file_1", "filename": "deck.pdf", "byte_size": 10}]


class _Tags:
    async def for_entity(self, principal, kind, entity_id):
        return []

    async def list(self, principal, entity_kind=None):
        return []


@pytest.fixture
def wired(monkeypatch):
    registry = _Registry()
    monkeypatch.setattr(
        incomings_api, "_resolve_principal", lambda request: _async(PRINCIPAL)
    )
    monkeypatch.setattr(incomings_api, "_registry", lambda mode=None: registry)
    monkeypatch.setattr(incomings_api, "_tags", lambda mode=None: _Tags())
    monkeypatch.setattr(
        incomings_api, "_table_exists", lambda reg: _async(reg.exists)
    )
    return registry


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_an_uninitialised_registry_is_an_empty_inbox(wired):
    # A box that has received nothing since the feature shipped has no table.
    # That is a new user, not a server error.
    wired.exists = False
    assert await incomings_api.list_incomings(request=None) == {
        "items": [],
        "next_cursor": None,
    }
    assert await incomings_api.incomings_facets(request=None) == {
        "surfaces": [],
        "importance": [],
        "tags": [],
    }


@pytest.mark.asyncio
async def test_filters_reach_the_registry_split_and_typed(wired):
    await incomings_api.list_incomings(
        request=None,
        q="invoice",
        surface="email, whatsapp",
        tag="finance,urgent",
        exclude_tag="spam",
        tag_match="ALL",
        remembered="true",
        has_attachments="1",
        since="2026-08-01T00:00:00Z",
    )
    call = wired.calls[-1]
    assert call["query"] == "invoice"
    assert call["surfaces"] == ["email", "whatsapp"]
    assert call["include_tags"] == ["finance", "urgent"]
    assert call["exclude_tags"] == ["spam"]
    assert call["tag_match"] == "all"
    assert call["remembered"] is True
    assert call["has_attachments"] is True
    assert call["since"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
    # Absent tri-state flags stay absent: "not filtered" is not "false".
    assert call["until"] is None


@pytest.mark.asyncio
async def test_the_page_size_is_capped_and_never_zero(wired):
    await incomings_api.list_incomings(request=None, limit=10_000)
    assert wired.calls[-1]["limit"] == incomings_api._MAX_LIMIT
    await incomings_api.list_incomings(request=None, limit=0)
    assert wired.calls[-1]["limit"] == incomings_api._DEFAULT_LIMIT


@pytest.mark.asyncio
async def test_a_malformed_date_is_the_caller_s_error(wired):
    with pytest.raises(HTTPException) as excinfo:
        await incomings_api.list_incomings(request=None, since="yesterday")
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_the_page_carries_the_cursor_and_no_total(wired):
    page = await incomings_api.list_incomings(request=None)
    assert page["next_cursor"] == "cur_2"
    assert "total" not in page
    assert page["items"][0]["subject"] == "Invoice 42"
    assert page["items"][0]["tags"] == []


@pytest.mark.asyncio
async def test_detail_carries_attachments_and_tags(wired):
    payload = await incomings_api.get_incoming(
        request=None, item_id="11111111-1111-1111-1111-111111111111"
    )
    assert payload["attachments"][0]["filename"] == "deck.pdf"
    assert payload["tags"] == []


@pytest.mark.asyncio
async def test_somebody_else_s_arrival_is_simply_not_found(wired):
    # RLS filters it out, so `get` returns None — and a 403 here would confirm
    # the message exists.
    with pytest.raises(HTTPException) as excinfo:
        await incomings_api.get_incoming(request=None, item_id="not-mine")
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_remembering_an_empty_arrival_is_refused(wired, monkeypatch):
    wired.items = [_item(subject=None, body="")]
    with pytest.raises(HTTPException) as excinfo:
        await incomings_api.remember_incoming(
            request=None, item_id="11111111-1111-1111-1111-111111111111"
        )
    assert excinfo.value.status_code == 400


def test_flag_parses_the_tri_state():
    assert incomings_api._flag(None) is None
    assert incomings_api._flag("") is None
    assert incomings_api._flag("true") is True
    assert incomings_api._flag("no") is False
