#!/usr/bin/env python3
"""
Double Bottom Breakout — PICO Paper Trading Signal Generator
Asset: BTC/USDT | Timeframe: Daily | Exchange: Binance

Generates Telegram-formatted signals for PICO dashboard.
Run daily via cron: 0 9 * * * /home/openclaw/workspace/strategies/double-bottom-signal.py
"""

import json
import urllib.request
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

# ─── CONFIG ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("HERMES_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = "401478089"  # Pete's Telegram
API_BASE = "https://api.telegram.org/bot"
STRATEGY_VERSION = "1.0"

# Strategy parameters
MIN_GAP = 8        # minimum days between two lows
MAX_GAP = 60       # maximum days between two lows
LOW_TOL = 0.07     # 7% tolerance between two lows
PEAK_MIN = 0.04    # peak must be 4% above avg low
SL_PCT = 0.03      # 3% stop loss below avg low
TP_MULT = 1.0      # TP = neckline + height × TP_MULT (1.0 = full measured move)
MAX_WAIT = 30      # days to wait for entry after pattern forms
MIN_PATTERN_HEIGHT_PCT = 0.03  # pattern height must be at least 3% of price
# ─────────────────────────────────────────────────────────────────────────────

def get_binance_klines(symbol="BTCUSDT", interval="1d", limit=500,
                       start_time: Optional[int] = None,
                       end_time: Optional[int] = None):
    """Fetch klines from Binance public API."""
    params = f"?symbol={symbol}&interval={interval}&limit={limit}"
    if start_time:
        params += f"&startTime={start_time}"
    if end_time:
        params += f"&endTime={end_time}"
    url = "https://api.binance.com/api/v3/klines" + params
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return [{
        "time": datetime.fromtimestamp(d[0] / 1000),
        "open": float(d[1]), "high": float(d[2]),
        "low": float(d[3]), "close": float(d[4]),
        "volume": float(d[5])
    } for d in data]


def compute_rsi(candles, period=14):
    """Compute RSI(14) for each candle."""
    n = len(candles)
    if n < period + 1:
        return [50.0] * n
    closes = [c["close"] for c in candles]
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi = [50.0] * period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100 - (100 / (1 + rs)))
    return rsi


def find_local_mins(candles, window=4):
    """Return indices of local minima in candles list."""
    mins = []
    for i in range(window, len(candles) - window):
        zone = [candles[k]["low"] for k in range(i - window, i + window + 1)]
        if candles[i]["low"] == min(zone):
            mins.append(i)
    return mins


def find_double_bottoms(candles, rsi_list,
                         min_gap=MIN_GAP, max_gap=MAX_GAP,
                         low_tol=LOW_TOL, peak_min_pct=PEAK_MIN):
    """
    Find double bottom patterns prospectively.
    Returns patterns that have formed (right_low is the most recent low).
    Excludes patterns where neckline has already been broken to the downside.
    """
    mins = find_local_mins(candles, window=4)
    patterns = []
    n = len(candles)

    for a in range(len(mins)):
        for b in range(a + 1, len(mins)):
            gap = mins[b] - mins[a]
            if gap < min_gap or gap > max_gap:
                continue
            i, j = mins[a], mins[b]
            ll, rl = candles[i]["low"], candles[j]["low"]
            avg_low = (ll + rl) / 2

            # Tolerance check
            if abs(ll - rl) / avg_low > low_tol:
                continue

            # Find peak between the two lows
            between = candles[i + 1:j]
            if not between:
                continue
            peak_price = max(c["high"] for c in between)
            rise = (peak_price - avg_low) / avg_low
            if rise < peak_min_pct:
                continue

            # Pattern height sanity check
            height = peak_price - avg_low
            if height / avg_low < MIN_PATTERN_HEIGHT_PCT:
                continue

            # RSI filter: 2nd low RSI should be below 60 (not overbought bounce)
            if rsi_list and rsi_list[j] >= 60:
                continue

            # Neckline already broken to downside by the time right low forms?
            # Check: did price close below avg_low before right_low formed?
            sl_check = avg_low * (1 - SL_PCT)
            sl_broken = any(
                candles[k]["close"] < sl_check
                for k in range(j + 1, min(n, j + 5))
            )
            if sl_broken:
                continue

            patterns.append({
                "left_idx": i, "right_idx": j,
                "left_date": candles[i]["time"].date().isoformat(),
                "right_date": candles[j]["time"].date().isoformat(),
                "left_low": ll, "right_low": rl, "avg_low": avg_low,
                "peak": peak_price, "neckline": peak_price,
                "height": height,
                "gap_days": gap,
                "rsi_2nd_low": round(rsi_list[j], 1) if rsi_list else None,
            })

    return patterns


def build_signal(candles, pattern) -> dict:
    """Build a trade signal dict from a pattern."""
    avg_low = pattern["avg_low"]
    sl = round(avg_low * (1 - SL_PCT), 2)
    tp = round(pattern["neckline"] + pattern["height"] * TP_MULT, 2)
    tp1 = round(pattern["neckline"] + pattern["height"] * 0.5, 2)
    entry_price = round(pattern["neckline"], 2)

    risk_amount = entry_price - sl
    reward_amount = tp - entry_price
    rr_ratio = round(reward_amount / risk_amount, 2) if risk_amount > 0 else 0

    # Position size: risk max 2% of bankroll
    # With $10,000 bankroll, 2% = $200. Risk per trade = risk_amount × position_size
    # position_size = $200 / risk_amount
    bankroll = 10000
    max_risk_pct = 0.02
    max_risk_amount = bankroll * max_risk_pct
    position_size_usd = round(max_risk_amount / (risk_amount / entry_price), 2)
    position_size_pct = round(position_size_usd / bankroll * 100, 1)

    return {
        "pattern": "DOUBLE_BOTTOM_BREAKOUT",
        "version": STRATEGY_VERSION,
        "signal_date": candles[-1]["time"].date().isoformat(),
        "left_low_date": pattern["left_date"],
        "right_low_date": pattern["right_date"],
        "days_since_right_low": (candles[-1]["time"].date() - datetime.strptime(pattern["right_date"], "%Y-%m-%d").date()).days,
        "current_price": round(candles[-1]["close"], 2),
        "entry_price": entry_price,
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp,
        "risk_reward": rr_ratio,
        "position_size_usd": position_size_usd,
        "position_size_pct": position_size_pct,
        "risk_amount_usd": round(position_size_usd * (risk_amount / entry_price), 2),
        "reward_amount_usd": round(position_size_usd * (reward_amount / entry_price), 2),
        "rsi_2nd_low": pattern["rsi_2nd_low"],
        "neckline": round(pattern["neckline"], 2),
        "pattern_height": round(pattern["height"], 2),
    }


def format_telegram_message(signal: dict) -> str:
    """Format a PICO-style Telegram signal message."""
    days_ago = signal["days_since_right_low"]
    days_str = f"{days_ago}d ago"

    msg = (
        f"📊 DOUBLE BOTTOM BREAKOUT — BTC/USDT\n"
        f"{'─'*36}\n"
        f"Pattern confirmed: {signal['left_low_date']} → {signal['right_low_date']} ({days_str})\n"
        f"RSI at 2nd low: {signal['rsi_2nd_low']}\n\n"
        f"🎯 Entry: ${signal['entry_price']:,.2f}\n"
        f"🛑 Stop Loss: ${signal['stop_loss']:,.2f}\n"
        f"📍 TP1: ${signal['tp1']:,.2f}\n"
        f"📍 TP2 (full): ${signal['tp2']:,.2f}\n\n"
        f"📐 Neckline: ${signal['neckline']:,.2f} | Height: ${signal['pattern_height']:,.2f}\n"
        f"⚖️ Risk/Reward: 1:{signal['risk_reward']}\n\n"
        f"💰 Position: {signal['position_size_pct']}% of bankroll\n"
        f"   → ${signal['position_size_usd']:,.2f} @ ${signal['entry_price']:,.2f}\n"
        f"   Risk: ${signal['risk_amount_usd']:,.2f} | Reward: ${signal['reward_amount_usd']:,.2f}\n\n"
        f"💡 Rule: Enter when BTC closes above neckline. TP2 = full measured move.\n"
        f"[D1] Binance · {signal['version']}"
    )
    return msg


def check_pending_entry(candles, patterns, sl_pct=SL_PCT, max_wait=MAX_WAIT):
    """
    Check if any pattern is pending entry (right_low formed but neckline not yet broken).
    Returns list of patterns that are waiting for breakout.
    """
    pending = []
    last_idx = len(candles) - 1
    last_date = candles[last_idx]["time"].date()

    for p in patterns:
        right_idx = p["right_idx"]
        days_since = (last_date - datetime.strptime(p["right_date"], "%Y-%m-%d").date()).days

        if 0 <= days_since <= max_wait:
            # Check if neckline already broken up or down
            neckline = p["neckline"]
            sl = p["avg_low"] * (1 - sl_pct)
            future = candles[right_idx + 1:]
            broken_up = any(c["close"] > neckline for c in future)
            broken_down = any(c["close"] < sl for c in future)

            if not broken_up and not broken_down:
                p["days_since_formation"] = days_since
                p["broken_up"] = broken_up
                p["broken_down"] = broken_down
                pending.append(p)

    return pending


def main():
    """Main signal generation."""
    now = datetime.utcnow()

    # Fetch recent 500 daily candles (covers ~2 years — Binance max lookback)
    candles = get_binance_klines(limit=500)
    if not candles:
        print("ERROR: No candles fetched from Binance")
        sys.exit(1)

    if len(candles) < 100:
        print(f"ERROR: Only {len(candles)} candles fetched")
        sys.exit(1)

    rsi_list = compute_rsi(candles)
    patterns = find_double_bottoms(candles, rsi_list)
    pending = check_pending_entry(candles, patterns)

    current_price = candles[-1]["close"]
    current_date = candles[-1]["time"].date()

    # Output for PICO cron delivery
    if pending:
        signal = build_signal(candles, pending[0])
        msg = format_telegram_message(signal)
    else:
        msg = (
            f"📊 DOUBLE BOTTOM BREAKOUT — BTC/USDT\n"
            f"{'─'*36}\n"
            f"🕐 Scan date: {current_date}\n"
            f"💰 BTC price: ${current_price:,.2f}\n\n"
            f"⚪ No pending double bottom setup.\n"
            f"   No valid pattern waiting for entry.\n\n"
            f"[D1] Binance · {STRATEGY_VERSION}"
        )

    # Save signal to file for PICO cron pickup
    output_path = "/home/openclaw/workspace/strategies/double-bottom-signal.json"
    with open(output_path, "w") as f:
        json.dump({
            "signal": signal if pending else None,
            "pending_count": len(pending),
            "current_price": current_price,
            "scan_date": current_date.isoformat(),
            "telegram_message": msg,
        }, f, indent=2)

    # Print for debug / cron stdout
    print(msg)
    print(f"\n[Saved to {output_path}]")


if __name__ == "__main__":
    main()
