#!/usr/bin/env python3
"""
MR Strategy — Day of Week + Session Analysis
ET HUSDT 5m, 90 days, all days shown (no filtering)
"""

import math
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SYMBOL  = "ETHUSDT"
TF      = "5m"
DAYS    = 90
RISK_USD = 100   # $ risk per trade for P&L calculation
INITIAL_CAPITAL = 10_000

# ─── DATA ─────────────────────────────────────────────────────────────────────
def fetch_klines(symbol: str, interval: str, days: int) -> pd.DataFrame:
    INTERVAL_SECS = {"5m": 300}
    sec = INTERVAL_SECS[interval]
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
        start_ts = batch[-1][0] + sec * 1000
        if len(batch) < 1000:
            break

    df = pd.DataFrame(all_klines, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ["open","high","low","close","volume","trades"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df[["open_time","open","high","low","close","volume","trades"]].copy()
    df = df.set_index("open_time")
    df = df[~df.index.duplicated(keep="first")]
    print(f"  Fetched {len(df):,} candles: {df.index[0].date()} → {df.index[-1].date()}")
    return df

# ─── INDICATORS ───────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    lb = 20

    # Range
    df["range_high"] = df["high"].rolling(lb).max()
    df["range_low"]  = df["low"].rolling(lb).min()
    df["range_width"] = (df["range_high"] - df["range_low"]) / df["close"]
    df["in_range"]    = df["range_width"] < 0.03

    # Volume
    df["vol_ma"] = df["volume"].rolling(lb).mean()
    df["vol_ok"] = df["volume"] <= df["vol_ma"]

    # ATR
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()

    # Swing highs/lows (vectorised, shifted to avoid lookahead)
    roll_h = df["high"].rolling(7, center=True).max()
    roll_l = df["low"].rolling(7, center=True).min()
    df["is_sh"] = (df["high"] == roll_h) & (df["high"] > df["close"])
    df["is_sl"] = (df["low"]  == roll_l)  & (df["low"]  < df["close"])

    # Failed breakouts
    df["fb_long"]  = (df["in_range"].shift(1)) & (df["close"].shift(1) > df["range_high"].shift(1)) & (df["close"] < df["range_high"]) & df["vol_ok"]
    df["fb_short"] = (df["in_range"].shift(1)) & (df["close"].shift(1) < df["range_low"].shift(1))  & (df["close"] > df["range_low"])  & df["vol_ok"]

    # Fast spike reversal
    df["avg_range"]  = (df["high"] - df["low"]).rolling(lb).mean()
    df["spike_mult"] = (df["high"] - df["low"]) / df["avg_range"]
    df["is_spike"]   = (df["spike_mult"] > 3.0) & ((df["high"] - df["low"]) > df["close"] * 0.01)
    df["spike_up"]   = df["is_spike"] & (df["close"] >= df["open"])
    df["spike_down"] = df["is_spike"] & (df["close"] <= df["open"])

    spike_window = 15
    df["spike_up_rolling"]   = df["spike_up"].rolling(spike_window).sum().shift(1) > 0
    df["spike_down_rolling"] = df["spike_down"].rolling(spike_window).sum().shift(1) > 0

    # prev SL/SH (shifted)
    df["prev_sl_hist"] = df["low"].shift(1).rolling(lb).min().shift(1)
    df["prev_sh_hist"] = df["high"].shift(1).rolling(lb).max().shift(1)

    df["spike_rev_long"]  = df["spike_down_rolling"] & df["is_sl"] & (df["close"] > df["low"].shift(1))
    df["spike_rev_short"] = df["spike_up_rolling"]   & df["is_sh"] & (df["close"] < df["high"].shift(1))

    # Regime score
    df["regime_score"] = (
        (df["is_spike"] | df["in_range"]).astype(int) +
        df["vol_ok"].astype(int) +
        df["in_range"].astype(int)
    )

    return df

# ─── BACKTEST ────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame) -> pd.DataFrame:
    LOOKBACK = 25
    RISK_R   = 1.0   # FB/partial
    SPIKE_R  = 1.5

    trades = []
    in_pos = None

    for i, (ts, row) in enumerate(df.iterrows()):
        if i < LOOKBACK:
            continue

        # ── Check open position ───────────────────────────────────────────────
        if in_pos is not None:
            pnl, exit_reason, exit_price = 0, None, None
            if in_pos["side"] == "long":
                hit_sl = row["low"]  <= in_pos["sl"]
                hit_tp = row["high"] >= in_pos["tp"]
            else:
                hit_sl = row["high"] >= in_pos["sl"]
                hit_tp = row["low"]  <= in_pos["tp"]

            if hit_sl:
                pnl = -1.0; exit_reason = "SL"; exit_price = in_pos["sl"]
            elif hit_tp:
                pnl = in_pos["r_mult"]; exit_reason = "TP"; exit_price = in_pos["tp"]

            if pnl != 0:
                risk_eth = abs(in_pos["entry"] - in_pos["sl"])
                trades.append({
                    "entry_time":   in_pos["ts"],
                    "exit_time":    ts,
                    "side":         in_pos["side"],
                    "entry_price":  in_pos["entry"],
                    "exit_price":   exit_price,
                    "setup":        in_pos["setup"],
                    "setup_type":   in_pos["setup_type"],
                    "regime_score": in_pos["regime_score"],
                    "pnl_r":        pnl,
                    "exit_reason":  exit_reason,
                    "risk_eth":     risk_eth,
                    "pnl_usd":      pnl * RISK_USD,
                    "day":          in_pos["ts"].day_name(),
                    "hour_utc":     in_pos["ts"].hour,
                })
                in_pos = None
            continue

        # ── Entry signals ─────────────────────────────────────────────────────
        regime = int(row["regime_score"])
        if regime < 2:
            continue

        entry = row["close"]
        side, sl, tp, setup, stype, r_mult = None, None, None, None, None, None

        if row["fb_long"] and regime >= 2:
            side, sl, setup, stype, r_mult = "long", row["range_low"], "Failed Breakout Long", "fb", RISK_R
            tp = row["range_high"]

        elif row["fb_short"] and regime >= 2:
            side, sl, setup, stype, r_mult = "short", row["range_high"], "Failed Breakout Short", "fb", RISK_R
            tp = row["range_low"]

        elif row["spike_rev_long"] and regime >= 2:
            side, sl, setup, stype, r_mult = "long", row["low"], "Spike Rev Long", "spike", SPIKE_R
            tp = entry + (entry - sl) * SPIKE_R

        elif row["spike_rev_short"] and regime >= 2:
            side, sl, setup, stype, r_mult = "short", row["high"], "Spike Rev Short", "spike", SPIKE_R
            tp = entry - (sl - entry) * SPIKE_R

        if side and sl and tp:
            if side == "long"  and (sl >= entry or tp <= entry): continue
            if side == "short" and (sl <= entry or tp >= entry): continue
            in_pos = {
                "ts": ts, "side": side, "entry": entry, "sl": sl, "tp": tp,
                "setup": setup, "setup_type": stype, "regime_score": regime, "r_mult": r_mult
            }

    return pd.DataFrame(trades)

# ─── ANALYSE ─────────────────────────────────────────────────────────────────
def analyse(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"error": "No trades"}

    trades = trades.sort_values("entry_time").reset_index(drop=True)

    # Equity curve
    equity = INITIAL_CAPITAL
    eq_curve = [INITIAL_CAPITAL]
    eq_ts = [trades["entry_time"].iloc[0]]

    rows = []
    for _, t in trades.iterrows():
        equity += t["pnl_usd"]
        eq_curve.append(equity)
        eq_ts.append(t["exit_time"])
        rows.append({**t.to_dict(), "equity": equity})

    trades = pd.DataFrame(rows)

    # Overall stats
    n = len(trades)
    wins  = (trades["pnl_r"] > 0).sum()
    loss  = (trades["pnl_r"] <= 0).sum()
    wr    = wins / n * 100
    avg_r = trades["pnl_r"].mean()
    total_r = trades["pnl_r"].sum()

    # Max DD
    eq_s = pd.Series(eq_curve)
    mdd  = ((eq_s - eq_s.cummax()) / eq_s.cummax() * 100).min()

    # By day
    days_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    by_day = {}
    for day in days_order:
        sub = trades[trades["day"] == day]
        if len(sub) == 0:
            by_day[day] = None
            continue
        w = (sub["pnl_r"] > 0).sum()
        by_day[day] = {
            "count": len(sub), "wins": int(w), "losses": int(len(sub)-w),
            "wr": w/len(sub)*100,
            "avg_r": sub["pnl_r"].mean(),
            "total_r": sub["pnl_r"].sum(),
            "pnl_usd": sub["pnl_usd"].sum(),
            "setup_breakdown": sub.groupby("setup_type")["pnl_r"].agg(["count","mean","sum"]).to_dict("index")
        }

    # By setup
    by_setup = {}
    for stype in trades["setup_type"].unique():
        sub = trades[trades["setup_type"] == stype]
        w = (sub["pnl_r"] > 0).sum()
        by_setup[stype] = {
            "count": len(sub), "wins": int(w),
            "wr": w/len(sub)*100,
            "avg_r": sub["pnl_r"].mean(),
            "total_r": sub["pnl_r"].sum(),
        }

    # By session (UTC hour)
    def session(h):
        if 0 <= h < 8:   return "Asian (00-08)"
        if 8 <= h < 12:  return "London AM (08-12)"
        if 12 <= h < 16: return "London PM (12-16)"
        return "NY (16-21)"

    trades["session"] = trades["hour_utc"].apply(session)
    by_session = {}
    for sess in ["Asian (00-08)","London AM (08-12)","London PM (12-16)","NY (16-21)"]:
        sub = trades[trades["session"] == sess]
        if len(sub) == 0:
            by_session[sess] = None
            continue
        w = (sub["pnl_r"] > 0).sum()
        by_session[sess] = {
            "count": len(sub), "wins": int(w), "losses": int(len(sub)-w),
            "wr": w/len(sub)*100,
            "avg_r": sub["pnl_r"].mean(),
            "total_r": sub["pnl_r"].sum(),
        }

    return {
        "symbol": SYMBOL, "interval": TF, "days": DAYS,
        "n_trades": n, "n_wins": int(wins), "n_losses": int(loss),
        "win_rate": wr, "avg_r": avg_r, "total_r": total_r,
        "final_equity": equity, "total_pnl_usd": equity - INITIAL_CAPITAL,
        "max_drawdown_pct": mdd,
        "equity_curve": eq_curve, "equity_ts": eq_ts,
        "trades": trades,
        "by_day": by_day,
        "by_setup": by_setup,
        "by_session": by_session,
    }

# ─── PRINT ───────────────────────────────────────────────────────────────────
def print_report(stats: dict):
    print(f"\n{'='*65}")
    print(f"  MR BACKTEST — {stats['symbol']} {stats['interval']} ({stats['days']} days)")
    print(f"  All days shown — no filtering")
    print(f"{'='*65}")

    print(f"\n  OVERALL ({stats['n_trades']} trades)")
    print(f"  {'Win Rate:':<20} {stats['win_rate']:.1f}%  ({stats['n_wins']}W / {stats['n_losses']}L)")
    print(f"  {'Avg R:':<20} {stats['avg_r']:+.2f}R")
    print(f"  {'Total R:':<20} {stats['total_r']:+.2f}R")
    print(f"  {'Total P&L:':<20} ${stats['total_pnl_usd']:+,.2f}")
    print(f"  {'Max Drawdown:':<20} {stats['max_drawdown_pct']:.1f}%")
    print(f"  {'Final Equity:':<20} ${stats['final_equity']:,.2f}")

    # By setup
    print(f"\n  BY SETUP")
    for stype, s in stats["by_setup"].items():
        label = stype.upper()
        print(f"  {label:<10} {s['count']:>3} trades   WR {s['wr']:.0f}%   Avg {s['avg_r']:+.2f}R   Total {s['total_r']:+.2f}R")

    # Day of week
    print(f"\n  BY DAY OF WEEK")
    print(f"  {'Day':<12} {'Trades':>6} {'WR':>6} {'Avg R':>8} {'Total R':>10} {'P&L':>12}  Verdict")
    print(f"  {'-'*65}")
    day_verdicts = {}
    all_avg_r = [v["avg_r"] for v in stats["by_day"].values() if v]
    overall_avg = sum(all_avg_r) / len(all_avg_r) if all_avg_r else 0
    for day, v in stats["by_day"].items():
        if v is None:
            print(f"  {day:<12} {'—':>6}")
            continue
        c = v["count"]
        wr = v["wr"]
        avg_r = v["avg_r"]
        total_r = v["total_r"]
        pnl = v["pnl_usd"]
        if avg_r >= 0.8 and wr >= 75:    verdict = "STRONG — full size"
        elif avg_r >= 0.5 and wr >= 65:  verdict = "solid — good"
        elif avg_r >= 0.3:               verdict = "okay"
        elif avg_r >= 0:                 verdict = "flat"
        elif avg_r >= -0.3:              verdict = "weak"
        else:                            verdict = "AVOID"
        day_verdicts[day] = verdict
        print(f"  {day:<12} {c:>6} {wr:>5.0f}% {avg_r:>+8.2f}R {total_r:>+10.2f}R ${pnl:>+11,.2f}  {verdict}")

    # Session
    print(f"\n  BY SESSION (UTC)")
    print(f"  {'Session':<20} {'Trades':>6} {'WR':>6} {'Avg R':>8} {'Total R':>10}")
    print(f"  {'-'*55}")
    for sess, v in stats["by_session"].items():
        if v is None:
            print(f"  {sess:<20} {'—':>6}")
            continue
        c = v["count"]
        wr = v["wr"]
        avg_r = v["avg_r"]
        total_r = v["total_r"]
        print(f"  {sess:<20} {c:>6} {wr:>5.0f}% {avg_r:>+8.2f}R {total_r:>+10.2f}R")

    # Equity curve (text)
    eq = pd.Series(stats["equity_curve"])
    if len(eq) > 2:
        n_pts = 40
        step = max(1, len(eq) // n_pts)
        seq = eq.iloc[::step]
        min_v, max_v = seq.min(), seq.max()
        h = 8
        rows = [[" "] * len(seq) for _ in range(h + 1)]
        for j, v in enumerate(seq):
            bar = int((v - min_v) / (max_v - min_v + 1e-9) * h)
            bar = min(h, max(0, bar))
            for ri in range(bar, h + 1):
                rows[h - ri][j] = "█"
        print(f"\n  EQUITY CURVE")
        print(f"  ${max_v:,.0f}")
        for row in rows:
            print(f"  |" + "".join(row))
        print(f"  +" + "-" * len(rows[0]) + f"+")
        print(f"  ${min_v:,.0f}")

    print(f"\n{'='*65}")
    return stats

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\nFetching {SYMBOL} {TF} data ({DAYS} days)...")
    df = fetch_klines(SYMBOL, TF, DAYS)
    print("Computing indicators...")
    df = compute_indicators(df)
    print("Running backtest...")
    trades = run_backtest(df)
    print(f"  {len(trades)} trades generated")
    print("Analysing...")
    stats = analyse(trades)
    print_report(stats)

    # Save CSV
    out = f"/home/openclaw/workspace/strategies/mr_day_of_week_{SYMBOL}_{TF}.csv"
    stats["trades"].to_csv(out, index=False)
    print(f"\n  Trades saved to: {out}")
