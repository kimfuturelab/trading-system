#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/eogus1215/trading-system-stage3}"
PYTHON_BIN="$REPO_DIR/collector/.venv/bin/python"

cd "$REPO_DIR"

echo "=== 1. FETCH origin/main ==="
git fetch origin main

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$HOME/material-evidence-backup-$TS"
mkdir -p "$BACKUP_DIR"

if [ -f collector/material_evidence_client.py ]; then
  cp -a collector/material_evidence_client.py "$BACKUP_DIR/"
fi

echo "=== 2. INSTALL MATERIAL client only ==="
git show origin/main:collector/material_evidence_client.py > collector/material_evidence_client.py

echo "=== 3. PYTHON COMPILE ==="
"$PYTHON_BIN" -m py_compile collector/material_evidence_client.py
echo "PY_COMPILE=PASS"

echo "=== 4. CONFIG CHECK ==="
"$PYTHON_BIN" - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "collector"))
from material_evidence_client import load_material_runtime

url, secret = load_material_runtime()
print("WEBHOOK_STATUS=SET" if url else "WEBHOOK_STATUS=MISSING")
print("SECRET_STATUS=SET" if secret else "SECRET_STATUS=MISSING")
print("SECRET_NOT_PRINTED=YES")
PY

echo "=== 5. LIVE MATERIAL PROBE ==="
"$PYTHON_BIN" collector/material_evidence_client.py --consumer material-setup-probe

echo
echo "=== RESULT ==="
echo "MATERIAL_CLIENT=PASS"
echo "BACKUP_DIR=$BACKUP_DIR"
echo "EXISTING_SERVICES_NOT_RESTARTED=YES"
