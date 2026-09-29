# Calendar delta sync + auxiliary entitlement-denial fallback

Status: **implemented, PR pending** — 2026-10-03

## Requests

1. Production was running ~50% busy; investigation found
   `hermes-calendar-poller` performing a **full 395-day calendar sync every
   ~60s** (~770 events reprocessed + ~770 `register_item` Postgres
   connections per pass). Fix approved: delta sync.
2. The Projects-page "draft a plan" action failed with
   `Error code: 403 - AccessDenied.Unpurchased`.

## Root cause: calendar poller

- `calendar_accounts.sync_token` was NULL for all three Google accounts.
  The full-sync request sends `timeMin`/`timeMax`/`singleEvents`, and Google
  never issues `nextSyncToken` for that request shape — so the cursor was
  never persisted and every pass was a full sync, forever.
- Every event row was unconditionally rewritten (no change detection), and
  `register_item` spun up a fresh asyncio loop + datastore + Postgres
  connection per event (~144 mkdir/s, ~13 connects/s → ~90% of one core).
- `showDeleted` was never requested, so the `status == 'cancelled'` handling
  was dead code — deletions never propagated.
- The service logs to `/var/log/hermes-calendar-poller.log` (not journald),
  which hid the `~770 updated`-per-pass noise.

## Root cause: plan-draft 403

- `auxiliary.projects_plan` is unconfigured → `projects_api._call_plan_model`
  falls back to the `compression` task → `provider: alibaba, model: glm-5.2`.
- The `alibaba` provider points at the Alibaba **Token Plan** endpoint
  (`token-plan.ap-southeast-1.maas.aliyuncs.com`, `sk-sp-` key). Probing the
  endpoint showed **every** chat model — including `auto` — returns
  `AccessDenied.Unpurchased`: the subscription itself is inactive, not just
  that model. The key is plan-scoped (401 on the standard DashScope
  endpoints), so there is no working alibaba model until the plan is renewed.
- `auxiliary_client._is_payment_error` did not recognise the `Unpurchased`
  entitlement denial, so `should_fallback`/`is_capacity_error` stayed False
  and the raw 403 reached the UI instead of the existing fallback chain
  (explicit-provider bypass → main-agent safety net).

## Changes

### `custom/calendar/calendar_poller.py`

- Watermark: `SELECT sync_token, last_synced`; when no `sync_token` and
  `last_synced` is <7d old, request `updatedMin = last_synced − 5min`
  (skew overlap) instead of the full window. Full window only on first run
  or staleness (weekly safety net for permanent deletions).
- `showDeleted: True` on full/delta requests (removed for `syncToken`
  requests, which return tombstones inherently and forbid extra params).
- Canonical `raw_json = json.dumps(event, sort_keys=True)`; unchanged
  events short-circuit before UPDATE / `register_item` / `sync_attendees`.
- Already-cancelled rows are not rewritten or recounted.
- `last_synced` persists on every completed pass, so the next poll is a
  delta even without `nextSyncToken`.

### `agent/auxiliary_client.py`

- `_is_payment_error` now also matches `"unpurchased"` and
  `"eligible for using the model"` — Alibaba Token Plan's
  `AccessDenied.Unpurchased` (subscription/entitlement denial). Effect:
  lapsed-subscription 403s become capacity errors → explicit-provider
  configs fall through the configured chain to the main-agent safety net,
  and the provider is marked unhealthy for 10 min. Compression, vision,
  title-gen, plan drafting, etc. all self-heal while the plan is dead and
  resume using `alibaba` automatically once renewed.

### `hermes_cli/projects_api.py`

- `_plan_draft_error_detail()` translates payment/auth-class provider
  failures into an actionable UI detail ("renew the provider plan or
  repoint auxiliary.compression / auxiliary.projects_plan") instead of
  surfacing only the raw provider payload.

## Operator note

The Alibaba **Token Plan subscription is inactive** — every chat model on
it returns `AccessDenied.Unpurchased`, and its `sk-sp-` key works only on
that endpoint (401 on standard DashScope). Renew/repurchase the plan in the
Alibaba Model Studio console to restore `alibaba`-pinned aux tasks and
`fallback_providers`. Until then the new fallback routes aux calls through
the main `opencode-go` provider automatically.

## Tests

- `tests/custom/test_calendar_poller_delta.py` (new, 9 tests): updatedMin
  selection, full-sync fallback, watermark persistence, unchanged-event
  skip, cancelled-event handling, syncToken path preserved.
- `tests/agent/test_auxiliary_client.py` (+3 predicate, +1 call-level):
  Unpurchased 403 → `_is_payment_error`; explicit `alibaba` + Unpurchased
  → falls back to main-agent model.

## Verification

- `pytest tests/agent/test_auxiliary_client.py` — 290 passed
- `pytest tests/custom/test_calendar_poller_delta.py` — 9 passed
- `pytest tests/hermes_cli/test_projects_api_plan_draft.py
  tests/hermes_cli/test_projects_api.py` — 44 passed
