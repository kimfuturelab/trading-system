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

"$VENV/bin/python" -m py_compile "$REPO_DIR/collector/historical_calendar_backfill.py"

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

# 2026-08-25 실시간 마감 실측값과 복원값 교차검증
actual_theme='반도체_생산'
actual_share=36.3609
actual_top2='PCB(인쇄회로기판)'
row=next((r for r in d.get('rows', []) if r.get('date')=='2026-08-25'), None)
print('\n===== CALIBRATION 2026-08-25 =====')
if not row:
    print('CALIBRATION=REVIEW reason=no_2026-08-25_row')
else:
    restored_theme=row.get('close_top1_theme')
    restored_share=float(row.get('close_top1_share_pct') or 0)
    restored_top2=row.get('close_top2_theme')
    diff=abs(restored_share-actual_share)
    same1=restored_theme==actual_theme
    same2=restored_top2==actual_top2
    print('actual  =', actual_theme, f'{actual_share:.4f}%', '/', actual_top2)
    print('restore =', restored_theme, f'{restored_share:.4f}%', '/', restored_top2)
    print('share_diff_pp =', round(diff,4))
    passed=same1 and diff <= 5.0
    print('CALIBRATION=' + ('PASS' if passed else 'REVIEW'))
    if not same2:
        print('note=2위 테마 불일치 — 백필은 마감 생존주기 기초자료로만 사용하고 순위 신뢰도 추가검토')
PY

echo
echo "RESULT_JSON=$OUT"
echo "CALIBRATION 확인 전에는 --post를 실행하지 않습니다."
