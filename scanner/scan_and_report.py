#!/usr/bin/env python3
"""
Full market scan: Binance data + optional Velo browser capture.
Saves report and optionally sends to Telegram.
Usage:
  python3 scan_and_report.py                   # Binance only
  python3 scan_and_report.py --with-velo       # Binance + Velo browser capture
  python3 scan_and_report.py --telegram        # Binance + Telegram delivery
  python3 scan_and_report.py --with-velo --telegram  # Full scan + Telegram
"""

import sys, os, requests, json
sys.path.insert(0, os.path.dirname(__file__))

from market_scanner import (
    fetch_binance_screener, filter_coins, classify_spikes,
    score_coins, format_report, parse_velo_vision,
    VELO_BIAS_PROMPT, UTC,
)
from datetime import datetime


def velo_capture_and_analyse():
    """
    Opens Velo Market page in browser, extracts Return Buckets and Spaghetti Chart
    data from the browser snapshot (no vision/LLM needed).
    
    Spicy's Velo methodology:
      - Return Buckets → directional bias (BULLISH/BEARISH/MIXED)
      - Spaghetti Chart → pattern classification + parabolic coin detection
    
    Returns (velo_bias_dict, spaghetti_notes_str)
    """
    try:
        import sys
        sys.path.insert(0, "/home/openclaw/.hermes/hermes-agent")
        from tools.browser_tool import browser_navigate, browser_snapshot
    except Exception as e:
        print(f"Browser tools not available — skipping Velo capture ({e})")
        return None, None

    print("  Opening Velo Market page...")
    try:
        browser_navigate(url="https://velo.xyz/market")
    except Exception as e:
        print(f"  Browser navigate failed: {e}")
        return None, None

    import time
    time.sleep(4)  # let charts render

    # Get snapshot — contains all chart data in text form
    # browser_snapshot() returns a JSON string: {"success": true, "snapshot": "..."}
    print("  Capturing page snapshot...")
    try:
        import json as _json
        raw = browser_snapshot()
        if isinstance(raw, str):
            parsed = _json.loads(raw)
            snapshot_text = parsed.get("snapshot", "")
        elif isinstance(raw, dict):
            snapshot_text = raw.get("snapshot", str(raw))
        else:
            snapshot_text = str(raw)
    except Exception as e:
        print(f"  Browser snapshot failed: {e}")
        return None, None

    # ── Parse Return Buckets from snapshot text ──────────────────────────────
    # Snapshot contains: img "1w Return Buckets": ... -15% -10% -5% <0% >0% +5% +10% +15% N N N N
    # Bucket bar heights are proportional to coin counts
    print("  Parsing Return Buckets...")
    velo_bias = parse_velo_snapshot(snapshot_text)

    # ── Parse Spaghetti Chart from snapshot text ─────────────────────────────
    # Snapshot contains: img "Price Changes": RAVE 币安人生 BLESS ... y-axis labels
    # Y-axis range tells us which coins are parabolic
    print("  Parsing Spaghetti Chart...")
    spaghetti_text = parse_spaghetti_snapshot(snapshot_text)

    return velo_bias, spaghetti_text


def parse_velo_snapshot(snap_text: str) -> dict:
    """
    Parse Return Buckets bias directly from browser snapshot text.
    
    Snapshot format (line 37 example):
      img "1w Return Buckets": 1w Return Buckets -15% -10% -5% <0% >0% +5% +10% +15% 100 50 0 50 100 150 200
    
    First 8 tokens = bucket labels (left to right: -15%, -10%, -5%, <0%, >0%, +5%, +10%, +15%)
    Next 8 numbers   = bar heights (coin counts per bucket)
    
    Spicy's interpretation:
      - Heavy green right tail  → BULLISH bias  → prefer LONG reversals
      - Heavy red left tail     → BEARISH bias → prefer SHORT reversals
      - Balanced distribution   → MIXED         → trade both directions
    """
    import re
    
    lines = snap_text.split('\n')
    bucket_line = ""
    for line in lines:
        if '1w return buckets' in line.lower():
            bucket_line = line
            break
    
    if not bucket_line:
        return {"bias": "MIXED", "strength": "MILD", "raw": "Could not parse Return Buckets from snapshot"}
    
    # Extract all tokens from the line
    tokens = bucket_line.split()
    
    # Find where the numbers start (they follow the bucket labels)
    # Bucket labels end at +15%, then numbers start
    # Tokens look like: ['img', '"1w', 'Return', 'Buckets":', '-15%', '-10%', '-5%', '<0%', '>0%', '+5%', '+10%', '+15%', '100', '50', '0', '50', '100', '150', '200']
    # Find the index of the first numeric token
    num_start_idx = None
    for i, tok in enumerate(tokens):
        # Strip colons and quotes then check if it's a number
        clean = tok.rstrip(':").')
        if clean.lstrip('-').isdigit():
            num_start_idx = i
            break
    
    if num_start_idx is None:
        return {"bias": "MIXED", "strength": "MILD", "raw": "Could not locate bucket bar heights"}
    
    label_tokens = tokens[num_start_idx - 8:num_start_idx]  # 8 bucket labels
    bar_tokens   = tokens[num_start_idx:num_start_idx + 8]  # 8 bar heights
    
    try:
        bars = [int(t.rstrip(':").')) for t in bar_tokens]
        # labels: -15%, -10%, -5%, <0%, >0%, +5%, +10%, +15%
        # bars:   [100,   50,    0,   50,  50, 100,  150,  200]
        neg_sum = sum(bars[:4])  # negative side total
        pos_sum = sum(bars[4:])  # positive side total
    except (ValueError, IndexError):
        return {"bias": "MIXED", "strength": "MILD", "raw": f"Error parsing bars: {bar_tokens}"}
    
    # Determine bias and strength
    total = neg_sum + pos_sum
    if total == 0:
        return {"bias": "MIXED", "strength": "MILD", "raw": "No bucket data available"}
    
    pos_ratio = pos_sum / total
    
    # Find tail extreme — highest bar on positive side
    pos_bars = dict(zip(['>0%', '+5%', '+10%', '+15%'], bars[4:]))
    neg_bars = dict(zip(['-15%', '-10%', '-5%', '<0%'], bars[:4]))
    tail_extreme = max(pos_bars, key=pos_bars.get) if pos_sum > neg_sum else max(neg_bars, key=neg_bars.get)
    
    if pos_ratio >= 0.75:
        bias = "BULLISH"
        strength = "EXTREME" if pos_ratio >= 0.85 else "MODERATE"
        raw = (f"Heavy BULLISH — positive bars ({pos_sum}) dominate negative ({neg_sum}). "
               f"Largest bucket: {tail_extreme}")
    elif pos_ratio <= 0.25:
        bias = "BEARISH"
        strength = "EXTREME" if pos_ratio <= 0.15 else "MODERATE"
        raw = (f"Heavy BEARISH — negative bars ({neg_sum}) dominate positive ({pos_sum}). "
               f"Largest bucket: {tail_extreme}")
    else:
        bias = "MIXED"
        strength = "MILD" if 0.35 <= pos_ratio <= 0.65 else "MODERATE"
        raw = f"Balanced — neg {neg_sum} vs pos {pos_sum} (pos ratio {pos_ratio:.0%})"
    
    return {"bias": bias, "strength": strength, "raw": raw}


def parse_spaghetti_snapshot(snap_text: str) -> str:
    """
    Parse Spaghetti Chart (Price Changes) from browser snapshot text.
    
    Snapshot format (line 45 example):
      img "Price Changes": Price Changes RAVE 币安人生 BLESS ENJ AKE AIOT GIGGLE MAGMA CHILLGUY SKYAI
                           8 Apr 9 Apr ... 14 Apr -1000% 0% 1000% 2000% 3000% 4000% 5000% 6000%
    
    Coin names come first (top gainers), then dates, then y-axis labels.
    Y-axis max tells us how parabolic the move is:
      >1000%  = PARABOLIC (AVOID reversal — staircase breakout)
      200-1000% = Choppy Uptrend (reversal longs preferred)
      <200%   = Choppy Range (BEST for reversals)
    
    Spicy's pattern rules:
      - Staircase (clean breaks, repeated tags) → AVOID reversals
      - Choppy Uptrend → Reversal longs preferred
      - Choppy Downtrend → Reversal shorts preferred
      - Choppy Range → BEST for mean reversion (both directions)
    """
    import re
    
    lines = snap_text.split('\n')
    price_line = ""
    for line in lines:
        if 'price changes' in line.lower() and 'return bucket' not in line.lower():
            price_line = line
            break
    
    if not price_line:
        return "Could not parse Spaghetti Chart from snapshot"
    
    tokens = price_line.split()
    
    # Extract coin names — they appear between "Price Changes" and the date tokens (months + days)
    # Dates look like "8", "Apr", "9", "Apr", ... 
    # We stop at the first token that looks like a date (month name)
    MONTHS = {'Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'}
    coin_names = []
    in_coins = False
    for tok in tokens:
        clean = tok.rstrip(':").')
        if clean == 'Changes':
            in_coins = True
            continue
        if clean in MONTHS:
            break
        if in_coins and clean not in ('Price', 'Changes'):
            # Strip quotes
            coin_names.append(clean.strip('"'))
    
    # Extract y-axis range — look for the last large numbers before end of line
    # Y-axis labels come after dates: -1000% 0% 1000% 2000% 3000% 4000% 5000% 6000%
    all_nums = re.findall(r'-?\d+%?', price_line)
    yaxis_nums = []
    for n in all_nums:
        stripped = n.rstrip('%')
        try:
            yaxis_nums.append(abs(int(stripped)))
        except ValueError:
            pass
    
    yaxis_max = max(yaxis_nums) if yaxis_nums else 0
    
    # Classify by parabolicity
    if yaxis_max > 1000:
        classification = "PARABOLIC"
        recommendation = "AVOID reversal trades — staircase breakout in progress"
        emoji = "⚠️"
    elif yaxis_max > 200:
        classification = "CHOPPY UPTREND"
        recommendation = "Reversal longs preferred at support"
        emoji = "↗️"
    else:
        classification = "CHOPPY RANGE"
        recommendation = "Reversal longs AND shorts viable — BEST environment"
        emoji = "↔️"
    
    notes = [
        "Spaghetti Chart Analysis (Velo Market — 1w timeframe):",
        "",
        f"{emoji}  Classification: {classification}",
        f"   Coins shown: {', '.join(coin_names[:10])}",
        f"   Y-axis max: {'+' if yaxis_max > 0 else ''}{yaxis_max}%",
        f"   {recommendation}",
        "",
        "Spicy pattern rules:",
        "  Upstairs/Downstairs Staircase → AVOID reversals (trend continuation)",
        "  Choppy Uptrend → Reversal longs preferred",
        "  Choppy Downtrend → Reversal shorts preferred",
        "  Choppy Range → BEST for mean reversion (both directions)",
        f"  Parabolic y-axis ({yaxis_max}%) → AVOID — staircase mode",
    ]
    
    return '\n'.join(notes)


def send_telegram(message: str, chat_id: str = None):
    """Send message via Telegram bot."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("TELEGRAM_BOT_TOKEN not set — skipping Telegram delivery")
        return
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        print("TELEGRAM_CHAT_ID not set — skipping")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Telegram max message length is 4096
    for i in range(0, len(message), 4000):
        chunk = message[i:i+4000]
        r = requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"})
        if r.status_code != 200:
            print(f"Telegram error: {r.text[:200]}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-velo", action="store_true", help="Capture Velo charts via browser")
    parser.add_argument("--telegram", action="store_true", help="Deliver report to Telegram")
    parser.add_argument("--min-vol", type=float, default=0.5)
    parser.add_argument("--min-trades", type=int, default=5000)
    args = parser.parse_args()

    print("=" * 60)
    print("MEAN REVERSION MARKET SCANNER")
    print(datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 60)

    # 1. Velo capture
    velo_bias = None
    spaghetti = None
    if args.with_velo:
        velo_bias, spaghetti = velo_capture_and_analyse()

    # 2. Binance scan
    print("\nFetching Binance futures data...")
    df = fetch_binance_screener()
    print(f"  {len(df)} symbols loaded")

    df = filter_coins(df, min_volume_5m=args.min_vol * 1e6, min_trades_5m=args.min_trades)
    print(f"  {len(df)} pass liquidity filters")

    df = classify_spikes(df)
    df = score_coins(df)

    # 3. Format report
    report = format_report(
        df,
        velo_bias=velo_bias,
        velo_spaghetti=spaghetti,
    )

    print("\n" + report)

    # 4. Deliver
    if args.telegram:
        print("\nSending to Telegram...")
        send_telegram(report)
        print("Done.")

    # 5. Save
    out_path = "/home/openclaw/workspace/scanner/latest_scan.txt"
    with open(out_path, "w") as f:
        f.write(report)
    print(f"\nSaved to {out_path}")
