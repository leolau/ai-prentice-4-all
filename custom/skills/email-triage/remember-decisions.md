---
name: remember-decisions
description: >
  Criteria for deciding if an email contains facts worth persisting to the
  agent's long-term memory. Edit this to change what gets remembered.
---

# Memory-Worthy Email Content

## Always remember from emails

- New or updated contact information (phone, email, company, job title)
  mentioned in email body or signature
- Role changes ("I'm now the VP of Engineering")
- Company changes ("we've rebranded to...", "we moved to a new office at...")
- Decisions communicated via email ("the board approved the budget",
  "we're going with vendor X")
- Project status milestones ("migration complete", "Phase 2 launched")
- Financial facts (budgets, quotes, pricing changes)

## Do not remember

- Meeting links and one-time codes
- Routine status updates that expire
- Newsletter content
- Tasks (those go in the tasks array)
- Email thread quoting (only extract from new content, not quoted replies)

## How to write a memory fact

Each fact must be self-contained. Bad: "She changed her role." Good: "Alice
Wong is now VP of Engineering at Acme Corp (from email 2026-08-07,
alice@acme.com)."
