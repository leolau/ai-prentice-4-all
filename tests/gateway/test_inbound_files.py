"""Channel attachments reach the file registry with their provenance intact."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from gateway.inbound_files import (
    cached_display_name,
    register_event_files,
    surface_for,
)


@dataclass
class _Source:
    platform: str = "telegram"
    chat_id: str = "chat-1"
    thread_id: str = ""
    user_id: str = "tg-9001"
    user_name: str = "Ada Wong"
    account_id: str = "bot-main"
    internal_user_id: Optional[str] = "ada"
    internal_user_role: str = "member"


@dataclass
class _Event:
    media_urls: list = field(default_factory=list)
    media_types: list = field(default_factory=list)
    message_id: str = "42"


class _Registry:
    """Records what would have been written, without a database."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def register(self, principal, **kwargs):
        self.calls.append({"owner": principal.user_id, **kwargs})
        return kwargs


class _Storage:
    bucket = "agent-home-media"

    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str]] = []

    async def upload(self, path, data, *, content_type="application/octet-stream"):
        self.uploads.append((path, data, content_type))
        return path


def test_cached_display_name_strips_the_cache_prefix() -> None:
    assert cached_display_name("/tmp/cache/doc_0123456789ab_grant.pdf") == "grant.pdf"
    assert cached_display_name("/tmp/cache/plain.txt") == "plain.txt"


def test_surface_names_the_channel_a_person_recognises() -> None:
    assert surface_for("telegram") == "telegram"
    assert surface_for("bluebubbles") == "imessage"

    class _Enum:
        value = "WhatsApp"

    assert surface_for(_Enum()) == "whatsapp"


@pytest.mark.asyncio
async def test_an_attachment_is_registered_with_its_provenance(tmp_path) -> None:
    cached = tmp_path / "doc_0123456789ab_grant.pdf"
    cached.write_bytes(b"%PDF-1.7 grant")
    registry, storage = _Registry(), _Storage()

    written = await register_event_files(
        _Event(media_urls=[str(cached)], media_types=["application/pdf"]),
        _Source(thread_id="topic-7"),
        registry=registry,
        storage=storage,
    )

    assert len(written) == 1
    call = registry.calls[0]
    assert call["owner"] == "ada"
    assert call["surface"] == "telegram"
    assert call["filename"] == "grant.pdf"
    assert call["content_type"] == "application/pdf"
    assert call["account_id"] == "bot-main"
    assert call["conversation"] == "chat-1#topic-7"
    assert call["sender_id"] == "tg-9001"
    assert call["sender_name"] == "Ada Wong"
    assert call["message_id"] == "42"
    assert storage.uploads[0][1] == b"%PDF-1.7 grant"


@pytest.mark.asyncio
async def test_the_content_type_is_guessed_when_the_adapter_declares_none(
    tmp_path,
) -> None:
    cached = tmp_path / "doc_0123456789ab_notes.md"
    cached.write_bytes(b"# notes")
    registry = _Registry()

    await register_event_files(
        _Event(media_urls=[str(cached)]),
        _Source(),
        registry=registry,
        storage=_Storage(),
    )
    assert registry.calls[0]["content_type"] == "text/markdown"


@pytest.mark.asyncio
async def test_an_unenrolled_sender_registers_nothing(tmp_path) -> None:
    """A row needs an owner; parking it under someone else would be worse."""
    cached = tmp_path / "doc_0123456789ab_x.bin"
    cached.write_bytes(b"x")
    registry = _Registry()

    written = await register_event_files(
        _Event(media_urls=[str(cached)]),
        _Source(internal_user_id=None),
        registry=registry,
        storage=_Storage(),
    )
    assert written == []
    assert registry.calls == []


@pytest.mark.asyncio
async def test_a_pruned_cache_file_is_skipped_not_fatal(tmp_path) -> None:
    present = tmp_path / "doc_0123456789ab_here.txt"
    present.write_bytes(b"here")
    registry = _Registry()

    written = await register_event_files(
        _Event(media_urls=[str(tmp_path / "gone.txt"), str(present)]),
        _Source(),
        registry=registry,
        storage=_Storage(),
    )
    assert len(written) == 1
    assert registry.calls[0]["filename"] == "here.txt"


@pytest.mark.asyncio
async def test_an_event_without_attachments_touches_nothing() -> None:
    registry = _Registry()
    assert await register_event_files(_Event(), _Source(), registry=registry) == []
    assert registry.calls == []
