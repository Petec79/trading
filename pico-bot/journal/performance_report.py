#!/usr/bin/env python3
"""
PICO Performance Report — Standup-format paper-trading summary.
===============================================================

Purpose
-------
Produce the PICO Trading section the daily standup cron (`b2c878f8eb76`)
prepends to its CEO summary. Designed to never go missing: it discovers
the canonical PICO journal DB across known locations and falls back to a
direct sqlite read if the wrapper module is unreachable.

Data source (single source of truth)
------------------------------------
SQLite database at `/home/openclaw/workspace/journal/journal.db`
(the canonical Spicy / PICO paper-trading journal). Schema: `trades` and
`sessions` tables; see `/home/openclaw/workspace/journal/journal.py`
for the Journal class that writes to it.

This script READS the DB directly so it never depends on the canonical
`/home/openclaw/workspace/journal/performance_report.py` being present
(historically intermittent — see kanban t_db0c9a68). If `journal.py`
is importable we use `Journal.get_all_time_stats()` and
`Journal.get_open_trades()`; otherwise we re-implement the same SQL
inline. Same numbers either way.

Usage
-----
    python journal/performance_report.py --telegram    # standup default
    python journal/performance_report.py --dry-run    # emit report to stdout, exit 0
    python journal/performance_report.py --json       # machine-readable report

Exit codes
----------
0  — always. Empty-state uses output 'PICO: no trades recorded'.
1  — only on sqlite/programming error (db missing AND unreachable parent).

Flags
-----
--telegram   Render the report body expected by the standup cron (default if no
            other output flag). Output prepends the '**PICO Trading**' header.
--dry-run    Identical to --telegram; named for CEO_PLAYBOOK production-scripts
            standard. Does not post to Telegram/Discord.
--json       Emit the report as JSON (state_source, trade_count, etc.) for
            downstream tooling. Output includes the same fields the standup
            needs plus REPORTING METADATA (see AC).
--scanner    Filter to one bot: momentum_v2 | mean_reversion_v2 (default: all).
--stale-hours N  Threshold for the signal-stale flag (default 24). Signals
            whose newest `signal_ts` is older than this many hours trip the
            STALE flag.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent

# Candidate journal roots, in priority order. We probe each until we find a
# readable `journal.db`. Keeps this script decoupled from whichever path the
# standup's relative `journal/performance_report.py` happens to resolve to.
_JOURNAL_ROOTS: tuple[Path, ...] = (
    THIS_DIR.parent.parent / "workspace" / "journal",   # /home/openclaw/workspace/journal
    Path("/home/openclaw/workspace/journal"),
    Path("/home/openclaw/midas/journal"),
    Path("/home/openclaw/journal"),
    THIS_DIR / "journal",                                # pico-bot/journal/journal
    THIS_DIR,                                            # pico-bot/journal (fallback, no db)
)


def _find_db() -> tuple[Path | None, str]:
    """Return (db_path or None, state_source label) for the journal DB.

    state_source is a short identifier the standup can quote verbatim:
        sqlite:<path>           — DB found and readable
        sqlite:<path>:no_table  — DB exists but `trades` table missing
        none                    — no DB at any candidate location
    """
    for root in _JOURNAL_ROOTS:
        db = root / "journal.db"
        if db.exists() and db.is_file():
            try:
                with sqlite3.connect(db) as con:
                    row = con.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='trades' LIMIT 1"
                    ).fetchone()
                if row:
                    return db, f"sqlite:{db}"
                return db, f"sqlite:{db}:no_table"
            except sqlite3.DatabaseError as exc:
                return db, f"sqlite:{db}:error:{type(exc).__name__}"
    return None, "none"


def _query_stats(db: Path, scanner: str | None) -> dict[str, Any]:
    """Read trades directly from sqlite. Mirrors journal.py:get_all_time_stats."""
    where = ["outcome != 'pending'"]
    params: tuple[Any, ...] = ()
    if scanner:
        where.append("scanner = ?")
        params = (scanner,)
    sql = "SELECT * FROM trades WHERE " + " AND ".join(where)

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()
        pending_rows = con.execute(
            "SELECT * FROM trades WHERE outcome = 'pending'"
        ).fetchall()
        ts_rows = con.execute(
            "SELECT MIN(signal_ts) AS oldest, MAX(signal_ts) AS newest "
            "FROM trades"
        ).fetchone()
    # Filter canonical closed set: exclude pending AND cancelled (mirrors
    # /home/openclaw/workspace/journal/journal.py:PerformanceReport convention).
    trade_rows = [dict(r) for r in rows if r["outcome"] != "cancelled"]
    pending = [dict(r) for r in pending_rows]
    oldest = ts_rows["oldest"] if ts_rows else None
    newest = ts_rows["newest"] if ts_rows else None
    return {
        "trades": trade_rows,
        "pending": pending,
        "oldest_trade_ts": oldest,
        "newest_trade_ts": newest,
    }


def _shared_stats():
    """journal/stats.py — the one set of numbers every report uses. None if unavailable."""
    for root in (THIS_DIR.parent.parent / "journal", *_JOURNAL_ROOTS):   # repo layout first, then the VPS paths
        if (root / "stats.py").exists():
            sys.path.insert(0, str(root))
            try:
                import stats as S  # type: ignore
                return S
            except Exception:
                continue
    return None


def _compute_stats(state: dict[str, Any]) -> dict[str, Any]:
    trades = state["trades"]
    pending = state["pending"]
    S = _shared_stats()
    if S is not None:
        sm = S.summarize(trades)
        by_bot = {k: v for k, v in S.breakdown(trades, lambda t: t.get("scanner") or "unknown").items()}
        closed, wins, losses = sm["closed"], sm["wins"], sm["losses"]
        wr, total_r, total_pnl_usd, exp = sm["win_rate"], sm["total_r"], sm["total_pnl_usd"], sm["expectancy_r"]
    else:  # fallback: same conventions, computed inline
        cl = [t for t in trades if t["outcome"] in ("win", "loss", "breakeven") and t["r_multiple"] is not None]
        wins = sum(1 for t in cl if t["outcome"] == "win")
        losses = sum(1 for t in cl if t["outcome"] == "loss")
        closed = len(cl)
        total_r = sum(t["r_multiple"] for t in cl)
        total_pnl_usd = sum(t["pnl_usd"] for t in cl if t["pnl_usd"] is not None)
        wr = round(wins / closed * 100, 1) if closed else 0.0
        exp = round(total_r / closed, 3) if closed else 0.0
        by_bot = {}
    return {
        "total_trades": closed + len(pending),
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "pending": len(pending),
        "open_positions": len(pending),
        "win_rate": round(wr, 2),
        "expectancy_r": exp,
        "total_r": round(total_r, 2),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "by_bot": by_bot,
        "oldest_trade_ts": state["oldest_trade_ts"],
        "newest_trade_ts": state["newest_trade_ts"],
    }


def _signal_stale(newest_ts: str | None, now: datetime, stale_hours: int) -> dict[str, Any]:
    """STALE if newest signal_ts is older than stale_hours, or no signals at all."""
    if not newest_ts:
        return {"stale": True, "reason": "no_signals_recorded", "age_hours": None}
    try:
        # SQLite returns ISO 8601 strings; tolerate trailing Z or +00:00.
        ts = datetime.fromisoformat(newest_ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return {"stale": True, "reason": f"unparseable_ts:{newest_ts!r}", "age_hours": None}
    age = (now - ts).total_seconds() / 3600.0
    return {
        "stale": age > stale_hours,
        "reason": "fresh" if age <= stale_hours else f"no_signal_in_{age:.1f}h",
        "age_hours": round(age, 2),
    }


def _format_telegram(stats: dict[str, Any], stale: dict[str, Any],
                     state_source: str) -> str:
    if stats["total_trades"] == 0:
        return "PICO: no trades recorded"
    lines = [
        "**PICO Trading**",
        f"Trades: {stats['total_trades']} ({stats['closed']} closed, "
        f"{stats['open_positions']} open)",
        f"WR: {stats['win_rate']}% ({stats['wins']}W / {stats['losses']}L) · "
        f"expectancy {stats.get('expectancy_r', 0):+.3f}R/trade",
        f"P&L: {stats['total_r']:+.2f}R ({stats['total_pnl_usd']:+.2f} USD, paper)",
        *[f"  {({'momentum_v2': 'Momentum', 'mean_reversion_v2': 'Mean reversion'}).get(k, k)}: "
          f"{v['closed']} closed, {v['win_rate']:.0f}% WR, {v['expectancy_r']:+.3f}R/trade, {v['total_r']:+.2f}R"
          for k, v in stats.get("by_bot", {}).items()],
        f"Range: {stats['oldest_trade_ts']} → {stats['newest_trade_ts']}",
        f"Signal: {'⚠️ STALE' if stale['stale'] else '✅ fresh'} "
        f"({stale['reason']})",
        f"Source: {state_source}",
    ]
    return "\n".join(lines)


def _format_json(stats: dict[str, Any], stale: dict[str, Any],
                 state_source: str) -> str:
    payload = {
        "state_source": state_source,
        "trade_count": stats["total_trades"],
        "closed_count": stats["closed"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": stats["win_rate"],
        "pnl_r": stats["total_r"],
        "pnl_usd": stats["total_pnl_usd"],
        "open_positions": stats["open_positions"],
        "oldest_trade_ts": stats["oldest_trade_ts"],
        "newest_trade_ts": stats["newest_trade_ts"],
        "signal_stale": stale,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        description="PICO Performance Report (standup-ready, never-missing).",
    )
    parser.add_argument(
        "--telegram", action="store_true",
        help="Emit standup-ready report (default if no other output flag).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Per CEO_PLAYBOOK production-scripts standard. Same as --telegram: "
            "emit the report to stdout without side effects. Exit 0 always."
        ),
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit machine-readable JSON instead of the Telegram block.",
    )
    parser.add_argument(
        "--scanner", default=None, choices=("mean_reversion", "momentum"),
        help="Filter to a single scanner (default: both).",
    )
    parser.add_argument(
        "--stale-hours", type=int, default=24,
        help="Hours since last signal before flagging STALE (default 24).",
    )
    args = parser.parse_args(argv)

    db, state_source = _find_db()

    if db is None or state_source.endswith(":no_table") or state_source.endswith(":error"):
        # No data: report cleanly, exit 0 (per AC).
        now = datetime.now(timezone.utc)
        if args.as_json:
            print(json.dumps({
                "state_source": state_source,
                "trade_count": 0,
                "closed_count": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "pnl_r": 0.0,
                "pnl_usd": 0.0,
                "open_positions": 0,
                "oldest_trade_ts": None,
                "newest_trade_ts": None,
                "signal_stale": {"stale": True, "reason": "no_db_or_table",
                                 "age_hours": None},
                "as_of": now.isoformat(),
            }, indent=2, sort_keys=True))
        else:
            print("PICO: no trades recorded")
        return 0

    raw = _query_stats(db, scanner=args.scanner)
    stats = _compute_stats(raw)
    stale = _signal_stale(
        stats["newest_trade_ts"], datetime.now(timezone.utc), args.stale_hours
    )

    # Pick output format. --telegram and --dry-run both render the
    # standup-ready block; --json wins for tooling.
    if args.as_json:
        out = _format_json(stats, stale, state_source)
    else:
        out = _format_telegram(stats, stale, state_source)
    print(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except sqlite3.DatabaseError as exc:
        print(f"PICO: db error ({type(exc).__name__}): {exc}", file=sys.stderr)
        sys.exit(1)
