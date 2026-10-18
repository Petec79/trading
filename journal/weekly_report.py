#!/usr/bin/env python3
"""
Spicy Weekly Alpha Report
=========================
Pulls journal stats and prints a Telegram-ready weekly report.
Run via cron every Monday morning.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from journal import Journal
from datetime import datetime, timezone

def build_report():
    j = Journal()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        f"📊 SPICY WEEKLY ALPHA REPORT",
        f"{'='*36}",
        f"Week of: {today}",
        "",
    ]

    # ── All-time stats ──────────────────────────────────────────────────────
    at = j.get_all_time_stats()
    if at["count"] > 0:
        wr_color = "🟢" if at["win_rate"] >= 60 else "🟡" if at["win_rate"] >= 40 else "🔴"
        lines += [
            f"All-Time (since journal start)",
            f"  {at['count']} closed trades | {wr_color} {at['win_rate']:.0f}% win rate",
            f"  avg R: {at['avg_r']:+.2f} | best: {at['best_r']:+.2f}R | worst: {at['worst_r']:+.2f}R",
            f"  Total PnL: ${at['total_pnl']:+.2f}",
            "",
        ]
        if at["by_scanner"].get("mean_reversion", {}).get("count", 0) > 0:
            mr = at["by_scanner"]["mean_reversion"]
            lines.append(f"  MR: {mr['count']} trades | {mr['win_rate']:.0f}% WR | avg R: {mr.get('avg_r', 0):+.2f}")
        if at["by_scanner"].get("momentum", {}).get("count", 0) > 0:
            momo = at["by_scanner"]["momentum"]
            lines.append(f"  MOMO: {momo['count']} trades | {momo['win_rate']:.0f}% WR | avg R: {momo.get('avg_r', 0):+.2f}")
        lines.append("")
    else:
        lines.append("No closed trades yet — journal is empty.")
        lines.append("")

    # ── Last 4 weeks breakdown ────────────────────────────────────────────────
    lines.append("Weekly breakdown (last 4 weeks):")
    stats = j.get_weekly_stats(weeks_back=4)
    has_data = False
    for week_label, w in stats.items():
        if w["count"] == 0:
            continue
        has_data = True
        wr = w["win_rate"]
        wr_icon = "🟢" if wr >= 60 else "🟡" if wr >= 40 else "🔴"
        avg_r = w["avg_r"]
        r_icon = "🟢" if avg_r >= 1 else "🟡" if avg_r >= 0.5 else "🔴"
        lines.append(
            f"  {week_label}: {w['count']} trades | {wr_icon}{wr:.0f}% WR "
            f"| {r_icon}avg R: {avg_r:+.2f} | ${w['total_pnl']:+.2f}"
        )
    if not has_data:
        lines.append("  No weekly data yet.")

    lines.append("")
    lines.append("Recent closed trades (last 14 days):")
    recent = j.get_closed_trades(days_back=14)
    if recent:
        for t in recent[:8]:
            icon = "✅" if t["outcome"] == "win" else "❌" if t["outcome"] == "loss" else "➖"
            r = f"{t['r_multiple']:+.2f}R" if t["r_multiple"] else "—"
            pnl = f"${t['pnl_usd']:+.2f}" if t["pnl_usd"] else ""
            scanner_tag = "MR" if t["scanner"] == "mean_reversion" else "MOMO"
            date = t["signal_ts"][:10]
            direction_icon = "🔴" if t["direction"] == "short" else "🟢"
            lines.append(
                f"  {icon} {date} {direction_icon}{t['coin']} {scanner_tag} {r} {pnl}"
            )
    else:
        lines.append("  No closed trades in the last 14 days.")

    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_report())
