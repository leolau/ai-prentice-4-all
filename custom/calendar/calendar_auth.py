#!/usr/bin/env python3
"""
Google OAuth2 Authentication Flow (Calendar, Docs, Drive)

One-time script to obtain refresh tokens for each Google account.
Run locally (not on the server) — it opens a browser for consent.

Usage:
    python3 calendar_auth.py --email leo11lau@gmail.com
    python3 calendar_auth.py --email leo11lau@gmail.com \
        --scopes calendar documents drive.file \
        --credentials-out ~/gcreds

Environment variables required:
    GCAL_CLIENT_ID      - OAuth2 client ID from Google Cloud Console
    GCAL_CLIENT_SECRET  - OAuth2 client secret

The script will:
1. Open a browser for the user to approve access
2. Listen on localhost for the OAuth callback
3. Exchange the auth code for access + refresh tokens
4. Print the refresh token, and optionally write a credential file in the
   format the Google Workspace MCP server reads
"""

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone

SCOPE_PREFIX = 'https://www.googleapis.com/auth/'
DEFAULT_SCOPES = ('calendar',)
REDIRECT_PORT = 8090
REDIRECT_URI = f'http://localhost:{REDIRECT_PORT}'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'


def resolve_scopes(scopes):
    """Expand shorthand scope names to full Google scope URLs.

    'calendar' -> 'https://www.googleapis.com/auth/calendar'. Values that are
    already URLs are passed through unchanged.
    """
    return [s if '://' in s else SCOPE_PREFIX + s for s in scopes]


def get_auth_url(client_id, email, scopes=DEFAULT_SCOPES):
    """Build the OAuth2 authorization URL."""
    params = urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(resolve_scopes(scopes)),
        'access_type': 'offline',
        'prompt': 'consent',
        'login_hint': email,
    })
    return f'{AUTH_URL}?{params}'


def exchange_code(code, client_id, client_secret):
    """Exchange authorization code for tokens."""
    data = urllib.parse.urlencode({
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code',
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def refresh_access_token(refresh_token, client_id, client_secret):
    """Get a new access token using a refresh token."""
    data = urllib.parse.urlencode({
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token',
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode())
        return result.get('access_token')


def credential_document(tokens, client_id, client_secret, scopes):
    """Build the credential dict the Google Workspace MCP server stores per account.

    Mirrors auth/credential_store.py in taylorwilsdon/google_workspace_mcp:
    token, refresh_token, token_uri, client_id, client_secret, scopes, expiry.
    """
    expires_in = tokens.get('expires_in')
    expiry = None
    if expires_in:
        # Naive UTC: what google.oauth2.credentials.Credentials expects.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expiry = (now + timedelta(seconds=int(expires_in))).isoformat()
    return {
        'token': tokens.get('access_token'),
        'refresh_token': tokens.get('refresh_token'),
        'token_uri': TOKEN_URL,
        'client_id': client_id,
        'client_secret': client_secret,
        'scopes': resolve_scopes(scopes),
        'expiry': expiry,
    }


def credential_filename(email):
    """Filename the Workspace MCP credential store looks the account up under."""
    return urllib.parse.quote(email, safe='@._-') + '.json'


def write_credential_file(path, document):
    """Write a credential document to ``path`` with 0600 permissions."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(document, f, indent=2)
    return path


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles the OAuth2 redirect callback."""
    auth_code = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if 'code' in params:
            OAuthCallbackHandler.auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h2>Authorization successful!</h2>'
                             b'<p>You can close this tab and return to the terminal.</p>'
                             b'</body></html>')
        elif 'error' in params:
            self.send_response(400)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            error = params.get('error', ['unknown'])[0]
            self.wfile.write(f'<html><body><h2>Error: {error}</h2></body></html>'.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress HTTP logs


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Google OAuth2 auth flow')
    parser.add_argument('--email', required=True, help='Google account email')
    parser.add_argument(
        '--scopes', nargs='+', default=list(DEFAULT_SCOPES),
        help="Scopes to request, shorthand ('calendar', 'documents', "
             "'drive.file') or full URLs. Default: calendar",
    )
    parser.add_argument(
        '--credentials-out', metavar='DIR',
        help='Write <email>.json (mode 0600) in Workspace MCP credential '
             'format into this directory, alongside printing the token',
    )
    args = parser.parse_args()

    client_id = os.environ.get('GCAL_CLIENT_ID', '')
    client_secret = os.environ.get('GCAL_CLIENT_SECRET', '')

    if not client_id or not client_secret:
        print("ERROR: Set GCAL_CLIENT_ID and GCAL_CLIENT_SECRET environment variables")
        sys.exit(1)

    auth_url = get_auth_url(client_id, args.email, args.scopes)
    print("Requesting scopes:")
    for scope in resolve_scopes(args.scopes):
        print(f"  {scope}")
    print(f"\nOpening browser for {args.email}...")
    print(f"If the browser doesn't open, visit this URL:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print(f"Waiting for OAuth callback on port {REDIRECT_PORT}...")
    server = http.server.HTTPServer(('localhost', REDIRECT_PORT), OAuthCallbackHandler)
    server.handle_request()

    if not OAuthCallbackHandler.auth_code:
        print("ERROR: No authorization code received")
        sys.exit(1)

    print("Exchanging code for tokens...")
    tokens = exchange_code(OAuthCallbackHandler.auth_code, client_id, client_secret)

    if 'error' in tokens:
        print(f"ERROR: {tokens['error']} - {tokens.get('error_description', '')}")
        sys.exit(1)

    refresh_token = tokens.get('refresh_token', '')
    access_token = tokens.get('access_token', '')

    if not refresh_token:
        print("WARNING: No refresh token received. You may need to revoke access and re-authorize.")
        print("Go to https://myaccount.google.com/permissions and remove 'Hermes Agent'")

    print(f"\n{'='*60}")
    print(f"Account: {args.email}")
    print(f"Refresh Token: {refresh_token}")
    print(f"{'='*60}")
    print(f"\nSave this refresh token as a secret.")
    print(f"Access token (expires in {tokens.get('expires_in', '?')}s): {access_token[:20]}...")

    if args.credentials_out:
        out_dir = os.path.expanduser(args.credentials_out)
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
        path = write_credential_file(
            os.path.join(out_dir, credential_filename(args.email)),
            credential_document(tokens, client_id, client_secret, args.scopes),
        )
        print(f"\nCredential file written to {path} (mode 0600).")
        print("It contains the refresh token and the client secret — do not commit it.")


if __name__ == '__main__':
    main()
