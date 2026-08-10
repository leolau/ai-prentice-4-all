---
name: remember-decisions
description: >
  Criteria for deciding if a WhatsApp message contains facts worth persisting
  to the agent's long-term memory. Edit this to change what gets remembered.
  Changes take effect on the next batch — no restart, no deploy.
---

# Memory-Worthy WhatsApp Content

After classifying and extracting tasks/notes, decide if any content should be
persisted to long-term memory (facts the agent should remember across sessions).
Put these in the `memory_facts` array of the JSON response.

## Always remember

- New contact information (name, company, role, relationship)
- Important dates (birthdays, anniversaries, deadlines mentioned as facts)
- Decisions made ("let's go with plan B", "we decided to cancel the contract")
- Project status changes ("the migration is complete", "we're now live")
- Preferences the user expresses ("I prefer morning calls", "don't contact me on weekends")
- Financial facts ("our budget is $50k", "the quote was for $12k/month")

## Do not remember

- Routine status updates that will be stale tomorrow
- Conversational filler ("ok", "thanks", "see you")
- Tasks (those go in the tasks array, not memory)
- Time-sensitive information that expires (meeting links, one-time codes)
- Media-only messages with no text content

## How to write a memory fact

Each fact must be self-contained — the agent will read it without seeing the
original message. Bad: "He is the new manager." Good: "John Smith from Acme
Corp is now the procurement manager (mentioned in WhatsApp 2026-08-07)."
