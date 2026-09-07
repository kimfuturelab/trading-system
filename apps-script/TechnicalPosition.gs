const TECH_POSITION_SPREADSHEET_ID = '13BOm_pS5ultKzoBm9fbtUF8zPgfb3PK5i2ArDXZbPoE';
const TECH_POSITION_DAILY_SHEET = '11_DAILY_기술적위치';
const TRADING_MASTER_SPREADSHEET_ID = '1D2Dl0AtVCWvwn9R8xa-EnovERQVsUpQaKP4Z2B1Y21s';
const TRADING_MASTER_MANUAL_SHEET = '00_수동입력';

function writeTechnicalPosition_(payload) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('No technical_position rows supplied.');

  const ss = SpreadsheetApp.openById(TECH_POSITION_SPREADSHEET_ID);
  const sheet = ss.getSheetByName(TECH_POSITION_DAILY_SHEET);
  if (!sheet) throw new Error(`Sheet not found: ${TECH_POSITION_DAILY_SHEET}`);

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    rows.forEach(r => upsertTechnicalPosition_(sheet, r, payload));
    sortTechnicalPositionDaily_(sheet);
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    sheet: TECH_POSITION_DAILY_SHEET,
    received: rows.length,
    captured_at: payload.captured_at || '',
    source: payload.source || '',
  };
}

function getMasterKospiBox_(tradeDate) {
  const master = SpreadsheetApp.openById(TRADING_MASTER_SPREADSHEET_ID);
  const sheet = master.getSheetByName(TRADING_MASTER_MANUAL_SHEET);
  if (!sheet) throw new Error(`Sheet not found: ${TRADING_MASTER_MANUAL_SHEET}`);

  // A32:D60 = 적용일 / BOX_HIGH / BOX_LOW / 메모. 최신 적용일 <= 거래일을 사용한다.
  const values = sheet.getRange('A32:D60').getValues();
  const target = new Date(String(tradeDate).slice(0, 10) + 'T00:00:00+09:00');
  let best = null;

  values.forEach(row => {
    const effectiveDate = row[0];
    const high = Number(row[1]);
    const low = Number(row[2]);
    if (!(effectiveDate instanceof Date) || !isFinite(high) || !isFinite(low) || high <= low) return;
    if (effectiveDate.getTime() > target.getTime()) return;
    if (!best || effectiveDate.getTime() >= best.date.getTime()) {
      best = {date: effectiveDate, high, low};
    }
  });

  return best;
}

function upsertTechnicalPosition_(sheet, r, payload) {
  const dateKey = String(r.trade_date || '').trim();
  const market = String(r.market || '').trim();
  if (!dateKey || !market) throw new Error('technical_position requires trade_date and market');

  const firstDataRow = 4;
  const lastRow = Math.max(firstDataRow, sheet.getLastRow());
  const keys = sheet.getRange(firstDataRow, 1, lastRow - firstDataRow + 1, 3).getDisplayValues();
  let targetRow = null;
  let firstBlankRow = null;

  for (let i = 0; i < keys.length; i++) {
    const rowNo = firstDataRow + i;
    const existingDate = String(keys[i][0] || '').trim();
    const existingMarket = String(keys[i][2] || '').trim();
    if (existingDate === dateKey && existingMarket === market) {
      targetRow = rowNo;
      break;
    }
    if (!existingDate && !existingMarket && firstBlankRow === null) firstBlankRow = rowNo;
  }
  if (!targetRow) targetRow = firstBlankRow || (lastRow + 1);

  const capturedAt = String(r.captured_at || payload.captured_at || '');
  const status = String(r.status || 'OK');
  const noteParts = [
    payload.source ? `source=${payload.source}` : '',
    r.note || '',
    r.error ? `error=${r.error}` : '',
    payload.collector_version ? `version=${payload.collector_version}` : ''
  ].filter(Boolean);

  sheet.getRange(targetRow, 1, 1, 11).setValues([[
    dateKey, capturedAt, market, String(r.industry_code || ''),
    numOrBlank_(r.current_price), numOrBlank_(r.open_price), numOrBlank_(r.high_price),
    numOrBlank_(r.low_price), numOrBlank_(r.previous_close),
    numOrBlank_(r.high_20d), numOrBlank_(r.low_20d),
  ]]);

  // KOSPI 운영 BOX의 수동 Source of Truth는 트레이딩 마스터 00_수동입력이다.
  // 거래일별로 최신 적용일 <= 거래일 값을 숫자로 스냅샷 저장하므로 과거 BOX가 나중 수정으로 덮이지 않는다.
  if (market === 'KOSPI') {
    const box = getMasterKospiBox_(dateKey);
    if (box) {
      sheet.getRange(targetRow, 13, 1, 2).setValues([[box.high, box.low]]);
      noteParts.push(`manual_box_source=TRADING_MASTER; box_effective=${Utilities.formatDate(box.date, 'Asia/Seoul', 'yyyy-MM-dd')}`);
    } else {
      noteParts.push('manual_box_source=TRADING_MASTER; box=PENDING');
    }
  }

  sheet.getRange(targetRow, 18, 1, 4).setValues([[
    String(r.max60_start || ''), numOrBlank_(r.max60_high),
    numOrBlank_(r.max60_low), numOrBlank_(r.max60_volume),
  ]]);

  sheet.getRange(targetRow, 29, 1, 3).setValues([[capturedAt, status, noteParts.join(' | ')]]);
}

function sortTechnicalPositionDaily_(sheet) {
  const firstDataRow = 4;
  const lastRow = sheet.getLastRow();
  if (lastRow < firstDataRow) return;
  sheet.getRange(firstDataRow, 1, lastRow - firstDataRow + 1, 31)
    .sort([{column: 1, ascending: true}, {column: 3, ascending: true}]);
}
