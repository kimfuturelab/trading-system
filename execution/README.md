# V0 Trading Execution

## 목적

자동매매 전체 전략을 연결하기 전에 **키움 주문·체결·잔고 경로만 독립적으로 검증**한다.

이 디렉터리는 기존 하이닉스 추격자와 3.5 자금흐름 collector를 수정하지 않는다.

```text
기존
- hynix collector/service        # 수정 금지
- market-flow collector/service  # 수정 금지

신규
- execution/                     # V0 주문엔진 코드
- trading-execution.service      # 후속 배포 시 별도 서비스
```

## V0 안전 원칙

1. 기본 상태는 `AUTO_MODE=OFF`.
2. 기본 `KILL_SWITCH=ON`.
3. `LIVE_SMALL`도 `ALLOW_LIVE_ORDERS=YES`와 당일 `DAILY_ARM_DATE`가 동시에 있어야 한다.
4. 허용 계좌·허용 종목·최대 수량이 맞지 않으면 주문을 차단한다.
5. V0 기본 최대 주문수량은 1주.
6. `intent_id`를 먼저 저장한 뒤 주문한다. 동일 `intent_id` 재사용은 차단한다.
7. 주문 응답이 끊겼다고 같은 주문을 다시 보내지 않는다. 먼저 키움 주문/미체결/체결 상태를 조회한다.
8. 주문번호 수신과 체결은 다른 상태로 취급한다.
9. 재시작 시 로컬 상태만 믿지 않고 키움 실제 미체결·체결·잔고와 대사한다.
10. 조회/대사 실패 시 신규 주문은 차단한다(Fail Closed).

## 기존 서버에서 재사용할 것

현재 3.5에서 검증된 공통 인증층만 재사용한다.

- VM: `kiwoom-server`
- 공통 인증 env: `~/api-read-v2.env`
- 필요 변수: `KIWOOM_MODE`, `APP_KEY`, `APP_SECRET`
- Kiwoom CLI: `kiwoomcli` (fallback `~/.local/bin/kiwoomcli`)

**App Key/Secret, 계좌번호는 GitHub에 저장하지 않는다.**

## 키움 V0 API 지도

- `kt10000` — 국내주식 매수주문 — `/api/dostk/ordr`
- `kt10001` — 국내주식 매도주문 — `/api/dostk/ordr`
- `kt10002` — 국내주식 정정주문 — `/api/dostk/ordr`
- `kt10003` — 국내주식 취소주문 — `/api/dostk/ordr`
- `ka10075` — 미체결요청 — `/api/dostk/acnt`
- `ka10076` — 체결요청 — `/api/dostk/acnt`
- `ka00001` — 계좌번호조회
- `kt00018` — 계좌평가잔고내역

주문 API는 V0 첫 커밋에서는 호출하지 않는다. 먼저 서버 환경과 읽기 경로를 검증한 뒤 연결한다.

## 서버 1차 점검

서버에서 이 브랜치를 받은 후:

```bash
cd ~/trading-system
git fetch origin
git checkout feature/trading-execution-v0

python3 execution/v0_probe.py doctor
python3 execution/v0_probe.py api-map
python3 execution/v0_probe.py safety-test
```

`doctor`는 키 값을 출력하지 않는다. 설치 여부와 필수 환경변수 존재만 확인한다.

## V0 진행 순서

```text
STEP 1  서버/인증/CLI 확인
STEP 2  독립 execution 설정 + SQLite intent 저장소
STEP 3  계좌/잔고/미체결/체결 READ 연결
STEP 4  주문 전 reconciliation + Safety Gate
STEP 5  매수/매도 함수 연결 (기본 OFF)
STEP 6  PAPER/Shadow 검증
STEP 7  명시적 LIVE_SMALL ARM 후 1주 왕복
STEP 8  재시작 후 포지션 복구
```

## 오늘 성공 기준

필수:
- `execution/`이 기존 collector와 독립됨
- 서버 인증/CLI 점검 가능
- Safety Gate 기본 차단 확인
- SQLite 기반 중복 intent 차단 가능

도전:
- 계좌·잔고·미체결 READ 경로까지 연결
- 이후에만 1주 LIVE_SMALL 검토
