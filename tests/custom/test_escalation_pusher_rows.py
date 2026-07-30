"""Escalation pushers read optional columns with ``row.get(...)``.

``sqlite3.Row`` has no ``.get``, so a row factory that returns Row objects makes
``format_whatsapp_escalation`` / ``format_email_escalation`` raise
``AttributeError`` the moment an escalation reaches the contact-lookup branch —
i.e. the notification is silently never delivered. These tests exercise the real
modules against a real temporary SQLite database.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    "escalation_pusher": REPO_ROOT / "custom" / "whatsapp" / "escalation_pusher.py",
    "escalation_pusher_v2": REPO_ROOT / "custom" / "shared" / "escalation_pusher_v2.py",
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(f"_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE escalations (
            id INTEGER PRIMARY KEY,
            channel TEXT, sender_phone TEXT, sender_email TEXT, sender_name TEXT,
            reason TEXT, summary TEXT, priority TEXT, source_phone TEXT,
            contact_id INTEGER, source_msg_id TEXT, status TEXT
        );
        CREATE TABLE unified_contacts (
            id INTEGER PRIMARY KEY, display_name TEXT, relation TEXT, is_family INTEGER
        );
        CREATE TABLE contact_handles (
            id INTEGER PRIMARY KEY, contact_id INTEGER, handle TEXT
        );
        CREATE TABLE email_messages (id TEXT PRIMARY KEY, subject TEXT);
        CREATE TABLE email_accounts (id TEXT PRIMARY KEY, label TEXT);
        INSERT INTO unified_contacts VALUES (7, 'Ada Lovelace', 'sister', 1);
        INSERT INTO contact_handles VALUES (1, 7, '+85212345678');
        INSERT INTO email_messages VALUES ('msg-1', 'Invoice overdue');
        INSERT INTO email_accounts VALUES ('acct-1', 'Work');
        INSERT INTO escalations VALUES
            (1, 'whatsapp', '+85212345678', NULL, NULL, 'family', 'Call me back',
             'high', 'phone1', 7, NULL, 'pending'),
            (2, 'email', NULL, 'ada@example.com', NULL, 'urgent_business',
             'Payment needed', 'high', 'acct-1', 7, 'msg-1', 'pending');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture(params=sorted(MODULES))
def pusher(request, tmp_path):
    name = request.param
    module = _load(name, MODULES[name])
    db_path = tmp_path / f"{name}.db"
    _seed(db_path)
    module.DB_PATH = str(db_path)
    return module


def test_rows_support_mapping_get(pusher):
    db = pusher.get_db()
    try:
        row = db.execute("SELECT * FROM escalations WHERE id = 1").fetchone()
        assert row.get("contact_id") == 7
        assert row.get("not_a_column") is None
        assert row["reason"] == "family"
        assert dict(row)["priority"] == "high"
    finally:
        db.close()


def test_formats_escalation_with_contact_lookup(pusher):
    db = pusher.get_db()
    try:
        wa = db.execute("SELECT * FROM escalations WHERE id = 1").fetchone()
        text = pusher.format_whatsapp_escalation(wa, db)
        assert "Ada Lovelace (sister)" in text
        assert "Call me back" in text

        em = db.execute("SELECT * FROM escalations WHERE id = 2").fetchone()
        text = pusher.format_email_escalation(em, db)
        assert "Ada Lovelace (sister)" in text
        assert "Payment needed" in text
    finally:
        db.close()
