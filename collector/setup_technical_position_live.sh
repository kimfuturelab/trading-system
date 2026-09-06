#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/eogus1215/trading-system-stage3}"
ENV_FILE="${TECH_POSITION_ENV_FILE:-/home/eogus1215/technical-position.env}"
WEBHOOK_URL="https://script.google.com/macros/s/AKfycbz0ZP3Nf4Tpp1peTGF6ZY2rWKVYpo0W9kIdvLUt4SlFZB1dHdXyrbxYQUT_R381iAvI/exec"
PYTHON_BIN="$REPO_DIR/collector/.venv/bin/python"

cd "$REPO_DIR"

echo "=== 1. FETCH origin/main ==="
git fetch origin main

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$HOME/technical-position-backup-$TS"
mkdir -p "$BACKUP_DIR"

for f in \
  collector/technical_position_collector.py \
  collector/technical_position_backfill.py \
  collector/technical-position-live.service \
  collector/technical_position.env.example
do
  if [ -f "$f" ]; then
    cp -a "$f" "$BACKUP_DIR/"
  fi
done

echo "=== 2. INSTALL technical-position files only ==="
git show origin/main:collector/technical_position_collector.py > collector/technical_position_collector.py
git show origin/main:collector/technical_position_backfill.py > collector/technical_position_backfill.py
git show origin/main:collector/technical-position-live.service > collector/technical-position-live.service
git show origin/main:collector/technical_position.env.example > collector/technical_position.env.example

echo "=== 3. PYTHON COMPILE ==="
"$PYTHON_BIN" -m py_compile \
  collector/technical_position_collector.py \
  collector/technical_position_backfill.py
echo "PY_COMPILE=PASS"

echo "=== 4. CREATE/REPAIR DEDICATED ENV ==="
WEBHOOK_URL="$WEBHOOK_URL" ENV_FILE="$ENV_FILE" "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import os
import secrets

path = Path(os.environ["ENV_FILE"]).expanduser()
webhook = os.environ["WEBHOOK_URL"]

existing = {}
if path.exists():
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        existing[key.strip()] = value.strip()

secret = existing.get("TECH_POSITION_INGEST_SECRET") or secrets.token_urlsafe(48)

values = {
    "TECH_POSITION_WEBHOOK_URL": webhook,
    "TECH_POSITION_INGEST_SECRET": secret,
    "TECH_POSITION_POLL_SECONDS": existing.get("TECH_POSITION_POLL_SECONDS", "60"),
    "TECH_POSITION_PREP_TIME": existing.get("TECH_POSITION_PREP_TIME", "08:50"),
    "TECH_POSITION_LIVE_START": existing.get("TECH_POSITION_LIVE_START", "09:00"),
    "TECH_POSITION_SESSION_CONFIRM_UNTIL": existing.get("TECH_POSITION_SESSION_CONFIRM_UNTIL", "09:20"),
    "TECH_POSITION_LIVE_END": existing.get("TECH_POSITION_LIVE_END", "15:30"),
    "TECH_POSITION_FINAL_TIME": existing.get("TECH_POSITION_FINAL_TIME", "15:35"),
}

content = "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
path.write_text(content, encoding="utf-8")
path.chmod(0o600)

print(f"ENV_FILE={path}")
print("ENV_PERMISSION=600")
print("SECRET_STATUS=SET")
PY

echo
echo "=== RESULT ==="
echo "CODE=PASS"
echo "SERVICE_NOT_STARTED=YES"
echo "BACKUP_DIR=$BACKUP_DIR"
echo "SECRET_NOT_PRINTED=YES"
