# V1 자동 청산/매도 엔진 설계

## 1. 역할 경계

V1 execution 계층은 **전략을 판단하지 않는다.**

상위 계층이 이미 확정한 EXIT_PLAN을 받아 다음만 수행한다.

```text
보유 확인
→ EXIT_PLAN 유효성 검증
→ 가격/시간 감시
→ STOP / TAKE_PROFIT / TIMECUT 최초 트리거 1개 확정
→ SELL intent 정확히 1개 생성
→ broker write 전 대사
→ 매도 주문
→ 체결/잔여수량 대사
→ holding 0 확인 후 CLOSED
```

V1이 임의로 하지 않는 것:

- 종목 선택
- 진입 판단
- R 결정
- 손절가 변경
- 목표가 변경
- 타임컷 연장
- 물타기
- 트리거 발생 후 더 좋은 가격을 기다리는 판단

## 2. V1 최소 EXIT_PLAN

초기 V1은 LONG 1포지션, full-exit 1회 구조를 먼저 검증한다.

```text
plan_id
symbol
qty
entry_price
stop_price
take_profit_price
timecut_at
```

필수 관계:

```text
qty > 0
stop_price > 0
take_profit_price > 0
stop_price < entry_price < take_profit_price
timecut_at = timezone-aware datetime
```

하나라도 틀리면 Fail Closed.

2단계 익절/50% 분할청산은 V1 상태머신이 안정된 뒤 별도 확장한다.

## 3. 상태머신

```text
EXIT_ARMED
  ↓ trigger
EXIT_TRIGGERED
  ↓ pre-write reconcile PASS
EXIT_SUBMITTED
  ├─ partial fill → PARTIAL_EXIT
  ├─ holding 0   → CLOSED
  └─ mismatch/error → HALTED
```

`EXIT_TRIGGERED` 이후 trigger_reason은 변경하지 않는다.

## 4. 트리거 우선순위

LONG 기준:

1. `STOP`: `last_price <= stop_price`
2. `TAKE_PROFIT`: `last_price >= take_profit_price`
3. `TIMECUT`: `now >= timecut_at`

동일 평가 순간 여러 조건이 가능하면 안전상 STOP이 가장 우선이다.

한 번 trigger가 확정되면 이후 가격이 변해도 reason을 바꾸거나 새 SELL intent를 만들지 않는다.

## 5. SELL 중복 방지

V0 원칙을 그대로 계승한다.

- `exit_intent_id`는 broker write 전에 고정/저장한다.
- 동일 plan에서 trigger는 1번만 reserve 가능.
- open broker order가 있으면 새 SELL 금지.
- local open intent가 있으면 새 SELL 금지.
- ACK/응답 유실 시 blind resend 금지.
- broker 주문/체결/holding을 조회해 먼저 대사한다.

## 6. 보유수량 Gate

실매도 직전 broker holding이 Source of Truth다.

초기 V1 full-exit에서는:

```text
broker_holding_qty == plan.remaining_qty
```

이어야 신규 SELL을 허용한다.

0이면 이미 CLOSED 후보이며 매도하지 않는다.

더 많거나 적으면 외부 수동매매/부분체결/대사불일치 가능성이 있으므로 자동으로 추정하지 않고 HALTED/재대사한다.

## 7. 부분체결

초기 V1은 부분체결을 ‘새 주문을 자동으로 다시 내는 신호’로 사용하지 않는다.

```text
original qty 10
SELL 일부체결 후 broker holding 6
→ PARTIAL_EXIT
→ remaining_qty=6
→ 신규 SELL 자동재전송 금지
→ 현재 주문 terminal 여부와 broker 상태를 먼저 확인
```

잔량 자동 재주문 정책은 별도 검증 후 추가한다.

## 8. 재시작 복구

프로세스 재시작 시:

1. SQLite EXIT_PLAN 로드
2. broker holding/open/fills READ
3. 이미 CLOSED인지 확인
4. EXIT_SUBMITTED인데 broker visibility가 늦으면 재전송 금지
5. PARTIAL_EXIT이면 남은 실제 holding만 기록
6. 대사 완료 전 새 SELL 금지

## 9. V1 개발 순서

```text
V1-1 ExitPlan + validation + SQLite store
V1-2 trigger pure function
V1-3 trigger_once atomic reservation
V1-4 duplicate exit/restart fixture
V1-5 partial/closed reconciliation fixture
V1-6 broker price READ adapter
V1-7 Shadow monitor — NO ORDER
V1-8 broker SELL adapter 연결
V1-9 1주 LIVE_SMALL 자동청산
```

현재 단계에서는 V1-1~V1-5까지만 진행하고 **실주문 코드는 추가하지 않는다.**
