#!/usr/bin/env bash
# Authenticate the Figma MCP server for the bridge client (Claude Code).
#
# The OAuth callback lands on loopback, which no browser can reach on a headless
# host, so `start` keeps the listener alive in tmux and `complete` replays the
# redirect URL locally.
#
# Usage:
#   figma_login.sh start [callback-port]     # register + print the authorize URL
#   figma_login.sh complete "<redirect URL>" # replay the localhost callback
#   figma_login.sh status                    # health-check the server
set -euo pipefail

SERVER=figma
URL=https://mcp.figma.com/mcp
SESSION=hermes-figma-login

require_claude() {
  if ! command -v claude >/dev/null 2>&1; then
    echo "figma_login: claude (Claude Code) not found — npm i -g @anthropic-ai/claude-code" >&2
    exit 127
  fi
}

cmd_start() {
  local port="${1:-34711}"
  require_claude
  if ! command -v tmux >/dev/null 2>&1; then
    echo "figma_login: tmux not found; run 'claude mcp login ${SERVER}' in an interactive terminal instead" >&2
    exit 127
  fi

  if ! claude mcp get "${SERVER}" >/dev/null 2>&1; then
    claude mcp add --transport http --scope user --callback-port "${port}" \
      "${SERVER}" "${URL}"
  fi

  tmux kill-session -t "${SESSION}" 2>/dev/null || true
  tmux new-session -d -s "${SESSION}" -x 220 -y 50 \
    "BROWSER=true claude mcp login ${SERVER}"

  local pane=""
  for _ in $(seq 1 20); do
    sleep 2
    # -J unwraps the pane's soft-wrapped lines so the URL stays on one line.
    pane="$(tmux capture-pane -p -J -t "${SESSION}" 2>/dev/null || true)"
    if printf '%s' "${pane}" | grep -q 'figma.com/oauth'; then
      break
    fi
  done

  local auth_url
  auth_url="$(printf '%s' "${pane}" | grep -o 'https://www\.figma\.com/oauth/[^ ]*' | head -1)"
  if [ -z "${auth_url}" ]; then
    echo "figma_login: no authorize URL yet; inspect with: tmux capture-pane -p -J -t ${SESSION}" >&2
    printf '%s\n' "${pane}" >&2
    exit 1
  fi

  cat <<EOF
Open this URL in a browser signed in to Figma and click "Allow access":

${auth_url}

The browser will fail to load http://localhost:${port}/callback?... — that is
expected on a headless host. Copy that failed URL and finish with:

  $0 complete "<redirect URL>"
EOF
}

cmd_complete() {
  local redirect="${1:?usage: $0 complete \"<redirect URL>\"}"
  require_claude
  case "${redirect}" in
    http://localhost:*|http://127.0.0.1:*) ;;
    *)
      echo "figma_login: expected a http://localhost:<port>/callback?... URL" >&2
      exit 2
      ;;
  esac
  curl -fsS -o /dev/null -m 20 "${redirect}"
  sleep 5
  cmd_status
}

cmd_status() {
  require_claude
  claude mcp list 2>&1 | grep "^${SERVER}:" || {
    echo "figma_login: ${SERVER} MCP server is not registered — run '$0 start'" >&2
    exit 1
  }
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  complete) shift; cmd_complete "$@" ;;
  status) cmd_status ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
