# Settings → Connected accounts: add Google Drive as a service option

Status: implemented, plus follow-up fix for the consent redirect hang.

## Request

The agent-home Settings → *Connected accounts* section offered Email
(IMAP + Gmail API), Calendar and the "Full workspace" bundle. The user
asked for Google Drive as its own checkbox so an account can be
connected/consented for Drive only (e.g. for RAG Drive ingestion and
workspace-skill drive commands) without requesting mail/calendar scopes.

## Changes

- `hermes_cli/credential_store.py` — `SERVICES` gains `"drive"` so the
  flag validates on put/patch and `resolve_for_service("drive")` works
  for future consumers.
- `hermes_cli/google_oauth.py` — `SCOPES_BY_SERVICE["drive"]` =
  `https://www.googleapis.com/auth/drive` (same full-drive scope the
  `workspace` bundle already requests; the skill's drive commands
  include upload/update/delete, so `drive.readonly` would not cover it).
- `agent-home/src/components/settings/ConnectedAccounts.tsx` —
  `SERVICE_OPTIONS` gains `{ id: "drive", label: "Drive" }` between
  Calendar and Full workspace. The per-entry toggle list already renders
  every option except `workspace`, so Drive appears there automatically.
  Also added the missing `data-component` attribute on the root element.
- `agent-home/src/app/api/credentials/google/start/route.ts` — docstring
  service list updated.
- `docs/design/unified-credential-store.md` — service lists and the
  `SCOPES_BY_SERVICE` table updated.
- Tests: `validate_services(["drive"])` and a `["drive"]` scope-union
  case in `tests/hermes_cli/test_credential_store.py`.

## Notes

- Drive tooling (`hermes_cli/rag_drive.py`, the google-workspace skill)
  already checks the credential's granted *scopes* for `auth/drive`, so
  a Drive-only consent works immediately; the `drive` services flag is
  the opt-in marker for future consumers via `resolve_for_service`.
- A Drive-only consent intentionally does NOT include
  `mail.google.com`/`auth/calendar` — narrower grants are the point of
  the separate option.

## Follow-up: consent redirect hang (fixed)

The manual code-paste flow used `redirect_uri=http://localhost:1`. Port 1
is on browser restricted-port blocklists and is privileged/unlistenable —
the post-consent redirect could hang forever instead of erroring, so the
`?code=` URL never became copyable (reported on the box by the local
agent, which worked around it with a port-4321 consent URL).

`REDIRECT_URI` is now `http://localhost:4321` in both copies —
`hermes_cli/google_oauth.py` (settings flow) and
`skills/productivity/google-workspace/scripts/setup.py` — so the browser
fails fast with the code visible in the address bar. The deployed OAuth
client is `installed`-type, so any `http://localhost:<port>` redirect is
accepted without console pre-registration. Design doc updated.

## Follow-up 2: confirm dialog on destructive settings actions (done)

User request: "any disconnect or sign out or logout — add a pop up window
to double confirm."

- New shared `agent-home/src/components/ui/ConfirmDialog.tsx` — modal
  (bottom sheet on phones, centred dialog from `sm` up) matching
  `ApprovalModal`'s shape; dismissible via backdrop/Esc/Cancel (unlike
  ApprovalModal, nothing is paused awaiting an answer).
- `ConnectedAccounts` Disconnect now opens the dialog before DELETE.
- `LogoutButton` Sign out now opens the dialog before POST /logout.
- Already-covered surfaces left as-is: model-picker disconnect (built-in
  confirm phase), tag delete (native `confirm()`), folder-bridge
  disconnect (lives on Files, not Settings).
- Tests: `ConnectedAccounts.test.tsx` gained a confirm-flow test;
  new `LogoutButton.test.tsx` covers confirm + cancel paths.

## Verification

- `npx vitest run ConnectedAccounts.test.tsx` — 3 pass.
- `npx tsc --noEmit` — clean.
- Local functional check: `scopes_for_services(["drive"])` returns the
  drive scope only; `validate_services` accepts `drive`, rejects unknown.
- Python suite runs on the box venv post-deploy.
