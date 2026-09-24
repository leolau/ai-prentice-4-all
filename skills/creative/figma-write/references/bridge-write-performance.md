# Bridge Write Performance — Debugging Path

## Symptom

`figma_write.sh` calls with complex multi-element prompts (multiple shapes, gradients,
icons in one instruction) time out at the 10–15 min mark producing **zero output**.
Exit code 124 (`timeout` killed the process). The log file stays at 0 bytes.

Read operations (`whoami`, `get_metadata`) complete instantly — the issue is isolated
to **write/create operations** through the Figma MCP Plugin API.

## Root Cause

The bridge chain is: `figma_write.sh` → `claude -p` (Claude Code subprocess) →
`ANTHROPIC_BASE_URL` (glm-5.2 via DashScope token-plan, Singapore) → Figma MCP →
Figma Plugin API → canvas.

Each write operation requires the bridge agent to:
1. Read the target file structure (MCP call)
2. Plan the write (LLM reasoning — slow with glm-5.2)
3. Execute the create/edit (MCP call)
4. Verify the result (optional MCP call)

For a **single element**, this is 3–4 MCP round-trips → 5–8 min wall time. For a
**multi-element prompt** (e.g. "create 4 shapes with gradients, 12 circuit lines, 3
waves, 6 icons"), the agent attempts 20+ MCP round-trips and exceeds the 10 min
default `--timeout` before completing any of them. The subprocess is killed mid-execution
and produces no output.

## Confirmation Test (July 30, 2026)

| Prompt | Elements | Timeout | Result |
|---|---|---|---|
| "Create a rectangle 100x100 #FF0000" | 1 | 10 min | ✅ Success, node `32:2`, ~30 sec |
| "Create an ellipse with radial gradient + delete node + find hero" | 2 | 10 min | ❌ Timeout, exit 124 |
| "Create 4 frames, clone all, rename, reposition" | 4 (clone ops) | 10 min | ✅ Success, ~5 min |
| "Add 5 enhancement groups (circuit lines, waves, glow, motion lines, icons, dividers, orbs)" | ~30 elements | 15 min | ❌ Timeout, exit 124 |
| "Create ONE ellipse (radial gradient, send to back)" | 1 | 10 min | ✅ Success, node `34:2`, ~8 min |

**Conclusion:** The hard limit is ~1-2 write operations per call. Single-element calls
reliably succeed; multi-element calls reliably time out.

## Conclusion: Timeout ≠ Nothing Written

**Critical discovery (July 30, 2026):** A timed-out call (exit 124) may still have
written elements to the Figma file. Claude Code sends MCP write calls to Figma's
Plugin API as it works; the `timeout` command kills the *process* but does not roll
back writes already committed to the canvas.

**Evidence:** A call to "create a frame with 8 circuit lines + 4 via-node circles
(12 elements)" timed out at 600s with empty output (0 bytes in log). The next call
— a simpler "create a frame with 3 lines" — returned: *"A frame named 'Circuit
pattern' already exists inside the Hero frame, and it already contains 12 vector
nodes."* All 12 elements had been written before the timeout killed the process.

**Action:** Always check for partial results before retrying a timed-out call. Send a
short "find node named X inside frame Y" call, or just look for duplicate names in
the file structure. Retrying blindly creates duplicate elements.

**Bridge agent detects existing nodes.** When you send a "create X" prompt and X
already exists (from a timed-out prior call), the bridge agent will detect it and
report "already exists, matches your spec" rather than creating a duplicate. This is
safe — but it costs a 5-8 min call just to discover the element is already there.
To avoid this wasted cycle, send a short read call ("find all nodes named 'X'
inside frame Y") before re-issuing the create prompt.

## Fix: Sequential Single-Element Calls (same parent) or Parallel Calls (different parents)

Instead of one large prompt, queue N `terminal(background=true,
notify_on_complete=true)` calls, each with ONE element:

**Same parent frame → sequential (do NOT parallelize):**
Calls that write to the SAME parent frame will collide on auto-layout state and must be
sequential — wait for each to complete before launching the next.

**Different parent containers → parallel (safe, up to 3 concurrent):**
Calls that write to DIFFERENT parent nodes (e.g. 4 different icon containers in 4
different cards) can run simultaneously. Tested July 30, 2026: 3 parallel calls for
3 independent principle icons completed without collision, cutting total wall time
from ~20 min (sequential) to ~8 min (parallel).

## Auto-Layout Gotcha (discovered same session)

When adding decorative elements (glows, waves, circuit patterns) to an **auto-layout
frame**, the new element gets inserted as an in-flow child and pushes existing content
out of position. The bridge agent must set `layoutPositioning = "ABSOLUTE"` on the
decorative element to take it out of the flow. This is automatic if you mention
"use absolute positioning" in the prompt, but worth stating explicitly.

## Radial Gradients

Radial gradient fills work correctly (confirmed with the Brand glow ellipse, node
`34:2`). The MCP accepts the gradient spec and applies it. No special handling needed.
