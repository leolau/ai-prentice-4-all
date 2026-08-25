#!/usr/bin/env python3
"""Email poller XOAUTH2 authentication tests (no sockets, no network)."""

import base64
import os
import sqlite3
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'email'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import email_poller
import google_oauth

CRED = google_oauth.GoogleCredentials(
    'cid', 'sec', 'ref', 'owner-1', 'alice@gmail.com'
)


def _db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE email_accounts (
            id TEXT PRIMARY KEY, address TEXT, label TEXT,
            last_poll TEXT, last_uid INTEGER, status TEXT)"""
    )
    conn.commit()
    return conn


ACCOUNT = {
    'id': 'gmail1',
    'address': 'alice@gmail.com',
    'imap': {'host': 'imap.gmail.com', 'port': 993, 'tls': True},
    'folders': ['INBOX'],
}


class TestEmailPollerXoauth2(unittest.TestCase):
    def setUp(self):
        email_poller.poll_status['accounts'] = {}
        google_oauth._token_cache.clear()

    @patch.object(email_poller.google_oauth, 'get_access_token', return_value='tok-1')
    @patch.object(email_poller.google_oauth, 'credentials_for_email', return_value=CRED)
    def test_authenticates_with_xoauth2(self, _cred, _tok):
        conn_mock = MagicMock()
        conn_mock.select.return_value = ('OK', [b'0'])
        conn_mock.uid.return_value = ('OK', [b''])
        with patch.object(email_poller.imaplib, 'IMAP4_SSL', return_value=conn_mock):
            new = email_poller.poll_account(ACCOUNT, _db())
        self.assertEqual(new, 0)
        conn_mock.authenticate.assert_called_once()
        mech, responder = conn_mock.authenticate.call_args[0]
        self.assertEqual(mech, 'XOAUTH2')
        decoded = responder(b'').decode()
        self.assertEqual(decoded, 'user=alice@gmail.com\x01auth=Bearer tok-1\x01\x01')
        self.assertEqual(
            email_poller.poll_status['accounts']['gmail1']['auth'], 'xoauth2'
        )

    @patch.object(email_poller.google_oauth, 'credentials_for_email', return_value=None)
    def test_missing_store_entry_skips_account(self, _cred):
        self.assertEqual(email_poller.poll_account(ACCOUNT, _db()), 0)
        self.assertEqual(
            email_poller.poll_status['accounts']['gmail1']['auth'], 'no_credential'
        )

    @patch.object(email_poller.google_oauth, 'invalidate')
    @patch.object(
        email_poller.google_oauth,
        'get_access_token',
        side_effect=['stale', 'fresh'],
    )
    @patch.object(email_poller.google_oauth, 'credentials_for_email', return_value=CRED)
    def test_retries_once_on_auth_failure(self, _cred, tok, invalidate):
        conn_bad = MagicMock()
        conn_bad.authenticate.side_effect = email_poller.imaplib.IMAP4.error('NO')
        conn_good = MagicMock()
        conn_good.select.return_value = ('OK', [b'0'])
        conn_good.uid.return_value = ('OK', [b''])
        with patch.object(
            email_poller.imaplib, 'IMAP4_SSL', side_effect=[conn_bad, conn_good]
        ):
            email_poller.poll_account(ACCOUNT, _db())
        invalidate.assert_called_once_with(CRED)
        self.assertEqual(tok.call_count, 2)
        conn_good.authenticate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
