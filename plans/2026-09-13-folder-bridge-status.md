# Implementation status — Folder Bridge

Plan: `2026-09-13-folder-bridge.md`

| Item | Status |
|---|---|
| Backend: second `Hub` + path-routed WS handler in `app-mcp` | **Done** |
| Backend: `folder_bridge_*` MCP tools (state/list_folders/list_directory/search_files/read_file/get_file_metadata) | **Done** |
| Backend: `/files/bridge` page + help entries | **Done** |
| Backend: routing unit test (`test_routing.py`) | **Done** — 24/24 app-mcp tests pass |
| Frontend: File System Access API client (`fsOps.ts`, `handles.ts`) | **Done** |
| Frontend: WS transport (`transport.ts`) | **Done** |
| Frontend: `/files/bridge` page + `FolderBridgeView` UI | **Done** |
| Frontend: entry link from `/files` | **Done** |
| Frontend: ambient DOM types for File System Access API | **Done** (`src/types/file-system-access.d.ts`) |
| Docs: `docs/design/folder-bridge.md`, `app-mcp/README.md`, Caddyfile | **Done** |
| Tests: Python (`test_routing.py`), TS (`fsOps.test.ts`) | **Done** — 12/12 TS, 24/24 Python |
| Verification: `tsc --noEmit`, targeted `vitest run` | **Done**, both clean |
| Verification: full `vitest run` (whole agent-home suite) | 94 files / 629 tests pass; 14 pre-existing environment errors (jsdom `html-encoding-sniffer` ESM/CJS interop, unrelated files, not caused by this change) |
| Production config (`approvals.tools`, deploy) | **Not done — owner decision, deliberately deferred** |
| Whether `app-mcp` itself is live on the current (Hetzner) production box | **Not verified** — flagged as a gap in the design doc; `PRODUCTION.md`'s service catalog doesn't list it |

## What shipped

**Backend (`app-mcp/`)** — extended the existing service rather than adding
a new one:
- `app_mcp/server.py`: a second `Hub` instance (`folder_hub`), a
  `WS_ROUTES` dict + `_route_for_path()` helper so one `_ws_handler` serves
  both `/app-mcp/ws` (existing UI bridge) and `/app-mcp/folders/ws` (new),
  and six new MCP tools: `folder_bridge_state`, `_list_folders`,
  `_list_directory`, `_search_files`, `_read_file`, `_get_file_metadata`.
- `app_mcp/pages.py` / `app_mcp/help.py`: `/files/bridge` entry + guide (the
  existing `test_every_page_has_a_guide` test enforces this pairing).
- `README.md`, `deploy/app-mcp.env.example`, `deploy/app-mcp.service`:
  updated docs/config comments; new `APP_MCP_FOLDER_COMMAND_TIMEOUT` env
  var (default 30s, vs. 10s for UI commands — a real directory search/read
  legitimately takes longer than a UI click).
- `tests/test_routing.py`: 5 new tests for the path-routing helper.

**Frontend (`agent-home/`)**:
- `src/lib/folder-bridge/types.ts` — shared wire/UI types.
- `src/lib/folder-bridge/handles.ts` — the `Map<folderId, handle>` registry,
  IndexedDB persistence for handle restoration, `showDirectoryPicker()`
  wrapper, permission query/request.
- `src/lib/folder-bridge/fsOps.ts` — pure(ish) traversal logic: list a
  directory, recursively search with depth/count safety caps, read a file
  (with a `maxBytes` truncation), get metadata. Fully unit tested against
  fake handle objects (`fsOps.test.ts`, 12 tests) — no real browser/disk
  needed.
- `src/lib/folder-bridge/transport.ts` — the opt-in WebSocket client
  (Connect/Disconnect are explicit user actions, unlike the always-on UI
  bridge), reusing the exact same envelope and ticket route as `app-mcp`'s
  existing bridge.
- `src/components/files/FolderBridgeView.tsx` + `src/app/files/bridge/page.tsx`
  — the UI per spec §5: connection status, Connect/Disconnect, folder list
  with per-folder re-approval and remove, a local search box + results +
  preview pane, and a plain "unsupported browser" message when
  `showDirectoryPicker` isn't available.
- `src/components/files/FilesView.tsx` — an entry link/banner into
  `/files/bridge`.
- `src/types/file-system-access.d.ts` — ambient types for the parts of the
  File System Access API TypeScript's bundled `lib.dom.d.ts` doesn't have
  yet (`showDirectoryPicker`, `queryPermission`/`requestPermission`).

**Docs**: `docs/design/folder-bridge.md` (architecture, deviations from the
spec's literal envelope, security posture, known gaps — mirrors
`docs/design/app-mcp.md`'s format); `agent-home/deploy/Caddyfile.agent-home`
gained the `/app-mcp/ws` + `/app-mcp/folders/ws` proxy block, which — a
pre-existing gap independent of this feature — wasn't in the checked-in
Caddyfile at all before this change.

## Deliberately not done this pass

- **Not deployed.** No production `config.yaml` edit, no service
  restart, no box touched. Per the design doc, enabling
  `approvals.tools: [mcp_app_folder_bridge_read_file, ...]` and actually
  wiring `mcp_servers: app:` (if not already present) are owner decisions.
- **Whether `app-mcp` is even running in production today was not
  checked.** `docs/deployment/PRODUCTION.md`'s post-migration service
  catalog doesn't list `app-mcp`; this needs a read-only check against the
  live Hetzner box before assuming either the existing UI bridge or this
  new Folder Bridge capability is reachable there.
- Write/delete file operations (out of scope per spec §10 and this pass).
- A dedicated integration test that opens a real WebSocket end-to-end
  (matches the existing convention: `app-mcp`'s own `_ws_handler` and
  `agent-home`'s `bridge.ts`-equivalent transport layer aren't
  integration-tested either — only the pure logic underneath is).
