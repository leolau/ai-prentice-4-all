---
name: todo-decisions
description: >
  Criteria for deciding whether a WhatsApp batch deserves a to-do, and whether
  that to-do should interrupt the user. Edit this file to tune the bar — it is
  hot-reloaded, so no deploy is needed.
---

# When a message becomes a to-do

A `task` is something you extracted. A **to-do** is a judgement: *the user may
want to act on this, and should be able to see it waiting*. Chat produces a lot
of extractable tasks and very few real to-dos.

Two independent decisions, in order:

1. **Is this a to-do at all?** If no, leave it out of `todos` — it is still
   captured in `tasks`, `notes` and the Inbox.
2. **Should the user be told now?** That is the `notify` flag. Default false.

## Emit a to-do when

- Someone **asked the user directly** for something and is waiting: a reply, a
  price, a document, a decision, a confirmation.
- There is a **commitment with a time on it**: a meeting to confirm, a pickup,
  a payment, a deadline the sender named.
- The user themselves said they would do something ("I'll send it tonight") —
  a promise made in chat is the most commonly dropped kind of work.
- Something **needs a human** and cannot be answered by the agent alone.

## Do not emit a to-do for

- Group-chat banter, forwards, stickers, greetings, thanks.
- Messages already answered later in the same batch.
- Broadcast/marketing messages and OTP codes.
- Ongoing conversation that is merely *about* work with nothing asked.

Chat is the noisiest surface the agent reads. When in doubt, leave it out
entirely; the message is in the Inbox regardless.

## Set `notify: true` only when

- A **family member or VIP** is asking for something now.
- Money, safety, or a same-day commitment is involved.
- The sender is visibly waiting and delay itself is the harm.

If you set `escalate: true` for this batch, the resulting to-do should
normally carry `notify: true` as well.

## Volume

At most **three** to-dos per batch reach the store, highest priority first. A
busy group chat must never become three to-dos on its own — one, or usually
none.

## Writing the fields

- `title` — imperative and specific, under ~80 characters, naming the person:
  "Send Ada the deposit receipt", not "Reply to message".
- `detail` — one or two sentences of context, including who asked and any
  constraint. The user reads this instead of scrolling back through the chat.
- `priority` — `high` when dated or costly, `medium` by default, `low` for
  optional courtesies.
- `due_date` — only when the message states or clearly implies one. Never
  invent a deadline.
