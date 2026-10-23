"""
One set of trade statistics for every report (journal.py, performance_report.py,
weekly_report.py, standup_report.py). Previously each report computed
its own numbers and they disagreed.

Conventions
-----------
* A trade counts once it is CLOSED: outcome in win/loss/breakeven.
  'pending' and 'cancelled' (limit orders that never filled) are not trades.
* Expectancy = total R / number of closed trades (breakevens included).
* Win rate is shown two ways: wins / all closed, and wins / (wins + losses).
  The v2 bots move stops to ~breakeven at +0.9R, so breakevens are common.
* Time = EXIT time (when the result happened), parsed as a datetime — never
  compared as text (that dropped every Sunday from the weekly report).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

CLOSED = ("win", "loss", "breakeven")


def parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def setup_of(t: dict) -> str:
    if t.get("setup"):
        return t["setup"]
    try:
        for f in json.loads(t.get("flags") or "[]"):
            if isinstance(f, str) and f.startswith("setup:"):
                return f[6:]
    except (ValueError, TypeError):
        pass
    return "unknown"


def exit_reason_of(t: dict) -> str:
    n = (t.get("notes") or "")
    return n.split(":", 1)[0].strip() or "unknown"


def closed_trades(trades: Iterable[dict]) -> list[dict]:
    return [t for t in trades if t.get("outcome") in CLOSED and t.get("r_multiple") is not None]


def summarize(trades: Iterable[dict]) -> dict:
    ts = sorted(closed_trades(trades), key=lambda t: parse_ts(t.get("exit_ts")) or datetime.min.replace(tzinfo=timezone.utc))
    n = len(ts)
    rs = [float(t["r_multiple"]) for t in ts]
    wins = [r for t, r in zip(ts, rs) if t["outcome"] == "win"]
    losses = [r for t, r in zip(ts, rs) if t["outcome"] == "loss"]
    be = n - len(wins) - len(losses)
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    eq = peak = dd = 0.0
    streak_w = streak_l = cur_w = cur_l = 0
    for t, r in zip(ts, rs):
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        if t["outcome"] == "win":
            cur_w, cur_l = cur_w + 1, 0
        elif t["outcome"] == "loss":
            cur_l, cur_w = cur_l + 1, 0
        streak_w, streak_l = max(streak_w, cur_w), max(streak_l, cur_l)
    pnl = [float(t["pnl_usd"]) for t in ts if t.get("pnl_usd") is not None]
    return {
        "closed": n, "wins": len(wins), "losses": len(losses), "breakeven": be,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "win_rate_decisive": round(len(wins) / (len(wins) + len(losses)) * 100, 1) if (wins or losses) else 0.0,
        "total_r": round(sum(rs), 2),
        "expectancy_r": round(sum(rs) / n, 3) if n else 0.0,
        "avg_win_r": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (None if not gross_win else float("inf")),
        "max_drawdown_r": round(dd, 2),
        "best_r": round(max(rs), 2) if rs else 0.0,
        "worst_r": round(min(rs), 2) if rs else 0.0,
        "streak_w": streak_w, "streak_l": streak_l,
        "total_pnl_usd": round(sum(pnl), 2),
    }


def breakdown(trades: Iterable[dict], key) -> dict:
    g = defaultdict(list)
    for t in closed_trades(trades):
        g[key(t)].append(t)
    return {k: summarize(v) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


def full(trades: list[dict]) -> dict:
    """Summary plus every breakdown the reports use."""
    trades = list(trades)
    return {
        "summary": summarize(trades),
        "pending": sum(1 for t in trades if t.get("outcome") == "pending"),
        "cancelled": sum(1 for t in trades if t.get("outcome") == "cancelled"),
        "by_scanner": breakdown(trades, lambda t: t.get("scanner") or "unknown"),
        "by_setup": breakdown(trades, setup_of),
        "by_side": breakdown(trades, lambda t: (t.get("direction") or "").lower()),
        "by_score": breakdown(trades, lambda t: f"{t.get('quality_score')}/3"),
        "by_exit": breakdown(trades, exit_reason_of),
    }


def in_window(trades: Iterable[dict], start: datetime, end: datetime) -> list[dict]:
    """Closed trades whose EXIT falls in [start, end)."""
    out = []
    for t in trades:
        d = parse_ts(t.get("exit_ts"))
        if d and start <= d < end:
            out.append(t)
    return out


def fmt_line(s: dict) -> str:
    pf = s["profit_factor"]
    pf_s = "—" if pf is None else ("∞" if pf == float("inf") else f"{pf:.2f}")
    return (f"{s['closed']} trades · {s['wins']}W/{s['losses']}L/{s['breakeven']}BE · "
            f"WR {s['win_rate']:.0f}% ({s['win_rate_decisive']:.0f}% excl. BE) · "
            f"exp {s['expectancy_r']:+.3f}R · total {s['total_r']:+.2f}R · PF {pf_s} · "
            f"max DD {s['max_drawdown_r']:.2f}R")
