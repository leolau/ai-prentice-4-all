# Models page — Test button + change-key path

Status: **implemented** — pending PR/deploy

## Request

After changing model settings on `/models`, add a button that proves the
config actually works, and make sure the API key can be set/changed.

## Gaps found

- No round-trip probe existed: `POST /api/providers/validate` checks a key
  *before* saving; the picker's own hint was "the catalog below is the real
  test". Nothing fired a real completion.
- Key entry existed only for *unauthenticated* providers — once connected,
  the only credential action was Disconnect.

## What shipped

### Backend — `POST /api/model/test` (web_server.py)

One real `chat.completions.create("Reply with exactly: OK")`, timed, never
raises — `ok:false` carries `error`/`error_type` (90s overall timeout →
`TimeoutError`). Three modes:

- `scope:"main"` — saved `model.provider`/`default`/`base_url`/`api_key`
  via `resolve_provider_client` (same path sessions use)
- `scope:"main"` + explicit `provider`+`model` — ad-hoc picker selection;
  saved `base_url`/`api_key` are NOT inherited (they belong to the saved
  provider)
- `scope:"auxiliary"` + `task` — via `get_text_auxiliary_client`, so the
  aux-task config (and its `aux:<task>` telemetry row) is exercised

### Frontend

- Main-model card: **Test** button → `✓ <model> replied in Ns — "<reply>"`
  or a red `✗ <error>`.
- Picker sheet: **Test** beside Set model — exercises the *selected*
  provider+model before saving.
- Picker footer (authenticated key-based providers): **Change API key…**
  reveals the key field — same validate-then-save `/provider-key` flow as
  first-time connect; Disconnect moved alongside it.

## Files

- `hermes_cli/web_server.py` — `ModelTestRequest`, `POST /api/model/test`,
  `_run_model_test_sync`
- `agent-home/src/app/api/models/test/route.ts` — BFF
- `agent-home/src/lib/api/client.ts` — `testModel()`
- `agent-home/src/types/index.ts` — `ModelTestResponse`
- `agent-home/src/components/models/ModelsView.tsx` — Test button + result
- `agent-home/src/components/models/ModelPickerSheet.tsx` — Test + change-key

## Tests

- `test_model_test_endpoint_reports_roundtrip` — ok/reply/latency, ad-hoc
  selection, provider failure → `ok:false` (no 5xx)
- `test_model_test_auxiliary_scope_uses_task_client` — aux resolution +
  unconfigured-slot error
- interaction tests: button posts `scope:"main"`, renders success/failure
