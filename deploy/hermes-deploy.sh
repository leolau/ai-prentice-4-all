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
UNITS=$(ls /etc/systemd/system/hermes-*.service | xargs -n1 basename)

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

# hermes owns the source tree, but NOT .venv: root runs pip from it, so a
# hermes-writable venv would be a path back to root.
find "$REPO" -path "$REPO/.venv" -prune -o -print0 | xargs -0 chown hermes:hermes
chown -R root:root "$REPO/.venv"

echo "== restarting services =="
systemctl daemon-reload
for u in $UNITS; do systemctl restart "$u"; done
sleep 15
fail=0
for u in $UNITS; do
  s=$(systemctl is-active "$u")
  printf "%-40s %s\n" "$u" "$s"
  [ "$s" = active ] || fail=1
done
[ "$fail" = 0 ] || { echo "DEPLOY WARNING: a unit is not active — see journalctl -u <unit>" >&2; exit 1; }
echo "deploy OK ($AFTER)  backup: /opt/data/backups/deploy-$TS"
