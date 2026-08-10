"""The backfill reads the pipeline's SQLite the way the live hooks write it.

Reading is the risky half: the three services store timestamps and ids in
their own shapes, and a mapping that disagrees with the live hook by one field
produces duplicate inbox rows instead of updating one.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cli.incomings_backfill import (
    _calendar_rows,
    _email_rows,
    _since_bound,
    _whatsapp_rows,
)

WHATSAPP_SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, source_phone TEXT, sender_phone TEXT,
    sender_name TEXT, chat_id TEXT, is_group INTEGER, text TEXT,
    media_type TEXT, media_path TEXT, media_mimetype TEXT,
    timestamp TEXT, received_at TEXT, batch_id TEXT, raw_json TEXT
);
"""

EMAIL_SCHEMA = """
CREATE TABLE email_messages (
    id TEXT PRIMARY KEY, account_id TEXT, from_addr TEXT, from_name TEXT,
    to_addrs TEXT, cc_addrs TEXT, subject TEXT, body_text TEXT,
    body_html TEXT, has_attachments INTEGER, attachment_info TEXT,
    message_id TEXT, in_reply_to TEXT, thread_id TEXT, folder TEXT,
    received_at TEXT, batch_id TEXT, raw_headers TEXT
);
"""

CALENDAR_SCHEMA = """
CREATE TABLE calendar_events (
    id TEXT PRIMARY KEY, google_event_id TEXT, account_id TEXT,
    calendar_id TEXT, summary TEXT, description TEXT, location TEXT,
    start_time TEXT, end_time TEXT, all_day INTEGER, timezone TEXT,
    status TEXT, organizer_email TEXT, organizer_name TEXT,
    recurring_event_id TEXT, html_link TEXT, conference_link TEXT,
    raw_json TEXT, triaged INTEGER, created_at TEXT, updated_at TEXT
);
"""


def _db(tmp_path: Path, schema: str) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "pipeline.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    return conn


def test_whatsapp_rows_carry_sender_and_media_presence(tmp_path: Path) -> None:
    conn = _db(tmp_path, WHATSAPP_SCHEMA)
    conn.execute(
        "INSERT INTO messages (id, source_phone, sender_phone, sender_name, "
        "chat_id, is_group, text, media_type, timestamp) "
        "VALUES ('wamid.1', '+85211112222', '+85233334444', 'Ada', "
        "'group:tender', 1, '請問明天的會議', 'image', "
        "'2026-08-10T09:30:00+00:00')"
    )

    (row,) = list(_whatsapp_rows(conn, None))
    assert row["surface"] == "whatsapp"
    assert row["external_id"] == "wamid.1"
    assert row["account_id"] == "+85211112222"
    assert row["conversation"] == "group:tender"
    assert row["sender_name"] == "Ada"
    assert row["body"] == "請問明天的會議"
    assert row["has_attachments"] is True
    assert row["metadata"]["is_group"] is True
    assert row["occurred_at"] == datetime(2026, 8, 10, 9, 30, tzinfo=timezone.utc)


def test_email_rows_never_carry_the_html_part(tmp_path: Path) -> None:
    """The registry is for reading; marketing HTML only bloats the index."""
    conn = _db(tmp_path, EMAIL_SCHEMA)
    conn.execute(
        "INSERT INTO email_messages (id, account_id, from_addr, from_name, "
        "to_addrs, cc_addrs, subject, body_text, body_html, has_attachments, "
        "message_id, thread_id, folder, received_at) "
        "VALUES ('1', 'work', 'ada@example.com', 'Ada', '[]', '[]', "
        "'Invoice 42', 'the plain text', '<html>spam</html>', 1, "
        "'<abc@example.com>', 'thread-7', 'INBOX', "
        "'2026-08-10T09:30:00+00:00')"
    )

    (row,) = list(_email_rows(conn, None))
    assert row["external_id"] == "<abc@example.com>"
    assert row["conversation"] == "thread-7"
    assert row["subject"] == "Invoice 42"
    assert row["body"] == "the plain text"
    assert "spam" not in str(row)
    assert row["has_attachments"] is True
    assert row["metadata"]["folder"] == "INBOX"


def test_calendar_rows_keep_the_time_range_and_the_series(tmp_path: Path) -> None:
    conn = _db(tmp_path, CALENDAR_SCHEMA)
    conn.execute(
        "INSERT INTO calendar_events (id, google_event_id, account_id, "
        "calendar_id, summary, description, location, start_time, end_time, "
        "all_day, status, organizer_email, organizer_name, "
        "recurring_event_id, html_link, conference_link) "
        "VALUES ('1', 'gcal-1', 'work', 'primary', 'Standup', 'daily', "
        "'Room 3', '2026-08-11T01:00:00+00:00', '2026-08-11T01:15:00+00:00', "
        "0, 'confirmed', 'leo@example.com', 'Leo', 'series-1', "
        "'https://cal', 'https://meet')"
    )

    (row,) = list(_calendar_rows(conn, None))
    assert row["kind"] == "event"
    assert row["external_id"] == "gcal-1"
    assert row["conversation"] == "series-1"
    assert row["subject"] == "Standup"
    assert row["occurred_at"] == datetime(2026, 8, 11, 1, 0, tzinfo=timezone.utc)
    assert row["ends_at"] == datetime(2026, 8, 11, 1, 15, tzinfo=timezone.utc)
    assert row["metadata"]["conference_link"] == "https://meet"


def test_since_filters_out_older_arrivals(tmp_path: Path) -> None:
    conn = _db(tmp_path, WHATSAPP_SCHEMA)
    conn.executemany(
        "INSERT INTO messages (id, source_phone, chat_id, text, timestamp) "
        "VALUES (?, '+1', 'c', 'hi', ?)",
        [
            ("old", "2026-01-01T00:00:00+00:00"),
            ("new", "2026-08-10T00:00:00+00:00"),
        ],
    )
    ids = [r["external_id"] for r in _whatsapp_rows(conn, "2026-06-01T00:00:00+00:00")]
    assert ids == ["new"]


def test_a_missing_table_is_not_an_error(tmp_path: Path) -> None:
    """A box that only runs email has no `messages` table, and that is fine."""
    conn = _db(tmp_path, EMAIL_SCHEMA)
    assert list(_whatsapp_rows(conn, None)) == []
    assert list(_calendar_rows(conn, None)) == []


def test_relative_since_is_what_an_operator_actually_types() -> None:
    absolute = _since_bound("2026-08-01T00:00:00+00:00")
    assert absolute == "2026-08-01T00:00:00+00:00"

    bound = _since_bound("30d")
    assert bound is not None
    parsed = datetime.fromisoformat(bound)
    expected = datetime.now(timezone.utc) - timedelta(days=30)
    assert abs((parsed - expected).total_seconds()) < 60

    assert _since_bound(None) is None
