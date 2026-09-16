from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
BASE_DIR = Path(__file__).resolve().parent
TOKEN_CACHE = Path.home() / ".cache" / "trading-system" / "kiwoom_token.json"
STATE_PATH = BASE_DIR / ".atmosphere_v1_state.json"
MODEL_PATH = BASE_DIR / ".atmosphere_v1_model.json"

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/NQ=F"


def load_runtime_env() -> None:
    # Preserve the proven server priority: stage3-supply.env before api-read-v2.env.
    for p in (
        Path.home() / "stage3-supply.env",
        Path.home() / "api-read-v2.env",
        BASE_DIR / ".env",
    ):
        if p.exists():
            load_dotenv(p, override=False)


def fnum(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def now_kst() -> datetime:
    return datetime.now(KST)


def iso_kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Settings:
    app_key: str
    app_secret: str
    base_url: str
    webhook_url: str
    ingest_secret: str
    poll_seconds: int = 20
    lock_hhmm: str = "08:50"
    open_hhmm: str = "09:00"
    checkpoint_hhmm: str = "09:30"
    close_hhmm: str = "15:35"
    skhy_exchange: str = "ND"
    skhy_code: str = "SKHY"
    hynix_code: str = "000660"

    @classmethod
    def from_env(cls) -> "Settings":
        load_runtime_env()
        webhook = (
            os.getenv("ATMOSPHERE_WEBHOOK_URL", "").strip()
            or os.getenv("SHEETS_WEBHOOK_URL", "").strip()
        )
        secret = (
            os.getenv("ATMOSPHERE_INGEST_SECRET", "").strip()
            or os.getenv("INGEST_SECRET", "").strip()
        )
        s = cls(
            app_key=os.getenv("KIWOOM_APP_KEY", os.getenv("APP_KEY", "")).strip(),
            app_secret=os.getenv("KIWOOM_APP_SECRET", os.getenv("APP_SECRET", "")).strip(),
            base_url=os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com").rstrip("/"),
            webhook_url=webhook,
            ingest_secret=secret,
            poll_seconds=int(os.getenv("ATMOSPHERE_POLL_SECONDS", "20")),
            lock_hhmm=os.getenv("ATMOSPHERE_LOCK_TIME", "08:50"),
            open_hhmm=os.getenv("ATMOSPHERE_OPEN_TIME", "09:00"),
            checkpoint_hhmm=os.getenv("ATMOSPHERE_CHECKPOINT_TIME", "09:30"),
            close_hhmm=os.getenv("ATMOSPHERE_CLOSE_TIME", "15:35"),
            skhy_exchange=os.getenv("ATMOSPHERE_SKHY_EXCHANGE", "ND"),
            skhy_code=os.getenv("ATMOSPHERE_SKHY_CODE", "SKHY"),
            hynix_code=os.getenv("ATMOSPHERE_HYNIX_CODE", "000660"),
        )
        missing = [
            name for name, val in (
                ("KIWOOM_APP_KEY/APP_KEY", s.app_key),
                ("KIWOOM_APP_SECRET/APP_SECRET", s.app_secret),
                ("SHEETS_WEBHOOK_URL", s.webhook_url),
                ("INGEST_SECRET", s.ingest_secret),
            )
            if not val
        ]
        if missing:
            raise RuntimeError("Missing env: " + ", ".join(missing))
        return s


class KiwoomClient:
    def __init__(self, s: Settings):
        self.s = s
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json;charset=UTF-8"})

    def _cached_token(self) -> str | None:
        if not TOKEN_CACHE.exists():
            return None
        try:
            data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            token = str(data.get("token") or "")
            expiry = datetime.strptime(str(data.get("expires_at")), "%Y%m%d%H%M%S").replace(tzinfo=KST)
            if token and now_kst() < expiry - timedelta(minutes=10):
                return token
        except Exception:
            pass
        return None

    def _invalidate(self, token: str | None = None) -> None:
        try:
            if not TOKEN_CACHE.exists():
                return
            if token:
                try:
                    cur = json.loads(TOKEN_CACHE.read_text(encoding="utf-8")).get("token")
                except Exception:
                    cur = None
                if cur and str(cur) != str(token):
                    return
            TOKEN_CACHE.unlink(missing_ok=True)
        except OSError:
            pass

    def token(self) -> str:
        cached = self._cached_token()
        if cached:
            return cached
        r = self.session.post(
            f"{self.s.base_url}/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "appkey": self.s.app_key,
                "secretkey": self.s.app_secret,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if int(data.get("return_code", -1)) != 0:
            raise RuntimeError(f"Kiwoom token error: {data}")
        token = data.get("token")
        expires = data.get("expires_dt")
        if not token or not expires:
            raise RuntimeError(f"Unexpected token payload: {data}")
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TOKEN_CACHE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"token": token, "expires_at": expires}), encoding="utf-8")
        os.replace(tmp, TOKEN_CACHE)
        return str(token)

    def post(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        last = None
        for attempt in range(2):
            token = self.token()
            r = self.session.post(
                f"{self.s.base_url}{path}",
                headers={"authorization": f"Bearer {token}", "api-id": api_id},
                json=body,
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            last = data
            if int(data.get("return_code", -1)) == 0:
                return data
            msg = str(data.get("return_msg", ""))
            if attempt == 0 and ("8005" in msg or "Token이 유효하지 않습니다" in msg):
                self._invalidate(token)
                continue
            raise RuntimeError(f"{api_id} error: {data}")
        raise RuntimeError(f"{api_id} failed: {last}")


def parse_hhmm(text: str) -> dtime:
    h, m = text.split(":", 1)
    return dtime(int(h), int(m))


def local_dt(d: date, hhmm: str) -> datetime:
    t = parse_hhmm(hhmm)
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=KST)


def fetch_yahoo_nq(range_text: str = "60d", interval: str = "5m") -> list[tuple[datetime, float]]:
    r = requests.get(
        YAHOO_CHART,
        params={
            "range": range_text,
            "interval": interval,
            "includePrePost": "true",
            "events": "div,splits",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo NQ empty: {payload}")
    ts = result.get("timestamp") or []
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    out: list[tuple[datetime, float]] = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        out.append((datetime.fromtimestamp(int(t), tz=ZoneInfo("UTC")), float(c)))
    if not out:
        raise RuntimeError("Yahoo NQ returned no usable close points")
    return out


def build_nq_maps(points: list[tuple[datetime, float]]) -> tuple[dict[date, tuple[datetime, float]], dict[date, tuple[datetime, float]]]:
    us_close: dict[date, tuple[datetime, float]] = {}
    lock: dict[date, tuple[datetime, float]] = {}

    for ts_utc, close in points:
        ny = ts_utc.astimezone(NY)
        kst = ts_utc.astimezone(KST)

        # Pick the bar nearest 16:00 New York, within 20 minutes.
        target_ny = datetime(ny.year, ny.month, ny.day, 16, 0, tzinfo=NY)
        if abs((ny - target_ny).total_seconds()) <= 20 * 60:
            old = us_close.get(ny.date())
            if old is None or abs((ny - target_ny).total_seconds()) < abs((old[0].astimezone(NY) - target_ny).total_seconds()):
                us_close[ny.date()] = (ts_utc, close)

        # Pick latest bar at or before 08:50 KST, but not older than 45 minutes.
        target_kst = datetime(kst.year, kst.month, kst.day, 8, 50, tzinfo=KST)
        delta = (target_kst - kst).total_seconds()
        if 0 <= delta <= 45 * 60:
            old = lock.get(kst.date())
            if old is None or ts_utc > old[0]:
                lock[kst.date()] = (ts_utc, close)

    return us_close, lock


def fetch_us_daily(client: KiwoomClient, s: Settings, wanted: int = 90) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    base = now_kst().astimezone(NY).date()
    for _ in range(8):
        data = client.post(
            "usa20590",
            "/api/us/mrkcond",
            {"stex_tp": s.skhy_exchange, "stk_cd": s.skhy_code, "base_dt": base.strftime("%Y%m%d")},
        )
        chunk = data.get("result_list") or []
        oldest: date | None = None
        for r in chunk:
            d = str(r.get("dt") or "").strip()
            if len(d) != 8:
                continue
            rows[d] = r
            dd = datetime.strptime(d, "%Y%m%d").date()
            oldest = dd if oldest is None or dd < oldest else oldest
        if len(rows) >= wanted or oldest is None:
            break
        base = oldest - timedelta(days=1)
        time.sleep(0.25)
    return [rows[k] for k in sorted(rows, reverse=True)]


def fetch_kr_daily(client: KiwoomClient, s: Settings, wanted: int = 90) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    base = now_kst().date()
    for _ in range(8):
        data = client.post(
            "ka10086",
            "/api/dostk/mrkcond",
            {"stk_cd": s.hynix_code, "qry_dt": base.strftime("%Y%m%d"), "indc_tp": "0"},
        )
        chunk = data.get("daly_stkpc") or []
        oldest: date | None = None
        for r in chunk:
            d = str(r.get("date") or "").strip()
            if len(d) != 8:
                continue
            rows[d] = r
            dd = datetime.strptime(d, "%Y%m%d").date()
            oldest = dd if oldest is None or dd < oldest else oldest
        if len(rows) >= wanted or oldest is None:
            break
        base = oldest - timedelta(days=1)
        time.sleep(0.25)
    return [rows[k] for k in sorted(rows, reverse=True)]


def current_hynix_price(client: KiwoomClient, s: Settings) -> float | None:
    data = client.post("ka10003", "/api/dostk/stkinfo", {"stk_cd": s.hynix_code})
    rows = data.get("cntr_infr") or []
    if not rows:
        return None
    return abs(fnum(rows[0].get("cur_prc")) or 0) or None


def percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("empty percentile")
    vals = sorted(values)
    idx = max(0, min(len(vals) - 1, math.ceil(q * len(vals)) - 1))
    return vals[idx]


def solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    aug = [list(a[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise RuntimeError("Singular regression matrix")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        aug[col] = [x / div for x in aug[col]]
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if abs(factor) < 1e-15:
                continue
            aug[r] = [aug[r][c] - factor * aug[col][c] for c in range(n + 1)]
    return [aug[i][n] for i in range(n)]


def fit_coeff(rows: list[dict[str, Any]]) -> tuple[list[float], int, int, float, float]:
    usable = [r for r in rows if all(r.get(k) is not None for k in ("nq_us_pct", "adr_rel_pct", "nq_post_pct", "actual_gap_pct"))]
    usable.sort(key=lambda r: r["date"])
    if len(usable) < 20:
        raise RuntimeError(f"Need >=20 usable rows, got {len(usable)}")

    val_n = max(5, min(15, len(usable) // 5))
    train = usable[:-val_n]
    valid = usable[-val_n:]
    if len(train) < 15:
        train, valid = usable, []

    dim = 4
    xtx = [[0.0] * dim for _ in range(dim)]
    xty = [0.0] * dim
    for r in train:
        x = [1.0, float(r["nq_us_pct"]), float(r["adr_rel_pct"]), float(r["nq_post_pct"])]
        y = float(r["actual_gap_pct"])
        for i in range(dim):
            xty[i] += x[i] * y
            for j in range(dim):
                xtx[i][j] += x[i] * x[j]
    for i in range(dim):
        xtx[i][i] += 1e-8

    coef = solve_linear(xtx, xty)

    eval_rows = valid or train
    errors = []
    for r in eval_rows:
        pred = coef[0] + coef[1] * float(r["nq_us_pct"]) + coef[2] * float(r["adr_rel_pct"]) + coef[3] * float(r["nq_post_pct"])
        errors.append(abs(float(r["actual_gap_pct"]) - pred))
    band = percentile(errors, 0.80)
    mae = sum(errors) / len(errors)
    return coef, len(train), len(valid), band, mae


def build_backfill(client: KiwoomClient, s: Settings, wanted: int = 60) -> list[dict[str, Any]]:
    kr_rows = fetch_kr_daily(client, s, wanted + 30)
    us_rows = fetch_us_daily(client, s, wanted + 30)
    points = fetch_yahoo_nq("60d", "5m")
    us_close, locks = build_nq_maps(points)

    us_by_date = {
        datetime.strptime(str(r.get("dt")), "%Y%m%d").date(): r
        for r in us_rows if str(r.get("dt") or "").isdigit() and len(str(r.get("dt"))) == 8
    }

    kr_sorted = sorted(
        [
            (datetime.strptime(str(r.get("date")), "%Y%m%d").date(), r)
            for r in kr_rows if str(r.get("date") or "").isdigit() and len(str(r.get("date"))) == 8
        ],
        key=lambda x: x[0],
    )

    close_dates = sorted(us_close)
    out: list[dict[str, Any]] = []
    for i in range(1, len(kr_sorted)):
        d, row = kr_sorted[i]
        if d not in locks:
            continue
        lock_ts, lock_px = locks[d]

        eligible = [ud for ud in close_dates if us_close[ud][0] < lock_ts]
        if len(eligible) < 2:
            continue
        ud = eligible[-1]
        prev_ud = eligible[-2]
        us_px = us_close[ud][1]
        prev_us_px = us_close[prev_ud][1]
        nq_us = pct(us_px, prev_us_px)
        nq_post = pct(lock_px, us_px)

        sk = us_by_date.get(ud)
        if not sk:
            continue
        adr = fnum(sk.get("flu_rt"))
        if adr is None or nq_us is None or nq_post is None:
            continue

        prev_close = abs(fnum(kr_sorted[i - 1][1].get("close_pric")) or 0) or None
        open_px = abs(fnum(row.get("open_pric")) or 0) or None
        actual = pct(open_px, prev_close)
        if actual is None:
            continue

        out.append({
            "date": d.isoformat(),
            "nq_us_pct": nq_us,
            "skhy_adr_pct": adr,
            "adr_rel_pct": adr - nq_us,
            "nq_post_pct": nq_post,
            "actual_gap_pct": actual,
            "us_date": ud.isoformat(),
            "prev_close": prev_close,
            "open_price": open_px,
        })

    return out[-wanted:]


def model_predict(model: dict[str, Any], nq_us: float, adr_rel: float, nq_post: float, news_post: float = 0.0) -> tuple[float, float, float]:
    center = (
        float(model["alpha"])
        + float(model["beta_nq_us"]) * nq_us
        + float(model["beta_adr_rel"]) * adr_rel
        + float(model["beta_nq_post"]) * nq_post
        + float(model.get("beta_news_post", 0.0)) * news_post
    )
    band = float(model["band80_pctp"])
    return center, center - band, center + band


def atmosphere(actual: float, low: float, high: float) -> str:
    if actual > high:
        return "예상보다 강함"
    if actual < low:
        return "예상보다 약함"
    return "예상범위"


def send(s: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    body = {"secret": s.ingest_secret, "type": "atmosphere", **payload}
    r = requests.post(s.webhook_url, json=body, timeout=30)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError(f"Webhook non-JSON: {r.text[:500]}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"Webhook rejected: {data}")
    return data


def save_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def train_and_backfill(client: KiwoomClient, s: Settings, wanted: int) -> dict[str, Any]:
    rows = build_backfill(client, s, wanted)
    coef, train_n, val_n, band, mae = fit_coeff(rows)
    model = {
        "model_version": "ATM_PRICE_V1",
        "model_status": "ACTIVE",
        "trained_through": rows[-1]["date"],
        "train_n": train_n,
        "validation_n": val_n,
        "alpha": coef[0],
        "beta_nq_us": coef[1],
        "beta_adr_rel": coef[2],
        "beta_nq_post": coef[3],
        "beta_news_post": 0.0,
        "band80_pctp": band,
        "mae_pctp": mae,
    }
    save_json(MODEL_PATH, model)

    enriched = []
    split_at = max(0, len(rows) - val_n)
    for idx, r in enumerate(rows):
        center, low, high = model_predict(model, r["nq_us_pct"], r["adr_rel_pct"], r["nq_post_pct"])
        residual = r["actual_gap_pct"] - center
        enriched.append({
            **r,
            "expected_center_pct": center,
            "expected_low_pct": low,
            "expected_high_pct": high,
            "residual_pctp": residual,
            "atmosphere": atmosphere(r["actual_gap_pct"], low, high),
            "model_version": model["model_version"],
            "split": "VALID" if val_n and idx >= split_at else "TRAIN",
            "data_status": "OK",
            "source": "Kiwoom usa20590 + ka10086 + Yahoo NQ=F 5m",
        })

    send(s, {"mode": "model", "captured_at": iso_kst(now_kst()), **model})
    send(s, {
        "mode": "backfill",
        "captured_at": iso_kst(now_kst()),
        "model_version": model["model_version"],
        "source": "atmosphere_v1.py",
        "rows": enriched,
    })
    return {"model": model, "rows": len(enriched)}


def live_features(client: KiwoomClient, s: Settings, trade_date: date) -> dict[str, Any]:
    points = fetch_yahoo_nq("5d", "5m")
    us_close, locks = build_nq_maps(points)
    lock_dt = local_dt(trade_date, s.lock_hhmm)
    lock = locks.get(trade_date)
    if not lock:
        raise RuntimeError(f"No NQ 08:50 lock bar for {trade_date}")
    lock_ts, lock_px = lock

    eligible = [d for d in sorted(us_close) if us_close[d][0] < lock_ts]
    if len(eligible) < 2:
        raise RuntimeError("Not enough NQ US close bars")
    ud = eligible[-1]
    prev_ud = eligible[-2]
    nq_us = pct(us_close[ud][1], us_close[prev_ud][1])
    nq_post = pct(lock_px, us_close[ud][1])
    if nq_us is None or nq_post is None:
        raise RuntimeError("NQ feature calculation failed")

    us_daily = fetch_us_daily(client, s, 15)
    sk = None
    for r in us_daily:
        if str(r.get("dt") or "") == ud.strftime("%Y%m%d"):
            sk = r
            break
    if not sk:
        raise RuntimeError(f"SKHY daily row missing for {ud}")
    adr = fnum(sk.get("flu_rt"))
    if adr is None:
        raise RuntimeError("SKHY flu_rt missing")

    kr = fetch_kr_daily(client, s, 15)
    kr_by_date = {
        datetime.strptime(str(r.get("date")), "%Y%m%d").date(): r
        for r in kr if str(r.get("date") or "").isdigit() and len(str(r.get("date"))) == 8
    }
    prev_dates = sorted([d for d in kr_by_date if d < trade_date])
    prev_close = None
    if prev_dates:
        prev_close = abs(fnum(kr_by_date[prev_dates[-1]].get("close_pric")) or 0) or None

    return {
        "trade_date": trade_date.isoformat(),
        "captured_at": iso_kst(now_kst()),
        "us_close_kst": iso_kst(us_close[ud][0]),
        "lock_time_kst": iso_kst(lock_dt),
        "nq_us_pct": nq_us,
        "skhy_adr_pct": adr,
        "adr_rel_pct": adr - nq_us,
        "nq_post_pct": nq_post,
        "prev_close": prev_close,
        "us_date": ud.isoformat(),
    }


def domestic_today(client: KiwoomClient, s: Settings, trade_date: date) -> dict[str, Any] | None:
    rows = fetch_kr_daily(client, s, 15)
    by_date = {
        datetime.strptime(str(r.get("date")), "%Y%m%d").date(): r
        for r in rows if str(r.get("date") or "").isdigit() and len(str(r.get("date"))) == 8
    }
    today = by_date.get(trade_date)
    if not today:
        return None
    prev_dates = sorted([d for d in by_date if d < trade_date])
    if not prev_dates:
        return None
    prev_close = abs(fnum(by_date[prev_dates[-1]].get("close_pric")) or 0) or None
    open_px = abs(fnum(today.get("open_pric")) or 0) or None
    close_px = abs(fnum(today.get("close_pric")) or 0) or None
    return {
        "prev_close": prev_close,
        "open_price": open_px,
        "close_price": close_px,
        "actual_gap_pct": pct(open_px, prev_close),
        "close_pct": pct(close_px, prev_close),
    }


def run_cycle(client: KiwoomClient, s: Settings) -> None:
    now = now_kst()
    d = now.date()
    if now.weekday() >= 5:
        print(f"{iso_kst(now)} WEEKEND")
        return

    state = load_json(STATE_PATH)
    if state.get("trade_date") != d.isoformat():
        state = {"trade_date": d.isoformat()}

    lock_dt = local_dt(d, s.lock_hhmm)
    open_dt = local_dt(d, s.open_hhmm)
    checkpoint_dt = local_dt(d, s.checkpoint_hhmm)
    close_dt = local_dt(d, s.close_hhmm)

    features: dict[str, Any] | None = None

    if now >= lock_dt and not state.get("preopen"):
        features = live_features(client, s, d)
        model = load_json(MODEL_PATH)
        payload = {"mode": "preopen", **features}
        if model.get("model_status") == "ACTIVE":
            center, low, high = model_predict(
                model,
                float(features["nq_us_pct"]),
                float(features["adr_rel_pct"]),
                float(features["nq_post_pct"]),
                0.0,
            )
            payload.update({
                "expected_center_pct": center,
                "expected_low_pct": low,
                "expected_high_pct": high,
                "model_version": model["model_version"],
                "status": "OK",
            })
        else:
            payload.update({"model_version": "MODEL_PENDING", "status": "MODEL_PENDING"})
        result = send(s, payload)
        state["preopen"] = True
        save_json(STATE_PATH, state)
        print(f"PREOPEN SENT {d} | {result.get('atmosphere', 'MODEL_PENDING')}")

    if now >= open_dt and not state.get("open"):
        dom = domestic_today(client, s, d)
        if dom and dom.get("open_price") and dom.get("actual_gap_pct") is not None:
            if features is None:
                features = live_features(client, s, d)
            result = send(s, {
                "mode": "open",
                **features,
                **dom,
                "status": "OK",
                "model_version": load_json(MODEL_PATH).get("model_version", "MODEL_PENDING"),
            })
            state["open"] = True
            save_json(STATE_PATH, state)
            print(f"OPEN SENT {d} gap={dom['actual_gap_pct']:.3f}% | {result.get('atmosphere')}")

    if now >= checkpoint_dt and not state.get("checkpoint_0930"):
        dom = domestic_today(client, s, d)
        if dom and dom.get("prev_close"):
            cur = current_hynix_price(client, s)
            p0930 = pct(cur, dom["prev_close"])
            if p0930 is not None:
                if features is None:
                    features = live_features(client, s, d)
                send(s, {
                    "mode": "checkpoint_0930",
                    **features,
                    "pct_0930": p0930,
                    "status": "OK",
                    "model_version": load_json(MODEL_PATH).get("model_version", "MODEL_PENDING"),
                })
                state["checkpoint_0930"] = True
                save_json(STATE_PATH, state)
                print(f"09:30 SENT {d} pct={p0930:.3f}%")

    if now >= close_dt and not state.get("close"):
        dom = domestic_today(client, s, d)
        if dom and dom.get("close_pct") is not None:
            if features is None:
                features = live_features(client, s, d)
            send(s, {
                "mode": "close",
                **features,
                "close_pct": dom["close_pct"],
                "status": "OK",
                "model_version": load_json(MODEL_PATH).get("model_version", "MODEL_PENDING"),
            })
            state["close"] = True
            save_json(STATE_PATH, state)
            print(f"CLOSE SENT {d} pct={dom['close_pct']:.3f}%")

    if all(state.get(k) for k in ("preopen", "open", "checkpoint_0930", "close")):
        print(f"{iso_kst(now)} COMPLETE {d}")
    else:
        print(f"{iso_kst(now)} STATE {json.dumps(state, ensure_ascii=False)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--backfill", type=int, default=0, metavar="DAYS")
    args = parser.parse_args()

    try:
        s = Settings.from_env()
        client = KiwoomClient(s)
        if args.backfill:
            result = train_and_backfill(client, s, args.backfill)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        while True:
            try:
                run_cycle(client, s)
            except KeyboardInterrupt:
                return 0
            except Exception as exc:
                print(f"ATMOSPHERE ERROR: {exc}", file=sys.stderr)
                if not args.loop:
                    return 1
            if not args.loop:
                return 0
            time.sleep(max(10, s.poll_seconds))
    except Exception as exc:
        print(f"CONFIG/FATAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
