# Multi-Element Enhancement Workflow

## Problem

When enhancing a Figma design with many visual elements (circuit patterns, wave motifs,
icons, glow effects, dividers, background textures), a single large prompt requesting
all elements consistently times out. Even medium prompts (2-3 elements) are unreliable.

## Solution: Decompose into Single-Element Calls

Break the enhancement into a sequence of `terminal(background=true, notify_on_complete=true)`
calls, each requesting ONE element. The pattern:

### Step 1: Clone the target frames (one call)

If the user wants a "Rev 2" or versioned copy, clone frames within the same file:

```
"Clone all 4 top-level frames using node.clone(), rename with 'Rev 2' prefix,
stack vertically below originals with 100px gap. Report node IDs."
```

This is one call that does N clone operations — it works because `node.clone()` is a
single Plugin API call per frame (fast, no LLM reasoning needed).

### Step 2: Get the clone's node structure

Use `mcp_figma_get_figma_data` (the read-only Hermes MCP tool) to pull the clone's
structure. Identify the node IDs for:
- Each section frame (Hero, Mission, Teaching Principles, Course Groups, Footer)
- Each icon container (the 56px gold circles)
- Each card content frame

### Step 3: Queue single-element write calls

For each enhancement element, launch a separate background call:

```
# Element 1: Brand glow
"Create ONE ellipse named 'Brand glow' inside the Hero: 400x400px, radial gradient
center #403010 30% opacity to transparent edge, centered behind 'AI&I' text,
absolute positioning, send to back."

# Element 2: Wave motif
"Create ONE vector path at the BOTTOM of the Hero: flowing bezier wave, 1440px
wide, 60px tall, fill #403010 at 15% opacity, absolute positioning."

# Element 3: Circuit pattern
"Create a frame named 'Circuit pattern' inside Hero: 300x600px, no fill. Inside
create 3 vector lines: 1px stroke #00D4FF opacity 0.2, 60-100px, various angles."
```

### Parallel vs Sequential

| Scenario | Strategy | Why |
|---|---|---|
| Same parent frame | **Sequential** | Auto-layout state collisions |
| Different parent containers | **Parallel (up to 3)** | No shared state, safe |
| Read/check operations | **Parallel with writes** | Read-only, no collision risk |

Example: 4 principle icons in 4 different cards → 4 parallel calls.
Example: 3 elements inside the same Hero frame → 3 sequential calls.

### Step 4: Verify and check for partial results

After a timed-out call, ALWAYS check for partial results before retrying:

```
"Check if a frame named 'Circuit pattern' already exists inside frame 19:2's Hero.
Do NOT create anything new — just inspect and report."
```

The bridge agent will find existing nodes and report their properties, saving a
wasted duplicate-creating write call.

## Timing Reference (July 30, 2026, glm-5.2 via DashScope)

| Operation | Typical wall time |
|---|---|
| `whoami` (read) | ~5 sec |
| Create 1 rectangle (simple fill) | ~30 sec |
| Create 1 ellipse (radial gradient) | ~8 min |
| Create 1 vector path (bezier wave) | ~8 min |
| Create 1 frame with 3 lines | ~5 min |
| Clone 4 frames (node.clone) | ~5 min (total) |
| Create frame with 12 vectors | ~10 min (may timeout, but elements land) |
| Multi-element prompt (5+ elements) | Timeout (15 min), partial results may exist |

## Prompt Sizing Rules

- **1 element per call**: the hard rule. One shape, one icon, one gradient.
- **Keep prompts under 100 words** of instruction. Long prompts slow LLM reasoning.
- **Include absolute positioning** for any element inside an auto-layout frame.
- **Include the file URL** every time — the bridge has no memory between calls.
- **Name elements explicitly** ("Brand glow", "Hero wave 1") so duplicates are detectable.
