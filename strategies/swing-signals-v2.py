#!/usr/bin/env python3
"""
Swing Signal Generator v2 — Multi-Timeframe
============================================
Timeframes:
  Daily  → POC filter (direction bias), swing zone identification
  4h     → Swing high/low confirmation, strength scoring
  30m    → Rejection candle detection + volume filter → GET READY / ENTRY signals

Entry logic:
  - Daily POC must confirm direction (close above POC for longs, below for shorts)
  - 4h swing must be confirmed with strength score
  - 30m rejection candle: wick > 1.5x body AND volume > 1.5x avg volume at that level
  - ENTRY: break of rejection candle's high (for longs) / low (for shorts)

Signals:
  GET_READY  → price approaching swing zone, rejection forming, awaiting break confirmation
  BUY/SELL   → rejection candle broken on volume → take position

Run via cron or on-demand.
"""

import json
import urllib.request
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

# ─── CONFIG ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = "401478089"
API_BASE           = "https://api.telegram.org/bot"
STRATEGY_VERSION   = "2.0"
OUTPUT_PATH        = "/home/openclaw/workspace/strategies/swing-signals-v2.json"

# Asset configuration
ASSETS = [
    {"symbol": "AAVEUSDT", "name": "AAVE",  "tick": 0.50},
    {"symbol": "BTCUSDT",  "name": "BTC",   "tick": 10.0},
    {"symbol": "ETHUSDT",  "name": "ETH",   "tick": 0.10},
]

# Strategy parameters
LOOKBACK_DAILY   = 120   # daily candles for POC + swing detection
LOOKBACK_4H      = 400   # 4h candles (~66 days)
LOOKBACK_30M     = 300   # 30m candles (~6 days)
SWING_STRENGTH  = 4     # swing lookback/lookforward in candles
TICK_SIZE_USD    = 1.00  # dollar tick for volume profile bins
REJECT_WICK_MULT = 1.5  # wick must be this many times the body
REJECT_VOL_MULT  = 1.5  # rejection candle vol must exceed this × avg volume
MAX_WAIT_DAYS    = 40   # max days since swing formation before stale
# ─────────────────────────────────────────────────────────────────────────────

# ─── DATA FETCHING ────────────────────────────────────────────────────────────

def fetch_klines(symbol, interval, limit):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return [{
        "ts":    int(d[0]),
        "dt":    datetime.utcfromtimestamp(int(d[0]) / 1000),
        "date":  datetime.utcfromtimestamp(int(d[0]) / 1000).strftime("%Y-%m-%d"),
        "time":  datetime.utcfromtimestamp(int(d[0]) / 1000).strftime("%H:%M"),
        "open":  float(d[1]),
        "high":  float(d[2]),
        "low":   float(d[3]),
        "close": float(d[4]),
        "vol":   float(d[5]),
        "qvol":  float(d[7]) if len(d) > 7 else 0,
        "trades": int(d[8]) if len(d) > 8 else 0,
    } for d in data]


def group_by_session(candles_30m, session_start_hour=0):
    """Group 30m candles into daily sessions."""
    sessions = defaultdict(list)
    for c in candles_30m:
        day = c["dt"].strftime("%Y-%m-%d")
        sessions[day].append(c)
    return sessions


# ─── VOLUME PROFILE ─────────────────────────────────────────────────────────

def build_daily_profile(candles_daily, tick=TICK_SIZE_USD):
    """
    Build volume profile for each daily candle.
    Returns dict: date -> {poc, vah, val, total_vol, touched}
    """
    profiles = {}
    for c in candles_daily:
        lo = c["low"]
        hi = c["high"]
        lo_bin = int(lo / tick) * tick
        hi_bin = int(hi / tick) * tick + tick
        n_bins = max(1, int((hi_bin - lo_bin) / tick))
        bins = [0.0] * n_bins

        # Single price candle
        rng = c["high"] - c["low"]
        if rng == 0:
            b = max(0, min(n_bins - 1, int((c["close"] - lo_bin) / tick)))
            bins[b] += c["vol"]
        else:
            for b_idx in range(n_bins):
                bin_lo_p = lo_bin + b_idx * tick
                bin_hi_p = bin_lo_p + tick
                overlap_lo = max(c["low"], bin_lo_p)
                overlap_hi = min(c["high"], bin_hi_p)
                fraction = max(0, (overlap_hi - overlap_lo) / rng)
                bins[b_idx] += c["vol"] * fraction

        total = sum(bins)
        if total == 0:
            continue

        poc_bin = max(range(len(bins)), key=lambda i: bins[i])
        poc_price = lo_bin + (poc_bin + 0.5) * tick
        poc_vol = bins[poc_bin]

        # Value area 70%
        target = total * 0.70
        cum = bins[poc_bin]
        val_lo_bin, val_hi_bin = poc_bin, poc_bin
        for delta in range(1, len(bins)):
            for b in [poc_bin - delta, poc_bin + delta]:
                if 0 <= b < len(bins):
                    cum += bins[b]
                    if b < poc_bin:
                        val_lo_bin = b
                    if b > poc_bin:
                        val_hi_bin = b
            if cum >= target:
                break

        vah = lo_bin + (val_hi_bin + 0.5) * tick
        val = lo_bin + (val_lo_bin + 0.5) * tick

        profiles[c["date"]] = {
            "poc": poc_price,
            "poc_vol": poc_vol,
            "vah": vah,
            "val": val,
            "total_vol": total,
            "close": c["close"],
            "touched": sum(1 for v in bins if v > 0),
            "hi": c["high"],
            "lo": c["low"],
        }
    return profiles


# ─── SWING DETECTION ────────────────────────────────────────────────────────

def detect_swings(candles, strength=SWING_STRENGTH):
    """
    Return list of swing points from candles.
    Each swing: {idx, date, type, price, vol, bullish, close}
    """
    swings = []
    n = len(candles)
    for i in range(strength, n - strength):
        left  = candles[i - strength:i]
        right = candles[i + 1:i + strength + 1]
        h, l  = candles[i]["high"], candles[i]["low"]
        is_high = h >= max(c["high"] for c in left) and h >= max(c["high"] for c in right)
        is_low  = l <= min(c["low"]  for c in left) and l <= min(c["low"]  for c in right)
        if is_high and not is_low:
            swings.append({
                "idx": i, "date": candles[i]["date"],
                "type": "HIGH", "price": h,
                "vol": candles[i]["vol"],
                "bullish": candles[i]["close"] > candles[i]["open"],
                "close": candles[i]["close"],
                "swing_high": candles[i]["high"],
                "swing_low": candles[i]["low"],
            })
        elif is_low and not is_high:
            swings.append({
                "idx": i, "date": candles[i]["date"],
                "type": "LOW", "price": l,
                "vol": candles[i]["vol"],
                "bullish": candles[i]["close"] > candles[i]["open"],
                "close": candles[i]["close"],
                "swing_high": candles[i]["high"],
                "swing_low": candles[i]["low"],
            })
    return swings


def score_swing(swing, daily_profiles, candles_daily):
    """
    Score swing quality (1-5).
    Factors: volume vs avg, candle color, POC position on formation day.
    """
    score = 1
    date = swing["date"]

    # Volume score
    swing_type = swing["type"]
    same_type_swings = [s for s in detect_swings(candles_daily) if s["type"] == swing_type]
    if same_type_swings:
        avg_vol = sum(s["vol"] for s in same_type_swings) / len(same_type_swings)
        if swing["vol"] > avg_vol * 1.5:
            score += 2
        elif swing["vol"] > avg_vol:
            score += 1

    # Candle color score
    if swing["type"] == "LOW" and swing["bullish"]:
        score += 1  # green candle at swing low = strong support
    if swing["type"] == "HIGH" and not swing["bullish"]:
        score += 1  # red candle at swing high = strong rejection

    # POC position score
    if date in daily_profiles:
        poc = daily_profiles[date]["poc"]
        if swing["type"] == "LOW" and swing["price"] < poc:
            score += 1  # swing low below POC = distribution, more bearish
        if swing["type"] == "HIGH" and swing["price"] > poc:
            score += 1  # swing high above POC = accumulation

    return min(score, 5)


def get_swing_zone_price(swings, target_date, direction="LOW", lookback=5):
    """Find the most recent swing of given type before target_date."""
    prior = [s for s in swings if s["date"] < target_date and s["type"] == direction]
    if not prior:
        return None
    return prior[-1]["price"]


# ─── REJECTION CANDLE DETECTION ──────────────────────────────────────────────

def detect_rejection_candles(candles_30m, lookback=20):
    """
    Find 30m rejection candles in the most recent candles_30m.
    Rejection: wick > REJECT_WICK_MULT × body, with volume confirmation.

    Returns list of {idx, dt, type, low, high, open, close, vol, body, wick, is_rejection}
    """
    results = []
    n = len(candles_30m)

    for i in range(lookback, n):
        c = candles_30m[i]
        bull = c["close"] > c["open"]
        body = abs(c["close"] - c["open"])
        rng  = c["high"] - c["low"]
        if rng == 0:
            continue

        wick_up = c["high"] - max(c["open"], c["close"])
        wick_dn = min(c["open"], c["close"]) - c["low"]

        is_rejection = False
        if bull and wick_dn > body * REJECT_WICK_MULT:
            is_rejection = True   # ▲ REJECT = bullish rejection (low wick = support found)
        elif not bull and wick_up > body * REJECT_WICK_MULT:
            is_rejection = True   # ▼ REJECT = bearish rejection (high wick = rejection)

        results.append({
            "idx": i,
            "dt": c["dt"],
            "date": c["date"],
            "time": c["time"],
            "type": "REJECTION_UP" if (bull and wick_dn > body * REJECT_WICK_MULT) else
                    "REJECTION_DOWN" if (not bull and wick_up > body * REJECT_WICK_MULT) else "NONE",
            "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
            "vol": c["vol"],
            "body": body,
            "wick_up": wick_up, "wick_dn": wick_dn,
            "is_rejection": is_rejection,
            "bullish": bull,
        })
    return results


def get_avg_volume_at_level(candles_30m, price, tolerance_pct=0.02, lookback=50):
    """
    Average volume of 30m candles that overlapped with a given price level.
    This tells us "normal" volume for that price zone.
    """
    vol_at_level = []
    for c in candles_30m[-lookback:]:
        # Does this candle's range overlap with the price level?
        if c["low"] <= price <= c["high"]:
            vol_at_level.append(c["vol"])
    return sum(vol_at_level) / len(vol_at_level) if vol_at_level else sum(c["vol"] for c in candles_30m[-lookback:]) / min(lookback, len(candles_30m))


def find_swing_zone_in_30m(candles_30m, swings_4h, direction):
    """
    Find if price is currently near a confirmed 4h swing zone.
    Returns the swing and distance in %.
    """
    current_price = candles_30m[-1]["close"]
    current_date  = candles_30m[-1]["date"]

    # Get most recent swing of the given type
    relevant_swings = [s for s in swings_4h if s["type"] == direction]
    if not relevant_swings:
        return None

    latest = relevant_swings[-1]
    zone_price = latest["price"]
    dist_pct = abs(current_price - zone_price) / current_price * 100

    return {
        "swing": latest,
        "zone_price": zone_price,
        "current_price": current_price,
        "distance_pct": dist_pct,
        "distance_dollar": abs(current_price - zone_price),
    }


# ─── SIGNAL GENERATION ───────────────────────────────────────────────────────

def generate_signal(asset, candles_daily, candles_4h, candles_30m,
                    daily_profiles, swings_daily, swings_4h):
    """
    Multi-timeframe signal generation.

    Steps:
    1. Daily bias: POC position
    2. 4h swing: find recent confirmed swing + strength
    3. 30m: detect rejection candle at swing zone with volume confirmation
    4. Cross-filter and generate GET_READY or ENTRY signal
    """
    current_price = candles_30m[-1]["close"]
    current_dt    = candles_30m[-1]["dt"]
    current_date  = candles_30m[-1]["date"]

    signals = []

    # ── Step 1: Daily POC bias ──────────────────────────────────────────────
    if current_date not in daily_profiles:
        return []

    poc_data = daily_profiles[current_date]
    poc      = poc_data["poc"]
    poc_above = current_price > poc
    poc_below = current_price < poc
    poc_dist_pct = abs(current_price - poc) / current_price * 100

    # ── Step 2: Recent 4h swings ────────────────────────────────────────────
    recent_highs = [s for s in swings_4h if s["type"] == "HIGH"]
    recent_lows  = [s for s in swings_4h if s["type"] == "LOW"]

    if not recent_highs or not recent_lows:
        return []

    # Most recent swings
    last_high = recent_highs[-1]
    last_low  = recent_lows[-1]

    # ── Step 3: 30m rejection candles ─────────────────────────────────────
    rejections = detect_rejection_candles(candles_30m)

    # ── Step 4: Check each direction ───────────────────────────────────────

    # LONG SETUP
    # Requirements:
    #   - POC above price (market structure bearish → look for long reversal)
    #   - 4h swing low nearby (within 5%)
    #   - 30m rejection UP candle at/near the swing low zone
    #   - Volume on rejection > 1.5x avg volume at that level
    #   - If rejection candle high is broken → BUY signal
    #   - If price approaching swing low but no rejection yet → GET_READY

    low_zone = find_swing_zone_in_30m(candles_30m, swings_4h, "LOW")
    high_zone = find_swing_zone_in_30m(candles_30m, swings_4h, "HIGH")

    long_score = 0
    long_signal = None
    short_score = 0
    short_signal = None

    # ── LONG ANALYSIS ──────────────────────────────────────────────────────
    if low_zone:
        zone_price = low_zone["zone_price"]
        dist_pct   = low_zone["distance_pct"]

        # Find 30m rejection candles near this zone
        nearby_rejections = [
            r for r in rejections
            if r["is_rejection"]
            and r["type"] == "REJECTION_UP"
            and r["low"] <= zone_price * 1.02
            and r["low"] >= zone_price * 0.95
        ]

        for rej in nearby_rejections[-5:]:  # most recent 5 rejections
            # Volume check
            avg_vol = get_avg_volume_at_level(candles_30m, rej["low"])
            vol_ratio = rej["vol"] / avg_vol if avg_vol > 0 else 0

            if vol_ratio >= REJECT_VOL_MULT:
                # BUY signal: rejection candle high broken
                if rej["high"] < current_price:  # already above rejection high
                    long_signal = {
                        "direction": "BUY",
                        "stage": "ENTRY",
                        "asset": asset["name"],
                        "price": current_price,
                        "entry_price": round(rej["high"], 2),
                        "stop_loss": round(rej["low"], 2),
                        "swing_zone": round(zone_price, 2),
                        "rejection_dt": rej["dt"].isoformat(),
                        "rejection_high": round(rej["high"], 2),
                        "rejection_low": round(rej["low"], 2),
                        "rejection_vol": round(rej["vol"], 0),
                        "avg_vol_at_level": round(avg_vol, 0),
                        "vol_ratio": round(vol_ratio, 2),
                        "wick_size": round(rej["wick_dn"], 2),
                        "body_size": round(rej["body"], 2),
                        "poc": round(poc, 2),
                        "poc_above": poc_above,
                        "poc_dist_pct": round(poc_dist_pct, 2),
                        "swing_score": score_swing(low_zone["swing"], daily_profiles, candles_daily),
                        "timestamp": current_dt.isoformat(),
                    }
                    long_score = 5
                    break

        # GET_READY: approaching swing low but no confirmed rejection yet
        # Only skip GET_READY if there's a nearby rejection WITH volume confirmation
        if long_score == 0 and dist_pct <= 4.0:
            # Check for nearby rejections that are also volume-confirmed
            any_vol_nearby = any(
                (lambda r: (
                    r["is_rejection"] and r["type"] == "REJECTION_UP"
                    and r["low"] <= zone_price * 1.03
                    and r["low"] >= zone_price * 0.97
                    and r["vol"] / max(get_avg_volume_at_level(candles_30m, r["low"]), 1) >= REJECT_VOL_MULT
                ))(r)
                for r in rejections[-10:]
            )
            if not any_vol_nearby:
                long_signal = {
                    "direction": "BUY",
                    "stage": "GET_READY",
                    "asset": asset["name"],
                    "price": current_price,
                    "swing_zone": round(zone_price, 2),
                    "swing_date": low_zone["swing"]["date"],
                    "swing_type": "LOW",
                    "distance_pct": round(dist_pct, 2),
                    "distance_dollar": round(low_zone["distance_dollar"], 2),
                    "poc": round(poc, 2),
                    "poc_above": poc_above,
                    "poc_dist_pct": round(poc_dist_pct, 2),
                    "swing_score": score_swing(low_zone["swing"], daily_profiles, candles_daily),
                    "timestamp": current_dt.isoformat(),
                }
                long_score = 3

    # ── SHORT ANALYSIS ─────────────────────────────────────────────────────
    if high_zone:
        zone_price = high_zone["zone_price"]
        dist_pct   = high_zone["distance_pct"]

        nearby_rejections = [
            r for r in rejections
            if r["is_rejection"]
            and r["type"] == "REJECTION_DOWN"
            and r["high"] >= zone_price * 0.98
            and r["high"] <= zone_price * 1.03
        ]

        for rej in nearby_rejections[-5:]:
            avg_vol = get_avg_volume_at_level(candles_30m, rej["high"])
            vol_ratio = rej["vol"] / avg_vol if avg_vol > 0 else 0

            if vol_ratio >= REJECT_VOL_MULT:
                # SELL signal: rejection candle low broken (price fell below it)
                if rej["low"] > current_price:  # still above rejection low → not triggered yet
                    short_signal = {
                        "direction": "SELL",
                        "stage": "GET_READY",
                        "asset": asset["name"],
                        "price": current_price,
                        "swing_zone": round(zone_price, 2),
                        "swing_date": high_zone["swing"]["date"],
                        "swing_type": "HIGH",
                        "distance_pct": round(dist_pct, 2),
                        "distance_dollar": round(high_zone["distance_dollar"], 2),
                        "rejection_dt": rej["dt"].isoformat(),
                        "rejection_high": round(rej["high"], 2),
                        "rejection_low": round(rej["low"], 2),
                        "rejection_vol": round(rej["vol"], 0),
                        "avg_vol_at_level": round(avg_vol, 0),
                        "vol_ratio": round(vol_ratio, 2),
                        "wick_size": round(rej["wick_up"], 2),
                        "body_size": round(rej["body"], 2),
                        "poc": round(poc, 2),
                        "poc_above": poc_above,
                        "poc_dist_pct": round(poc_dist_pct, 2),
                        "swing_score": score_swing(high_zone["swing"], daily_profiles, candles_daily),
                        "timestamp": current_dt.isoformat(),
                    }
                else:
                    short_signal = {
                        "direction": "SELL",
                        "stage": "ENTRY",
                        "asset": asset["name"],
                        "price": current_price,
                        "entry_price": round(rej["low"], 2),
                        "stop_loss": round(rej["high"], 2),
                        "swing_zone": round(zone_price, 2),
                        "rejection_dt": rej["dt"].isoformat(),
                        "rejection_high": round(rej["high"], 2),
                        "rejection_low": round(rej["low"], 2),
                        "rejection_vol": round(rej["vol"], 0),
                        "avg_vol_at_level": round(avg_vol, 0),
                        "vol_ratio": round(vol_ratio, 2),
                        "wick_size": round(rej["wick_up"], 2),
                        "body_size": round(rej["body"], 2),
                        "poc": round(poc, 2),
                        "poc_above": poc_above,
                        "poc_dist_pct": round(poc_dist_pct, 2),
                        "swing_score": score_swing(high_zone["swing"], daily_profiles, candles_daily),
                        "timestamp": current_dt.isoformat(),
                    }
                    short_score = 5
                    break

        if short_score == 0 and dist_pct <= 4.0:
            # Only skip GET_READY if there's a nearby rejection WITH volume confirmation
            any_vol_nearby = any(
                (lambda r: (
                    r["is_rejection"] and r["type"] == "REJECTION_DOWN"
                    and r["high"] >= zone_price * 0.97
                    and r["high"] <= zone_price * 1.03
                    and r["vol"] / max(get_avg_volume_at_level(candles_30m, r["high"]), 1) >= REJECT_VOL_MULT
                ))(r)
                for r in rejections[-10:]
            )
            if not any_vol_nearby:
                short_signal = {
                    "direction": "SELL",
                    "stage": "GET_READY",
                    "asset": asset["name"],
                    "price": current_price,
                    "swing_zone": round(zone_price, 2),
                    "swing_date": high_zone["swing"]["date"],
                    "swing_type": "HIGH",
                    "distance_pct": round(dist_pct, 2),
                    "distance_dollar": round(high_zone["distance_dollar"], 2),
                    "poc": round(poc, 2),
                    "poc_above": poc_above,
                    "poc_dist_pct": round(poc_dist_pct, 2),
                    "swing_score": score_swing(high_zone["swing"], daily_profiles, candles_daily),
                    "timestamp": current_dt.isoformat(),
                }
                short_score = 3

    if long_signal:
        signals.append(long_signal)
    if short_signal:
        signals.append(short_signal)

    return signals


# ─── TELEGRAM FORMATTER ─────────────────────────────────────────────────────

def format_telegram(signal, asset_name, current_price):
    """Format signal as Telegram message."""
    direction  = signal["direction"]
    stage      = signal["stage"]
    symbol     = asset_name
    poc        = signal.get("poc", 0)
    poc_above  = signal.get("poc_above", None)
    poc_dist   = signal.get("poc_dist_pct", 0)
    swing_zone = signal.get("swing_zone", 0)
    swing_date = signal.get("swing_date", "N/A")
    dist_pct   = signal.get("distance_pct", 0)
    dist_dol   = signal.get("distance_dollar", 0)
    score      = signal.get("swing_score", 0)

    if stage == "GET_READY":
        emoji = "⚠️" if direction == "BUY" else "🔻"
        header = f"{emoji} GET READY — {direction} — {symbol}/USDT"
        if direction == "BUY":
            body = (
                f"{'─'*36}\n"
                f"📍 Swing zone: ${swing_zone:,.2f} (from {swing_date})\n"
                f"📏 Distance: {dist_pct:.1f}% / ${dist_dol:.2f}\n"
                f"📊 POC: ${poc:,.2f} | {'above price ✓' if poc_above else 'below price'}\n"
                f"⭐ Swing score: {score}/5\n\n"
                f"Waiting for 30m rejection candle at swing low...\n"
                f"Trigger: rejection UP on 30m with vol > 1.5× avg\n"
                f"Entry: break above rejection candle HIGH\n"
                f"[30m] Binance · v{STRATEGY_VERSION}"
            )
        else:
            body = (
                f"{'─'*36}\n"
                f"📍 Swing zone: ${swing_zone:,.2f} (from {swing_date})\n"
                f"📏 Distance: {dist_pct:.1f}% / ${dist_dol:.2f}\n"
                f"📊 POC: ${poc:,.2f} | {'above price' if poc_above else 'below price ✓'}\n"
                f"⭐ Swing score: {score}/5\n\n"
                f"Waiting for 30m rejection candle at swing high...\n"
                f"Trigger: rejection DOWN on 30m with vol > 1.5× avg\n"
                f"Entry: break below rejection candle LOW\n"
                f"[30m] Binance · v{STRATEGY_VERSION}"
            )

    else:  # ENTRY
        emoji  = "🚀" if direction == "BUY" else "🔻"
        header = f"{emoji} {direction} SIGNAL — {symbol}/USDT"
        rej_dt  = signal.get("rejection_dt", "")[-14:]
        rej_h   = signal.get("rejection_high", 0)
        rej_l   = signal.get("rejection_low", 0)
        rej_vol = signal.get("rejection_vol", 0)
        avg_vol = signal.get("avg_vol_at_level", 0)
        vol_r   = signal.get("vol_ratio", 0)
        wick    = signal.get("wick_size", 0)
        body_sz = signal.get("body_size", 0)
        entry_p = signal.get("entry_price", 0)
        sl      = signal.get("stop_loss", 0)
        risk    = entry_p - sl if direction == "BUY" else sl - entry_p
        rr      = (swing_zone - entry_p) / risk if risk > 0 else 0

        if direction == "BUY":
            body = (
                f"{'─'*36}\n"
                f"30m rejection confirmed at ${swing_zone:,.2f}\n"
                f"Rejected: {rej_dt} | vol={rej_vol:,.0f} (avg={avg_vol:,.0f}, {vol_r:.1f}×)\n"
                f"Wick: ${wick:.2f} | Body: ${body_sz:.2f} | Ratio: {wick/body_sz:.1f}:1\n\n"
                f"🎯 Entry (break of {rej_h}): ${entry_p:,.2f}\n"
                f"🛑 Stop Loss: ${sl:,.2f} (below rejection low)\n"
                f"📍 TP: ${swing_zone:,.2f} → ${swing_zone * 1.15:,.2f}\n"
                f"⚖️ R:R ~ 1:{rr:.1f}\n\n"
                f"📊 POC: ${poc:,.2f} | {'above price ✓' if poc_above else 'below price'}\n"
                f"⭐ Swing score: {score}/5\n\n"
                f"Rule: Enter on break of ${rej_h:,.2f}.\n"
                f"[30m] Binance · v{STRATEGY_VERSION}"
            )
        else:
            body = (
                f"{'─'*36}\n"
                f"30m rejection confirmed at ${swing_zone:,.2f}\n"
                f"Rejected: {rej_dt} | vol={rej_vol:,.0f} (avg={avg_vol:,.0f}, {vol_r:.1f}×)\n"
                f"Wick: ${wick:.2f} | Body: ${body_sz:.2f} | Ratio: {wick/body_sz:.1f}:1\n\n"
                f"🎯 Entry (break of {rej_l}): ${entry_p:,.2f}\n"
                f"🛑 Stop Loss: ${sl:,.2f} (above rejection high)\n"
                f"📍 TP: ${swing_zone:,.2f} → ${swing_zone * 0.85:,.2f}\n"
                f"⚖️ R:R ~ 1:{rr:.1f}\n\n"
                f"📊 POC: ${poc:,.2f} | {'above price' if poc_above else 'below price ✓'}\n"
                f"⭐ Swing score: {score}/5\n\n"
                f"Rule: Enter on break below ${rej_l:,.2f}.\n"
                f"[30m] Binance · v{STRATEGY_VERSION}"
            )

    return f"{header}\n{body}"


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    all_results = []
    now = datetime.utcnow()

    for asset in ASSETS:
        sym = asset["symbol"]
        name = asset["name"]

        # Fetch all timeframes
        try:
            daily_4h   = fetch_klines(sym, "4h",   LOOKBACK_4H)
            candles_30m = fetch_klines(sym, "30m",  LOOKBACK_30M)
            candles_daily = fetch_klines(sym, "1d",  LOOKBACK_DAILY)
        except Exception as e:
            print(f"ERROR fetching {sym}: {e}")
            continue

        if len(candles_daily) < 60 or len(candles_30m) < 50:
            print(f"WARNING: insufficient data for {sym}")
            continue

        # Build daily volume profiles
        daily_profiles = build_daily_profile(candles_daily)

        # Detect swings on both timeframes
        swings_daily = detect_swings(candles_daily)
        swings_4h    = detect_swings(daily_4h)

        # Generate signals
        signals = generate_signal(
            asset, candles_daily, daily_4h, candles_30m,
            daily_profiles, swings_daily, swings_4h
        )

        current_price = candles_30m[-1]["close"]
        current_dt    = candles_30m[-1]["dt"]

        for sig in signals:
            msg = format_telegram(sig, name, current_price)
            sig["telegram_message"] = msg
            sig["symbol"] = sym
            sig["current_price"] = current_price
            sig["scan_dt"] = now.isoformat()
            all_results.append(sig)

            print(f"\n{'='*50}")
            print(msg)

    # Save results
    output = {
        "scan_dt": now.isoformat(),
        "version": STRATEGY_VERSION,
        "assets_scanned": len(ASSETS),
        "signals": all_results,
        "signal_count": len(all_results),
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n[Saved to {OUTPUT_PATH}]")
    print(f"Signals found: {len(all_results)}")

    # Send to Telegram if we have a token
    if TELEGRAM_BOT_TOKEN and all_results:
        for sig in all_results:
            if sig["stage"] == "ENTRY":
                try:
                    url = f"{API_BASE}{TELEGRAM_BOT_TOKEN}/sendMessage"
                    data = {
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": sig["telegram_message"],
                        "parse_mode": "HTML",
                    }
                    req = urllib.request.Request(
                        url, data=json.dumps(data).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                    print(f"[Telegram sent for {sig['asset']} {sig['direction']}]")
                except Exception as e:
                    print(f"Telegram error: {e}")


if __name__ == "__main__":
    main()
