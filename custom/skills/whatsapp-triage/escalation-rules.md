---
name: escalation-rules
description: >
  When to push a WhatsApp batch to the user immediately vs. letting it
  surface in the hourly digest. Edit this file to change escalation behavior
  without a code deploy or service restart.
---

# Escalation Rules

## Always escalate

- ANY message from a family contact -> ESCALATE (reason: "family")
- Business requests needing immediate attention -> ESCALATE (reason: "urgent_business")
- Sales opportunities needing immediate attention -> ESCALATE (reason: "sales_opportunity")

## Never escalate

- Everything else -> DO NOT escalate (will surface in hourly digest)

## How to add a temporary suppression

Add a block like this (remove it when no longer needed):

```
## Temporary suppressions (remove after YYYY-MM-DD)
- Messages from +852-XXXX-YYYY about "Project Name" -> DO NOT escalate
```

The LLM reads this file on every batch, so changes take effect within 3-5 seconds.
