# tool-approval

Require explicit user approval before named tools run.

Hermes' approval gate is command-shaped — it inspects terminal commands and
executed code. A tool that reaches outside the box through a structured API
never passes through it, so an MCP server holding cloud credentials can be
invoked with no prompt. Server-side consent flags help but are partial: the
AWS API MCP server's `REQUIRE_MUTATION_CONSENT` only covers calls its
read-only operation list classifies as mutating, and it has no wildcard.

This plugin puts the gate on the client side of the call, where it can cover
every invocation of a tool regardless of what the tool does.

## Enable

```yaml
plugins:
  enabled:
    - tool-approval

approvals:
  tools:
    - mcp_aws_api_*
```

`approvals.tools` holds fnmatch patterns matched case-sensitively against the
tool name the model called (the same name that appears in the tool schema —
MCP tools are `mcp_<server>_<tool>`, with `-` in the server name becoming
`_`). An empty or missing list makes the plugin a no-op.

Optional keys:

| Key | Default | Meaning |
| --- | --- | --- |
| `approvals.tools_timeout` | elicitation default (300s) | Seconds to wait for the answer. |
| `approvals.tools_respect_bypass` | `false` | When `true`, `--yolo` / `approvals.mode: off` / a session `/yolo` skips these prompts too. Default keeps the per-tool gate above a blanket bypass. |

## Behaviour

The prompt goes to whichever surface owns the session — native approve/deny
buttons on Telegram, Slack and Discord, the dangerous-command prompt on
CLI/TUI — and shows the tool name plus its arguments (values under keys that
look like credentials are redacted, long payloads truncated).

* **Every call prompts.** There is no session-wide "approve all"; that is the
  point of listing a tool here.
* **Fails closed.** Decline, timeout, a session with no approval channel, or
  an error inside the approval machinery all block the call. The tool never
  runs and the model is told not to retry.
* The block is reported to the model as a tool error, so it can explain
  itself to the user rather than looping.
