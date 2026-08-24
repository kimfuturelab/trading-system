const SPREADSHEET_ID = '1POt3TiYivKugAWAeBsOLUAaPLiVGyPy2DrWNgkOvweU';
const RAW_SHEET = '원시_TOP100';

/**
 * One-time setup helper.
 * 1) Replace CHANGE_ME with a long random string.
 * 2) Run this function once in Apps Script editor.
 * 3) Put the same value in market-flow.env as WEBHOOK_SECRET.
 * 4) After setup, you may restore CHANGE_ME in source code.
 */
function setIngestSecretOnce() {
  const secret = 'CHANGE_ME';
  if (!secret || secret === 'CHANGE_ME') {
    throw new Error('Set a real secret before running.');
  }
  PropertiesService.getScriptProperties().setProperty('INGEST_SECRET', secret);
}

function doGet() {
  return json_({ ok: true, service: 'market-flow-ingest', ts: new Date().toISOString() });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return json_({ ok: false, error: 'empty_body' });
    }

    const payload = JSON.parse(e.postData.contents);
    verifySecret_(payload.secret);

    if (payload.type !== 'top100') {
      return json_({ ok: false, error: 'unsupported_type', type: payload.type || null });
    }

    const result = writeTop100_(payload);
    return json_({ ok: true, ...result });
  } catch (err) {
    console.error(err && err.stack ? err.stack : err);
    return json_({ ok: false, error: String(err && err.message ? err.message : err) });
  }
}

function verifySecret_(incoming) {
  const expected = PropertiesService.getScriptProperties().getProperty('INGEST_SECRET');
  if (!expected) throw new Error('INGEST_SECRET is not configured in Script Properties.');
  if (!incoming || incoming !== expected) throw new Error('unauthorized');
}

function writeTop100_(payload) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(RAW_SHEET);
  if (!sheet) throw new Error(`Sheet not found: ${RAW_SHEET}`);

  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('No rows supplied.');
  if (rows.length > 100) throw new Error(`Too many rows: ${rows.length}`);

  // Existing 3.5 raw schema (A:N):
  // 기준시각 | 순위 | 종목코드 | 종목명 | 시장 | 현재가 | 등락률(%) | 거래대금(억원)
  // | 외국인 순매수 | 기관 순매수 | 프로그램 순매수 | 직전 순위 | 순위 변화 | 수집상태
  const values = rows.map(r => [
    r.captured_at || payload.captured_at || '',
    numOrBlank_(r.rank),
    String(r.stock_code || ''),
    String(r.stock_name || ''),
    String(r.market || ''),
    numOrBlank_(r.current_price),
    numOrBlank_(r.change_rate_pct),
    numOrBlank_(r.trading_value_eok),
    '',
    '',
    '',
    numOrBlank_(r.previous_snapshot_rank),
    numOrBlank_(r.rank_change),
    String(r.status || 'OK'),
  ]);

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    const maxDataRows = Math.max(100, sheet.getMaxRows() - 1);

    // Clear old values first.
    sheet.getRange(2, 1, maxDataRows, 14).clearContent();

    // CRITICAL: stock codes must remain text so 005930/000660 keep leading zeros.
    // This must be applied BEFORE setValues().
    sheet.getRange(2, 3, maxDataRows, 1).setNumberFormat('@');

    sheet.getRange(2, 1, values.length, 14).setValues(values);
    sheet.getRange(2, 6, values.length, 1).setNumberFormat('#,##0');
    sheet.getRange(2, 7, values.length, 1).setNumberFormat('0.00');
    sheet.getRange(2, 8, values.length, 1).setNumberFormat('#,##0.00');
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    sheet: RAW_SHEET,
    received: rows.length,
    captured_at: payload.captured_at || '',
    source: payload.source || '',
  };
}

function numOrBlank_(v) {
  if (v === null || v === undefined || v === '') return '';
  const n = Number(v);
  return Number.isFinite(n) ? n : '';
}

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
