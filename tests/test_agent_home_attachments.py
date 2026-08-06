"""Behaviour contract for agent-home chat attachment materialization.

agent-home stores chat uploads in a private Supabase bucket and, before this
fix, passed the brain only an unreachable ``/api/chat/media`` URL — so an
attached PDF/DOCX/XLSX was invisible to the model (it searched the local
filesystem, found nothing, and refused to summarize). These tests exercise the
real code path in :mod:`hermes_cli.web_server`: the SSRF guard on the download
URL, and the fetch → document-cache → context-note enrichment that makes the
upload readable, reusing the gateway's document pipeline.
"""

import asyncio

import pytest

import hermes_constants
from hermes_cli import web_server


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://proj.supabase.co/storage/v1/object/sign/media/x?token=y", True),
        ("https://proj.supabase.in/storage/v1/object/sign/media/x", True),
        ("http://proj.supabase.co/storage/v1/object/sign/media/x", False),  # not https
        ("https://169.254.169.254/latest/meta-data/", False),  # metadata IP
        ("https://evil.example.com/x", False),  # arbitrary host
        ("https://supabase.co.evil.com/x", False),  # suffix spoof
        ("", False),
        ("not a url", False),
    ],
)
def test_download_guard(url, allowed):
    assert web_server._agent_home_download_allowed(url) is allowed


def test_download_guard_honors_supabase_url_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://myproj.internal-host.example")
    assert web_server._agent_home_download_allowed(
        "https://myproj.internal-host.example/storage/v1/object/sign/media/x"
    )
    # A different host is still rejected even with the env set.
    assert not web_server._agent_home_download_allowed("https://other.example/x")


# --------------------------------------------------------------------------- #
# Materialization
# --------------------------------------------------------------------------- #


@pytest.fixture
def temp_home(tmp_path):
    token = hermes_constants.set_hermes_home_override(tmp_path)
    try:
        yield tmp_path
    finally:
        hermes_constants.reset_hermes_home_override(token)


def _make_fetch(payload: bytes):
    async def _fetch(url: str) -> bytes:  # noqa: ARG001
        return payload

    return _fetch


def test_binary_document_is_cached_and_noted(temp_home):
    attachments = [
        {
            "url": "https://proj.supabase.co/storage/v1/object/sign/media/report.pdf",
            "name": "report.pdf",
            "content_type": "application/pdf",
            "size": 10,
        }
    ]
    out = _run(
        web_server._materialize_agent_home_attachments(
            attachments, "summarize this", fetch=_make_fetch(b"%PDF-1.7 fake")
        )
    )
    # Original user text is preserved after the context note.
    assert out.strip().endswith("summarize this")
    # The note points the brain at a real cached file and tells it to extract.
    assert "report.pdf" in out
    assert "extract the document's text yourself" in out
    docs = list((temp_home / "cache" / "documents").glob("doc_*_report.pdf"))
    assert len(docs) == 1
    assert docs[0].read_bytes() == b"%PDF-1.7 fake"
    assert str(docs[0]) in out  # local path is what the brain is pointed at


def test_text_document_is_inlined(temp_home):
    attachments = [
        {
            "url": "https://proj.supabase.co/storage/v1/object/sign/media/notes.md",
            "name": "notes.md",
            "content_type": "text/markdown",
            "size": 5,
        }
    ]
    out = _run(
        web_server._materialize_agent_home_attachments(
            attachments, "", fetch=_make_fetch(b"# Title\nhello")
        )
    )
    assert "# Title\nhello" in out  # content inlined for a text file
    assert "notes.md" in out


def test_disallowed_url_is_skipped_without_fabrication(temp_home):
    attachments = [
        {
            "url": "https://evil.example.com/x",
            "name": "secret.pdf",
            "content_type": "application/pdf",
            "size": 1,
        }
    ]
    called = {"n": 0}

    async def _fetch(url):  # noqa: ARG001
        called["n"] += 1
        return b"should not happen"

    out = _run(
        web_server._materialize_agent_home_attachments(
            attachments, "summarize", fetch=_fetch
        )
    )
    assert called["n"] == 0  # never fetched a non-allowlisted host
    assert "could not be retrieved" in out
    assert not list((temp_home / "cache" / "documents").glob("*"))


def test_download_failure_tells_agent_not_to_invent(temp_home):
    async def _boom(url):  # noqa: ARG001
        raise RuntimeError("network down")

    out = _run(
        web_server._materialize_agent_home_attachments(
            [
                {
                    "url": "https://proj.supabase.co/x",
                    "name": "deck.pptx",
                    "content_type": "",
                    "size": 1,
                }
            ],
            "summarize",
            fetch=_boom,
        )
    )
    assert "could not be downloaded" in out
    assert "do not invent" in out


def test_no_attachments_is_noop():
    out = _run(web_server._materialize_agent_home_attachments([], "hi"))
    assert out == "hi"
