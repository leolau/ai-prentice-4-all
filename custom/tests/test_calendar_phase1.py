#!/usr/bin/env python3
"""
Phase 1 Tests: Calendar DB schema + credential resolution via the unified store.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'migrations'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'calendar'))

os.environ['DB_PATH'] = ':memory:'

class TestCalendarSchema(unittest.TestCase):
    """Test calendar table creation and schema."""

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        # Create the unified_contacts table (foreign key target)
        self.db.execute("""CREATE TABLE unified_contacts (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_create_calendar_accounts_table(self):
        """calendar_accounts table should be created with correct columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_accounts)").fetchall()
        col_names = {c['name'] for c in cols}
        self.assertIn('id', col_names)
        self.assertIn('email', col_names)
        self.assertIn('label', col_names)
        self.assertIn('sync_token', col_names)
        self.assertIn('last_synced', col_names)
        self.assertIn('enabled', col_names)

    def test_create_calendar_events_table(self):
        """calendar_events table should have all required columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_events)").fetchall()
        col_names = {c['name'] for c in cols}
        expected = {
            'id', 'google_event_id', 'account_id', 'calendar_id',
            'summary', 'description', 'location',
            'start_time', 'end_time', 'all_day', 'timezone',
            'status', 'organizer_email', 'organizer_name',
            'recurring_event_id', 'html_link', 'conference_link',
            'raw_json', 'importance', 'triage_notes', 'triaged',
            'contact_id', 'created_at', 'updated_at'
        }
        self.assertTrue(expected.issubset(col_names))

    def test_create_calendar_attendees_table(self):
        """calendar_attendees table should have all required columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_attendees)").fetchall()
        col_names = {c['name'] for c in cols}
        expected = {
            'id', 'event_id', 'email', 'display_name',
            'response_status', 'organizer', 'self', 'contact_id'
        }
        self.assertTrue(expected.issubset(col_names))

    def test_create_calendar_reminders_table(self):
        """calendar_reminders table should have all required columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_reminders)").fetchall()
        col_names = {c['name'] for c in cols}
        expected = {'id', 'event_id', 'remind_at', 'lead_minutes', 'sent', 'sent_at'}
        self.assertTrue(expected.issubset(col_names))

    def test_seed_accounts(self):
        """Should seed 3 Google Calendar accounts."""
        from create_calendar_tables import create_tables, seed_accounts
        create_tables(self.db)
        seed_accounts(self.db)

        accounts = self.db.execute("SELECT * FROM calendar_accounts ORDER BY id").fetchall()
        self.assertEqual(len(accounts), 3)
        self.assertEqual(accounts[0]['id'], 'gcal1')
        self.assertEqual(accounts[0]['email'], 'leo11lau@gmail.com')
        self.assertEqual(accounts[1]['id'], 'gcal2')
        self.assertEqual(accounts[1]['email'], 'leolau@joyaether.com')
        self.assertEqual(accounts[2]['id'], 'gcal3')
        self.assertEqual(accounts[2]['email'], 'leolau@snappopapp.com')

    def test_idempotent_creation(self):
        """Running create_tables twice should not fail."""
        from create_calendar_tables import create_tables, seed_accounts
        create_tables(self.db)
        seed_accounts(self.db)
        create_tables(self.db)
        seed_accounts(self.db)

        accounts = self.db.execute("SELECT * FROM calendar_accounts").fetchall()
        self.assertEqual(len(accounts), 3)


class TestStoreCredentialResolution(unittest.TestCase):
    """Calendar accounts resolve OAuth material from the unified store."""

    def setUp(self):
        import calendar_poller
        import google_oauth
        self.poller = calendar_poller
        self.helper = google_oauth
        google_oauth._token_cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write_legacy(self, email, **overrides):
        payload = {
            'client_id': f'client-for-{email}',
            'client_secret': f'secret-for-{email}',
            'refresh_token': f'refresh-for-{email}',
            'scopes': ['https://www.googleapis.com/auth/calendar'],
        }
        payload.update(overrides)
        with open(os.path.join(self.tmp, f'{email}.json'), 'w', encoding='utf-8') as handle:
            json.dump(payload, handle)

    def test_store_entry_wins(self):
        entry = self.helper.GoogleCredentials(
            'store-client', 'store-secret', 'store-refresh', 'owner-1', 'x@y.co'
        )
        with patch.object(self.helper, 'store_accounts', return_value=[entry]):
            creds = self.poller.credentials_for_account(
                {'id': 'gcal1', 'email': 'x@y.co'}, {}
            )
        self.assertEqual(creds.refresh_token, 'store-refresh')
        self.assertEqual(creds.owner_user_id, 'owner-1')

    def test_store_without_matching_entry_is_none(self):
        with patch.object(self.helper, 'store_accounts', return_value=[]):
            creds = self.poller.credentials_for_account(
                {'id': 'gcal9', 'email': 'nobody@example.com'}, {}
            )
        self.assertIsNone(creds)

    def test_legacy_file_fallback_when_store_unavailable(self):
        self._write_legacy('leo11lau@gmail.com')
        with patch.object(self.helper, 'store_accounts', return_value=None), \
             patch.dict(os.environ, {'WORKSPACE_MCP_CREDENTIALS_DIR': self.tmp}):
            creds = self.poller.credentials_for_account(
                {'id': 'gcal1', 'email': 'leo11lau@gmail.com'}, {}
            )
        self.assertEqual(creds.client_id, 'client-for-leo11lau@gmail.com')
        self.assertEqual(creds.refresh_token, 'refresh-for-leo11lau@gmail.com')

    def test_legacy_missing_or_unscoped_is_none(self):
        self._write_legacy('docs@example.com', scopes=[
            'https://www.googleapis.com/auth/documents'
        ])
        with patch.object(self.helper, 'store_accounts', return_value=None), \
             patch.dict(os.environ, {'WORKSPACE_MCP_CREDENTIALS_DIR': self.tmp}):
            self.assertIsNone(self.poller.credentials_for_account(
                {'id': 'gcal1', 'email': 'absent@example.com'}, {}
            ))
            self.assertIsNone(self.poller.credentials_for_account(
                {'id': 'gcal1', 'email': 'docs@example.com'}, {}
            ))


class TestAccessTokenCache(unittest.TestCase):
    """Access tokens cache and persist rotation through the store."""

    def setUp(self):
        import google_oauth
        self.helper = google_oauth
        google_oauth._token_cache.clear()

    @patch('hermes_cli.google_oauth.refresh_access_token')
    def test_refresh_and_cache(self, mock_refresh):
        mock_refresh.return_value = {'access_token': 'ya29.t1', 'expires_in': 3600}
        cred = self.helper.GoogleCredentials('cid', 'sec', 'ref', '', 'x@y.co')
        t1 = self.helper.get_access_token(cred)
        t2 = self.helper.get_access_token(cred)
        self.assertEqual(t1, 'ya29.t1')
        self.assertEqual(t1, t2)
        mock_refresh.assert_called_once()

    @patch('hermes_cli.google_oauth.refresh_access_token')
    def test_invalidate_forces_refresh(self, mock_refresh):
        mock_refresh.side_effect = [
            {'access_token': 'ya29.a', 'expires_in': 3600},
            {'access_token': 'ya29.b', 'expires_in': 3600},
        ]
        cred = self.helper.GoogleCredentials('cid', 'sec', 'ref', '', 'x@y.co')
        self.helper.get_access_token(cred)
        self.helper.invalidate(cred)
        self.assertEqual(self.helper.get_access_token(cred), 'ya29.b')
        self.assertEqual(mock_refresh.call_count, 2)

    def test_xoauth2_string(self):
        self.assertEqual(
            self.helper.xoauth2_string('u@x.co', 'tok'),
            'user=u@x.co\x01auth=Bearer tok\x01\x01',
        )


if __name__ == '__main__':
    unittest.main()
