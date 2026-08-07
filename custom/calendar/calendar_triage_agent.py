#!/usr/bin/env python3
"""
Calendar Triage Agent for Hermes Agent

Watches for new/updated calendar events (triaged = 0), classifies importance
using DeepSeek, detects conflicts, extracts prep context, and pushes
memory-worthy facts to long-term memory.

Mirrors the WhatsApp/email triage agent pattern:
- Loads skills from /opt/data/skills/calendar-triage/ (per-event, hot-reload)
- Uses the shared handler registry for processing results
- Writes importance/conflicts/prep_notes to calendar_events table
- Pushes memory_facts via the memory bridge
"""

import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from urllib.request import Request, urlopen

# Credit tracking
sys.path.insert(0, '/opt/data')
from track_credit_helper import track_inference

# Shared modules (triage_handlers, memory_bridge)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Paths
DB_PATH = os.environ.get('DB_PATH', '/opt/data/whatsapp-messages/whatsapp_data.db')
CONFIG_PATH = os.environ.get('CALENDAR_CONFIG_PATH', '/opt/data/calendar/config.json')
SKILLS_DIR = '/opt/data/skills/calendar-triage'

DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_BASE_URL = 'https://api.deepseek.com/v1/chat/completions'

# Module-level config fallback (set by main() at startup)
config = {}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def load_config():
    """Load calendar config. Falls back to env-var-based defaults if no config file."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_skills(config=None):
    """Load all skill .md files from configured skill directories.

    Reads skills_dirs from config.json's 'triage' section.
    Falls back to SKILLS_DIR + 'custom' subdirectory.
    """
    import glob
    skills_content = []

    triage_cfg = (config or {}).get('triage', {})
    base_dirs = triage_cfg.get('skills_dirs', [SKILLS_DIR])
    subdirs = triage_cfg.get('skills_subdirs', ['custom'])

    search_dirs = list(base_dirs)
    for bd in base_dirs:
        for sd in subdirs:
            search_dirs.append(os.path.join(bd, sd))

    for sdir in search_dirs:
        if not os.path.isdir(sdir):
            continue
        for f in sorted(glob.glob(os.path.join(sdir, '*.md'))):
            if f.endswith('.disabled'):
                continue
            with open(f) as fh:
                content = fh.read()
            skills_content.append(f"## Skill: {os.path.basename(f)}\n{content}")

    return "\n\n---\n\n".join(skills_content)


def call_deepseek(messages, temperature=0.3, max_tokens=2000):
    """Call DeepSeek Chat API."""
    model = config.get('triage', {}).get('model', 'deepseek-chat')

    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'response_format': {'type': 'json_object'},
    }

    req = Request(
        DEEPSEEK_BASE_URL,
        data=json.dumps(payload).encode(),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
        }
    )

    try:
        def _do_api_call():
            resp = urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())
            return data['choices'][0]['message']['content']
        return track_inference("Calendar processing", _do_api_call)
    except Exception as e:
        print(f"[calendar-triage] DeepSeek API error: {e}")
        return None


def get_untriaged_events(db, limit=10):
    """Get calendar events that haven't been triaged yet."""
    return db.execute(
        """SELECT id, google_event_id, account_id, calendar_id,
                  summary, description, location,
                  start_time, end_time, all_day, timezone,
                  status, organizer_email, organizer_name,
                  recurring_event_id, html_link, conference_link
           FROM calendar_events
           WHERE triaged = 0 AND status = 'confirmed'
           ORDER BY start_time ASC
           LIMIT ?""",
        (limit,)
    ).fetchall()


def get_attendees(db, event_id):
    """Get attendees for a calendar event."""
    return db.execute(
        "SELECT email, display_name, response_status, organizer, self FROM calendar_attendees WHERE event_id = ?",
        (event_id,)
    ).fetchall()


def triage_event(event, attendees, config):
    """Run triage on a single calendar event."""
    skills_text = load_skills(config)

    # Build attendee text
    attendee_list = []
    for a in attendees:
        name = a['display_name'] or a['email']
        status = a['response_status']
        role = ''
        if a['organizer']:
            role = ' (organizer)'
        elif a['self']:
            role = ' (you)'
        attendee_list.append(f"- {name} <{a['email']}> [{status}]{role}")

    attendees_text = "\n".join(attendee_list) if attendee_list else "(no attendees)"

    # Build event text
    all_day = event['all_day'] == 1
    time_info = "All-day" if all_day else f"{event['start_time']} to {event['end_time']}"

    description = event['description'] or '(no description)'
    location = event['location'] or '(no location)'
    conf_link = event['conference_link'] or '(no conference link)'

    system_prompt = f"""You are a calendar triage agent. Your job is to analyze a calendar event and:
1. Classify its importance (critical, normal, or low)
2. Detect scheduling conflicts (if any other events overlap)
3. Extract preparation context (what to prepare before the meeting)
4. Determine if this event should be escalated (pushed to user immediately)
5. Extract any facts worth persisting to long-term memory (meeting patterns, relationships)

{skills_text}

Analyze the event and respond according to the response schema defined in the skills above.
"""

    user_prompt = f"""Triage this calendar event:

Title: {event['summary']}
Time: {time_info}
Location: {location}
Conference: {conf_link}
Organizer: {event['organizer_name']} <{event['organizer_email']}>
Account: {event['account_id']}
Recurring: {'yes' if event['recurring_event_id'] else 'no'}

Description:
{description}

Attendees ({len(attendees)}):
{attendees_text}
"""

    response = call_deepseek([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ])

    if not response:
        return None

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        print(f"[calendar-triage] Failed to parse response: {response[:200]}")
        return None


def process_triage_result(event, attendees, result, db):
    """Write triage results via the handler registry."""
    batch = {
        '_channel': 'calendar',
        'event_id': event['id'],
        'google_event_id': event['google_event_id'],
        'account_id': event['account_id'],
        'summary': event['summary'],
        'organizer_email': event['organizer_email'],
        'organizer_name': event['organizer_name'],
        'start_time': event['start_time'],
    }

    try:
        from shared.triage_handlers import process_result
        process_result(result, batch, db)
    except ImportError:
        # Fallback: just mark as triaged
        now = datetime.now(timezone.utc).isoformat()
        try:
            db.execute(
                "UPDATE calendar_events SET triaged = 1, updated_at = ? WHERE id = ?",
                (now, event['id'])
            )
        except Exception as e:
            print(f"[calendar-triage] Error marking triaged: {e}")

    print(f"[calendar-triage] Result: importance={result.get('importance')}, "
          f"escalate={result.get('escalate')}, "
          f"conflicts={len(result.get('conflicts', []))}, "
          f"memory_facts={len(result.get('memory_facts', []))}")


def ensure_schema(db):
    """Add triage columns to calendar_events if they don't exist."""
    for col, coltype in [
        ('importance', 'TEXT'),
        ('importance_reason', 'TEXT'),
        ('prep_notes', 'TEXT'),
    ]:
        try:
            db.execute(f"ALTER TABLE calendar_events ADD COLUMN {col} {coltype}")
            print(f"[calendar-triage] Added column {col} to calendar_events")
        except sqlite3.OperationalError:
            pass  # Column already exists

    db.commit()


def main():
    global config
    config = load_config()
    poll_interval = config.get('calendar', {}).get('triage_poll_interval', 30)
    model = config.get('triage', {}).get('model', 'deepseek-chat')

    print(f"[calendar-triage] Starting calendar triage agent (model: {model})")
    print(f"[calendar-triage] Skills dir: {SKILLS_DIR}")
    print(f"[calendar-triage] Poll interval: {poll_interval}s")

    # Ensure schema has triage columns
    db = get_db()
    ensure_schema(db)
    db.close()

    while True:
        try:
            db = get_db()

            # Get untriaged events
            events = get_untriaged_events(db, limit=10)

            if events:
                print(f"[calendar-triage] Found {len(events)} untriaged event(s)")

            for event in events:
                event_id = event['id']
                attendees = get_attendees(db, event_id)

                print(f"[calendar-triage] Triaging: {event['summary']} ({event['start_time']})")

                result = triage_event(event, attendees, config)

                if result:
                    process_triage_result(event, attendees, result, db)
                    db.commit()
                else:
                    print(f"[calendar-triage] Failed to triage event {event_id}, will retry")

                time.sleep(1)  # Rate limit between events

            db.close()

        except Exception as e:
            print(f"[calendar-triage] Error: {e}")
            traceback.print_exc()

        time.sleep(poll_interval)


if __name__ == '__main__':
    main()
