const HYNIX_KRX_REFERENCE_SHEET = 'KRX_공식기준값';
const HYNIX_KRX_CODE = '000660';
const HYNIX_KRX_ENDPOINT = 'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd';
const HYNIX_KRX_TZ = 'Asia/Seoul';
const HYNIX_KRX_KEY_PROPERTY = 'KRX_AUTH_KEY';

/**
 * One-time setup from the bound spreadsheet.
 * The KRX key is stored only in Script Properties, never in cells or source code.
 */
function setupHynixKrxAuthKey() {
  const ui = SpreadsheetApp.getUi();
  const result = ui.prompt(
    'KRX OPEN API 인증키 등록',
    'KRX Data Marketplace에서 발급받은 인증키를 입력하세요. 시트에는 저장되지 않습니다.',
    ui.ButtonSet.OK_CANCEL
  );
  if (result.getSelectedButton() !== ui.Button.OK) return;
  const key = String(result.getResponseText() || '').trim();
  if (!key) throw new Error('KRX 인증키가 비어 있습니다.');
  PropertiesService.getScriptProperties().setProperty(HYNIX_KRX_KEY_PROPERTY, key);
  ui.alert('KRX 인증키를 Script Properties에 저장했습니다.');
}

/**
 * Fetches the most recent official KRX trading-day row before today for SK hynix.
 * No GOOGLEFINANCE / portal / article fallback is allowed.
 */
function refreshHynixKrxReference() {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(HYNIX_KRX_REFERENCE_SHEET);
    if (!sheet) throw new Error('Sheet not found: ' + HYNIX_KRX_REFERENCE_SHEET);

    const key = PropertiesService.getScriptProperties().getProperty(HYNIX_KRX_KEY_PROPERTY);
    if (!key) {
      writeHynixKrxFailure_(sheet, 'KRX_AUTH_KEY_MISSING');
      throw new Error('KRX_AUTH_KEY is not configured in Script Properties.');
    }

    const now = new Date();
    const serviceDate = Utilities.formatDate(now, HYNIX_KRX_TZ, 'yyyy-MM-dd');
    let official = null;
    let lastError = '';

    // Search backwards because weekends/holidays have no daily row.
    for (let offset = 1; offset <= 10; offset++) {
      const d = new Date(now.getTime() - offset * 24 * 60 * 60 * 1000);
      const basDd = Utilities.formatDate(d, HYNIX_KRX_TZ, 'yyyyMMdd');
      try {
        const row = fetchHynixKrxDailyRow_(key, basDd);
        if (row) {
          official = row;
          break;
        }
      } catch (err) {
        lastError = String(err && err.message ? err.message : err);
      }
    }

    if (!official) {
      writeHynixKrxFailure_(sheet, lastError || 'KRX_PREV_TRADE_DAY_NOT_FOUND');
      throw new Error(lastError || 'No official KRX row found in the last 10 calendar days.');
    }

    const prevTradeDate = krxDate_(official.BAS_DD);
    const prevClose = krxInt_(official.TDD_CLSPRC);
    const changeWon = krxInt_(official.CMPPREVDD_PRC);
    const changeRate = krxNumber_(official.FLUC_RT) / 100;
    if (!prevTradeDate || !Number.isFinite(prevClose) || prevClose <= 0) {
      writeHynixKrxFailure_(sheet, 'KRX_INVALID_OFFICIAL_ROW');
      throw new Error('KRX row is missing BAS_DD or TDD_CLSPRC.');
    }

    const serviceDateObj = dateOnlyKst_(serviceDate);
    const receivedAt = new Date();
    const name = String(official.ISU_NM || 'SK하이닉스').trim();
    const values = [[
      serviceDateObj,
      HYNIX_KRX_CODE,
      name,
      prevTradeDate,
      prevClose,
      changeWon,
      changeRate,
      receivedAt,
      'OK',
      'KRX_OPEN_API / 유가증권 일별매매정보',
      'BAS_DD=' + String(official.BAS_DD || '') + ' / ISU_SRT_CD=' + String(official.ISU_SRT_CD || ''),
      '공식 기준값. fallback 없음'
    ]];

    sheet.getRange('A4:L4').setValues(values);
    sheet.getRange('A4').setNumberFormat('yyyy-mm-dd');
    sheet.getRange('D4').setNumberFormat('yyyy-mm-dd');
    sheet.getRange('E4:F4').setNumberFormat('#,##0');
    sheet.getRange('G4').setNumberFormat('0.00%');
    sheet.getRange('H4').setNumberFormat('yyyy-mm-dd hh:mm:ss');
    upsertHynixKrxHistory_(sheet, values[0]);
    SpreadsheetApp.flush();
    return {
      ok: true,
      service_date: serviceDate,
      prev_trade_date: Utilities.formatDate(prevTradeDate, HYNIX_KRX_TZ, 'yyyy-MM-dd'),
      source: 'KRX_OPEN_API'
    };
  } finally {
    lock.releaseLock();
  }
}

function fetchHynixKrxDailyRow_(key, basDd) {
  const response = UrlFetchApp.fetch(HYNIX_KRX_ENDPOINT, {
    method: 'post',
    contentType: 'application/json; charset=utf-8',
    headers: { AUTH_KEY: key },
    payload: JSON.stringify({ basDd: basDd }),
    muteHttpExceptions: true,
    followRedirects: true
  });

  const status = response.getResponseCode();
  const body = response.getContentText('UTF-8');
  if (status !== 200) throw new Error('KRX_HTTP_' + status + ': ' + body.slice(0, 180));

  let json;
  try {
    json = JSON.parse(body);
  } catch (err) {
    throw new Error('KRX_NON_JSON_RESPONSE');
  }
  const rows = Array.isArray(json.OutBlock_1) ? json.OutBlock_1 : [];
  if (!rows.length) return null;

  return rows.find(function (row) {
    const shortCode = String(row.ISU_SRT_CD || '').replace(/[^0-9]/g, '');
    const fullCode = String(row.ISU_CD || '').replace(/[^0-9]/g, '');
    return shortCode === HYNIX_KRX_CODE || fullCode.endsWith(HYNIX_KRX_CODE);
  }) || null;
}

function writeHynixKrxFailure_(sheet, reason) {
  const serviceDate = Utilities.formatDate(new Date(), HYNIX_KRX_TZ, 'yyyy-MM-dd');
  sheet.getRange('A4:L4').clearContent();
  sheet.getRange('A4:C4').setValues([[dateOnlyKst_(serviceDate), HYNIX_KRX_CODE, 'SK하이닉스']]);
  sheet.getRange('I4:L4').setValues([[
    'KRX_ERROR',
    'KRX_OPEN_API / 유가증권 일별매매정보',
    String(reason || 'UNKNOWN').slice(0, 240),
    '공식값 미수신 → 전일종가·등락률 BLOCK'
  ]]);
  sheet.getRange('A4').setNumberFormat('yyyy-mm-dd');
  SpreadsheetApp.flush();
}

function upsertHynixKrxHistory_(sheet, row) {
  const headerRow = 6;
  const firstDataRow = 7;
  if (sheet.getRange(headerRow, 1).isBlank()) {
    sheet.getRange(headerRow, 1, 1, 12).setValues([[
      '서비스일','종목코드','종목명','직전거래일','전일종가','전일대비','KRX 등락률','수신시각','상태','원천','검증','비고'
    ]]);
  }

  const serviceKey = Utilities.formatDate(row[0], HYNIX_KRX_TZ, 'yyyy-MM-dd');
  const lastRow = Math.max(sheet.getLastRow(), headerRow);
  let target = null;
  if (lastRow >= firstDataRow) {
    const dates = sheet.getRange(firstDataRow, 1, lastRow - firstDataRow + 1, 1).getValues();
    for (let i = 0; i < dates.length; i++) {
      const v = dates[i][0];
      if (v instanceof Date && Utilities.formatDate(v, HYNIX_KRX_TZ, 'yyyy-MM-dd') === serviceKey) {
        target = firstDataRow + i;
        break;
      }
    }
  }
  if (!target) target = Math.max(firstDataRow, lastRow + 1);
  sheet.getRange(target, 1, 1, 12).setValues([row]);
  sheet.getRange(target, 1).setNumberFormat('yyyy-mm-dd');
  sheet.getRange(target, 4).setNumberFormat('yyyy-mm-dd');
  sheet.getRange(target, 5, 1, 2).setNumberFormat('#,##0');
  sheet.getRange(target, 7).setNumberFormat('0.00%');
  sheet.getRange(target, 8).setNumberFormat('yyyy-mm-dd hh:mm:ss');
}

/**
 * Installs two pre-market retries. No daily human check is required.
 */
function createHynixKrxReferenceTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === 'refreshHynixKrxReference') ScriptApp.deleteTrigger(trigger);
  });
  ScriptApp.newTrigger('refreshHynixKrxReference')
    .timeBased().everyDays(1).atHour(7).nearMinute(30).inTimezone(HYNIX_KRX_TZ).create();
  ScriptApp.newTrigger('refreshHynixKrxReference')
    .timeBased().everyDays(1).atHour(8).nearMinute(30).inTimezone(HYNIX_KRX_TZ).create();
}

function verifyHynixKrxReference() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(HYNIX_KRX_REFERENCE_SHEET);
  if (!sheet) throw new Error('Sheet not found: ' + HYNIX_KRX_REFERENCE_SHEET);
  const row = sheet.getRange('A4:L4').getDisplayValues()[0];
  if (row[8] !== 'OK') throw new Error('KRX reference is not OK: ' + row[8] + ' / ' + row[10]);
  return {
    service_date: row[0],
    code: row[1],
    prev_trade_date: row[3],
    prev_close: row[4],
    status: row[8],
    source: row[9]
  };
}

function dateOnlyKst_(yyyyMmDd) {
  const parts = String(yyyyMmDd).split('-').map(Number);
  return new Date(parts[0], parts[1] - 1, parts[2], 0, 0, 0);
}

function krxDate_(value) {
  const s = String(value || '').replace(/[^0-9]/g, '');
  if (s.length !== 8) return null;
  return new Date(Number(s.slice(0,4)), Number(s.slice(4,6)) - 1, Number(s.slice(6,8)), 0, 0, 0);
}

function krxNumber_(value) {
  const n = Number(String(value == null ? '' : value).replace(/,/g, '').trim());
  return Number.isFinite(n) ? n : NaN;
}

function krxInt_(value) {
  const n = krxNumber_(value);
  return Number.isFinite(n) ? Math.round(n) : NaN;
}
