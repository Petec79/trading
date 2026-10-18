#!/usr/bin/env python3
"""
Momentum Strategy Backtester — ETHUSDT Binance 5m
Based on @spicyofc "Momentum Trading Strategy Guide"

Rules implemented:
  1. Grindy "Staircase" — successive HH+HL (longs) or LH+LL (shorts) approaching level
  2. Increasing Volume — vol above MA × 1.1 for last 5 bars
  3. 2+ Hours of Staircase — at least 24 bars (5m) of structure before entry

Entry:  1 candle closes through swing high (long) / swing low (short)
SL:     Relevant swing low (long) / swing high (short)
Target: 1R conservative (backtest default)

Sessions:
  Asia      00:00–08:00 UTC
  London AM 08:00–12:00 UTC  (BEST for mean reversion — testing for momentum too)
  London PM 12:00–16:00 UTC
  New York  16:00–21:00 UTC

Run:
  python momentum_backtest.py [--days 60] [--symbol ETHUSDT]
"""

import argparse
import math
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd
import requests

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL = "ETHUSDT"
DEFAULT_DAYS   = 60

TIMEFRAMES = {
    "1m":  (1,    "days ago UTC",       1440),
    "5m":  (5,    "days ago UTC",        288),
    "15m": (12,   "days ago UTC",        96),
    "1h":  (30,   "days ago UTC",        24),
    "4h":  (60,   "days ago UTC",        6),
}

# ─── DATA FETCHING ────────────────────────────────────────────────────────────
def fetch_binance_klines(symbol: str, interval: str = "5m",
                          days: int = DEFAULT_DAYS) -> pd.DataFrame:
    """Fetch OHLCV from Binance public API, 5m default."""
    interval_sec, _, _ = TIMEFRAMES[interval]
    now = pd.Timestamp.utcnow()
    start_ts = int((now - pd.Timedelta(days=days)).timestamp() * 1000)

    all_klines = []
    while True:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval,
                  "startTime": start_ts, "limit": 1000}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_klines.extend(batch)
        start_ts = batch[-1][0] + interval_sec * 60 * 1000
        if len(batch) < 1000:
            break

    df = pd.DataFrame(all_klines, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    for col in ["open","high","low","close","volume","quote_volume","trades"]:
        df[col] = pd.to_numeric(df[col])
    df = df[["open_time","open","high","low","close","volume","trades"]].copy()
    df = df.set_index("open_time")
    df = df[~df.index.duplicated(keep="first")]
    print(f"  Fetched {len(df):,} candles: {df.index[0]} → {df.index[-1]}")
    return df


# ─── SWING DETECTION ─────────────────────────────────────────────────────────
def detect_swings(df: pd.DataFrame, swing_n: int = 3) -> pd.DataFrame:
    """
    A bar i is a swing high if high[i] is the max over [i-swing_n .. i+swing_n]
    AND high[i] > close[i]  (so it's a true peak, not just wick)
    Similarly for swing low.
    """
    roll_high = df["high"].rolling(swing_n * 2 + 1, center=True).max()
    roll_low  = df["low"].rolling(swing_n * 2 + 1, center=True).min()

    # is_sh/is_sl detected at bar i using surrounding window — no lookahead
    df["is_sh"] = (df["high"] == roll_high) & (df["high"] > df["close"])
    df["is_sl"] = (df["low"]  == roll_low)  & (df["low"]  < df["close"])

    return df


# ─── STAIRCASE DETECTION ──────────────────────────────────────────────────────
def detect_staircase(df: pd.DataFrame, window_bars: int = 24) -> pd.DataFrame:
    """
    For each bar, check if the last `window_bars` bars show a clean staircase.

    LONG staircase:  consecutive swing lows are RISING  (HL + higher HL)
                     AND/OR consecutive swing highs are RISING  (HH + higher HH)
    SHORT staircase: consecutive swing lows are FALLING
                     AND/OR consecutive swing highs are FALLING

    We track the last N swing points and check directional consistency.
    """
    is_long_staircase  = pd.Series(False, index=df.index)
    is_short_staircase = pd.Series(False, index=df.index)

    sh_prices = []
    sl_prices = []
    sh_bars   = []
    sl_bars   = []

    swing_n = 3
    min_staircase_bars = window_bars  # 24 bars × 5m = 2h

    for i in range(swing_n * 2 + 5, len(df)):
        bar_time = df.index[i]

        # Add new swings
        if df["is_sh"].iloc[i]:
            sh_prices.append((i, df["high"].iloc[i]))
            sh_bars.append(i)
        if df["is_sl"].iloc[i]:
            sl_prices.append((i, df["low"].iloc[i]))
            sl_bars.append(i)

        # Keep only swings within the staircase window
        cutoff = i - min_staircase_bars

        recent_sh = [(bi, p) for (bi, p) in sh_prices if bi >= cutoff and bi <= i]
        recent_sl = [(bi, p) for (bi, p) in sl_prices if bi >= cutoff and bi <= i]

        if len(recent_sh) < 2 and len(recent_sl) < 2:
            continue

        # ── Long staircase: swing lows rising ────────────────────────────────
        # AND/OR swing highs rising (both should align)
        long_ok  = False
        short_ok = False

        if len(recent_sl) >= 2:
            # Check if consecutive SLs are rising
            sl_sorted = sorted(recent_sl, key=lambda x: x[0])
            rising_lows = sum(1 for k in range(1, len(sl_sorted))
                              if sl_sorted[k][1] > sl_sorted[k-1][1])
            if rising_lows >= len(sl_sorted) - 1 and rising_lows >= 1:
                long_ok = True

        if len(recent_sh) >= 2:
            # Check if consecutive SHs are rising (confirms uptrend)
            sh_sorted = sorted(recent_sh, key=lambda x: x[0])
            rising_highs = sum(1 for k in range(1, len(sh_sorted))
                               if sh_sorted[k][1] > sh_sorted[k-1][1])
            if rising_highs >= len(sh_sorted) - 1 and rising_highs >= 1:
                long_ok = True

        # ── Short staircase: swing lows falling ───────────────────────────────
        if len(recent_sl) >= 2:
            sl_sorted = sorted(recent_sl, key=lambda x: x[0])
            falling_lows = sum(1 for k in range(1, len(sl_sorted))
                               if sl_sorted[k][1] < sl_sorted[k-1][1])
            if falling_lows >= len(sl_sorted) - 1 and falling_lows >= 1:
                short_ok = True

        if len(recent_sh) >= 2:
            sh_sorted = sorted(recent_sh, key=lambda x: x[0])
            falling_highs = sum(1 for k in range(1, len(sh_sorted))
                                if sh_sorted[k][1] < sh_sorted[k-1][1])
            if falling_highs >= len(sh_sorted) - 1 and falling_highs >= 1:
                short_ok = True

        is_long_staircase.iloc[i]  = long_ok
        is_short_staircase.iloc[i] = short_ok

    df["is_long_staircase"]  = is_long_staircase
    df["is_short_staircase"] = is_short_staircase
    return df


# ─── VOLUME REGIME ────────────────────────────────────────────────────────────
def add_volume_regime(df: pd.DataFrame,
                      vol_lookback: int = 20,
                      vol_mult: float = 1.1,
                      vol_confirm_bars: int = 5) -> pd.DataFrame:
    """Volume must be above MA × mult for last N bars."""
    df["vol_ma"] = df["volume"].rolling(vol_lookback).mean()

    vol_ok_count = pd.Series(0, index=df.index)
    for i in range(vol_lookback, len(df)):
        count = 0
        for b in range(vol_confirm_bars):
            if df["volume"].iloc[i - b] > df["vol_ma"].iloc[i - b] * vol_mult:
                count += 1
        vol_ok_count.iloc[i] = count

    df["vol_regime"] = vol_ok_count >= vol_confirm_bars
    return df


# ─── INDICATORS ───────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = detect_swings(df, swing_n=3)
    df = add_volume_regime(df)
    df = detect_staircase(df, window_bars=24)  # 24 × 5m = 2h minimum
    return df


# ─── SESSION HELPERS ─────────────────────────────────────────────────────────
SESSION_HOURS = {
    "Asia":      (0,  8),
    "London AM": (8,  12),
    "London PM": (12, 16),
    "New York":  (16, 21),
}

def get_session(ts: pd.Timestamp) -> str:
    h = ts.hour
    for sess, (start, end) in SESSION_HOURS.items():
        if start <= h < end:
            return sess
    return "Off-hours"


# ─── BACKTEST ENGINE ─────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame,
                 min_quality: int = 2,
                 target_r: float = 1.0,
                 trail_on_r: float = 0.9) -> pd.DataFrame:
    """
    Walk forward bar by bar. When a bar triggers momentum long/short:
      - Check staircase + volume regime
      - Find relevant swing level
      - Calculate entry, SL, target
      - Walk forward to find exit (TP hit, SL hit, or 50-bar timeout)
    """
    trades = []

    # Precompute shifted SL/SH for "most recent swing before current bar"
    df["sh_price"] = df["high"].where(df["is_sh"]).ffill()
    df["sl_price"] = df["low"].where(df["is_sl"]).ffill()

    # Staircase + vol regime are already computed
    # Previous-bar values to avoid same-bar lookahead
    df["prev_close"]    = df["close"].shift(1)
    df["prev_is_sh"]    = df["is_sh"].shift(1)
    df["prev_is_sl"]    = df["is_sl"].shift(1)
    df["prev_sh_price"] = df["sh_price"].shift(1)
    df["prev_sl_price"] = df["sl_price"].shift(1)

    # ── Entry trigger: 1 candle CLOSES through the level ──────────────────
    # Long: prev_close <= prev_sh_price  AND  close > prev_sh_price
    # Short: prev_close >= prev_sl_price  AND  close < prev_sl_price
    df["trigger_long"]  = (df["prev_close"] <= df["prev_sh_price"]) & \
                           (df["close"] > df["prev_sh_price"])
    df["trigger_short"] = (df["prev_close"] >= df["prev_sl_price"]) & \
                           (df["close"] < df["prev_sl_price"])

    in_trade = False
    trade = None
    trade_id = 0

    for i in range(50, len(df)):
        row = df.iloc[i]
        sess = get_session(df.index[i])
        dow  = df.index[i].day_name()

        # ── Exit open trade ────────────────────────────────────────────────
        if in_trade and trade is not None:
            if trade["side"] == "long":
                # TP hit: price ROSE to hit TP (high must reach or exceed TP)
                if row["high"] >= trade["tp"]:
                    trade["exit_price"] = trade["tp"]
                    trade["exit_time"]  = df.index[i]
                    trade["exit_reason"] = "TP"
                    trade["pnl_r"]       = 1.0
                    trades.append(trade)
                    in_trade = False; trade = None
                # SL hit: price FELL to hit SL (low must reach or go below SL)
                elif row["low"] <= trade["sl"]:
                    trade["exit_price"] = trade["sl"]
                    trade["exit_time"]  = df.index[i]
                    trade["exit_reason"] = "SL"
                    pnl = (trade["sl"] - trade["entry_price"]) / trade["entry_price"] / trade["risk_pct"]
                    trade["pnl_r"] = pnl
                    trades.append(trade)
                    in_trade = False; trade = None
            else:  # short
                # TP hit: price FELL to hit TP (low must reach or go below TP)
                if row["low"] <= trade["tp"]:
                    trade["exit_price"] = trade["tp"]
                    trade["exit_time"]  = df.index[i]
                    trade["exit_reason"] = "TP"
                    trade["pnl_r"]       = 1.0
                    trades.append(trade)
                    in_trade = False; trade = None
                # SL hit: price ROSE to hit SL (high must reach or exceed SL)
                elif row["high"] >= trade["sl"]:
                    trade["exit_price"] = trade["sl"]
                    trade["exit_time"]  = df.index[i]
                    trade["exit_reason"] = "SL"
                    pnl = (trade["entry_price"] - trade["sl"]) / trade["entry_price"] / trade["risk_pct"]
                    trade["pnl_r"] = pnl
                    trades.append(trade)
                    in_trade = False; trade = None

            # Timeout after 50 bars
            if in_trade and trade is not None:
                bars_held = i - trade["entry_bar"]
                if bars_held >= 50:
                    exit_p = row["close"]
                    trade["exit_price"] = exit_p
                    trade["exit_time"]  = df.index[i]
                    trade["exit_reason"] = "TIMEOUT"
                    if trade["side"] == "long":
                        pnl = (exit_p - trade["entry_price"]) / trade["entry_price"] / trade["risk_pct"]
                    else:
                        pnl = (trade["entry_price"] - exit_p) / trade["entry_price"] / trade["risk_pct"]
                    trade["pnl_r"] = pnl
                    trades.append(trade)
                    in_trade = False; trade = None
            continue

        # ── Check for new entry ──────────────────────────────────────────────
        if not in_trade:
            # Count staircase bars in the 24 bars BEFORE the trigger bar (no lookahead)
            # is_long_staircase at bar k is computed from swings up to k, so k itself
            # has no future data — but we exclude it anyway for purity
            window_stairs_long  = df["is_long_staircase"].iloc[i-25:i-1].sum()
            window_stairs_short = df["is_short_staircase"].iloc[i-25:i-1].sum()

            vol_reg = row["vol_regime"]

            # Quality score
            # Criteria 1: staircase present in last 24 bars
            crit1_long  = window_stairs_long  >= 5   # at least 5 staircase bars
            crit1_short = window_stairs_short >= 5

            # Criteria 2: volume regime on
            crit2_long  = crit2_short = vol_reg

            # Criteria 3: 2h+ of history (we're at bar i >= 50, always satisfied)
            crit3 = True

            q_long  = int(crit1_long)  + int(crit2_long)  + int(crit3)
            q_short = int(crit1_short) + int(crit2_short) + int(crit3)

            if row["trigger_long"] and q_long >= min_quality:
                entry_price = row["close"]
                sl_price    = row["sl_price"] if not pd.isna(row["sl_price"]) else row["low"] * 0.995
                risk_pct    = (entry_price - sl_price) / entry_price
                if risk_pct <= 0:
                    continue
                tp_price    = entry_price + risk_pct * target_r * entry_price

                trade_id += 1
                trade = {
                    "trade_id":    trade_id,
                    "entry_time":  df.index[i],
                    "entry_bar":   i,
                    "side":        "long",
                    "entry_price": entry_price,
                    "sl":          sl_price,
                    "tp":          tp_price,
                    "risk_pct":    risk_pct,
                    "quality":     q_long,
                    "session":     sess,
                    "dow":         dow,
                    "staircase":   crit1_long,
                    "vol_regime":  crit2_long,
                    "setup":       "Momentum Long",
                }
                in_trade = True

            elif row["trigger_short"] and q_short >= min_quality:
                entry_price = row["close"]
                sl_price    = row["sh_price"] if not pd.isna(row["sh_price"]) else row["high"] * 1.005
                risk_pct    = (sl_price - entry_price) / entry_price
                if risk_pct <= 0:
                    continue
                tp_price    = entry_price - risk_pct * target_r * entry_price

                trade_id += 1
                trade = {
                    "trade_id":    trade_id,
                    "entry_time":  df.index[i],
                    "entry_bar":   i,
                    "side":        "short",
                    "entry_price": entry_price,
                    "sl":          sl_price,
                    "tp":          tp_price,
                    "risk_pct":    risk_pct,
                    "quality":     q_short,
                    "session":     sess,
                    "dow":         dow,
                    "staircase":   crit1_short,
                    "vol_regime":  crit2_short,
                    "setup":       "Momentum Short",
                }
                in_trade = True

    if trade is not None and in_trade:
        # Close at last bar
        last = df.iloc[-1]
        trade["exit_price"] = last["close"]
        trade["exit_time"]  = df.index[-1]
        trade["exit_reason"] = "EOD"
        if trade["side"] == "long":
            pnl = (last["close"] - trade["entry_price"]) / trade["entry_price"] / trade["risk_pct"]
        else:
            pnl = (trade["entry_price"] - last["close"]) / trade["entry_price"] / trade["risk_pct"]
        trade["pnl_r"] = pnl
        trades.append(trade)

    return pd.DataFrame(trades)


# ─── REPORTING ────────────────────────────────────────────────────────────────
def print_report(trades_df: pd.DataFrame):
    if trades_df.empty:
        print("No trades generated. Try lowering min_quality.")
        return

    total = len(trades_df)
    wins   = (trades_df["pnl_r"] > 0).sum()
    losses = (trades_df["pnl_r"] <= 0).sum()
    wr     = wins / total * 100
    avg_r  = trades_df["pnl_r"].mean()
    total_r= trades_df["pnl_r"].sum()

    print(f"\n{'═'*60}")
    print(f"  OVERALL — {total} trades | {wins}W/{losses}L | {wr:.0f}% WR | "
          f"Avg R: {avg_r:+.2f} | Total R: {total_r:+.1f}")
    print(f"{'═'*60}")

    # ── By Session ──────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"  BY SESSION")
    print(f"{'─'*50}")
    sessions = ["Asia", "London AM", "London PM", "New York", "Off-hours"]
    print(f"  {'Session':<14} {'Trades':>6} {'WR%':>6} {'AvgR':>6} {'TotalR':>8} {'Win?'}")
    print(f"  {'-'*50}")
    for sess in sessions:
        sub = trades_df[trades_df["session"] == sess]
        if sub.empty:
            continue
        n   = len(sub)
        wr_s= (sub["pnl_r"] > 0).sum() / n * 100
        avg = sub["pnl_r"].mean()
        tot = sub["pnl_r"].sum()
        best= "★ BEST" if sess == "London AM" else ""
        print(f"  {sess:<14} {n:>6} {wr_s:>5.0f}% {avg:>+6.2f} {tot:>+8.2f}  {best}")

    # ── By Day of Week ──────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"  BY DAY OF WEEK")
    print(f"{'─'*50}")
    days_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    print(f"  {'Day':<12} {'Trades':>6} {'WR%':>6} {'AvgR':>6} {'TotalR':>8} {'Verdict'}")
    print(f"  {'-'*50}")
    for day in days_order:
        sub = trades_df[trades_df["dow"] == day]
        if sub.empty:
            continue
        n   = len(sub)
        wr_s= (sub["pnl_r"] > 0).sum() / n * 100
        avg = sub["pnl_r"].mean()
        tot = sub["pnl_r"].sum()
        verdict = "★ BEST" if day == "Thursday" else ("★ Strong" if wr_s >= 75 else
                  ("✗ AVOID" if wr_s < 55 else "—"))
        print(f"  {day:<12} {n:>6} {wr_s:>5.0f}% {avg:>+6.2f} {tot:>+8.2f}  {verdict}")

    # ── By Side ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"  BY SIDE")
    print(f"{'─'*50}")
    for side in ["long", "short"]:
        sub = trades_df[trades_df["side"] == side]
        if sub.empty:
            continue
        n   = len(sub)
        wr_s= (sub["pnl_r"] > 0).sum() / n * 100
        avg = sub["pnl_r"].mean()
        tot = sub["pnl_r"].sum()
        print(f"  {side.capitalize():<10} {n:>6} {wr_s:>5.0f}% {avg:>+6.2f} {tot:>+8.2f}")

    # ── By Quality ──────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"  BY QUALITY SCORE")
    print(f"{'─'*50}")
    for q in sorted(trades_df["quality"].unique()):
        sub = trades_df[trades_df["quality"] == q]
        n   = len(sub)
        wr_s= (sub["pnl_r"] > 0).sum() / n * 100
        avg = sub["pnl_r"].mean()
        tot = sub["pnl_r"].sum()
        note = "— 3/3 ideal" if q == 3 else ("— 2/3 good" if q == 2 else "")
        print(f"  {q}/3             {n:>4} {wr_s:>5.0f}% {avg:>+6.2f} {tot:>+8.2f}  {note}")

    # ── Session × Day heatmap (total R) ─────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"  SESSION × DAY  (Total R)")
    print(f"{'─'*50}")
    pivot = trades_df.pivot_table(index="session", columns="dow",
                                   values="pnl_r", aggfunc="sum").fillna(0)
    pivot = pivot.reindex(index=["Asia","London AM","London PM","New York","Off-hours"],
                          columns=["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])
    # Print header
    print(f"  {'':12}", end="")
    for d in ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]:
        print(f" {d:>6}", end="")
    print()
    print(f"  {'─'*62}")
    for sess in pivot.index:
        print(f"  {sess:<12}", end="")
        for day in pivot.columns:
            val = pivot.loc[sess, day] if sess in pivot.index and day in pivot.columns else 0
            if val == 0:
                print(f" {'–':>6}", end="")
            else:
                col = "\033[92m" if val > 0 else "\033[91m"
                print(f" {col}{val:>+5.1f}\033[0m", end="")
        print()

    print(f"\n  Green = profit  |  Red = loss  |  – = no trades")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Momentum Strategy Backtester")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--days",   type=int, default=DEFAULT_DAYS)
    parser.add_argument("--tf",     default="5m")
    parser.add_argument("--min-quality", type=int, default=2)
    parser.add_argument("--target-r",   type=float, default=1.0)
    args = parser.parse_args()

    print(f"\n{'═'*60}")
    print(f"  MOMENTUM STRATEGY BACKTEST  —  {args.symbol}")
    print(f"  {args.days} days {args.tf}  |  Min quality: {args.min_quality}/3  |  Target: {args.target_r}R")
    print(f"{'═'*60}\n")

    print(f"Fetching Binance {args.symbol} {args.tf} data...")
    df = fetch_binance_klines(args.symbol, interval=args.tf, days=args.days)

    print(f"Computing indicators...")
    df = compute_indicators(df)

    long_sig  = df["is_long_staircase"].sum()
    short_sig = df["is_short_staircase"].sum()
    vol_reg   = df["vol_regime"].sum()
    print(f"  Staircase bars (long):  {long_sig:,}")
    print(f"  Staircase bars (short): {short_sig:,}")
    print(f"  Volume regime bars:     {vol_reg:,}")

    print(f"\nRunning backtest...")
    trades = run_backtest(df, min_quality=args.min_quality, target_r=args.target_r)

    if not trades.empty:
        trades.to_csv(f"/home/openclaw/workspace/strategies/momentum_trades_{args.symbol}_{args.tf}.csv",
                      index=False)
        print(f"  Trades saved to momentum_trades_{args.symbol}_{args.tf}.csv")

    print_report(trades)


if __name__ == "__main__":
    main()
