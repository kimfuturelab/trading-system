#!/usr/bin/env bash
set -euo pipefail

BRANCH="feature/market-flow-cloud-collector"
REPO_DIR="/tmp/trading-system-market-flow"
VENV="$HOME/.venvs/market-flow-backfill"
OUT="$HOME/.market-flow-calendar-backfill-20260801-20260827.json"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone -b "$BRANCH" https://github.com/kimfuturelab/trading-system.git "$REPO_DIR"
else
  git -C "$REPO_DIR" fetch origin "$BRANCH"
  git -C "$REPO_DIR" checkout "$BRANCH"
  git -C "$REPO_DIR" pull --ff-only origin "$BRANCH"
fi

python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$REPO_DIR/collector/requirements-backfill.txt"

python3 -m py_compile "$REPO_DIR/collector/historical_calendar_backfill.py"

echo "===== AUGUST BACKFILL DRY BUILD ====="
"$VENV/bin/python" "$REPO_DIR/collector/historical_calendar_backfill.py" \
  --from-date 20260801 \
  --to-date 20260827 \
  --theme-master "$HOME/.market-flow-theme-master.json" \
  --overrides "$HOME/market-flow/theme_overrides.json" \
  --output "$OUT"

echo
echo "===== SUMMARY ====="
"$VENV/bin/python" - <<'PY'
import json, os
p=os.path.expanduser('~/.market-flow-calendar-backfill-20260801-20260827.json')
d=json.load(open(p, encoding='utf-8'))
print('rows =', d.get('row_count'))
for r in d.get('rows', []):
    q=r.get('diagnostics', {})
    print(
        r.get('date'),
        r.get('close_top1_theme'),
        f"{r.get('close_top1_share_pct')}%",
        '/', r.get('close_top2_theme'),
        'gap', f"{r.get('top1_top2_gap_pp')}%p",
        '/', r.get('market_weight'),
        '/ leader', r.get('leader_stock'),
        '/ unclassified', f"{q.get('unclassified_share_top100_pct')}%"
    )
PY

echo
echo "RESULT_JSON=$OUT"
echo "검산 전에는 --post를 실행하지 않습니다."
