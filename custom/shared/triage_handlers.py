#!/usr/bin/env python3
"""
Triage Handler Registry

A dispatch table that maps JSON response field names to handler functions.
Each triage agent (WhatsApp, email, calendar) calls process_result() with the
LLM's JSON response, and the dispatcher routes each field to its registered
handler.

Handlers are channel-aware: the batch dict carries a `_channel` key
('whatsapp', 'email', 'calendar') that determines which SQLite table and
columns to use.

Adding a new output type (e.g., 'deadlines') requires:
1. Adding the field to response-schema.md (no deploy — LLM starts returning it)
2. Adding a @register('deadlines') handler here (deploy required)
Step 1 alone is safe: the dispatcher logs "no handler for deadlines — ignoring."
"""

import json
import uuid
from datetime import datetime, timezone


_HANDLERS = {}


def register(key):
    """Decorator to register a handler for a JSON response field."""
    def deco(fn):
        _HANDLERS[key] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------
# Shared handler — memory_facts (all channels)
# ---------------------------------------------------------------------------

@register('memory_facts')
def _handle_memory_facts(facts, batch, db):
    """Push facts to Hermes long-term memory via memory_bridge."""
    if not facts or not isinstance(facts, list):
        return
    try:
        from shared.memory_bridge import remember_facts
        channel = batch.get('_channel', 'unknown')
        sender = (batch.get('sender_phone') or batch.get('sender')
                  or batch.get('organizer_email', ''))
        remembered = remember_facts(facts, source=channel, sender=sender)
        if remembered:
            print(f"[triage] Remembered {remembered} fact(s) to long-term memory")
    except ImportError:
        pass  # memory_bridge not available
    except Exception as e:
        print(f"[triage] Memory bridge error: {e}")


# ---------------------------------------------------------------------------
# WhatsApp handlers
# ---------------------------------------------------------------------------

@register('tasks')
def _handle_tasks(tasks, batch, db):
    """Write tasks to the appropriate SQLite table based on channel."""
    if not tasks:
        return
    channel = batch.get('_channel', 'whatsapp')
    now = datetime.now(timezone.utc).isoformat()

    if channel == 'whatsapp':
        source_phone = batch.get('source_phone', '')
        msg_id = batch['messages'][0]['msg_id'] if batch.get('messages') else ''
        for task in tasks:
            try:
                db.execute(
                    """INSERT INTO wa_tasks (id, source_phone, source_msg_id, description, due_date, status, priority, created_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (str(uuid.uuid4()), source_phone, msg_id,
                     task.get('description', ''), task.get('due_date'),
                     task.get('priority', 'medium'), now)
                )
            except Exception as e:
                print(f"[triage] Error inserting task: {e}")

    elif channel == 'email':
        account_id = batch.get('account_id', '')
        first_email_id = batch['emails'][0]['id'] if batch.get('emails') else None
        for task in tasks:
            try:
                db.execute(
                    """INSERT INTO email_tasks (id, account_id, source_email_id, description, due_date, status, priority, created_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (str(uuid.uuid4()), account_id, first_email_id,
                     task.get('description', ''), task.get('due_date'),
                     task.get('priority', 'medium'), now)
                )
            except Exception as e:
                print(f"[triage] Error inserting task: {e}")


@register('notes')
def _handle_notes(notes, batch, db):
    """Write notes to the appropriate SQLite table based on channel."""
    if not notes:
        return
    channel = batch.get('_channel', 'whatsapp')
    now = datetime.now(timezone.utc).isoformat()

    if channel == 'whatsapp':
        source_phone = batch.get('source_phone', '')
        msg_id = batch['messages'][0]['msg_id'] if batch.get('messages') else ''
        for note in notes:
            try:
                db.execute(
                    """INSERT INTO wa_notes (id, source_phone, source_msg_id, content, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), source_phone, msg_id,
                     note.get('content', ''), now)
                )
            except Exception as e:
                print(f"[triage] Error inserting note: {e}")

    elif channel == 'email':
        account_id = batch.get('account_id', '')
        first_email_id = batch['emails'][0]['id'] if batch.get('emails') else None
        for note in notes:
            try:
                db.execute(
                    """INSERT INTO email_notes (id, account_id, source_email_id, content, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), account_id, first_email_id,
                     note.get('content', ''), now)
                )
            except Exception as e:
                print(f"[triage] Error inserting note: {e}")


@register('escalate')
def _handle_escalate(should_escalate, batch, db):
    """Create an escalation record if flagged."""
    if not should_escalate:
        return
    channel = batch.get('_channel', 'whatsapp')
    now = datetime.now(timezone.utc).isoformat()

    if channel == 'whatsapp':
        source_phone = batch.get('source_phone', '')
        msg_id = batch['messages'][0]['msg_id'] if batch.get('messages') else ''
        sender_phone = batch.get('sender_phone', '')
        try:
            db.execute(
                """INSERT INTO escalations (id, source_phone, source_msg_id, sender_phone, reason, summary, priority, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (str(uuid.uuid4()), source_phone, msg_id, sender_phone,
                 batch.get('escalation_reason', 'unknown'),
                 batch.get('summary', ''),
                 batch.get('escalation_priority', 'medium'), now)
            )
        except Exception as e:
            print(f"[triage] Error inserting escalation: {e}")

    elif channel == 'email':
        account_id = batch.get('account_id', '')
        first_email_id = batch['emails'][0]['id'] if batch.get('emails') else None
        sender_email = batch.get('sender', '')
        sender_name = batch.get('sender_name', '')

        # Look up contact_id
        contact_id = None
        try:
            handle = db.execute(
                "SELECT contact_id FROM contact_handles WHERE handle_type = 'email' AND handle_value = ?",
                (sender_email,)
            ).fetchone()
            if handle:
                contact_id = handle['contact_id']
        except Exception:
            pass

        try:
            db.execute(
                """INSERT INTO escalations (id, source_phone, source_msg_id, sender_phone,
                   reason, summary, priority, status, created_at,
                   channel, sender_email, sender_name, contact_id)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, 'pending', ?, 'email', ?, ?, ?)""",
                (str(uuid.uuid4()), account_id, first_email_id,
                 batch.get('escalation_reason', 'unknown'),
                 batch.get('summary', ''),
                 batch.get('escalation_priority', 'medium'), now,
                 sender_email, sender_name, contact_id)
            )
        except Exception as e:
            print(f"[triage] Error inserting escalation: {e}")


# ---------------------------------------------------------------------------
# Calendar handlers
# ---------------------------------------------------------------------------

@register('importance')
def _handle_importance(importance, batch, db):
    """Update the calendar event's importance and triage status."""
    event_id = batch.get('event_id', '')
    if not event_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(
            "UPDATE calendar_events SET importance = ?, triaged = 1, updated_at = ? WHERE id = ?",
            (importance, now, event_id)
        )
    except Exception as e:
        print(f"[calendar-triage] Error updating importance: {e}")


@register('importance_reason')
def _handle_importance_reason(reason, batch, db):
    """Store the importance classification reason."""
    event_id = batch.get('event_id', '')
    if not event_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(
            "UPDATE calendar_events SET importance_reason = ?, updated_at = ? WHERE id = ?",
            (reason, now, event_id)
        )
    except Exception as e:
        print(f"[calendar-triage] Error updating importance_reason: {e}")


@register('prep_notes')
def _handle_prep_notes(prep_notes, batch, db):
    """Store preparation notes for the event."""
    event_id = batch.get('event_id', '')
    if not event_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(
            "UPDATE calendar_events SET prep_notes = ?, updated_at = ? WHERE id = ?",
            (prep_notes, now, event_id)
        )
    except Exception as e:
        print(f"[calendar-triage] Error updating prep_notes: {e}")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def process_result(result, batch, db):
    """Generic dispatcher — handles whatever fields the LLM returned.

    Iterates over every key in the JSON response and calls the registered
    handler. Unregistered fields are logged and safely ignored.

    The batch dict must contain '_channel' ('whatsapp', 'email', 'calendar')
    so handlers know which SQLite tables and columns to use.

    The result dict is also merged into the batch dict so escalation handlers
    can access escalation_reason, escalation_priority, and summary from the
    LLM response.
    """
    # Merge result fields into batch so escalation handler can access them
    for key, value in result.items():
        batch[key] = value

    for key, value in result.items():
        handler = _HANDLERS.get(key)
        if handler:
            try:
                handler(value, batch, db)
            except Exception as e:
                print(f"[triage] Handler error for field '{key}': {e}")
        else:
            # Silently ignore fields without handlers (e.g., classification, summary)
            pass
