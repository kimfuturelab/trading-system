const TECH_POSITION_SPREADSHEET_ID = '13BOm_pS5ultKzoBm9fbtUF8zPgfb3PK5i2ArDXZbPoE';
const TECH_POSITION_DAILY_SHEET = '11_DAILY_기술적위치';

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
  const note = [r.note || '', r.error ? `error=${r.error}` : '', payload.collector_version ? `version=${payload.collector_version}` : '']
    .filter(Boolean)
    .join(' | ');

  sheet.getRange(targetRow, 1, 1, 11).setValues([[
    dateKey, capturedAt, market, String(r.industry_code || ''),
    numOrBlank_(r.current_price), numOrBlank_(r.open_price), numOrBlank_(r.high_price),
    numOrBlank_(r.low_price), numOrBlank_(r.previous_close),
    numOrBlank_(r.high_20d), numOrBlank_(r.low_20d),
  ]]);

  sheet.getRange(targetRow, 18, 1, 4).setValues([[
    String(r.max60_start || ''), numOrBlank_(r.max60_high),
    numOrBlank_(r.max60_low), numOrBlank_(r.max60_volume),
  ]]);

  sheet.getRange(targetRow, 29, 1, 3).setValues([[capturedAt, status, note]]);
}
