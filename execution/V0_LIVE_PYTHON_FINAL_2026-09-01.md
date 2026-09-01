# V0 실계좌 Python 최종 검증 — 2026-09-01

## 결론

V0 최소 주문엔진의 핵심 실계좌 경로를 `LIVE_SMALL` 1주로 검증했다.

검증된 전체 흐름:

```text
DISARM
→ private account config
→ daily ARM
→ no-order preflight
→ broker/local reconciliation
→ symbol-specific max orderable capacity
→ BUY intent persisted first
→ real BUY submitted
→ broker fill reconciliation
→ holding 1
→ SELL intent persisted first
→ real SELL submitted
→ broker fill reconciliation
→ holding 0
→ open broker orders 0
→ open local intents 0
→ DISARM
```

이 결과는 전체 자동매매 시스템의 LIVE 승격을 의미하지 않는다. V0 실행기술 핵심 경로를 닫고 V1 자동청산 코드 개발을 시작할 수 있다는 의미다.

## 1. 수동 Kiwoom CLI 실계좌 왕복

V0 Python 이식 전, 같은 서버/인증/CLI 경로 자체를 먼저 검증했다.

- 종목: 삼성전자 `005930`
- 시장: KRX
- 수량: 1주
- 방식: 시장가
- 수동 BUY 주문번호: `0013510`
- BUY 체결가: `259,250`
- 수동 SELL 주문번호: `0013540`
- SELL 체결가: `259,000`
- 최종 보유: 0

이 단계의 목적은 Kiwoom REST 주문 통로와 실제 체결/잔고 반영이 정상임을 확인하는 것이었다.

## 2. Python V0 실계좌 BUY

실행 경로는 `execution/v0_engine.py live-order`를 사용했다.

주문 직전 확인:

- 계좌: configured/broker suffix 일치
- 종목 allowlist: `005930`
- 최대수량: 1주
- 당일 ARM: 일치
- Kill Switch: OFF
- broker open orders: 0
- local open intents: 0
- 기존 holding: 0
- reconciliation: PASS

Capacity 확인:

- 최대주문가능금액: `499,167`
- 주문가능현금 참고값: `240,761`
- 금일재사용가능금액 참고값: `258,405`
- 예수금 참고값: `500,011`
- 기준가격: `259,000`
- 필요금액: `259,000`
- 판정: PASS

실제 BUY 결과:

- intent_id: `V0-LIVE-20260901-BUY-001`
- 주문번호: `0016681`
- 체결가: `259,750`
- 체결수량: 1
- 상태전이: `SUBMITTED → FILLED`
- 체결 후 holding: 1
- broker open orders: 0
- local open intents: 0

## 3. Python V0 실계좌 SELL

BUY가 broker 기준 `FILLED`이고 실제 holding 1주임을 확인한 뒤에만 SELL을 실행했다.

실제 SELL 결과:

- intent_id: `V0-LIVE-20260901-SELL-001`
- 주문번호: `0016730`
- 체결가: `259,250`
- 체결수량: 1
- 상태전이: `SUBMITTED → FILLED`
- 체결 후 holding: 0
- broker open orders: 0
- local open intents: 0
- reconciliation: PASS

체결 payload상 비용 참고:

- BUY 수수료: 30원
- SELL 수수료: 30원
- SELL 세금: 517원
- 가격손익: -500원
- 해당 1주 기술검증 roundtrip의 표시 비용 합계: -1,077원

비용 수치는 이번 체결 payload 기록값이며 전략 성과평가 목적이 아니다.

## 4. 최종 안전상태

검증 종료 후 `execution/disarm_live_small.py`를 실행했다.

```text
KILL_SWITCH=ON
ALLOW_LIVE_ORDERS=NO
DAILY_ARM_DATE=(empty)
```

계좌/종목/최대수량 allowlist는 유지하되 실제 write 권한만 다시 잠갔다.

## 5. Capacity 정책 수정 — 중요 시행착오

초기 preflight는 일반 `주문가능금액`/`주문가능현금`을 BUY capacity로 사용해 `240,761 < 300,000`으로 잘못 차단했다.

Kiwoom `domestic orders chance` 실제 응답을 확인한 결과:

```text
증거금20%주문가능금액 = 499,167
증거금30%주문가능금액 = 499,167
증거금40%주문가능금액 = 499,167
증거금50%주문가능금액 = 499,167
증거금60%주문가능금액 = 499,167
증거금100%주문가능금액 = 499,167
주문가능현금 = 240,761
금일재사용가능금액 = 258,405
예수금 = 500,011
```

따라서 V0 정책을 다음과 같이 수정했다.

```text
BUY capacity Source of Truth
= orders chance 응답의 증거금별 주문가능금액 중 최대값

PASS 조건
= 최대주문가능금액 >= 기준가격 × 주문수량
```

`예수금`, `주문가능현금`, 일반 cash 조회값은 참고 정보일 뿐 신규 BUY capacity 차단 근거로 사용하지 않는다.

## 6. 계좌번호 REST suffix 처리

Kiwoom 앱에 보이는 기본 계좌번호와 REST account 형식의 길이가 달라 사용자가 뒤 분류 suffix를 직접 붙이는 혼동이 발생했다.

수정 후 원칙:

- 사용자는 서버 프롬프트에 앱의 기본 계좌번호를 그대로 입력한다.
- 전체 번호는 화면/쉘 히스토리에 노출하지 않는다.
- Python은 이미 인증된 broker masked REST account의 길이와 tail을 대조한다.
- REST 분류 suffix는 broker 값에서 자동 반영한다.
- 전체 계좌번호/App Key/App Secret은 GitHub, Sheet, 채팅에 저장하지 않는다.

## 7. V0 확정 안전원칙

1. Broker가 최종 truth다.
2. 주문번호 수신은 체결이 아니다.
3. `intent_id`는 broker write 전에 저장한다.
4. timeout/응답유실/visibility 지연 시 blind resend 금지.
5. 신규 write 전 항상 broker READ + local reconciliation.
6. broker open order 또는 local open intent가 있으면 신규 write 차단.
7. BUY는 기존 보유가 있으면 V0에서 stacking 차단.
8. SELL은 실제 broker holding보다 많은 수량을 요청하면 차단.
9. BUY capacity는 symbol-specific `orders chance`만 사용.
10. LIVE는 당일 ARM + account allowlist + symbol allowlist + max qty + explicit CLI acknowledgement가 모두 필요.
11. 대기/종료 중에는 DISARM한다.
12. 불확실하면 추정하지 않고 Fail Closed한다.

## 8. Fixture/상태기계 검증

실계좌 주문 전에 네트워크/실주문 0 fixture에서 다음을 검증했다.

- BUY intent 선저장
- `SUBMITTED → FILLED`
- holding 1
- DB reopen 후 terminal state 유지
- SELL `SUBMITTED → FILLED`
- final holding 0
- 동일 intent_id 중복 차단
- open/unfilled order blocking
- partial/open 상태 blocking
- broker visibility 지연 시 resend 금지

Demo/mock credential은 서버에 없었고 demo 경로는 정상적으로 Fail Closed 했다.

## 9. V0 완료판정과 남은 위험예외

V0 핵심 실행 경로는 완료로 본다.

다만 다음 위험예외는 실계좌로 억지 재현하지 않았다.

- 실제 부분체결
- 실제 주문 거부/취소
- broker write 직후 프로세스 강제 재시작
- 실제 네트워크 timeout/ACK 유실
- 429 rate-limit이 주문 직전에 발생하는 상황

이 항목들은 실전 규모를 키우는 근거가 아니라 V1/Shadow 예외 시나리오에서 계속 검증한다.

## 10. 다음 단계 — V1 자동청산

V1의 역할은 전략을 새로 판단하는 것이 아니다.

상위 단계가 이미 확정한 아래 값을 받아 실행만 한다.

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

V1 우선순위:

1. EXIT_PLAN 검증 및 Fail Closed
2. POSITION → EXIT_ARMED → EXIT_TRIGGERED → EXIT_SUBMITTED → PARTIAL_EXIT/CLOSED/HALTED 상태머신
3. STOP / TAKE_PROFIT / TIMECUT 최초 1개 트리거 고정
4. duplicate SELL 방지
5. 부분체결 시 잔여수량 기준 관리
6. 재시작 시 broker holdings/order/fill 기반 복구
7. fixture E2E
8. 실시간 가격 READ Shadow
9. 그 뒤에만 1주 자동청산 LIVE_SMALL 검토
