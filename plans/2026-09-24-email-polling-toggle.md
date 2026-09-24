# Plan: per-account email-polling toggle in Settings

Date: 2026-09-24

## Status: implemented

## Problem

The deployment email poller (`custom/email/email_poller.py`) only polls
accounts listed in `/opt/data/email-messages/config.json` with
`enabled: true`. A newly connected Google account (e.g. heidilui@…) gets no
background email polling until someone hand-edits that JSON — invisible to the
Settings UI.

## Design

Two separate axes, both surfaced per connected account:

- credential `email` service flag — may Hermes use this credential for mail
- poller `enabled` flag — does the background poller run for this address

Backend:

- `hermes_cli/email_accounts_api.py` — `GET /api/email-accounts` lists poller
  entries + `config_present`; `PATCH` `{address, enabled}` flips the flag and
  creates a Gmail-defaults entry (imap/smtp.gmail.com, INBOX, 60s) when
  enabling an unknown address. Owner-only: the config is box-global. Atomic
  write via tempfile+replace; the poller re-reads every cycle so no restart is
  needed.
- Config path honors `EMAIL_CONFIG_PATH` (default `/opt/data/email-messages/
  config.json`); `email_poller.py`'s `CONFIG_PATH` now reads the same env var.
- Mounted in `web_server.py` beside the credentials router.

Frontend:

- BFF `agent-home/src/app/api/email-accounts/route.ts` (GET + PATCH), client
  methods `emailAccounts()` / `setEmailPolling()`.
- `ConnectedAccounts.tsx`: per-google-entry "Email polling" checkbox.
  Unchecked entries created on enable; when the credential lacks the `email`
  service the control notes "(grant Email service too)". Hidden entirely when
  the deployment has no poller config.

## Tests

- `tests/hermes_cli/test_email_accounts_api.py` — missing-config list, toggle
  existing, create-on-enable (Gmail defaults, id allocation), 404 on
  disable-unknown, case-insensitive address match.
- `ConnectedAccounts.test.tsx` — toggle renders + PATCHes; hidden without
  poller config.
