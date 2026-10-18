#!/usr/bin/env python3
"""
PICO Exit Monitor — Check pending trades against live prices.
================================================================
Run via cron (hourly) or manually. Fetches current Binance prices
for all open journal trades and checks for SL/TP hits.

Usage:
    python exit_monitor.py              # dry-run: show what would close
    python exit_monitor.py --execute     # actually close trades
    python exit_monitor.py --execute --report   # close + print Telegram report
"""
import argparse
import json
import requests
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
sys.path.insert(0, str(WORKSPACE / "journal"))
sys.path.insert(0, str(WORKSPACE / "scanner"))

from journal import Journal

BINANCE_URL = "https://fapi.binance.com"

# Discord webhooks
DISCORD_JOURNAL_WH = "https://discord.com/api/webhooks/1506298466375045291/4dsrRrdlhKtUHZsE11iyKRvSMZ1IhSqA5T1TZ-aCKUScbbmIAWrlaCHusWE7yOkkBsRi"

def _discord_post(url, content):
    try:
        data = json.dumps({"content": content}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception:
        pass

import urllib.request


def fetch_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch current lastPrice for each symbol from Binance USDT futures."""
    prices = {}
    batch = [s for s in symbols if s.endswith("USDT")]
    # Fetch all at once via 24hr tickers (includes price)
    try:
        r = requests.get(f"{BINANCE_URL}/fapi/v1/ticker/24hr", timeout=15)
        if r.status_code == 200:
            for row in r.json():
                sym = row.get("symbol", "")
                if sym in batch:
                    try:
                        prices[sym] = float(row.get("lastPrice") or row.get("closePrice") or 0)
                    except ValueError:
                        pass
    except Exception:
        pass

    # Fill missing with individual requests
    missing = [s for s in batch if s not in prices]
    for sym in missing:
        p = _fetch_single(sym)
        if p:
            prices[sym] = p
    return prices


def _fetch_single(symbol: str) -> float | None:
    """Fetch price for a single symbol."""
    try:
        r = requests.get(
            f"{BINANCE_URL}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=10,
        )
        if r.status_code == 200:
            return float(r.json().get("price", 0))
    except Exception:
        pass
    return None


def build_market_data(prices: dict, open_trades: list) -> dict:
    """
    Build market_data dict for check_pending_exits.
    Format: {SYMBOL: {price: float, direction: str, scanner: str}}
    """
    market = {}
    for trade in open_trades:
        coin = trade["coin"].upper()
        if not coin.endswith("USDT"):
            coin += "USDT"
        if coin in prices:
            market[coin] = {
                "price": prices[coin],
                "direction": trade["direction"].upper(),
                "scanner": trade["scanner"],
            }
    return market


def format_closed_report(closed: list[dict], open_trades: list, prices: dict) -> str:
    """Format a Telegram-ready report of what just closed."""
    if not closed:
        return "Exit monitor: No trades hit SL or TP this check."

    lines = [
        f"EXITS — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 48,
    ]

    for result in closed:
        outcome_icon = "✅" if result["outcome"] == "win" else "❌" if result["outcome"] == "loss" else "⚖️"
        lines.append(
            f"{outcome_icon} {result['trade_id']} | {result['outcome'].upper()} "
            f"| {result['pnl_r']:+.2f}R | exit: {result['exit_price']}"
        )

    # Summary
    wins = [c for c in closed if c["outcome"] == "win"]
    losses = [c for c in closed if c["outcome"] == "loss"]
    total_r = sum(c["pnl_r"] for c in closed)
    lines.append("")
    lines.append(f"Closed: {len(wins)}W / {len(losses)}L | Net: {total_r:+.2f}R")

    # Current open trades summary
    lines.append("")
    lines.append(f"Open trades: {len(open_trades)}")
    open_with_prices = []
    for trade in open_trades:
        coin = trade["coin"].upper()
        if not coin.endswith("USDT"):
            coin += "USDT"
        if coin in prices:
            entry = trade["entry_price"]
            sl = trade["sl_price"]
            tp = trade["tp_price"]
            direction = trade["direction"].upper()
            current = prices[coin]
            # Distance to SL and TP
            if direction == "LONG" and sl and tp:
                sl_dist = ((current - sl) / entry * 100) if entry else 0
                tp_dist = ((tp - current) / entry * 100) if entry else 0
            elif direction == "SHORT" and sl and tp:
                sl_dist = ((sl - current) / entry * 100) if entry else 0
                tp_dist = ((current - tp) / entry * 100) if entry else 0
            else:
                sl_dist = tp_dist = 0
            open_with_prices.append(
                f"  {coin}: {direction} | entry={entry} current={current} "
                f"| SL={sl} ({sl_dist:+.1f}%) TP={tp} ({tp_dist:+.1f}%)"
            )
    for line in open_with_prices:
        lines.append(line)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="PICO Exit Monitor")
    parser.add_argument("--execute", action="store_true",
                        help="Execute closes (default: dry-run)")
    parser.add_argument("--report", action="store_true",
                        help="Print Telegram-formatted report to stdout")
    args = parser.parse_args()

    j = Journal()
    open_trades = j.get_open_trades()

    if not open_trades:
        print("No open trades.")
        return

    # Get unique symbols
    symbols = []
    for trade in open_trades:
        coin = trade["coin"].upper()
        if not coin.endswith("USDT"):
            coin += "USDT"
        symbols.append(coin)
    symbols = list(set(symbols))

    print(f"Fetching prices for {len(symbols)} symbols...", flush=True)
    prices = fetch_prices(symbols)
    found = sum(1 for s in symbols if s in prices)
    print(f"Got {found}/{len(symbols)} prices", flush=True)

    if not prices:
        print("ERROR: Could not fetch any prices.")
        sys.exit(1)

    market_data = build_market_data(prices, open_trades)
    print(f"Market data built for {len(market_data)} coins.", flush=True)

    if not args.execute:
        # Dry run — just show what would close
        print("\n=== DRY RUN — trades that would close ===")
        for trade in open_trades:
            coin = trade["coin"].upper()
            if not coin.endswith("USDT"):
                coin += "USDT"
            if coin not in market_data:
                print(f"  {trade['trade_id']} {coin}: NO PRICE")
                continue
            data = market_data[coin]
            current = data["price"]
            direction = trade["direction"].upper()
            sl = trade["sl_price"]
            tp = trade["tp_price"]
            entry = trade["entry_price"]
            if direction == "LONG":
                if tp and current >= tp:
                    print(f"  {trade['trade_id']} LONG {coin}: TP hit ({current} >= {tp})")
                elif sl and current <= sl:
                    print(f"  {trade['trade_id']} LONG {coin}: SL hit ({current} <= {sl})")
                else:
                    print(f"  {trade['trade_id']} LONG {coin}: entry={entry} current={current} sl={sl} tp={tp}")
            elif direction == "SHORT":
                if tp and current <= tp:
                    print(f"  {trade['trade_id']} SHORT {coin}: TP hit ({current} <= {tp})")
                elif sl and current >= sl:
                    print(f"  {trade['trade_id']} SHORT {coin}: SL hit ({current} >= {sl})")
                else:
                    print(f"  {trade['trade_id']} SHORT {coin}: entry={entry} current={current} sl={sl} tp={tp}")
        return

    # Execute
    print("\nChecking exits...", flush=True)
    closed = j.check_pending_exits(market_data)

    # Post closed trades to Discord #journal
    for c in closed:
        outcome_icon = "✅" if c["outcome"] == "win" else "❌" if c["outcome"] == "loss" else "➖"
        coin_sym = c.get("coin", c.get("symbol", "???"))
        direction = c.get("direction", "???").upper()
        pnl_r = c.get("pnl_r", 0)
        entry_ts = c.get("entry_ts", c.get("signal_ts", "?"))
        exit_ts  = c.get("exit_ts", "?")
        def fmt_ts(ts):
            if not ts or ts == "?":
                return "?"
            return str(ts)[:16]
        content = (
            f"{outcome_icon} **{coin_sym}** {direction} — {c['outcome'].upper()}\n"
            f"PnL: {pnl_r:+.2f}R | Entry: `{fmt_ts(entry_ts)}` → Exit: `{fmt_ts(exit_ts)}`"
        )
        _discord_post(DISCORD_JOURNAL_WH, content)

    if args.report:
        report = format_closed_report(closed, j.get_open_trades(), prices)
        print(report)
    else:
        if closed:
            print(f"Closed {len(closed)} trades:")
            for c in closed:
                print(f"  {c['trade_id']}: {c['outcome']} {c['pnl_r']:+.2f}R @ {c['exit_price']}")
        else:
            print("No exits this check.")


if __name__ == "__main__":
    main()
