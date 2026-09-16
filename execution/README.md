# Trading Execution — V0 완료 / V1 진입

## 목적

자동매매 전체 전략을 한 번에 연결하지 않고, **키움 주문·체결·잔고·청산 경로를 단계별로 독립 검증**한다.

기존 하이닉스 추격자와 3.5 자금흐름 collector는 수정하지 않는다.

```text
기존
- hynix collector/service        # 수정 금지
- market-flow collector/service  # 수정 금지

신규
- execution/                     # 주문·체결·포지션 실행 계층
- trading-execution.service      # 후속 배포 시 별도 서비스
```

## V0 최종 상태 — 2026-09-01

V0 최소 주문엔진은 **실계좌 Python LIVE_SMALL 1주 왕복까지 PASS**했다.

```text
Python BUY  005930 1주 KRX 시장가
→ 주문 0016681
→ 259,750원 체결
→ SUBMITTED → FILLED
→ holding 1

Python SELL 005930 1주 KRX 시장가
→ 주문 0016730
→ 259,250원 체결
→ SUBMITTED → FILLED
→ holding 0

최종
→ broker open orders 0
→ local open intents 0
→ reconciliation PASS
→ DISARM
```

상세 최종 검증 기록:

- [`V0_LIVE_PYTHON_FINAL_2026-09-01.md`](./V0_LIVE_PYTHON_FINAL_2026-09-01.md)
- [`V0_LIVE_ROUNDTRIP_2026-09-01.md`](./V0_LIVE_ROUNDTRIP_2026-09-01.md)
- [`V0_STATE_MACHINE_VALIDATION_2026-09-01.md`](./V0_STATE_MACHINE_VALIDATION_2026-09-01.md)

## V0 안전 원칙

1. 기본 상태는 `AUTO_MODE=OFF` 또는 DISARM.
2. 대기/종료 중 `KILL_SWITCH=ON`.
3. LIVE_SMALL도 `ALLOW_LIVE_ORDERS=YES`와 당일 `DAILY_ARM_DATE`가 동시에 있어야 한다.
4. 허용 계좌·허용 종목·최대 수량이 맞지 않으면 주문을 차단한다.
5. V0 기본 최대 주문수량은 1주.
6. `intent_id`를 먼저 저장한 뒤 주문한다. 동일 `intent_id` 재사용은 차단한다.
7. 주문 응답이 끊겼다고 같은 주문을 다시 보내지 않는다. 먼저 키움 주문/미체결/체결 상태를 조회한다.
8. 주문번호 수신과 체결은 다른 상태로 취급한다.
9. 재시작 시 로컬 상태만 믿지 않고 키움 실제 미체결·체결·잔고와 대사한다.
10. 조회/대사 실패 시 신규 주문은 차단한다(Fail Closed).
11. BUY capacity는 일반 예수금/주문가능현금이 아니라 **종목별 `orders chance`의 최대주문가능금액**을 Source of Truth로 사용한다.
12. 실주문 종료 후 반드시 DISARM한다.

## BUY capacity 기준

2026-09-01 실계좌 검증 중 일반 `주문가능현금`을 기준으로 잘못 차단하는 시행착오가 있었다.

최종 정책:

```text
maximum_orderable_amount
= orders chance 응답의 증거금별 주문가능금액 중 최대값

BUY PASS
= maximum_orderable_amount >= reference_price × qty
```

`예수금`, `주문가능현금`, `금일재사용가능금액`은 참고만 한다.

## 기존 서버에서 재사용할 것

현재 3.5에서 검증된 공통 인증층만 재사용한다.

- VM: `kiwoom-server`
- 공통 인증 env: `~/api-read-v2.env`
- 필요 변수: `KIWOOM_MODE`, `APP_KEY`, `APP_SECRET`
- Kiwoom CLI: `kiwoomcli` (fallback `~/.local/bin/kiwoomcli`)

**App Key/Secret, 전체 계좌번호는 GitHub에 저장하지 않는다.**

## 키움 V0 API 지도

- `kt10000` — 국내주식 매수주문
- `kt10001` — 국내주식 매도주문
- `kt10002` — 국내주식 정정주문
- `kt10003` — 국내주식 취소주문
- `ka10075` — 미체결요청
- `ka10076` — 체결요청
- `ka00001` — 계좌번호조회
- `kt00018` — 계좌평가잔고내역
- `domestic orders chance` — 종목별 주문가능 시뮬레이션 / BUY capacity Source of Truth

## V0 검증 체크포인트

```text
STEP 1  서버/인증/CLI 확인                          DONE
STEP 2  독립 execution 설정 + SQLite intent 저장소   DONE
STEP 3  계좌/잔고/미체결/체결 READ                  DONE
STEP 4  reconciliation + Safety Gate               DONE
STEP 5  매수/매도 broker adapter                    DONE
STEP 6  상태머신/fixture/restart/duplicate           DONE
STEP 7  no-order LIVE_SMALL preflight               DONE
STEP 8  Python 실계좌 BUY 1주                       DONE
STEP 9  BUY 체결/holding reconciliation             DONE
STEP 10 Python 실계좌 SELL 1주                      DONE
STEP 11 final flat/open-intent=0 확인               DONE
STEP 12 DISARM                                      DONE
```

## V0 완료판정

V0 핵심 실행기술 경로는 완료한다.

단, 이것은 정상 R/전체 자동매매 LIVE 승격이 아니다. 실제 부분체결·거부/취소·write 직후 프로세스 재시작·ACK 유실 등 고위험 예외는 억지로 실계좌에서 재현하지 않았고 V1/Shadow 검증 대상으로 유지한다.

## 다음 단계 — V1 자동청산

V1은 전략판단 계층이 아니다. 상위 단계가 이미 확정한 EXIT_PLAN을 안전하게 집행한다.

```text
EXIT_PLAN
- plan_id
- symbol
- qty
- entry_price
- stop_price
- take_profit_price / 단계별 목표
- timecut_at
```

V1 개발 순서:

```text
1. EXIT_PLAN 규격 + validation
2. EXIT 상태머신
3. STOP / TAKE_PROFIT / TIMECUT 최초 트리거 고정
4. duplicate SELL 차단
5. 부분체결/잔여수량 처리
6. restart recovery
7. fixture E2E
8. broker price READ Shadow
9. 이후에만 LIVE_SMALL 자동청산 검토
```

V1 fixture/Shadow 통과 전 추가 실주문은 하지 않는다.
