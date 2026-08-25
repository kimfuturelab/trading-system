const SPREADSHEET_ID = '1POt3TiYivKugAWAeBsOLUAaPLiVGyPy2DrWNgkOvweU';
const RAW_SHEET = '원시_TOP100';
const THEME_SHEET = '테마_분류';
const THEME_AGG_SHEET = '테마_집계';
const SNAPSHOT_SHEET = '자금이동_스냅샷';
const FLOW_ENGINE_SHEET = '흐름_판정_엔진';
const DELTA_TOLERANCE_MINUTES = 3;

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
    } else if (payload.type === 'theme_aggregate') {
      result = writeThemeAggregate_(payload);
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
 * 테마_분류 A:M
 * E/F 대표테마는 theme_aggregate에서 별도 갱신합니다.
 */
function writeThemeMap_(payload) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(THEME_SHEET);
  if (!sheet) throw new Error(`시트를 찾을 수 없습니다: ${THEME_SHEET}`);

  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('수신된 테마 매핑 데이터가 없습니다.');
  if (rows.length > 100) throw new Error(`테마 매핑 100행 초과: ${rows.length}`);

  if (sheet.getMaxColumns() < 13) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), 13 - sheet.getMaxColumns());
  }

  const headers = [
    '종목코드', '종목명', '등록테마수', '등록테마목록', '오늘 대표테마', '대표테마코드',
    '매핑상태', '매핑출처', '마지막확인시각', '비고', '종목유형', '집계대상', '테마보완필요'
  ];
  sheet.getRange(1, 1, 1, 13).setValues([headers]);

  const maxDataRows = Math.max(100, sheet.getMaxRows() - 1);
  const oldValues = sheet.getRange(2, 1, maxDataRows, 13).getValues();
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
    const instrumentType = String(r.instrument_type || 'UNKNOWN');
    const aggregationPolicy = String(r.aggregation_policy || 'REVIEW');
    const needsEnrichment = !!r.needs_theme_enrichment;

    let note;
    if (aggregationPolicy === 'EXCLUDE') {
      note = `집계 제외 종목유형: ${instrumentType}`;
    } else if (needsEnrichment) {
      note = '일반주인데 키움 등록테마 0개 — 보조 테마 매핑 필요';
    } else if (status === 'CACHE_MISSING') {
      note = '테마 캐시 누락 — 재조회 필요';
    } else {
      note = `TOP100 순위 ${numOrBlank_(r.rank)}`;
    }

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
      note,
      instrumentType,
      aggregationPolicy,
      needsEnrichment ? 'Y' : 'N'
    ];
  });

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    sheet.getRange(2, 1, maxDataRows, 13).clearContent();
    sheet.getRange(2, 1, maxDataRows, 1).setNumberFormat('@');
    sheet.getRange(2, 1, values.length, 13).setValues(values);
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

/**
 * theme_aggregate 한 번으로 4개 영역을 갱신합니다.
 * 1) 테마_분류 E:F 대표테마
 * 2) 테마_집계 현재 집계
 * 3) 자금이동_스냅샷 누적 기록 + Δ5/15/30/60
 * 4) 흐름_판정_엔진 현재 상태
 */
function writeThemeAggregate_(payload) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const themeSheet = ss.getSheetByName(THEME_SHEET);
  const aggSheet = ss.getSheetByName(THEME_AGG_SHEET);
  const snapshotSheet = ss.getSheetByName(SNAPSHOT_SHEET);
  const flowSheet = ss.getSheetByName(FLOW_ENGINE_SHEET);

  if (!themeSheet) throw new Error(`시트를 찾을 수 없습니다: ${THEME_SHEET}`);
  if (!aggSheet) throw new Error(`시트를 찾을 수 없습니다: ${THEME_AGG_SHEET}`);
  if (!snapshotSheet) throw new Error(`시트를 찾을 수 없습니다: ${SNAPSHOT_SHEET}`);
  if (!flowSheet) throw new Error(`시트를 찾을 수 없습니다: ${FLOW_ENGINE_SHEET}`);

  const representatives = Array.isArray(payload.representatives) ? payload.representatives : [];
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!representatives.length) throw new Error('대표테마 데이터가 없습니다.');
  if (!rows.length) throw new Error('테마 집계 데이터가 없습니다.');
  if (representatives.length > 100) throw new Error(`대표테마 100행 초과: ${representatives.length}`);
  if (rows.length > 100) throw new Error(`테마 집계 100행 초과: ${rows.length}`);

  const captured = parseCapturedAt_(payload.captured_at);
  if (!captured) throw new Error(`captured_at 파싱 실패: ${payload.captured_at || ''}`);

  const representativeByCode = {};
  representatives.forEach(r => {
    const code = String(r.stock_code || '').trim();
    if (!code) return;
    representativeByCode[code] = {
      themeName: String(r.theme_name || ''),
      themeCode: String(r.theme_code || '')
    };
  });

  const maxThemeRows = Math.max(100, themeSheet.getMaxRows() - 1);
  const themeCodes = themeSheet.getRange(2, 1, maxThemeRows, 1).getValues();
  const repValues = themeCodes.map(r => {
    const code = String(r[0] || '').trim();
    const rep = representativeByCode[code];
    return rep ? [rep.themeName, rep.themeCode] : ['', ''];
  });

  const aggHeaders = [
    '기준시각', '테마 순위', '테마', '총 거래대금(억원)', 'TOP100 점유율',
    'TOP20 종목수', 'TOP100 종목수', '평균 등락률', '순위 지속성',
    '수급 점수', '가격 강도', '최종 점수', '상태'
  ];
  const aggValues = rows.map(r => [
    String(payload.captured_at || ''),
    numOrBlank_(r.theme_rank),
    String(r.theme_name || ''),
    numOrBlank_(r.trading_value_eok),
    numOrBlank_(r.share_top100_pct),
    numOrBlank_(r.top20_count),
    numOrBlank_(r.stock_count),
    numOrBlank_(r.avg_change_rate_pct),
    '', '', '', '', 'CURRENT'
  ]);

  const history = loadSnapshotHistory_(snapshotSheet);
  const currentMetrics = buildCurrentMetrics_(rows, captured, history);
  const newSnapshotValues = currentMetrics
    .filter(m => !history.existingKeys[m.key])
    .map(m => [
      captured.date,
      captured.time,
      m.theme,
      m.currentRank,
      m.previousRank,
      m.rankChange,
      m.amount,
      m.currentShare,
      m.share5,
      m.delta5,
      m.share15,
      m.delta15,
      m.share30,
      m.delta30,
      m.share60,
      m.delta60,
      m.top20Count,
      m.stockCount,
      m.avgChange,
      '', '', ''
    ]);

  const top1Share = rows.length ? numOrBlank_(rows[0].share_top100_pct) : '';
  const flowValues = currentMetrics.map(m => [
    captured.date,
    captured.time,
    m.theme,
    m.currentRank,
    m.previousRank,
    m.currentShare,
    m.delta5,
    m.delta15,
    m.delta30,
    m.delta60,
    speedOrBlank_(m.delta5, 5),
    speedOrBlank_(m.delta15, 15),
    speedOrBlank_(m.delta30, 30),
    m.top20Count,
    m.stockCount,
    m.avgChange,
    '', '', '',
    '',
    m.currentRank === 1 ? 'Y' : 'N',
    top1Share
  ]);

  const flowHeaders = [
    '기준일', '기준시각', '테마', '현재순위', '직전순위', '현재점유율',
    'Δ5분', 'Δ15분', 'Δ30분', 'Δ60분', '유입속도5', '유입속도15',
    '유입속도30', 'TOP20종목수', 'TOP100종목수', '평균등락률',
    '외국인순매수합', '기관순매수합', '프로그램순매수합', '주도지속시간(분)',
    '1위여부', '1위점유율'
  ];

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    themeSheet.getRange(2, 5, maxThemeRows, 2).clearContent();
    themeSheet.getRange(2, 5, repValues.length, 2).setValues(repValues);
    themeSheet.getRange(2, 6, repValues.length, 1).setNumberFormat('@');

    const maxAggRows = Math.max(100, aggSheet.getMaxRows() - 1);
    aggSheet.getRange(1, 1, 1, 13).setValues([aggHeaders]);
    aggSheet.getRange(2, 1, maxAggRows, 13).clearContent();
    aggSheet.getRange(2, 1, aggValues.length, 13).setValues(aggValues);
    aggSheet.getRange(2, 4, aggValues.length, 1).setNumberFormat('#,##0.00');
    aggSheet.getRange(2, 5, aggValues.length, 1).setNumberFormat('0.00"%"');
    aggSheet.getRange(2, 8, aggValues.length, 1).setNumberFormat('0.00"%"');

    if (newSnapshotValues.length) {
      const startRow = Math.max(2, snapshotSheet.getLastRow() + 1);
      ensureRows_(snapshotSheet, startRow + newSnapshotValues.length - 1);
      snapshotSheet.getRange(startRow, 1, newSnapshotValues.length, 22).setValues(newSnapshotValues);
      snapshotSheet.getRange(startRow, 7, newSnapshotValues.length, 1).setNumberFormat('#,##0.00');
      snapshotSheet.getRange(startRow, 8, newSnapshotValues.length, 9).setNumberFormat('0.00"%"');
      snapshotSheet.getRange(startRow, 19, newSnapshotValues.length, 1).setNumberFormat('0.00"%"');
    }

    flowSheet.getRange(1, 1, 1, 22).setValues([flowHeaders]);
    const maxFlowRows = Math.max(100, flowSheet.getMaxRows() - 1);
    flowSheet.getRange(2, 1, maxFlowRows, 22).clearContent();
    flowSheet.getRange(2, 1, flowValues.length, 22).setValues(flowValues);
    flowSheet.getRange(2, 6, flowValues.length, 8).setNumberFormat('0.000"%"');
    flowSheet.getRange(2, 16, flowValues.length, 1).setNumberFormat('0.00"%"');
    flowSheet.getRange(2, 22, flowValues.length, 1).setNumberFormat('0.00"%"');

    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    sheet: THEME_AGG_SHEET,
    received: rows.length,
    representatives: representatives.length,
    snapshot_appended: newSnapshotValues.length,
    flow_rows: flowValues.length,
    captured_at: payload.captured_at || '',
    source: payload.source || '',
    selection_rule: payload.selection_rule || ''
  };
}

function loadSnapshotHistory_(sheet) {
  const lastRow = sheet.getLastRow();
  const byTheme = {};
  const existingKeys = {};

  if (lastRow < 2) {
    return { byTheme, existingKeys };
  }

  const values = sheet.getRange(2, 1, lastRow - 1, 16).getValues();
  values.forEach(r => {
    const date = String(r[0] || '').trim();
    const time = String(r[1] || '').trim();
    const theme = String(r[2] || '').trim();
    if (!date || !time || !theme) return;

    const parsed = parseCapturedAt_(`${date} ${time}`);
    if (!parsed) return;

    const point = {
      ms: parsed.ms,
      rank: numberOrNull_(r[3]),
      share: numberOrNull_(r[7])
    };

    if (!byTheme[theme]) byTheme[theme] = [];
    byTheme[theme].push(point);
    existingKeys[`${date}|${time}|${theme}`] = true;
  });

  Object.keys(byTheme).forEach(theme => {
    byTheme[theme].sort((a, b) => a.ms - b.ms);
  });

  return { byTheme, existingKeys };
}

function buildCurrentMetrics_(rows, captured, history) {
  return rows.map(r => {
    const theme = String(r.theme_name || '');
    const points = history.byTheme[theme] || [];
    const previous = findLatestPrior_(points, captured.ms);
    const p5 = findPriorNear_(points, captured.ms, 5);
    const p15 = findPriorNear_(points, captured.ms, 15);
    const p30 = findPriorNear_(points, captured.ms, 30);
    const p60 = findPriorNear_(points, captured.ms, 60);

    const currentRank = numOrBlank_(r.theme_rank);
    const currentShare = numOrBlank_(r.share_top100_pct);
    const previousRank = previous && previous.rank !== null ? previous.rank : '';

    return {
      key: `${captured.date}|${captured.time}|${theme}`,
      theme,
      currentRank,
      previousRank,
      rankChange: previousRank === '' || currentRank === '' ? '' : previousRank - currentRank,
      amount: numOrBlank_(r.trading_value_eok),
      currentShare,
      share5: pointShare_(p5),
      delta5: deltaOrBlank_(currentShare, pointShare_(p5)),
      share15: pointShare_(p15),
      delta15: deltaOrBlank_(currentShare, pointShare_(p15)),
      share30: pointShare_(p30),
      delta30: deltaOrBlank_(currentShare, pointShare_(p30)),
      share60: pointShare_(p60),
      delta60: deltaOrBlank_(currentShare, pointShare_(p60)),
      top20Count: numOrBlank_(r.top20_count),
      stockCount: numOrBlank_(r.stock_count),
      avgChange: numOrBlank_(r.avg_change_rate_pct)
    };
  });
}

function findLatestPrior_(points, currentMs) {
  let found = null;
  for (let i = 0; i < points.length; i++) {
    if (points[i].ms >= currentMs) break;
    found = points[i];
  }
  return found;
}

function findPriorNear_(points, currentMs, targetMinutes) {
  let best = null;
  let bestGap = Infinity;

  points.forEach(p => {
    if (p.ms >= currentMs) return;
    const diffMinutes = (currentMs - p.ms) / 60000;
    const gap = Math.abs(diffMinutes - targetMinutes);
    if (gap <= DELTA_TOLERANCE_MINUTES && gap < bestGap) {
      best = p;
      bestGap = gap;
    }
  });

  return best;
}

function pointShare_(point) {
  return point && point.share !== null ? point.share : '';
}

function deltaOrBlank_(current, previous) {
  if (current === '' || previous === '') return '';
  return Number(current) - Number(previous);
}

function speedOrBlank_(delta, minutes) {
  if (delta === '' || delta === null || delta === undefined) return '';
  const n = Number(delta);
  return Number.isFinite(n) ? n / minutes : '';
}

function parseCapturedAt_(text) {
  const s = String(text || '').trim();
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;

  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const hour = Number(m[4]);
  const minute = Number(m[5]);
  const second = Number(m[6] || 0);

  return {
    date: `${m[1]}-${m[2]}-${m[3]}`,
    time: `${m[4]}:${m[5]}:${String(second).padStart(2, '0')}`,
    ms: Date.UTC(year, month - 1, day, hour, minute, second)
  };
}

function numberOrNull_(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function ensureRows_(sheet, requiredLastRow) {
  if (requiredLastRow <= sheet.getMaxRows()) return;
  sheet.insertRowsAfter(sheet.getMaxRows(), requiredLastRow - sheet.getMaxRows());
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
    themeAggregateSheetExists: !!ss.getSheetByName(THEME_AGG_SHEET),
    snapshotSheetExists: !!ss.getSheetByName(SNAPSHOT_SHEET),
    flowEngineSheetExists: !!ss.getSheetByName(FLOW_ENGINE_SHEET),
    ingestSecretExists: secretExists
  });
}
