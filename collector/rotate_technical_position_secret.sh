#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${TECH_POSITION_ENV_FILE:-/home/eogus1215/technical-position.env}"
PYTHON_BIN="${PYTHON_BIN:-/home/eogus1215/trading-system-stage3/collector/.venv/bin/python}"

ENV_FILE="$ENV_FILE" "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import os
import secrets

path = Path(os.environ["ENV_FILE"]).expanduser()
if not path.exists():
    raise SystemExit(f"ENV file not found: {path}")

lines = path.read_text(encoding="utf-8").splitlines()
new_secret = secrets.token_urlsafe(48)
out = []
replaced = False
for raw in lines:
    if raw.startswith("TECH_POSITION_INGEST_SECRET="):
        out.append("TECH_POSITION_INGEST_SECRET=" + new_secret)
        replaced = True
    else:
        out.append(raw)
if not replaced:
    out.append("TECH_POSITION_INGEST_SECRET=" + new_secret)

path.write_text("\n".join(out) + "\n", encoding="utf-8")
path.chmod(0o600)

secret_file = path.with_suffix(path.suffix + ".secret")
secret_file.write_text(new_secret + "\n", encoding="utf-8")
secret_file.chmod(0o600)

print("ROTATE=PASS")
print(f"ENV_FILE={path}")
print(f"COPY_FROM_FILE={secret_file}")
print("SECRET_NOT_PRINTED=YES")
PY
