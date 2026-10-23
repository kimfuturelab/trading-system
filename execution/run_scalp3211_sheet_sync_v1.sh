#!/usr/bin/env bash
# 3211-only spreadsheet sync. Cron host is UTC; enforce KST in this wrapper.
set -euo pipefail
export TZ=Asia/Seoul
DOW=$(date +%u)
HM=$(date +%H%M)
if [ "$DOW" -gt 5 ] || [ "$HM" -lt 0800 ] || [ "$HM" -ge 2000 ]; then
  exit 0
fi
cd /home/eogus1215/trading-system/execution
case "${1:-}" in
  capital)
    exec /usr/bin/timeout 105s /home/eogus1215/trading-system-stage3/.venv-sheet-sync/bin/python scalp3211_sheet_capital_sync_v1.py
    ;;
  gold)
    exec /usr/bin/timeout 45s /home/eogus1215/trading-system-stage3/.venv-sheet-sync/bin/python scalp3211_gold_signal_sheet_v1.py
    ;;
  *)
    echo "3211_SYNC_MODE_MUST_BE_capital_OR_gold" >&2
    exit 2
    ;;
esac
