# V0 실계좌 1주 왕복 검증 기록 — 2026-09-01

## 결론

키움 REST API + GCP `kiwoom-server` + `kiwoomcli` 경로에서 **국내주식 1주 시장가 매수 → 실제 체결/보유 확인 → 1주 시장가 매도 → 보유 0 확인**까지 수동 CLI로 실계좌 왕복 검증에 성공했다.

이 문서는 성공 결과뿐 아니라, 같은 테스트를 다시 할 때 반복하지 말아야 할 시행착오와 안전 원칙을 남기는 기록이다.

> 보안 원칙: App Key/Secret, 전체 계좌번호, 실제 주문번호는 저장소에 기록하지 않는다.

---

## 1. 검증 환경

- VM: `kiwoom-server`
- OS: Ubuntu 24.04 LTS
- Repo: `kimfuturelab/trading-system`
- Branch: `feature/trading-execution-v0`
- V0 코드: `execution/`
- 인증 env: `~/api-read-v2.env`
- Kiwoom mode: `real`
- CLI: `kiwoomcli`
- 테스트 종목: 삼성전자 `005930`
- 테스트 수량: 1주
- 주문 유형: KRX 시장가

기존 `hynix` / `market-flow` 서비스는 수정하지 않았다.

---

## 2. 서버 준비 과정과 첫 시행착오

### 시행착오 A — `~/trading-system` 디렉터리가 없었음

처음 아래 명령을 바로 실행했으나:

```bash
cd ~/trading-system
git fetch origin
git switch feature/trading-execution-v0
python3 execution/v0_probe.py doctor
python3 execution/v0_probe.py state-test
```

서버에 repo가 아직 clone되어 있지 않아 `No such file or directory`, `not a git repository`가 연쇄 발생했다.

### 해결

서버에 V0 브랜치를 새로 clone했다.

```bash
cd ~
git clone -b feature/trading-execution-v0 \
  https://github.com/kimfuturelab/trading-system.git \
  trading-system
cd ~/trading-system
```

### 노하우

- 새 VM/새 경로에서는 `cd` 전에 repo 존재 여부부터 확인한다.
- 기존 collector 경로를 추측해서 수정하지 않는다.
- V0는 독립 repo checkout/디렉터리에서 시작하는 편이 안전하다.

---

## 3. V0 안전 점검 결과

실행:

```bash
python3 execution/v0_probe.py doctor
python3 execution/v0_probe.py state-test
```

확인 결과:

- `KIWOOM_MODE=real`
- `APP_KEY=SET`
- `APP_SECRET=SET`
- `kiwoomcli` 실행 가능
- `AUTO_MODE=OFF`
- `KILL_SWITCH=ON`
- `ALLOW_LIVE_ORDERS=NO`
- `MAX_ORDER_QTY=1`
- SQLite 첫 intent 저장 성공
- 동일 `intent_id` 중복 저장 차단 성공

### 노하우

**실주문 기능을 붙이기 전에 기본 차단 상태가 정상인지 먼저 증명한다.**

V0의 정상 출발점은 주문 가능 상태가 아니라 **주문 불가능 상태**다.

---

## 4. CLI 명령 구조 확인 과정

### 시행착오 B — `account`가 아니라 `accounts`

잘못 시도:

```bash
kiwoomcli domestic account --help
```

정확한 명령군:

```bash
kiwoomcli domestic accounts --help
```

주요 계좌 READ 명령:

- `accounts list` — 계좌번호 목록
- `accounts cash` — 예수금/주문가능금액
- `accounts holdings` — 보유종목/수량
- `accounts order-fill-status` — 주문/체결 상태
- `accounts today-status` — 당일 계좌현황

주요 주문 명령:

- `orders chance` — 주문/인출 가능금액 조회
- `orders list-open` — 미체결 조회
- `orders list-fills` — 체결 조회
- `orders buy` — 매수
- `orders sell` — 매도

### 노하우

CLI의 하위 명령 이름은 추측하지 말고 `--help`로 서버에 실제 설치된 버전을 기준으로 확인한다.

---

## 5. 계좌 READ 검증

실전 모드를 명시해 READ를 실행했다.

```bash
kiwoomcli domestic accounts list --mode real --named

kiwoomcli domestic accounts holdings \
  --basis total \
  --exchange KRX \
  --mode real \
  --named

kiwoomcli domestic accounts order-fill-status \
  --asset-kind all \
  --market all \
  --side all \
  --fill-status all \
  --exchange ALL \
  --mode real \
  --named

kiwoomcli domestic accounts today-status \
  --mode real \
  --named
```

확인:

- 실계좌 인식 성공
- 초기 보유종목 없음
- 초기 총매입금액 0
- 당일 주문이 없을 때 `관련자료가없습니다` 응답이 나올 수 있음

### 시행착오 C — `auth status`가 profile 미설정으로 실패

`kiwoomcli auth status`는 저장된 계좌 별칭/profile이 없어서 실패했다.

하지만 `--mode real`을 명시한 계좌 READ는 정상 동작했다.

### 결정

V0 검증 중 기존 인증구조를 불필요하게 바꾸지 않는다.

- `kiwoomcli setup` / `auth login`을 즉흥적으로 실행하지 않는다.
- 현재 검증된 `~/api-read-v2.env` + `--mode real` 경로를 유지한다.

---

## 6. 주문가능금액 확인 과정

### 시행착오 D — `accounts cash`에 필수 옵션 누락

처음에는 `--cash-basis`를 빼서 CLI validation error가 발생했다.

정확한 명령:

```bash
kiwoomcli domestic accounts cash \
  --cash-basis normal \
  --mode real \
  --named

kiwoomcli domestic accounts cash \
  --cash-basis estimated \
  --mode real \
  --named
```

초기에는 주문가능금액이 사실상 없는 상태임을 확인했다.

테스트 자금 입금 후 다시 조회하여 **예수금·출금가능금액·주문가능금액이 정상 반영된 것**을 확인한 뒤 주문 단계로 진행했다.

### 노하우

잔고가 비어 있다고 바로 주문을 보내지 않는다.

**주문 전 `cash` 또는 `orders chance`로 실제 주문가능금액부터 확인한다.**

---

## 7. 실제 주문 전 미전송 Preview 검증

`kiwoomcli`의 중요한 안전기능:

> `orders buy/sell`은 `--confirm`이 없으면 실제 주문 API를 호출하지 않고 미전송 주문 확인만 출력한다.

실제 매수 전 사용한 Preview:

```bash
kiwoomcli domestic orders buy \
  --exchange KRX \
  --code 005930 \
  --qty 1 \
  --order-type market \
  --mode real \
  --named
```

Preview에서 확인한 항목:

- 매수
- KRX
- `005930`
- 1주
- 시장가
- 실제 미전송

### 노하우

실주문 직전에는 항상 동일 주문을 **`--confirm` 없이 먼저 출력**하고 사람이 종목/수량/방향/시장/주문유형을 확인한다.

---

## 8. 실전 1주 매수

실주문은 Preview와 동일한 명령에 `--confirm`만 추가했다.

```bash
kiwoomcli domestic orders buy \
  --exchange KRX \
  --code 005930 \
  --qty 1 \
  --order-type market \
  --mode real \
  --named \
  --confirm
```

결과:

- `return_code=0`
- KRX 매수주문 접수 성공
- 키움 모바일 계좌에서 삼성전자 1주 실제 보유 확인

### 핵심 구분

`return_code=0` + 주문번호 수신은 **주문 접수 성공**이지 체결 확정 자체는 아니다.

실전 엔진에서는 반드시 다음을 분리한다.

```text
ORDER_SUBMITTED
→ FILLED/PARTIAL/OPEN 확인
→ holdings 대사
```

---

## 9. 실전 1주 매도 및 청산

실전 매도:

```bash
kiwoomcli domestic orders sell \
  --exchange KRX \
  --code 005930 \
  --qty 1 \
  --order-type market \
  --mode real \
  --named \
  --confirm
```

결과:

- `return_code=0`
- 매도주문 접수 성공
- 모바일 계좌에서 최종 보유종목 없음 확인
- 총매입 0, 포지션 0 확인

따라서 수동 CLI 기준 V0 핵심 왕복은 성공했다.

---

## 10. 오늘 검증된 것 / 아직 검증되지 않은 것

### 검증 완료

- GCP 서버에서 Kiwoom 인증 env 사용
- `kiwoomcli` 실전 READ
- 계좌/현금/보유 READ
- 매수 Preview
- 실전 1주 매수 접수
- 실제 보유 1주 확인
- 실전 1주 매도 접수
- 최종 보유 0 확인
- SQLite 중복 intent 차단
- 기본 Fail Closed 설정

### 아직 미검증

- Python 코드가 직접 위 왕복을 자동 수행
- Python의 미체결/체결 polling
- 부분체결
- 주문거절
- 네트워크 timeout 후 재조회
- 주문 응답 유실 상황에서 중복주문 방지
- 재시작 후 broker ↔ SQLite reconciliation
- 취소/정정
- Kill Switch를 실시간으로 내렸을 때 동작
- 자동 손절/익절/타임컷
- 0·1·2·3·3.5·4단계와 execution 연결

---

## 11. V0에서 반드시 유지할 안전 규칙

1. `AUTO_MODE=OFF` 기본.
2. `KILL_SWITCH=ON` 기본.
3. 실주문은 `LIVE_SMALL` + `ALLOW_LIVE_ORDERS=YES` + 당일 ARM이 동시에 맞아야 함.
4. V0 최대 수량 1주.
5. `intent_id`를 broker 호출 **전에** 저장.
6. 동일 `intent_id` 재전송 금지.
7. 주문 응답이 끊겨도 동일 주문을 즉시 재전송하지 않음.
8. 응답 유실 시 먼저 미체결/체결/잔고 조회.
9. 주문번호 수신 ≠ 체결 완료.
10. 로컬 DB ≠ 진실. Kiwoom 실제 계좌 상태가 최종 truth.
11. READ/reconciliation 실패 시 신규 WRITE 금지.
12. 기존 Hynix/3.5 서비스와 execution 서비스 분리.
13. App Key 공유 429 가능성이 있으므로 무한/공격적 retry 금지.

---

## 12. 다음 구현 순서

수동 CLI 왕복을 그대로 Python으로 옮긴다.

```text
1. broker CLI adapter
2. account/cash/holdings/open/fills READ
3. startup reconciliation
4. intent reserve
5. Safety Gate
6. buy/sell preview
7. live write guard
8. 주문번호 저장
9. fill polling
10. holdings 대사
11. 종료/청산 상태 기록
12. 재시작 복구 테스트
```

**다음 승격 조건:** Python이 동일한 1주 왕복을 자동으로 수행하기 전에, READ/reconciliation과 DRY RUN이 먼저 통과해야 한다.
