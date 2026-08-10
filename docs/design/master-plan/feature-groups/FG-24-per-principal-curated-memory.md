# FG-24 — Per-principal curated memory (memory layers 1–2 become per-user)

**Wave:** P6-A (Phase-6) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started

## Summary

Today memory layers 1–2 (`MEMORY.md` + `USER.md`) are **profile-scoped**: one
pair of files under `$HERMES_HOME/memories`, frozen into the system prompt at
session start, shared verbatim by every principal in the deployment, on a
single shared character budget (2200 / 1375) that is **already at its ceiling**
(peak 2029 chars, writes refused). In a one-owner deployment this is correct.
With hundreds of principals in one profile it is incoherent: a file whose
stated purpose is "what the agent knows about *the user*" cannot serve 500
different users, and every personal fact competes for the same budget.

This FG splits the curated tier in two: a **shared** block (org/class knowledge,
owner/admin-writable, everyone reads) and a **per-principal** block (this user's
own curated facts, their own budget, invisible to everyone else). Both are
frozen at session start exactly as today, so the prompt-cache invariant is
untouched.

## The prompt-cache question — resolved, with evidence

The original constraint (recorded in D2 and repeated in the FG-01 rationale)
was that curated memory must stay instance-wide because *per-user prompt blocks
would fragment the upstream prompt cache*. **Re-examined against the current
code, that constraint does not hold**:

1. `agent/prompt_caching.py` implements a single layout, `system_and_3`: **four
   `cache_control` breakpoints — the whole system prompt, plus the last three
   non-system messages.** There is no breakpoint at the end of the `stable`
   tier, so a partial system-prompt prefix is never independently cacheable.
2. `agent/system_prompt.py` already appends, as the last element of the
   `volatile` tier, a line containing `Conversation started: <date>` and
   (when `pass_session_id`) `Session ID: <session_id>`, plus model/provider.

Together these mean **the system prompt is already unique per session**, so
cross-user or cross-session prefix sharing does not exist today and cannot be
lost by this change.

The invariant that *does* matter — the system prompt must be **byte-identical
for every turn within one conversation** — is fully preserved: `MemoryStore`
is constructed once in `agent_init` and `load_from_disk()` captures a frozen
snapshot; reading a *different* file per principal is exactly as stable as
reading the same one. Mid-session writes continue to change disk only, never
the live prompt.

**No change to D2 is required; this FG records the narrower reading:** curated
memory must be *frozen per session*, not *identical across sessions*.

## Decisions applied

- **D2 (hybrid memory consistency)** — unchanged. Curated durable facts stay a
  frozen prompt snapshot; volatile state stays tool-call-queried.
- **D1 (multi-user, three-tier visibility)** — this FG brings memory layers 1–2
  into line with it. They are currently the only tier that ignores C2.
- Consumes **C1** (`Principal`) and **C2** semantics (shared vs. private), but
  adds **no new contract** — the per-user split is expressed with the
  principal that `agent_init` already has.

## Reuse map

- `tools/memory_tool.py` — `get_memory_dir()`, `MemoryStore`,
  `load_from_disk()`, `_system_prompt_snapshot`, `format_for_system_prompt()`,
  `_file_lock()`, `_sanitize_entries_for_snapshot()`. All reused; the change is
  a **scope parameter**, not a new store.
- `agent/agent_init.py:1245` — where `MemoryStore` is constructed. The
  principal identity is **already** threaded to this point: `agent._user_id`
  exists, and `principal_user_id` / `principal_role` are already passed into
  the memory-provider init kwargs a few lines below. No new plumbing.
- `agent/system_prompt.py` `volatile_parts` — where the blocks are appended.
- `hermes_cli/access.py` C1 `Principal` + `_validate_user_id` (the `user_id`
  charset guard is what makes a `user_id` safe as a **path component**).

## Design / approach

### 1. Directory layout

```
$HERMES_HOME/memories/
    MEMORY.md                       # shared org/class knowledge  (existing file, unchanged path)
    USER.md                         # DEPRECATED at instance scope — see migration
    users/
        <user_id>/MEMORY.md         # this principal's curated facts
        <user_id>/USER.md           # this principal's profile
```

`users/<user_id>/` is safe as a path because `_validate_user_id` already
restricts `user_id` to `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` on every write
path — no separators, no traversal. The resolver must still `resolve()` and
assert containment under `memories/users/` (defence in depth, fail-closed).

### 2. Scope parameter

`get_memory_dir(user_id: str | None = None) -> Path` — `None` returns the
shared directory (today's behaviour, so every existing caller and test is
unaffected); a `user_id` returns the per-principal directory.

`MemoryStore(..., user_id: str | None = None)` resolves both:
`load_from_disk()` reads the shared pair **and** the per-user pair and captures
**four** snapshot blocks (`shared_memory`, `shared_user`, `memory`, `user`).

### 3. Prompt assembly

`volatile_parts` gains the shared block first, then the per-principal block:

```
[MEMORY — shared]        …org/class knowledge…
[MEMORY]                 …this user's curated facts…
[USER]                   …this user's profile…
```

Ordering is fixed and deterministic. When `user_id is None` (local CLI, no
identity) the per-principal blocks are absent and the output is
**byte-identical to today** — regression-locked by a baseline test.

### 4. Write routing + authority

| write | target file | who |
|---|---|---|
| `memory(action=add\|update\|remove, target=user)` | per-principal `USER.md` | the principal themself |
| `memory(... target=memory)` | per-principal `MEMORY.md` | the principal themself |
| `memory(... target=shared)` | shared `MEMORY.md` | **owner / instance-admin only** |

`target=shared` is a new value. A non-owner/admin attempt is **refused with a
clear message** (not silently redirected), and the refusal is audited via C5 —
same shape as the existing Core write-guard refusal (C7). The agent must not be
able to talk its way into writing the shared block on a member's behalf.

### 5. Budgets

Each principal gets their **own** `memory_char_limit` / `user_char_limit`
(defaults unchanged). The shared block keeps its own budget. This dissolves the
current shared-ceiling contention: 500 principals × ~3.5 KB ≈ 2 MB on disk,
irrelevant, and each user's consolidation pressure is independent.

Config gains `memory.shared_memory_char_limit` (default: today's 2200) so the
shared block can be tightened once personal facts move out of it.

### 6. Concurrency

Per-principal files **reduce** contention: today 500 writers would serialise on
one `MEMORY.md.lock`. The existing `_file_lock` + atomic `os.replace` are reused
per file, unchanged.

### 7. Security

`_sanitize_entries_for_snapshot` (threat-pattern scan at snapshot-build time)
applies to the per-principal blocks unchanged — same code path. **This matters
more than before**: previously only the owner could write curated memory; now
any member can write content that lands in *their own* system prompt. The scan
already blocks injection/promptware patterns; the new test matrix must prove a
member cannot (a) poison another user's prompt, or (b) poison the shared block.

### 8. Relationship to layer 4

This FG covers curated (layer 1–2) memory only. The complementary piece —
surfacing a user's *semantic* (layer 4, pgvector) memories at session start via
the cache-safe `prefetch()` seam — is FG-21 P2 and stays there. FG-24 makes the
curated tier per-user; FG-21 makes the semantic tier actually get read. Neither
depends on the other.

## Data model

**No database change.** Curated memory stays a filesystem artifact of the
profile (D2/D4: SQLite + files for the agent core, Supabase for the application
layer). Moving it to Postgres was considered and rejected for this FG: it would
put a network round-trip on the session-start path, and the access control it
would buy is already provided by the filesystem split (a principal's session
only ever opens its own directory).

## Migration

1. The existing `memories/MEMORY.md` **stays where it is** and becomes the
   shared block. Zero data movement, and a single-user deployment behaves
   identically.
2. The existing instance-scoped `memories/USER.md` describes **the owner**. On
   first run after upgrade, if `memories/users/` does not exist, copy
   `USER.md` → `users/<owner_id>/USER.md` and leave the original in place as a
   backup (`USER.md.pre-fg24`). One-shot, idempotent, logged.
3. Per-principal files are created lazily on first write. A user with no file
   contributes no block.

## Dev/Prod + Supabase

Not applicable — no `app_*` tables. The per-principal directories live inside
the profile's `HERMES_HOME` and are covered by the existing backup path.

## Testing requirements

- **Baseline (required):** with `user_id=None`, `build_system_prompt()` output
  is byte-identical to pre-change for the same inputs (extends
  `tests/plan_baseline/`).
- **Cache invariant (required):** within one session, the assembled system
  prompt is byte-stable across turns after a mid-session memory write — the
  existing invariant, re-asserted for the per-user path.
- Snapshot isolation: two `MemoryStore`s for two principals produce disjoint
  blocks; user A's entries never appear in user B's snapshot.
- Path containment: a crafted `user_id` cannot escape `memories/users/`
  (belt-and-braces over `_validate_user_id`).
- Authority: `target=shared` refused for `member`/`viewer`, allowed for
  owner/instance-admin, refusal audited (C5).
- Threat scan applies to per-principal entries; a poisoned per-user entry is
  replaced by `[BLOCKED: …]` in that user's snapshot only.
- Budgets are independent: filling user A's `MEMORY.md` does not refuse a write
  by user B or to the shared block.
- Migration: idempotent; running twice does not duplicate the owner's profile;
  original preserved.
- Concurrency: parallel writes by two principals do not block or interleave.

## System testing (system-test box)

Required before promotion, on `hermes-systest` against `app_dev`:
- ≥2 enrolled principals; each adds a curated fact; each session's prompt shows
  their own block and the shared block, and **not** the other's — verified from
  the real assembled prompt, not a unit stub.
- A member's `target=shared` write is refused on the live box and audited.
- Owner's existing memory survives the migration verbatim.

## Dependencies

- **Blocked by:** none (C1 already provides everything needed).
- **Blocks:** nothing hard. Ships independently of FG-25.
- **Related:** FG-21 (layer-4 recall, P2 `prefetch`), FG-05 (pgvector tier).

## Definition of Done

Baseline + cache-invariant tests green; per-principal isolation proven;
`target=shared` authority enforced + audited; migration idempotent;
`scripts/run_tests.sh`, `ruff`, `ty` clean; system-test checklist passed.

## Progress checklist

- [ ] `get_memory_dir(user_id)` + `MemoryStore(user_id=…)` + four-block snapshot
- [ ] `agent_init` passes the resolved principal; `system_prompt` renders shared + per-user
- [ ] `target=shared` write path + owner/admin authority + C5 refusal audit
- [ ] Independent budgets + `memory.shared_memory_char_limit` config
- [ ] Migration (owner `USER.md` → `users/<owner>/`, idempotent)
- [ ] Tests: baseline byte-identity, cache invariant, isolation, containment, authority, threat-scan, budgets, concurrency, migration
- [ ] System test on `hermes-systest` passed

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Scale-out to hundreds of principals in one profile makes an instance-wide `USER.md` incoherent and its shared 2200-char budget a hard blocker (already at 2029, writes refused). Investigation of `prompt_caching.py` (single `system_and_3` layout, one system breakpoint) and `system_prompt.py` (per-session `Session ID` line already in the volatile tier) shows the "per-user memory breaks the prompt cache" constraint does not hold — the prompt is already unique per session, and the real invariant (byte-stable within a conversation) is preserved by the existing frozen-snapshot mechanism. |

## Cloud-agent prompt

> **[Phase-6 Wave A — independent; may run in parallel with FG-25]** Repo
> `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, and this doc (FG-24). Split
> curated memory layers 1–2 into a **shared** block and a **per-principal**
> block. Add a `user_id` scope parameter to `get_memory_dir()` and
> `MemoryStore` (`None` ⇒ today's shared paths, byte-identical behaviour —
> regression-lock it); per-principal files live at
> `$HERMES_HOME/memories/users/<user_id>/{MEMORY,USER}.md`. `load_from_disk()`
> captures four frozen snapshot blocks; `agent/system_prompt.py` renders shared
> then per-user in the `volatile` tier. `agent/agent_init.py:1245` already has
> the principal in scope — pass it, do **not** add new plumbing. Add
> `target=shared`, writable by **owner/instance-admin only**, refusal audited
> via C5. Independent char budgets + `memory.shared_memory_char_limit`.
> One-shot idempotent migration of the owner's `USER.md` into
> `users/<owner_id>/`. **Do not** move curated memory into Postgres and **do
> not** touch `prompt_caching.py`. The system prompt must stay byte-identical
> across turns within a session (assert it) and byte-identical to pre-change
> when `user_id is None`. Tests: baseline, cache invariant, cross-principal
> snapshot isolation, path containment, `target=shared` authority + audit,
> threat-scan on per-user entries, independent budgets, concurrent writes,
> idempotent migration. Run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY
> this FG doc. Open a PR linking this doc.
