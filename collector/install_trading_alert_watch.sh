#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COLLECTOR_DIR="$ROOT_DIR/collector"
USER_NAME="$(id -un)"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [[ -x "$COLLECTOR_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$COLLECTOR_DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
fi

if [[ ! -f "$COLLECTOR_DIR/trading_alert_watch.py" ]]; then
  echo "missing: $COLLECTOR_DIR/trading_alert_watch.py" >&2
  exit 1
fi

"$PYTHON_BIN" -m py_compile   "$COLLECTOR_DIR/alert_manager.py"   "$COLLECTOR_DIR/trading_alert_watch.py"

UNIT_PATH="/etc/systemd/system/trading-alert-watch.service"

sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=SK Hynix Trading Score Telegram Watch
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$COLLECTOR_DIR
ExecStart=$PYTHON_BIN $COLLECTOR_DIR/trading_alert_watch.py --loop
Restart=always
RestartSec=10
NoNewPrivileges=true
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now trading-alert-watch.service

echo "=== trading-alert-watch.service ==="
sudo systemctl --no-pager --full status trading-alert-watch.service || true
