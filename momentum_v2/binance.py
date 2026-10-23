"""Minimal Binance USDT-M futures REST client (public endpoints only)."""
from __future__ import annotations

import time
import requests

from .core import Bar
from .config import TF_MS

BASE = "https://fapi.binance.com"
_session = requests.Session()


def _get(path: str, params: dict | None = None, retries: int = 3):
    for i in range(retries):
        try:
            r = _session.get(BASE + path, params=params, timeout=15)
            if r.status_code == 429 or r.status_code == 418:
                time.sleep(int(r.headers.get("Retry-After", 5)))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == retries - 1:
                raise
            time.sleep(1 + i)
    raise RuntimeError(f"Binance GET failed: {path}")


def klines(symbol: str, interval: str, limit: int = 500,
           start_ms: int | None = None, end_ms: int | None = None) -> list[Bar]:
    p = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        p["startTime"] = start_ms
    if end_ms is not None:
        p["endTime"] = end_ms
    return [Bar.from_kline(k) for k in _get("/fapi/v1/klines", p)]


def closed_klines(symbol: str, interval: str, limit: int, now_ms: int) -> list[Bar]:
    """Klines with the still-forming candle removed."""
    bars = klines(symbol, interval, limit)
    ms = TF_MS[interval]
    return [b for b in bars if b.t + ms <= now_ms]


def history_1m(symbol: str, start_ms: int, end_ms: int) -> list[Bar]:
    """Paged download of 1m candles in [start_ms, end_ms)."""
    out: list[Bar] = []
    t = start_ms
    while t < end_ms:
        chunk = klines(symbol, "1m", 1500, start_ms=t, end_ms=end_ms - 1)
        if not chunk:
            break
        out.extend(chunk)
        t = chunk[-1].t + 60_000
        time.sleep(0.15)
    return out


def tradable_alt_perps(majors: set) -> set[str]:
    """USDT-margined crypto perpetuals that are trading, minus majors.
    Excludes stock/commodity/index perps (underlyingType other than COIN)."""
    info = _get("/fapi/v1/exchangeInfo")
    out = set()
    for s in info.get("symbols", []):
        if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT":
            continue
        if s.get("status") != "TRADING":
            continue
        if s.get("underlyingType", "COIN") != "COIN":
            continue
        if s["symbol"] in majors:
            continue
        out.add(s["symbol"])
    return out


def server_time_ms() -> int:
    """Binance's clock — used to decide which candles have closed."""
    return int(_get("/fapi/v1/time")["serverTime"])


def trades_last_minutes(symbol: str, minutes: int, now_ms: int) -> int:
    """Number of trades in the last `minutes` CLOSED 1m candles (kline field 8).
    This is Orion's "TRD (5M)" column."""
    raw = _get("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": minutes + 1})
    done = [k for k in raw if int(k[0]) + 60_000 <= now_ms][-minutes:]
    return sum(int(k[8]) for k in done)


def premium_index(symbol: str) -> tuple[float, int | None]:
    """(last funding rate as a fraction, next funding time ms)."""
    d = _get("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(d.get("lastFundingRate", 0) or 0), d.get("nextFundingTime")


def tickers_24h() -> list[dict]:
    return _get("/fapi/v1/ticker/24hr")
