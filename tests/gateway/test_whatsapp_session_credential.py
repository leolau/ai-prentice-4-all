"""Tests for the WhatsApp credential-store session seam (PR6).

Covers the reference session-directory adapter from the unified credential
store design (docs/design/unified-credential-store.md §B.1):

- materialize writes ``creds.json`` (0600) from the store when absent;
- materialize never overwrites an existing ``creds.json``;
- the seam is entirely inert when ``extra.session_credential`` is unset;
- write-back upserts on sha256 hash diff and is a no-op when unchanged;
- write-back preserves an existing entry's kind/services/visibility.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform


def _make_adapter(tmp_path: Path, extra: dict):
    """Bare WhatsAppAdapter with just the attributes the seam touches."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = SimpleNamespace(extra=extra)
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    adapter._session_path = session_dir
    adapter._session_cred_hash = None
    return adapter


_PRINCIPAL = SimpleNamespace(
    user_id="owner", role="owner", private_visibility="private:owner"
)


def _patch_store(store):
    return (
        patch(
            "hermes_cli.credential_store.resolve_owner_principal",
            new=AsyncMock(return_value=_PRINCIPAL),
        ),
        patch(
            "hermes_cli.credential_store.default_credential_store",
            return_value=store,
        ),
    )


class TestSessionCredentialName:
    def test_unset_returns_none(self, tmp_path):
        adapter = _make_adapter(tmp_path, {})
        assert adapter._session_credential_name() is None

    def test_non_string_returns_none(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": 123})
        assert adapter._session_credential_name() is None

    def test_whitespace_returns_none(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "   "})
        assert adapter._session_credential_name() is None

    def test_name_stripped(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": " box1 "})
        assert adapter._session_credential_name() == "box1"


class TestMaterialize:
    @pytest.mark.asyncio
    async def test_writes_creds_0600_when_absent(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})
        session = {"me": "1234@s.whatsapp.net", "noiseKey": "abc"}
        entry = SimpleNamespace(payload={"session": session})
        store = MagicMock()
        store.get = AsyncMock(return_value=entry)

        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._materialize_session_credential()

        creds = adapter._session_path / "creds.json"
        assert creds.exists()
        assert json.loads(creds.read_text()) == session
        assert (creds.stat().st_mode & 0o777) == 0o600
        store.get.assert_awaited_once_with(_PRINCIPAL, "whatsapp", "box1")
        # Hash of the materialized content recorded so write-back can diff.
        assert adapter._session_cred_hash == hashlib.sha256(
            json.dumps(session).encode()
        ).hexdigest()

    @pytest.mark.asyncio
    async def test_noop_when_creds_present(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})
        creds = adapter._session_path / "creds.json"
        creds.write_text('{"me": "paired@s.whatsapp.net"}')
        store = MagicMock()
        store.get = AsyncMock()

        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._materialize_session_credential()

        # Existing file untouched, store never consulted.
        assert json.loads(creds.read_text()) == {"me": "paired@s.whatsapp.net"}
        store.get.assert_not_awaited()
        assert adapter._session_cred_hash == hashlib.sha256(
            creds.read_bytes()
        ).hexdigest()

    @pytest.mark.asyncio
    async def test_skipped_when_unset(self, tmp_path):
        adapter = _make_adapter(tmp_path, {})
        with patch(
            "hermes_cli.credential_store.default_credential_store"
        ) as mock_store:
            await adapter._materialize_session_credential()

        mock_store.assert_not_called()
        assert not (adapter._session_path / "creds.json").exists()

    @pytest.mark.asyncio
    async def test_missing_entry_leaves_dir_empty(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "ghost"})
        store = MagicMock()
        store.get = AsyncMock(return_value=None)

        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._materialize_session_credential()

        assert not (adapter._session_path / "creds.json").exists()

    @pytest.mark.asyncio
    async def test_bad_payload_shape_is_not_written(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})
        entry = SimpleNamespace(payload={"session": "not-a-dict"})
        store = MagicMock()
        store.get = AsyncMock(return_value=entry)

        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._materialize_session_credential()

        assert not (adapter._session_path / "creds.json").exists()


class TestPersist:
    @pytest.mark.asyncio
    async def test_upserts_on_hash_diff(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})
        session = {"me": "new@s.whatsapp.net"}
        creds = adapter._session_path / "creds.json"
        creds.write_text(json.dumps(session))
        adapter._session_cred_hash = "stale"

        existing = SimpleNamespace(
            kind="whatsapp-session",
            services=["messaging"],
            visibility="private:owner",
        )
        store = MagicMock()
        store.get = AsyncMock(return_value=existing)
        store.put = AsyncMock()

        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._persist_session_credential()

        store.put.assert_awaited_once()
        args, kwargs = store.put.await_args
        assert args == (_PRINCIPAL,)
        assert kwargs["provider"] == "whatsapp"
        assert kwargs["name"] == "box1"
        # Existing entry's kind/services/visibility preserved across upsert.
        assert kwargs["kind"] == "whatsapp-session"
        assert kwargs["services"] == ["messaging"]
        assert kwargs["visibility"] == "private:owner"
        assert kwargs["payload"] == {"session": session}
        assert adapter._session_cred_hash == hashlib.sha256(
            creds.read_bytes()
        ).hexdigest()

    @pytest.mark.asyncio
    async def test_creates_private_entry_when_absent(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})
        session = {"me": "paired@s.whatsapp.net"}
        creds = adapter._session_path / "creds.json"
        creds.write_text(json.dumps(session))

        store = MagicMock()
        store.get = AsyncMock(return_value=None)
        store.put = AsyncMock()

        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._persist_session_credential()

        _, kwargs = store.put.await_args
        assert kwargs["kind"] == "whatsapp-session"
        assert kwargs["visibility"] == "private"
        assert kwargs["services"] is None
        assert kwargs["payload"] == {"session": session}

    @pytest.mark.asyncio
    async def test_noop_when_hash_unchanged(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})
        creds = adapter._session_path / "creds.json"
        creds.write_text('{"me": "same@s.whatsapp.net"}')
        adapter._session_cred_hash = hashlib.sha256(creds.read_bytes()).hexdigest()

        store = MagicMock()
        store.get = AsyncMock()
        store.put = AsyncMock()

        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._persist_session_credential()

        store.get.assert_not_awaited()
        store.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skipped_when_unset(self, tmp_path):
        adapter = _make_adapter(tmp_path, {})
        (adapter._session_path / "creds.json").write_text("{}")

        with patch(
            "hermes_cli.credential_store.default_credential_store"
        ) as mock_store:
            await adapter._persist_session_credential()

        mock_store.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_when_creds_missing(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})

        store = MagicMock()
        store.put = AsyncMock()
        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._persist_session_credential()

        store.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_json_not_persisted(self, tmp_path):
        adapter = _make_adapter(tmp_path, {"session_credential": "box1"})
        creds = adapter._session_path / "creds.json"
        creds.write_text("not-json")

        store = MagicMock()
        store.put = AsyncMock()
        p1, p2 = _patch_store(store)
        with p1, p2:
            await adapter._persist_session_credential()

        store.put.assert_not_awaited()
