/**
 * GOOGLEFINANCE KOSPI 종가 반영시각 SHADOW 감시기
 *
 * - 운영 B열/시장국면_개선판 수정 금지
 * - __시장국면_자동조회 J:R 비교판만 사용
 * - 15:25~16:45 KST 사이 5분마다 확인
 * - 오늘 행이 없으면 SHADOW 행을 자동 생성
 * - GOOGLEFINANCE 종가가 처음 보이면 O열(GF 확인시각)에 최초시각 기록
 */

const GF_CLOSE_WATCH = Object.freeze({
  SS_ID: '1FTmoK13a9COaIyUju89w0GGWzR6W7CuPORJLmhbQ6Wg',
  SHEET: '__시장국면_자동조회',
  START_ROW: 2,
  DATE_COL: 10,       // J
  GF_CLOSE_COL: 11,   // K
  KRX_CLOSE_COL: 12,  // L
  DIFF_COL: 13,       // M
  STATUS_COL: 14,     // N
  GF_SEEN_COL: 15,    // O
  KRX_SEEN_COL: 16,   // P
  OPERATING_COL: 17,  // Q
  MEMO_COL: 18,       // R
  TZ: 'Asia/Seoul'
});


function gfWatchDateKey_(date) {
  return Utilities.formatDate(date, GF_CLOSE_WATCH.TZ, 'yyyy-MM-dd');
}


function gfWatchToday_() {
  return gfWatchDateKey_(new Date());
}


function gfWatchFindRow_(sheet, dateKey) {
  const lastRow = Math.max(sheet.getLastRow(), GF_CLOSE_WATCH.START_ROW);
  const count = lastRow - GF_CLOSE_WATCH.START_ROW + 1;

  if (count <= 0) return null;

  const values = sheet
    .getRange(GF_CLOSE_WATCH.START_ROW, GF_CLOSE_WATCH.DATE_COL, count, 1)
    .getValues();

  for (let i = 0; i < values.length; i++) {
    const value = values[i][0];
    if (!value) continue;

    let key = '';

    if (value instanceof Date) {
      key = gfWatchDateKey_(value);
    } else {
      key = String(value).trim().slice(0, 10);
    }

    if (key === dateKey) {
      return GF_CLOSE_WATCH.START_ROW + i;
    }
  }

  return null;
}


function gfWatchEnsureTodayRow_(sheet, todayKey) {
  let row = gfWatchFindRow_(sheet, todayKey);

  if (row) return row;

  row = Math.max(sheet.getLastRow() + 1, GF_CLOSE_WATCH.START_ROW);

  const parts = todayKey.split('-').map(Number);
  const y = parts[0];
  const m = parts[1];
  const d = parts[2];

  sheet.getRange(row, GF_CLOSE_WATCH.DATE_COL).setValue(todayKey);

  sheet
    .getRange(row, GF_CLOSE_WATCH.GF_CLOSE_COL)
    .setFormula(
      '=IFERROR(INDEX(GOOGLEFINANCE("KRX:KOSPI","close",DATE(' +
      y + ',' + m + ',' + d + '),DATE(' +
      y + ',' + m + ',' + d + '),"DAILY"),2,2),"")'
    )
    .setNumberFormat('#,##0.00');

  sheet
    .getRange(row, GF_CLOSE_WATCH.DIFF_COL)
    .setFormula(
      '=IF(OR(K' + row + '="",L' + row + '=""),"",K' + row + '-L' + row + ')'
    )
    .setNumberFormat('#,##0.00');

  sheet
    .getRange(row, GF_CLOSE_WATCH.STATUS_COL)
    .setFormula(
      '=IF(K' + row + '="","GF PENDING",IF(L' + row +
      '="","KRX PENDING",IF(ABS(K' + row + '-L' + row +
      ')<=0.0001,"MATCH","WARN")))'
    );

  sheet.getRange(row, GF_CLOSE_WATCH.OPERATING_COL).setValue('NO');
  sheet
    .getRange(row, GF_CLOSE_WATCH.MEMO_COL)
    .setValue('GOOGLEFINANCE 장마감 반영시각 SHADOW 관찰 / 운영 미반영');

  return row;
}


function googleFinanceCloseWatchTick() {
  const now = new Date();
  const hhmm = Number(
    Utilities.formatDate(now, GF_CLOSE_WATCH.TZ, 'HHmm')
  );

  // 장마감 주변 시간에만 실제 작업
  if (hhmm < 1525 || hhmm > 1645) {
    return 'OUTSIDE_WINDOW';
  }

  const day = Number(
    Utilities.formatDate(now, GF_CLOSE_WATCH.TZ, 'u')
  );

  // 토/일 제외
  if (day === 6 || day === 7) {
    return 'WEEKEND';
  }

  const ss = SpreadsheetApp.openById(GF_CLOSE_WATCH.SS_ID);
  const sheet = ss.getSheetByName(GF_CLOSE_WATCH.SHEET);

  if (!sheet) {
    throw new Error('시트 없음: ' + GF_CLOSE_WATCH.SHEET);
  }

  const todayKey = gfWatchToday_();
  const row = gfWatchEnsureTodayRow_(sheet, todayKey);

  SpreadsheetApp.flush();
  Utilities.sleep(1500);

  const close = sheet
    .getRange(row, GF_CLOSE_WATCH.GF_CLOSE_COL)
    .getValue();

  const seenAt = sheet
    .getRange(row, GF_CLOSE_WATCH.GF_SEEN_COL)
    .getValue();

  if (
    close !== '' &&
    Number.isFinite(Number(close)) &&
    Number(close) > 0 &&
    !seenAt
  ) {
    const stamp = Utilities.formatDate(
      now,
      GF_CLOSE_WATCH.TZ,
      'yyyy-MM-dd HH:mm:ss'
    );

    sheet
      .getRange(row, GF_CLOSE_WATCH.GF_SEEN_COL)
      .setValue(stamp);

    sheet
      .getRange(row, GF_CLOSE_WATCH.MEMO_COL)
      .setValue(
        'GOOGLEFINANCE 최초 종가 확인=' + stamp +
        ' / 운영 미반영'
      );

    return {
      status: 'GF_CLOSE_SEEN',
      row: row,
      date: todayKey,
      close: Number(close),
      firstSeenAt: stamp
    };
  }

  return {
    status: close ? 'ALREADY_SEEN' : 'GF_PENDING',
    row: row,
    date: todayKey,
    close: close || null,
    firstSeenAt: seenAt || null
  };
}


function deleteGoogleFinanceCloseWatchTriggers_() {
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === 'googleFinanceCloseWatchTick') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}


function installGoogleFinanceCloseWatch() {
  deleteGoogleFinanceCloseWatchTriggers_();

  ScriptApp
    .newTrigger('googleFinanceCloseWatchTick')
    .timeBased()
    .everyMinutes(5)
    .create();

  return 'GF CLOSE WATCH installed: every 5 min; active work only 15:25~16:45 KST';
}


function uninstallGoogleFinanceCloseWatch() {
  deleteGoogleFinanceCloseWatchTriggers_();
  return 'GF CLOSE WATCH removed';
}


function testGoogleFinanceCloseWatchNow() {
  const ss = SpreadsheetApp.openById(GF_CLOSE_WATCH.SS_ID);
  const sheet = ss.getSheetByName(GF_CLOSE_WATCH.SHEET);

  if (!sheet) {
    throw new Error('시트 없음: ' + GF_CLOSE_WATCH.SHEET);
  }

  const todayKey = gfWatchToday_();
  const row = gfWatchEnsureTodayRow_(sheet, todayKey);

  SpreadsheetApp.flush();
  Utilities.sleep(1500);

  return {
    row: row,
    date: todayKey,
    close: sheet.getRange(row, GF_CLOSE_WATCH.GF_CLOSE_COL).getValue(),
    status: sheet.getRange(row, GF_CLOSE_WATCH.STATUS_COL).getDisplayValue(),
    firstSeenAt: sheet.getRange(row, GF_CLOSE_WATCH.GF_SEEN_COL).getDisplayValue(),
    operating: sheet.getRange(row, GF_CLOSE_WATCH.OPERATING_COL).getDisplayValue()
  };
}
