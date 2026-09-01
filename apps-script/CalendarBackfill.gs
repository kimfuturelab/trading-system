const BACKFILL_CALENDAR_SHEET = '주도테마_캘린더';
const BACKFILL_HEADER_ROW = 5;
const BACKFILL_FIRST_DATA_ROW = 6;
const BACKFILL_COLS = 17;

/**
 * historical_calendar_backfill.py 의 calendar_backfill payload 수신부.
 *
 * 보호 원칙
 * - 자동완료/부분 기록 등 실제 장중·장마감 데이터는 덮어쓰지 않는다.
 * - API 대기/과거복원/빈 날짜만 과거 복원값으로 채운다.
 * - 날짜순으로 다시 정렬해 월간 캘린더를 유지한다.
 */
function writeCalendarBackfill_(payload) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) {
    throw new Error('calendar_backfill rows가 없습니다.');
  }
  if (rows.length > 40) {
    throw new Error(`calendar_backfill 행이 너무 많습니다: ${rows.length}`);
  }

  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(BACKFILL_CALENDAR_SHEET);
  if (!sheet) {
    throw new Error(`시트를 찾을 수 없습니다: ${BACKFILL_CALENDAR_SHEET}`);
  }

  const headers = [
    '기준일', '요일', '오늘 1등 테마(마감)', '마감 점유율', '2위 테마',
    '1·2위 격차', '시장 무게추', '장중 대표주도', '장중 최고점유율',
    '주도교체횟수', '마감 생애주기', '주도 지속시간(분)', '주요 자금 이동',
    '대표주', '시장 자금 상태', '기록상태', '비고'
  ];
  sheet.getRange(BACKFILL_HEADER_ROW, 1, 1, BACKFILL_COLS).setValues([headers]);

  const lastRow = Math.max(sheet.getLastRow(), BACKFILL_FIRST_DATA_ROW - 1);
  const existingCount = Math.max(0, lastRow - BACKFILL_FIRST_DATA_ROW + 1);
  const existingValues = existingCount
    ? sheet.getRange(BACKFILL_FIRST_DATA_ROW, 1, existingCount, BACKFILL_COLS).getValues()
    : [];
  const existingDisplay = existingCount
    ? sheet.getRange(BACKFILL_FIRST_DATA_ROW, 1, existingCount, BACKFILL_COLS).getDisplayValues()
    : [];

  const byDate = {};
  const protectedDates = {};

  existingValues.forEach((row, i) => {
    const date = normalizeCalendarDate_(existingDisplay[i][0] || row[0]);
    if (!date) return;
    byDate[date] = row.slice(0, BACKFILL_COLS);

    const status = String(existingDisplay[i][15] || row[15] || '').trim();
    if (status && status !== 'API 대기' && status !== '과거복원') {
      protectedDates[date] = status;
    }
  });

  let inserted = 0;
  let replaced = 0;
  let skippedProtected = 0;

  rows.forEach(r => {
    const date = normalizeCalendarDate_(r.date);
    if (!date) return;

    if (protectedDates[date]) {
      skippedProtected += 1;
      return;
    }

    const value = [
      date,
      String(r.weekday || ''),
      String(r.close_top1_theme || ''),
      backfillNumOrBlank_(r.close_top1_share_pct),
      String(r.close_top2_theme || ''),
      backfillNumOrBlank_(r.top1_top2_gap_pp),
      String(r.market_weight || ''),
      String(r.intraday_representative_theme || ''),
      backfillNumOrBlank_(r.intraday_max_share_pct),
      backfillNumOrBlank_(r.lead_switch_count),
      String(r.close_lifecycle || ''),
      backfillNumOrBlank_(r.lead_duration_minutes),
      String(r.major_money_move || ''),
      String(r.leader_stock || ''),
      String(r.market_money_state || ''),
      String(r.record_status || '과거복원'),
      String(r.note || '')
    ];

    if (byDate[date]) {
      replaced += 1;
    } else {
      inserted += 1;
    }
    byDate[date] = value;
  });

  const dates = Object.keys(byDate).sort();
  const merged = dates.map(d => byDate[d]);

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    const clearRows = Math.max(
      sheet.getMaxRows() - BACKFILL_FIRST_DATA_ROW + 1,
      merged.length
    );
    if (clearRows > 0) {
      sheet.getRange(
        BACKFILL_FIRST_DATA_ROW,
        1,
        clearRows,
        BACKFILL_COLS
      ).clearContent();
    }

    if (merged.length) {
      if (sheet.getMaxRows() < BACKFILL_FIRST_DATA_ROW + merged.length - 1) {
        sheet.insertRowsAfter(
          sheet.getMaxRows(),
          BACKFILL_FIRST_DATA_ROW + merged.length - 1 - sheet.getMaxRows()
        );
      }

      sheet.getRange(
        BACKFILL_FIRST_DATA_ROW,
        1,
        merged.length,
        BACKFILL_COLS
      ).setValues(merged);

      sheet.getRange(BACKFILL_FIRST_DATA_ROW, 4, merged.length, 1)
        .setNumberFormat('0.00');
      sheet.getRange(BACKFILL_FIRST_DATA_ROW, 6, merged.length, 1)
        .setNumberFormat('0.00');
      sheet.getRange(BACKFILL_FIRST_DATA_ROW, 9, merged.length, 1)
        .setNumberFormat('0.00');
      sheet.getRange(BACKFILL_FIRST_DATA_ROW, 10, merged.length, 1)
        .setNumberFormat('0');
      sheet.getRange(BACKFILL_FIRST_DATA_ROW, 12, merged.length, 1)
        .setNumberFormat('0');
    }
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    sheet: BACKFILL_CALENDAR_SHEET,
    received: rows.length,
    inserted,
    replaced,
    skipped_protected: skippedProtected,
    total_calendar_rows: merged.length,
    source: payload.source || ''
  };
}

function normalizeCalendarDate_(value) {
  if (!value) return '';

  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }

  const s = String(value).trim();
  const m = s.match(/^(\d{4})[-\/.]?(\d{2})[-\/.]?(\d{2})/);
  if (!m) return '';
  return `${m[1]}-${m[2]}-${m[3]}`;
}

function backfillNumOrBlank_(value) {
  if (value === null || value === undefined || value === '') return '';
  const n = Number(value);
  return Number.isFinite(n) ? n : '';
}
