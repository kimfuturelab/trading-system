# SK하이닉스 자사주 추격자 V1

2026-08-27 Vercel 운영 프로젝트 소실/조회 불가 상태에서 복구한 V1 운영 소스입니다.

## 복구 기준
- 2026-08-24 보관된 `hynix-buyback-dashboard-ui.zip`의 V1 UI 원본
- 2026-08-24 14:55 LIVE 정상 화면 PDF
- `V1_자사주 매입 추격자` Google Sheet의 `키움API_설치` 탭에 보존된 공개 읽기 API 스키마
- 동일 시트 `시트2`에 보존된 정상 Apps Script 배포 URL

## 운영 구조
Google Cloud VM → Kiwoom REST API → Google Sheet → Apps Script public read API → Vercel `/api/live` → dashboard

- 브라우저는 `/api/live`만 호출합니다.
- 화면은 30초마다 갱신합니다.
- `api/live.js`는 Apps Script ContentService의 리다이렉트를 따라가 JSON을 전달합니다.
- 공개 화면 수치는 지정 중개사(SK증권) 거래원 흐름을 이용한 장중 추정치이며 공식 자사주 체결량이 아닙니다.

## 복구 원칙
이 디렉터리를 V1 웹 소스의 Source of Truth로 사용합니다. 향후 Vercel 프로젝트가 사라져도 이 GitHub 소스로 재배포합니다.
