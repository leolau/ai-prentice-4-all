"""Tests for the ``hermes incomings list|search|show`` query verbs.

The registry is stubbed at the module's single seam
(``_resolve_principal``); everything under test is argparse wiring, filter
passthrough, and the printed contract an agent copies from (full ids,
``more:`` continuation lines, exit codes).
"""

import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest

from hermes_cli import incomings_query
from hermes_cli.access import Principal
from hermes_cli.inbound_registry import InboundItem, InboundPage


def _item(**kwargs) -> InboundItem:
    fields = {
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
        "occurred_at": datetime(2026, 8, 10, 14, 3, tzinfo=timezone.utc),
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


PRINCIPAL = Principal(user_id="leo", display="Leo", role="owner")


class _RegistryStub:
    def __init__(self, items=(), next_cursor=None, attachments=()):
        self.items = list(items)
        self.next_cursor = next_cursor
        self._attachments = list(attachments)
        self.calls = []
        self.get_result = None

    async def list(self, principal, **kwargs):
        self.calls.append(kwargs)
        return InboundPage(items=self.items, next_cursor=self.next_cursor)

    async def get(self, principal, item_id):
        self.calls.append({"get": item_id})
        return self.get_result

    async def attachments(self, principal, item_id):
        return self._attachments


@pytest.fixture
def parser():
    top = argparse.ArgumentParser(prog="hermes")
    sub = top.add_subparsers(dest="incomings_command", required=True)
    incomings_query.register_incomings_query_verbs(sub)
    return top


@pytest.fixture
def wired(monkeypatch):
    """Patch the seam; tests inject the stub via the returned holder."""
    holder = {}

    async def _fake_resolve(actor):
        if actor == "ghost":
            raise RuntimeError("unknown --actor")
        if "stub" not in holder:
            raise RuntimeError("no owner enrolled")
        return holder["stub"], PRINCIPAL

    monkeypatch.setattr(incomings_query, "_resolve_principal", _fake_resolve)
    return holder


def _run(parser, capsys, argv):
    args = parser.parse_args(argv)
    code = args.func(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_list_passes_filters_split_and_typed(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[_item()])
    code, out, err = _run(
        parser,
        capsys,
        [
            "list",
            "--surface", "email,whatsapp",
            "--sender", "Ada",
            "--importance", "high",
            "--since", "2026-08-01T00:00:00Z",
            "--remembered",
            "--limit", "5",
        ],
    )
    assert code == 0
    call = wired["stub"].calls[0]
    assert call["query"] == ""
    assert call["surfaces"] == ["email", "whatsapp"]
    assert call["senders"] == ["Ada"]
    assert call["importance"] == ["high"]
    assert call["since"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert call["remembered"] is True
    assert call["limit"] == 5
    assert call["cursor"] is None


def test_list_defaults(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[_item()])
    code, _, _ = _run(parser, capsys, ["list"])
    assert code == 0
    call = wired["stub"].calls[0]
    assert call["limit"] == 20
    assert call["cursor"] is None
    assert call["remembered"] is None
    assert call["surfaces"] == []


def test_unremembered_maps_to_false(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[_item()])
    _run(parser, capsys, ["list", "--unremembered"])
    assert wired["stub"].calls[0]["remembered"] is False


def test_relative_since(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[_item()])
    code, _, _ = _run(parser, capsys, ["list", "--since", "7d"])
    assert code == 0
    since = wired["stub"].calls[0]["since"]
    assert since.tzinfo is not None
    assert timedelta(days=6) < datetime.now(timezone.utc) - since < timedelta(days=8)


def test_list_prints_full_ids_and_cursor_hint(parser, wired, capsys):
    first = _item()
    second = _item(
        id="22222222-2222-2222-2222-222222222222",
        surface="whatsapp",
        sender_name="+4915123456",
        subject=None,
        has_attachments=True,
    )
    wired["stub"] = _RegistryStub(items=[first, second], next_cursor="cur_2")
    code, out, _ = _run(parser, capsys, ["list", "--limit", "2"])
    assert code == 0
    assert first.id in out
    assert second.id in out
    assert "+att" in out
    assert "cur_2" in out
    assert "hermes incomings list" in out
    assert "--limit 2" in out


def test_empty_page_is_reported(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[])
    code, out, _ = _run(parser, capsys, ["list"])
    assert code == 0
    assert "No arrivals match." in out
    assert "more:" not in out


def test_search_sends_the_query_and_echoes_it_in_the_hint(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[_item()], next_cursor="cur_9")
    code, out, _ = _run(parser, capsys, ["search", "invoice 42", "--surface", "email"])
    assert code == 0
    call = wired["stub"].calls[0]
    assert call["query"] == "invoice 42"
    assert call["surfaces"] == ["email"]
    assert 'hermes incomings search "invoice 42"' in out
    assert '--surface "email"' in out


def test_show_prints_body_and_attachments(parser, wired, capsys):
    item = _item()
    stub = _RegistryStub(
        attachments=[
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "filename": "deck.pdf",
                "content_type": "application/pdf",
                "byte_size": 12345,
                "document_id": None,
                "remembered": False,
            }
        ]
    )
    stub.get_result = item
    wired["stub"] = stub
    code, out, _ = _run(parser, capsys, ["show", item.id])
    assert code == 0
    assert item.id in out
    assert "the tender is due friday" in out
    assert "deck.pdf" in out
    assert "12345 bytes" in out


def test_show_of_an_invisible_or_missing_item(parser, wired, capsys):
    stub = _RegistryStub()
    stub.get_result = None
    wired["stub"] = stub
    code, out, err = _run(
        parser, capsys, ["show", "99999999-9999-9999-9999-999999999999"]
    )
    assert code == 1
    assert "visible to leo" in err
    assert out == ""


def test_json_modes_parse(parser, wired, capsys):
    item = _item()
    stub = _RegistryStub(items=[item], next_cursor="cur_j")
    stub.get_result = item
    wired["stub"] = stub

    code, out, _ = _run(parser, capsys, ["list", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["next_cursor"] == "cur_j"
    assert payload["items"][0]["id"] == item.id

    code, out, _ = _run(parser, capsys, ["show", item.id, "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["id"] == item.id
    assert "attachments" in payload


def test_no_principal_is_a_clean_error(parser, wired, capsys):
    # No stub injected: the seam raises "no owner enrolled".
    code, out, err = _run(parser, capsys, ["list"])
    assert code == 1
    assert "no owner enrolled" in err


def test_unknown_actor_is_a_clean_error(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[_item()])
    code, _, err = _run(parser, capsys, ["list", "--actor", "ghost"])
    assert code == 1
    assert "unknown --actor" in err


def test_a_malformed_since_is_a_usage_error(parser, wired, capsys):
    wired["stub"] = _RegistryStub(items=[_item()])
    code, out, err = _run(parser, capsys, ["list", "--since", "yesterdayish"])
    assert code == 2
    assert "--since" in err
