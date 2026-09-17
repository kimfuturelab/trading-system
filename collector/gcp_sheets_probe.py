from __future__ import annotations

import json
from urllib.parse import quote

import requests

METADATA_ROOT = "http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADERS = {"Metadata-Flavor": "Google"}
SPREADSHEET_ID = "1FTmoK13a9COaIyUju89w0GGWzR6W7CuPORJLmhbQ6Wg"
PROBE_RANGE = "'PY_시장레짐_SHADOW'!A1:S6"


def metadata_text(path: str) -> str:
    response = requests.get(
        f"{METADATA_ROOT}/{path.lstrip('/')}",
        headers=METADATA_HEADERS,
        timeout=5,
    )
    response.raise_for_status()
    return response.text.strip()


def metadata_json(path: str) -> dict:
    response = requests.get(
        f"{METADATA_ROOT}/{path.lstrip('/')}",
        headers=METADATA_HEADERS,
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    try:
        email = metadata_text("instance/service-accounts/default/email")
        scopes_raw = metadata_text("instance/service-accounts/default/scopes")
        token_payload = metadata_json("instance/service-accounts/default/token")
    except Exception as exc:
        print(f"METADATA = FAIL | {exc}")
        return 2

    scopes = [line.strip() for line in scopes_raw.splitlines() if line.strip()]
    token = str(token_payload.get("access_token") or "").strip()
    if not token:
        print("METADATA = FAIL | access token missing")
        return 2

    print("METADATA = PASS")
    print(f"SERVICE_ACCOUNT = {email}")
    print("SCOPES =")
    for scope in scopes:
        print(f"  - {scope}")

    encoded_range = quote(PROBE_RANGE, safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/"
        f"{SPREADSHEET_ID}/values/{encoded_range}"
    )
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )

    if response.status_code == 200:
        payload = response.json()
        rows = len(payload.get("values") or [])
        print(f"SHEETS_READ = PASS | rows={rows}")
        print("SHEETS_WRITE = NOT_TESTED")
        return 0

    print(f"SHEETS_READ = FAIL | HTTP={response.status_code}")
    try:
        error = response.json()
        message = (
            error.get("error", {}).get("message")
            if isinstance(error, dict)
            else ""
        )
    except ValueError:
        message = response.text[:300]
    print(f"DETAIL = {message}")
    print("SHEETS_WRITE = NOT_TESTED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
