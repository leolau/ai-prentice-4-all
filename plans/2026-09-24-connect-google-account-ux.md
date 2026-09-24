# Plan: reliable "Connect Google account" UX (settings)

Date: 2026-09-24

## Status: implemented

## Problem

Production incident while restoring `leolau@joyaether.com`:

- The email-hint field and the redirect-URL paste field are easy to confuse —
  a pasted redirect URL re-ran `start`, overwrote the pending state, and the
  code was never exchanged.
- The scope set lacked `openid`/`userinfo.email`, so `userinfo` returned no
  email; with no hint, `complete` failed *after* consuming the single-use code.
- `prompt=consent` alone can silently reuse the browser's signed-in account —
  wrong when connecting an additional (4th) account.
- The 10-minute pending TTL was invisible to the user.

## Changes

Backend (`hermes_cli/google_oauth.py`, `credentials_api.py`):

- `IDENTITY_SCOPES` (`openid` + `userinfo.email`) added to every connect via
  `connect_scopes()`; `start` stores the requested scopes in pending and
  returns `expires_in`.
- `prompt=select_account consent` — account chooser always shows.
- `email_from_id_token()` — `complete` resolves the account email from the
  `id_token` claim, then userinfo, then the hint.

Frontend (`ConnectedAccounts.tsx`, `client.ts`):

- Numbered steps: **1** pick services + optional email hint → **2** open Google
  sign-in (account chooser; shows expected account + link expiry) → **3** paste
  the `localhost:4321` redirect URL.
- Hint validation: URLs / `code=` blobs rejected with "belongs in step 3";
  non-email input rejected.
- Paste validation: only a `localhost:<port>` URL containing `code=` or a bare
  `4/` code is accepted.
- `complete` 409 (expired/replaced pending) → "sign-in link expired" + reset to
  step 1. Result line warns when the consented account differs from the hint.

## Tests

- `test_credential_store.py`: connect_scopes identity union, account-chooser
  prompt, id_token email decode (+ malformed inputs).
- `ConnectedAccounts.test.tsx`: flow end-to-end updated to new UI; new cases
  for URL-in-hint rejection and non-URL paste rejection.

## Notes

- Entry naming now always reflects the *actually consented* account, so a
  wrong-account approval shows up as a new/renamed entry rather than silently
  overwriting the hinted one.
- Existing stored credentials are unaffected — identity scopes only appear in
  newly consented payloads.
