const LIFECYCLE_SYNC_SPREADSHEET_ID = '1POt3TiYivKugAWAeBsOLUAaPLiVGyPy2DrWNgkOvweU';
const LIFECYCLE_SYNC_FLOW_SHEET = '흐름_판정_엔진';
const LIFECYCLE_SYNC_CALENDAR_SHEET = '주도테마_캘린더';

/**
 * B단계 후처리: 장마감 캘린더가 만들어진 뒤 오늘 1위 테마의
 * 생애주기(X열)를 같은 날짜 캘린더 K열에 동기화한다.
 * 기존 TOP100/Δ/fail-soft/Calendar.gs는 수정하지 않는다.
 */
function syncCloseLifecycleToCalendar() {
  const ss = SpreadsheetApp.openById(LIFECYCLE_SYNC_SPREADSHEET_ID);
  const flow = ss.getSheetByName(LIFECYCLE_SYNC_FLOW_SHEET);
  const calendar = ss.getSheetByName(LIFECYCLE_SYNC_CALENDAR_SHEET);
  if (!flow || !calendar) throw new Error('3.5 lifecycle sync sheet missing');

  const flowLast = flow.getLastRow();
  if (flowLast < 2) return { ok: false, reason: 'flow_empty' };

  // A:Y = 25 columns. X=생애주기, U=1위여부.
  const flowValues = flow.getRange(2, 1, flowLast - 1, 25).getDisplayValues();
  let target = null;
  for (const r of flowValues) {
    const date = normalizeLifecycleSyncDate_(r[0]);
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
    const date = normalizeLifecycleSyncDate_(calValues[i][0]);
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
 * 기존 장마감 캘린더 트리거(15:40 전후)가 끝난 뒤 실행되도록 16:05 전후로 설치.
 * Apps Script time trigger는 정확한 분 단위가 아니라 근사 실행이다.
 */
function installCloseLifecycleSyncTriggerOnce() {
  const handler = 'syncCloseLifecycleToCalendar';
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === handler) ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger(handler)
    .timeBased()
    .atHour(16)
    .nearMinute(5)
    .everyDays(1)
    .create();

  Logger.log('3.5 장마감 생애주기 동기화 트리거 설치 완료');
}

function normalizeLifecycleSyncDate_(value) {
  if (!value) return '';
  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }
  const s = String(value).trim();
  const m = s.match(/^(\d{4})[-\/.]?(\d{2})[-\/.]?(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : '';
}
