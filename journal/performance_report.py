#!/usr/bin/env python3
"""
PICO Performance Report — Full trading journal analytics.
========================================================
Run manually or via cron for weekly review.

Usage:
    python performance_report.py                   # full report
    python performance_report.py --weeks 1         # last week only
    python performance_report.py --scanner mean_reversion  # MR only
    python performance_report.py --telegram        # Telegram-formatted
"""
import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "journal.db"


def get_trades(db_path: Path, scanner: str = None, weeks_back: int = None):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    query = "SELECT * FROM trades"
    where = []
    params = []
    if scanner:
        where.append("scanner = ?")
        params.append(scanner)
    if weeks_back:
        cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks_back)).isoformat()
        where.append("signal_ts >= ?")
        params.append(cutoff)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY signal_ts DESC"
    rows = con.execute(query, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def compute_stats(trades, label=""):
    closed = [t for t in trades if t["outcome"] not in ("pending", "cancelled")]
    pending = [t for t in trades if t["outcome"] == "pending"]

    wins     = [t for t in closed if t["outcome"] == "win"]
    losses   = [t for t in closed if t["outcome"] == "loss"]
    breakeven = [t for t in closed if t["outcome"] == "breakeven"]

    total_closed = len(wins) + len(losses)
    wr = len(wins) / total_closed * 100 if total_closed > 0 else 0

    avg_win_r  = sum(t["r_multiple"] for t in wins) / len(wins) if wins else 0
    avg_loss_r = sum(t["r_multiple"] for t in losses) / len(losses) if losses else 0

    total_r = sum(t["r_multiple"] for t in closed)
    total_pnl_usd = sum(t["pnl_usd"] for t in closed if t["pnl_usd"] is not None)
    expectancy = total_r / total_closed if total_closed > 0 else 0

    # Best and worst
    best = max(closed, key=lambda t: t["r_multiple"]) if closed else None
    worst = min(closed, key=lambda t: t["r_multiple"]) if closed else None

    # Streaks
    streak_w = streak_l = cur_w = cur_l = 0
    for t in sorted(closed, key=lambda x: x["exit_ts"] or ""):
        if t["outcome"] == "win":
            if cur_w == 0: cur_w = 1
            else: cur_w += 1
            cur_l = 0
        elif t["outcome"] == "loss":
            if cur_l == 0: cur_l = 1
            else: cur_l += 1
            cur_w = 0
        streak_w = max(streak_w, cur_w)
        streak_l = max(streak_l, cur_l)

    # By direction
    dir_stats = {}
    for direction in ("SHORT", "LONG"):
        dtrades = [t for t in closed if t["direction"].upper() == direction]
        dwins = [t for t in dtrades if t["outcome"] == "win"]
        dlosses = [t for t in dtrades if t["outcome"] == "loss"]
        dwr = len(dwins) / (len(dwins)+len(dlosses)) * 100 if (dwins+dlosses) else 0
        dir_stats[direction] = {"total": len(dtrades), "wins": len(dwins), "losses": len(dlosses), "wr": dwr}

    return {
        "label": label,
        "total": len(trades),
        "closed": len(closed),
        "pending": len(pending),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(wr, 1),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
        "total_r": round(total_r, 2),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "expectancy": round(expectancy, 3),
        "best": best,
        "worst": worst,
        "streak_w": streak_w,
        "streak_l": streak_l,
        "dir_stats": dir_stats,
        "closed_trades": closed,
    }


def format_telegram_report(stats: dict, scanner: str = None) -> str:
    """Format a clean Telegram-ready performance report."""
    label = stats["label"]
    lbl = f" — {label}" if label else ""

    lines = [
        f"PERFORMANCE{label}",
        "=" * 40,
        f"Trades: {stats['closed']} closed | {stats['pending']} pending",
        f"WR: {stats['win_rate']}% ({stats['wins']}W / {stats['losses']}L)",
        f"Avg win: {stats['avg_win_r']}R | Avg loss: {stats['avg_loss_r']}R",
        f"Net P&L: {stats['total_r']:+.2f}R ({stats['total_pnl_usd']:+.2f}USD)",
        f"Expectancy: {stats['expectancy']}R/trade",
    ]

    if stats["best"]:
        t = stats["best"]
        lines.append(f"Best: {t['coin']} {t['direction']} {t['r_multiple']:+.2f}R")
    if stats["worst"]:
        t = stats["worst"]
        lines.append(f"Worst: {t['coin']} {t['direction']} {t['r_multiple']:+.2f}R")

    lines.append(f"Streaks: {stats['streak_w']}W max / {stats['streak_l']}L max")

    # Direction breakdown
    for direction, ds in stats["dir_stats"].items():
        if ds["total"] > 0:
            lines.append(f"{direction}: {ds['wins']}W/{ds['losses']}L WR={ds['wr']:.0f}%")

    # Sample size warning
    if stats["closed"] < 20:
        lines.append("")
        lines.append(f"⚠️ Small sample ({stats['closed']} trades) — need 30+ for significance")

    return "\n".join(lines)


def format_full_report(stats: dict) -> str:
    """Format a detailed text report."""
    lines = [
        f"PICO Trading Journal — Performance Report",
        f"=" * 50,
        f"Period: {stats['label']}",
        f"",
        f"SUMMARY",
        f"  Total trades:    {stats['total']}",
        f"  Closed:          {stats['closed']}",
        f"  Pending:         {stats['pending']}",
        f"  Wins:            {stats['wins']}",
        f"  Losses:          {stats['losses']}",
        f"  Breakeven:       {stats['breakeven']}",
        f"  Win rate:        {stats['win_rate']}%",
        f"",
        f"R-MULTIPLES",
        f"  Avg win:          {stats['avg_win_r']}R",
        f"  Avg loss:         {stats['avg_loss_r']}R",
        f"  Total P&L:        {stats['total_r']:+.2f}R",
        f"  Expectancy/trade: {stats['expectancy']}R",
        f"",
        f"STREAKS",
        f"  Max win streak:   {stats['streak_w']}",
        f"  Max loss streak:  {stats['streak_l']}",
    ]

    if stats["dir_stats"]:
        lines.append("")
        lines.append("BY DIRECTION")
        for direction, ds in stats["dir_stats"].items():
            lines.append(f"  {direction:6}: {ds['wins']:2}W / {ds['losses']:2}L | WR={ds['wr']:.0f}%")

    if stats["best"]:
        t = stats["best"]
        lines.append(f"")
        lines.append(f"BEST TRADE: {t['coin']} {t['direction']} | {t['r_multiple']:+.2f}R")
        lines.append(f"  Entry: {t['entry_price']} → Exit: {t['exit_price']}")
        lines.append(f"  Thesis: {t.get('thesis', '—')}")

    if stats["worst"]:
        t = stats["worst"]
        lines.append(f"")
        lines.append(f"WORST TRADE: {t['coin']} {t['direction']} | {t['r_multiple']:+.2f}R")
        lines.append(f"  Entry: {t['entry_price']} → Exit: {t['exit_price']}")

    if stats["closed"] < 20:
        lines.append("")
        lines.append(f"⚠️  Sample size ({stats['closed']} trades) below 30 — results not yet statistically significant")

    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="PICO Performance Report")
    parser.add_argument("--scanner", default=None, help="Filter by scanner: mean_reversion or momentum")
    parser.add_argument("--weeks", type=int, default=None, help="Weeks back to include")
    parser.add_argument("--telegram", action="store_true", help="Telegram-formatted output")
    parser.add_argument("--all", action="store_true", help="Show all time (not just closed)")
    args = parser.parse_args()

    label_parts = []
    if args.scanner:
        label_parts.append(args.scanner.replace("_", " ").title())
    if args.weeks:
        label_parts.append(f"Last {args.weeks}w")
    label = " ".join(label_parts) if label_parts else "All Time"

    trades = get_trades(DB_PATH, scanner=args.scanner, weeks_back=args.weeks)
    stats = compute_stats(trades, label=label)

    if args.telegram:
        print(format_telegram_report(stats, scanner=args.scanner))
    else:
        print(format_full_report(stats))

    # Individual trade list
    closed = stats["closed_trades"]
    if closed and not args.telegram:
        print("")
        print("CLOSED TRADES:")
        print(f"{'TradeID':22} {'Coin':10} {'Dir':5} {'Entry':12} {'Exit':12} {'R':>6} {'Outcome':8} {'ExitWhen'}")
        print("-" * 95)
        for t in sorted(closed, key=lambda x: x.get("exit_ts") or ""):
            exit_ts = (t.get("exit_ts") or "")[:19]
            print(f"{t['trade_id']:22} {t['coin']:10} {t['direction']:5} {str(t.get('entry_price','?'))[:12]:12} {str(t.get('exit_price','?'))[:12]:12} {t['r_multiple']:>+6.2f} {t['outcome']:8} {exit_ts}")


if __name__ == "__main__":
    main()
