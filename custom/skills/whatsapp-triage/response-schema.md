---
name: response-schema
description: >
  JSON response contract. Edit this file to add, remove, or rename fields
  the LLM returns. The Python processing layer handles any field that has a
  registered handler; unhandled fields are logged and ignored safely.
---

# Response Format

You MUST respond in this exact JSON format. Include every field listed below.
Use empty arrays or null values when there is nothing to extract for a field.

```json
{
  "classification": "task|reminder|note|urgent_business|sales_opportunity|informational|ignorable",
  "summary": "brief 1-2 sentence summary of the batch",
  "escalate": true,
  "escalation_reason": "family|urgent_business|sales_opportunity|null",
  "escalation_priority": "high|medium|low",
  "tasks": [
    {"description": "...", "due_date": "YYYY-MM-DD or null", "priority": "high|medium|low"}
  ],
  "notes": [
    {"content": "..."}
  ],
  "memory_facts": [
    {"fact": "self-contained factual statement", "category": "contact|preference|decision|project|financial"}
  ]
}
```

## Field notes

- `classification` — pick exactly one category from the taxonomy in classify-messages.md
- `summary` — always include, even for ignorable batches
- `escalate` — boolean; true means push to user immediately
- `escalation_reason` — null when escalate is false
- `tasks` — each task needs at minimum description and priority
- `notes` — information worth saving in the local DB (not long-term memory)
- `memory_facts` — facts worth persisting to the agent's long-term memory so
  it remembers them in future conversations. See remember-decisions.md for
  criteria. Each fact must be a self-contained statement (the agent will
  read it without seeing the original message). Use the category field to
  help the agent organize: contact, preference, decision, project, financial.
