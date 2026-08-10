---
name: response-schema
description: >
  JSON response contract for calendar triage. Edit this file to change what
  the LLM returns for each calendar event.
---

# Response Format

You MUST respond in this exact JSON format for each event:

```json
{
  "importance": "critical|normal|low",
  "importance_reason": "brief explanation of the classification",
  "escalate": true,
  "escalation_reason": "conflict|critical_event|deadline|null",
  "escalation_priority": "high|medium|low",
  "conflicts": [
    {"type": "hard|soft|location|cross_account", "event_a": "...", "event_b": "...", "overlap_minutes": 30}
  ],
  "prep_notes": "what to prepare before this meeting",
  "memory_facts": [
    {"fact": "self-contained factual statement", "category": "contact|preference|decision|project|financial"}
  ]
}
```

## Field notes

- `importance` — see classify-importance.md for criteria
- `conflicts` — only include if conflicts were detected (see detect-conflicts.md)
- `prep_notes` — relevant preparation context (see extract-prep-context.md)
- `memory_facts` — meeting patterns or contact relationships worth remembering
  (see remember-decisions.md)
- `escalate` — true for hard conflicts or critical events starting soon
