"""tool-approval plugin — per-call user approval for named tools.

Hermes' built-in approval gate is command-shaped: it inspects terminal
commands and code for dangerous patterns. Tools that reach outside the box
through a structured API — an MCP server holding cloud credentials being the
motivating case — never pass through it, so nothing asks the user before an
``mcp_aws_api_call_aws`` runs.

Some servers offer their own consent flag (the AWS API MCP server has
``REQUIRE_MUTATION_CONSENT``), but those flags only cover *mutating* calls,
and the operation lists they key off are enumerations that lag the API. When
the requirement is "ask me before this tool does anything at all", the gate
has to live on the client side of the call.

This plugin puts it there. Any tool whose name matches a pattern in
``approvals.tools`` is routed through ``tools.approval`` before it executes,
so the prompt lands on whatever surface owns the session — native
approve/deny buttons on Telegram, Slack and Discord, the dangerous-command
prompt on CLI/TUI.

Configuration (``config.yaml`` — behavioural, so not ``.env``)::

    approvals:
      tools:
        - mcp_aws_api_*          # fnmatch, case-sensitive
        - mcp_supabase_*
      # Optional, seconds. Falls back to the elicitation default (300).
      tools_timeout: 300
      # Optional. When true, --yolo / approvals.mode: off / a session /yolo
      # skips these prompts too. Default false: an explicit per-tool gate
      # outranks a blanket bypass.
      tools_respect_bypass: false

Semantics:

* **Every call prompts.** There is no "approve for the session" — the point
  of listing a tool here is that each individual call gets seen.
* **Fails closed.** Decline, timeout, no registered approval channel, or an
  exception inside the approval machinery all block the call. The model gets
  a plain error telling it not to retry, and the tool never runs.
* **Empty/missing ``approvals.tools`` is a no-op**, so enabling the plugin
  costs one dict lookup per tool call until it's configured.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Keep the prompt readable on a phone. Long payloads (a file body, a base64
# blob) are truncated -- the user is approving *which tool with which shape
# of input*, and an unreadable wall of text degrades that decision.
_MAX_DETAIL_CHARS = 700

# Argument names whose values never belong on an approval surface.
_SECRET_ARG_HINTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "credential", "private_key", "access_key",
)

_REDACTED = "<redacted>"


def _approvals_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly().get("approvals")
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning("tool-approval: config unavailable: %s", exc)
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _patterns(cfg: Dict[str, Any]) -> List[str]:
    raw = cfg.get("tools")
    if not isinstance(raw, list):
        return []
    return [p.strip() for p in raw if isinstance(p, str) and p.strip()]


def _timeout(cfg: Dict[str, Any]) -> Optional[int]:
    raw = cfg.get("tools_timeout")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("tool-approval: ignoring non-numeric tools_timeout %r", raw)
        return None
    return value if value > 0 else None


def _bypass_allowed(cfg: Dict[str, Any]) -> bool:
    return cfg.get("tools_respect_bypass") is True


def _redact(args: Any) -> Any:
    if isinstance(args, dict):
        out = {}
        for key, value in args.items():
            lowered = str(key).lower()
            if any(hint in lowered for hint in _SECRET_ARG_HINTS):
                out[key] = _REDACTED
            else:
                out[key] = _redact(value)
        return out
    if isinstance(args, list):
        return [_redact(v) for v in args]
    return args


def _format_args(args: Any) -> str:
    if not args:
        return "(no arguments)"
    try:
        rendered = json.dumps(_redact(args), ensure_ascii=False, indent=2, default=str)
    except Exception:
        rendered = str(args)
    if len(rendered) > _MAX_DETAIL_CHARS:
        rendered = rendered[:_MAX_DETAIL_CHARS] + "\n… (truncated)"
    return rendered


def _on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    **_: Any,
) -> Optional[Dict[str, str]]:
    """Ask the user before *tool_name* runs; block the call unless approved."""
    if not tool_name:
        return None

    cfg = _approvals_config()
    patterns = _patterns(cfg)
    if not patterns:
        return None
    if not any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in patterns):
        return None

    if _bypass_allowed(cfg):
        try:
            from tools.approval import is_approval_bypass_active
            if is_approval_bypass_active():
                return None
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning("tool-approval: bypass check failed: %s", exc)

    try:
        from tools.approval import request_elicitation_consent
    except Exception as exc:  # pragma: no cover -- defensive
        logger.error("tool-approval: approval system unavailable: %s", exc)
        return {
            "action": "block",
            "message": (
                f"'{tool_name}' requires user approval (approvals.tools) and the "
                "approval system is unavailable, so the call was blocked. Ask the "
                "user to run it themselves."
            ),
        }

    decision = request_elicitation_consent(
        f"{tool_name}\n{_format_args(args)}",
        f"Tool '{tool_name}' requires your approval before it runs (approvals.tools).",
        timeout_seconds=_timeout(cfg),
        surface="tool-approval",
    )

    if decision == "accept":
        logger.info("tool-approval: user approved %s", tool_name)
        return None

    logger.info("tool-approval: %s not approved (%s)", tool_name, decision)
    reason = (
        "did not respond in time" if decision == "cancel" else "declined"
    )
    return {
        "action": "block",
        "message": (
            f"'{tool_name}' requires user approval before it runs and the user "
            f"{reason}. The call was NOT executed. Do not retry it — tell the "
            "user what you wanted to run and why, and wait for them to ask again."
        ),
    }


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
