#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat >"$UNIT_DIR/ramon-daily-learning.service" <<EOF
[Unit]
Description=Ramon daily Chronos challenger training

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/usr/bin/env bash $ROOT/scripts/daily_learning.sh
Nice=10
EOF

cat >"$UNIT_DIR/ramon-daily-learning.timer" <<'EOF'
[Unit]
Description=Run Ramon daily learning once per day

[Timer]
OnCalendar=*-*-* 03:15:00
Persistent=true
RandomizedDelaySec=300
Unit=ramon-daily-learning.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ramon-daily-learning.timer
systemctl --user list-timers ramon-daily-learning.timer
