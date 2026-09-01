# 3.5단계 자금흐름 — 기존 Google Cloud 재사용 배포

## 기준

이번 프로젝트는 새 VM이나 새 공인 IP를 만들지 않는다.

하이닉스 추격자에서 이미 검증된 공통 인프라를 그대로 사용한다.

- VM: `kiwoom-server`
- 리전: `asia-northeast3` (서울)
- OS: Ubuntu 24.04 LTS
- Python: 3.12 계열
- 고정 외부 IP 리소스: `kiwoom-static-ip`
- 키움 허용 IP: 기존 Cloud 고정 IP 등록 완료
- 실행관리: systemd 패턴 재사용
- Google Sheet 기록: Apps Script Webhook 패턴 재사용

실제 공인 IP 숫자, App Key/Secret, Webhook Secret, 계좌정보는 GitHub와 Google Sheet에 저장하지 않는다.

## 배포 원칙

하이닉스 추격자 프로세스와 같은 VM을 사용하되 서비스는 분리한다.

예시 구조:

```text
/home/<user>/
  hynix-buyback/                # 기존 서비스 — 수정하지 않음
  trading-system/               # 3.5 프로젝트
    collector/
      kiwoom_collector.py
      .env                      # 비공개, gitignore 대상
      .venv/
```

## 1. 코드 받기

```bash
cd ~
git clone https://github.com/kimfuturelab/trading-system.git
cd trading-system
git checkout feature/market-flow-cloud-collector
```

이미 clone 되어 있다면:

```bash
cd ~/trading-system
git fetch origin
git checkout feature/market-flow-cloud-collector
git pull
```

## 2. Python 환경

```bash
cd ~/trading-system/collector
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

`.env`에는 서버에 이미 보관 중인 Kiwoom App Key/Secret을 안전하게 재사용하고, 3.5 Apps Script Web App URL과 INGEST_SECRET을 입력한다.

## 3. 1회 수동 테스트

장중 여부와 관계없이 수동 테스트는 1회 API 호출을 수행한다.

```bash
cd ~/trading-system/collector
source .venv/bin/activate
python kiwoom_collector.py
```

성공 기준:

1. Kiwoom OAuth 토큰 발급/재사용 성공
2. `ka10032` 응답 수신
3. 최대 100개 종목 정규화
4. Apps Script가 `원시_TOP100`에 기록
5. 콘솔에 `sent N rows` 출력

## 4. 반드시 확인할 값

실데이터 첫날에는 아래를 HTS와 대조한다.

1. 거래대금 TOP100 순위
2. 종목코드/종목명
3. 현재가/등락률
4. `trde_prica` 단위
5. KRX/NXT/SOR 중복 여부
6. 5분 후 직전 스냅샷 순위 변화

특히 `TRADING_VALUE_DIVISOR=100`은 아직 가설이다. HTS 실제 거래대금과 대조하기 전에는 확정값으로 취급하지 않는다.

## 5. systemd 등록

`deploy/market-flow-collector.service.example`을 복사해 Linux 사용자명과 경로를 실제 값으로 바꾼다.

```bash
sudo cp deploy/market-flow-collector.service.example /etc/systemd/system/market-flow-collector.service
sudo nano /etc/systemd/system/market-flow-collector.service
sudo systemctl daemon-reload
sudo systemctl enable market-flow-collector
sudo systemctl start market-flow-collector
sudo systemctl status market-flow-collector
```

로그 확인:

```bash
journalctl -u market-flow-collector -f
```

## 장중 호출 제한

`--loop` 모드는 KST 기준 평일 `COLLECT_START_HHMM`~`COLLECT_END_HHMM` 안에서만 Kiwoom을 호출한다.

기본값:

```text
COLLECT_START_HHMM=0855
COLLECT_END_HHMM=1535
POLL_SECONDS=300
```

Cloud VM의 OS timezone이 UTC여도 Python은 `Asia/Seoul`을 명시적으로 사용한다.

한국 공휴일 자동 판정은 아직 넣지 않았다. 1차 운영에서는 평일 시간창만 적용하고, 필요 시 거래일 캘린더를 후속 추가한다.

## 다음 단계

TOP100이 2~3회 정상 수집된 뒤에만 다음을 붙인다.

1. `ka90001` 테마 그룹
2. `ka90002` 테마 구성종목
3. 복수 등록테마 → 오늘 대표테마 1개
4. 테마 거래대금 점유율
5. Δ5/15/30/60
6. 시장 무게추
7. 주도테마 생애주기
8. 주도테마 캘린더

4단계 후보 자동전달은 현재 프로젝트 범위에서 제외한다.
