---
name: remember-decisions
description: >
  Criteria for deciding if a calendar event contains patterns or facts worth
  persisting to the agent's long-term memory.
---

# Memory-Worthy Calendar Content

## Always remember

- New recurring meeting patterns (weekly 1:1, monthly review) — note the
  cadence, attendees, and inferred relationship
- First-time meetings with new contacts
- Meeting outcomes or decisions mentioned in event description
- Conference/event attendance (tells the agent where you'll be)
- Changes to recurring meetings (time shift, attendee added/removed)

## Do not remember

- Individual one-off meetings (too transient)
- Cancelled events
- Recurring events with no changes
- Focus time blocks and lunch reminders
- Events you've declined

## How to write a memory fact

Each fact must be self-contained. Bad: "Weekly 1:1." Good: "Weekly 1:1 with
alice@company.com every Monday at 10am — likely a direct report or close
collaborator (12 consecutive meetings detected)."
