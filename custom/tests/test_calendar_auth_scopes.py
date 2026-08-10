#!/usr/bin/env python3
"""
Tests for multi-scope consent and Workspace MCP credential output in
custom/calendar/calendar_auth.py.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'calendar'))

import calendar_auth


class TestScopeResolution(unittest.TestCase):

    def test_shorthand_expands_to_google_scope_url(self):
        self.assertEqual(
            calendar_auth.resolve_scopes(['calendar', 'documents', 'drive.file']),
            [
                'https://www.googleapis.com/auth/calendar',
                'https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/drive.file',
            ],
        )

    def test_full_urls_pass_through_unchanged(self):
        url = 'https://www.googleapis.com/auth/calendar.readonly'
        self.assertEqual(calendar_auth.resolve_scopes([url]), [url])

    def test_default_stays_calendar_only(self):
        self.assertEqual(
            calendar_auth.resolve_scopes(calendar_auth.DEFAULT_SCOPES),
            ['https://www.googleapis.com/auth/calendar'],
        )

    def test_auth_url_requests_every_scope_space_separated(self):
        url = calendar_auth.get_auth_url(
            'client-id', 'someone@example.com', ['calendar', 'documents']
        )
        scope = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['scope'][0]
        self.assertEqual(
            scope.split(' '),
            [
                'https://www.googleapis.com/auth/calendar',
                'https://www.googleapis.com/auth/documents',
            ],
        )

    def test_auth_url_asks_for_a_refresh_token(self):
        """offline + consent is what makes Google return a refresh token."""
        url = calendar_auth.get_auth_url('client-id', 'someone@example.com')
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(params['access_type'], ['offline'])
        self.assertEqual(params['prompt'], ['consent'])


class TestCredentialDocument(unittest.TestCase):

    def test_document_carries_every_field_the_mcp_store_reads(self):
        doc = calendar_auth.credential_document(
            {'access_token': 'ya29.tok', 'refresh_token': '1//rt', 'expires_in': 3600},
            'client-id',
            'client-secret',
            ['calendar', 'documents'],
        )
        self.assertEqual(
            set(doc),
            {'token', 'refresh_token', 'token_uri', 'client_id',
             'client_secret', 'scopes', 'expiry'},
        )
        self.assertEqual(doc['refresh_token'], '1//rt')
        self.assertEqual(doc['token_uri'], calendar_auth.TOKEN_URL)
        self.assertEqual(doc['scopes'], calendar_auth.resolve_scopes(
            ['calendar', 'documents']))

    def test_expiry_is_none_when_google_omits_expires_in(self):
        doc = calendar_auth.credential_document(
            {'access_token': 'ya29.tok', 'refresh_token': '1//rt'},
            'client-id', 'client-secret', ['calendar'],
        )
        self.assertIsNone(doc['expiry'])

    def test_filename_matches_the_stores_url_encoded_form(self):
        self.assertEqual(
            calendar_auth.credential_filename('leo11lau@gmail.com'),
            'leo11lau@gmail.com.json',
        )
        self.assertEqual(
            calendar_auth.credential_filename('a+b@example.com'),
            'a%2Bb@example.com.json',
        )

    def test_credential_file_is_owner_read_write_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'creds.json')
            calendar_auth.write_credential_file(path, {'refresh_token': '1//rt'})
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)
            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f), {'refresh_token': '1//rt'})


if __name__ == '__main__':
    unittest.main()
