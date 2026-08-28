const STAGE4_RSYNC_STAGE2_ID = '1KR5RGAplOjsSnUYnb8Ati0w4LEQJkYMBNkFwCiwJ2js';
const STAGE4_RSYNC_STAGE4_ID = '1zhv_PEVqTG2SfYqNe-kdNEK6WpOrpe8qxOFL3Gez6mE';
const STAGE4_RSYNC_SOURCE_SHEET = '4단계연동';

/**
 * 2단계가 확정한 Base R을 4단계 당일 빠른계산 B7에 그대로 기록한다.
 * - 3단계 등급을 R로 재해석하지 않는다.
 * - 3.5/4단계가 R을 상향하지 않는다.
 * - 소스 날짜/상태/R이 이상하면 B7을 비워 Fail Closed한다.
 * - 과거 일일탭은 절대 수정하지 않고 '오늘 탭'만 갱신한다.
 */
function syncStage2BaseRToStage4() {
  const tz = 'Asia/Seoul';
  const now = new Date();
  const todayYmd = Utilities.formatDate(now, tz, 'yyyy-MM-dd');

  const stage2 = SpreadsheetApp.openById(STAGE4_RSYNC_STAGE2_ID);
  const source = stage2.getSheetByName(STAGE4_RSYNC_SOURCE_SHEET);
  if (!source) throw new Error('2단계 4단계연동 시트를 찾을 수 없습니다.');

  const sourceDateValue = source.getRange('B2').getValue();
  const baseRValue = source.getRange('B3').getValue();
  const dateStatus = String(source.getRange('C2').getDisplayValue() || '').trim();
  const rStatus = String(source.getRange('C3').getDisplayValue() || '').trim();
  const ready = String(source.getRange('B5').getDisplayValue() || '').trim();

  const sourceYmd = normalizeStage4SyncDate_(sourceDateValue, tz);
  const canonicalR = canonicalStage4R_(baseRValue);

  const stage4 = SpreadsheetApp.openById(STAGE4_RSYNC_STAGE4_ID);
  const targetName = `${todayYmd}_빠른계산`;
  const target = stage4.getSheetByName(targetName);

  if (!target) {
    return { ok: false, reason: 'today_sheet_missing', sheet: targetName };
  }

  const valid =
    sourceYmd === todayYmd &&
    dateStatus === '정상' &&
    rStatus === '정상' &&
    ready === 'READY' &&
    canonicalR !== '';

  const b7 = target.getRange('B7');

  if (!valid) {
    b7.clearContent();
    b7.setNote(
      `FAIL CLOSED | 2단계 Base R 동기화 실패 | sourceDate=${sourceYmd || '-'} | dateStatus=${dateStatus || '-'} | rStatus=${rStatus || '-'} | ready=${ready || '-'} | ${Utilities.formatDate(now, tz, 'yyyy-MM-dd HH:mm:ss')}`
    );
    SpreadsheetApp.flush();

    return {
      ok: false,
      reason: 'source_not_ready',
      source_date: sourceYmd,
      date_status: dateStatus,
      r_status: rStatus,
      ready,
      base_r: canonicalR
    };
  }

  b7.setValue(canonicalR);
  b7.setNote(
    `AUTO | Source of Truth=2단계 | Base R=${canonicalR} | ${Utilities.formatDate(now, tz, 'yyyy-MM-dd HH:mm:ss')}`
  );

  // 최신 정책: Exception Multiplier 폐기, Final R = Base R.
  target.getRange('M14').setValue('BASE_R_ONLY');
  target.getRange('Q14').setValue(1);
  target.getRange('U14').setValue(canonicalR);

  SpreadsheetApp.flush();

  return {
    ok: true,
    date: todayYmd,
    sheet: targetName,
    base_r: canonicalR,
    final_r: canonicalR
  };
}

/**
 * 장중/장전 자동 동기화.
 * 2단계가 아직 오늘값을 만들지 않았으면 B7을 비워 신규 위험생성을 막는다.
 */
function syncStage2BaseRScheduled() {
  const now = new Date();
  const tz = 'Asia/Seoul';
  const weekday = Utilities.formatDate(now, tz, 'EEE');

  if (
    weekday === '토' || weekday === '일' ||
    weekday === 'Sat' || weekday === 'Sun'
  ) {
    return;
  }

  const hhmm = Number(Utilities.formatDate(now, tz, 'HHmm'));
  if (hhmm < 400 || hhmm > 1610) return;

  syncStage2BaseRToStage4();
}

/**
 * 최초 1회만 실행.
 * 기존 동일 핸들러 트리거가 있으면 제거하고 5분 트리거 1개만 재설치한다.
 */
function installStage4BaseRSyncTriggerOnce() {
  const handler = 'syncStage2BaseRScheduled';

  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === handler) {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger(handler)
    .timeBased()
    .everyMinutes(5)
    .create();

  Logger.log('4단계 2단계 Base R 직접동기화 5분 트리거 설치 완료');
}

function canonicalStage4R_(value) {
  if (value === null || value === undefined || value === '') return '';

  let n;
  if (typeof value === 'number') {
    n = value;
  } else {
    const text = String(value).trim().toUpperCase().replace('R', '');
    n = Number(text);
  }

  if (!Number.isFinite(n)) return '';

  if (Math.abs(n - 1) < 1e-9) return '1R';
  if (Math.abs(n - 0.75) < 1e-9) return '0.75R';
  if (Math.abs(n - 0.5) < 1e-9) return '0.50R';
  if (Math.abs(n - 0.25) < 1e-9) return '0.25R';
  if (Math.abs(n - 0.1) < 1e-9) return '0.10R';

  return '';
}

function normalizeStage4SyncDate_(value, tz) {
  if (!value) return '';

  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(value, tz, 'yyyy-MM-dd');
  }

  const s = String(value).trim();
  const m = s.match(/^(\d{4})[-\/.]\s?(\d{1,2})[-\/.]\s?(\d{1,2})/);
  if (!m) return '';

  return `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}`;
}
