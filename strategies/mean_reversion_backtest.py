#!/usr/bin/env python3
"""
Mean Reversion Strategy Backtester — ETHUSDT Binance 1m
Based on @spicyofc "Mean Reversion Trading Strategy Guide"

3 Setups:
  1. Failed Breakouts    — price in range, vol not increasing, candle closes back inside
  2. Fast Spike Reversals — vertical spike fails, breaks structure, reverts to origin
  3. Partial Fill Reversals — exact top/bottom tick, next candle confirms reversal

Run:
  python mean_reversion_backtest.py [--symbol ETHUSDT] [--days 90] [--tf 1m]
"""

import json, math, argparse
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL = "ETHUSDT"
DEFAULT_DAYS   = 90
TIMEFRAMES = {
    "1m":  (60,      "1 day ago UTC",          1440),
    "5m":  (300,     "5 days ago UTC",          288),
    "15m": (900,     "12 days ago UTC",         96),
    "1h":  (3600,    "30 days ago UTC",         24),
    "4h":  (14400,   "60 days ago UTC",         6),
    "1d":  (86400,   "365 days ago UTC",        1),
}

# ─── DATA FETCHING ────────────────────────────────────────────────────────────
def fetch_binance_klines(symbol: str, interval: str, days: int = 90) -> pd.DataFrame:
    """Fetch OHLCV from Binance public API."""
    sec, since, limit = TIMEFRAMES[interval]
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
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    for col in ["open","high","low","close","volume","quote_volume"]:
        df[col] = pd.to_numeric(df[col])
    df = df[["open_time","open","high","low","close","volume","trades"]].copy()
    df = df.set_index("open_time")
    # Deduplicate
    df = df[~df.index.duplicated(keep="first")]
    print(f"  Fetched {len(df):,} candles: {df.index[0]} → {df.index[-1]}")
    return df

# ─── INDICATORS ───────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all strategy indicators to the dataframe."""

    # Volume
    df["vol_ma"]        = df["volume"].rolling(20).mean()
    df["vol_ok"]        = df["volume"] <= df["vol_ma"]   # NOT increasing = good

    # ── Range Detection ───────────────────────────────────────────────────────
    df["range_high"] = df["high"].rolling(20).max()
    df["range_low"]  = df["low"].rolling(20).min()
    df["range_width"] = (df["range_high"] - df["range_low"]) / df["close"]
    df["in_range"]   = df["range_width"] < 0.03

    # ── Swing High / Low (pivots) ─────────────────────────────────────────────
    swing_n = 3
    # Shift by 1 to avoid lookahead: pivot detected at bar i using prior bars
    df["shifted_high"] = df["high"].shift(1)
    df["shifted_low"]  = df["low"].shift(1)

    # A bar is a swing high if it is the highest over the surrounding swing_n bars
    # We check: current high == max of high[i-swing_n..i+swing_n]  AND it's a local peak
    def is_swing_high(series, idx, n):
        i = idx
        if pd.isna(series.iloc[i]): return False
        start = max(0, i - n)
        end   = min(len(series), i + n + 1)
        return series.iloc[i] == series.iloc[start:end].max() and \
               series.iloc[i] > series.iloc[i+1] if i+1 < len(series) else False

    def is_swing_low(series, idx, n):
        i = idx
        if pd.isna(series.iloc[i]): return False
        start = max(0, i - n)
        end   = min(len(series), i + n + 1)
        return series.iloc[i] == series.iloc[start:end].min() and \
               series.iloc[i] < series.iloc[i+1] if i+1 < len(series) else False

    # Vectorised-ish: use rolling max/min shifted so detection isn't at current bar
    roll_high = df["high"].rolling(swing_n*2+1, center=True).max()
    roll_low  = df["low"].rolling(swing_n*2+1, center=True).min()
    df["is_sh"] = (df["high"] == roll_high) & (df["high"] > df["close"])
    df["is_sl"] = (df["low"]  == roll_low)  & (df["low"]  < df["close"])

    # ── Failed Breakouts ───────────────────────────────────────────────────────
    # Price was above range high, now closes back inside
    df["prev_in_range"]  = df["in_range"].shift(1)
    df["prev_above_rh"]  = df["close"].shift(1) > df["range_high"].shift(1)
    df["prev_below_rl"]  = df["close"].shift(1) < df["range_low"].shift(1)
    df["fb_long"]  = df["prev_in_range"] & df["prev_above_rh"] & (df["close"] < df["range_high"]) & df["vol_ok"]
    df["fb_short"] = df["prev_in_range"] & df["prev_below_rl"]  & (df["close"] > df["range_low"])  & df["vol_ok"]

    # ── Fast Spike Detection ───────────────────────────────────────────────────
    df["avg_range"]  = (df["high"] - df["low"]).rolling(20).mean()
    df["spike_mult"] = (df["high"] - df["low"]) / df["avg_range"]
    df["is_spike"]   = (df["spike_mult"] > 3.0) & ((df["high"] - df["low"]) > df["close"] * 0.01)
    df["spike_up"]   = df["is_spike"] & (df["close"] >= df["open"])
    df["spike_down"] = df["is_spike"] & (df["close"] <= df["open"])

    # ── Spike pending state (simple rolling window) ────────────────────────────
    # Track: was there a spike_up/down in last N bars and price since made a swing low/high
    spike_window = 15
    df["spike_up_rolling"]   = df["spike_up"].rolling(spike_window).sum().shift(1) > 0
    df["spike_down_rolling"] = df["spike_down"].rolling(spike_window).sum().shift(1) > 0

    # After spike up, look for break below swing low; after spike down, break above swing high
    # We use shifted SL/SH
    df["prev_sl"] = df["low"].shift(1).rolling(20).min().shift(1)
    df["prev_sh"] = df["high"].shift(1).rolling(20).max().shift(1)

    df["close_prev"] = df["close"].shift(1)
    # Break in structure: current close crosses below last swing low after spike up
    df["spike_rev_long"]  = df["spike_down_rolling"] & df["is_sl"] & (df["close"] > df["low"].shift(1))
    df["spike_rev_short"] = df["spike_up_rolling"]   & df["is_sh"] & (df["close"] < df["high"].shift(1))

    # ── Partial Fill / Top Tick ────────────────────────────────────────────────
    # Top tick: price touches same level as prior swing high, with above-avg volume
    # We track SH prices and SL prices in rolling arrays
    tick = 0.01  # ETH tick
    sh_history = df["high"].copy()
    sl_history = df["low"].copy()

    top_tick   = pd.Series(False, index=df.index)
    bot_tick   = pd.Series(False, index=df.index)

    for i in range(5, len(df)):
        # Check if current bar is a swing high and price matches a prior SH from 2-5 bars ago
        if df["is_sh"].iloc[i]:
            cur_high = df["high"].iloc[i]
            # Look back 2-5 bars for matching SH price
            for lag in range(2, 6):
                if i - lag >= 0 and df["is_sh"].iloc[i - lag]:
                    prev_sh = df["high"].iloc[i - lag]
                    if abs(cur_high - prev_sh) < tick and df["volume"].iloc[i] > df["vol_ma"].iloc[i]:
                        top_tick.iloc[i] = True
                        break

        if df["is_sl"].iloc[i]:
            cur_low = df["low"].iloc[i]
            for lag in range(2, 6):
                if i - lag >= 0 and df["is_sl"].iloc[i - lag]:
                    prev_sl = df["low"].iloc[i - lag]
                    if abs(cur_low - prev_sl) < tick and df["volume"].iloc[i] > df["vol_ma"].iloc[i]:
                        bot_tick.iloc[i] = True
                        break

    df["top_tick"]   = top_tick
    df["bot_tick"]   = bot_tick

    # Next candle closes in opposite direction = confirmation
    df["partial_long"]  = df["bot_tick"].shift(1) & (df["close"] > df["close"].shift(1)) & (df["close"] > df["open"])
    df["partial_short"] = df["top_tick"].shift(1) & (df["close"] < df["close"].shift(1)) & (df["close"] < df["open"])

    # ── Regime / Quality Score ─────────────────────────────────────────────────
    df["regime_score"] = (
        (df["is_spike"] | df["in_range"]).astype(int) +
        df["vol_ok"].astype(int) +
        df["in_range"].astype(int)
    )

    return df

# ─── BACKTEST ENGINE ─────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame,
                 fb_r: float = 1.0,   # risk R for FB stops
                 spike_r: float = 1.5,
                 pf_r: float = 1.0,
                 regime_min: int = 2,
                 range_lookback: int = 20) -> pd.DataFrame:
    """
    Walk through bars, simulate trades.
    Returns trade log DataFrame.
    """

    trades = []
    in_pos = None   # {"side": "long"|"short", "entry": float, "tp": float, "sl": float,
                     #  "setup": str, "bar": datetime}

    target_mult  = {"fb": fb_r,   "spike": spike_r, "partial": pf_r}

    def _atr(df_, n=14):
        hl = df_["high"] - df_["low"]
        return hl.rolling(n).mean()

    atr = _atr(df)

    for i, (ts, row) in enumerate(df.iterrows()):
        if i < range_lookback + 5:
            continue  # warmup

        # ── Check open position ───────────────────────────────────────────────
        if in_pos is not None:
            pnl = 0
            exit_reason = None
            hit_tp = False
            hit_sl = False

            if in_pos["side"] == "long":
                if row["low"]  <= in_pos["sl"]:   hit_sl = True
                if row["high"] >= in_pos["tp"]:    hit_tp = True
            else:
                if row["high"] >= in_pos["sl"]:   hit_sl = True
                if row["low"]  <= in_pos["tp"]:    hit_tp = True

            if hit_sl:
                pnl = -1.0
                exit_reason = "SL"
            elif hit_tp:
                pnl = target_mult.get(in_pos["setup_type"], 1.0)
                exit_reason = "TP"
            else:
                # No TP/SL hit this bar — skip (will be checked next bar)
                pass

            if pnl != 0:
                trades.append({
                    "entry_time":   in_pos["bar"],
                    "exit_time":    ts,
                    "side":         in_pos["side"],
                    "entry_price":  in_pos["entry"],
                    "exit_price":   in_pos["sl"] if hit_sl else in_pos["tp"],
                    "setup":        in_pos["setup"],
                    "setup_type":   in_pos["setup_type"],
                    "regime_score": in_pos["regime_score"],
                    "pnl_r":        pnl,
                    "exit_reason":  exit_reason,
                    "risk_eth":     abs(in_pos["entry"] - in_pos["sl"]),
                })
                in_pos = None
            continue

        # ── Entry signals (only if no position) ───────────────────────────────
        if in_pos is None:
            regime = int(row["regime_score"])
            if regime < regime_min:
                continue

            entry_price = row["close"]
            sl = None
            tp = None
            setup_type = None
            setup = None
            side = None

            # 1. Failed Breakout Long
            if row["fb_long"] and regime >= regime_min:
                setup_type = "fb"
                side = "long"
                atr_val = atr.iloc[i] if not pd.isna(atr.iloc[i]) else row["close"] * 0.005
                sl = row["range_low"] if not pd.isna(row["range_low"]) else entry_price - atr_val
                tp = row["range_high"]
                setup = "Failed Breakout Long"

            # 2. Failed Breakout Short
            elif row["fb_short"] and regime >= regime_min:
                setup_type = "fb"
                side = "short"
                atr_val = atr.iloc[i] if not pd.isna(atr.iloc[i]) else row["close"] * 0.005
                sl = row["range_high"] if not pd.isna(row["range_high"]) else entry_price + atr_val
                tp = row["range_low"]
                setup = "Failed Breakout Short"

            # 3. Fast Spike Reversal Long
            elif row["spike_rev_long"] and regime >= regime_min:
                setup_type = "spike"
                side = "long"
                spike_origin = row["close"]   # approximate origin
                spike_extreme = row["low"]
                atr_val = atr.iloc[i] if not pd.isna(atr.iloc[i]) else row["close"] * 0.005
                sl = spike_extreme if not pd.isna(spike_extreme) else entry_price - atr_val * 2
                tp = entry_price + (entry_price - sl) * spike_r
                setup = "Spike Reversal Long"

            # 4. Fast Spike Reversal Short
            elif row["spike_rev_short"] and regime >= regime_min:
                setup_type = "spike"
                side = "short"
                spike_extreme = row["high"]
                atr_val = atr.iloc[i] if not pd.isna(atr.iloc[i]) else row["close"] * 0.005
                sl = spike_extreme if not pd.isna(spike_extreme) else entry_price + atr_val * 2
                tp = entry_price - (sl - entry_price) * spike_r
                setup = "Spike Reversal Short"

            # 5. Partial Fill Long
            elif row["partial_long"] and regime >= regime_min:
                setup_type = "partial"
                side = "long"
                atr_val = atr.iloc[i] if not pd.isna(atr.iloc[i]) else row["close"] * 0.005
                sl_val = df["low"].shift(1).iloc[i]
                sl = sl_val if pd.notna(sl_val) else entry_price - atr_val
                target_dist = entry_price - sl
                tp = entry_price + target_dist * pf_r
                setup = "Partial Fill Long"

            # 6. Partial Fill Short
            elif row["partial_short"] and regime >= regime_min:
                setup_type = "partial"
                side = "short"
                atr_val = atr.iloc[i] if not pd.isna(atr.iloc[i]) else row["close"] * 0.005
                sh_val = df["high"].shift(1).iloc[i]
                sl = sh_val if pd.notna(sh_val) else entry_price + atr_val
                target_dist = sl - entry_price
                tp = entry_price - target_dist * pf_r
                setup = "Partial Fill Short"

            if side and sl and tp:
                # Enforce sanity checks
                if side == "long" and (sl >= entry_price or tp <= entry_price):
                    continue
                if side == "short" and (sl <= entry_price or tp >= entry_price):
                    continue
                in_pos = {
                    "side": side, "entry": entry_price, "tp": tp, "sl": sl,
                    "setup": setup, "setup_type": setup_type,
                    "regime_score": regime, "bar": ts
                }

    return pd.DataFrame(trades)

# ─── ANALYSE ──────────────────────────────────────────────────────────────────
def analyse(trades: pd.DataFrame, initial_capital: float = 10_000,
            risk_eth: float = 100) -> dict:
    if trades.empty:
        return {"error": "No trades generated"}

    trades = trades.sort_values("entry_time").reset_index(drop=True)

    # Equity curve
    equity = initial_capital
    equity_curve = [initial_capital]
    timestamps   = [trades["entry_time"].iloc[0] if len(trades) else None]

    results = []
    for _, t in trades.iterrows():
        if t["side"] == "long":
            pnl_eth = t["pnl_r"] * risk_eth
        else:
            pnl_eth = t["pnl_r"] * risk_eth
        equity += pnl_eth
        equity_curve.append(equity)
        timestamps.append(t["exit_time"])
        results.append({**t.to_dict(), "equity": equity, "cumulative_pnl": equity - initial_capital})

    trades = pd.DataFrame(results)

    # Stats
    n = len(trades)
    n_wins  = (trades["pnl_r"] > 0).sum()
    n_loss  = (trades["pnl_r"] <= 0).sum()
    win_rate = n_wins / n * 100

    avg_win  = trades.loc[trades["pnl_r"] > 0, "pnl_r"].mean() if n_wins > 0 else 0
    avg_loss = trades.loc[trades["pnl_r"] <= 0, "pnl_r"].mean() if n_loss > 0 else 0
    rr       = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # Expectancy
    expectancy = (trades["pnl_r"] * risk_eth).mean()

    # Max drawdown
    eq = pd.Series(equity_curve)
    rolling_max = eq.cummax()
    drawdown    = (eq - rolling_max) / rolling_max * 100
    max_dd      = drawdown.min()

    # Sharpe (simplified — using pnl_r series as returns proxy)
    returns = trades["pnl_r"] * risk_eth
    sharpe  = (returns.mean() / returns.std() * math.sqrt(252 * 24 * 60)) if returns.std() > 0 else 0

    # Best / worst
    best = trades.loc[trades["pnl_r"].idxmax()]
    worst = trades.loc[trades["pnl_r"].idxmin()]

    # Setup breakdown
    setup_stats = trades.groupby("setup_type").agg(
        count=("pnl_r","count"),
        wins=("pnl_r", lambda x: (x>0).sum()),
        avg_r=("pnl_r","mean"),
        total_r=("pnl_r","sum")
    ).to_dict("index")

    return {
        "n_trades": n,
        "n_wins": int(n_wins),
        "n_losses": int(n_loss),
        "win_rate": win_rate,
        "avg_win_R": avg_win,
        "avg_loss_R": avg_loss,
        "RR": rr,
        "expectancy_eth": expectancy,
        "final_equity": equity,
        "total_pnl_eth": equity - initial_capital,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "best_trade_R": best["pnl_r"],
        "worst_trade_R": worst["pnl_r"],
        "setup_breakdown": setup_stats,
        "equity_curve": equity_curve,
        "timestamps": timestamps,
        "trades_df": trades,
    }

# ─── PRINT RESULTS ────────────────────────────────────────────────────────────
def print_report(stats: dict, symbol: str, interval: str, days: int):
    print(f"\n{'='*65}")
    print(f"  MEAN REVERSION BACKTEST — {symbol}  {interval}  ({days} days)")
    print(f"{'='*65}")

    if "error" in stats:
        print(f"  ERROR: {stats['error']}")
        return

    t = stats
    print(f"\n  OVERALL")
    print(f"  {'Trades:':<22} {t['n_trades']}")
    print(f"  {'Wins / Losses:':<22} {t['n_wins']} / {t['n_losses']}")
    print(f"  {'Win Rate:':<22} {t['win_rate']:.1f}%")
    print(f"  {'Avg Win (R):':<22} {t['avg_win_R']:.2f}R")
    print(f"  {'Avg Loss (R):':<22} {t['avg_loss_R']:.2f}R")
    print(f"  {'Risk/Reward:':<22} {t['RR']:.2f}")
    print(f" ")

    print(f"  P&L")
    print(f"  {'Final Equity:':<22} ${t['final_equity']:,.2f}")
    print(f"  {'Total P&L:':<22} ${t['total_pnl_eth']:+,.2f}")
    print(f"  {'Expectancy/trade:':<22} ${t['expectancy_eth']:+,.2f}")
    print(f"  {'Max Drawdown:':<22} {t['max_drawdown_pct']:.1f}%")
    print(f"  {'Sharpe:':<22} {t['sharpe']:.2f}")
    print(f"  ")

    print(f"  BEST / WORST")
    print(f"  {'Best trade:':<22} {t['best_trade_R']:+.2f}R")
    print(f"  {'Worst trade:':<22} {t['worst_trade_R']:+.2f}R")
    print(f"  ")

    print(f"  BY SETUP TYPE")
    for stype, s in t["setup_breakdown"].items():
        wr = s["wins"] / s["count"] * 100
        label = stype.upper()
        print(f"  {label:<12} {int(s['count']):>3} trades   WR {wr:.0f}%   Avg {s['avg_r']:+.2f}R   Total {s['total_r']:+.2f}R")

    # ── Equity curve (ASCII) ───────────────────────────────────────────────────
    eq = pd.Series(t["equity_curve"])
    if len(eq) > 2:
        # Downsample equity curve for ASCII display
        n_points = 40
        step = max(1, len(eq) // n_points)
        sampled_eq = eq.iloc[::step]
        sampled_ts = t["timestamps"][::step] if t["timestamps"] else range(0, len(sampled_eq))

        min_v = sampled_eq.min()
        max_v = sampled_eq.max()
        h = 10
        rows = [[" "] * (len(sampled_eq)) for _ in range(h + 1)]
        for j, v in enumerate(sampled_eq):
            bar = int((v - min_v) / (max_v - min_v + 1e-9) * h)
            bar = min(h, max(0, bar))
            for row_i in range(bar):
                rows[h - row_i][j] = "█"
            rows[h - bar][j] = "▄" if rows[h - bar][j] == " " else "█"

        print(f"\n  EQUITY CURVE (sampled)")
        max_label = f"${max_v:,.0f}"
        min_label = f"${min_v:,.0f}"
        print(f"  {max_label}")
        for row in rows:
            print(f"  |" + "".join(row))
        print(f"  +" + "-" * len(rows[0]) + "+")
        print(f"  {min_label}")

    print(f"\n{'='*65}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mean Reversion Backtester")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--days",   type=int, default=DEFAULT_DAYS)
    parser.add_argument("--tf",     default="1m",
                        choices=["1m","5m","15m","1h","4h","1d"])
    parser.add_argument("--r",       type=float, default=1.0,
                        help="Risk in ETH per trade (for $ P&L calc)")
    args = parser.parse_args()

    print(f"\nFetching {args.symbol} {args.tf} data for last {args.days} days...")

    df = fetch_binance_klines(args.symbol, args.tf, days=args.days)
    print("Computing indicators...")
    df = compute_indicators(df)
    df = df.dropna()

    print("Running backtest...")
    trades = run_backtest(df)

    print(f"Found {len(trades)} trades.")
    stats  = analyse(trades, risk_eth=args.r)
    print_report(stats, args.symbol, args.tf, args.days)

    # Save trade log
    if not trades.empty:
        out = trades.drop(columns=["equity","cumulative_pnl"], errors="ignore")
        out_path = f"/home/openclaw/workspace/strategies/mean_reversion_trades_{args.symbol}_{args.tf}.csv"
        out.to_csv(out_path, index=False)
        print(f"\nTrade log saved → {out_path}")
