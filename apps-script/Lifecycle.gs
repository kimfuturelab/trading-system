const LIFECYCLE_FLOW_SHEET = '흐름_판정_엔진';
const LIFECYCLE_SNAPSHOT_SHEET = '자금이동_스냅샷';
const LIFECYCLE_CALENDAR_SHEET = '주도테마_캘린더';

const LIFECYCLE_FLOW_STATE_COL = 24; // X
const LIFECYCLE_FLOW_REASON_COL = 25; // Y
const LIFECYCLE_CALENDAR_STATE_COL = 11; // K

const LIFECYCLE_RULE_VERSION = 'B-v0.1-20260828';

/**
 * 3.5 B단계 주도 생애주기 엔진.
 *
 * 철학
 * - 발견 → 신규유입 → 가속 → 주도 → 지배 → 둔화 → 이탈
 * - 현재 강도와 돈의 방향을 분리한다.
 * - 임계값은 8월 데이터로 검산할 초기 운영 가설이며 I단계 백테스트 전까지 확정규칙이 아니다.
 * - 기존 A:W 실시간 엔진, Δ 계산, 장마감 자동기록 코드는 건드리지 않는다.
 * - X:Y만 후처리하고, 캘린더 K열은 장마감 행 생성 뒤 후처리한다.
 */
function refreshLifecycleNow() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const flowSheet = ss.getSheetByName(LIFECYCLE_FLOW_SHEET);
  const snapshotSheet = ss.getSheetByName(LIFECYCLE_SNAPSHOT_SHEET);
  const calendarSheet = ss.getSheetByName(LIFECYCLE_CALENDAR_SHEET);

  if (!flowSheet) throw new Error(`시트를 찾을 수 없습니다: ${LIFECYCLE_FLOW_SHEET}`);
  if (!snapshotSheet) throw new Error(`시트를 찾을 수 없습니다: ${LIFECYCLE_SNAPSHOT_SHEET}`);
  if (!calendarSheet) throw new Error(`시트를 찾을 수 없습니다: ${LIFECYCLE_CALENDAR_SHEET}`);

  ensureLifecycleColumns_(flowSheet);

  const flowResult = refreshFlowLifecycle_(flowSheet, snapshotSheet);
  const calendarResult = refreshCalendarLifecycle_(calendarSheet, snapshotSheet);

  return {
    ok: true,
    rule_version: LIFECYCLE_RULE_VERSION,
    flow: flowResult,
    calendar: calendarResult,
    at: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss')
  };
}

/**
 * 5분 트리거용 래퍼. 장중~장마감 후처리 구간만 실제 계산한다.
 */
function refreshLifecycleScheduled() {
  const now = new Date();
  const dow = Number(Utilities.formatDate(now, 'Asia/Seoul', 'u')); // 1=Mon ... 7=Sun
  if (dow >= 6) return;

  const hhmm = Number(Utilities.formatDate(now, 'Asia/Seoul', 'HHmm'));
  if (hhmm < 850 || hhmm > 1610) return;

  refreshLifecycleNow();
}

function installLifecycleTriggerOnce() {
  const handler = 'refreshLifecycleScheduled';
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === handler) {
      ScriptApp.deleteTrigger(t);
    }
  });

  ScriptApp.newTrigger(handler)
    .timeBased()
    .everyMinutes(5)
    .create();

  Logger.log('3.5 생애주기 5분 후처리 트리거 설치 완료');
}

function refreshFlowLifecycle_(flowSheet, snapshotSheet) {
  const lastRow = flowSheet.getLastRow();
  if (lastRow < 2) {
    return { rows: 0, message: 'flow empty' };
  }

  const rowCount = lastRow - 1;
  const display = flowSheet.getRange(2, 1, rowCount, 23).getDisplayValues(); // A:W
  const raw = flowSheet.getRange(2, 1, rowCount, 23).getValues();

  const valid = [];
  for (let i = 0; i < rowCount; i++) {
    const theme = String(display[i][2] || raw[i][2] || '').trim();
    const rank = lifecycleNumber_(raw[i][3]);
    if (!theme || rank === null) continue;
    valid.push({ i, theme, rank, raw: raw[i], display: display[i] });
  }

  if (!valid.length) {
    flowSheet.getRange(2, LIFECYCLE_FLOW_STATE_COL, rowCount, 2).clearContent();
    return { rows: 0, message: 'no valid flow rows' };
  }

  valid.sort((a, b) => a.rank - b.rank);
  const top1 = valid[0];
  const top2 = valid.length > 1 ? valid[1] : null;
  const top1Share = lifecycleNumber_(top1.raw[5]) || 0;
  const top2Share = top2 ? (lifecycleNumber_(top2.raw[5]) || 0) : 0;
  const gap = top1Share - top2Share;

  const currentDate = normalizeLifecycleDate_(top1.display[0] || top1.raw[0]);
  const leaderMinutes = currentDate
    ? currentLeaderDurationMinutes_(snapshotSheet, currentDate, top1.theme)
    : 0;

  const output = Array.from({ length: rowCount }, () => ['', '']);
  let classified = 0;

  valid.forEach(item => {
    const r = item.raw;
    const previousRank = lifecycleNumber_(r[4]);
    const share = lifecycleNumber_(r[5]);
    const d5 = lifecycleNumber_(r[6]);
    const d15 = lifecycleNumber_(r[7]);
    const d30 = lifecycleNumber_(r[8]);
    const d60 = lifecycleNumber_(r[9]);
    const speed5 = lifecycleNumber_(r[10]);
    const speed15 = lifecycleNumber_(r[11]);
    const top100Count = lifecycleNumber_(r[14]) || 0;

    const state = classifyIntradayLifecycle_({
      rank: item.rank,
      previousRank,
      share,
      d5,
      d15,
      d30,
      d60,
      speed5,
      speed15,
      top100Count,
      top1Gap: gap,
      leaderMinutes: item.rank === 1 ? leaderMinutes : 0
    });

    const reason = lifecycleReason_({
      rank: item.rank,
      previousRank,
      share,
      d5,
      d15,
      d30,
      d60,
      top1Gap: gap,
      leaderMinutes: item.rank === 1 ? leaderMinutes : 0
    });

    output[item.i] = [state, reason];
    classified += 1;
  });

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    flowSheet.getRange(1, LIFECYCLE_FLOW_STATE_COL, 1, 2)
      .setValues([['생애주기', '생애주기 판정근거']]);
    flowSheet.getRange(2, LIFECYCLE_FLOW_STATE_COL, Math.max(100, rowCount), 2)
      .clearContent();
    flowSheet.getRange(2, LIFECYCLE_FLOW_STATE_COL, output.length, 2)
      .setValues(output);
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return {
    rows: classified,
    top1_theme: top1.theme,
    top1_share: top1Share,
    top2_theme: top2 ? top2.theme : '',
    gap_pp: gap,
    leader_minutes: leaderMinutes
  };
}

/**
 * 실시간 생애주기 v0.1.
 * 숫자는 확정값이 아니라 8월 검산용 초기 운영 가설이다.
 */
function classifyIntradayLifecycle_(m) {
  const rank = m.rank;
  const prev = m.previousRank;
  const share = m.share;
  const d5 = m.d5;
  const d15 = m.d15;
  const d30 = m.d30;
  const d60 = m.d60;
  const speed5 = m.speed5;
  const speed15 = m.speed15;
  const top100Count = m.top100Count || 0;
  const gap = m.top1Gap || 0;
  const leaderMinutes = m.leaderMinutes || 0;

  const rankWorsened = prev !== null && rank > prev;
  const rankImproved = prev !== null && rank < prev;
  const has15 = d15 !== null;
  const has30 = d30 !== null;

  if (!has15 && !has30) {
    return '발견';
  }

  // 1) 이탈: 중기 점유율 하락 + 순위 악화 또는 60분 하락 확인.
  if (
    d15 !== null && d30 !== null &&
    d15 <= -0.05 && d30 <= -0.10 &&
    (rankWorsened || (d60 !== null && d60 <= -0.10))
  ) {
    return '이탈';
  }

  // 2) 둔화: 상위권인데 단기 유입이 꺾이는 구간.
  if (
    rank <= 3 &&
    d5 !== null && d15 !== null &&
    d5 <= -0.10 && d15 <= 0
  ) {
    return '둔화';
  }

  // 3) 지배: 1위 + 큰 점유율/격차 + 30분 이상 연속 1위.
  // 시장 무게추 C열을 직접 쓰지 않고 원자료만 사용한다.
  if (
    rank === 1 && share !== null &&
    share >= 30 && gap >= 15 && leaderMinutes >= 30 &&
    (d15 === null || d15 > -0.50)
  ) {
    return '지배';
  }

  // 4) 가속: 5분 유입속도가 15분 평균보다 뚜렷하게 빨라짐.
  if (
    d5 !== null && d15 !== null &&
    speed5 !== null && speed15 !== null &&
    d5 >= 0.02 && d15 >= 0.02 &&
    speed15 > 0 && speed5 >= speed15 * 1.20 &&
    !rankWorsened
  ) {
    return '가속';
  }

  // 5) 주도: TOP3 + 일정 점유율 + 복수 종목 확산 + 30분 급락 아님.
  if (
    rank <= 3 && share !== null && share >= 3 &&
    top100Count >= 2 &&
    (d30 === null || d30 >= -0.50)
  ) {
    return '주도';
  }

  // 6) 신규유입: 15/30분 모두 점유율 증가.
  if (
    d15 !== null && d30 !== null &&
    d15 >= 0.02 && d30 >= 0.02
  ) {
    return '신규유입';
  }

  // 7) 발견: 순위 개선 또는 아직 작은 양(+)의 변화.
  if (
    rankImproved ||
    (d15 !== null && d15 > 0) ||
    (d30 !== null && d30 > 0)
  ) {
    return '발견';
  }

  // 명확한 하락은 보수적으로 이탈, 나머지는 발견/관찰 단계로 둔다.
  if (
    d15 !== null && d30 !== null &&
    d15 < 0 && d30 < 0
  ) {
    return '이탈';
  }

  return '발견';
}

function lifecycleReason_(m) {
  const parts = [
    `R${m.rank}`,
    `share ${lifecycleFmt_(m.share)}%`,
    `gap ${lifecycleFmt_(m.top1Gap)}%p`,
    `Δ5 ${lifecycleSigned_(m.d5)}`,
    `Δ15 ${lifecycleSigned_(m.d15)}`,
    `Δ30 ${lifecycleSigned_(m.d30)}`
  ];
  if (m.d60 !== null) parts.push(`Δ60 ${lifecycleSigned_(m.d60)}`);
  if (m.leaderMinutes) parts.push(`1위지속 ${Math.round(m.leaderMinutes)}m`);
  return `${LIFECYCLE_RULE_VERSION} | ${parts.join(' / ')}`;
}

function refreshCalendarLifecycle_(calendarSheet, snapshotSheet) {
  const lastRow = calendarSheet.getLastRow();
  if (lastRow < 6) return { rows: 0 };

  const count = lastRow - 5;
  const raw = calendarSheet.getRange(6, 1, count, 17).getValues();
  const display = calendarSheet.getRange(6, 1, count, 17).getDisplayValues();
  const snapshotClose = loadCloseSnapshotMap_(snapshotSheet);

  const states = [];
  let previous = null;
  let consecutive = 0;

  for (let i = 0; i < count; i++) {
    const date = normalizeLifecycleDate_(display[i][0] || raw[i][0]);
    const theme = String(display[i][2] || raw[i][2] || '').trim();
    const share = lifecycleNumber_(raw[i][3]);
    const gap = lifecycleNumber_(raw[i][5]);

    if (!date || !theme || share === null) {
      states.push(['']);
      previous = null;
      consecutive = 0;
      continue;
    }

    if (previous && previous.theme === theme) consecutive += 1;
    else consecutive = 1;

    const closePoint = snapshotClose[`${date}|${theme}`] || null;
    let state;

    if (closePoint && (closePoint.d15 !== null || closePoint.d30 !== null)) {
      state = classifyIntradayLifecycle_({
        rank: closePoint.rank || 1,
        previousRank: closePoint.previousRank,
        share,
        d5: closePoint.d5,
        d15: closePoint.d15,
        d30: closePoint.d30,
        d60: closePoint.d60,
        speed5: closePoint.d5 === null ? null : closePoint.d5 / 5,
        speed15: closePoint.d15 === null ? null : closePoint.d15 / 15,
        top100Count: closePoint.stockCount || 0,
        top1Gap: gap || 0,
        leaderMinutes: lifecycleNumber_(raw[i][11]) || closePoint.leaderMinutes || 0
      });
    } else {
      state = classifyDailyLifecycle_({
        theme,
        share,
        gap: gap || 0,
        previous,
        consecutive
      });
    }

    states.push([state]);
    previous = { date, theme, share, gap: gap || 0 };
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    calendarSheet.getRange(6, LIFECYCLE_CALENDAR_STATE_COL, states.length, 1)
      .setValues(states);
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  return { rows: states.length };
}

/**
 * 과거복원처럼 장중 Δ가 없는 날짜의 일별 fallback.
 * 당일 TOP1의 전일 대비 점유율 변화와 연속 1위 여부만 사용한다.
 */
function classifyDailyLifecycle_(m) {
  if (!m.previous) return '발견';
  if (m.previous.theme !== m.theme) return '신규유입';

  const delta = m.share - m.previous.share;

  if (delta <= -2.0) return '둔화';
  if (delta >= 2.0) return '가속';
  if (m.consecutive >= 2 && m.share >= 30 && m.gap >= 15) return '지배';
  if (m.consecutive >= 2) return '주도';
  if (delta > 0) return '신규유입';
  return '발견';
}

function loadCloseSnapshotMap_(snapshotSheet) {
  const lastRow = snapshotSheet.getLastRow();
  const out = {};
  if (lastRow < 2) return out;

  // 현재 운영 규모에서 충분하고 매 5분 실행 비용을 제한하기 위해 최근 3500행만 본다.
  const startRow = Math.max(2, lastRow - 3499);
  const count = lastRow - startRow + 1;
  const raw = snapshotSheet.getRange(startRow, 1, count, 19).getValues();
  const display = snapshotSheet.getRange(startRow, 1, count, 19).getDisplayValues();

  for (let i = 0; i < count; i++) {
    const date = normalizeLifecycleDate_(display[i][0] || raw[i][0]);
    const time = String(display[i][1] || '').trim();
    const theme = String(display[i][2] || raw[i][2] || '').trim();
    if (!date || !time || !theme) continue;

    const key = `${date}|${theme}`;
    const point = {
      time,
      rank: lifecycleNumber_(raw[i][3]),
      previousRank: lifecycleNumber_(raw[i][4]),
      share: lifecycleNumber_(raw[i][7]),
      d5: lifecycleNumber_(raw[i][9]),
      d15: lifecycleNumber_(raw[i][11]),
      d30: lifecycleNumber_(raw[i][13]),
      d60: lifecycleNumber_(raw[i][15]),
      stockCount: lifecycleNumber_(raw[i][17]),
      leaderMinutes: 0
    };

    if (!out[key] || time > out[key].time) {
      out[key] = point;
    }
  }

  return out;
}

function currentLeaderDurationMinutes_(snapshotSheet, date, theme) {
  const lastRow = snapshotSheet.getLastRow();
  if (lastRow < 2) return 0;

  const startRow = Math.max(2, lastRow - 2499);
  const count = lastRow - startRow + 1;
  const raw = snapshotSheet.getRange(startRow, 1, count, 4).getValues();
  const display = snapshotSheet.getRange(startRow, 1, count, 4).getDisplayValues();
  const points = [];

  for (let i = 0; i < count; i++) {
    const d = normalizeLifecycleDate_(display[i][0] || raw[i][0]);
    const t = String(display[i][1] || '').trim();
    const th = String(display[i][2] || raw[i][2] || '').trim();
    const rank = lifecycleNumber_(raw[i][3]);
    if (d !== date || th !== theme || !t || rank === null) continue;
    points.push({ minute: lifecycleTimeMinutes_(t), rank });
  }

  if (!points.length) return 0;
  points.sort((a, b) => a.minute - b.minute);

  let end = points[points.length - 1];
  if (end.rank !== 1) return 0;
  let start = end;

  for (let i = points.length - 2; i >= 0; i--) {
    const p = points[i];
    if (p.rank !== 1) break;
    if (start.minute - p.minute > 12) break;
    start = p;
  }

  return Math.max(0, end.minute - start.minute);
}

function ensureLifecycleColumns_(sheet) {
  if (sheet.getMaxColumns() < LIFECYCLE_FLOW_REASON_COL) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      LIFECYCLE_FLOW_REASON_COL - sheet.getMaxColumns()
    );
  }
}

function normalizeLifecycleDate_(value) {
  if (!value) return '';
  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(value, 'Asia/Seoul', 'yyyy-MM-dd');
  }
  const s = String(value).trim();
  const m = s.match(/^(\d{4})[-\/.]?(\d{2})[-\/.]?(\d{2})/);
  if (!m) return '';
  return `${m[1]}-${m[2]}-${m[3]}`;
}

function lifecycleNumber_(value) {
  if (value === null || value === undefined || value === '') return null;
  const text = String(value).replace(/[%+,]/g, '').trim();
  if (!text) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

function lifecycleTimeMinutes_(value) {
  const m = String(value || '').trim().match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return -1;
  return Number(m[1]) * 60 + Number(m[2]) + Number(m[3] || 0) / 60;
}

function lifecycleFmt_(value) {
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(3) : '-';
}

function lifecycleSigned_(value) {
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  return `${n >= 0 ? '+' : ''}${n.toFixed(3)}%p`;
}
