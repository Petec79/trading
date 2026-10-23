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
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

try:                                   # package import (from journal import Journal)
    from journal import stats as S
except ImportError:                    # run as a script: python journal/journal.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import stats as S

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

-- Your own (manual) trades, optionally linked to the bot signal you took.
CREATE TABLE IF NOT EXISTS manual_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_trade_id TEXT,                       -- trades.trade_id of the bot signal (optional)
    coin            TEXT    NOT NULL,
    direction       TEXT    NOT NULL,           -- LONG | SHORT
    venue           TEXT,
    entry_price     REAL    NOT NULL,
    stop_price      REAL    NOT NULL,           -- your stop at entry (defines your 1R)
    target_price    REAL,
    size_usd        REAL,
    entry_ts        TEXT    NOT NULL,
    exit_price      REAL,
    exit_ts         TEXT,
    fees_usd        REAL    DEFAULT 0,
    r_multiple      REAL,
    pnl_usd         REAL,
    status          TEXT    DEFAULT 'open',     -- open | closed
    notes           TEXT
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
            cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
            for col in ("setup", "exit_reason"):          # added for the v2 bots
                if col not in cols:
                    con.execute(f"ALTER TABLE trades ADD COLUMN {col} TEXT")

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
        setup: Optional[str] = None,
    ) -> str:
        """
        Log a new trade signal. Returns the unique trade_id.
        If the same (scanner, coin, direction, date) already exists as pending,
        does nothing and returns the existing trade_id.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # One prefix per scanner — momentum_v2 and mean_reversion_v2 used to share
        # "MOMO" and collided on the UNIQUE trade_id.
        prefix = {"mean_reversion": "MR", "momentum": "MOMO",
                  "momentum_v2": "MOMO2", "mean_reversion_v2": "MR2"}.get(scanner, scanner.upper()[:6])

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

        # Next free number for this prefix today (based on existing ids, so a
        # back-dated signal_ts can never produce a duplicate)
        stem = f"{prefix}-{today.replace('-','')}-"
        with sqlite3.connect(self.db) as con:
            rows = con.execute("SELECT trade_id FROM trades WHERE trade_id LIKE ?", (stem + "%",)).fetchall()
        nums = [int(r[0][len(stem):]) for r in rows if r[0][len(stem):].isdigit()]
        count = max(nums, default=0) + 1

        trade_id = f"{stem}{count:03d}"
        flags_json = json.dumps(flags or [])
        now = datetime.now(timezone.utc).isoformat()
        sig_ts = signal_ts or now

        with sqlite3.connect(self.db) as con:
            con.execute("""
                INSERT INTO trades (
                    trade_id, scanner, direction, coin, signal_ts,
                    sl_price, tp_price, entry_price, quality_score, mr_score,
                    flags, entry_type, level_approach, volume_regime, thesis, created_at, setup
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, scanner, direction.upper(), coin.upper(),
                sig_ts, sl_price, tp_price, entry_price, quality_score, mr_score,
                flags_json, entry_type, level_approach, volume_regime, thesis, now, setup
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
        exit_ts: Optional[str] = None,      # when it actually closed (default: now)
        exit_reason: Optional[str] = None,
    ):
        """Update a trade with its realized outcome."""
        exit_ts = exit_ts or datetime.now(timezone.utc).isoformat()
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
                    reviewed  = ?,
                    exit_reason = COALESCE(?, exit_reason)
                WHERE trade_id = ?
            """, (outcome, exit_ts, exit_price, pnl_usd, pnl_pct,
                  r_multiple, notes, 1 if reviewed else 0, exit_reason, trade_id))

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
        exit_ts: Optional[str] = None,       # when it actually closed (the v2 bots pass this)
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

        # Paper P&L in $: R × the risk per trade used by the alerts ($10k × 1% = $100)
        risk_usd = float(os.environ.get("ALERT_ACCOUNT", "10000")) * float(os.environ.get("ALERT_RISK_PCT", "1")) / 100
        pnl_usd = round(pnl_r * risk_usd, 2)
        d = 1 if direction == "LONG" else -1
        pnl_pct = round((exit_price - entry_price) * d / entry_price * 100, 3) if entry_price else None

        self.update_outcome(
            trade_id=trade_id,
            outcome=outcome,
            exit_price=exit_price,
            r_multiple=round(pnl_r, 3),
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            notes=f"{exit_reason}" + (f": {notes}" if notes else ""),
            exit_ts=exit_ts,
            exit_reason=exit_reason,
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
        """Closed trades (win/loss/breakeven) that EXITED in the last N days, newest first."""
        start = datetime.now(timezone.utc) - timedelta(days=days_back)
        rows = S.in_window(S.closed_trades(self._all_rows()), start, datetime.now(timezone.utc) + timedelta(days=1))
        return sorted(rows, key=lambda t: S.parse_ts(t["exit_ts"]), reverse=True)

    def _all_rows(self) -> list[dict]:
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute("SELECT * FROM trades")]

    def get_weekly_stats(self, weeks_back: int = 4) -> dict:
        """Stats per calendar week (Mon 00:00 → next Mon 00:00 UTC), by EXIT time."""
        rows = self._all_rows()
        now = datetime.now(timezone.utc)
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        out = {}
        for week in range(weeks_back):
            start = monday - timedelta(weeks=week)
            out[f"Week of {start.date().isoformat()}"] = self._compute_stats(S.in_window(rows, start, start + timedelta(weeks=1)))
        return out

    def get_all_time_stats(self) -> dict:
        """Compute all-time stats across all closed trades."""
        return self._compute_stats(self._all_rows())

    def _compute_stats(self, trades: list[dict]) -> dict:
        """Shared stats (journal/stats.py), plus the legacy keys older callers read."""
        f = S.full(trades)
        sm = f["summary"]

        def legacy(group: dict) -> dict:
            return {k: {"count": v["closed"], "wins": v["wins"], "win_rate": v["win_rate"],
                        "avg_r": v["expectancy_r"], **v} for k, v in group.items()}
        return {
            "count": sm["closed"], "wins": sm["wins"], "losses": sm["losses"], "breakeven": sm["breakeven"],
            "win_rate": sm["win_rate"], "avg_r": sm["expectancy_r"],
            "best_r": sm["best_r"], "worst_r": sm["worst_r"], "total_pnl": sm["total_pnl_usd"],
            "by_scanner": legacy(f["by_scanner"]), "by_direction": legacy(f["by_side"]),
            "summary": sm, "breakdowns": f,
            "trades": S.closed_trades(trades),
        }

    # ── Manual trades (you, trading the signals by hand) ─────────────────────

    def manual_open(self, coin: str, direction: str, entry_price: float, stop_price: float,
                    size_usd: Optional[float] = None, target_price: Optional[float] = None,
                    venue: Optional[str] = None, signal_trade_id: Optional[str] = None,
                    entry_ts: Optional[str] = None, notes: Optional[str] = None) -> int:
        if (entry_price - stop_price) * (1 if direction.upper() == "LONG" else -1) <= 0:
            raise ValueError("stop must be below entry for a long, above entry for a short")
        with sqlite3.connect(self.db) as con:
            cur = con.execute("""
                INSERT INTO manual_trades (signal_trade_id, coin, direction, venue, entry_price, stop_price,
                    target_price, size_usd, entry_ts, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (signal_trade_id, coin.upper(), direction.upper(), venue, entry_price, stop_price,
                  target_price, size_usd, entry_ts or datetime.now(timezone.utc).isoformat(), notes))
            return cur.lastrowid

    def manual_close(self, mid: int, exit_price: float, fees_usd: float = 0.0,
                     exit_ts: Optional[str] = None, notes: Optional[str] = None) -> dict:
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            r = con.execute("SELECT * FROM manual_trades WHERE id=? AND status='open'", (mid,)).fetchone()
            if not r:
                raise ValueError(f"no open manual trade #{mid}")
            d = 1 if r["direction"] == "LONG" else -1
            risk = abs(r["entry_price"] - r["stop_price"])
            gross = (exit_price - r["entry_price"]) * d
            pnl_usd = None
            r_mult = gross / risk
            if r["size_usd"]:
                pnl_usd = round(r["size_usd"] * gross / r["entry_price"] - (fees_usd or 0), 2)
                risk_usd = r["size_usd"] * risk / r["entry_price"]
                r_mult = pnl_usd / risk_usd if risk_usd else r_mult      # net of fees when size is known
            con.execute("""UPDATE manual_trades SET exit_price=?, exit_ts=?, fees_usd=?, r_multiple=?, pnl_usd=?,
                           status='closed', notes=COALESCE(?, notes) WHERE id=?""",
                        (exit_price, exit_ts or datetime.now(timezone.utc).isoformat(), fees_usd,
                         round(r_mult, 3), pnl_usd, notes, mid))
        return {"id": mid, "r_multiple": round(r_mult, 3), "pnl_usd": pnl_usd}

    def manual_trades(self, status: Optional[str] = None) -> list[dict]:
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            q = "SELECT * FROM manual_trades" + (" WHERE status=?" if status else "") + " ORDER BY entry_ts"
            return [dict(r) for r in con.execute(q, (status,) if status else ())]

    def manual_vs_paper(self) -> list[dict]:
        """Each closed manual trade next to the bot's paper result for the same signal."""
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT m.*, t.entry_price AS paper_entry, t.r_multiple AS paper_r, t.outcome AS paper_outcome,
                       t.scanner AS scanner, t.setup AS setup
                FROM manual_trades m LEFT JOIN trades t ON t.trade_id = m.signal_trade_id
                WHERE m.status='closed' ORDER BY m.exit_ts""").fetchall()
        out = []
        for r in rows:
            r = dict(r)
            d = 1 if r["direction"] == "LONG" else -1
            if r.get("paper_entry"):
                # positive = you got a WORSE price than the bot's paper fill
                r["entry_slippage_pct"] = round((r["entry_price"] - r["paper_entry"]) * d / r["paper_entry"] * 100, 3)
            out.append(r)
        return out

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
                f"{prefix} {t['coin']} {score_emoji}{mr_str}  [{t['trade_id']}]\n"
                f"   SL: {t['sl_price']}  TP: {t['tp_price'] or '?'}  {flag_str}"
            )
        return "OPEN TRADES:\n" + "\n".join(lines)

    def format_weekly_report(self, weeks_back: int = 4) -> str:
        """The weekly journal report (Telegram-ready). Same numbers as every other report."""
        names = {"momentum_v2": "Momentum", "mean_reversion_v2": "Mean reversion"}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        at = self.get_all_time_stats()
        L = [f"📊 SPICY JOURNAL — week report {today}", "(paper trades, R after fees)", ""]
        if not at["count"]:
            L.append("No closed trades yet.")
        else:
            L.append("All time: " + S.fmt_line(at["summary"]))
            for sc, v in at["breakdowns"]["by_scanner"].items():
                L.append(f"  {names.get(sc, sc)}: " + S.fmt_line(v))
            by_setup = at["breakdowns"]["by_setup"]
            if len(by_setup) > 1:
                L.append("  by setup: " + " | ".join(
                    f"{k} {v['closed']}t {v['expectancy_r']:+.2f}R" for k, v in by_setup.items()))
        L.append("")
        for label, w in self.get_weekly_stats(weeks_back).items():
            if w["count"]:
                L.append(f"{label}: " + S.fmt_line(w["summary"]))
        recent = self.get_closed_trades(days_back=14)
        if recent:
            L += ["", "Last 14 days:"]
            for t in recent[:10]:
                icon = "✅" if t["outcome"] == "win" else "❌" if t["outcome"] == "loss" else "➖"
                side = "🟢" if t["direction"].upper() == "LONG" else "🔴"
                L.append(f"  {icon} {(t['exit_ts'] or '')[:10]} {names.get(t['scanner'], t['scanner'])} "
                         f"{side}{t['coin'].replace('USDT', '')} {S.setup_of(t)} {t['r_multiple']:+.2f}R "
                         f"({S.exit_reason_of(t)})")
        mvp = self.manual_vs_paper()
        if mvp:
            mine = sum(m["r_multiple"] or 0 for m in mvp)
            paper = sum(m["paper_r"] or 0 for m in mvp if m.get("paper_r") is not None)
            slips = [m["entry_slippage_pct"] for m in mvp if m.get("entry_slippage_pct") is not None]
            L += ["", f"Your manual trades: {len(mvp)} closed · yours {mine:+.2f}R vs bot paper {paper:+.2f}R"
                  + (f" · avg entry slippage {sum(slips) / len(slips):+.3f}%" if slips else "")]
        if at["count"] and at["count"] < 30:
            L += ["", f"⚠️ {at['count']} trades — too few to judge yet (aim for 30+ per bot)"]
        return "\n".join(L)

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
    p.add_argument("--scanner", required=True,
                   choices=["mean_reversion", "momentum", "momentum_v2", "mean_reversion_v2"])
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

    mo = sub.add_parser("manual-open", help="Log a trade YOU took (optionally linked to a bot signal)")
    mo.add_argument("--coin", required=True)
    mo.add_argument("--direction", required=True, choices=["long", "short"])
    mo.add_argument("--entry", type=float, required=True)
    mo.add_argument("--stop", type=float, required=True)
    mo.add_argument("--target", type=float)
    mo.add_argument("--size-usd", type=float, help="position size (notional) in USD")
    mo.add_argument("--venue", default="breakout")
    mo.add_argument("--signal", help="bot trade_id, e.g. MR2-20261023-001 (shown in `open`)")
    mo.add_argument("--notes")

    mc = sub.add_parser("manual-close", help="Close one of your manual trades")
    mc.add_argument("--id", type=int, required=True)
    mc.add_argument("--exit", type=float, required=True)
    mc.add_argument("--fees", type=float, default=0.0, help="total fees in USD")
    mc.add_argument("--notes")

    sub.add_parser("manual", help="List your manual trades vs the bot's paper result")

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

    elif args.cmd == "manual-open":
        mid = j.manual_open(args.coin if args.coin.upper().endswith("USDT") else args.coin + "USDT",
                            args.direction, args.entry, args.stop, args.size_usd, args.target,
                            args.venue, args.signal, notes=args.notes)
        print(f"Manual trade #{mid} opened")

    elif args.cmd == "manual-close":
        res = j.manual_close(args.id, args.exit, args.fees, notes=args.notes)
        print(f"Manual trade #{res['id']} closed: {res['r_multiple']:+.2f}R"
              + (f" (${res['pnl_usd']:+.2f})" if res["pnl_usd"] is not None else ""))

    elif args.cmd == "manual":
        for m in j.manual_trades():
            print(f"#{m['id']} {m['status']:6} {m['coin']} {m['direction']} entry {m['entry_price']} "
                  f"stop {m['stop_price']} exit {m['exit_price'] or '-'} R {m['r_multiple'] if m['r_multiple'] is not None else '-'}"
                  f" signal {m['signal_trade_id'] or '-'}")
        for m in j.manual_vs_paper():
            print(f"  #{m['id']} yours {m['r_multiple']:+.2f}R vs paper "
                  f"{m['paper_r'] if m['paper_r'] is not None else '-'}R, entry slippage {m.get('entry_slippage_pct', '-')}%")

    else:
        parser.print_help()
