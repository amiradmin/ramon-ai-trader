#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
user="$(id -un)"
docker_bin="$(command -v docker)"
if [[ "$root" == *$'\n'* || "$root" == *'"'* || "$root" == *'\\'* ]]; then
  echo "Unsupported project path for systemd unit" >&2
  exit 2
fi
if ! "$docker_bin" info >/dev/null 2>&1; then
  echo "Docker unavailable for current user; check Docker permissions/daemon." >&2
  exit 2
fi
if ! (cd "$root" && "$docker_bin" compose --profile logging config --quiet); then
  echo "Compose config invalid; fix configuration before enabling boot startup." >&2
  exit 2
fi

unit="$(mktemp)"
trap 'rm -f "$unit"' EXIT
cat >"$unit" <<EOF
[Unit]
Description=Ramon AI Trader Docker Compose boot
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service
StartLimitIntervalSec=0

[Service]
Type=oneshot
RemainAfterExit=yes
User=$user
WorkingDirectory=$root
Environment=HOME=$HOME
ExecStart=$docker_bin compose --profile logging up -d --no-build --no-recreate
TimeoutStartSec=0
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

echo "Installing ramon-compose.service for user=$user project=$root"
sudo install -o root -g root -m 0644 "$unit" /etc/systemd/system/ramon-compose.service
sudo systemctl daemon-reload
sudo systemctl enable ramon-compose.service
echo "Installed and enabled; live containers NOT restarted."
echo "After checking the desired Compose profiles and .env, activate with:"
echo "  sudo systemctl start ramon-compose.service"
echo "Status: systemctl status ramon-compose.service --no-pager"
