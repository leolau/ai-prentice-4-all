#!/usr/bin/env python3
"""
Phase 1 Tests: Calendar DB schema + OAuth2 token refresh.
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


class TestOAuthTokenRefresh(unittest.TestCase):
    """Test OAuth2 token refresh logic."""

    @patch('calendar_poller.urlopen')
    def test_refresh_access_token(self, mock_urlopen):
        """Should exchange refresh token for access token."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            'access_token': 'ya29.test-access-token',
            'expires_in': 3600,
            'token_type': 'Bearer'
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        import calendar_poller
        calendar_poller.GCAL_CLIENT_ID = 'test-client-id'
        calendar_poller.GCAL_CLIENT_SECRET = 'test-client-secret'
        calendar_poller._token_cache = {}

        token = calendar_poller.get_access_token('gcal1', 'test-refresh-token')
        self.assertEqual(token, 'ya29.test-access-token')
        mock_urlopen.assert_called_once()

    @patch('calendar_poller.urlopen')
    def test_token_caching(self, mock_urlopen):
        """Should cache access tokens and not re-request until expired."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            'access_token': 'ya29.cached-token',
            'expires_in': 3600,
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        import calendar_poller
        calendar_poller.GCAL_CLIENT_ID = 'test-client-id'
        calendar_poller.GCAL_CLIENT_SECRET = 'test-client-secret'
        calendar_poller._token_cache = {}

        token1 = calendar_poller.get_access_token('gcal_cache_test', 'test-refresh')
        token2 = calendar_poller.get_access_token('gcal_cache_test', 'test-refresh')

        self.assertEqual(token1, token2)
        self.assertEqual(mock_urlopen.call_count, 1)  # Only one API call


class TestWorkspaceCredentials(unittest.TestCase):
    """Reusing the Workspace MCP OAuth store instead of GCAL_* env vars."""

    def setUp(self):
        import calendar_poller
        self.poller = calendar_poller
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._dir = calendar_poller.WORKSPACE_CREDENTIALS_DIR
        self._id = calendar_poller.GCAL_CLIENT_ID
        self._secret = calendar_poller.GCAL_CLIENT_SECRET
        calendar_poller.WORKSPACE_CREDENTIALS_DIR = self.tmp
        calendar_poller.GCAL_CLIENT_ID = ''
        calendar_poller.GCAL_CLIENT_SECRET = ''
        calendar_poller._token_cache = {}

    def tearDown(self):
        self.poller.WORKSPACE_CREDENTIALS_DIR = self._dir
        self.poller.GCAL_CLIENT_ID = self._id
        self.poller.GCAL_CLIENT_SECRET = self._secret

    def _write(self, email, **overrides):
        payload = {
            'client_id': f'client-for-{email}',
            'client_secret': f'secret-for-{email}',
            'refresh_token': f'refresh-for-{email}',
            'scopes': ['https://www.googleapis.com/auth/calendar'],
        }
        payload.update(overrides)
        with open(os.path.join(self.tmp, f'{email}.json'), 'w', encoding='utf-8') as handle:
            json.dump(payload, handle)

    def test_reads_the_workspace_credential_file(self):
        """Each account's own OAuth client travels with its refresh token."""
        self._write('leo11lau@gmail.com')
        creds = self.poller.workspace_credentials('leo11lau@gmail.com')
        self.assertEqual(creds.client_id, 'client-for-leo11lau@gmail.com')
        self.assertEqual(creds.client_secret, 'secret-for-leo11lau@gmail.com')
        self.assertEqual(creds.refresh_token, 'refresh-for-leo11lau@gmail.com')

    def test_missing_or_unscoped_credential_is_none(self):
        """No file, or consent without the calendar scope, yields nothing."""
        self.assertIsNone(self.poller.workspace_credentials('absent@example.com'))
        self._write('docs@example.com', scopes=[
            'https://www.googleapis.com/auth/documents'
        ])
        self.assertIsNone(self.poller.workspace_credentials('docs@example.com'))

    def test_account_falls_back_to_the_workspace_store(self):
        """With no GCAL_* provisioning the account still resolves."""
        self._write('leolau@joyaether.com')
        account = {'id': 'gcal2', 'email': 'leolau@joyaether.com'}
        creds = self.poller.credentials_for_account(account, {})
        self.assertEqual(creds.refresh_token, 'refresh-for-leolau@joyaether.com')

    def test_explicit_env_token_wins(self):
        """An operator pointing one account elsewhere is not overridden."""
        self._write('leolau@joyaether.com')
        self.poller.GCAL_CLIENT_ID = 'env-client'
        self.poller.GCAL_CLIENT_SECRET = 'env-secret'
        account = {'id': 'gcal2', 'email': 'leolau@joyaether.com'}
        with patch.dict(os.environ, {'GCAL_REFRESH_TOKEN_2': 'env-refresh'}):
            creds = self.poller.credentials_for_account(account, {})
        self.assertEqual(creds.refresh_token, 'env-refresh')
        self.assertEqual(creds.client_id, 'env-client')

    def test_account_without_any_credential_is_skipped(self):
        account = {'id': 'gcal9', 'email': 'nobody@example.com'}
        self.assertIsNone(self.poller.credentials_for_account(account, {}))

    @patch('calendar_poller.urlopen')
    def test_refresh_uses_the_accounts_own_client(self, mock_urlopen):
        """Refreshing with another account's client id would 401."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            'access_token': 'ya29.per-account',
            'expires_in': 3600,
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        creds = self.poller.GoogleCredentials('cid', 'csecret', 'rtoken')
        token = self.poller.get_access_token('gcal3', creds)

        self.assertEqual(token, 'ya29.per-account')
        body = mock_urlopen.call_args[0][0].data.decode()
        self.assertIn('client_id=cid', body)
        self.assertIn('refresh_token=rtoken', body)


if __name__ == '__main__':
    unittest.main()
