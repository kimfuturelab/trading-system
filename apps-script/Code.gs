const TOP100_SPREADSHEET_ID = '1POt3TiYivKugAWAeBsOLUAaPLiVGyPy2DrWNgkOvweU';
const TOP100_RAW_SHEET = '원시_TOP100';
const SUPPLY_SPREADSHEET_ID = '1TT6r5yJGh3G4xRY6ygHIzD_r6iNKmGAbF4wFk3xQG5Q';
const SUPPLY_LIVE_SHEET = '실시간_시장수급';
const SUPPLY_RAW_SHEET = 'API_원시';
const SUPPLY_DAILY_SHEET = '일간기록';

/**
 * One-time setup helper.
 * 1) Replace CHANGE_ME with a long random string.
 * 2) Run this function once in Apps Script editor.
 * 3) Put the same value in collector/.env as INGEST_SECRET.
 * 4) After setup, you may blank the literal again.
 */
function setIngestSecretOnce() {
  const secret = 'CHANGE_ME';
  if (!secret || secret === 'CHANGE_ME') {
    throw new Error('Set a real secret before running.');
  }
  PropertiesService.getScriptProperties().setProperty('INGEST_SECRET', secret);
}

function doGet() {
  return json_({ ok: true, service: 'trading-system-ingest', ts: new Date().toISOString() });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return json_({ ok: false, error: 'empty_body' });
    }

    const payload = JSON.parse(e.postData.contents);
    verifySecret_(payload.secret);

    let result;
    switch (payload.type) {
      case 'top100':
        result = writeTop100_(payload);
        break;
      case 'market_supply':
        result = writeMarketSupply_(payload);
        break;
      default:
        return json_({ ok: false, error: 'unsupported_type', type: payload.type || null });
    }

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
  const ss = SpreadsheetApp.openById(TOP100_SPREADSHEET_ID);
  const sheet = ss.getSheetByName(TOP100_RAW_SHEET);
  if (!sheet) throw new Error(`Sheet not found: ${TOP100_RAW_SHEET}`);

  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('No rows supplied.');
  if (rows.length > 100) throw new Error(`Too many rows: ${rows.length}`);

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
    sheet.getRange(2, 1, maxDataRows, 14).clearContent();
    sheet.getRange(2, 1, values.length, 14).setValues(values);
    sheet.getRange(2, 7, values.length, 1).setNumberFormat('0.00');
    sheet.getRange(2, 8, values.length, 1).setNumberFormat('#,##0.00');
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    sheet: TOP100_RAW_SHEET,
    received: rows.length,
    captured_at: payload.captured_at || '',
    source: payload.source || '',
  };
}

function writeMarketSupply_(payload) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('No market_supply rows supplied.');
  if (rows.length > 10) throw new Error(`Too many market_supply rows: ${rows.length}`);

  const ss = SpreadsheetApp.openById(SUPPLY_SPREADSHEET_ID);
  const live = ss.getSheetByName(SUPPLY_LIVE_SHEET);
  const raw = ss.getSheetByName(SUPPLY_RAW_SHEET);
  const daily = ss.getSheetByName(SUPPLY_DAILY_SHEET);
  if (!live || !raw || !daily) throw new Error('Stage3 supply sheet structure is incomplete.');

  const normalized = rows.map(r => {
    const foreignNet = numOrBlank_(r.foreign_net);
    const institutionNet = numOrBlank_(r.institution_net);
    const programNet = numOrBlank_(r.program_net);
    const numeric = [foreignNet, institutionNet, programNet].filter(v => typeof v === 'number');
    const positiveCount = numeric.length === 3 ? numeric.filter(v => v > 0).length : '';
    const gate = numeric.length !== 3 ? 'PENDING' : (positiveCount >= 2 ? 'PASS' : (positiveCount === 1 ? 'WAIT' : 'BLOCK'));
    return {
      capturedAt: String(r.captured_at || payload.captured_at || ''),
      market: String(r.market || ''),
      individualNet: numOrBlank_(r.individual_net),
      foreignNet,
      institutionNet,
      programNet,
      positiveCount,
      gate,
      investorApi: String(r.investor_api_id || 'ka10051'),
      programApi: String(r.program_api_id || 'ka90005'),
      status: String(r.status || 'OK'),
      source: String(payload.source || 'kiwoom-rest'),
      investorExchange: String(r.investor_exchange || ''),
      programExchange: String(r.program_exchange || ''),
      investorMarketCode: String(r.investor_market_code || ''),
      programMarketCode: String(r.program_market_code || ''),
      version: String(payload.collector_version || ''),
      error: String(r.error || ''),
      note: String(r.note || ''),
    };
  });

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    // API_원시: row 3 is header, latest payload starts at row 4.
    const rawClearRows = Math.max(20, raw.getMaxRows() - 3);
    raw.getRange(4, 1, rawClearRows, 16).clearContent();
    const rawValues = normalized.map(r => [
      r.capturedAt, r.market, r.individualNet, r.foreignNet, r.institutionNet, r.programNet,
      r.investorApi, r.programApi, r.investorExchange, r.programExchange, r.status,
      r.investorMarketCode, r.programMarketCode, r.version, r.error, r.note,
    ]);
    raw.getRange(4, 1, rawValues.length, 16).setValues(rawValues);

    // 실시간_시장수급: fixed KOSPI/KOSDAQ rows 5/6. Preserve G/H sheet formulas.
    normalized.forEach(r => {
      const targetRow = r.market === 'KOSPI' ? 5 : (r.market === 'KOSDAQ' ? 6 : null);
      if (!targetRow) return;
      live.getRange(targetRow, 1, 1, 6).setValues([[
        r.capturedAt, r.market, r.individualNet, r.foreignNet, r.institutionNet, r.programNet,
      ]]);
      live.getRange(targetRow, 9, 1, 4).setValues([[
        r.investorApi, r.programApi, r.status, r.source,
      ]]);
    });

    normalized.forEach(r => upsertDailySupply_(daily, r));
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    sheet: SUPPLY_LIVE_SHEET,
    received: normalized.length,
    captured_at: payload.captured_at || '',
    source: payload.source || '',
  };
}

function upsertDailySupply_(sheet, r) {
  const dateKey = r.capturedAt ? r.capturedAt.slice(0, 10) : Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');
  const firstDataRow = 5;
  const lastRow = sheet.getLastRow();
  let targetRow = null;

  if (lastRow >= firstDataRow) {
    const values = sheet.getRange(firstDataRow, 1, lastRow - firstDataRow + 1, 3).getDisplayValues();
    for (let i = 0; i < values.length; i++) {
      if (String(values[i][0]) === dateKey && String(values[i][2]) === r.market) {
        targetRow = firstDataRow + i;
        break;
      }
    }
  }

  if (!targetRow) targetRow = Math.max(firstDataRow, lastRow + 1);
  sheet.getRange(targetRow, 1, 1, 12).setValues([[
    dateKey,
    r.capturedAt,
    r.market,
    r.individualNet,
    r.foreignNet,
    r.institutionNet,
    r.programNet,
    r.positiveCount,
    r.gate,
    r.status,
    r.version,
    r.note || r.error,
  ]]);
}

function numOrBlank_(v) {
  if (v === null || v === undefined || v === '') return '';
  const n = Number(String(v).replace(/,/g, ''));
  return Number.isFinite(n) ? n : '';
}

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
