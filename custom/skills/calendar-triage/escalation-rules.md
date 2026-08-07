---
name: escalation-rules
description: >
  When to push a calendar event to the user immediately. Edit this to change
  escalation behavior.
---

# Escalation Rules

## Always escalate

- Events with "deadline" or "board" in title within 2 hours -> ESCALATE (reason: "critical_event")
- Hard scheduling conflicts (double-booked) -> ESCALATE (reason: "conflict")
- Events with 5+ external attendees starting within 1 hour -> ESCALATE (reason: "critical_event")
- Events where you are the organizer with 3+ external attendees starting within 30 minutes -> ESCALATE

## Never escalate

- Normal internal meetings
- Recurring events with no changes
- Events you've declined
- All-day reminders and focus blocks
- Everything else -> surfaces in daily calendar digest

## How to add a temporary suppression

```
## Temporary suppressions (remove after YYYY-MM-DD)
- Events titled "Focus Time" -> DO NOT escalate
```
