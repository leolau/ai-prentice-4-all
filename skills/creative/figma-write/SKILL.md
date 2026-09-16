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
   is absent but `DEEPSEEK_API_KEY` is set, the bridge falls back to DeepSeek's
   Anthropic-compatible endpoint automatically.

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

Useful tools the bridge agent has: `use_figma` (general create/edit/inspect),
`create_new_file`, `upload_assets`, `generate_diagram`, `search_design_system`,
`get_design_context`, `get_metadata`, `get_variable_defs`, `whoami`.

## Procedure

1. Confirm the request names a target: an existing file/selection URL, or an explicit
   "create a new file".
2. `figma_login.sh status` if you have not used the bridge in this session.
3. Send one focused instruction per call. Large builds land better as several calls
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
4. Report the returned file URL and node IDs back to the user so they can review.

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

## Verification

```bash
scripts/figma_login.sh status
scripts/figma_write.sh "Call the figma whoami tool and report the result verbatim."
```

The first prints `figma: https://mcp.figma.com/mcp (HTTP) - Connected`; the second must
report your handle and a `"seat": "Full"` plan entry.
