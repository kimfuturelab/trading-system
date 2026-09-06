const MATERIAL_SPREADSHEET_ID = '1hwbzCC40rDrBWzIdSOT38oeSm9WBGkicv7oNLLMD5J0';
const MATERIAL_DECISION_SHEET = '05_재료판정';
const MATERIAL_SNAPSHOT_SHEET = '06_판단스냅샷';
const MATERIAL_TZ = 'Asia/Seoul';

function readMaterialEvidence_(payload) {
  const ss = SpreadsheetApp.openById(MATERIAL_SPREADSHEET_ID);
  const decision = ss.getSheetByName(MATERIAL_DECISION_SHEET);
  const snapshots = ss.getSheetByName(MATERIAL_SNAPSHOT_SHEET);
  if (!decision || !snapshots) {
    throw new Error('MATERIAL sheet structure is incomplete.');
  }

  SpreadsheetApp.flush();

  const state = readMaterialState_(decision);
  const consumer = String((payload && payload.consumer) || 'python').trim() || 'python';

  let snapshotStatus = 'SKIPPED_PENDING';
  let snapshotId = '';

  if (state.material_data_ok === 'OK') {
    const result = appendMaterialSnapshotIfNeeded_(snapshots, state, consumer);
    snapshotStatus = result.status;
    snapshotId = result.snapshot_id;
  }

  return {
    material_data_ok: state.material_data_ok,
    material_score: state.material_score,
    material_direction: state.material_direction,
    material_reason_snapshot: state.material_reason_snapshot,
    as_of_time: state.as_of_time,
    evidence_ids: state.evidence_ids,
    snapshot_status: snapshotStatus,
    snapshot_id: snapshotId,
    consumer: consumer,
  };
}

function readMaterialState_(sheet) {
  const dataOk = String(sheet.getRange('B12').getDisplayValue() || '').trim();
  const scoreRaw = sheet.getRange('B13').getValue();
  const direction = String(sheet.getRange('B14').getDisplayValue() || '').trim();
  const reason = String(sheet.getRange('B15').getDisplayValue() || '').trim();
  const asOfValue = sheet.getRange('B16').getValue();
  const evidenceIds = String(sheet.getRange('B17').getDisplayValue() || '').trim();

  const scheduleScore = numberOrNullMaterial_(sheet.getRange('B7').getValue());
  const scheduleReason = String(sheet.getRange('D7').getDisplayValue() || '').trim();
  const accumulatedScore = numberOrNullMaterial_(sheet.getRange('B8').getValue());
  const accumulatedReason = String(sheet.getRange('D8').getDisplayValue() || '').trim();
  const intradayScore = numberOrNullMaterial_(sheet.getRange('B9').getValue());
  const intradayReason = String(sheet.getRange('D9').getDisplayValue() || '').trim();

  const materialScore = numberOrNullMaterial_(scoreRaw);
  const asOfTime = materialDateTime_(asOfValue);

  if (!dataOk) throw new Error('MATERIAL_DATA_OK is blank.');
  if (!asOfTime) throw new Error('AS_OF_TIME is blank or invalid.');

  if (dataOk === 'OK') {
    if (materialScore === null || materialScore < -3 || materialScore > 3) {
      throw new Error(`Invalid MATERIAL_SCORE: ${scoreRaw}`);
    }
    if (!['POSITIVE', 'NEUTRAL', 'NEGATIVE'].includes(direction)) {
      throw new Error(`Invalid MATERIAL_DIRECTION: ${direction}`);
    }
    const expectedDirection = materialScore > 0 ? 'POSITIVE' : (materialScore < 0 ? 'NEGATIVE' : 'NEUTRAL');
    if (direction !== expectedDirection) {
      throw new Error(`MATERIAL score/direction mismatch: ${materialScore}/${direction}`);
    }
    if ([scheduleScore, accumulatedScore, intradayScore].some(v => v === null)) {
      throw new Error('One or more MATERIAL axis scores are blank.');
    }
  }

  return {
    material_data_ok: dataOk,
    material_score: materialScore,
    material_direction: direction || 'PENDING',
    material_reason_snapshot: reason,
    as_of_time: asOfTime,
    evidence_ids: evidenceIds,
    schedule_score: scheduleScore,
    schedule_reason: scheduleReason,
    accumulated_news_score: accumulatedScore,
    accumulated_news_reason: accumulatedReason,
    intraday_score: intradayScore,
    intraday_reason: intradayReason,
    trade_date: Utilities.formatDate(new Date(asOfTime), MATERIAL_TZ, 'yyyy-MM-dd'),
  };
}

function appendMaterialSnapshotIfNeeded_(sheet, state, consumer) {
  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    const firstDataRow = 4;
    const lastRow = Math.max(firstDataRow - 1, sheet.getLastRow());

    if (lastRow >= firstDataRow) {
      const rows = sheet.getRange(firstDataRow, 1, lastRow - firstDataRow + 1, 16).getDisplayValues();
      for (let i = 0; i < rows.length; i++) {
        const existingAsOf = String(rows[i][2] || '').trim();
        if (existingAsOf !== state.as_of_time) continue;

        const existingScore = String(rows[i][11] || '').trim();
        const existingDirection = String(rows[i][12] || '').trim();
        if (existingScore === String(state.material_score) && existingDirection === state.material_direction) {
          return { status: 'EXISTS', snapshot_id: String(rows[i][0] || '') };
        }
        throw new Error(`snapshot_conflict_same_as_of: ${state.as_of_time}`);
      }
    }

    const snapshotId = materialSnapshotId_(state.as_of_time);
    const targetRow = Math.max(firstDataRow, sheet.getLastRow() + 1);

    sheet.getRange(targetRow, 1, 1, 16).setValues([[
      snapshotId,
      state.trade_date,
      state.as_of_time,
      state.material_data_ok,
      state.schedule_score,
      state.schedule_reason,
      state.accumulated_news_score,
      state.accumulated_news_reason,
      state.intraday_score,
      state.intraday_reason,
      state.evidence_ids,
      state.material_score,
      state.material_direction,
      state.material_reason_snapshot,
      consumer,
      '고정완료',
    ]]);

    SpreadsheetApp.flush();
    return { status: 'APPENDED', snapshot_id: snapshotId };
  } finally {
    lock.releaseLock();
  }
}

function materialSnapshotId_(asOfTime) {
  const compact = String(asOfTime || '')
    .replace(/[^0-9]/g, '')
    .slice(0, 14);
  if (compact.length !== 14) throw new Error(`Invalid AS_OF_TIME for snapshot id: ${asOfTime}`);
  return `${compact}_MATERIAL_AUTO`;
}

function materialDateTime_(value) {
  if (value instanceof Date && !isNaN(value.getTime())) {
    return Utilities.formatDate(value, MATERIAL_TZ, 'yyyy-MM-dd HH:mm:ss') + ' KST';
  }

  const text = String(value || '').trim();
  if (!text) return '';

  const normalized = text.replace(/\s+KST$/i, '').trim();
  const parsed = new Date(normalized.replace(' ', 'T') + '+09:00');
  if (isNaN(parsed.getTime())) return '';
  return Utilities.formatDate(parsed, MATERIAL_TZ, 'yyyy-MM-dd HH:mm:ss') + ' KST';
}

function numberOrNullMaterial_(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
