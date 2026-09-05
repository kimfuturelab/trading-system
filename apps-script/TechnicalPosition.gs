const TECH_POSITION_SPREADSHEET_ID = '13BOm_pS5ultKzoBm9fbtUF8zPgfb3PK5i2ArDXZbPoE';
const TECH_POSITION_DAILY_SHEET = '11_DAILY_기술적위치';

function writeTechnicalPosition_(payload) {
  return { sheet: TECH_POSITION_DAILY_SHEET, received: Array.isArray(payload.rows) ? payload.rows.length : 0 };
}
