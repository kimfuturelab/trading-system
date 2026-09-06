#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/eogus1215/trading-system-stage3"
COLLECTOR_DIR="$REPO_DIR/collector"
VENV_PY="$COLLECTOR_DIR/.venv/bin/python"
SERVICE_NAME="technical-position-live.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
API_ENV="/home/eogus1215/api-read-v2.env"
TECH_ENV="/home/eogus1215/technical-position.env"

cd "$REPO_DIR"

echo "[1/8] Fetch origin/main"
git fetch origin main

TS="$(TZ=Asia/Seoul date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$COLLECTOR_DIR/.backup_technical_position_$TS"
mkdir -p "$BACKUP_DIR"

for f in technical_position_collector.py technical_position_collector_v3.py technical-position-live.service; do
  if [[ -f "$COLLECTOR_DIR/$f" ]]; then
    cp -a "$COLLECTOR_DIR/$f" "$BACKUP_DIR/$f"
  fi
done
if [[ -f "$SERVICE_DST" ]]; then
  sudo cp -a "$SERVICE_DST" "$BACKUP_DIR/$SERVICE_NAME.systemd"
fi

echo "[2/8] Install exact files from origin/main"
git show origin/main:collector/technical_position_collector.py > "$COLLECTOR_DIR/technical_position_collector.py"
git show origin/main:collector/technical_position_collector_v3.py > "$COLLECTOR_DIR/technical_position_collector_v3.py"
git show origin/main:collector/technical-position-live.service > "$COLLECTOR_DIR/technical-position-live.service"

if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: Python venv not found: $VENV_PY" >&2
  exit 2
fi
if [[ ! -f "$API_ENV" || ! -f "$TECH_ENV" ]]; then
  echo "ERROR: required env file missing" >&2
  exit 3
fi

echo "[3/8] Compile check"
"$VENV_PY" -m py_compile \
  "$COLLECTOR_DIR/technical_position_collector.py" \
  "$COLLECTOR_DIR/technical_position_collector_v3.py"

echo "[4/8] Install systemd unit"
sudo cp "$COLLECTOR_DIR/technical-position-live.service" "$SERVICE_DST"
sudo systemctl daemon-reload
sudo systemctl stop "$SERVICE_NAME" || true

# Load the same environment values used by systemd without printing secrets.
set -a
# shellcheck disable=SC1090
source "$API_ENV"
# shellcheck disable=SC1090
source "$TECH_ENV"
set +a

echo "[5/8] Immediate current-day recovery/upsert"
TODAY="$(TZ=Asia/Seoul date +%Y%m%d)"
WEEKDAY="$(TZ=Asia/Seoul date +%u)"
HHMM="$(TZ=Asia/Seoul date +%H%M)"
ONESHOT_RC=0

if (( WEEKDAY <= 5 )); then
  if (( 10#$HHMM >= 1535 )); then
    echo "KST $TODAY $HHMM -> FINAL recovery"
    "$VENV_PY" "$COLLECTOR_DIR/technical_position_collector_v3.py" \
      --base-date "$TODAY" --final || ONESHOT_RC=$?
  elif (( 10#$HHMM >= 900 )); then
    echo "KST $TODAY $HHMM -> LIVE recovery"
    "$VENV_PY" "$COLLECTOR_DIR/technical_position_collector_v3.py" \
      --base-date "$TODAY" || ONESHOT_RC=$?
  else
    echo "KST $TODAY $HHMM -> before market open; service will create rows automatically after live session is confirmed"
  fi
else
  echo "Weekend -> no trading-day row is created"
fi

if (( ONESHOT_RC != 0 )); then
  echo "WARNING: immediate one-shot returned rc=$ONESHOT_RC; continuous service will keep retrying" >&2
fi

echo "[6/8] Start and enable service"
sudo systemctl enable "$SERVICE_NAME" >/dev/null
sudo systemctl restart "$SERVICE_NAME"
sleep 12

echo "[7/8] Verify service"
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "ERROR: $SERVICE_NAME is not active" >&2
  systemctl --no-pager --full status "$SERVICE_NAME" || true
  journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
  exit 4
fi

systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,14p'

echo "[8/8] Recent collector log"
journalctl -u "$SERVICE_NAME" -n 60 --no-pager

echo
echo "DEPLOY_OK backup=$BACKUP_DIR one_shot_rc=$ONESHOT_RC"
