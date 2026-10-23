#!/usr/bin/env python3
"""
Spicy Trading Journal — Core Module
=====================================
Log, track, and review Mean Reversion and Momentum trade signals.

Usage:
    from journal import Journal
    j = Journal()

    j.log_signal(scanner="mean_reversion", direction="short", coin="ALPHAUSDT",
                  sl_price=0.00123, tp_price=0.00118, quality_score=2,
                  mr_score=70.2, flags=["TOP_TRD"], level_approach="fast_spike",
                  volume_regime="increasing", thesis="Extended +36%, MR fading")

    j.log_signal(scanner="momentum", direction="short", coin="ENJUSDT",
                  sl_price=0.05769, quality_score=3,
                  flags=["GET_READY"], level_approach="slow_grind",
                  volume_regime="increasing", thesis="Falling staircase grinding to SL")

    # After trade closes:
    j.update_outcome(trade_id="MR-2026-04-14-001", outcome="win",
                     exit_price=0.00118, pnl_usd=5.00, pnl_pct=2.5, r_multiple=1.5,
                     notes="ALPHA reversed exactly at MR zone. Fast spike thesis confirmed.")

    # Query:
    j.get_open_trades()
    j.get_weekly_stats(weeks_back=4)
"""

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "journal.db"

# Scanners whose trades are opened AND closed by their own engine.
SELF_MANAGED_SCANNERS = {"momentum_v2", "mean_reversion_v2"}

# ── Schema ────────────────────────────────────────────────────────────────────

INIT_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT    UNIQUE NOT NULL,
    scanner         TEXT    NOT NULL,          -- 'mean_reversion' | 'momentum'
    direction       TEXT    NOT NULL,           -- 'long' | 'short'
    coin            TEXT    NOT NULL,           -- e.g. 'ALPHAUSDT'
    signal_ts       TEXT    NOT NULL,           -- ISO timestamp when signal fired
    entry_price     REAL,                       -- filled entry (null until closed)
    sl_price        REAL,
    tp_price        REAL,
    quality_score   INTEGER,                   -- 1-3
    mr_score        REAL,                       -- 0-100 (MR scanner only)
    flags           TEXT,                       -- JSON array as string
    entry_type      TEXT,                       -- 'limit' | 'market' | null
    entry_ts        TEXT,                       -- when we actually entered
    exit_ts         TEXT,                       -- when we closed
    exit_price      REAL,
    pnl_usd         REAL,
    pnl_pct         REAL,
    r_multiple      REAL,
    outcome         TEXT    DEFAULT 'pending', -- 'pending'|'win'|'loss'|'breakeven'|'cancelled'
    level_approach   TEXT,                       -- 'slow_grind' | 'fast_spike' | 'unclear'
    volume_regime   TEXT,                       -- 'increasing' | 'decreasing' | 'unclear'
    thesis          TEXT,                       -- why we took this
    notes           TEXT,                       -- post-mortem
    reviewed        INTEGER  DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    TEXT    UNIQUE NOT NULL,   -- '2026-04-14'
    bias            TEXT,                       -- 'BULLISH'|'BEARISH'|'MIXED'
    session_notes   TEXT,
    created_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_scanner   ON trades(scanner);
CREATE INDEX IF NOT EXISTS idx_trades_outcome   ON trades(outcome);
CREATE INDEX IF NOT EXISTS idx_trades_signal_ts ON trades(signal_ts);
"""


# ── Journal Class ─────────────────────────────────────────────────────────────

class Journal:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db = Path(db_path)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Setup ────────────────────────────────────────────────────────────────

    def _init_db(self):
        with sqlite3.connect(self.db) as con:
            con.executescript(INIT_SQL)

    def _row_to_trade(self, row: tuple, cols: list) -> dict:
        return dict(zip(cols, row))

    # ── Logging Signals ───────────────────────────────────────────────────────

    def log_signal(
        self,
        scanner: str,          # 'mean_reversion' | 'momentum'
        direction: str,        # 'long' | 'short'
        coin: str,
        sl_price: float,
        tp_price: Optional[float] = None,
        entry_price: Optional[float] = None,
        quality_score: int = 0,
        mr_score: Optional[float] = None,
        flags: Optional[list] = None,
        level_approach: str = "unclear",
        volume_regime: str = "unclear",
        thesis: Optional[str] = None,
        entry_type: Optional[str] = None,
        signal_ts: Optional[str] = None,
    ) -> str:
        """
        Log a new trade signal. Returns the unique trade_id.
        If the same (scanner, coin, direction, date) already exists as pending,
        does nothing and returns the existing trade_id.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prefix = "MR" if scanner == "mean_reversion" else "MOMO"

        with sqlite3.connect(self.db) as con:
            cur = con.execute(
                "SELECT trade_id, entry_price FROM trades WHERE scanner=? AND coin=? "
                "AND direction=? AND outcome='pending'",
                (scanner, coin.upper(), direction.upper())
            )
            existing = cur.fetchone()
            if existing:
                existing_id, existing_entry = existing
                # If the existing entry has no entry_price but we have one now, fill it in
                if existing_entry is None and entry_price is not None:
                    con.execute("""
                        UPDATE trades SET entry_price=?, sl_price=?, tp_price=?, thesis=?
                        WHERE trade_id=?
                    """, (entry_price, sl_price, tp_price, thesis, existing_id))
                return existing_id

        # Count today's trades for numbering
        with sqlite3.connect(self.db) as con:
            cur = con.execute(
                "SELECT COUNT(*) FROM trades WHERE signal_ts >= ? AND scanner=?",
                (today, scanner)
            )
            count = cur.fetchone()[0] + 1

        trade_id = f"{prefix}-{today.replace('-','')}-{count:03d}"
        flags_json = json.dumps(flags or [])
        now = datetime.now(timezone.utc).isoformat()
        sig_ts = signal_ts or now

        with sqlite3.connect(self.db) as con:
            con.execute("""
                INSERT INTO trades (
                    trade_id, scanner, direction, coin, signal_ts,
                    sl_price, tp_price, entry_price, quality_score, mr_score,
                    flags, entry_type, level_approach, volume_regime, thesis, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, scanner, direction.upper(), coin.upper(),
                sig_ts, sl_price, tp_price, entry_price, quality_score, mr_score,
                flags_json, entry_type, level_approach, volume_regime, thesis, now
            ))

        return trade_id

    # ── Updating Outcomes ────────────────────────────────────────────────────

    def update_outcome(
        self,
        trade_id: str,
        outcome: str,           # 'win'|'loss'|'breakeven'|'cancelled'
        exit_price: Optional[float] = None,
        pnl_usd: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        r_multiple: Optional[float] = None,
        notes: Optional[str] = None,
        reviewed: bool = True,
    ):
        """Update a trade with its realized outcome."""
        exit_ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db) as con:
            con.execute("""
                UPDATE trades SET
                    outcome   = ?,
                    exit_ts   = ?,
                    exit_price = ?,
                    pnl_usd   = ?,
                    pnl_pct   = ?,
                    r_multiple = ?,
                    notes     = COALESCE(?, notes),
                    reviewed  = ?
                WHERE trade_id = ?
            """, (outcome, exit_ts, exit_price, pnl_usd, pnl_pct,
                  r_multiple, notes, 1 if reviewed else 0, trade_id))

    def update_entry(self, trade_id: str, entry_price: float,
                     entry_type: str = "market", entry_ts: Optional[str] = None):
        """Record that a trade was entered at a specific price."""
        ts = entry_ts or datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db) as con:
            con.execute("""
                UPDATE trades SET entry_price=?, entry_type=?, entry_ts=?
                WHERE trade_id=? AND outcome='pending'
            """, (entry_price, entry_type, ts, trade_id))

    def capture_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,  # 'SL'|'TP'|'COUNTER_SIGNAL'|'MANUAL'|'TIMEOUT'
        r_override: Optional[float] = None,  # force a specific R (e.g. -1 for SL)
        notes: Optional[str] = None,
    ):
        """
        Close a pending trade at a given price and auto-calculate outcome + R.
        Works for both long and short. r_override lets caller pass explicit R
        (e.g. -1 for SL, 1.5 for TP) instead of computing from SL distance.
        """
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM trades WHERE trade_id=? AND outcome='pending'",
                (trade_id,)
            ).fetchone()
            if not row:
                return None

        r = row
        direction = r["direction"].upper()
        entry_price = r["entry_price"]
        sl_price = r["sl_price"]
        tp_price = r["tp_price"]

        if entry_price is None:
            # Cannot compute without entry
            return None

        if r_override is not None:
            pnl_r = r_override
        else:
            risk = abs(entry_price - sl_price) if sl_price else 0
            if risk == 0:
                pnl_r = 0
            else:
                if direction == "LONG":
                    pnl_r = (exit_price - entry_price) / risk
                else:
                    pnl_r = (entry_price - exit_price) / risk

        if pnl_r > 0.2:
            outcome = "win"
        elif pnl_r < -0.2:
            outcome = "loss"
        else:
            outcome = "breakeven"

        # Fixed $100 risk per trade — pnl_usd = r_multiple × risk
        risk_usd = 100.0
        pnl_usd = round(pnl_r * risk_usd, 2)

        self.update_outcome(
            trade_id=trade_id,
            outcome=outcome,
            exit_price=exit_price,
            r_multiple=round(pnl_r, 3),
            pnl_usd=pnl_usd,
            notes=f"{exit_reason}" + (f": {notes}" if notes else ""),
        )
        return {"trade_id": trade_id, "outcome": outcome, "pnl_r": pnl_r, "pnl_usd": pnl_usd, "exit_price": exit_price}

    def check_pending_exits(
        self,
        market_data: dict,  # {COIN: {price: float, direction: str, scanner: str}}
    ) -> list[dict]:
        """
        Given current market data, check all pending trades for exits.
        Returns list of capture_exit results for each closed trade.

        Exit rules:
          - TP hit  → pnl_r = tp_r (positive)
          - SL hit  → pnl_r = -1
          - Counter-signal (same coin, opposite direction now) → mark breakeven
        """
        pending = self.get_open_trades()
        closed = []

        for trade in pending:
            # Bots that manage their own exits candle-by-candle (e.g. momentum_v2)
            # must not be closed here on a single 30-60 min price snapshot.
            if trade.get("scanner") in SELF_MANAGED_SCANNERS:
                continue
            coin = trade["coin"].upper().replace("USDT", "") + "USDT"
            if coin not in market_data:
                continue

            data = market_data[coin]
            current_price = data["price"]
            current_direction = data.get("direction", "").upper()
            trade_direction = trade["direction"].upper()
            entry_price = trade["entry_price"]

            if entry_price is None:
                continue

            sl = trade["sl_price"]
            tp = trade["tp_price"]

            # Determine TP R from the trade's thesis or default to 1.5
            tp_r = 1.5  # default
            thesis = (trade["thesis"] or "").lower()
            if "spike" in thesis:
                tp_r = 1.5
            elif "partial" in thesis:
                tp_r = 1.0
            elif "fb" in thesis or "breakout" in thesis:
                tp_r = 1.0

            exited = False

            # Check TP / SL
            if trade_direction == "LONG" and tp and sl:
                if current_price >= tp:
                    result = self.capture_exit(trade["trade_id"], tp, "TP",
                                               r_override=tp_r)
                    if result:
                        closed.append(result)
                    exited = True
                elif current_price <= sl:
                    result = self.capture_exit(trade["trade_id"], sl, "SL",
                                               r_override=-1.0)
                    if result:
                        closed.append(result)
                    exited = True

            elif trade_direction == "SHORT" and tp and sl:
                if current_price <= tp:
                    result = self.capture_exit(trade["trade_id"], tp, "TP",
                                               r_override=tp_r)
                    if result:
                        closed.append(result)
                    exited = True
                elif current_price >= sl:
                    result = self.capture_exit(trade["trade_id"], sl, "SL",
                                               r_override=-1.0)
                    if result:
                        closed.append(result)
                    exited = True

            # Counter-signal exit (no TP/SL hit but direction flipped)
            if not exited and current_direction and current_direction != trade_direction:
                # Same coin, opposite direction signal detected
                counter_direction = "LONG" if trade_direction == "SHORT" else "SHORT"
                if current_direction == counter_direction:
                    # Exit at current price as breakeven (could compute actual)
                    result = self.capture_exit(
                        trade["trade_id"], current_price, "COUNTER_SIGNAL",
                        notes=f"direction flipped {trade_direction}→{current_direction}"
                    )
                    if result:
                        closed.append(result)

        return closed

    # ── Queries ──────────────────────────────────────────────────────────────

    def get_open_trades(self) -> list[dict]:
        """Return all pending trades."""
        cols = ["trade_id","scanner","direction","coin","signal_ts",
                "sl_price","tp_price","quality_score","mr_score","flags",
                "level_approach","volume_regime","thesis"]
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM trades WHERE outcome='pending' ORDER BY signal_ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trades_by_date(self, date_str: str) -> list[dict]:
        """Return all signals for a given date (YYYY-MM-DD)."""
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM trades WHERE signal_ts LIKE ? ORDER BY signal_ts",
                (f"{date_str}%",)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_closed_trades(self, days_back: int = 30) -> list[dict]:
        """Return closed trades from the last N days."""
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM trades WHERE outcome != 'pending' "
                "AND signal_ts >= ? ORDER BY signal_ts DESC",
                (cutoff,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_weekly_stats(self, weeks_back: int = 4) -> dict:
        """Compute aggregate stats over the last N weeks."""
        stats = {}
        for week in range(weeks_back):
            # Calculate date range for this week
            from datetime import timedelta
            today = datetime.now(timezone.utc).date()
            start_of_week = today - timedelta(days=today.weekday() + 7 * week)
            end_of_week = start_of_week + timedelta(days=6)
            start_str = start_of_week.isoformat()
            end_str = end_of_week.isoformat()

            with sqlite3.connect(self.db) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT * FROM trades WHERE outcome != 'pending' "
                    "AND signal_ts >= ? AND signal_ts <= ?",
                    (start_str, end_str + " 23:59:59")
                ).fetchall()

            trades = [dict(r) for r in rows]
            stats[f"Week of {start_str}"] = self._compute_stats(trades)

        return stats

    def get_all_time_stats(self) -> dict:
        """Compute all-time stats across all closed trades."""
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM trades WHERE outcome != 'pending'"
            ).fetchall()
        return self._compute_stats([dict(r) for r in rows])

    def _compute_stats(self, trades: list[dict]) -> dict:
        if not trades:
            return {"count": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "avg_r": 0, "best_r": 0, "worst_r": 0, "total_pnl": 0,
                    "by_scanner": {}, "by_direction": {}}

        wins   = [t for t in trades if t["outcome"] == "win"]
        losses = [t for t in trades if t["outcome"] == "loss"]
        be     = [t for t in trades if t["outcome"] == "breakeven"]

        r_values = [t["r_multiple"] for t in trades if t["r_multiple"] is not None]
        pnl_vals = [t["pnl_usd"] for t in trades if t["pnl_usd"] is not None]

        # By scanner
        by_scanner = {}
        for scanner in ("mean_reversion", "momentum"):
            s_trades = [t for t in trades if t["scanner"] == scanner]
            s_r = [t["r_multiple"] for t in s_trades if t["r_multiple"] is not None]
            by_scanner[scanner] = {
                "count": len(s_trades),
                "wins": sum(1 for t in s_trades if t["outcome"] == "win"),
                "win_rate": (sum(1 for t in s_trades if t["outcome"] == "win") / len(s_trades) * 100) if s_trades else 0,
                "avg_r": (sum(s_r) / len(s_r)) if s_r else 0,
            }

        # By direction
        by_direction = {}
        for direction in ("long", "short"):
            d_trades = [t for t in trades if t["direction"].upper() == direction.upper()]
            d_r = [t["r_multiple"] for t in d_trades if t["r_multiple"] is not None]
            by_direction[direction] = {
                "count": len(d_trades),
                "wins": sum(1 for t in d_trades if t["outcome"] == "win"),
                "win_rate": (sum(1 for t in d_trades if t["outcome"] == "win") / len(d_trades) * 100) if d_trades else 0,
                "avg_r": (sum(d_r) / len(d_r)) if d_r else 0,
            }

        return {
            "count": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(be),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0,
            "avg_r": sum(r_values) / len(r_values) if r_values else 0,
            "best_r": max(r_values) if r_values else 0,
            "worst_r": min(r_values) if r_values else 0,
            "total_pnl": sum(pnl_vals) if pnl_vals else 0,
            "by_scanner": by_scanner,
            "by_direction": by_direction,
            "trades": trades,
        }

    # ── Formatting for Telegram ──────────────────────────────────────────────

    def format_open_trades(self) -> str:
        """Format pending trades for Telegram."""
        trades = self.get_open_trades()
        if not trades:
            return "No open trades."

        lines = []
        for t in trades:
            prefix = "🔴" if t["direction"].upper() == "SHORT" else "🟢"
            score_emoji = "🟢" * t["quality_score"] + "⚪" * (3 - t["quality_score"])
            flags = json.loads(t["flags"] or "[]")
            flag_str = " ".join(f"#{f}" for f in flags) if flags else ""
            mr_str = f" MR {t['mr_score']:.1f}" if t["mr_score"] else ""
            lines.append(
                f"{prefix} {t['coin']} {score_emoji}{mr_str}\n"
                f"   SL: {t['sl_price']}  TP: {t['tp_price'] or '?'}  {flag_str}"
            )
        return "OPEN TRADES:\n" + "\n".join(lines)

    def format_weekly_report(self, weeks_back: int = 4) -> str:
        """Format a full weekly stats report for Telegram."""
        stats = self.get_weekly_stats(weeks_back)
        at_stats = self.get_all_time_stats()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        lines = [
            f"📊 SPICY JOURNAL — {today}",
            f"═══════════════════════════",
        ]

        # All-time summary
        at = at_stats
        if at["count"] > 0:
            lines.append(f"All-time: {at['count']} trades | {at['win_rate']:.0f}% WR | avg R: {at['avg_r']:.2f}")
        else:
            lines.append("No closed trades yet.")

        lines.append("")
        for week_label, w in stats.items():
            c = w["count"]
            if c == 0:
                continue
            wr = w["win_rate"]
            avg_r = w["avg_r"]
            best = w["best_r"]
            worst = w["worst_r"]
            total_pnl = w["total_pnl"]
            mr = w["by_scanner"].get("mean_reversion", {})
            momo = w["by_scanner"].get("momentum", {})

            lines.append(f"{week_label}:")
            lines.append(f"  {c} trades | {w['wins']}W/{w['losses']}L | {wr:.0f}% WR")
            lines.append(f"  avg R: {avg_r:.2f} | best: {best:+.2f}R | worst: {worst:+.2f}R")
            if total_pnl:
                lines.append(f"  PnL: ${total_pnl:+.2f}")
            if mr["count"]:
                lines.append(f"  MR: {mr['count']} trades | {mr['win_rate']:.0f}% WR | avg R: {mr.get('avg_r',0):.2f}")
            if momo["count"]:
                lines.append(f"  MOMO: {momo['count']} trades | {momo['win_rate']:.0f}% WR | avg R: {momo.get('avg_r',0):.2f}")
            lines.append("")

        # Recent closed trades
        recent = self.get_closed_trades(days_back=14)
        if recent:
            lines.append("Recent closed trades:")
            for t in recent[:10]:
                outcome_icon = "✅" if t["outcome"] == "win" else "❌" if t["outcome"] == "loss" else "➖"
                r = f"{t['r_multiple']:+.2f}R" if t["r_multiple"] else "—"
                pnl = f"${t['pnl_usd']:+.2f}" if t["pnl_usd"] else ""
                scanner_tag = "MR" if t["scanner"] == "mean_reversion" else "MOMO"
                date = t["signal_ts"][:10]
                lines.append(
                    f"  {outcome_icon} {date} {scanner_tag} {t['coin']} {t['direction']} {r} {pnl}"
                )

        return "\n".join(lines)

    def format_signal_card(self, trade: dict) -> str:
        """Format a single signal as a readable Telegram card."""
        prefix = "🔴 SHORT" if trade["direction"].upper() == "SHORT" else "🟢 LONG"
        scanner_tag = "MR" if trade["scanner"] == "mean_reversion" else "MOMO"
        score = trade["quality_score"] or 0
        score_emoji = "🟢" * score + "⚪" * (3 - score)
        mr_str = f"MR {trade['mr_score']:.1f}" if trade.get("mr_score") else ""
        flags = json.loads(trade["flags"] or "[]")
        flags_str = " ".join(f"#{f}" for f in flags)
        la = trade["level_approach"] or "unclear"
        vr = trade["volume_regime"] or "unclear"
        thesis = trade["thesis"] or ""
        date = trade["signal_ts"][:16]

        card = [
            f"{prefix} {trade['coin']} {score_emoji} {mr_str}",
            f"─────────────────────────────────",
            f"SL: {trade['sl_price']}  TP: {trade['tp_price'] or '?'}  ID: {trade['trade_id']}",
            f"Approach: {la} | Vol: {vr}",
            f"{flags_str}",
            f"",
            f"Thesis: {thesis}",
            f"",
            f"Signal: {date} | {scanner_tag}",
        ]
        return "\n".join(line for line in card if line)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys

    j = Journal()

    parser = argparse.ArgumentParser(description="Spicy Trading Journal")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("add", help="Log a new signal")
    p.add_argument("--scanner", required=True, choices=["mean_reversion","momentum"])
    p.add_argument("--direction", required=True, choices=["long","short"])
    p.add_argument("--coin", required=True)
    p.add_argument("--sl")
    p.add_argument("--tp")
    p.add_argument("--quality", type=int, default=0)
    p.add_argument("--mr-score", type=float)
    p.add_argument("--flags", nargs="*")
    p.add_argument("--level", default="unclear")
    p.add_argument("--vol", default="unclear")
    p.add_argument("--thesis")
    p.add_argument("--entry-price", type=float)
    p.add_argument("--entry-type")
    p.add_argument("--signal-ts")

    u = sub.add_parser("update", help="Update trade outcome")
    u.add_argument("--trade-id", required=True)
    u.add_argument("--outcome", required=True, choices=["win","loss","breakeven","cancelled"])
    u.add_argument("--exit-price", type=float)
    u.add_argument("--pnl-usd", type=float)
    u.add_argument("--pnl-pct", type=float)
    u.add_argument("--r-multiple", type=float)
    u.add_argument("--notes")

    sub.add_parser("open", help="Show open trades")
    sub.add_parser("report", help="Print weekly report")

    args = parser.parse_args()

    if args.cmd == "add":
        trade_id = j.log_signal(
            scanner=args.scanner, direction=args.direction, coin=args.coin,
            sl_price=float(args.sl) if args.sl else None,
            tp_price=float(args.tp) if args.tp else None,
            entry_price=args.entry_price, entry_type=args.entry_type,
            quality_score=args.quality, mr_score=args.mr_score,
            flags=args.flags, level_approach=args.level,
            volume_regime=args.vol, thesis=args.thesis,
            signal_ts=args.signal_ts
        )
        print(f"Logged: {trade_id}")

    elif args.cmd == "update":
        j.update_outcome(
            trade_id=args.trade_id, outcome=args.outcome,
            exit_price=args.exit_price, pnl_usd=args.pnl_usd,
            pnl_pct=args.pnl_pct, r_multiple=args.r_multiple, notes=args.notes
        )
        print(f"Updated: {args.trade_id}")

    elif args.cmd == "open":
        print(j.format_open_trades())

    elif args.cmd == "report":
        print(j.format_weekly_report())

    else:
        parser.print_help()
