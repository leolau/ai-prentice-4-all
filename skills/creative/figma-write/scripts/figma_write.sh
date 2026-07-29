#!/usr/bin/env bash
# Write native Figma content by delegating to Figma's official MCP server.
#
# Figma only accepts MCP connections from clients in its own catalog, so the
# write path runs through Claude Code as a subprocess. Reads still go through
# Hermes' own `figma` MCP server.
#
# Usage: figma_write.sh [--timeout SECONDS] "<prompt including a Figma file URL>"
set -euo pipefail

TIMEOUT=600
SERVER=figma

while [ $# -gt 0 ]; do
  case "$1" in
    --timeout)
      TIMEOUT="${2:?--timeout needs a value}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

PROMPT="${*:-}"
if [ -z "${PROMPT}" ]; then
  PROMPT="$(cat)"
fi
if [ -z "${PROMPT}" ]; then
  echo "figma_write: no prompt given" >&2
  exit 2
fi

if ! command -v claude >/dev/null 2>&1; then
  cat >&2 <<'EOF'
figma_write: claude (Claude Code) not found on PATH.
Install the bridge client: npm i -g @anthropic-ai/claude-code
EOF
  exit 127
fi

# Claude Code is used purely as the transport to Figma's MCP server, so any
# Anthropic-compatible backend works. DeepSeek exposes one and is already
# configured for Hermes on most hosts.
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://api.deepseek.com/anthropic}"
    export ANTHROPIC_AUTH_TOKEN="${DEEPSEEK_API_KEY}"
    export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-deepseek-chat}"
    export ANTHROPIC_SMALL_FAST_MODEL="${ANTHROPIC_SMALL_FAST_MODEL:-deepseek-chat}"
  else
    echo "figma_write: no model backend — set ANTHROPIC_AUTH_TOKEN or DEEPSEEK_API_KEY" >&2
    exit 78
  fi
fi

if claude mcp list 2>&1 | grep -q "^${SERVER}:.*Needs authentication"; then
  echo "figma_write: ${SERVER} MCP server is not authenticated — run scripts/figma_login.sh start" >&2
  exit 77
fi

exec timeout "${TIMEOUT}" claude -p "${PROMPT}" \
  --allowedTools "mcp__${SERVER}" \
  --permission-mode acceptEdits
