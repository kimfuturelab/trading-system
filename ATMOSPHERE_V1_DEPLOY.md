# ATMOSPHERE V1 deployment

## Architecture
ATMOSPHERE has its own Google Apps Script Web App bound to the dedicated ATMOSPHERE workbook.

It must NOT be installed into:
- 3단계 market_supply Web App
- 3.5 TOP100 Web App
- P006 technical_position Web App
- Trading Master Apps Script

Authority remains ADVISORY ONLY.

## 1. Dedicated Apps Script
Open the ATMOSPHERE workbook:
https://docs.google.com/spreadsheets/d/1UAArSBsQj4C3VU_trfC8aH_NRpRwI62URW-dJqJrcGI/edit

Extensions -> Apps Script.

Create/replace one script file with repository file:
`apps-script/AtmosphereV1.gs`

Set a unique secret in `setAtmosphereIngestSecretOnce()`, run it once, then remove the literal secret from the editor if desired.

Deploy as a Web App and record its /exec URL as:
`ATMOSPHERE_WEBHOOK_URL`

The same secret goes only into:
`ATMOSPHERE_INGEST_SECRET`

## 2. Server staging
Do not switch the server repository branch.

Copy only these files from `origin/feature/atmosphere-v1` into the current working tree after backup:
- collector/atmosphere_v1.py
- collector/atmosphere-v1.service
- collector/install_atmosphere_v1.sh

Run py_compile first.

## 3. Backfill / model
`python collector/atmosphere_v1.py --backfill 50`

Expected:
- 03_검증로그 populated
- 06_모델파라미터 populated
- 07_API_RAW audit rows
- no changes to 재수차/Base R/ENTRY

## 4. Live schedule
- 08:50 KST: preopen LOCK
- 09:00+: actual opening gap
- 09:30: checkpoint
- 15:35: close checkpoint

## 5. Fail Closed
If dedicated webhook or dedicated secret is missing, collector exits with configuration error.
It never falls back to SHEETS_WEBHOOK_URL or INGEST_SECRET.
