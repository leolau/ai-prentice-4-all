---
name: todo-decisions
description: >
  Criteria for deciding whether an email deserves a to-do, and whether that
  to-do should interrupt the user. Edit this file to tune the bar — it is
  hot-reloaded, so no deploy is needed.
---

# When an email becomes a to-do

A `task` is something you extracted. A **to-do** is a judgement: *the user may
want to act on this, and should be able to see it waiting*. Only some tasks
deserve to be to-dos, and only some to-dos deserve to interrupt.

Two independent decisions, in order:

1. **Is this a to-do at all?** If no, leave it out of `todos` entirely — it is
   still captured in `tasks`, `notes` and the Inbox.
2. **Should the user be told now?** That is the `notify` flag. Default false.

## Emit a to-do when

- Someone is **waiting on a reply or a deliverable** from the user — a quote,
  a signature, an answer, a document, a decision.
- There is a **dated commitment**: a deadline, a renewal, an expiry, a payment
  due, an RSVP.
- The email **starts or advances a piece of work** the user has to do
  personally (review this contract, approve this invoice, prepare for this
  meeting).
- Something **failed or is at risk** and needs a human: a bounced payment, a
  rejected filing, a delivery problem.

## Do not emit a to-do for

- Newsletters, marketing, notifications, receipts filed for the record.
- FYI threads where the user is cc'd and nobody is asking them for anything.
- Anything already handled inside the thread ("never mind, sorted").
- Facts worth remembering but requiring no action — those are `memory_facts`.
- Work the agent has already completed and reported.

When in doubt, emit it with `notify: false`. A staged to-do is cheap: the user
sees it on the To-dos page when they look, and it expires by itself if nobody
touches it. A false interruption is expensive.

## Set `notify: true` only when

- Missing it has a **real cost** — a deadline within ~48 hours, money at
  stake, a client or family member actively waiting.
- The sender is a **VIP** by the same standard escalation-rules.md uses.
- It is **time-critical** in a way that reading it tomorrow would not fix.

If you set `escalate: true` for this batch, the corresponding to-do should
normally carry `notify: true` too — telling the user something is urgent and
then hiding the resulting to-do is incoherent.

## Volume

At most **three** to-dos per batch reach the store; the highest-priority ones
win. Do not pad the list to reach three. One good to-do beats three vague
ones — "Reply to Ada about the tender deadline" is actionable, "Follow up on
emails" is not.

## Writing the fields

- `title` — imperative, specific, under ~80 characters, readable months later
  without the email open. Name the person and the thing.
- `detail` — one or two sentences of context: what was asked, by whom, and any
  constraint that matters. The user reads this before deciding to open it.
- `priority` — `high` when dated or costly, `medium` by default, `low` for
  things that are merely nice to close.
- `due_date` — only when the email states or clearly implies one. Never invent
  a deadline; an invented one erodes trust in every date on the page.
