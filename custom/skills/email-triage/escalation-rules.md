---
name: escalation-rules
description: >
  When to push an email batch to the user immediately vs. hourly digest.
  Edit this file to change escalation behavior without a deploy or restart.
---

# Escalation Rules

## Always escalate

- ANY email from a family contact -> ESCALATE (reason: "family")
- ANY email from a VIP contact with important content -> ESCALATE (reason: "vip_sender")
- Business requests needing immediate attention -> ESCALATE (reason: "urgent_business")
- Sales/RFP/proposal deadlines within 24h -> ESCALATE (reason: "sales_opportunity")
- Invoices and payment requests -> ESCALATE (reason: "invoice")
- Client emails needing response -> ESCALATE (reason: "client_email")

## Never escalate

- Newsletters, marketing, auto-generated notifications -> DO NOT escalate
- Automated alerts and system notifications -> DO NOT escalate
- Everything else -> DO NOT escalate (surfaces in hourly digest)

## How to add a temporary suppression

Add a block like this (remove when no longer needed):

```
## Temporary suppressions (remove after YYYY-MM-DD)
- Emails from noisy@vendor.com -> DO NOT escalate (routine updates)
```

Changes take effect on the next batch (5 seconds).
