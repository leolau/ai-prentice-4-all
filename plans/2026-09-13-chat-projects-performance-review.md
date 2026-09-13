# Performance review: `agent-home` chat and projects interfaces

Requested: check the performance of the chat interface (`/chat`,
`ChatPane` + friends) and the projects interface (`/projects`,
`/projects/[slug]`) in `agent-home` — the primary user-facing UI per
`AGENTS.md`.

## Method

- Read `src/app/chat/page.tsx`, `src/components/chat/ChatPane.tsx` (full,
  1019 lines), `MessageBubble.tsx`, `RichText.tsx`, `lib/chat/messages.ts`.
- Read `src/app/projects/page.tsx`, `src/components/projects/ProjectsList.tsx`,
  `ProjectRow.tsx`, `ProjectDetailView.tsx`, `useProjectEvents.ts`.
- Ran `npm run typecheck` (passes) after the fix below; `vitest` in this
  worktree hits a pre-existing, unrelated `@rolldown/binding-darwin-arm64`
  native-binding install issue (npm optional-deps bug) blocking *all* test
  runs, not just this change — not something this review should fix.

## Findings

### Chat — real issue, fixed

`ChatPane` re-renders on every streamed token (`onDelta` → `setMessages`),
every second while a turn is in flight (the `now` clock tick, used for the
elapsed/stall indicator), and on every other bit of turn/approval/activity
state. `MessageBubble` (one per message in the thread) was a plain function
component — **not memoized** — so every one of those re-renders re-ran, for
*every message in the conversation, not just the one being streamed into*:

- the attachment/image segment regex scan (`MessageBubble.tsx` `segment()`),
- `splitCompactionContent` / `stripUiContextLine`,
- a full Markdown → HTML re-parse in `RichText` (`react-markdown` +
  `remark-gfm` + `rehype-raw` + `rehype-sanitize`) for every assistant
  bubble.

For a short conversation this is invisible. For a long-running conversation
(tens to hundreds of turns — this is a persistent per-profile chat, not a
disposable one) it means every streamed token pays for re-parsing the
Markdown of the *entire history*, and the 1s clock tick keeps doing that for
as long as a turn runs even with no new tokens. That's real, user-visible
jank (dropped frames, input lag in the composer) on exactly the surface
(“mobile-first”, per the file's own doc comment) where it costs the most.

`lib/chat/messages.ts`'s `setLastAssistantContent` already preserves object
identity for every message except the trailing one it rewrites
(`messages.slice()` + replace only the last index), so the fix is a clean
`React.memo` — no deeper refactor needed:

- `src/components/chat/MessageBubble.tsx`: wrapped the component in
  `memo(...)`. Default (shallow prop) comparison is correct because
  `message` identity is stable for unmodified turns and `highlightTerm` /
  `msgIndex` are primitives.

Not changed (deliberately, to keep the fix minimal and low-risk):

- No virtualization added for the message list. Chat threads are bounded by
  what a human conversation accumulates in one session and the box already
  paginates the *session list* (200 sessions) rather than message count; if
  a follow-up shows genuinely huge single-conversation transcripts causing
  DOM-size issues, that's a separate, larger change (e.g.
  `react-virtuoso`/windowing) and should be scoped on its own.
- `RichText`'s `components` object literal is recreated every render (new
  inline functions each time). With `MessageBubble` memoized this no longer
  matters for *unchanged* bubbles; for the actively-streaming bubble it's
  unavoidable overhead (content is changing every render anyway) and not
  worth the added indirection of hoisting it out for a single call site.

### Projects — no material issues found

- `src/app/projects/page.tsx` + `ProjectsList.tsx`: keyset pagination
  (`limit=50` first page, cursor-based `Load more` / `IntersectionObserver`
  infinite scroll), filters debounced 300ms before refetch, first page
  rendered server-side (no client refetch-on-mount flash). No N+1, no
  unbounded fetch.
- `ProjectRow.tsx`: a plain server component, cheap pure formatting, no
  client-side state — nothing to optimize.
- `ProjectDetailView.tsx` fans out to ~13 panel components, all rendered
  from data already fetched server-side (`project`, `board`, `playbook`,
  `directives`, `doctor` — one request each, all `| null`-safe, no
  client-side per-panel fetch waterfall on initial load).
- `useProjectEvents.ts`: a 15s poll, gated on `document.visibilityState ===
  "visible"`, cursor-seeded from the first response (never refetches history),
  calls `router.refresh()` only when the event head actually moves, swallows
  network/parse errors. This is already the correct pattern — nothing to
  change.

No changes made on the projects side.

## Verification

- `npm run typecheck` (agent-home workspace): passes after the
  `MessageBubble` change.
- `vitest` could not be run in this worktree (pre-existing native-binding
  install failure unrelated to this change — see Method). The change is a
  `React.memo` wrapper with no behavior change to props/JSX output, so
  existing chat component tests (e.g. any `MessageBubble`/`ChatPane`
  snapshot or interaction tests) should be unaffected; re-run them once the
  environment issue is resolved.
