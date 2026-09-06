from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


@dataclass(frozen=True)
class MaterialEvidence:
    material_data_ok: str
    material_score: int | None
    material_direction: str
    material_reason_snapshot: str
    as_of_time: str
    evidence_ids: str
    snapshot_status: str
    snapshot_id: str

    @property
    def executable(self) -> bool:
        return self.material_data_ok == "OK"


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def load_material_runtime() -> tuple[str, str]:
    base_dir = Path(__file__).resolve().parent
    load_dotenv(base_dir / ".env")

    explicit_env = os.getenv("MATERIAL_ENV_FILE", "").strip()
    if explicit_env:
        env_path = Path(explicit_env).expanduser()
        if env_path.exists():
            load_dotenv(env_path, override=True)
    else:
        shared_env = Path.home() / "technical-position.env"
        if shared_env.exists():
            load_dotenv(shared_env, override=True)

    webhook_url = _env_first("MATERIAL_WEBHOOK_URL", "TECH_POSITION_WEBHOOK_URL")
    ingest_secret = _env_first("MATERIAL_INGEST_SECRET", "TECH_POSITION_INGEST_SECRET")

    missing = []
    if not webhook_url:
        missing.append("MATERIAL_WEBHOOK_URL or TECH_POSITION_WEBHOOK_URL")
    if not ingest_secret:
        missing.append("MATERIAL_INGEST_SECRET or TECH_POSITION_INGEST_SECRET")
    if missing:
        raise RuntimeError("Missing required environment values: " + ", ".join(missing))

    return webhook_url, ingest_secret


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid MATERIAL_SCORE value: {value!r}") from exc
    return number


def _validate_material(data: dict[str, Any], *, allow_pending: bool) -> MaterialEvidence:
    evidence = MaterialEvidence(
        material_data_ok=str(data.get("material_data_ok", "")).strip(),
        material_score=_int_or_none(data.get("material_score")),
        material_direction=str(data.get("material_direction", "")).strip(),
        material_reason_snapshot=str(data.get("material_reason_snapshot", "")).strip(),
        as_of_time=str(data.get("as_of_time", "")).strip(),
        evidence_ids=str(data.get("evidence_ids", "")).strip(),
        snapshot_status=str(data.get("snapshot_status", "")).strip(),
        snapshot_id=str(data.get("snapshot_id", "")).strip(),
    )

    if not evidence.material_data_ok:
        raise RuntimeError("MATERIAL_DATA_OK is blank")
    if not evidence.as_of_time:
        raise RuntimeError("AS_OF_TIME is blank")

    if evidence.material_data_ok != "OK":
        if allow_pending:
            return evidence
        raise RuntimeError(
            f"MATERIAL Fail Closed: MATERIAL_DATA_OK={evidence.material_data_ok}"
        )

    if evidence.material_score is None or not -3 <= evidence.material_score <= 3:
        raise RuntimeError(f"MATERIAL_SCORE out of range: {evidence.material_score}")

    expected_direction = (
        "POSITIVE"
        if evidence.material_score > 0
        else "NEGATIVE"
        if evidence.material_score < 0
        else "NEUTRAL"
    )
    if evidence.material_direction != expected_direction:
        raise RuntimeError(
            "MATERIAL score/direction mismatch: "
            f"{evidence.material_score}/{evidence.material_direction}"
        )

    if not evidence.material_reason_snapshot:
        raise RuntimeError("MATERIAL_REASON_SNAPSHOT is blank")

    return evidence


def fetch_material_evidence(
    *,
    consumer: str = "trading-view-python",
    allow_pending: bool = False,
    timeout_seconds: int = 30,
) -> MaterialEvidence:
    webhook_url, ingest_secret = load_material_runtime()

    payload = {
        "secret": ingest_secret,
        "type": "material_read",
        "consumer": consumer,
    }

    response = requests.post(webhook_url, json=payload, timeout=timeout_seconds)
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Apps Script returned non-JSON: {response.text[:500]}"
        ) from exc

    if not data.get("ok"):
        raise RuntimeError(f"Apps Script material_read failed: {data}")

    return _validate_material(data, allow_pending=allow_pending)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read MATERIAL evidence through the central Apps Script webhook."
    )
    parser.add_argument("--consumer", default="material-probe")
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="Return PENDING evidence instead of raising Fail Closed.",
    )
    args = parser.parse_args()

    evidence = fetch_material_evidence(
        consumer=args.consumer,
        allow_pending=args.allow_pending,
    )
    print(json.dumps(asdict(evidence), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
