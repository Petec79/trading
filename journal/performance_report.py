#!/usr/bin/env python3
"""
Journal performance report — every number from journal/stats.py (shared with
the weekly report and the standup report), so the reports agree.

Usage:
    python journal/performance_report.py                          # all time, full detail
    python journal/performance_report.py --weeks 1                # trades that EXITED in the last week
    python journal/performance_report.py --scanner mean_reversion_v2
    python journal/performance_report.py --telegram               # short version
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats as S  # noqa: E402

DB_PATH = Path(__file__).parent / "journal.db"
NAMES = {"momentum_v2": "Momentum", "mean_reversion_v2": "Mean reversion"}


def get_trades(db_path: Path, scanner: str = None, weeks_back: int = None) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    q, params = "SELECT * FROM trades", ()
    if scanner:
        q, params = q + " WHERE scanner = ?", (scanner,)
    rows = [dict(r) for r in con.execute(q, params)]
    con.close()
    if weeks_back:
        now = datetime.now(timezone.utc)
        closed = S.in_window(rows, now - timedelta(weeks=weeks_back), now + timedelta(days=1))
        rows = closed + [r for r in rows if r["outcome"] in ("pending", "cancelled")]
    return rows


def report(trades: list[dict], label: str, telegram: bool) -> str:
    f = S.full(trades)
    sm = f["summary"]
    L = [f"PERFORMANCE — {label}", "(paper trades, R after fees)",
         S.fmt_line(sm), f"Open: {f['pending']} · unfilled/cancelled orders: {f['cancelled']}"]
    if not sm["closed"]:
        return "\n".join(L + ["No closed trades yet."])
    L.append(f"Avg win {sm['avg_win_r']:+.2f}R · avg loss {sm['avg_loss_r']:+.2f}R · best {sm['best_r']:+.2f}R · "
             f"worst {sm['worst_r']:+.2f}R · streaks {sm['streak_w']}W / {sm['streak_l']}L · ${sm['total_pnl_usd']:+.2f}")
    groups = [("bot", "by_scanner"), ("setup", "by_setup"), ("side", "by_side")]
    if not telegram:
        groups += [("score", "by_score"), ("exit", "by_exit")]
    for title, key in groups:
        L.append(f"\nBy {title}:")
        for k, v in f[key].items():
            L.append(f"  {NAMES.get(k, k):<22} {S.fmt_line(v)}")
    if sm["closed"] < 30:
        L.append(f"\n⚠️ {sm['closed']} trades — too few to judge yet (aim for 30+ per bot)")
    if not telegram:
        L.append("\nCLOSED TRADES (by exit time):")
        for t in sorted(S.closed_trades(trades), key=lambda t: S.parse_ts(t["exit_ts"])):
            L.append(f"  {t['trade_id']:20} {t['coin']:12} {t['direction']:5} {S.setup_of(t):18} "
                     f"{t['r_multiple']:+6.2f}R {t['outcome']:9} {S.exit_reason_of(t):14} {(t['exit_ts'] or '')[:16]}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Journal performance report")
    ap.add_argument("--scanner", default=None, help="momentum_v2 | mean_reversion_v2")
    ap.add_argument("--weeks", type=int, default=None, help="only trades that exited in the last N weeks")
    ap.add_argument("--telegram", action="store_true", help="short version")
    ap.add_argument("--db", default=str(DB_PATH))
    a = ap.parse_args()
    parts = [NAMES.get(a.scanner, a.scanner)] if a.scanner else []
    parts.append(f"last {a.weeks}w" if a.weeks else "all time")
    print(report(get_trades(Path(a.db), a.scanner, a.weeks), " · ".join(parts), a.telegram))


if __name__ == "__main__":
    main()
