# 3.5단계 자금흐름 — Kiwoom REST API 1차 연결

## 이번 단계 목표

화면 개발보다 먼저 아래 데이터 파이프라인을 실제로 가동한다.

`Kiwoom REST API -> 사용자 PC Python collector -> Google Apps Script Web App -> 3.5단계 자금흐름 / 원시_TOP100`

Supabase와 Vercel은 1차 검증에서 제외한다.
- Supabase: 장기 스냅샷/백테스트 DB가 필요해질 때 추가
- Vercel: 검증된 데이터로 최종 화면을 만들 때 추가

## 왜 사용자 PC에서 수집하는가

키움 REST API는 App Key 발급 전에 허용 IP 등록이 필요하다. 따라서 1차는 사용자의 현재 공인 IP를 키움에 등록하고 해당 PC에서 collector를 실행하는 구조가 가장 단순하다.

## Kiwoom 사용 API

### OAuth
- API ID: `au10001`
- Method: POST
- URL: `/oauth2/token`
- 토큰: 24시간 유효

### 거래대금 TOP100
- API ID: `ka10032`
- Method: POST
- URL: `/api/dostk/rkinfo`
- 요청값
  - `mrkt_tp=000` : 전체
  - `mang_stk_incls=1` : 관리종목 미포함
  - `stex_tp=3` : 통합(KRX+NXT/SOR)
- 주요 응답
  - `stk_cd`
  - `now_rank`
  - `pred_rank` (전일순위; 장중 직전 스냅샷 순위와 다름)
  - `stk_nm`
  - `cur_prc`
  - `flu_rt`
  - `now_trde_qty`
  - `trde_prica`

collector는 별도 로컬 state 파일을 사용해 `직전 스냅샷 순위`와 `순위 변화`를 계산한다.

## GitHub 파일

- `collector/kiwoom_collector.py` : 토큰 발급, ka10032 수집, 정규화, Apps Script 전송
- `collector/.env.example` : 환경변수 템플릿
- `collector/requirements.txt` : Python 패키지
- `collector/start_loop.bat` : 5분 반복 실행용 Windows 런처
- `apps-script/Code.gs` : Google Sheets 적재 Web App

## 로컬 설치

```bash
cd collector
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`.env`에 실제 App Key/App Secret을 넣는다. **실제 키는 GitHub에 올리지 않는다.**

## Apps Script 설정

1. 3.5단계 Google Sheet에서 Apps Script 프로젝트를 연다.
2. `apps-script/Code.gs` 내용을 붙여넣는다.
3. `setIngestSecretOnce()` 안의 `CHANGE_ME`를 임의의 긴 문자열로 바꾼 뒤 1회 실행한다.
4. Web App으로 배포한다.
5. 배포 URL을 `.env`의 `SHEETS_WEBHOOK_URL`에 넣는다.
6. 동일한 비밀문자열을 `.env`의 `INGEST_SECRET`에 넣는다.

## 1회 테스트

```bash
python kiwoom_collector.py
```

정상이면 `원시_TOP100`의 2행 이하가 현재 거래대금 상위 종목으로 교체된다.

## 반복 실행

```bash
python kiwoom_collector.py --loop
```

또는 `start_loop.bat` 실행.

기본 반복 주기는 300초(5분)이다.

## 최초 실데이터 검증 필수

`trde_prica`의 단위는 실제 값 한 종목을 HTS와 대조해 반드시 검증한다. 현재 `.env.example`의 `TRADING_VALUE_DIVISOR=100`은 초기 변환 가정이며, 실제 검증 후 고정한다.

검증 순서:
1. TOP100 행수
2. 종목코드/순위
3. 현재가/등락률
4. 거래대금 단위
5. KRX/NXT 통합 중복 여부
6. 5분 후 직전 스냅샷 순위/순위변화

## 다음 단계

TOP100이 2~3회 정상 적재되는 것을 확인한 다음에만 다음을 붙인다.
1. 키움 테마 API (`ka90001`, `ka90002`)
2. 종목 복수테마 -> 오늘 대표테마 1개 선택
3. 테마 거래대금 점유율
4. 5/15/30/60분 Delta 점유율
5. 시장 무게추
6. 주도테마 생애주기
7. 캘린더 자동 기록

4단계 후보 자동전달은 현재 범위에서 제외한다.
