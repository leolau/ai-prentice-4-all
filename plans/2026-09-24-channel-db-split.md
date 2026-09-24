# Channel DB split — per-domain SQLite files

**Status:** implemented locally; not yet deployed. Migration verified on a
fixture DB (copy, retire, rename, idempotent re-run, cross-DB attach writes).

## Why

`whatsapp_data.db` was the shared store for **all three channels** (WhatsApp,
email, calendar) plus shared tables (contacts, escalations). Every writer
serialized on one SQLite write lock — the email-poller log accumulated ~51k
`database is locked` entries. The name was also misleading.

## Layout after split

| DB | Path | Tables |
|---|---|---|
| Messaging | `/opt/data/whatsapp-messages/messaging_data.db` | `messages`, `wa_*`, `phones`, `unified_contacts`, `contact_handles`, `contact_merge_suggestions`, `contacts_old_backup`, `escalations` |
| Email | `/opt/data/email-messages/email_data.db` | `email_accounts`, `email_digests`, `email_messages`, `email_notes`, `email_tasks` |
| Calendar | `/opt/data/calendar/calendar_data.db` | `calendar_*` (4 tables) |

The old `whatsapp_data.db` file is renamed to `messaging_data.db` by the
migration (rollback = rename back).

## Mechanism

Table names are disjoint across domains, so modules that need a second DB use
SQLite `ATTACH` — unqualified queries keep resolving to whichever attached DB
owns the table. Zero query rewrites.

- Env overrides: `MESSAGING_DB_PATH`, `EMAIL_DB_PATH`, `CALENDAR_DB_PATH`
  (the old `DB_PATH` env name is retired in the touched modules; no systemd
  unit set it).
- Dual-domain consumers: `email_triage_agent`, `email_mcp_server` (attach
  `msg`), `contact_manager` + `digest_cron` + `escalation_pusher` (v1 and
  shared/ v2, attach `emaildb`), `hermes_cli/todos_cmd` and
  `incomings_backfill` (read-only attaches).

## Migration — `custom/migrations/split_channel_dbs.py`

Copies schema (tables + indexes + triggers via `sqlite_master`) and rows via
`ATTACH`/`INSERT INTO ... SELECT`, then renames source tables to
`zz_migrated_<name>` so a missed consumer fails loudly instead of writing
stale rows. Idempotent. Run with the affected services stopped; the legacy
file is renamed to `messaging_data.db` last.

Rollback: rename `messaging_data.db` back to `whatsapp_data.db`, rename
`zz_migrated_*` tables back (rows written post-split live only in the new
files — merge them back first if the rollback window is long).

## Rollout

1. Deploy code (services still point at defaults — they start against the new
   paths, so **run the migration immediately after deploy** or keep services
   down between deploy and migration).
2. Stop the channel services → run `split_channel_dbs.py` → chown new files
   to the service user → restart.
3. Verify: all units active, pollers cycling, `database is locked` rate drops,
   run `custom/tests/test_phase7/8/9` on the box.

## Caveats

- Transactions spanning attached DBs lose single-file atomicity — acceptable
  here (each module's writes are single-table or best-effort, same as before).
- WhatsApp-side writers still contend among themselves on
  `messaging_data.db` — the split removes *cross-channel* contention only.
- `custom/email/email_triage_agent.py` has a pre-existing broken
  `contact_manager` import (silently caught) — unrelated, untouched.
