# SK하이닉스 자사주 추격자 V1

2026-08-27 Vercel 운영 프로젝트/배포 조회 불가 상태에서 복구한 V1 운영 소스입니다.

## 복구 근거
- 2026-08-24 보관된 V1 UI 원본 ZIP
- 2026-08-24 14:55 LIVE 정상 화면 PDF
- `V1_자사주 매입 추격자` Google Sheet의 공개 읽기 API 스키마
- 같은 시트에 보존된 정확한 Apps Script 배포 URL

## 운영 구조
Google Cloud VM → Kiwoom REST API → Google Sheet → Apps Script public read API(JSONP) → Vercel static dashboard

브라우저는 Apps Script 공개 읽기 API를 JSONP로 30초마다 조회합니다. 별도 Vercel Function/Rewrite를 두지 않아 중계 장애 지점을 제거했습니다. 공개 API에는 시장/계산 데이터만 포함되며 Kiwoom App Key/Secret이나 webhook secret은 포함하지 않습니다.

## Source of Truth
이 GitHub 디렉터리가 V1 웹 소스의 Source of Truth입니다. 향후 Vercel 프로젝트가 사라져도 이 소스로 재배포합니다.
