# Dynamic Triage Skills System — Design Reference

## Purpose

The triage agents (WhatsApp, email, calendar) classify incoming content, extract
tasks/notes, decide whether to escalate, and determine what facts to persist to
long-term memory. This document describes the four-layer architecture that makes
90% of the LLM's behavior configurable through `.md` file edits on the ECS box —
no git commit, no deploy, no restart.

---

## Four-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: SKILLS (.md files)                                   │
│  Classification taxonomy, escalation criteria,                  │
│  task/note extraction patterns, memory decisions                │
│  Hot-reloaded per batch (every 3-5s). NO restart. NO deploy.   │
│  This is 90% of the LLM's behavior.                             │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: RESPONSE SCHEMA (response-schema.md)                  │
│  The JSON contract: what fields the LLM must return             │
│  Moved from hardcoded Python to a .md skill file                │
│  Edit to add/rename/remove fields. NO deploy.                  │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: CONFIG (config.json)                                  │
│  Model name, temperature, skill directories,                    │
│  family/VIP contacts, feature toggles, memory settings          │
│  Restart required (systemctl restart hermes-*-triage)          │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4: PROCESSING HANDLERS (triage_handlers.py)             │
│  Handler registry: one function per JSON field                 │
│  Writes to SQLite, pushes to Telegram, remembers to MEMORY.md │
│  New handler = new output action = deploy required             │
└─────────────────────────────────────────────────────────────────┘
```

**Key insight:** Layers 1 and 2 are the brain (the LLM's reasoning). Layer 4
is just plumbing (what happens with the LLM's output). After this design, you
can change the LLM's entire behavior — what it classifies, what it escalates,
what it remembers, what fields it returns — purely by editing `.md` files on
the ECS box. Python only needs touching when you add a genuinely new output
*destination* (a new SQLite table, a new external API to push to).

---

## File Layout

### In the repo (version-controlled, deployed via git)

```
custom/
├── shared/
│   ├── triage_handlers.py            # Handler registry (Layer 4)
│   ├── memory_bridge.py              # Push facts to MEMORY.md
│   └── file_registration.py           # (existing) file registry bridge
├── skills/
│   ├── whatsapp-triage/
│   │   ├── SKILL.md                  # (existing) overview
│   │   ├── classify-messages.md      # (existing) classification taxonomy
│   │   ├── business-urgency.md       # (existing) urgency criteria
│   │   ├── extract-tasks.md          # (existing) task extraction
│   │   ├── extract-contacts.md       # (existing) contact extraction
│   │   ├── sales-opportunities.md    # (existing) sales detection
│   │   ├── escalation-rules.md       # (new) moved from hardcoded Python
│   │   ├── response-schema.md       # (new) JSON contract, editable
│   │   └── remember-decisions.md     # (new) what to persist to memory
│   ├── email-triage/
│   │   ├── SKILL.md                  # (existing)
│   │   ├── classify-emails.md        # (existing)
│   │   ├── spam-newsletter-filter.md # (existing)
│   │   ├── attachment-handling.md     # (existing)
│   │   ├── extract-deadlines.md      # (existing)
│   │   ├── signature-parsing.md      # (existing)
│   │   ├── thread-context.md         # (existing)
│   │   ├── escalation-rules.md       # (new) email-specific escalation
│   │   ├── response-schema.md       # (new) email JSON contract
│   │   └── remember-decisions.md     # (new) email memory criteria
│   └── calendar-triage/
│       ├── SKILL.md                  # (existing)
│       ├── classify-importance.md    # (existing)
│       ├── detect-conflicts.md       # (existing)
│       ├── extract-prep-context.md   # (existing)
│       ├── relationship-signals.md   # (existing)
│       ├── escalation-rules.md       # (new) calendar escalation
│       ├── response-schema.md        # (new) calendar JSON contract
│       └── remember-decisions.md     # (new) calendar memory criteria
├── whatsapp/
│   └── triage_agent.py               # (modified) uses handler dispatcher
├── email/
│   └── email_triage_agent.py         # (modified) uses handler dispatcher
├── calendar/
│   ├── calendar_poller.py            # (existing, unchanged) polls Google Calendar
│   └── calendar_triage_agent.py      # (new) triage agent for calendar events
└── migrations/
    └── create_calendar_tables.py     # (existing) creates calendar SQLite tables
```

### On the ECS box (runtime, edited directly for quick changes)

```
/opt/data/skills/
├── whatsapp-triage/         ← same .md files as repo, editable in place
│   └── custom/              ← drop experimental skills here
├── email-triage/
│   └── custom/
└── calendar-triage/
    └── custom/

/opt/data/whatsapp-messages/
└── config.json              ← WhatsApp triage config (model, contacts, skills, memory)

/opt/data/email-messages/
└── config.json              ← Email triage config

/opt/data/calendar/
└── config.json              ← Calendar triage config

/opt/data/hermes-home-staging/memories/
└── MEMORY.md                ← where remembered facts land (agent reads at session start)

/opt/data/whatsapp-messages/
└── whatsapp_data.db         ← unified SQLite DB for all triage data
```

---

## Layer 1: Skills (.md files) — No Restart, No Deploy

### How it works

All three triage agents call `load_skills()` inside their main loop — meaning
skills are re-read from disk **on every single batch** (every 3-5 seconds for
WhatsApp, every 5 seconds for email, every 30 seconds for calendar). This is
the hot-reload mechanism.

```python
# triage_agent.py — load_skills() reads .md files from disk each batch
def load_skills(config=None):
    triage_cfg = (config or {}).get('triage', {})
    base_dirs = triage_cfg.get('skills_dirs', [SKILLS_DIR])
    subdirs = triage_cfg.get('skills_subdirs', ['custom'])

    search_dirs = list(base_dirs)
    for bd in base_dirs:
        for sd in subdirs:
            search_dirs.append(os.path.join(bd, sd))

    for sdir in search_dirs:
        if not os.path.isdir(sdir):
            continue
        for f in sorted(glob.glob(os.path.join(sdir, '*.md'))):
            if f.endswith('.disabled'):
                continue
            with open(f) as fh:
                content = fh.read()
            skills_content.append(f"## Skill: {os.path.basename(f)}\n{content}")

    return "\n\n---\n\n".join(skills_content)
```

The skill directories are configurable via `config.json`:

```json
{
  "triage": {
    "skills_dirs": ["/opt/data/skills/whatsapp-triage"],
    "skills_subdirs": ["custom"]
  }
}
```

If `skills_dirs` is absent, the code falls back to the hardcoded `SKILLS_DIR`
constant. The email triage agent falls back to both `whatsapp-triage` and
`email-triage` directories (it loads WhatsApp skills for shared context).

### What you can do

| Action | How | Takes effect |
|--------|-----|-------------|
| Add a new skill | Create `foo.md` in the skill dir | Next batch (3-5s) |
| Edit a skill | Edit the `.md` file in place | Next batch |
| Disable a skill | Rename to `foo.md.disabled` | Next batch |
| Remove a skill | Delete the file | Next batch |
| Add experimental skill | Put in `custom/` subdirectory | Next batch |
| Override a base skill | Put same-named file in `custom/` | Next batch (last loaded wins) |

### The `custom/` subdirectory override pattern

Skills are loaded in sorted order: base directory first, then `custom/`
subdirectory. When two files define conflicting instructions, the last-loaded
file wins (the LLM reads it later in the prompt). This means a file in
`custom/` overrides the base skill of the same topic.

---

## Layer 2: Response Schema — No Deploy

### What it controls

The `response-schema.md` file defines the JSON contract — what fields the LLM
must return. The Python system prompt no longer contains a hardcoded schema.
Instead, it says:

```python
system_prompt = f"""You are a WhatsApp message triage agent. Your job is to:
1. Classify the overall batch
2. Extract any tasks or action items
3. Extract any notes worth remembering
4. Determine if this batch should be escalated
5. Extract any facts worth persisting to long-term memory

{skills_text}

Analyze the batch and respond according to the response schema defined
in the skills above.
"""
```

The `response-schema.md` file (loaded as part of `skills_text`) contains:

```markdown
# Response Format

You MUST respond in this exact JSON format:

```json
{
  "classification": "task|reminder|note|urgent_business|...",
  "summary": "brief 1-2 sentence summary",
  "escalate": true,
  "escalation_reason": "family|urgent_business|...|null",
  "escalation_priority": "high|medium|low",
  "tasks": [{"description": "...", "due_date": "...", "priority": "..."}],
  "notes": [{"content": "..."}],
  "memory_facts": [{"fact": "...", "category": "contact|preference|..."}]
}
```
```

### To add a new field

1. Edit `response-schema.md` in the relevant skill directory
2. Add the field to the JSON example
3. The LLM starts returning it on the next batch
4. The handler registry (Layer 4) catches it if a handler exists, or silently
   ignores it if it doesn't

**Step 1 alone is safe** — the LLM returns the new field, the dispatcher logs
"no handler for field X — ignoring", and nothing breaks.

### Escalation rules — also moved to skills

Previously hardcoded in the Python system prompt:

```python
# OLD (hardcoded):
ESCALATION RULES:
- ANY message from a family contact -> ESCALATE
- Business requests needing immediate attention -> ESCALATE
```

Now in `escalation-rules.md`:

```markdown
# Escalation Rules

- ANY message from a family contact -> ESCALATE (reason: "family")
- Business requests needing immediate attention -> ESCALATE (reason: "urgent_business")
- Sales opportunities needing immediate attention -> ESCALATE (reason: "sales_opportunity")
- Everything else -> DO NOT escalate (surfaces in hourly digest)
```

Edit this file to change escalation behavior — add new rules, change
thresholds, add temporary suppressions, disable rules by commenting them out.

---

## Layer 3: Config (config.json) — Restart Required

### WhatsApp config (`/opt/data/whatsapp-messages/config.json`)

```json
{
  "triage": {
    "model": "deepseek-chat",
    "skills_dirs": ["/opt/data/skills/whatsapp-triage"],
    "skills_subdirs": ["custom"]
  },
  "memory": {
    "enabled": true,
    "memory_file": "/opt/data/hermes-home-staging/memories/MEMORY.md",
    "max_facts_per_batch": 10
  },
  "escalation": {
    "criteria": {
      "family_contacts": [
        {"name": "Heidi Lui", "relation": "Wife", "phone": "+852..."}
      ]
    }
  }
}
```

### Email config (`/opt/data/email-messages/config.json`)

```json
{
  "triage": {
    "model": "deepseek-chat",
    "skills_dirs": ["/opt/data/skills/whatsapp-triage", "/opt/data/skills/email-triage"],
    "skills_subdirs": ["custom"]
  },
  "memory": {
    "enabled": true,
    "memory_file": "/opt/data/hermes-home-staging/memories/MEMORY.md",
    "max_facts_per_batch": 10
  }
}
```

Email loads skills from **both** `whatsapp-triage` and `email-triage`
directories. This gives the email triage agent shared context (classification
taxonomy, urgency criteria) plus email-specific rules (spam filtering, signature
parsing, thread context).

### Calendar config (`/opt/data/calendar/config.json`)

```json
{
  "triage": {
    "model": "deepseek-chat",
    "skills_dirs": ["/opt/data/skills/calendar-triage"],
    "skills_subdirs": ["custom"]
  },
  "calendar": {
    "triage_enabled": true,
    "triage_poll_interval": 30
  },
  "memory": {
    "enabled": true,
    "memory_file": "/opt/data/hermes-home-staging/memories/MEMORY.md",
    "max_facts_per_batch": 10
  }
}
```

### Changes require restart

Changes to `config.json` require `systemctl restart hermes-*-triage`. The
restart takes ~2 seconds.

---

## Layer 4: Processing Handlers — Deploy Required

### Handler registry pattern

`custom/shared/triage_handlers.py` is a dispatch table mapping JSON response
field names to handler functions:

```python
_HANDLERS = {}

def register(key):
    """Decorator to register a handler for a JSON response field."""
    def deco(fn):
        _HANDLERS[key] = fn
        return fn
    return deco

@register('tasks')
def _handle_tasks(tasks, batch, db):
    """Write tasks to the appropriate SQLite table based on channel."""
    channel = batch.get('_channel', 'whatsapp')
    if channel == 'whatsapp':
        # INSERT INTO wa_tasks ...
    elif channel == 'email':
        # INSERT INTO email_tasks ...

@register('memory_facts')
def _handle_memory_facts(facts, batch, db):
    """Push facts to Hermes long-term memory via memory_bridge."""
    from shared.memory_bridge import remember_facts
    remember_facts(facts, source=channel, sender=sender)

def process_result(result, batch, db):
    """Generic dispatcher — handles whatever fields the LLM returned."""
    for key, value in result.items():
        batch[key] = value  # merge so escalation handler can access fields

    for key, value in result.items():
        handler = _HANDLERS.get(key)
        if handler:
            handler(value, batch, db)
        # else: silently ignore (no handler registered)
```

### Registered handlers

| Field | Handler | What it does |
|-------|---------|-------------|
| `tasks` | `_handle_tasks` | Writes to `wa_tasks` (WhatsApp) or `email_tasks` (email) |
| `notes` | `_handle_notes` | Writes to `wa_notes` or `email_notes` |
| `escalate` | `_handle_escalate` | Creates row in `escalations` table |
| `memory_facts` | `_handle_memory_facts` | Pushes to MEMORY.md via memory_bridge |
| `importance` | `_handle_importance` | Updates `calendar_events.importance` + sets `triaged=1` |
| `importance_reason` | `_handle_importance_reason` | Updates `calendar_events.importance_reason` |
| `prep_notes` | `_handle_prep_notes` | Updates `calendar_events.prep_notes` |

Fields without registered handlers (e.g., `classification`, `summary`,
`conflicts`) are silently ignored by the dispatcher. They're still available
in the `batch` dict for other handlers to access.

### How triage agents call the dispatcher

```python
# triage_agent.py (WhatsApp)
def process_triage_result(batch, result):
    db = get_db()
    batch['_channel'] = 'whatsapp'
    try:
        from shared.triage_handlers import process_result
        process_result(result, batch, db)
    except ImportError:
        # Fallback to inline processing if shared module not available
        ...
    db.commit()
    db.close()
```

The `batch['_channel']` key tells the handler which SQLite tables and columns
to use. This makes the same handler work for WhatsApp, email, and calendar.

### To add a new output type (e.g., `deadlines`)

**Step 1 (no deploy):** Edit `response-schema.md`:
```json
"deadlines": [
  {"description": "...", "due_date": "YYYY-MM-DD", "priority": "high|medium|low"}
]
```
The LLM starts returning `deadlines` on the next batch. The dispatcher logs
"no handler for deadlines — ignoring" until Step 2.

**Step 2 (deploy required):** Add a handler in `triage_handlers.py`:
```python
@register('deadlines')
def _handle_deadlines(deadlines, batch, db):
    for dl in deadlines:
        db.execute(
            "INSERT INTO deadlines (id, source, description, ...) VALUES (...)",
            (str(uuid.uuid4()), batch.get('_channel'), dl['description'], ...)
        )
```
Plus a SQLite migration to create the `deadlines` table.

After deploy, deadlines are processed. The skill files already told the LLM to
extract them, so there's no gap.

---

## Memory Bridge

### What it does

`custom/shared/memory_bridge.py` pushes facts from triage agents into Hermes's
long-term memory file (`MEMORY.md`). The agent reads this file at session start
(via `tools/memory_tool.py`), so facts pushed by triage agents appear in the
agent's system prompt for future conversations.

### How it works

```python
def remember_facts(facts, *, source, sender, config_path=None):
    """Append facts to MEMORY.md using the § delimiter."""
    # 1. Check if memory bridge is enabled (config.json memory.enabled)
    # 2. Cap facts to max_facts_per_batch (default 10)
    # 3. Resolve MEMORY.md path (config override or HERMES_HOME env var)
    # 4. Build provenance-prefixed entries:
    #    [whatsapp 2026-08-07 14:30 from +852...] John Smith is the procurement manager.
    # 5. Append with § delimiter (matching tools/memory_tool.py format)
    # 6. Atomic write via temp file + rename
```

### What the agent sees

At session start, `tools/memory_tool.py` reads `MEMORY.md` and injects a frozen
snapshot into the system prompt. Facts pushed by triage agents appear as
entries delimited by `§`:

```
§
[whatsapp 2026-08-07 from +852...] John Smith from Acme Corp is the procurement manager.
§
[email 2026-08-07 from ceo@startup.io] Board decided to pivot to enterprise pricing model.
```

The agent can recall these facts: "What do you know about John Smith?" →
"John Smith from Acme Corp is your procurement manager, mentioned in a WhatsApp
message on August 7."

### Configuration

The memory bridge reads from the `memory` section of each channel's
`config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | If false, no-op (facts are not persisted) |
| `memory_file` | `$HERMES_HOME/memories/MEMORY.md` | Override path to MEMORY.md |
| `max_facts_per_batch` | `10` | Cap to prevent flooding memory |

When `config_path` is not passed (as in the current handler), the bridge falls
back to the `HERMES_HOME` environment variable, which is set in
`/opt/data/hermes-messaging.env`:
```
HERMES_HOME=/opt/data/hermes-home-staging
```

---

## Calendar Triage Agent

### What it does

`custom/calendar/calendar_triage_agent.py` is a standalone service that mirrors
the WhatsApp/email triage pattern:

1. Watches for events where `triaged = 0` in the `calendar_events` table
2. Loads skills from `/opt/data/skills/calendar-triage/`
3. Calls DeepSeek with event details (title, time, attendees, description)
4. Gets back: importance classification, conflicts, prep context, memory facts
5. Writes results to SQLite + pushes memory facts via the handler registry
6. Marks the event as `triaged = 1`

### Systemd service

```
hermes-calendar-triage.service
  WorkingDirectory=/opt/data/hermes-agent/custom/calendar
  EnvironmentFile=/opt/data/hermes-messaging.env
  ExecStart=/opt/data/hermes-agent/.venv/bin/python .../calendar_triage_agent.py
  Restart=always
  Log: /var/log/hermes-calendar-triage.log
```

### Schema management

At startup, `ensure_schema()` checks if the `calendar_events` table exists and
adds triage columns (`importance`, `importance_reason`, `prep_notes`) via
`ALTER TABLE`. If the table doesn't exist (calendar poller not deployed yet),
it prints a warning and waits, retrying every poll cycle.

The table itself is created by the migration script:
```bash
/opt/data/hermes-agent/.venv/bin/python /opt/data/hermes-agent/custom/migrations/create_calendar_tables.py
```

### Calendar poller dependency

The calendar triage agent depends on the calendar poller
(`calendar_poller.py`) to populate the `calendar_events` table. The poller
polls Google Calendar API (OAuth2) and stores events. If the poller isn't
running, the triage agent will find 0 events and just poll every 30 seconds —
it starts working automatically once events start flowing in.

---

## Escalation Rules Per Channel

Each channel has its own `escalation-rules.md`:

### WhatsApp (`/opt/data/skills/whatsapp-triage/escalation-rules.md`)
- Family contact messages → ESCALATE (reason: "family")
- Business requests needing immediate attention → ESCALATE
- Sales opportunities needing immediate attention → ESCALATE
- Everything else → hourly digest

### Email (`/opt/data/skills/email-triage/escalation-rules.md`)
- VIP sender emails → ESCALATE
- Client emails needing immediate response → ESCALATE
- Sales/RFP/proposal deadlines within 24h → ESCALATE
- Invoices and payment requests → ESCALATE
- Auto-generated newsletters/marketing → ignore

### Calendar (`/opt/data/skills/calendar-triage/escalation-rules.md`)
- Events with "deadline" or "board" in title within 2 hours → ESCALATE
- Hard scheduling conflicts (double-booked) → ESCALATE
- Events with 5+ external attendees starting within 1 hour → ESCALATE
- Everything else → daily calendar digest

---

## What Requires What (Quick Reference)

| Change | Edit where | Restart? | Deploy? |
|--------|-----------|----------|---------|
| Classification taxonomy | `classify-*.md` | No | No |
| Escalation criteria | `escalation-rules.md` | No | No |
| What to remember | `remember-decisions.md` | No | No |
| Task extraction patterns | `extract-tasks.md` | No | No |
| Spam filtering | `spam-newsletter-filter.md` | No | No |
| JSON response fields | `response-schema.md` | No | No |
| New skill/behavior | new `.md` file | No | No |
| Disable a skill | rename to `.disabled` | No | No |
| Model name/temperature | `config.json` | Yes | No |
| Family/VIP contacts | `config.json` | Yes | No |
| Skill directory paths | `config.json` | Yes | No |
| Memory bridge on/off | `config.json` | Yes | No |
| New output handler (new SQLite table, new external push) | `triage_handlers.py` | Yes | Yes |
| Calendar triage enable | `config.json` + deploy agent | Yes | Yes (one-time) |

---

## Worked Examples

### Example 1: "Remember all contact changes from email"

**Goal:** When an email mentions someone's new phone number or role change,
persist it to long-term memory.

**Steps (no deploy):**

1. Edit `/opt/data/skills/email-triage/remember-decisions.md`:

```markdown
## ALWAYS remember from emails
- New or updated contact information (phone, email, company, job title)
- Role changes ("I'm now the VP of Engineering")
- Company changes ("we've rebranded to...", "we moved to a new office")
- Decisions communicated via email ("board approved the budget")
- Project status milestones ("migration complete")
- Financial facts (budgets, quotes, pricing changes)
```

2. Ensure `response-schema.md` includes `memory_facts` (it already does).

3. Done. The next email batch will extract memory facts and push them to
   MEMORY.md.

### Example 2: "Add a 'deadline' output type across all channels"

**Step 1 (no deploy):** Edit `response-schema.md` in both skill directories:

```json
"deadlines": [
  {"description": "...", "due_date": "YYYY-MM-DD", "priority": "high|medium|low"}
]
```

The LLM starts returning `deadlines`. The handler logs "no handler for
deadlines — ignoring" until Step 2.

**Step 2 (deploy required):** Add a handler in `triage_handlers.py`:

```python
@register('deadlines')
def _handle_deadlines(deadlines, batch, db):
    for dl in deadlines:
        db.execute(
            "INSERT INTO deadlines (id, source, description, due_date, ...) VALUES (...)",
            (str(uuid.uuid4()), batch.get('_channel'), dl['description'], ...)
        )
```

Plus a SQLite migration to create the `deadlines` table.

### Example 3: "Temporarily suppress escalations for a noisy project"

**Step (no deploy, no restart):** Edit
`/opt/data/skills/whatsapp-triage/escalation-rules.md`:

```markdown
# Escalation Rules

- ANY message from a family contact -> ESCALATE (reason: "family")
- Business requests needing immediate attention -> ESCALATE

## Temporary suppressions (remove after 2026-08-09)
- Messages from +852-XXXX-YYYY about "Project Phoenix" -> DO NOT escalate
  (routine project updates, not urgent)

- Everything else -> DO NOT escalate (hourly digest)
```

Remove the suppression block after 48 hours to restore normal behavior.

### Example 4: "A/B test two classification approaches"

**Step (no deploy):**

1. Create `/opt/data/skills/whatsapp-triage/custom/simple-taxonomy.md`:

```markdown
# Simplified Classification

Use ONLY these 5 categories (overrides classify-messages.md):
- urgent — needs response within hours
- task — actionable item
- info — informational, no action
- contact — new/updated contact info
- ignore — no useful content
```

2. Watch the triage logs for a few batches. The `custom/` subdirectory is
   loaded alongside base skills, so this overrides the base taxonomy.

3. To revert: rename to `simple-taxonomy.md.disabled` or delete the file.

### Example 5: "Different models per channel"

**Step (restart required, no deploy):** Edit `config.json` on the ECS box:

WhatsApp config (`/opt/data/whatsapp-messages/config.json`):
```json
{
  "triage": {
    "model": "deepseek-reasoner",
    "temperature": 0.2
  }
}
```

Email config (`/opt/data/email-messages/config.json`):
```json
{
  "triage": {
    "model": "deepseek-chat",
    "temperature": 0.3
  }
}
```

Then restart: `systemctl restart hermes-wa-triage hermes-email-triage`

### Example 6: "Set up calendar triage from scratch"

**Step 1 (one-time deploy):** Deploy `calendar_triage_agent.py` + handler for
calendar fields. Enable in config.json. Create the systemd service.

**Step 2 (one-time migration):** Run the calendar tables migration:
```bash
/opt/data/hermes-agent/.venv/bin/python /opt/data/hermes-agent/custom/migrations/create_calendar_tables.py
```

**Step 3 (no deploy, ongoing):** Edit skill files in
`/opt/data/skills/calendar-triage/`:
- `response-schema.md` — what fields the LLM returns
- `escalation-rules.md` — when to escalate calendar events
- `remember-decisions.md` — what meeting patterns to persist to memory
- `classify-importance.md` — how to classify event importance
- `detect-conflicts.md` — how to detect scheduling conflicts
- `extract-prep-context.md` — what to prepare before meetings

**Step 4 (optional, for calendar poller):** Set up Google Calendar API
credentials (`GCAL_CLIENT_ID`, `GCAL_CLIENT_SECRET` in
`/opt/data/hermes-messaging.env`) and create a systemd service for
`calendar_poller.py`. Once the poller starts populating events, the triage
agent picks them up automatically.

---

## Systemd Services

All three triage agents run as systemd services:

| Service | Description | Log file |
|---------|------------|----------|
| `hermes-wa-triage` | WhatsApp triage agent | `/var/log/hermes-wa-triage.log` |
| `hermes-email-triage` | Email triage agent | `/var/log/hermes-email-triage.log` |
| `hermes-calendar-triage` | Calendar triage agent | `/var/log/hermes-calendar-triage.log` |

Common commands:
```bash
# Check status
systemctl is-active hermes-wa-triage hermes-email-triage hermes-calendar-triage

# Restart after config change
systemctl restart hermes-wa-triage hermes-email-triage hermes-calendar-triage

# Check logs
tail -20 /var/log/hermes-wa-triage.log
tail -20 /var/log/hermes-email-triage.log
tail -20 /var/log/hermes-calendar-triage.log
```

All services use:
- **EnvironmentFile:** `/opt/data/hermes-messaging.env` (API keys, HERMES_HOME, etc.)
- **Python:** `/opt/data/hermes-agent/.venv/bin/python`
- **Working directory:** `/opt/data/hermes-agent/custom/{channel}/`

---

## Key Design Decisions

1. **Skills are the brain, handlers are plumbing.** The `.md` files control
   what the LLM thinks and returns. The Python handlers just decide where to
   write the output. This separation means most behavior changes require no
   code changes.

2. **Unhandled fields are silently ignored.** This makes it safe to add new
   fields to `response-schema.md` before the handler exists. The LLM returns
   the field, the dispatcher ignores it, and nothing breaks.

3. **The `custom/` subdirectory override pattern.** Later-loaded files can
   override earlier ones in the concatenated prompt. The last instruction the
   LLM reads wins. This is the same behavior as today.

4. **Memory bridge uses the `§` delimiter.** This matches the format that
   `tools/memory_tool.py` expects. Facts pushed by triage agents are read at
   the next session start and appear in the agent's system prompt.

5. **Channel-aware handlers.** The same `@register('tasks')` handler works for
   WhatsApp, email, and calendar. The `batch['_channel']` key determines which
   SQLite table and columns to use.

6. **Calendar triage is resilient to missing tables.** If the
   `calendar_events` table doesn't exist (calendar poller not deployed yet),
   the triage agent prints a warning and retries every poll cycle. Once the
   table is created by the migration, the agent starts processing automatically.

7. **Config-driven skill directories.** Skill directories are configurable via
   `config.json` (`skills_dirs` array + `skills_subdirs` array). This lets you
   point a triage agent at a different skill set without code changes.
