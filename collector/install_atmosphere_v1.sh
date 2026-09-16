#!/usr/bin/env bash
set -euo pipefail

ROOT="${HOME}/trading-system-stage3"
COL="${ROOT}/collector"
VENV="${ROOT}/.venv"
SERVICE_SRC="${COL}/atmosphere-v1.service"
SERVICE_DST="/etc/systemd/system/atmosphere-v1.service"

cd "${COL}"

echo "=== ATMOSPHERE 1. SYNTAX ==="
"${VENV}/bin/python" -m py_compile atmosphere_v1.py
echo "PY_COMPILE = PASS"

echo
echo "=== ATMOSPHERE 2. SERVICE FILE ==="
TMP="$(mktemp)"
sed -e "s|__HOME__|${HOME}|g" -e "s|__USER__|${USER}|g" "${SERVICE_SRC}" > "${TMP}"
sudo cp "${TMP}" "${SERVICE_DST}"
rm -f "${TMP}"
sudo systemctl daemon-reload

echo
echo "=== ATMOSPHERE 3. ONE-TIME BACKFILL + MODEL ==="
echo "This writes only the dedicated ATMOSPHERE workbook."
"${VENV}/bin/python" atmosphere_v1.py --backfill 50

echo
echo "=== ATMOSPHERE 4. ENABLE SERVICE ==="
sudo systemctl enable --now atmosphere-v1.service
sleep 3
systemctl is-enabled atmosphere-v1.service
systemctl is-active atmosphere-v1.service

echo
echo "=== ATMOSPHERE 5. RECENT LOG ==="
journalctl -u atmosphere-v1.service -n 20 --no-pager
