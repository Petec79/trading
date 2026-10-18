#!/usr/bin/env python3
"""
Crypto Mean Reversion Market Scanner
Combines:
  - Velo.xyz return buckets + spaghetti charts (browser capture + vision)
  - Binance public API (trade count, volume, 24h change, funding)
Outputs a clean morning briefing for mean reversion traders.
"""

import requests
import pandas as pd
import numpy as np
import json
import time
from datetime import datetime, timezone


# ─────────────────────────────────────────────
# 1.  BINANCE DATA  (replaces Orion screener)
# ─────────────────────────────────────────────

def fetch_binance_screener():
    """
    Pulls all Binance futures tickers + funding rates.
    Returns DataFrame with columns:
      symbol, price, change_24h_pct, volume_24h_usdt, trade_count_24h,
      funding_rate, next_funding_time
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
    }

    # 1a. All 24hr tickers
    tickers_resp = requests.get(
        "https://fapi.binance.com/fapi/v1/ticker/24hr",
        headers=headers,
        timeout=15,
    )
    tickers = pd.DataFrame(tickers_resp.json())

    num_cols = ["lastPrice", "priceChange", "priceChangePercent",
                "highPrice", "lowPrice", "volume", "quoteVolume", "count"]
    for c in num_cols:
        tickers[c] = pd.to_numeric(tickers[c], errors="coerce")

    # 1b. All funding rates (single call)
    funding_resp = requests.get(
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        headers=headers,
        timeout=15,
    )
    funding_data = funding_resp.json()
    funding_df = pd.DataFrame(funding_data)
    funding_df["funding_rate_pct"] = funding_df["lastFundingRate"].astype(float) * 100
    funding_df["next_funding_ts"]  = funding_df["nextFundingTime"].astype(float)

    # Merge
    df = tickers.merge(
        funding_df[["symbol", "funding_rate_pct", "next_funding_ts"]],
        on="symbol",
        how="left",
    )

    # Convert count (trade count in last 24h) → approximate 5m trade count
    # 24h / 5min periods = 288
    df["trades_per_5m"] = (df["count"].astype(float) / 288).round(0).astype("Int64")

    # Volume per 5min (rough)
    df["volume_5m_usdt"] = (df["quoteVolume"].astype(float) / 288 / 1e6).round(2)

    df = df.rename(columns={
        "priceChangePercent": "change_24h_pct",
        "quoteVolume":        "volume_24h_usdt",
        "volume":             "volume_24h_base",
        "count":              "trade_count_24h",
    })

    return df[[
        "symbol", "lastPrice", "change_24h_pct",
        "volume_24h_usdt", "volume_5m_usdt",
        "trade_count_24h", "trades_per_5m",
        "funding_rate_pct", "next_funding_ts",
    ]].copy()


def filter_coins(df, min_volume_5m=500_000, min_trades_5m=5_000):
    """Spicy's liquidity + activity filters from Orion."""
    return df[
        (df["volume_5m_usdt"] * 1e6 >= min_volume_5m) &
        (df["trades_per_5m"] >= min_trades_5m)
    ].copy()


def classify_spikes(df, threshold_pct=1.5):
    """Flag coins that moved ≥ threshold in last 5m (approx from 24h change rate)."""
    # Since we only have 24h change, we flag if 24h change is extreme relative to timeframe
    # For proper 5m spikes we'd need real-time; flag very large 24h movers as "exhaustion candidates"
    df["is_spike_coin"] = df["change_24h_pct"].abs() >= threshold_pct * 10
    df["is_extreme"]    = df["change_24h_pct"].abs() >= 20  # very hot coins
    return df


def top_gainers_losers(df, n=10):
    return (
        df.nlargest(n, "change_24h_pct")
        .assign(type="gainer")
        .append(df.nsmallest(n, "change_24h_pct").assign(type="loser"), ignore_index=True)
    )


# ─────────────────────────────────────────────
# 2.  VELO  (manual screenshot + vision)
# ─────────────────────────────────────────────

# VELO_URL = "https://velo.xyz/market"
# Browser captures handled externally; results passed in as args

VELO_BIAS_PROMPT = """
You are a crypto mean reversion trader looking at Velo's Return Buckets chart.

Return a JSON object:
{
  "bias": "BULLISH" | "BEARISH" | "MIXED",
  "strength": "EXTREME" | "MODERATE" | "MILD",
  "description": "1-sentence description of the distribution",
  "bucket_detail": {
    "most_coins": "up by X%" | "down by X%" | "around 0%",
    "tail_extreme": "+15%+" | "-15%-" | "none"
  }
}
"""


def parse_velo_vision(analysis_text: str) -> dict:
    """Parse the vision LLM output into structured data."""
    # Caller passes in the raw analysis; we look for keywords
    text = analysis_text.lower()
    if "strongly bullish" in text or "heavily bullish" in text or "mostly up" in text:
        bias = "BULLISH"
    elif "strongly bearish" in text or "heavily bearish" in text or "mostly down" in text:
        bias = "BEARISH"
    else:
        bias = "MIXED"

    if "extreme" in text or "parabolic" in text:
        strength = "EXTREME"
    elif "significant" in text or "large" in text:
        strength = "MODERATE"
    else:
        strength = "MILD"

    return {"bias": bias, "strength": strength, "raw": analysis_text[:500]}


# ─────────────────────────────────────────────
# 3.  MEAN REVERSION COIN SCORER
# ─────────────────────────────────────────────

def score_coins(df):
    """
    Score each coin 0-100 for mean reversion suitability.
    Based on Spicy's framework:
      - Hot coins (high trade count) → retail FOMO, easier reversals
      - Extreme 24h moves → exhaustion candidates
      - Not in clean breakout (check for >20% move = likely staircase)
      - Funding not excessive
    """
    df = df.copy()

    # Normalise trade count (0-30 points)
    # Convert to float first — quantile() returns float but column may be Int64
    df["trades_per_5m"] = df["trades_per_5m"].astype(float)
    max_trades = df["trades_per_5m"].clip(upper=df["trades_per_5m"].quantile(0.95)).max()
    df["trades_score"]     = (df["trades_per_5m"] / max_trades * 30).clip(0, 30)

    # Volume score (0-20 points)
    max_vol = df["volume_5m_usdt"].quantile(0.95)
    df["vol_score"]         = (df["volume_5m_usdt"] / max_vol * 20).clip(0, 20)

    # Spike candidate score (0-30 points) — big moves = reversal potential
    df["spike_score"]       = df["change_24h_pct"].abs().clip(upper=50) / 50 * 30

    # Funding penalty (>0.1% per 8h = bad if near funding time)
    df["funding_penalty"]   = df["funding_rate_pct"].abs().clip(upper=0.5) / 0.5 * -10
    df["funding_score"]     = (10 + df["funding_penalty"]).clip(0, 10)

    # Anti-pattern penalty — extremely parabolic coins (avoid)
    # Coins up >50% in 24h are likely in staircase — bad for reversal longs
    df["staircase_penalty"] = (df["change_24h_pct"].abs() > 50).astype(float) * -20

    df["mr_score"] = (
        df["trades_score"] +
        df["vol_score"] +
        df["spike_score"] +
        df["funding_score"] +
        df["staircase_penalty"]
    ).clip(0, 100).round(1)

    return df.sort_values("mr_score", ascending=False)


# ─────────────────────────────────────────────
# 4.  FUNDING ALERT
# ─────────────────────────────────────────────

def funding_warning(row, max_risk_pct=0.23, hold_minutes=60):
    """
    Returns a warning string if funding is due within 90 min and larger than 0.1R.
    Spicy's rule: if funding% > 0.1R and funding within 90 min → skip.
    """
    if pd.isna(row["next_funding_ts"]) or pd.isna(row["funding_rate_pct"]):
        return None

    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    mins_until = (row["next_funding_ts"] - now_ms) / 60 / 1000

    if mins_until > 90 or mins_until < 0:
        return None

    # funding as % of notional; compare to max_risk_pct (0.23% = 0.1R if 1R=2.3%)
    if abs(row["funding_rate_pct"]) < max_risk_pct:
        return None

    return (
        f"⚠️  Funding due in {mins_until:.0f} min "
        f"({row['funding_rate_pct']:+.3f}% — exceeds 0.1R, skip if entering)"
    )


# ─────────────────────────────────────────────
# 5.  REPORT FORMATTER
# ─────────────────────────────────────────────

UTC = timezone.utc


def format_report(df: pd.DataFrame, velo_bias: dict = None,
                  velo_spaghetti: str = None,
                  captured_at: datetime = None) -> str:
    captured_at = captured_at or datetime.now(UTC)
    hour = captured_at.hour

    lines = []
    width = 60

    def rule():
        lines.append("─" * width)

    def section(title):
        lines.append(f"\n{'='*width}")
        lines.append(f"  {title}")
        lines.append("=" * width)

    def colored_coin(symbol, change, mr_score):
        emoji = "🔴" if change < 0 else "🟢"
        return f"  {emoji} {symbol:12s}  {change:+8.2f}%  MR Score {mr_score:.0f}/100"

    # ── HEADER ──
    lines.append("""
╔══════════════════════════════════════════════════════════════╗
║         MEAN REVERSION MARKET SCAN          """ + captured_at.strftime("%Y-%m-%d %H:%M UTC") + """ ║
╚══════════════════════════════════════════════════════════════╝""")

    # ── SESSION BIAS ──
    section("SESSION BIAS  (Velo Return Buckets)")
    if velo_bias:
        emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡"}[velo_bias.get("bias", "MIXED")]
        lines.append(f"  {emoji}  {velo_bias.get('bias','MIXED')}  |  {velo_bias.get('strength','MILD')} conviction")
        lines.append(f"  → {velo_bias.get('raw','')[:120]}")
    else:
        lines.append("  🟡  MIXED  |  No Velo data (run with --velo)")

    lines.append(f"\n  Trading direction guidance:")
    if velo_bias:
        b = velo_bias.get("bias", "MIXED")
        if b == "BEARISH":
            lines.append("  → Heavy reds dominant. Prefer SHORT reversals at resistance.")
            lines.append("  → Avoid buying dips — market-wide selling in progress.")
        elif b == "BULLISH":
            lines.append("  → Heavy greens dominant. Prefer LONG reversals at support.")
            lines.append("  → Avoid shorting early — uptrend still intact.")
        else:
            lines.append("  → No strong directional skew. Trade both longs & shorts.")
            lines.append("  → Watch for mean reversion at range edges.")
    lines.append(f"\n  Your backtest best sessions (UTC):")
    lines.append("  • London AM (08-12 UTC):  83% WR — BEST")
    lines.append("  • Asian (00-08 UTC):      91% WR — very strong")
    lines.append("  • London PM (12-16 UTC):  monitor, mixed results")

    # ── FUNDING ALERTS ──
    section("FUNDING ALERTS  (due within 90 min)")
    alerts = []
    for _, row in df.iterrows():
        w = funding_warning(row)
        if w:
            alerts.append(f"  • {row['symbol']:12s}  {row['funding_rate_pct']*100:+.3f}%  →  {w}")
    if alerts:
        lines.extend(alerts[:10])
    else:
        lines.append("  ✅  No high-funding threats in next 90 min")

    # ── COIN RANKINGS ──
    section("TOP COINS FOR MEAN REVERSION  (Binance data)")
    top = df.head(20)
    # Split into potential longs vs shorts
    gainers = top[top["change_24h_pct"] > 0].head(8)
    losers  = top[top["change_24h_pct"] < 0].tail(8).iloc[::-1]

    lines.append("\n  🟢  TOP REVERSAL LONG CANDIDATES  (coins up, look for reversal shorts)")
    lines.append("  " + "─" * 50)
    lines.append(f"  {'Coin':12s}  {'24h %':>8s}  {'MR Score':>10s}  {'5m Vol':>8s}  {'Trades/5m':>10s}")
    for _, r in gainers.iterrows():
        staircase_warn = " ⚠️ PARABOLIC" if r["change_24h_pct"] > 50 else ""
        lines.append(
            f"  {r['symbol']:12s}  {r['change_24h_pct']:+8.2f}%  "
            f"MR {r['mr_score']:5.1f}     ${r['volume_5m_usdt']:6.1f}M  "
            f"{r['trades_per_5m']:>8,}  {staircase_warn}"
        )

    lines.append("\n  🔴  TOP REVERSAL SHORT CANDIDATES  (coins down, look for reversal longs)")
    lines.append("  " + "─" * 50)
    lines.append(f"  {'Coin':12s}  {'24h %':>8s}  {'MR Score':>10s}  {'5m Vol':>8s}  {'Trades/5m':>10s}")
    for _, r in losers.iterrows():
        lines.append(
            f"  {r['symbol']:12s}  {r['change_24h_pct']:+8.2f}%  "
            f"MR {r['mr_score']:5.1f}     ${r['volume_5m_usdt']:6.1f}M  "
            f"{r['trades_per_5m']:>8,}"
        )

    # ── COINS TO AVOID ──
    section("⚠️  COINS TO AVOID  (Staircase / Breakout mode)")
    avoid = top[top["change_24h_pct"].abs() > 50]
    if len(avoid):
        for _, r in avoid.iterrows():
            direction = "LONG" if r["change_24h_pct"] > 0 else "SHORT"
            lines.append(
                f"  🚫 {r['symbol']:12s}  {r['change_24h_pct']:+8.2f}%  "
                f"→ In breakout/staircase mode. DO NOT fade this direction."
            )
    else:
        lines.append("  ✅  No extreme staircase coins detected.")

    # ── SPAGHETTI CHART NOTES ──
    if velo_spaghetti:
        section("SPAGHETTI CHART  (Velo — Price Pattern Classification)")
        lines.append(f"\n  {velo_spaghetti[:400]}")
        lines.append("\n  Pattern key:")
        lines.append("  🟢 Choppy Range        → BEST for mean reversion (both directions)")
        lines.append("  ↗️ Choppy Uptrend      → Reversal longs preferred")
        lines.append("  ↘️ Choppy Downtrend    → Reversal shorts preferred")
        lines.append("  ↑↓ Staircase          → AVOID reversals — trend continuation")

    # ── YOUR BACKTEST BEST DAYS ──
    section("YOUR EDGE BY DAY  (ETHUSDT 5m backtest)")
    lines.append("""
  Day         | Win Rate | Total R  | Verdict
  ------------|----------|----------|------------------
  Thursday    |   89%    |  +8.0R   | ★★★ BEST DAY
  Monday      |   82%    |  +5.5R   | ★★  Strong
  Sunday      |   75%    |  +4.0R   | ★★  Good
  Saturday    |   67%    |  +2.0R   | ★   Decent
  Tuesday     |   61%    |  +1.5R   | —   Average
  Wednesday   |   58%    |  +0.5R   | —   Weak
  Friday      |   43%    |  -2.0R   | ✗   AVOID""")

    # ── RECOMMENDED SESSION TIMES ──
    section("TODAY'S BEST WINDOWS  (based on your backtest)")
    day_name = captured_at.strftime("%A")
    lines.append(f"\n  Today is {day_name}.")
    best_sessions = {
        "Monday":    [("08-12 UTC", "London AM", "83% WR"), ("00-08 UTC", "Asian", "91% WR")],
        "Tuesday":   [("08-12 UTC", "London AM", "83% WR")],
        "Wednesday": [("08-12 UTC", "London AM", "83% WR")],
        "Thursday":  [("00-08 UTC", "Asian", "91% WR"), ("08-12 UTC", "London AM", "83% WR")],
        "Friday":    [("00-08 UTC", "Asian", "91% WR"), ("12-16 UTC", "London PM", "monitor")],
        "Saturday":  [("08-12 UTC", "London AM", "67% WR")],
        "Sunday":    [("00-08 UTC", "Asian", "91% WR"), ("08-12 UTC", "London AM", "75% WR")],
    }
    sessions = best_sessions.get(day_name, [("08-12 UTC", "London AM", "83% WR")])
    for time_utc, name, wr in sessions:
        lines.append(f"  ★  {time_utc:10s}  {name:15s}  {wr}")

    lines.append(f"\n  Current UTC time: {captured_at.strftime('%H:%M')}")
    lines.append(f"  → {'ACTIVE — check Velo bias above' if 0 <= hour < 8 else ''}")
    lines.append(f"  → {'ACTIVE — London session starting' if 8 <= hour < 12 else ''}")
    lines.append(f"  → {'Afternoon — fading, monitor closely' if 12 <= hour < 16 else ''}")
    lines.append(f"  → {'Late session — Asian flush incoming' if 16 <= hour < 24 else ''}")

    lines.append("\n" + "─" * width)
    lines.append("  Scores formula: trades(30) + volume(20) + spike potential(30) + funding(10) - staircase penalty(20)")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# 6.  MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Crypto Mean Reversion Market Scanner")
    parser.add_argument("--velo", action="store_true", help="Launch browser for Velo capture")
    parser.add_argument("--velo-bias", help="Velo bias text from vision analysis")
    parser.add_argument("--velo-spaghetti", help="Velo spaghetti chart text from vision")
    parser.add_argument("--min-vol", type=float, default=0.5,
                        help="Min volume in USDT millions (default 0.5 = $500K)")
    parser.add_argument("--min-trades", type=int, default=5000,
                        help="Min trades per 5min (default 5000)")
    args = parser.parse_args()

    print("Fetching Binance data...")
    df = fetch_binance_screener()
    print(f"  {len(df)} symbols loaded")

    df = filter_coins(df, min_volume_5m=args.min_vol * 1e6, min_trades_5m=args.min_trades)
    print(f"  {len(df)} pass liquidity filters")

    df = classify_spikes(df)
    df = score_coins(df)

    velo_bias = None
    if args.velo_bias:
        try:
            velo_bias = json.loads(args.velo_bias)
        except Exception:
            velo_bias = parse_velo_vision(args.velo_bias)

    report = format_report(
        df,
        velo_bias=velo_bias,
        velo_spaghetti=args.velo_spaghetti,
    )
    print(report)
