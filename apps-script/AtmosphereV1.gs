// ATMOSPHERE V1 | expected reaction vs actual opening reaction
// This module is intentionally isolated from 재수차/Base R/ENTRY authority.
// It only writes the dedicated ATMOSPHERE workbook and returns an advisory result.

const ATM_SPREADSHEET_ID = '1UAArSBsQj4C3VU_trfC8aH_NRpRwI62URW-dJqJrcGI';
const ATM_DAILY_SHEET = '04_일일기록';
const ATM_BACKFILL_SHEET = '03_검증로그';
const ATM_MODEL_SHEET = '06_모델파라미터';
const ATM_RAW_SHEET = '07_API_RAW';

const ATM_STAGE2_SPREADSHEET_ID = '1KR5RGAplOjsSnUYnb8Ati0w4LEQJkYMBNkFwCiwJ2js';
const ATM_NEWS_SHEET = '1C_장전뉴스';

function atmNum_(value) {
  if (value === null || value === undefined || value === '') return '';
  const n = Number(String(value).replace(/,/g, '').trim());
  return Number.isFinite(n) ? n : '';
}

function atmDateText_(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }
  const text = String(value || '').trim();
  const m = text.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);
  if (!m) return '';
  return m[1] + '-' + String(Number(m[2])).padStart(2, '0') + '-' + String(Number(m[3])).padStart(2, '0');
}

function atmParseKst_(value) {
  if (value instanceof Date) return value;
  const text = String(value || '').trim();
  if (!text) return null;
  const m = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  const iso = m[1] + '-' + String(Number(m[2])).padStart(2, '0') + '-' +
    String(Number(m[3])).padStart(2, '0') + 'T' +
    String(Number(m[4])).padStart(2, '0') + ':' + m[5] + ':' + (m[6] || '00') + '+09:00';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

function atmFindOrAppendDateRow_(sheet, dateText) {
  const lastRow = Math.max(1, sheet.getLastRow());
  if (lastRow >= 2) {
    const values = sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues();
    for (let i = 0; i < values.length; i++) {
      if (atmDateText_(values[i][0]) === dateText) return i + 2;
    }
  }
  return Math.max(2, lastRow + 1);
}

function atmReadNews_(usCloseKst, lockKst) {
  const ss = SpreadsheetApp.openById(ATM_STAGE2_SPREADSHEET_ID);
  const sheet = ss.getSheetByName(ATM_NEWS_SHEET);
  if (!sheet) throw new Error('ATM news sheet missing: ' + ATM_NEWS_SHEET);

  const summaryRaw = atmNum_(sheet.getRange('B3').getValue());
  const out = {
    total_raw: summaryRaw === '' ? 0 : summaryRaw,
    post_score: 0,
    post_count: 0,
    post_titles: []
  };

  const start = atmParseKst_(usCloseKst);
  const end = atmParseKst_(lockKst);
  if (!start || !end) return out;

  const lastRow = sheet.getLastRow();
  if (lastRow < 6) return out;
  const rows = sheet.getRange(6, 1, lastRow - 5, 4).getValues();
  const display = sheet.getRange(6, 1, lastRow - 5, 4).getDisplayValues();

  for (let i = 0; i < rows.length; i++) {
    const title = String(display[i][0] || '').trim();
    const score = Number(String(display[i][1] || '').replace(/,/g, '').trim());
    const status = String(display[i][2] || '').trim();
    const ts = atmParseKst_(rows[i][3] || display[i][3]);
    if (!title || status !== '반영' || !Number.isFinite(score) || !ts) continue;
    if (ts.getTime() < start.getTime() || ts.getTime() > end.getTime()) continue;
    out.post_score += score;
    out.post_count += 1;
    if (out.post_titles.length < 8) out.post_titles.push(title);
  }
  return out;
}

function atmAppendRaw_(sheet, payload, news, resultStatus) {
  sheet.appendRow([
    String(payload.captured_at || ''),
    String(payload.mode || ''),
    String(payload.trade_date || ''),
    String(payload.us_close_kst || ''),
    String(payload.lock_time_kst || ''),
    atmNum_(payload.nq_us_pct),
    atmNum_(payload.skhy_adr_pct),
    atmNum_(payload.adr_rel_pct),
    atmNum_(payload.nq_post_pct),
    atmNum_(news && news.total_raw),
    atmNum_(news && news.post_score),
    atmNum_(payload.expected_center_pct),
    atmNum_(payload.actual_gap_pct),
    String(resultStatus || payload.status || ''),
    String(payload.model_version || ''),
    String(payload.note || '')
  ]);
}

function atmWriteModel_(ss, payload) {
  const sheet = ss.getSheetByName(ATM_MODEL_SHEET);
  if (!sheet) throw new Error('ATM model sheet missing');
  sheet.getRange(2, 1, 1, 12).setValues([[
    String(payload.model_version || 'ATM_PRICE_V1'),
    String(payload.model_status || 'ACTIVE'),
    String(payload.trained_through || ''),
    atmNum_(payload.train_n),
    atmNum_(payload.validation_n),
    atmNum_(payload.alpha),
    atmNum_(payload.beta_nq_us),
    atmNum_(payload.beta_adr_rel),
    atmNum_(payload.beta_nq_post),
    atmNum_(payload.beta_news_post || 0),
    atmNum_(payload.band80_pctp),
    atmNum_(payload.mae_pctp)
  ]]);
  return { mode: 'model', model_version: payload.model_version || 'ATM_PRICE_V1' };
}

function atmWriteBackfill_(ss, payload) {
  const sheet = ss.getSheetByName(ATM_BACKFILL_SHEET);
  if (!sheet) throw new Error('ATM backfill sheet missing');
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('No atmosphere backfill rows');

  const maxRows = Math.max(1, sheet.getMaxRows() - 1);
  sheet.getRange(2, 1, maxRows, 16).clearContent();

  const values = rows.map(r => [
    String(r.date || ''),
    atmNum_(r.nq_us_pct),
    atmNum_(r.skhy_adr_pct),
    atmNum_(r.adr_rel_pct),
    atmNum_(r.nq_post_pct),
    atmNum_(r.expected_center_pct),
    atmNum_(r.expected_low_pct),
    atmNum_(r.expected_high_pct),
    atmNum_(r.actual_gap_pct),
    atmNum_(r.residual_pctp),
    String(r.atmosphere || ''),
    String(r.model_version || payload.model_version || ''),
    String(r.split || ''),
    String(r.data_status || 'OK'),
    String(r.note || ''),
    String(r.source || payload.source || '')
  ]);
  sheet.getRange(2, 1, values.length, 16).setValues(values);
  return { mode: 'backfill', received: values.length };
}

function atmWriteLive_(ss, payload) {
  const daily = ss.getSheetByName(ATM_DAILY_SHEET);
  if (!daily) throw new Error('ATM daily sheet missing');

  const dateText = String(payload.trade_date || '').slice(0, 10);
  if (!dateText) throw new Error('trade_date is required');
  const row = atmFindOrAppendDateRow_(daily, dateText);

  const news = atmReadNews_(payload.us_close_kst, payload.lock_time_kst);
  const mode = String(payload.mode || '');

  if (mode === 'preopen') {
    daily.getRange(row, 1, 1, 13).setValues([[
      dateText,
      news.total_raw,
      news.post_score,
      atmNum_(payload.skhy_adr_pct),
      atmNum_(payload.nq_us_pct),
      atmNum_(payload.nq_post_pct),
      atmNum_(payload.adr_rel_pct),
      atmNum_(payload.expected_center_pct),
      atmNum_(payload.expected_low_pct),
      atmNum_(payload.expected_high_pct),
      String(payload.model_version || ''),
      atmNum_(payload.prev_close),
      ''
    ]]);
    daily.getRange(row, 23).setValue('미완료');
    daily.getRange(row, 24).setValue(
      'AUTO 08:50 | post_news=' + news.post_count +
      (news.post_titles.length ? ' | ' + news.post_titles.join(' / ') : '')
    );
  } else if (mode === 'open') {
    daily.getRange(row, 12, 1, 3).setValues([[
      atmNum_(payload.prev_close),
      atmNum_(payload.open_price),
      atmNum_(payload.actual_gap_pct)
    ]]);
  } else if (mode === 'checkpoint_0930') {
    daily.getRange(row, 18).setValue(atmNum_(payload.pct_0930));
  } else if (mode === 'close') {
    daily.getRange(row, 19).setValue(atmNum_(payload.close_pct));
    daily.getRange(row, 23).setValue('복기대기');
  } else {
    throw new Error('Unsupported atmosphere live mode: ' + mode);
  }

  SpreadsheetApp.flush();
  const display = daily.getRange(row, 1, 1, 24).getDisplayValues()[0];
  return {
    mode: mode,
    row: row,
    trade_date: dateText,
    news_total_raw: news.total_raw,
    news_post_score: news.post_score,
    news_post_count: news.post_count,
    atmosphere: display[16] || 'MODEL_PENDING',
    actual_gap_pct: display[13] || '',
    expected_center_pct: display[7] || ''
  };
}

function writeAtmosphere_(payload) {
  const ss = SpreadsheetApp.openById(ATM_SPREADSHEET_ID);
  const raw = ss.getSheetByName(ATM_RAW_SHEET);
  if (!raw) throw new Error('ATM raw sheet missing');

  const mode = String(payload.mode || '');
  let result;
  let news = { total_raw: '', post_score: '' };

  if (mode === 'model') {
    result = atmWriteModel_(ss, payload);
  } else if (mode === 'backfill') {
    result = atmWriteBackfill_(ss, payload);
  } else {
    news = atmReadNews_(payload.us_close_kst, payload.lock_time_kst);
    result = atmWriteLive_(ss, payload);
  }

  atmAppendRaw_(raw, payload, news, 'OK');
  return { sheet: ATM_SPREADSHEET_ID, ...result };
}
