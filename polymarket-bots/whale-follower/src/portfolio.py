"""
Portfolio Manager — Tracks positions, bankroll, PnL, and risk limits.
"""
import json
import csv
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from .config import STATE_FILE, LOG_FILE, CIRCUIT_BREAKER_LOSS_PCT, MAX_DAILY_TRADES


class Portfolio:
    def __init__(self, initial_bankroll: float = 10_000.0):
        self.initial_bankroll = initial_bankroll
        self.bankroll = initial_bankroll
        self.positions: dict[str, dict] = {}   # slug -> position dict
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_start_bankroll = initial_bankroll
        self.trade_log: list[dict] = []
        self.load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def load(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.bankroll = data.get("bankroll", self.initial_bankroll)
                self.initial_bankroll = data.get("initial_bankroll", self.initial_bankroll)
                self.positions = data.get("positions", {})
                self.daily_start_bankroll = data.get("daily_start_bankroll", self.initial_bankroll)
                self.daily_pnl = data.get("daily_pnl", 0.0)
                self.daily_trades = data.get("daily_trades", 0)
                print(f"[portfolio] Loaded state: bankroll=${self.bankroll:,.2f}, {len(self.positions)} positions")
            except Exception as e:
                print(f"[portfolio] Failed to load state: {e}")

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "bankroll": self.bankroll,
            "initial_bankroll": self.initial_bankroll,
            "positions": self.positions,
            "daily_start_bankroll": self.daily_start_bankroll,
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        STATE_FILE.write_text(json.dumps(data, indent=2))
        print(f"[portfolio] Saved state: bankroll=${self.bankroll:,.2f}")

    # ── Risk Checks ────────────────────────────────────────────────────────────

    def can_trade(self) -> tuple[bool, str]:
        """Check if we're within risk limits to take new trades."""
        if self.daily_trades >= MAX_DAILY_TRADES:
            return False, f"Daily trade limit reached ({MAX_DAILY_TRADES})"
        loss_pct = (self.daily_start_bankroll - self.bankroll) / self.daily_start_bankroll
        if loss_pct >= CIRCUIT_BREAKER_LOSS_PCT:
            return False, f"Circuit breaker: down {loss_pct*100:.1f}% today"
        return True, "OK"

    def check_position_limits(self) -> tuple[bool, str]:
        from .config import MAX_CONCURRENT_TRADES
        if len(self.positions) >= MAX_CONCURRENT_TRADES:
            return False, f"Max concurrent positions ({MAX_CONCURRENT_TRADES})"
        return True, "OK"

    # ── Position Management ─────────────────────────────────────────────────────

    def open_position(self, slug: str, side: str, price: float, size: float, cost: float, whale: str) -> bool:
        """Open or add to a position. Returns False if insufficient bankroll."""
        cost = float(cost)
        if cost > self.bankroll:
            print(f"[portfolio] Insufficient bankroll: need ${cost:.2f}, have ${self.bankroll:.2f}")
            return False

        if slug in self.positions:
            pos = self.positions[slug]
            pos["size"] += size if side == "BUY" else -size
            pos["avg_price"] = (
                (pos["avg_price"] * abs(pos["size"] - size) + price * size) / abs(pos["size"])
                if pos["size"] != 0 else price
            )
            pos["whale"] = whale
        else:
            self.positions[slug] = {
                "side": side,
                "size": size if side == "BUY" else -size,
                "avg_price": price,
                "whale": whale,
                "opened_at": datetime.now(timezone.utc).isoformat(),
            }

        self.bankroll -= cost
        self.daily_trades += 1
        self.save()
        return True

    def mark_position_closed(self, slug: str, pnl: float):
        """Close a position and record PnL."""
        if slug in self.positions:
            del self.positions[slug]
        self.bankroll += pnl
        self.daily_pnl += pnl
        self.save()

    # ── PnL ─────────────────────────────────────────────────────────────────────

    def current_pnl(self) -> float:
        return self.bankroll - self.initial_bankroll

    def daily_pnl_pct(self) -> float:
        return (self.bankroll - self.daily_start_bankroll) / self.daily_start_bankroll

    def reset_daily(self):
        """Reset daily counters at start of new trading day."""
        self.daily_start_bankroll = self.bankroll
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.save()
        print(f"[portfolio] Daily reset: bankroll=${self.bankroll:,.2f}")

    def log_trade(self, trade: dict):
        """Append a trade record to the persistent CSV log."""
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_exists = LOG_FILE.exists()
        with open(LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade)
