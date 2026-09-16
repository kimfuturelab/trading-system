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

function doGet(e) {
  try {
    const type = e && e.parameter ? String(e.parameter.type || '') : '';
    if (type === 'trading_alert_packet') {
      verifySecret_(e && e.parameter ? e.parameter.secret : '');
      return json_(buildTradingAlertPacket_());
    }
    if (type === 'box_control') {
      verifySecret_(e && e.parameter ? e.parameter.secret : '');
      return json_(buildBoxControl_());
    }
    return json_({ ok: true, service: 'trading-system-ingest', ts: new Date().toISOString() });
  } catch (err) {
    console.error(err && err.stack ? err.stack : err);
    return json_({ ok: false, error: String(err && err.message ? err.message : err) });
  }
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
      case 'stock_supply_live':
        result = writeStockSupplyLive_(payload);
        break;
      case 'stock_supply_minute':
        result = writeStockSupplyMinute_(payload);
        break;
      case 'stock_supply_final':
        result = writeStockSupplyFinal_(payload);
        break;
      case 'technical_position':
        result = writeTechnicalPosition_(payload);
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
    const rawClearRows = Math.max(20, raw.getMaxRows() - 3);
    raw.getRange(4, 1, rawClearRows, 16).clearContent();
    const rawValues = normalized.map(r => [
      r.capturedAt, r.market, r.individualNet, r.foreignNet, r.institutionNet, r.programNet,
      r.investorApi, r.programApi, r.investorExchange, r.programExchange, r.status,
      r.investorMarketCode, r.programMarketCode, r.version, r.error, r.note,
    ]);
    raw.getRange(4, 1, rawValues.length, 16).setValues(rawValues);

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


// -----------------------------------------------------------------------------
// SK Hynix trading alert packet | PENDING values are excluded, never fail-closed.
// Source of Truth: trading master 재수차_액션레벨_V1 (2026-09-11+).
// -----------------------------------------------------------------------------
const ALERT_STAGE0_SPREADSHEET_ID = '1u0YBIicGrbA07ND2fzn7vI-kq3ZJqLmhwntgv12M1GU';
const ALERT_STAGE0_SHEET = '0_운용시작_게이트_V1';
const ALERT_REGIME_SPREADSHEET_ID = '1FTmoK13a9COaIyUju89w0GGWzR6W7CuPORJLmhbQ6Wg';
const ALERT_REGIME_SHEET = '시장국면_개선판';
const ALERT_STAGE2_SPREADSHEET_ID = '1KR5RGAplOjsSnUYnb8Ati0w4LEQJkYMBNkFwCiwJ2js';
const ALERT_STAGE2_SHEET = '1_매매톨게이트_DB_신버전';
const ALERT_MATERIAL_SPREADSHEET_ID = '1hwbzCC40rDrBWzIdSOT38oeSm9WBGkicv7oNLLMD5J0';
const ALERT_MATERIAL_SHEET = '05_재료판정';
const ALERT_MONEYFLOW_SHEET = '자금흐름_대시보드';
const ALERT_TECH_SPREADSHEET_ID = '13BOm_pS5ultKzoBm9fbtUF8zPgfb3PK5i2ArDXZbPoE';
const ALERT_TECH_SHEET = '11_DAILY_기술적위치';

function alertTodayIso_() {
  return Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');
}

function alertNowText_() {
  return Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss');
}

function alertNormalizeDate_(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }
  const text = String(value || '').trim();
  const m = text.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);
  if (!m) return '';
  return m[1] + '-' + String(Number(m[2])).padStart(2, '0') + '-' + String(Number(m[3])).padStart(2, '0');
}

function alertFindTodayRow_(sheet, startRow, columnCount) {
  const lastRow = sheet.getLastRow();
  if (lastRow < startRow) return null;
  const values = sheet.getRange(startRow, 1, lastRow - startRow + 1, columnCount).getValues();
  const display = sheet.getRange(startRow, 1, lastRow - startRow + 1, columnCount).getDisplayValues();
  const today = alertTodayIso_();
  for (let i = 0; i < values.length; i++) {
    if (alertNormalizeDate_(values[i][0]) === today || alertNormalizeDate_(display[i][0]) === today) {
      return { values: values[i], display: display[i], row: startRow + i };
    }
  }
  return null;
}

function alertFindLabelValue_(sheet, label, startRow, endRow) {
  const values = sheet.getRange(startRow, 1, endRow - startRow + 1, 2).getDisplayValues();
  for (let i = 0; i < values.length; i++) {
    if (String(values[i][0] || '').trim() === label) return String(values[i][1] || '').trim();
  }
  return '';
}

function alertNumericScore_(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(String(value).replace(/,/g, '').trim());
  if (!isFinite(n)) return null;
  if (n > 0) return 1;
  if (n < 0) return -1;
  return 0;
}

function alertCompressAxis_(internalScores) {
  const valid = internalScores.filter(v => v === -1 || v === 0 || v === 1);
  if (!valid.length) return null;
  const sum = valid.reduce((a, b) => a + b, 0);
  if (sum >= 2) return 1;
  if (sum <= -2) return -1;
  return 0;
}

function alertLevelForScore_(score) {
  if (score === null || score === undefined || !isFinite(Number(score))) return null;
  const n = Number(score);
  if (n >= 3) return 9;
  if (n === 2) return 8;
  if (n === 1) return 7;
  if (n === 0) return 6;
  if (n === -1) return 5;
  if (n === -2) return 4;
  return 3;
}

function alertActionLevelMap_() {
  return {
    '서킷브레이크': 3,
    '사이드카': 4,
    '저점이탈': 5,
    '저점': 6,
    '눌림': 7,
    '박스': 7,
    '눌림/박스': 7,
    '돌파': 8,
    '돌파불타기': 9,
    '돌파 불타기': 9
  };
}

function alertSplitActions_(text) {
  return String(text || '')
    .split(/[·,\/]+/)
    .map(s => s.trim())
    .filter(Boolean);
}

function alertAllowedActions_(marketActionsText, maxLevel) {
  if (!maxLevel) return [];
  const levelMap = alertActionLevelMap_();
  const marketActions = alertSplitActions_(marketActionsText);
  const out = [];
  marketActions.forEach(action => {
    const level = levelMap[action];
    if (level && level <= maxLevel && out.indexOf(action) < 0) out.push(action);
  });
  return out;
}

function alertReadStage0_() {
  const sheet = SpreadsheetApp.openById(ALERT_STAGE0_SPREADSHEET_ID).getSheetByName(ALERT_STAGE0_SHEET);
  if (!sheet) throw new Error('Stage0 sheet missing');
  const top = sheet.getRange('A5:E5').getDisplayValues()[0];
  return {
    date: top[0] || '',
    self_mode: top[1] || '',
    operation_mode: top[2] || '',
    active_account: top[3] || '',
    system_start: top[4] || '',
    actual_day_capital: alertFindLabelValue_(sheet, '실제 단타계좌 자본', 17, 40)
  };
}

function alertReadRegime_() {
  const sheet = SpreadsheetApp.openById(ALERT_REGIME_SPREADSHEET_ID).getSheetByName(ALERT_REGIME_SHEET);
  if (!sheet) throw new Error('Regime sheet missing');
  const hit = alertFindTodayRow_(sheet, 3, 42);
  if (!hit) return { status: '미수신/제외', missing: ['오늘 시장국면'] };
  return {
    status: 'OK',
    date: hit.display[0] || '',
    regime: hit.display[2] || '',
    allowed_time: hit.display[3] || '',
    allowed_trades: hit.display[4] || ''
  };
}

function alertReadStage2_() {
  const sheet = SpreadsheetApp.openById(ALERT_STAGE2_SPREADSHEET_ID).getSheetByName(ALERT_STAGE2_SHEET);
  if (!sheet) throw new Error('Stage2 sheet missing');
  const hit = alertFindTodayRow_(sheet, 5, 11);
  if (!hit) return { status: '미수신/제외', missing: ['오늘 2단계'] };
  return {
    status: 'OK',
    date: hit.display[0] || '',
    total_score: hit.display[6] || '',
    tollgate: hit.display[7] || '',
    base_r: hit.display[8] || '',
    decision_time: hit.display[9] || ''
  };
}

function alertReadMaterial_() {
  const sheet = SpreadsheetApp.openById(ALERT_MATERIAL_SPREADSHEET_ID).getSheetByName(ALERT_MATERIAL_SHEET);
  if (!sheet) throw new Error('Material sheet missing');
  const raw = sheet.getRange(7, 1, 3, 6).getValues();
  const display = sheet.getRange(7, 1, 3, 6).getDisplayValues();
  const internal = [];
  const items = [];
  const missing = [];
  for (let i = 0; i < raw.length; i++) {
    const label = String(display[i][0] || '').trim();
    const score = alertNumericScore_(raw[i][1]);
    const state = String(display[i][5] || '').trim();
    items.push({ label: label, score: score, state: state });
    if (score === null) {
      missing.push(label);
    } else {
      internal.push(score);
    }
  }
  return {
    score: alertCompressAxis_(internal),
    confirmed_count: internal.length,
    missing: missing,
    items: items
  };
}

function alertReadSupply_() {
  const sheet = SpreadsheetApp.openById(SUPPLY_SPREADSHEET_ID).getSheetByName(SUPPLY_LIVE_SHEET);
  if (!sheet) throw new Error('Supply sheet missing');
  const rows = sheet.getRange(5, 1, 2, 12).getValues();
  const display = sheet.getRange(5, 1, 2, 12).getDisplayValues();
  let idx = -1;
  for (let i = 0; i < display.length; i++) {
    if (String(display[i][1] || '').trim() === 'KOSPI') { idx = i; break; }
  }
  if (idx < 0) return { score: null, missing: ['KOSPI 수급'] };

  const labels = ['외국인 현물', '기관 현물', '프로그램'];
  const cols = [3, 4, 5];
  const internal = [];
  const missing = [];
  const values = {};
  for (let i = 0; i < cols.length; i++) {
    const rawValue = rows[idx][cols[i]];
    const n = Number(String(rawValue === null || rawValue === undefined ? '' : rawValue).replace(/,/g, ''));
    if (!isFinite(n)) {
      missing.push(labels[i]);
      values[labels[i]] = null;
    } else {
      values[labels[i]] = n;
      internal.push(n > 0 ? 1 : (n < 0 ? -1 : 0));
    }
  }
  return {
    score: alertCompressAxis_(internal),
    as_of: String(display[idx][0] || ''),
    values: values,
    missing: missing,
    data_status: String(display[idx][10] || '')
  };
}

function alertReadMoneyFlow_() {
  const sheet = SpreadsheetApp.openById(TOP100_SPREADSHEET_ID).getSheetByName(ALERT_MONEYFLOW_SHEET);
  if (!sheet) return { status: '미수신/제외' };
  const v = sheet.getRange('A4:M10').getDisplayValues();
  return {
    status: 'OK',
    leader: v[0][1] || '',
    share: v[0][3] || '',
    market_weight: v[0][9] || '',
    lifecycle: v[0][11] || '',
    flow_30m_text: v[1][1] || '',
    delta_30m: v[1][3] || '',
    money_move: v[1][7] || '',
    market_money_state: v[1][9] || '',
    as_of: v[1][11] || ''
  };
}

function alertReadChart_() {
  const sheet = SpreadsheetApp.openById(ALERT_TECH_SPREADSHEET_ID).getSheetByName(ALERT_TECH_SHEET);
  if (!sheet) return { score: null, missing: ['기술적 위치 시트'] };
  const lastRow = sheet.getLastRow();
  if (lastRow < 4) return { score: null, missing: ['오늘 KOSPI 기술적 위치'] };
  const raw = sheet.getRange(4, 1, lastRow - 3, 35).getValues();
  const display = sheet.getRange(4, 1, lastRow - 3, 35).getDisplayValues();
  const today = alertTodayIso_();
  let idx = -1;
  for (let i = 0; i < display.length; i++) {
    if (alertNormalizeDate_(raw[i][0]) === today && String(display[i][2] || '').trim() === 'KOSPI') {
      idx = i;
      break;
    }
  }
  if (idx < 0) return { score: null, missing: ['오늘 KOSPI 기술적 위치'] };

  const missing = [];
  const boxHigh = String(display[idx][12] || '').trim();
  const boxLow = String(display[idx][13] || '').trim();
  const boxState = String(display[idx][15] || '').trim();
  const boxPos = String(display[idx][16] || '').trim();

  if (!boxHigh || !boxLow || !boxPos || boxState === 'PENDING') missing.push('운영 BOX 위치');
  // Box round-trip count is not yet persisted in the DAILY operational row.
  missing.push('박스 왕복횟수');

  return {
    score: null,
    as_of: String(display[idx][1] || ''),
    current_price: String(display[idx][4] || ''),
    box_high: boxHigh,
    box_low: boxLow,
    box_state: boxState,
    box_position: boxPos,
    data_status: String(display[idx][33] || ''),
    missing: missing
  };
}

function buildTradingAlertPacket_() {
  const stage0 = alertReadStage0_();
  const stage1 = alertReadRegime_();
  const stage2 = alertReadStage2_();
  const material = alertReadMaterial_();
  const supply = alertReadSupply_();
  const moneyflow = alertReadMoneyFlow_();
  const chart = alertReadChart_();

  const axes = [];
  if (material.score !== null) axes.push(material.score);
  if (supply.score !== null) axes.push(supply.score);
  if (chart.score !== null) axes.push(chart.score);

  const score = axes.length ? axes.reduce((a, b) => a + b, 0) : null;
  const maxLevel = alertLevelForScore_(score);
  const marketActionsText = stage1 && stage1.allowed_trades ? stage1.allowed_trades : '';
  const allowedActions = alertAllowedActions_(marketActionsText, maxLevel);

  const excluded = [];
  (material.missing || []).forEach(x => excluded.push('재료:' + x));
  (supply.missing || []).forEach(x => excluded.push('수급:' + x));
  (chart.missing || []).forEach(x => excluded.push('차트:' + x));

  return {
    ok: true,
    type: 'trading_alert_packet',
    symbol: 'SK하이닉스',
    code: '000660',
    as_of: alertNowText_(),
    trade_date: alertTodayIso_(),
    stage0: stage0,
    stage1: stage1,
    stage2: stage2,
    material: material,
    supply: supply,
    moneyflow: moneyflow,
    chart: chart,
    score: score,
    max_level: maxLevel,
    allowed_actions: allowedActions,
    excluded: excluded,
    available_axis_count: axes.length
  };
}


// -----------------------------------------------------------------------------
// BOX control-plane | MASTER 00_수동입력 -> server local cache sync.
// Human-controlled STRUCT BOX only. No price/cycle/order calculation here.
// Source of Truth: Trading Master 00_수동입력 KOSPI 운영 BOX 변경이력.
// -----------------------------------------------------------------------------
const BOX_CONTROL_MASTER_SPREADSHEET_ID = '1D2Dl0AtVCWvwn9R8xa-EnovERQVsUpQaKP4Z2B1Y21s';
const BOX_CONTROL_MASTER_SHEET = '00_수동입력';

function boxControlDateText_(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }
  const text = String(value || '').trim();
  const m = text.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);
  if (!m) return '';
  return m[1] + '-' + String(Number(m[2])).padStart(2, '0') + '-' + String(Number(m[3])).padStart(2, '0');
}

function boxControlNumber_(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(String(value).replace(/,/g, '').trim());
  return isFinite(n) ? n : null;
}

function boxControlSelectLatest_(values, todayIso) {
  let headerRow = -1;
  for (let i = 0; i < values.length; i++) {
    const a = String(values[i][0] || '').trim();
    const b = String(values[i][1] || '').trim();
    const c = String(values[i][2] || '').trim();
    if (a === '적용일' && b === 'BOX_HIGH' && c === 'BOX_LOW') {
      headerRow = i;
      break;
    }
  }
  if (headerRow < 0) throw new Error('BOX control header not found in MASTER 00_수동입력');

  let best = null;
  for (let i = headerRow + 1; i < values.length; i++) {
    const effectiveDate = boxControlDateText_(values[i][0]);
    if (!effectiveDate || effectiveDate > todayIso) continue;

    const high = boxControlNumber_(values[i][1]);
    const low = boxControlNumber_(values[i][2]);
    if (high === null || low === null || high <= low) continue;

    if (!best || effectiveDate > best.effective_date || (effectiveDate === best.effective_date && i > best._row_index)) {
      best = {
        effective_date: effectiveDate,
        box_high: high,
        box_low: low,
        memo: String(values[i][3] || '').trim(),
        _row_index: i
      };
    }
  }
  if (!best) throw new Error('No valid KOSPI BOX row effective today or earlier');
  return best;
}

function buildBoxControl_() {
  const ss = SpreadsheetApp.openById(BOX_CONTROL_MASTER_SPREADSHEET_ID);
  const sheet = ss.getSheetByName(BOX_CONTROL_MASTER_SHEET);
  if (!sheet) throw new Error('MASTER 00_수동입력 sheet missing');

  const lastRow = Math.max(sheet.getLastRow(), 1);
  const values = sheet.getRange(1, 1, lastRow, 4).getValues();
  const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');
  const hit = boxControlSelectLatest_(values, today);

  return {
    ok: true,
    type: 'box_control',
    market: 'KOSPI',
    effective_date: hit.effective_date,
    box_high: hit.box_high,
    box_low: hit.box_low,
    box_signature: String(hit.box_high) + '|' + String(hit.box_low),
    source: 'MASTER_00_수동입력',
    updated_at: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss'),
    status: 'READY',
    note: hit.memo || ''
  };
}

function testBoxControlPure_() {
  const rows = [
    ['다른섹션', '', '', ''],
    ['KOSPI 운영 BOX 변경이력 | 구조가 바뀔 때만 다음 빈 행에 입력', '', '', ''],
    ['적용일', 'BOX_HIGH', 'BOX_LOW', '메모'],
    ['2026-09-07', 7216, 6400, '초기'],
    ['2026-09-20', 7300, 6500, '미래']
  ];

  const hit = boxControlSelectLatest_(rows, '2026-09-16');
  if (hit.effective_date !== '2026-09-07') throw new Error('effective_date mismatch');
  if (hit.box_high !== 7216 || hit.box_low !== 6400) throw new Error('BOX value mismatch');

  const invalid = [
    ['적용일', 'BOX_HIGH', 'BOX_LOW', '메모'],
    ['2026-09-07', 6300, 6400, 'bad']
  ];
  let rejected = false;
  try {
    boxControlSelectLatest_(invalid, '2026-09-16');
  } catch (err) {
    rejected = true;
  }
  if (!rejected) throw new Error('invalid BOX range was not rejected');

  return {
    ok: true,
    test: 'BOX_CONTROL_PURE',
    effective_date: hit.effective_date,
    box_high: hit.box_high,
    box_low: hit.box_low,
    signature: String(hit.box_high) + '|' + String(hit.box_low),
    invalid_range_rejected: true
  };
}
