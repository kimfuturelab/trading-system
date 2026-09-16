# ATMOSPHERE V1 deployment

## Authority
ATMOSPHERE is advisory only. It must not change 재수차, Base R, Final R, ENTRY, or order authority.

## 1. Apps Script minimal deploy
Use the same Apps Script project that currently handles `SHEETS_WEBHOOK_URL`.

1. Add a new script file named `AtmosphereV1.gs` using the repository file.
2. In `Code.gs` add only this route inside the existing `switch (payload.type)`:

```javascript
case 'atmosphere':
  result = writeAtmosphere_(payload);
  break;
```

3. Deploy a new Web App version without changing the URL.
4. Do not replace the whole deployed Code.gs from an older Git copy.

## 2. Server deploy
After Apps Script deployment:

```bash
cd ~/trading-system-stage3
git fetch origin feature/atmosphere-v1
git checkout feature/atmosphere-v1
source .venv/bin/activate
bash collector/install_atmosphere_v1.sh
```

The installer:
- py_compile checks the collector
- runs a 50-trading-day backfill/model build
- writes model/backfill only to the dedicated ATMOSPHERE workbook
- enables the live systemd service

## 3. Live schedule
- 08:50 KST: preopen features and expected range LOCK
- 09:00+: actual SK Hynix open and opening-gap capture
- 09:30: follow-through checkpoint
- 15:35: close checkpoint

## 4. Inputs
- NQ regular US-session move from Yahoo NQ=F 5-minute data
- SKHY ADR daily return from Kiwoom usa20590
- NQ post-US-close to 08:50 move
- Existing 1C_장전뉴스 total raw score and post-close news score
- SK Hynix prior close/open/current from Kiwoom ka10086 / ka10003

## 5. Model
Initial `ATM_PRICE_V1` trains only price features:
- NQ_US
- ADR_REL = SKHY_ADR - NQ_US
- NQ_POST

NEWS_POST is logged but beta is locked to 0 until enough validated post-close-news observations exist.

Expected center:
`alpha + beta1*NQ_US + beta2*ADR_REL + beta3*NQ_POST`

Expected band:
80th percentile of validation absolute error.

## 6. Fail-closed behavior
If required market data is missing, the collector does not invent 0. The live event remains unsent/PENDING and retries on the next loop.
