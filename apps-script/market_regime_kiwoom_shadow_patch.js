/**
 * KOSPI 종가 KIWOOM ↔ KRX SHADOW 검증 전용 패치
 *
 * 목적
 * - 기존 시장국면_개선판/B16/행생성/레짐 계산은 절대 건드리지 않는다.
 * - Kiwoom ka20006 (업종일봉조회, KOSPI 001) exact-date 종가를 수집한다.
 * - 기존 fetchKrxKospiExact_() 결과와 비교해 __시장국면_자동조회 J:R에만 기록한다.
 *
 * Script Properties 필요:
 * - KIWOOM_APP_KEY
 * - KIWOOM_APP_SECRET
 *
 * 안전원칙:
 * - 운영반영 = NO
 * - 더 과거 날짜 fallback 금지
 * - 날짜 exact-match만 허용
 * - KRX 미수신/불일치여도 운영계통에는 영향 0
 */

const KIWOOM_SHADOW = Object.freeze({
  HOST: 'https://api.kiwoom.com',
  TOKEN_PATH: '/oauth2/token',
  CHART_PATH: '/api/dostk/chart',
  API_ID: 'ka20006',
  KOSPI_CODE: '001',
  LOG_SHEET: '__시장국면_자동조회',
  START_ROW: 2,
  START_COL: 10, // J
  WIDTH: 9,      // J:R
  TOKEN_CACHE_KEY: 'KIWOOM_SHADOW_TOKEN_V1',
  TOKEN_CACHE_SECONDS: 18000,
  MATCH_TOLERANCE: 0.0001
});


function kiwoomShadowDateKey_(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }

  const text = String(value || '').trim();
  const m = text.match(/^(\d{4})[-/]?(\d{2})[-/]?(\d{2})$/);

  if (!m) {
    throw new Error('KIWOOM SHADOW 날짜 형식 오류: ' + text);
  }

  return m[1] + '-' + m[2] + '-' + m[3];
}


function kiwoomShadowCompactDate_(dateKey) {
  return kiwoomShadowDateKey_(dateKey).replace(/-/g, '');
}


function kiwoomShadowIndexPrice_(value) {
  const raw = String(value == null ? '' : value)
    .replace(/,/g, '')
    .trim();

  if (!raw) return null;

  const unsigned = raw.replace(/^[+-]/, '');
  const n = Math.abs(Number(raw));

  if (!Number.isFinite(n)) return null;

  if (unsigned.indexOf('.') >= 0) {
    return n;
  }

  return n >= 10000 ? n / 100 : n;
}


function ensureKiwoomShadowProperties_() {
  const props = PropertiesService.getScriptProperties();

  const appKey = String(props.getProperty('KIWOOM_APP_KEY') || '').trim();
  const appSecret = String(props.getProperty('KIWOOM_APP_SECRET') || '').trim();

  const missing = [];
  if (!appKey) missing.push('KIWOOM_APP_KEY');
  if (!appSecret) missing.push('KIWOOM_APP_SECRET');

  if (missing.length) {
    throw new Error(
      'Script Properties 누락: ' + missing.join(', ') +
      '. 기존 KRX_AUTH_KEY는 변경하지 마십시오.'
    );
  }

  return { appKey, appSecret };
}


function getKiwoomShadowToken_() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get(KIWOOM_SHADOW.TOKEN_CACHE_KEY);

  if (cached) {
    return cached;
  }

  const auth = ensureKiwoomShadowProperties_();

  const response = UrlFetchApp.fetch(
    KIWOOM_SHADOW.HOST + KIWOOM_SHADOW.TOKEN_PATH,
    {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({
        grant_type: 'client_credentials',
        appkey: auth.appKey,
        secretkey: auth.appSecret
      }),
      muteHttpExceptions: true,
      followRedirects: true
    }
  );

  const status = response.getResponseCode();
  const body = response.getContentText();

  if (status !== 200) {
    throw new Error(
      'Kiwoom 토큰 HTTP ' + status + ' / ' + body.slice(0, 300)
    );
  }

  let json;
  try {
    json = JSON.parse(body);
  } catch (e) {
    throw new Error('Kiwoom 토큰 JSON 파싱 실패: ' + body.slice(0, 300));
  }

  if (Number(json.return_code) !== 0 || !json.token) {
    throw new Error('Kiwoom 토큰 발급 실패: ' + body.slice(0, 300));
  }

  const token = String(json.token);
  cache.put(
    KIWOOM_SHADOW.TOKEN_CACHE_KEY,
    token,
    KIWOOM_SHADOW.TOKEN_CACHE_SECONDS
  );

  return token;
}


function fetchKiwoomKospiExact_(dateString, options) {
  options = options || {};

  const requestDate = kiwoomShadowDateKey_(dateString);
  const baseDt = kiwoomShadowCompactDate_(requestDate);
  const token = getKiwoomShadowToken_();

  const response = UrlFetchApp.fetch(
    KIWOOM_SHADOW.HOST + KIWOOM_SHADOW.CHART_PATH,
    {
      method: 'post',
      contentType: 'application/json',
      headers: {
        authorization: 'Bearer ' + token,
        'api-id': KIWOOM_SHADOW.API_ID
      },
      payload: JSON.stringify({
        inds_cd: KIWOOM_SHADOW.KOSPI_CODE,
        base_dt: baseDt
      }),
      muteHttpExceptions: true,
      followRedirects: true
    }
  );

  const status = response.getResponseCode();
  const body = response.getContentText();

  if (status !== 200) {
    throw new Error(
      'Kiwoom ka20006 HTTP ' + status +
      ' / date=' + requestDate +
      ' / ' + body.slice(0, 300)
    );
  }

  let json;
  try {
    json = JSON.parse(body);
  } catch (e) {
    throw new Error(
      'Kiwoom ka20006 JSON 파싱 실패 / ' + body.slice(0, 300)
    );
  }

  if (Number(json.return_code) !== 0) {
    throw new Error(
      'Kiwoom ka20006 오류 / date=' + requestDate +
      ' / ' + body.slice(0, 300)
    );
  }

  const rows = Array.isArray(json.inds_dt_pole_qry)
    ? json.inds_dt_pole_qry
    : [];

  const exact = rows.find(row => {
    const dt = String(row && row.dt || '')
      .replace(/[^0-9]/g, '')
      .slice(0, 8);

    return dt === baseDt;
  });

  if (!exact) {
    if (options.allowNoData) {
      return null;
    }

    throw new Error(
      'Kiwoom exact-date KOSPI 일봉 없음: ' + requestDate
    );
  }

  const close = kiwoomShadowIndexPrice_(exact.cur_prc);

  if (!Number.isFinite(close) || close <= 0) {
    throw new Error(
      'Kiwoom KOSPI 종가 파싱 실패 / date=' + requestDate +
      ' / cur_prc=' + String(exact.cur_prc)
    );
  }

  return {
    date: requestDate,
    close: close,
    source: 'KIWOOM REST ka20006 / KOSPI 001',
    capturedAt: Utilities.formatDate(
      new Date(),
      'Asia/Seoul',
      'yyyy-MM-dd HH:mm:ss'
    )
  };
}


function openKiwoomShadowLog_() {
  const ss = SpreadsheetApp.openById(CFG.TARGET_SS_ID);
  const sheet = ss.getSheetByName(KIWOOM_SHADOW.LOG_SHEET);

  if (!sheet) {
    throw new Error(
      '검증 로그 시트 없음: ' + KIWOOM_SHADOW.LOG_SHEET
    );
  }

  return sheet;
}


function findKiwoomShadowRow_(sheet, sourceDate) {
  const lastRow = Math.max(sheet.getLastRow(), KIWOOM_SHADOW.START_ROW);
  const count = lastRow - KIWOOM_SHADOW.START_ROW + 1;

  if (count <= 0) return null;

  const dates = sheet
    .getRange(
      KIWOOM_SHADOW.START_ROW,
      KIWOOM_SHADOW.START_COL,
      count,
      1
    )
    .getValues();

  const target = kiwoomShadowDateKey_(sourceDate);

  for (let i = 0; i < dates.length; i++) {
    const value = dates[i][0];
    if (!value) continue;

    try {
      if (kiwoomShadowDateKey_(value) === target) {
        return KIWOOM_SHADOW.START_ROW + i;
      }
    } catch (e) {
      // 검증영역의 비정상 날짜는 무시
    }
  }

  return null;
}


function evaluateKiwoomKrxShadow_(kiwoom, krx) {
  if (!kiwoom) {
    return {
      diff: null,
      status: 'KIWOOM 대기'
    };
  }

  if (!krx) {
    return {
      diff: null,
      status: 'KRX PENDING'
    };
  }

  const diff = kiwoom.close - krx.close;

  return {
    diff,
    status:
      Math.abs(diff) <= KIWOOM_SHADOW.MATCH_TOLERANCE
        ? 'MATCH'
        : 'WARN'
  };
}


function writeKiwoomKrxShadow_(sourceDate, kiwoom, krx, note) {
  const sheet = openKiwoomShadowLog_();
  const normalizedDate = kiwoomShadowDateKey_(sourceDate);

  let row = findKiwoomShadowRow_(sheet, normalizedDate);

  if (!row) {
    row = Math.max(sheet.getLastRow() + 1, KIWOOM_SHADOW.START_ROW);
  }

  const result = evaluateKiwoomKrxShadow_(kiwoom, krx);

  const values = [[
    normalizedDate,
    kiwoom ? kiwoom.close : '',
    krx ? krx.close : '',
    result.diff == null ? '' : result.diff,
    result.status,
    kiwoom ? kiwoom.capturedAt : '',
    krx
      ? Utilities.formatDate(
          new Date(),
          'Asia/Seoul',
          'yyyy-MM-dd HH:mm:ss'
        )
      : '',
    'NO',
    String(note || '병렬검증 전용 / 기존 B열 운영값 변경 없음')
  ]];

  sheet
    .getRange(
      row,
      KIWOOM_SHADOW.START_COL,
      1,
      KIWOOM_SHADOW.WIDTH
    )
    .setValues(values);

  sheet
    .getRange(row, KIWOOM_SHADOW.START_COL + 1, 1, 3)
    .setNumberFormat('#,##0.00');

  return {
    row,
    sourceDate: normalizedDate,
    result: result.status,
    diff: result.diff
  };
}


function kiwoomShadowCollectForDate_(sourceDate) {
  const dateKey = kiwoomShadowDateKey_(sourceDate);

  const kiwoom = fetchKiwoomKospiExact_(
    dateKey,
    { allowNoData: true }
  );

  const krx = fetchKrxKospiExact_(
    dateKey,
    {
      forceRefresh: true,
      allowNoData: true
    }
  );

  return writeKiwoomKrxShadow_(
    dateKey,
    kiwoom,
    krx,
    '병렬검증 전용 / 운영 미반영'
  );
}


function kiwoomShadowCollectToday() {
  const today = Utilities.formatDate(
    new Date(),
    'Asia/Seoul',
    'yyyy-MM-dd'
  );

  const result = kiwoomShadowCollectForDate_(today);

  console.log(JSON.stringify(result));
  return result;
}


function kiwoomShadowRefreshPendingKrx() {
  const sheet = openKiwoomShadowLog_();
  const lastRow = sheet.getLastRow();

  if (lastRow < KIWOOM_SHADOW.START_ROW) {
    return 'NO_ROWS';
  }

  const count = lastRow - KIWOOM_SHADOW.START_ROW + 1;
  const rows = sheet
    .getRange(
      KIWOOM_SHADOW.START_ROW,
      KIWOOM_SHADOW.START_COL,
      count,
      KIWOOM_SHADOW.WIDTH
    )
    .getValues();

  const updated = [];

  rows.forEach((rowValues, index) => {
    const sourceDate = rowValues[0];
    const kiwoomClose = Number(rowValues[1]);

    if (!sourceDate || !Number.isFinite(kiwoomClose) || kiwoomClose <= 0) {
      return;
    }

    const status = String(rowValues[4] || '');

    if (status === 'MATCH' || status === 'WARN') {
      return;
    }

    const dateKey = kiwoomShadowDateKey_(sourceDate);
    const krx = fetchKrxKospiExact_(
      dateKey,
      {
        forceRefresh: true,
        allowNoData: true
      }
    );

    const kiwoom = {
      date: dateKey,
      close: kiwoomClose,
      capturedAt: String(rowValues[5] || '')
    };

    const result = writeKiwoomKrxShadow_(
      dateKey,
      kiwoom,
      krx,
      'KRX 재검증 / 운영 미반영'
    );

    updated.push(result);
  });

  console.log(JSON.stringify(updated));
  return updated;
}


function backfillKiwoomShadowExistingDates() {
  const sheet = openKiwoomShadowLog_();
  const lastRow = sheet.getLastRow();

  if (lastRow < KIWOOM_SHADOW.START_ROW) {
    return 'NO_ROWS';
  }

  const count = lastRow - KIWOOM_SHADOW.START_ROW + 1;
  const dates = sheet
    .getRange(
      KIWOOM_SHADOW.START_ROW,
      KIWOOM_SHADOW.START_COL,
      count,
      1
    )
    .getValues();

  const results = [];

  dates.forEach(row => {
    if (!row[0]) return;

    const dateKey = kiwoomShadowDateKey_(row[0]);

    const kiwoom = fetchKiwoomKospiExact_(
      dateKey,
      { allowNoData: true }
    );

    const krx = fetchKrxKospiExact_(
      dateKey,
      {
        forceRefresh: true,
        allowNoData: true
      }
    );

    results.push(
      writeKiwoomKrxShadow_(
        dateKey,
        kiwoom,
        krx,
        '과거 병렬검증 백필 / 운영 미반영'
      )
    );

    Utilities.sleep(300);
  });

  console.log(JSON.stringify(results));
  return results;
}


function deleteKiwoomShadowTriggers_() {
  const handlers = new Set([
    'kiwoomShadowCollectToday',
    'kiwoomShadowRefreshPendingKrx'
  ]);

  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (handlers.has(trigger.getHandlerFunction())) {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}


function installKiwoomShadowTriggers() {
  deleteKiwoomShadowTriggers_();

  ScriptApp
    .newTrigger('kiwoomShadowCollectToday')
    .timeBased()
    .atHour(15)
    .nearMinute(40)
    .everyDays(1)
    .inTimezone('Asia/Seoul')
    .create();

  ScriptApp
    .newTrigger('kiwoomShadowRefreshPendingKrx')
    .timeBased()
    .atHour(21)
    .nearMinute(10)
    .everyDays(1)
    .inTimezone('Asia/Seoul')
    .create();

  ScriptApp
    .newTrigger('kiwoomShadowRefreshPendingKrx')
    .timeBased()
    .atHour(8)
    .nearMinute(40)
    .everyDays(1)
    .inTimezone('Asia/Seoul')
    .create();

  return 'KIWOOM SHADOW triggers installed: 15:40 / 21:10 / 08:40';
}


function testKiwoomShadowNoWrite() {
  const today = Utilities.formatDate(
    new Date(),
    'Asia/Seoul',
    'yyyy-MM-dd'
  );

  const data = fetchKiwoomKospiExact_(
    today,
    { allowNoData: true }
  );

  console.log(JSON.stringify(data));
  return data;
}
