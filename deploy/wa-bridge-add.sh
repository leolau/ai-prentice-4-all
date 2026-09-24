#!/usr/bin/env bash
# Provision a new WhatsApp bridge unit: hermes-wa-bridge-<name>.service.
# Runs as root via `sudo -n` from hermes_cli/wa_bridge_api.py — the only
# bridge operation the hermes user's systemctl grant can't do is writing a
# new unit file. Requires a sudoers entry, e.g.:
#   hermes ALL=(root) NOPASSWD: /usr/bin/bash /opt/data/hermes-agent/deploy/wa-bridge-add.sh *
set -euo pipefail

name="${1:-}"
if ! [[ "$name" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]]; then
  echo "invalid bridge name: '$name'" >&2
  exit 2
fi

unit="hermes-wa-bridge-${name}.service"
unit_path="/etc/systemd/system/${unit}"
session_dir="/opt/data/hermes-home-staging/whatsapp/session-${name}"
log_path="/var/log/hermes-wa-bridge-${name}.log"
workdir="/opt/data/hermes-agent/scripts/whatsapp-bridge"

if [[ -e "$unit_path" ]]; then
  echo "unit already exists: $unit" >&2
  exit 3
fi

# Next free bridge port at/above 3000: not bound and not claimed by
# another bridge unit's ExecStart.
port=3000
used_ports=$(
  for u in $(systemctl list-unit-files 'hermes-wa-bridge-*.service' --no-legend \
             | awk '{print $1}'); do
    systemctl cat "$u" 2>/dev/null | grep -o -- '--port [0-9]*' | awk '{print $2}'
  done
)
while ss -tln | grep -q ":${port} " || grep -qx "$port" <<<"$used_ports"; do
  port=$((port + 1))
done

install -d -o hermes -g hermes "$session_dir"

cat > "$unit_path" <<EOF
[Unit]
Description=Hermes WhatsApp bridge (${name}, port ${port})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${workdir}
Environment=WHATSAPP_MODE=bot
Environment=WHATSAPP_ALLOWED_USERS=*
Environment=WHATSAPP_FORWARD_OWNER_MESSAGES=1
ExecStart=/usr/bin/node bridge.js --port ${port} --session ${session_dir}
Restart=always
RestartSec=5
StandardOutput=append:${log_path}
StandardError=append:${log_path}

[Install]
WantedBy=multi-user.target
EOF

mkdir -p "/etc/systemd/system/${unit}.d"
cat > "/etc/systemd/system/${unit}.d/10-unprivileged.conf" <<EOF
[Service]
User=hermes
Group=hermes
EOF

touch "$log_path"
chown hermes:hermes "$log_path"

systemctl daemon-reload
systemctl enable --now "$unit"
echo "provisioned ${unit} on port ${port} (session ${session_dir})"
