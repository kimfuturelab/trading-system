const SPREADSHEET_ID = '1POt3TiYivKugAWAeBsOLUAaPLiVGyPy2DrWNgkOvweU';
const RAW_SHEET = '원시_TOP100';
const THEME_SHEET = '테마_분류';

/**
 * 최초 1회만 실행.
 * CHANGE_ME 한 곳만 실제 Secret으로 바꾸고 실행한 뒤 다시 CHANGE_ME로 복구해도 됩니다.
 */
function setIngestSecretOnce() {
  const secret = 'CHANGE_ME';
  if (!secret || secret === 'CHANGE_ME') {
    throw new Error('CHANGE_ME를 실제 비밀문자열로 변경한 뒤 실행하세요.');
  }
  PropertiesService.getScriptProperties().setProperty('INGEST_SECRET', secret);
  Logger.log('INGEST_SECRET 저장 완료');
}

function doGet() {
  return json_({
    ok: true,
    service: 'market-flow-ingest',
    project: '3.5단계 자금흐름',
    ts: new Date().toISOString()
  });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return json_({ ok: false, error: 'empty_body' });
    }

    const payload = JSON.parse(e.postData.contents);
    verifySecret_(payload.secret);

    let result;
    if (payload.type === 'top100') {
      result = writeTop100_(payload);
    } else if (payload.type === 'theme_map') {
      result = writeThemeMap_(payload);
    } else {
      return json_({ ok: false, error: 'unsupported_type', type: payload.type || null });
    }

    return json_({ ok: true, ...result });
  } catch (err) {
    console.error(err && err.stack ? err.stack : err);
    return json_({
      ok: false,
      error: String(err && err.message ? err.message : err)
    });
  }
}

function verifySecret_(incoming) {
  const expected = PropertiesService
    .getScriptProperties()
    .getProperty('INGEST_SECRET');

  if (!expected) {
    throw new Error('INGEST_SECRET이 Script Properties에 설정되어 있지 않습니다.');
  }
  if (!incoming || incoming !== expected) {
    throw new Error('unauthorized');
  }
}

function writeTop100_(payload) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(RAW_SHEET);
  if (!sheet) throw new Error(`시트를 찾을 수 없습니다: ${RAW_SHEET}`);

  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('수신된 TOP100 데이터가 없습니다.');
  if (rows.length > 100) throw new Error(`TOP100 초과 데이터 수신: ${rows.length}`);

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
    String(r.status || 'OK')
  ]);

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    const maxDataRows = Math.max(100, sheet.getMaxRows() - 1);
    sheet.getRange(2, 1, maxDataRows, 14).clearContent();
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
    source: payload.source || ''
  };
}

/**
 * 테마_분류 A:J
 * 종목코드 | 종목명 | 등록테마수 | 등록테마목록 | 오늘 대표테마 | 대표테마코드
 * | 매핑상태 | 매핑출처 | 마지막확인시각 | 비고
 *
 * E/F는 종목코드 기준 기존 값을 보존합니다.
 */
function writeThemeMap_(payload) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(THEME_SHEET);
  if (!sheet) throw new Error(`시트를 찾을 수 없습니다: ${THEME_SHEET}`);

  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('수신된 테마 매핑 데이터가 없습니다.');
  if (rows.length > 100) throw new Error(`테마 매핑 100행 초과: ${rows.length}`);

  // 기존 오늘 대표테마/대표테마코드를 종목코드 기준으로 보존.
  const maxDataRows = Math.max(100, sheet.getMaxRows() - 1);
  const oldValues = sheet.getRange(2, 1, maxDataRows, 10).getValues();
  const representativeByCode = {};
  oldValues.forEach(r => {
    const code = String(r[0] || '').trim();
    if (!code) return;
    const repTheme = String(r[4] || '').trim();
    const repCode = String(r[5] || '').trim();
    if (repTheme || repCode) {
      representativeByCode[code] = { repTheme, repCode };
    }
  });

  const values = rows.map(r => {
    const code = String(r.stock_code || '').trim();
    const themes = Array.isArray(r.themes) ? r.themes : [];
    const existing = representativeByCode[code] || { repTheme: '', repCode: '' };
    const themeText = themes
      .map(t => `${String(t.theme_code || '')}:${String(t.theme_name || '')}`)
      .join(' | ');
    const status = String(r.mapping_status || (themes.length ? 'KIWOOM_THEME' : 'NO_THEME'));
    const note = status === 'NO_THEME'
      ? '키움 등록테마 0개 — 종목유형 판별 후 일반주는 보조 매핑 필요'
      : `TOP100 순위 ${numOrBlank_(r.rank)}`;

    return [
      code,
      String(r.stock_name || ''),
      themes.length,
      themeText,
      existing.repTheme,
      existing.repCode,
      status,
      String(r.mapping_source || payload.source || ''),
      String(r.checked_at || ''),
      note
    ];
  });

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    sheet.getRange(2, 1, maxDataRows, 10).clearContent();
    sheet.getRange(2, 1, maxDataRows, 1).setNumberFormat('@');
    sheet.getRange(2, 1, values.length, 10).setValues(values);
    sheet.getRange(2, 3, values.length, 1).setNumberFormat('0');
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    sheet: THEME_SHEET,
    received: rows.length,
    captured_at: payload.captured_at || '',
    source: payload.source || ''
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

function checkConfig() {
  const secretExists = !!PropertiesService
    .getScriptProperties()
    .getProperty('INGEST_SECRET');
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  Logger.log({
    spreadsheet: ss.getName(),
    rawSheetExists: !!ss.getSheetByName(RAW_SHEET),
    themeSheetExists: !!ss.getSheetByName(THEME_SHEET),
    ingestSecretExists: secretExists
  });
}
