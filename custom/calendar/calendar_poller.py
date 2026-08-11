#!/usr/bin/env python3
"""
Google Calendar Poller for Hermes Agent

Polls multiple Google Calendar accounts via the Calendar API.
Uses incremental sync (syncToken) to fetch only changed events.
Stores events, attendees, and schedules reminders in SQLite.
"""

import json
import os
import sqlite3
import sys
import time
import uuid
import re
import traceback
import threading
from collections import namedtuple
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

DB_PATH = os.environ.get('DB_PATH', '/opt/data/whatsapp-messages/whatsapp_data.db')
CONFIG_PATH = os.environ.get('CALENDAR_CONFIG_PATH', '/opt/data/calendar/config.json')
HEALTH_PORT = int(os.environ.get('CALENDAR_HEALTH_PORT', '7903'))

GCAL_CLIENT_ID = os.environ.get('GCAL_CLIENT_ID', '')
GCAL_CLIENT_SECRET = os.environ.get('GCAL_CLIENT_SECRET', '')

#: Where the Google Workspace MCP server keeps its per-account OAuth files.
#: The agent already runs that server against these calendars, so the tokens
#: it holds are the ones this poller needs; a second GCAL_* provisioning was
#: only ever a way to have the same consent twice.
WORKSPACE_CREDENTIALS_DIR = os.environ.get(
    'WORKSPACE_MCP_CREDENTIALS_DIR',
    os.path.join(
        os.environ.get('HERMES_HOME', os.path.expanduser('~')),
        'google-workspace',
        'credentials',
    ),
)
CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar'

TOKEN_URL = 'https://oauth2.googleapis.com/token'
CALENDAR_API_BASE = 'https://www.googleapis.com/calendar/v3'

# Default reminder lead times by importance (minutes before event)
DEFAULT_REMINDERS = {
    'critical': [1440, 60, 15],
    'normal': [60, 15],
    'low': [15],
}

# Cache access tokens per account (token, expiry_time)
_token_cache = {}

#: One account's OAuth material. The client pair travels with the refresh
#: token because each Workspace account was consented under its own OAuth
#: client — refreshing account B's token with account A's client id fails.
GoogleCredentials = namedtuple(
    'GoogleCredentials', ('client_id', 'client_secret', 'refresh_token')
)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def load_config():
    """Load calendar config. Falls back to env-var-based defaults if no config file."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def as_credentials(credentials):
    """Coerce a bare refresh token into a full :class:`GoogleCredentials`.

    Callers that only have a token fall back to the process-wide GCAL_*
    client pair, which is how the env-var provisioning has always worked.
    """
    if isinstance(credentials, GoogleCredentials):
        return credentials
    return GoogleCredentials(GCAL_CLIENT_ID, GCAL_CLIENT_SECRET, credentials or '')


def workspace_credentials(email):
    """Read one account's OAuth material from the Workspace MCP store.

    Returns ``None`` when there is no file for the address, when it is
    unreadable, or when the consent it records does not cover Calendar.
    """
    if not email:
        return None
    path = os.path.join(WORKSPACE_CREDENTIALS_DIR, f'{email}.json')
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None

    scopes = data.get('scopes') or []
    if scopes and CALENDAR_SCOPE not in scopes:
        print(f"[calendar] {email}: workspace credential lacks the calendar scope")
        return None

    creds = GoogleCredentials(
        data.get('client_id', '') or GCAL_CLIENT_ID,
        data.get('client_secret', '') or GCAL_CLIENT_SECRET,
        data.get('refresh_token', ''),
    )
    if not (creds.client_id and creds.client_secret and creds.refresh_token):
        return None
    return creds


def get_access_token(account_id, credentials):
    """Get a valid access token, refreshing if needed."""
    cached = _token_cache.get(account_id)
    if cached:
        token, expiry = cached
        if datetime.now(timezone.utc) < expiry - timedelta(minutes=2):
            return token

    creds = as_credentials(credentials)
    if not creds.client_id or not creds.client_secret:
        raise RuntimeError(
            f"No OAuth client for account {account_id}: set GCAL_CLIENT_ID and "
            f"GCAL_CLIENT_SECRET, or provide a workspace credential file"
        )
    if not creds.refresh_token:
        raise RuntimeError(f"No refresh token for account {account_id}")

    data = urlencode({
        'refresh_token': creds.refresh_token,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'grant_type': 'refresh_token',
    }).encode()

    req = Request(TOKEN_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    with urlopen(req) as resp:
        result = json.loads(resp.read().decode())

    access_token = result['access_token']
    expires_in = result.get('expires_in', 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    _token_cache[account_id] = (access_token, expiry)

    return access_token


def gcal_api(access_token, path, params=None):
    """Make a Google Calendar API request."""
    url = f'{CALENDAR_API_BASE}{path}'
    if params:
        url += '?' + urlencode(params)

    req = Request(url)
    req.add_header('Authorization', f'Bearer {access_token}')
    req.add_header('Accept', 'application/json')

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode()
        print(f"[calendar] API error {e.code}: {body}")
        raise


def extract_conference_link(event):
    """Extract video conference link from event data."""
    # Check conferenceData first (structured)
    conf_data = event.get('conferenceData', {})
    for entry_point in conf_data.get('entryPoints', []):
        if entry_point.get('entryPointType') == 'video':
            return entry_point.get('uri', '')

    # Check hangoutLink
    hangout = event.get('hangoutLink', '')
    if hangout:
        return hangout

    # Check description and location for Zoom/Teams/Meet links
    text = (event.get('description', '') or '') + ' ' + (event.get('location', '') or '')
    patterns = [
        r'https?://[a-z0-9]+\.zoom\.us/j/\S+',
        r'https?://meet\.google\.com/\S+',
        r'https?://teams\.microsoft\.com/\S+',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)

    return ''


def parse_event_time(event):
    """Parse event start/end times. Returns (start_iso, end_iso, all_day, tz)."""
    start = event.get('start', {})
    end = event.get('end', {})

    if 'date' in start:
        # All-day event
        return start['date'], end.get('date', start['date']), True, start.get('timeZone', '')
    else:
        return (
            start.get('dateTime', ''),
            end.get('dateTime', ''),
            False,
            start.get('timeZone', '')
        )


def sync_events(db, account_id, credentials, calendar_id='primary'):
    """Sync events from a single calendar using incremental sync."""
    access_token = get_access_token(account_id, credentials)
    now = datetime.now(timezone.utc)

    # Get existing sync token
    account = db.execute(
        "SELECT sync_token FROM calendar_accounts WHERE id = ?",
        (account_id,)
    ).fetchone()

    sync_token = account['sync_token'] if account else None

    all_events = []
    page_token = None
    new_sync_token = None

    # Google rejects a pageToken whose request does not repeat the query it
    # was issued for ("Invalid page token value"), so the window is built once
    # and every page reuses it — a calendar with more than one page of events
    # used to fail on page two and sync nothing.
    base_params = {'maxResults': 250, 'singleEvents': True}
    if sync_token:
        base_params['syncToken'] = sync_token
    else:
        # Full sync: get events from 30 days ago to 365 days ahead
        base_params['timeMin'] = (now - timedelta(days=30)).isoformat()
        base_params['timeMax'] = (now + timedelta(days=365)).isoformat()
        base_params['orderBy'] = 'startTime'

    while True:
        params = dict(base_params)
        if page_token:
            params['pageToken'] = page_token

        try:
            result = gcal_api(access_token, f'/calendars/{calendar_id}/events', params)
        except HTTPError as e:
            if e.code == 410:
                # Sync token expired — do a full sync
                print(f"[calendar] Sync token expired for {account_id}, doing full sync")
                db.execute(
                    "UPDATE calendar_accounts SET sync_token = NULL WHERE id = ?",
                    (account_id,)
                )
                db.commit()
                return sync_events(db, account_id, credentials, calendar_id)
            raise

        items = result.get('items', [])
        all_events.extend(items)

        page_token = result.get('nextPageToken')
        if not page_token:
            new_sync_token = result.get('nextSyncToken')
            break

    # Process events
    created = 0
    updated = 0
    cancelled = 0

    for event in all_events:
        google_event_id = event.get('id', '')
        status = event.get('status', 'confirmed')

        if status == 'cancelled':
            # Mark as cancelled in DB
            rows = db.execute(
                """UPDATE calendar_events SET status = 'cancelled', updated_at = ?
                   WHERE google_event_id = ? AND account_id = ?""",
                (now.isoformat(), google_event_id, account_id)
            ).rowcount
            if rows > 0:
                cancelled += 1
            continue

        start_time, end_time, all_day, tz = parse_event_time(event)
        conference_link = extract_conference_link(event)
        organizer = event.get('organizer', {})

        # Check if event already exists
        existing = db.execute(
            "SELECT id FROM calendar_events WHERE google_event_id = ? AND account_id = ?",
            (google_event_id, account_id)
        ).fetchone()

        event_id = existing['id'] if existing else str(uuid.uuid4())

        if existing:
            # Update existing event
            db.execute(
                """UPDATE calendar_events SET
                    summary = ?, description = ?, location = ?,
                    start_time = ?, end_time = ?, all_day = ?, timezone = ?,
                    status = ?, organizer_email = ?, organizer_name = ?,
                    recurring_event_id = ?, html_link = ?, conference_link = ?,
                    raw_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    event.get('summary', ''),
                    event.get('description', ''),
                    event.get('location', ''),
                    start_time, end_time, int(all_day), tz,
                    status,
                    organizer.get('email', ''),
                    organizer.get('displayName', ''),
                    event.get('recurringEventId', ''),
                    event.get('htmlLink', ''),
                    conference_link,
                    json.dumps(event),
                    now.isoformat(),
                    event_id
                )
            )
            updated += 1
        else:
            # Insert new event
            db.execute(
                """INSERT INTO calendar_events
                   (id, google_event_id, account_id, calendar_id,
                    summary, description, location,
                    start_time, end_time, all_day, timezone,
                    status, organizer_email, organizer_name,
                    recurring_event_id, html_link, conference_link,
                    raw_json, triaged, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    event_id, google_event_id, account_id, calendar_id,
                    event.get('summary', ''),
                    event.get('description', ''),
                    event.get('location', ''),
                    start_time, end_time, int(all_day), tz,
                    status,
                    organizer.get('email', ''),
                    organizer.get('displayName', ''),
                    event.get('recurringEventId', ''),
                    event.get('htmlLink', ''),
                    conference_link,
                    json.dumps(event),
                    now.isoformat(), now.isoformat()
                )
            )
            created += 1

        # Mirror into the shared inbound registry, on both the create and the
        # update branch: the upsert keys on the Google event id, so a
        # rescheduled meeting moves in the Inbox instead of appearing twice.
        try:
            from shared.inbound_registration import register_item
            register_item(
                surface='calendar',
                external_id=google_event_id,
                account_id=account_id,
                kind='event',
                conversation=event.get('recurringEventId') or google_event_id,
                conversation_name=calendar_id,
                sender_id=organizer.get('email', ''),
                sender_name=organizer.get('displayName', ''),
                subject=event.get('summary', ''),
                body=event.get('description', '') or '',
                occurred_at=start_time,
                ends_at=end_time,
                metadata={
                    'location': event.get('location', ''),
                    'html_link': event.get('htmlLink', ''),
                    'conference_link': conference_link,
                    'status': status,
                    'all_day': bool(all_day),
                },
            )
        except ImportError:
            pass
        except Exception as e:
            print(f"[calendar] Inbound registration failed for {google_event_id}: {e}")

        # Sync attendees
        sync_attendees(db, event_id, event.get('attendees', []))

    # Update sync token
    if new_sync_token:
        db.execute(
            "UPDATE calendar_accounts SET sync_token = ?, last_synced = ? WHERE id = ?",
            (new_sync_token, now.isoformat(), account_id)
        )

    db.commit()
    return created, updated, cancelled


def sync_attendees(db, event_id, attendees):
    """Sync attendees for a calendar event."""
    # Remove existing attendees
    db.execute("DELETE FROM calendar_attendees WHERE event_id = ?", (event_id,))

    for attendee in attendees:
        db.execute(
            """INSERT INTO calendar_attendees
               (id, event_id, email, display_name, response_status, organizer, self)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                event_id,
                attendee.get('email', ''),
                attendee.get('displayName', ''),
                attendee.get('responseStatus', 'needsAction'),
                int(attendee.get('organizer', False)),
                int(attendee.get('self', False)),
            )
        )


def get_refresh_token_for_account(account_config):
    """Get refresh token from env var specified in config."""
    env_var = account_config.get('refresh_token_env', '')
    if env_var:
        return os.environ.get(env_var, '')

    # Fallback: try numbered env vars
    account_id = account_config.get('id', '')
    num = account_id.replace('gcal', '')
    return os.environ.get(f'GCAL_REFRESH_TOKEN_{num}', '')


def credentials_for_account(account, account_config):
    """Resolve one account's OAuth material, or ``None`` if it has none.

    An explicit GCAL_* refresh token wins, so an operator can point a single
    account somewhere else; otherwise the Workspace MCP store is used, which
    is where this box's Google consent actually lives.
    """
    account_id = account['id']
    refresh_token = get_refresh_token_for_account(account_config) or os.environ.get(
        f"GCAL_REFRESH_TOKEN_{account_id.replace('gcal', '')}", ''
    )
    if refresh_token and GCAL_CLIENT_ID and GCAL_CLIENT_SECRET:
        return GoogleCredentials(GCAL_CLIENT_ID, GCAL_CLIENT_SECRET, refresh_token)

    creds = workspace_credentials(account['email'])
    if creds:
        return creds

    # A token with no client pair is unusable on its own, but saying so is
    # more useful than the generic "no refresh token" below.
    if refresh_token:
        print(
            f"[calendar] {account_id} ({account['email']}): refresh token set but "
            f"GCAL_CLIENT_ID/GCAL_CLIENT_SECRET are not"
        )
    return None


def poll_all_accounts(db, config):
    """Poll all enabled calendar accounts."""
    accounts = db.execute(
        "SELECT * FROM calendar_accounts WHERE enabled = 1"
    ).fetchall()

    cal_config = config.get('calendar', {})
    account_configs = {a['id']: a for a in cal_config.get('accounts', [])}

    total_created = 0
    total_updated = 0
    total_cancelled = 0

    for account in accounts:
        acc_config = account_configs.get(account['id'], {})
        credentials = credentials_for_account(account, acc_config)

        if not credentials:
            print(
                f"[calendar] Skipping {account['id']} ({account['email']}): no "
                f"credentials in GCAL_* or {WORKSPACE_CREDENTIALS_DIR}"
            )
            continue

        calendars = acc_config.get('calendars', ['primary'])
        for cal_id in calendars:
            try:
                created, updated, cancelled = sync_events(
                    db, account['id'], credentials, cal_id
                )
                total_created += created
                total_updated += updated
                total_cancelled += cancelled

                if created or updated or cancelled:
                    print(f"[calendar] {account['email']}/{cal_id}: "
                          f"+{created} new, ~{updated} updated, -{cancelled} cancelled")
            except Exception as e:
                print(f"[calendar] Error syncing {account['email']}/{cal_id}: {e}")
                traceback.print_exc()

    return total_created, total_updated, total_cancelled


class HealthHandler(BaseHTTPRequestHandler):
    """Health check endpoint."""
    poll_count = 0
    last_poll = None
    last_error = None

    def do_GET(self):
        if self.path == '/health':
            status = {
                'status': 'ok',
                'service': 'calendar-poller',
                'poll_count': HealthHandler.poll_count,
                'last_poll': HealthHandler.last_poll,
                'last_error': HealthHandler.last_error,
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server():
    """Start health check HTTP server in a background thread."""
    server = HTTPServer(('0.0.0.0', HEALTH_PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[calendar] Health check: http://localhost:{HEALTH_PORT}/health")


def main():
    if not (GCAL_CLIENT_ID and GCAL_CLIENT_SECRET) and not os.path.isdir(
        WORKSPACE_CREDENTIALS_DIR
    ):
        print(
            "[calendar] ERROR: no credentials — set GCAL_CLIENT_ID and "
            f"GCAL_CLIENT_SECRET, or populate {WORKSPACE_CREDENTIALS_DIR}"
        )
        return

    config = load_config()
    poll_interval = config.get('calendar', {}).get('poll_interval_seconds', 60)

    start_health_server()
    print(f"[calendar] Starting calendar poller (interval: {poll_interval}s)")

    while True:
        try:
            db = get_db()
            created, updated, cancelled = poll_all_accounts(db, config)
            db.close()

            HealthHandler.poll_count += 1
            HealthHandler.last_poll = datetime.now(timezone.utc).isoformat()
            HealthHandler.last_error = None

            if created or updated or cancelled:
                print(f"[calendar] Poll #{HealthHandler.poll_count}: "
                      f"+{created} new, ~{updated} updated, -{cancelled} cancelled")

        except Exception as e:
            HealthHandler.last_error = str(e)
            print(f"[calendar] Poll error: {e}")
            traceback.print_exc()

        time.sleep(poll_interval)


if __name__ == '__main__':
    main()
