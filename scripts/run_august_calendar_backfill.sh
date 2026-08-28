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

"$VENV/bin/python" -m py_compile "$REPO_DIR/collector/kiwoom_calendar_history_backfill.py"

echo "===== AUGUST BACKFILL — KIWOOM HISTORY ====="
echo "※ 최초 실행은 ka10015 과거데이터 캐시를 만들기 때문에 시간이 걸릴 수 있습니다. 중단돼도 다음 실행에서 캐시 재사용합니다."
"$VENV/bin/python" "$REPO_DIR/collector/kiwoom_calendar_history_backfill.py" \
  --from-date 20260803 \
  --to-date 20260827 \
  --auth-env "$HOME/api-read-v2.env" \
  --theme-master "$HOME/.market-flow-theme-master.json" \
  --overrides "$HOME/market-flow/theme_overrides.json" \
  --history-cache "$HOME/.market-flow-calendar-history-cache-202608.json" \
  --stock-candidate-rank 350 \
  --sleep 0.25 \
  --output "$OUT"

echo
echo "===== SUMMARY ====="
"$VENV/bin/python" - <<'PY'
import json, os
p=os.path.expanduser('~/.market-flow-calendar-backfill-20260801-20260827.json')
d=json.load(open(p, encoding='utf-8'))
print('source =', d.get('source'))
print('rows =', d.get('row_count'))
print('history_target_count =', d.get('history_target_count'))
print('history_cached_count =', d.get('history_cached_count'))
print('history_error_count =', d.get('history_error_count'))
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
        '/ EX', q.get('exclude_count'), f"({q.get('excluded_share_top100_pct')}%)",
        '/ unclassified', f"{q.get('unclassified_share_top100_pct')}%"
    )

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
    q=row.get('diagnostics', {})
    print('actual  =', actual_theme, f'{actual_share:.4f}%', '/', actual_top2)
    print('restore =', restored_theme, f'{restored_share:.4f}%', '/', restored_top2)
    print('share_diff_pp =', round(diff,4))
    print('restore_TOP100 = include', q.get('include_count'), '/ exclude', q.get('exclude_count'), '/ review', q.get('review_count'))
    print('excluded_share =', q.get('excluded_share_top100_pct'), '%')
    passed=(restored_theme==actual_theme and restored_top2==actual_top2 and diff <= 3.0 and int(d.get('history_error_count') or 0) <= 5)
    print('CALIBRATION=' + ('PASS' if passed else 'REVIEW'))
PY

echo
echo "RESULT_JSON=$OUT"
echo "CALIBRATION=PASS 확인 전에는 캘린더에 쓰지 않습니다."
