---
name: figma-write
description: Create and edit native Figma content on the canvas.
version: 1.0.0
author: Leo Lau + Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [figma, design, mcp, canvas, design-systems]
    category: creative
    related_skills: [popular-web-designs, design-md, claude-design]
---

# Figma Write Skill

Writes native Figma content — frames, components, variables, auto layout, FigJam
boards — by delegating to Figma's official MCP server through a bridge CLI. Read-only
Figma work (pulling nodes, images, variables) still goes through the `figma` MCP server
configured in `config.yaml`; this skill is only for changes that must land on the canvas.

## When to Use

- "Create a Figma file / page / screen for ..."
- "Add this component to <Figma file URL>"
- "Convert these raw values in <selection link> to variables"
- "Draw an architecture diagram in FigJam"

Do not use it to read a design (use the `figma` MCP tools) or to generate HTML/CSS
mockups (use `popular-web-designs` / `claude-design`).

## Prerequisites

Figma's official MCP server (`https://mcp.figma.com/mcp`) is the only surface that can
write to the canvas, and it accepts neither personal access tokens nor connections from
unlisted MCP clients: registration is restricted to clients in the Figma MCP Catalog.
Hermes therefore drives a catalog-listed client — Claude Code — as a subprocess:

```
Hermes (terminal tool) -> claude -p -> Figma MCP (OAuth) -> Figma Plugin API -> canvas
```

Requirements:

1. **Figma Full seat** (Pro plan or above). Dev seats are read-only, and you need edit
   permission on the target file.
2. **Claude Code installed** on the same host: `npm i -g @anthropic-ai/claude-code`.
3. **`figma` MCP server registered and authenticated** for the user running Hermes:
   `scripts/figma_login.sh` handles both, including headless hosts.
4. **A model backend for Claude Code.** If `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
   is absent, the bridge falls back to **glm-5.2 via DashScope's Anthropic-compatible
   endpoint** automatically. DeepSeek is a tertiary fallback. The priority order is:
   `ANTHROPIC_*` → `DASHSCOPE_API_KEY` (glm-5.2) → `DEEPSEEK_API_KEY` (deepseek-chat).
   The DashScope Anthropic endpoint URL depends on your key plan (token-plan vs intl) —
   a wrong-endpoint `403 invalid api-key` means swap to the other URL. See
   `references/dashscope-anthropic-endpoint.md` for the probe recipe and the two URLs.

## How to Run

Use the `terminal` tool:

```bash
skills/creative/figma-write/scripts/figma_write.sh "Using this Figma file: <url>, add a \
settings screen built from our existing components"
```

The prompt is passed through verbatim, so always include the Figma **file URL or
selection link** — the MCP server is link-based and cannot guess the target file.
Output is the bridge agent's final message (created node IDs, file URL, warnings) —
`claude -p` already defaults to `--output-format text`, i.e. Claude Code's own final
response only, not a verbose tool-call trace.

That output becomes one Hermes tool-call result verbatim, and this bridge is typically
called *many times* in a row for anything with more than a few work items (see the 20 KB
per-MCP-response cap under Procedure below) — so a chatty final response compounds fast
across calls. Ask for a terse one, e.g. append `Respond with a one-line completion
summary only (what was created/changed) — no narration of your process.` to the prompt.
Found necessary after a token-collection setup card needed 22 context compressions in a
single session — the individual bridge responses weren't oversized on their own, but
many calls with even moderately chatty responses added up.

One-time authentication on a headless host:

```bash
scripts/figma_login.sh start                      # prints the Figma authorize URL
scripts/figma_login.sh complete "<redirect URL>"  # feed back the localhost callback URL
scripts/figma_login.sh status                     # figma: ... - Connected
```

`start` keeps the OAuth listener alive in a `tmux` session; open the printed URL in any
browser signed in to Figma, approve access, then paste the `http://localhost:<port>/callback?...`
URL your browser failed to load into `complete`.

## Quick Reference

| Task | Command |
| --- | --- |
| Write to canvas | `figma_write.sh "<prompt with file URL>"` |
| Longer budget | `figma_write.sh --timeout 1200 "<prompt>"` |
| Check auth | `figma_login.sh status` |
| Re-authenticate | `figma_login.sh start` then `figma_login.sh complete "<url>"` |
| Source API key first | `source ~/.hermes/.env && export DASHSCOPE_API_KEY` |

**Always source the `.env` before calling `figma_write.sh` on a headless host.**
The DASHSCOPE_API_KEY lives in Hermes' secrets file but is not auto-exported
to the shell. Full invocation pattern:

```bash
cd ~/.hermes && \
  source .env && export DASHSCOPE_API_KEY && \
  bash skills/creative/figma-write/scripts/figma_write.sh --timeout 600 "<prompt>"
```

Useful tools the bridge agent has: `use_figma` (general create/edit/inspect),
`create_new_file`, `upload_assets`, `generate_diagram`, `search_design_system`,
`get_design_context`, `get_metadata`, `get_variable_defs`, `whoami`.

## Procedure

1. Confirm the request names a target: an existing file/selection URL, or an explicit
   "create a new file".
2. `figma_login.sh status` if you have not used the bridge in this session.
3. **Clarify your role vs the AI's role.** When the user says "use Figma-AI" or "you
   work with Figma-AI," they mean the *bridge agent* (Claude Code + glm-5.2) should make
   the **design decisions** — not you. You are the project owner: you hold content,
   brand, requirements. The bridge agent is the design expert: it owns layout, spacing,
   visual hierarchy, component design, color usage, iconography. Write the prompt to
   delegate creative authority, NOT to specify exact pixel positions and hex codes.
   Bad: "Create a 400x400 ellipse at x=580,y=1353 with radial gradient #403010 at 30%."
   Good: "Design a premium hero section with our brand glow aesthetic. You decide the
   exact dimensions, positioning, and visual treatment." See
   `references/ai-designer-delegation.md` for the full briefing pattern.
4. **Review before presenting.** After the bridge agent produces a design, verify it
   for: (a) content accuracy (correct names, tagline, audience groups, principles),
   (b) brand compliance (colors, logo, font, dark mode), (c) completeness (all sections
   present). Fix issues with follow-up calls before showing the user. Never present
   unreviewed output.
5. Send one focused instruction per call. Large builds land better as several calls
   ("create the frame and layout", then "convert colors to variables") because each MCP
   response is capped at 20 KB. That cap is a real ceiling, not just a style preference —
   but "several calls" means grouping by natural work units (e.g. one call per token
   *tier* — all Primitive collections together, then all Semantic, then all Component —
   or one call per page section), not one call per individual variable/element. Splitting
   finer than a call actually needs just multiplies round trips, and each round trip's
   full instruction + response re-enters the calling agent's own context — the thing that
   forced 22 compressions on one token-setup card was many small calls, not any single
   oversized one. Start at the widest grouping you expect to fit under 20 KB and only
   split further if a call actually fails or truncates.
6. **One write operation per call** — the hard constraint. The bridge agent (Claude Code
   + glm-5.2 via DashScope) times out on prompts that request multiple vector elements
   or complex multi-step writes. A single "create one ellipse" call completes in 5–8 min;
   a "create 4 elements with gradients" call hits the 10–15 min timeout and produces
   nothing. Keep each prompt to ONE element (one shape, one icon, one gradient). Queue
   them as sequential background calls (`notify_on_complete=true`) rather than batching.
   See `references/bridge-write-performance.md` for the full debugging path that
   established this rule and `references/multi-element-enhancement-workflow.md` for
   the decomposition pattern (parallel vs sequential, prompt sizing, timing
   reference).
7. **Parallel calls are safe for independent elements on different parent containers**
   (e.g. 4 different icon containers in 4 different cards). Calls that write to the
   SAME parent frame will collide on auto-layout state and must be sequential. This
   cuts total wall time by ~60% for multi-icon/multi-card enhancements.
8. **Creative design delegation: section-level works, page-level doesn't.**
   The bridge can **execute specific instructions** (one shape, one gradient) but
   cannot **design open-ended creative work** at page level. However,
   **section-level briefs DO work** (tested July 30, 2026): brief Figma-AI one
   section at a time (~200-300 words per call) with content specified but visual
   details delegated, using `--timeout 1800` (30 min). Each section completes in
   ~8-12 min. Full-page briefs (500+ words, all sections) time out regardless of
   timeout. See `references/ai-designer-delegation.md` for the full briefing
   pattern, what works vs what doesn't, and Figma-AI's autonomous design decisions
   (CJK font substitution, inside-aligned outlines, two-pass equal-height cards).
9. Report the returned file URL and node IDs back to the user so they can review.

## Pitfalls

- **No file link, no write.** Without a URL the bridge agent will either ask or create a
  new draft file.
- **Assets and custom fonts** are unsupported by `use_figma` today; images must be
  uploaded with `upload_assets`, and custom typefaces will fall back.
- **Dev seat** accounts fail on write with a permission error — check `whoami` output.
- **Beta-quality output.** Review generated layers; ask for incremental fixes rather than
  regenerating whole screens.
- The bridge starts a fresh agent per call: it has no memory of earlier calls, so repeat
  the file URL every time.
- **Timed-out calls may still write successfully.** A timeout (exit code 124) kills the
  `claude` process but does NOT roll back MCP writes already sent to Figma. Claude Code
  may have completed several write operations before the timeout fired. **Always check the
  file for partial results before retrying** — the next call will see existing nodes and
  can skip or build on them. Retrying blindly creates duplicate elements.
- **Prompt size → timeout correlation.** Prompts requesting 5+ vector elements (e.g. "create
  12 lines + 4 circles + 3 waves + 6 icons in one call") consistently time out at 600s.
  Single-element calls (one ellipse, one frame with 3 lines) complete in ~5-8 min. Keep
  to 1-3 elements per call. Split multi-element enhancements into a sequence of small
  calls rather than one large prompt. See `references/bridge-write-performance.md` for
  the full debugging path that established this rule.
- **Auto-layout + decorative elements.** When adding decorative/overlay elements (glows,
  waves, circuit patterns) inside an auto-layout frame, set `layoutPositioning = "ABSOLUTE"`
  on the new element. Without it, auto-layout pushes the element into the flow stack and
  ignores explicit x/y positions, distorting the existing layout.
- **`clipsContent: true` clips boundary-straddling children.** If an element straddles the
  boundary between two sections (e.g. a wave at the hero/mission divide), parent it to the
  outer frame (the page-level container) with absolute positioning, not to the clipping
  frame itself — otherwise the lower half is invisible.
- **Creative design delegation: section-level works, page-level doesn't.**
  When the user asks you to "let Figma-AI design it" or "you're the project
  owner, Figma-AI is the designer," the bridge agent **can** execute section-level
  creative briefs (one section per call, ~200-300 words, `--timeout 1800`). It
  **cannot** execute page-level creative briefs (all sections in one call, 500+
  words — times out regardless of timeout). Section-level briefs complete in ~8-12
  min each; the full landing page was built in 5 sequential section calls (~50 min
  total). See `references/ai-designer-delegation.md` for the full delegation pattern,
  what works vs what doesn't, and Figma-AI's autonomous design decisions.
- **DASHSCOPE_API_KEY not auto-exported.** The Hermes `.env` file (at the secrets path
  shown by `hermes config path`) holds `DASHSCOPE_API_KEY`, but it is NOT auto-exported
  to the shell. Before calling `figma_write.sh`, source it:
  `source ~/.hermes/.env && export DASHSCOPE_API_KEY`.
  Verify with `hermes config show` (NOT `hermes config get` — that is not a valid
  subcommand and silently returns nothing).
- **Cross-file duplication is impossible via MCP.** There is no Figma Plugin API or MCP
  operation to copy nodes between files. `create_new_file` makes a *blank* file — it does
  not clone from a source. `cloneNode` / `clone` only works *within* the same file. If
  the user wants a duplicate *file*, they must use Figma's native Duplicate in the
  desktop/web editor (right-click → Duplicate, or `⌘D`). Tell the user this *before*
  attempting a cross-file clone — the bridge agent will discover the constraint on its
  own and report it, but flagging it first saves a 3-5 minute wasted call.
- **Asset upload with Bash tool.** To upload images (logo, reference assets) via
  the bridge, include `"Bash"` in `--allowedTools` alongside `"mcp__figma"` and use
  `--permission-mode acceptEdits`. The `upload_assets` MCP tool gets a single-use
  submit URL from Figma (expires in 10 min), then the bridge agent POSTs the image
  bytes via curl. `--dangerously-skip-permissions` does NOT work as root/sudo —
  `acceptEdits` is the correct alternative. See `references/ai-designer-delegation.md`
  for the full asset upload pattern.
- **Use `node.clone()`, not `cloneNode`.** When cloning frames within the same file, the
  Plugin API exposes `node.clone()` — not `cloneNode`. Calling `cloneNode` on a FRAME
  throws `no such property 'cloneNode' on FRAME node`. The bridge agent handles this
  automatically, but if you are writing prompts that reference the method by name, use
  `clone`.
- **CJK text needs Noto Sans SC, not Inter.** When briefing Figma-AI with Chinese
  text (e.g. AI與我, 伴學式引導學習), the bridge agent may autonomously use Noto
  Sans SC for CJK characters (correct) or may use Inter (renders as tofu boxes).
  Always check `textStyle.fontFamily` on Chinese text nodes in your review.
  Fix with a follow-up call: "Change the font of node X to Noto Sans SC."
  This is the #1 content-accuracy issue to catch in review.
- **In-file versioning pattern (works well).** When a user wants a "Rev 2" or versioned
  copy of a design, clone the target frames *within the same file* using `node.clone()`,
  rename the clones with a "Rev 2" prefix, and reposition them below the originals with a
  100-200px vertical gap. Then enhance the clones while leaving originals intact. This
  avoids the cross-file duplication blocker entirely and keeps all versions in one
  reviewable file. Pattern:
  1. `get_figma_data` to capture source node IDs and frame heights
  2. One bridge call: clone all target frames, rename, stack vertically below originals
  3. Subsequent bridge calls: enhance each clone independently — one element per call
     (see step 4 above). Use parallel calls for elements on different parent containers.

## Variable Collections (Design Tokens)

Building token systems (Primitives → Semantic → Component collections with
alias chains) follows DIFFERENT rules than canvas writes: 20+ variables with
aliases can be created in ONE call per collection, and independent collections
can be created IN PARALLEL. Verification is via structural enumeration plus
an end-to-end node-binding resolution probe (REST variables endpoint needs a
PAT scope the default token lacks). Full workflow, prompt patterns, and
pitfalls: `references/variable-collection-workflow.md`.

**Batch by tier, not by individual collection.** "ONE call per collection" above is a
per-call *capability* ceiling (how much one call can safely hold), not a target for how
many calls the whole token setup should take. A full token setup run that fires one
bridge call per collection (e.g. separate calls for Primitives/Space, Primitives/Type,
Semantic/Color, ...) racks up round trips fast — one such run needed 22 context
compressions in a single session even though no individual response was oversized (the
largest was ~32 KB total across the session, well under the per-response cap). Group
sibling collections within the same tier into one call where they fit under 20 KB (e.g.
all Primitive collections in one call, then all Semantic, then all Component), and only
drop to one-collection-per-call if a combined call actually fails or truncates.

## Bridge Agent Behavior Notes

The bridge agent (Claude Code + glm-5.2) has several built-in behaviors that affect
how you should prompt:

1. **Duplicate detection.** When you send a "create X" prompt and X already exists
   (e.g. from a timed-out prior call), the bridge agent will detect the existing node,
   compare its properties against your spec, and report "already exists, matches your
   spec" rather than creating a duplicate. This is safe but costs a 5-8 min call just
   to discover the element is already there. To avoid this wasted cycle, send a short
   read call ("find all nodes named 'X' inside frame Y, do NOT create anything") before
   re-issuing a create prompt that may have partially succeeded.

2. **Malformed existing elements.** If the bridge finds an existing element that
   matches the spec in name but has wrong geometry (e.g. two arcs curving the same
   direction instead of opposite), it will rebuild the inner vectors cleanly while
   preserving the container frame and gold circle. This is the desired behavior — let
   it happen.

3. **The 2-element ceiling.** Even prompts requesting just 2 write operations (e.g.
   "delete node X, then create ellipse Y") can time out. The practical ceiling is
   truly **1 write operation per call**. A single "create one ellipse with a radial
   gradient and send to back" call takes ~8 min and succeeds; adding "also delete node
   X first" pushes it over the 10-min timeout. Split into separate calls.

4. **whoami is instant.** Read operations (whoami, get_metadata, find node) complete
   in seconds. Use these as heartbeat checks between write calls to verify the bridge
   is responsive without spending a write-cycle slot.

## Verification

```bash
scripts/figma_login.sh status
scripts/figma_write.sh "Call the figma whoami tool and report the result verbatim."
```

The first prints `figma: https://mcp.figma.com/mcp (HTTP) - Connected`; the second must
report your handle and a `"seat": "Full"` plan entry.
