#!/usr/bin/env bash
# Deploy the tracked branch of leolau/ai-prentice-4-all onto this box.
#   /opt/data/deploy-hermes.sh [branch]        (default: develop)
# Idempotent. Aborts on unexpected local modifications so a hotfix is never
# silently clobbered; ALLOWED_LOCAL_MODS lists the ones we knowingly carry.
set -euo pipefail

REPO=/opt/data/hermes-agent
BRANCH="${1:-develop}"
ALLOWED_LOCAL_MODS=(
)
# Only the long-running services. `hermes-*` also matches the oneshot jobs
# behind timers (drift-check, memory-projection, secret-backup): restarting
# those *runs* them mid-deploy, and `systemctl is-active` reports `inactive`
# with exit 3 for a oneshot that has finished — under `set -e` that aborted the
# verification loop, so this script exited 3 on every successful deploy and
# never printed its own "deploy OK". Enabled unit files are exactly the ones
# meant to be running; `static` (timer-invoked) units are excluded.
UNITS=$(systemctl list-unit-files 'hermes-*.service' --state=enabled --no-legend \
  | awk '{print $1}')
# agent-home is restarted separately (it is not in the hermes-* glob) but a
# deploy that leaves the phone app down must still fail loudly.
VERIFY_UNITS="$UNITS agent-home.service"

cd "$REPO"

echo "== local modifications check =="
mapfile -t mods < <(git status --porcelain --untracked-files=no | awk '{print $2}')
for m in "${mods[@]:-}"; do
  [ -z "$m" ] && continue
  ok=0
  for a in "${ALLOWED_LOCAL_MODS[@]}"; do [ "$m" = "$a" ] && ok=1; done
  if [ "$ok" = 0 ]; then
    echo "ABORT: unexpected local change to $m — commit it upstream or add it to ALLOWED_LOCAL_MODS." >&2
    exit 1
  fi
  echo "carrying known local mod: $m"
done

TS=$(date +%Y%m%d-%H%M%S)
mkdir -p /opt/data/backups/deploy-$TS
for a in "${ALLOWED_LOCAL_MODS[@]}"; do
  [ -f "$a" ] && install -D "$a" "/opt/data/backups/deploy-$TS/$a"
done
git diff > "/opt/data/backups/deploy-$TS/local-mods.diff" || true

echo "== fetching origin/$BRANCH =="
git fetch --no-tags origin "$BRANCH"
BEFORE=$(git rev-parse --short HEAD)
git checkout -q "$BRANCH" 2>/dev/null || git checkout -q -b "$BRANCH" --track "origin/$BRANCH"
git checkout -f "origin/$BRANCH" -- .
git reset -q "origin/$BRANCH"
git merge --ff-only "origin/$BRANCH" >/dev/null 2>&1 || git update-ref "refs/heads/$BRANCH" "origin/$BRANCH"
for a in "${ALLOWED_LOCAL_MODS[@]}"; do
  [ -f "/opt/data/backups/deploy-$TS/$a" ] && cp -a "/opt/data/backups/deploy-$TS/$a" "$a"
done
AFTER=$(git rev-parse --short HEAD)
echo "$BEFORE -> $AFTER"
[ "$BEFORE" = "$AFTER" ] && echo "(already up to date)"

echo "== reinstalling package =="
./.venv/bin/pip install -q -e .

# The dashboard runs with --skip-build and serves the prebuilt bundle in
# hermes_cli/web_dist, which is git-ignored: without this step a deploy ships
# new API endpoints with the old SPA, and every frontend change is invisible
# on the box. Only rebuild when web/ actually moved — it is minutes of CPU on
# a box that is also serving conversations.
if [ "$BEFORE" != "$AFTER" ] && \
   ! git diff --quiet "$BEFORE" "$AFTER" -- web/; then
  echo "== rebuilding dashboard bundle (web/ changed) =="
  nice -n 15 npm ci --no-audit --no-fund --silent
  (cd web && nice -n 15 npm run build)
elif [ ! -d hermes_cli/web_dist/assets ]; then
  echo "== building dashboard bundle (no bundle present) =="
  nice -n 15 npm ci --no-audit --no-fund --silent
  (cd web && nice -n 15 npm run build)
fi

# FG-23 A0.2: Rebuild agent-home (the phone PWA) only when its sources
# changed, or when no build exists. agent-home is an npm *workspace* of the
# root package.json — install/build from the repo root, NOT from inside
# agent-home/ (that would create a second, unhoisted dep tree). The root
# package-lock.json is matched explicitly because a workspace dependency change
# lands there.
if [ "$BEFORE" != "$AFTER" ] && \
   git diff --name-only "$BEFORE" "$AFTER" | grep -qE '^(agent-home/|package-lock\.json$)'; then
  echo "== rebuilding agent-home bundle (agent-home/ changed) =="
  nice -n 15 npm ci --no-audit --no-fund --silent
  nice -n 15 npm run build --workspace agent-home
  restart_agent_home=1
elif [ ! -f agent-home/.next/BUILD_ID ]; then
  echo "== building agent-home bundle (no build present) =="
  nice -n 15 npm ci --no-audit --no-fund --silent
  nice -n 15 npm run build --workspace agent-home
  restart_agent_home=1
fi

# hermes owns the source tree, but NOT .venv: root runs pip from it, so a
# hermes-writable venv would be a path back to root.
find "$REPO" -path "$REPO/.venv" -prune -o -print0 | xargs -0 chown hermes:hermes
chown -R root:root "$REPO/.venv"

echo "== restarting services =="
systemctl daemon-reload
# agent-home runs as `hermes` and `next start` writes into .next, so it is
# restarted *after* the chown above — a build leaves root-owned files behind.
if [ "${restart_agent_home:-0}" = 1 ]; then
  systemctl restart agent-home
fi
for u in $UNITS; do systemctl restart "$u"; done
sleep 15
fail=0
for u in $VERIFY_UNITS; do
  # `is-active` exits nonzero for anything not active; report it, don't abort.
  s=$(systemctl is-active "$u" || true)
  printf "%-40s %s\n" "$u" "$s"
  [ "$s" = active ] || fail=1
done
[ "$fail" = 0 ] || { echo "DEPLOY WARNING: a unit is not active — see journalctl -u <unit>" >&2; exit 1; }
echo "deploy OK ($AFTER)  backup: /opt/data/backups/deploy-$TS"

# docs/deployment/README.md claims to state what is *currently* true of this box
# and names the revision it was verified against. Nothing kept that honest, so
# it went three deploys stale unnoticed. Advisory here — a stale document must
# not block a deploy — but the weekly state check reports it as drift.
./.venv/bin/python scripts/deploy_state.py handover || true
