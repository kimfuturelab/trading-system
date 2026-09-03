const STOCK_V2_MINUTE_SHEET = '장중_1분기록';
const STOCK_V2_SHEETS = Object.freeze({
  '005930': '삼성전자_수급',
  '000660': 'SK하이닉스_수급',
});

function writeStockSupplyLive_(payload) {
  const rows = normalizeStockSupplyRows_(payload);
  const ss = SpreadsheetApp.openById(SUPPLY_SPREADSHEET_ID);
  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    rows.forEach(r => writeStockCurrentRow_(ss, r));
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }
  return { sheet: '삼성전자_수급 / SK하이닉스_수급', received: rows.length, type: 'stock_supply_live' };
}

function writeStockSupplyMinute_(payload) {
  const rows = normalizeStockSupplyRows_(payload);
  const ss = SpreadsheetApp.openById(SUPPLY_SPREADSHEET_ID);
  const minuteSheet = ss.getSheetByName(STOCK_V2_MINUTE_SHEET);
  if (!minuteSheet) throw new Error(`Sheet not found: ${STOCK_V2_MINUTE_SHEET}`);

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    rows.forEach(r => {
      writeStockCurrentRow_(ss, r);
      upsertStockMinute_(minuteSheet, r);
      upsertStockDailyLedger_(ss, r, false);
    });
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }
  return { sheet: STOCK_V2_MINUTE_SHEET, received: rows.length, type: 'stock_supply_minute' };
}

function writeStockSupplyFinal_(payload) {
  const rows = normalizeStockSupplyRows_(payload);
  const ss = SpreadsheetApp.openById(SUPPLY_SPREADSHEET_ID);
  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    rows.forEach(r => {
      writeStockCurrentRow_(ss, r);
      upsertStockDailyLedger_(ss, r, true);
    });
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }
  return { sheet: '삼성전자_수급 / SK하이닉스_수급', received: rows.length, type: 'stock_supply_final' };
}

function normalizeStockSupplyRows_(payload) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) throw new Error('No stock_supply rows supplied.');
  if (rows.length > 2) throw new Error(`Too many stock_supply rows: ${rows.length}`);

  const capturedAt = String(payload.captured_at || '');
  const seen = new Set();
  return rows.map(raw => {
    const code = String(raw.stock_code || '').trim();
    if (!Object.prototype.hasOwnProperty.call(STOCK_V2_SHEETS, code)) {
      throw new Error(`Stock not allowed in Stage3 V2: ${code || '(blank)'}`);
    }
    if (seen.has(code)) throw new Error(`Duplicate stock row: ${code}`);
    seen.add(code);

    const dateKey = String(raw.date || capturedAt.slice(0, 10) || Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd'));
    const snapshotTime = capturedAt.length >= 16 ? capturedAt.slice(11, 16) : String(raw.program_time || '').slice(0, 5);
    return {
      code,
      name: String(raw.stock_name || ''),
      sheetName: STOCK_V2_SHEETS[code],
      date: dateKey,
      snapshotTime,
      provisionalTime: String(raw.provisional_time || ''),
      provisionalForeign: numOrBlank_(raw.provisional_foreign),
      provisionalInstitution: numOrBlank_(raw.provisional_institution),
      mood: String(raw.mood || '잠정대기'),
      programTime: String(raw.program_time || ''),
      programAmount: numOrBlank_(raw.program_amount),
      programQuantity: numOrBlank_(raw.program_quantity),
      finalTime: String(raw.final_time || ''),
      finalForeign: numOrBlank_(raw.final_foreign),
      finalInstitution: numOrBlank_(raw.final_institution),
      status: String(raw.status || 'PENDING'),
      error: String(raw.error || ''),
      source: String(payload.source || ''),
      version: String(payload.collector_version || ''),
    };
  });
}

function writeStockCurrentRow_(ss, r) {
  const sheet = ss.getSheetByName(r.sheetName);
  if (!sheet) throw new Error(`Sheet not found: ${r.sheetName}`);

  const noteParts = [r.code];
  if (r.error) noteParts.push(r.error);
  sheet.getRange(6, 1, 1, 13).setValues([[
    r.date,
    r.provisionalTime,
    r.provisionalForeign,
    r.provisionalInstitution,
    r.mood,
    r.programTime,
    r.programAmount,
    r.programQuantity,
    r.finalTime,
    r.finalForeign,
    r.finalInstitution,
    r.status,
    noteParts.join(' | '),
  ]]);
  sheet.getRange(6, 3, 1, 2).setNumberFormat('#,##0');
  sheet.getRange(6, 7, 1, 2).setNumberFormat('#,##0');
  sheet.getRange(6, 10, 1, 2).setNumberFormat('#,##0');
}

function upsertStockMinute_(sheet, r) {
  if (r.programAmount === '' && r.programQuantity === '') return;
  const timeKey = r.snapshotTime || r.programTime.slice(0, 5);
  if (!timeKey) throw new Error(`No minute timestamp for ${r.code}`);

  const firstDataRow = 4;
  const lastRow = sheet.getLastRow();
  let targetRow = null;
  if (lastRow >= firstDataRow) {
    const scanStart = Math.max(firstDataRow, lastRow - 11);
    const existing = sheet.getRange(scanStart, 1, lastRow - scanStart + 1, 3).getDisplayValues();
    for (let i = existing.length - 1; i >= 0; i--) {
      if (String(existing[i][0]) === r.date && String(existing[i][1]) === timeKey && String(existing[i][2]) === r.code) {
        targetRow = scanStart + i;
        break;
      }
    }
  }
  if (!targetRow) targetRow = Math.max(firstDataRow, lastRow + 1);
  sheet.getRange(targetRow, 1, 1, 8).setValues([[
    r.date,
    timeKey,
    r.code,
    r.name,
    r.provisionalForeign,
    r.provisionalInstitution,
    r.programAmount,
    r.programQuantity,
  ]]);
  sheet.getRange(targetRow, 5, 1, 4).setNumberFormat('#,##0');
}

function upsertStockDailyLedger_(ss, r, isFinal) {
  const sheet = ss.getSheetByName(r.sheetName);
  if (!sheet) throw new Error(`Sheet not found: ${r.sheetName}`);

  const firstDataRow = 43;
  const lastRow = sheet.getLastRow();
  let targetRow = null;
  if (lastRow >= firstDataRow) {
    const dates = sheet.getRange(firstDataRow, 1, lastRow - firstDataRow + 1, 1).getDisplayValues();
    for (let i = dates.length - 1; i >= 0; i--) {
      if (String(dates[i][0]) === r.date) {
        targetRow = firstDataRow + i;
        break;
      }
    }
  }
  if (!targetRow) targetRow = Math.max(firstDataRow, lastRow + 1);

  let existing = new Array(14).fill('');
  if (targetRow <= sheet.getLastRow()) {
    existing = sheet.getRange(targetRow, 1, 1, 14).getValues()[0];
  }

  const currentAmount = r.programAmount;
  let maxAmount = existing[6];
  let maxTime = existing[7];
  let minAmount = existing[8];
  let minTime = existing[9];
  if (typeof currentAmount === 'number') {
    if (typeof maxAmount !== 'number' || currentAmount > maxAmount) {
      maxAmount = currentAmount;
      maxTime = r.programTime || r.snapshotTime;
    }
    if (typeof minAmount !== 'number' || currentAmount < minAmount) {
      minAmount = currentAmount;
      minTime = r.programTime || r.snapshotTime;
    }
  }

  const finalForeign = isFinal ? r.finalForeign : existing[10];
  const finalInstitution = isFinal ? r.finalInstitution : existing[11];
  const completeState = isFinal ? '완료' : '장중';
  const note = [r.code, r.source, r.version, r.error].filter(Boolean).join(' | ');

  sheet.getRange(targetRow, 1, 1, 14).setValues([[
    r.date,
    r.provisionalTime,
    r.provisionalForeign,
    r.provisionalInstitution,
    r.programAmount,
    r.programQuantity,
    maxAmount,
    maxTime,
    minAmount,
    minTime,
    finalForeign,
    finalInstitution,
    completeState,
    note,
  ]]);
  sheet.getRange(targetRow, 3, 1, 5).setNumberFormat('#,##0');
  sheet.getRange(targetRow, 9, 1, 1).setNumberFormat('#,##0');
  sheet.getRange(targetRow, 11, 1, 2).setNumberFormat('#,##0');
}
