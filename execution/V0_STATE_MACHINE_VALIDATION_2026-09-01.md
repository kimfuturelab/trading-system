# V0 상태기계 / Broker Reconciliation 검증 기록 — 2026-09-01

## 목적

수동 CLI 실계좌 1주 왕복 성공 후, Python 주문엔진이 **주문 접수와 실제 체결을 분리해서 인식하고 미체결/부분체결 상태에서 다음 주문을 차단할 수 있는지** 검증한다.

## 서버 검증 환경

- VM: `kiwoom-server`
- branch: `feature/trading-execution-v0`
- broker adapter: `execution/broker_cli.py`
- engine: `execution/v0_engine.py`
- state: SQLite `StateStore`

## 1. 코드 컴파일

```bash
python3 -m py_compile execution/*.py
```

결과: PASS

## 2. fixture 상태기계 테스트

```bash
python3 execution/test_reconciliation_state_machine.py
```

결과:

```text
[PASS] SUBMITTED -> FILLED from broker fill; intent becomes terminal
[PASS] SUBMITTED -> OPEN when broker reports unfilled order; remains blocking
[SAFE] fixture-only test completed; no Kiwoom API and no order call occurred
```

### 확인된 상태 규칙

```text
RESERVED
  -> SUBMITTED
      -> FILLED        # 전량체결, order intent terminal
      -> OPEN          # 미체결, 다음 write 차단
      -> PARTIAL       # 부분체결, 다음 write 차단
      -> AWAITING_BROKER_VISIBILITY
                        # 주문번호는 받았지만 broker 조회에 아직 안 보임. 절대 재전송 금지
```

## 3. 실제 broker READ 결과

```text
broker_account: ******3211
orderable_cash: 240,761
estimated_deposit: 500,011
holding_count: 0
broker_open_order_count: 0
broker_fill_row_count: 2
local_open_intent_count: 0
```

`ALLOWED_ACCOUNT_NOT_SET` 하나 때문에 reconciliation은 의도적으로 BLOCK 상태였다.

이는 오류가 아니라 **실주문용 private execution env를 아직 구성하지 않았기 때문에 정상적인 Fail Closed**이다.

## 4. 오늘 실제 체결 truth

당일 broker order/fill status에서 다음 두 건이 확인됐다.

### BUY

- 주문번호: `0013510`
- 종목: 삼성전자 `005930`
- 주문유형: 현금매수 / 시장가
- 주문수량: 1
- 체결수량: 1
- 체결단가: 259,250원
- 체결번호: `0156137`
- 체결시간: 11:53:44
- 통신구분: REST API

### SELL

- 주문번호: `0013540`
- 종목: 삼성전자 `005930`
- 주문유형: 현금매도 / 시장가
- 주문수량: 1
- 체결수량: 1
- 체결단가: 259,000원
- 체결번호: `0156559`
- 체결시간: 11:55:33
- 통신구분: REST API

최종 holdings는 빈 배열이며 보유수량 0으로 확인됐다.

## 5. 시행착오에서 확정된 운영 노하우

1. `주문 접수 성공(return_code=0)`은 체결 성공이 아니다.
2. 주문번호를 받은 뒤 `list-open`, `list-fills`, account order/fill status와 holdings를 다시 읽어야 한다.
3. 주문이 조회에 바로 안 보이면 propagation lag일 수 있다. 같은 주문을 다시 보내지 않는다.
4. `예수금`과 `주문가능금액`은 다르다. 신규매수 한도는 broker가 제공하는 `주문가능금액`을 사용한다.
5. 실제 계좌번호 전체는 GitHub에 저장하지 않는다. private env에서만 관리하고 broker redaction suffix와 대사한다.
6. broker open order가 하나라도 있으면 신규 write를 막는다.
7. local non-terminal intent가 하나라도 있으면 신규 write를 막는다.
8. 부분체결은 완료가 아니다. 남은 미체결을 해소하기 전 다음 단계로 넘어가지 않는다.
9. broker가 최종 truth이며 SQLite는 중복주문 방지/복구를 위한 로컬 상태다.
10. 실계좌 write 재시험 전에 demo/mock 전체 왕복을 먼저 자동화해 검증한다.

## 6. 다음 단계

실계좌와 분리된 demo 전용 자동 왕복 오케스트레이터를 만든다.

목표:

```text
DEMO preflight
-> BUY 1 share
-> broker fill 확인
-> holdings +1 확인
-> SELL 1 share
-> broker fill 확인
-> holdings 0 확인
-> local intent terminal 확인
```

이 demo 경로가 통과하기 전에는 Python 실계좌 자동 왕복으로 승격하지 않는다.
