const STAGE35_SYNC_SPREADSHEET_ID = '1POt3TiYivKugAWAeBsOLUAaPLiVGyPy2DrWNgkOvweU';
const STAGE3_SYNC_SPREADSHEET_ID = '1hOvskpiYgrYlAPiWKoYOzD7-BIvGGKzDptpRRGoloFA';
const STAGE35_SYNC_FLOW_SHEET = '흐름_판정_엔진';
const STAGE35_SYNC_CALENDAR_SHEET = '주도테마_캘린더';
const STAGE3_SYNC_OUTPUT_SHEET = '3.5연동';

/**
 * F단계 후처리.
 * 3단계의 고정 출력탭 B3(오늘 방향)를 그대로 읽어 3.5 AH:AJ에 기록한다.
 * 3.5는 시장 방향을 재판단하지 않고 현재 시장 자금 상태와의 일치/충돌만 표시한다.
 */
function syncStage3DirectionToFlow() {
  const stage3 = SpreadsheetApp.openById(STAGE3_SYNC_SPREADSHEET_ID);
  const link = stage3.getSheetByName(STAGE3_SYNC_OUTPUT_SHEET);
  if (!link) throw new Error('3단계 3.5연동 시트가 없습니다.');

  const direction = String(link.getRange('B3').getDisplayValue() || '').trim();
  if (!direction) return { ok: false, reason: 'stage3_direction_empty' };

  const ss = SpreadsheetApp.openById(STAGE35_SYNC_SPREADSHEET_ID);
  const flow = ss.getSheetByName(STAGE35_SYNC_FLOW_SHEET);
  if (!flow) throw new Error('3.5 흐름_판정_엔진 시트가 없습니다.');

  const moneyState = String(flow.getRange('AF2').getDisplayValue() || '').trim();
  const alignment = stage35Alignment_(direction, moneyState);
  const reason = `F-v0.1 | 3단계=${direction} / 3.5=${moneyState || '-'} / 3.5는 방향 재판단 없이 정합만 표시`;

  flow.getRange('AH2:AJ2').setValues([[direction, alignment, reason]]);
  SpreadsheetApp.flush();

  return {
    ok: true,
    direction,
    money_state: moneyState,
    alignment
  };
}

function stage35Alignment_(direction, moneyState) {
  if (!direction) return '원본대기';
  if (!moneyState) return '3.5대기';

  const up = moneyState === '집중 상승' || moneyState === '순환 상승';
  const down = moneyState === '집중 하락';

  if ((direction === '상방' && up) || (direction === '하방' && down)) {
    return '일치';
  }
  if ((direction === '상방' && down) || (direction === '하방' && up)) {
    return '충돌/주의';
  }
  return '중립/혼조';
}

/**
 * B단계 후처리.
 * 기존 장마감 캘린더 행이 만들어진 뒤 오늘 1위 테마의 생애주기(X열)를
 * 같은 날짜 캘린더 K열에 동기화한다.
 * 기존 TOP100/Δ/fail-soft/Calendar.gs는 수정하지 않는다.
 */
function syncCloseLifecycleToCalendar() {
  const ss = SpreadsheetApp.openById(STAGE35_SYNC_SPREADSHEET_ID);
  const flow = ss.getSheetByName(STAGE35_SYNC_FLOW_SHEET);
  const calendar = ss.getSheetByName(STAGE35_SYNC_CALENDAR_SHEET);
  if (!flow || !calendar) throw new Error('3.5 lifecycle sync sheet missing');

  const flowLast = flow.getLastRow();
  if (flowLast < 2) return { ok: false, reason: 'flow_empty' };

  // A:Y = 25 columns. X=생애주기, U=1위여부.
  const flowValues = flow.getRange(2, 1, flowLast - 1, 25).getDisplayValues();
  let target = null;
  for (const r of flowValues) {
    const date = normalizeStage35SyncDate_(r[0]);
    const isTop1 = String(r[20] || '').trim() === 'Y';
    const lifecycle = String(r[23] || '').trim();
    const theme = String(r[2] || '').trim();
    if (!date || !isTop1 || !lifecycle || !theme) continue;
    target = { date, theme, lifecycle };
    break;
  }
  if (!target) return { ok: false, reason: 'top1_lifecycle_missing' };

  const calLast = calendar.getLastRow();
  if (calLast < 6) return { ok: false, reason: 'calendar_empty' };
  const calValues = calendar.getRange(6, 1, calLast - 5, 17).getDisplayValues();

  let targetRow = 0;
  for (let i = 0; i < calValues.length; i++) {
    const date = normalizeStage35SyncDate_(calValues[i][0]);
    if (date === target.date) {
      targetRow = i + 6;
      break;
    }
  }
  if (!targetRow) return { ok: false, reason: 'calendar_date_missing', date: target.date };

  const calendarTheme = String(calendar.getRange(targetRow, 3).getDisplayValue() || '').trim();
  if (calendarTheme && calendarTheme !== target.theme) {
    return {
      ok: false,
      reason: 'top1_theme_mismatch',
      date: target.date,
      flow_theme: target.theme,
      calendar_theme: calendarTheme
    };
  }

  calendar.getRange(targetRow, 11).setValue(target.lifecycle); // K 마감 생애주기
  SpreadsheetApp.flush();

  return {
    ok: true,
    date: target.date,
    theme: target.theme,
    lifecycle: target.lifecycle,
    row: targetRow
  };
}

/**
 * 장중 5분 후처리: 3단계 방향만 동기화한다.
 * 원본 수집기와 독립이며 장외시간/주말에는 아무것도 하지 않는다.
 */
function syncStage3DirectionScheduled() {
  const now = new Date();
  const dow = Number(Utilities.formatDate(now, 'Asia/Seoul', 'u'));
  if (dow >= 6) return;

  const hhmm = Number(Utilities.formatDate(now, 'Asia/Seoul', 'HHmm'));
  if (hhmm < 850 || hhmm > 1610) return;

  syncStage3DirectionToFlow();
}

/**
 * 최종 1회 설치 함수.
 * - 5분마다: 3단계 오늘 방향 → 3.5 정합판정
 * - 16:05 전후: 장마감 캘린더 K 생애주기 후처리
 *
 * Apps Script 시간 트리거는 정확한 분 단위가 아니라 근사 실행이다.
 */
function installStage35SyncTriggersOnce() {
  const handlers = new Set([
    'syncStage3DirectionScheduled',
    'syncCloseLifecycleToCalendar'
  ]);

  ScriptApp.getProjectTriggers().forEach(t => {
    if (handlers.has(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('syncStage3DirectionScheduled')
    .timeBased()
    .everyMinutes(5)
    .create();

  ScriptApp.newTrigger('syncCloseLifecycleToCalendar')
    .timeBased()
    .atHour(16)
    .nearMinute(5)
    .everyDays(1)
    .create();

  Logger.log('3.5 F방향동기화 + B장마감생애주기 트리거 설치 완료');
}

function normalizeStage35SyncDate_(value) {
  if (!value) return '';
  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }
  const s = String(value).trim();
  const m = s.match(/^(\d{4})[-\/.]?(\d{2})[-\/.]?(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : '';
}
