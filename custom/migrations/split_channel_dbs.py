#!/usr/bin/env python3
"""Split the shared whatsapp_data.db into per-domain SQLite files.

Copies schema + rows for each domain's tables into its own database, then
renames the source tables to ``zz_migrated_<name>`` so any consumer missed by
the repoint fails loudly instead of silently writing stale rows.

Layout after the split:

- ``MESSAGING_DB``  (was whatsapp_data.db): messages, wa_*, phones,
  unified_contacts, contact_handles, contact_merge_suggestions,
  contacts_old_backup, escalations
- ``EMAIL_DB``:     email_accounts, email_digests, email_messages,
  email_notes, email_tasks
- ``CALENDAR_DB``:  calendar_accounts, calendar_attendees, calendar_events,
  calendar_reminders

Idempotent: tables already migrated (zz_migrated_* present, original gone) are
skipped. Rollback: point the env vars back at the old file and rename the
zz_migrated_* tables back.

Run with services stopped: systemctl stop hermes-email-poller ... etc.
"""

import os
import sqlite3
import sys

MESSAGING_DB = os.environ.get(
    'MESSAGING_DB_PATH', '/opt/data/whatsapp-messages/messaging_data.db')
# The pre-split file sits next to the renamed one.
LEGACY_DB = os.path.join(os.path.dirname(MESSAGING_DB), 'whatsapp_data.db')
EMAIL_DB = os.environ.get(
    'EMAIL_DB_PATH', '/opt/data/email-messages/email_data.db')
CALENDAR_DB = os.environ.get(
    'CALENDAR_DB_PATH', '/opt/data/calendar/calendar_data.db')

DOMAINS = {
    EMAIL_DB: [
        'email_accounts', 'email_digests', 'email_messages',
        'email_notes', 'email_tasks',
    ],
    CALENDAR_DB: [
        'calendar_accounts', 'calendar_attendees', 'calendar_events',
        'calendar_reminders',
    ],
}


def copy_domain(src, src_path, dest_path, tables):
    """Copy schema objects + rows for `tables` from src into dest_path."""
    dest = sqlite3.connect(dest_path)
    dest.execute("PRAGMA journal_mode=WAL")
    dest.execute("ATTACH DATABASE ? AS srcdb", (src_path,))
    moved = []
    for table in tables:
        exists = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            print(f"  {table}: not in source (already migrated?) — skip")
            continue
        # Copy every schema object belonging to the table (indexes,
        # triggers) — the CREATE TABLE itself plus its dependents.
        objects = src.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name=? AND sql IS NOT NULL", (table,),
        ).fetchall()
        # CREATE TABLE first, then indexes/triggers.
        objects.sort(key=lambda r: 0 if r[0] == 'table' else 1)
        for _type, _name, sql in objects:
            dest.execute(sql)
        n = dest.execute(
            f"SELECT COUNT(*) FROM srcdb.\"{table}\"").fetchone()[0]
        dest.execute(
            f"INSERT INTO \"{table}\" SELECT * FROM srcdb.\"{table}\"")
        dest.commit()
        print(f"  {table}: {n} rows copied")
        moved.append(table)
    dest.close()
    return moved


def retire_source(src, tables):
    """Rename migrated source tables so stale consumers fail loudly."""
    for table in tables:
        src.execute(
            f"ALTER TABLE \"{table}\" RENAME TO \"zz_migrated_{table}\"")
    src.commit()


def main():
    source_path = LEGACY_DB if os.path.exists(LEGACY_DB) else MESSAGING_DB
    if not os.path.exists(source_path):
        print(f"no source DB at {LEGACY_DB} or {MESSAGING_DB} — nothing to do")
        return 1
    print(f"source: {source_path}")
    src = sqlite3.connect(source_path, timeout=60)
    src.execute("PRAGMA journal_mode=WAL")
    for dest_path, tables in DOMAINS.items():
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        print(f"-> {dest_path}")
        moved = copy_domain(src, source_path, dest_path, tables)
        if moved:
            retire_source(src, moved)
    # If we read from the legacy path, rename the file itself last.
    if source_path == LEGACY_DB:
        src.close()
        for suffix in ('', '-wal', '-shm'):
            old, new = LEGACY_DB + suffix, MESSAGING_DB + suffix
            if os.path.exists(old):
                os.replace(old, new)
        print(f"renamed {LEGACY_DB} -> {MESSAGING_DB}")
    else:
        src.close()
    print("done")
    return 0


if __name__ == '__main__':
    sys.exit(main())
