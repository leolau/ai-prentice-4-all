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

* **Scope choices are honored.** ``once`` approves a single call;
  ``session`` covers the chat session (keyed on the stable session id, not
  the per-turn approval key); ``always`` additionally writes the tool name
  to ``command_allowlist`` so it survives restarts. Surfaces that only
  offer accept/decline behave as ``once``.
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


def _block_message(tool_name: str, reason: str) -> str:
    """Explain the block to the model in terms of what actually happened.

    A prompt that never reached the user is not a refusal.  Told "the user
    declined", a model apologises for a decision nobody made and the user is
    left insisting they approved it — so undeliverable and no-surface say so,
    and tell the model the workaround (a channel that can prompt) instead of
    "wait to be asked again".
    """
    tail = (
        "The call was NOT executed. Do not retry it — tell the user what you "
        "wanted to run and why, and wait for them to ask again."
    )
    if reason == "timeout":
        return (
            f"'{tool_name}' requires user approval before it runs and the user "
            f"did not respond in time. {tail}"
        )
    if reason in ("no_surface", "undeliverable", "error"):
        detail = {
            "no_surface": (
                "this session has no way to prompt them (no approval surface "
                "is attached to it)"
            ),
            "undeliverable": "the prompt could not be delivered to them",
            "error": "the approval system failed",
        }[reason]
        return (
            f"'{tool_name}' requires user approval before it runs, and {detail}. "
            "The call was NOT executed. The user did NOT decline — they were "
            "never asked, so do not apologise for a refusal or ask them to "
            "approve again here. Say the approval prompt could not reach them, "
            "and that this needs looking at in the logs (approval surface for "
            "this session), then offer another way to get what they wanted."
        )
    return (
        f"'{tool_name}' requires user approval before it runs and the user "
        f"declined. {tail}"
    )


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

    # Honor prior scope choices: a session approval for this chat session or
    # a permanent "always" (command_allowlist) skips the prompt. The scope key
    # is the stable chat session id where one exists — the per-turn approval
    # key would die at the end of the turn.
    scope_key = ""
    try:
        from gateway.session_context import get_session_env
        from tools.approval import get_current_session_key, is_approved

        scope_key = get_session_env("HERMES_SESSION_ID", "") or \
            get_current_session_key(default="")
        # is_approved() checks the permanent allowlist even when scope_key
        # is empty, so an "always" still skips the prompt with no session.
        if is_approved(scope_key, tool_name):
            logger.info("tool-approval: %s already approved for %s", tool_name, scope_key)
            return None
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning("tool-approval: approval-memory check failed: %s", exc)

    try:
        from tools.approval import request_elicitation_consent_detailed
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

    decision, reason = request_elicitation_consent_detailed(
        f"{tool_name}\n{_format_args(args)}",
        f"Tool '{tool_name}' requires your approval before it runs (approvals.tools).",
        timeout_seconds=_timeout(cfg),
        surface="tool-approval",
        persist_key=tool_name,
        persist_session_key=scope_key or None,
    )

    if decision == "accept":
        logger.info("tool-approval: user approved %s", tool_name)
        return None

    logger.info(
        "tool-approval: %s not approved (%s/%s)", tool_name, decision, reason,
    )
    return {
        "action": "block",
        "message": _block_message(tool_name, reason),
    }


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
