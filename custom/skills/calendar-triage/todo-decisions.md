---
name: todo-decisions
description: >
  Criteria for deciding whether a calendar event deserves a to-do, and whether
  that to-do should interrupt the user. Edit this file to tune the bar — it is
  hot-reloaded, so no deploy is needed.
---

# When an event becomes a to-do

The event is already on the calendar; putting "attend the meeting" on the
To-dos page repeats what the user can already see. A calendar to-do is for the
**work around** the event — what has to happen *before* it, or what the user
must decide *about* it.

Two independent decisions, in order:

1. **Is this a to-do at all?** If no, leave it out of `todos`; `prep_notes`
   already carries preparation guidance for the event itself.
2. **Should the user be told now?** That is the `notify` flag. Default false.

## Emit a to-do when

- The event **needs preparation** the user must do personally: a document to
  write, numbers to pull, a deck to finish, a decision to reach beforehand.
- It requires an **RSVP, a reschedule, or a decline** that has not happened.
- It has **logistics to arrange**: a room, travel, a dial-in, someone to
  invite, something to bring.
- There is **follow-through the event implies** and nothing else will capture:
  sending minutes, confirming a booking, chasing an attendee.

## Do not emit a to-do for

- Recurring internal standups, blocked focus time, personal reminders.
- Events already fully prepared, or ones the agent handled itself.
- Simple attendance with nothing to bring or decide.
- Declined or cancelled events.

## Set `notify: true` only when

- The event is **soon** — roughly within 48 hours — and the preparation is
  non-trivial.
- Someone external is waiting on the RSVP or the material.
- The event is high-importance by the same standard `importance` uses.

Prefer `notify: false` for anything further out: a staged to-do surfaces on the
To-dos page, and step 3's digest will pick it up as the date approaches.

## Volume

At most **three** to-dos per event reach the store, highest priority first.
Usually one is right: the single piece of preparation that actually matters.

## Writing the fields

- `title` — imperative and specific, naming the event: "Finish the Q3 deck for
  Thursday's board review", not "Prepare for meeting".
- `detail` — one or two sentences: what is needed, for whom, and why now.
- `priority` — driven by how soon the event is and who is waiting.
- `due_date` — the day the preparation must be done by, which is normally the
  day *before* the event, not the event's own date.
