#!/usr/bin/env python3
"""
Paper Trade Runner — Polymarket Bots
Runs the whale follower in paper trade mode, tracks all outcomes,
resolves markets, and computes Sharpe ratio per strategy.

Usage:
  python paper_trade_runner.py run     — Run continuous paper trading loop
  python paper_trade_runner.py report  — Generate paper trade report
  python paper_trade_runner.py status  — Show current tracking status
"""

import json
import time
import math
import sqlite3
import requests
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

# Add parent dir for imports
import sys
sys.path.insert(0, str(Path(__file__).parent))

from whale_follower import (
    init_db, get_db_connection, fetch_top_wallets, save_wallets,
    fetch_active_markets, generate_signals, run_challenges,
    execute_signals, BANKROLL, GAMMA_API, PAPER_TRADE,
    quarter_kelly, bet_size
)

# =============================================================================
# PATHS
# =============================================================================

PAPER_TRADES_DIR = Path(__file__).parent / "paper-trades"
PAPER_TRADES_DIR.mkdir(exist_ok=True)
TRADES_LOG = PAPER_TRADES_DIR / "trades.jsonl"
RESOLVED_DIR = PAPER_TRADES_DIR / "resolved"
RESOLVED_DIR.mkdir(exist_ok=True)

DB_PATH = Path.home() / ".polyterm" / "whale_follower.db"

# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class PaperTrade:
    """A single paper trade with entry and exit data."""
    trade_id: str
    signal_id: int
    wallet_address: str
    market_slug: str
    market_question: str
    side: str           # 'yes' or 'no'
    entry_price: float
    exit_price: Optional[float]
    size: float         # $ amount
    ev_at_entry: float  # expected value at entry (yes_price)
    confidence: str
    category: str
    entered_at: str
    resolved_at: Optional[str]
    market_end_date: Optional[str]
    pnl: Optional[float]
    outcome: Optional[str]  # 'win', 'loss', 'pending'
    days_held: Optional[float]
    strategy: str = "whale_follower"  # for multi-strategy reporting


@dataclass
class StrategyStats:
    """Statistics for a single strategy."""
    strategy: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    sharpe_ratio: float
    ev_edge: float        # avg EV captured vs naive holding
    naive_total: float     # what naive holder would have
    best_trade: float
    worst_trade: float
    avg_days_held: float
    max_drawdown: float


# =============================================================================
# TRADE LOGGING
# =============================================================================

def log_trade(trade: PaperTrade):
    """Append a trade to the JSONL log."""
    with open(TRADES_LOG, "a") as f:
        f.write(json.dumps(asdict(trade)) + "\n")


def load_trades() -> list[PaperTrade]:
    """Load all trades from JSONL log."""
    trades = []
    if TRADES_LOG.exists():
        with open(TRADES_LOG, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    trades.append(PaperTrade(**data))
    return trades


def load_resolved() -> list[PaperTrade]:
    """Load resolved trades from the resolved directory."""
    trades = []
    for f in RESOLVED_DIR.glob("*.json"):
        with open(f, "r") as fh:
            data = json.loads(fh.read())
            for trade_data in data.values():
                trades.append(PaperTrade(**trade_data))
    return trades


def save_resolved_batch(trades: list[PaperTrade], batch_id: str):
    """Save a batch of resolved trades."""
    path = RESOLVED_DIR / f"{batch_id}.json"
    data = {t.trade_id: asdict(t) for t in trades}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    # Remove from main log
    if TRADES_LOG.exists():
        existing = load_trades()
        pending = [t for t in existing if t.outcome == "pending"]
        with open(TRADES_LOG, "w") as f:
            for t in pending:
                f.write(json.dumps(asdict(t)) + "\n")


# =============================================================================
# MARKET RESOLUTION
# =============================================================================

def fetch_market_result(slug: str) -> Optional[dict]:
    """
    Check if a market is resolved and get the outcome.
    Returns {resolved: bool, outcome: str, final_price: float} or None on error.
    """
    try:
        resp = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=10)
        if not resp.ok:
            return None
        
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            return None
        
        market = data[0]
        closed = market.get("closed", False)
        end_date = market.get("endDate", "")
        
        # Get the YES price (final price if closed)
        yes_price = float(market.get("yesPrice", 0.5) or 0.5)
        
        return {
            "resolved": closed,
            "outcome": "win" if yes_price > 0.5 else "loss",
            "final_price": yes_price,
            "end_date": end_date
        }
    except Exception as e:
        print(f"  fetch_market_result error for {slug}: {e}")
        return None


def resolve_pending_trades():
    """
    Check all pending trades — if their markets have resolved, update PnL.
    This should be called periodically (e.g., every 15 minutes).
    """
    pending = [t for t in load_trades() if t.outcome == "pending"]
    if not pending:
        return 0, 0
    
    print(f"\n=== Resolving {len(pending)} pending trades ===")
    
    resolved = []
    still_pending = []
    batch_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    
    for trade in pending:
        result = fetch_market_result(trade.market_slug)
        
        if result is None:
            still_pending.append(trade)
            continue
        
        if result["resolved"]:
            # Market has closed — resolve the trade
            trade.exit_price = result["final_price"]
            trade.resolved_at = datetime.utcnow().isoformat()
            trade.market_end_date = result["end_date"]
            
            if trade.side == "yes":
                # Bought YES: win if final_price > 0.5 (market resolved YES)
                trade.outcome = "win" if result["final_price"] > 0.5 else "loss"
            else:
                # Bought NO: win if final_price < 0.5 (market resolved NO)
                trade.outcome = "win" if result["final_price"] < 0.5 else "loss"
            
            # Calculate PnL
            if trade.outcome == "win":
                # Bought at entry_price, paid entry_price * size, won (1 - entry_price) * size
                trade.pnl = (1.0 - trade.entry_price) * trade.size
            else:
                # Lost the position size
                trade.pnl = -trade.size
            
            # Days held
            entered = datetime.fromisoformat(trade.entered_at)
            resolved_at = datetime.fromisoformat(trade.resolved_at)
            trade.days_held = (resolved_at - entered).total_seconds() / 86400
            
            resolved.append(trade)
            print(f"  RESOLVED {trade.trade_id[:8]}: {trade.outcome.upper()} | "
                  f"${trade.pnl:+.2f} | held {trade.days_held:.1f}d")
        else:
            # Check if market end_date has passed even if not marked closed
            if trade.market_end_date:
                try:
                    end = datetime.fromisoformat(trade.market_end_date.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) > end:
                        # Past end date but not marked closed — likely resolved
                        trade.exit_price = 0.5  # Unknown — treat as uncertain
                        trade.resolved_at = datetime.utcnow().isoformat()
                        trade.market_end_date = trade.market_end_date
                        trade.outcome = "pending"  # Keep as pending, API not updated yet
                        still_pending.append(trade)
                        continue
                except:
                    pass
            still_pending.append(trade)
    
    # Save resolved batch
    if resolved:
        save_resolved_batch(resolved, batch_id)
    
    print(f"  Resolved: {len(resolved)} | Still pending: {len(still_pending)}")
    return len(resolved), len(still_pending)


# =============================================================================
# ANALYTICS
# =============================================================================

def compute_sharpe(pnls: list[float], risk_free_rate: float = 0.0) -> float:
    """Compute Sharpe ratio from a list of PnLs."""
    if len(pnls) < 2:
        return 0.0
    
    mean = sum(pnls) / len(pnls)
    std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1))
    
    if std == 0:
        return 0.0
    
    # Annualize assuming ~365 trades/year (1 trade per day)
    sharpe = (mean - risk_free_rate) / std * math.sqrt(365)
    return round(sharpe, 2)


def max_drawdown(pnls: list[float]) -> float:
    """Compute max drawdown from PnL series."""
    if not pnls:
        return 0.0
    
    cumulative = []
    running = 0.0
    for p in pnls:
        running += p
        cumulative.append(running)
    
    peak = cumulative[0]
    max_dd = 0.0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def compute_strategy_stats(trades: list[PaperTrade], strategy: str = "whale_follower") -> StrategyStats:
    """Compute statistics for a strategy from resolved trades."""
    strat_trades = [t for t in trades if t.strategy == strategy and t.outcome in ("win", "loss")]
    
    if not strat_trades:
        return StrategyStats(
            strategy=strategy, total_trades=0, wins=0, losses=0,
            win_rate=0.0, avg_pnl=0.0, total_pnl=0.0, sharpe_ratio=0.0,
            ev_edge=0.0, naive_total=0.0, best_trade=0.0, worst_trade=0.0,
            avg_days_held=0.0, max_drawdown=0.0
        )
    
    wins = [t for t in strat_trades if t.outcome == "win"]
    losses = [t for t in strat_trades if t.outcome == "loss"]
    pnls = [t.pnl for t in strat_trades if t.pnl is not None]
    
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / len(pnls) if pnls else 0.0
    best = max(pnls) if pnls else 0.0
    worst = min(pnls) if pnls else 0.0
    
    # Naive baseline: what if you just held the position size in cash?
    # Actually: what if you bought YES at 0.5 (EV=0) and held — expected value = 0
    # Better baseline: compare to just holding YES at entry price (no Kelly sizing)
    # EV at entry = entry_price (that's what we expected to win)
    ev_captured = [t.ev_at_entry * t.size for t in strat_trades]  # EV we thought we'd make
    naive_total = sum(ev_captured)  # Sum of expected values
    ev_edge = total_pnl - naive_total if strat_trades else 0.0
    
    days_held = [t.days_held for t in strat_trades if t.days_held is not None]
    
    sharpe = compute_sharpe(pnls)
    dd = max_drawdown(pnls)
    
    return StrategyStats(
        strategy=strategy,
        total_trades=len(strat_trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(strat_trades) if strat_trades else 0.0,
        avg_pnl=round(avg_pnl, 2),
        total_pnl=round(total_pnl, 2),
        sharpe_ratio=sharpe,
        ev_edge=round(ev_edge, 2),
        naive_total=round(naive_total, 2),
        best_trade=round(best, 2),
        worst_trade=round(worst, 2),
        avg_days_held=round(sum(days_held) / len(days_held), 1) if days_held else 0.0,
        max_drawdown=dd
    )


# =============================================================================
# REPORTING
# =============================================================================

def generate_report() -> str:
    """Generate a full paper trading report."""
    resolved_trades = load_resolved()
    pending_trades = [t for t in load_trades() if t.outcome == "pending"]
    
    all_trades = resolved_trades + pending_trades
    resolved_only = [t for t in all_trades if t.outcome in ("win", "loss")]
    
    stats = compute_strategy_stats(resolved_only)
    
    # Build report
    lines = []
    lines.append("=" * 70)
    lines.append("POLYMARKET PAPER TRADE REPORT")
    lines.append(f"Generated: {datetime.utcnow().isoformat()}")
    lines.append("=" * 70)
    
    # Track all trades (resolved + pending)
    all_trades_count = len(resolved_only) + len(pending_trades)
    
    lines.append(f"\n--- OVERVIEW ---")
    lines.append(f"  Total signals:       {all_trades_count}")
    lines.append(f"  Resolved:            {len(resolved_only)}")
    lines.append(f"  Pending:             {len(pending_trades)}")
    lines.append(f"  Wins:                {stats.wins}")
    lines.append(f"  Losses:              {stats.losses}")
    lines.append(f"  Win rate:            {stats.win_rate:.1%}")
    lines.append(f"  Progress:             {len(resolved_only)}/200 resolved trades")
    
    lines.append(f"\n--- PnL ---")
    lines.append(f"  Total PnL:           ${stats.total_pnl:+.2f}")
    lines.append(f"  Avg PnL per trade:   ${stats.avg_pnl:+.2f}")
    lines.append(f"  Best trade:          ${stats.best_trade:+.2f}")
    lines.append(f"  Worst trade:         ${stats.worst_trade:+.2f}")
    lines.append(f"  Max drawdown:        ${stats.max_drawdown:.2f}")
    
    lines.append(f"\n--- RISK-ADJUSTED ---")
    lines.append(f"  Sharpe ratio:        {stats.sharpe_ratio:.2f}")
    lines.append(f"  Avg days held:       {stats.avg_days_held:.1f}")
    
    lines.append(f"\n--- EDGE vs NAIVE BASELINE ---")
    lines.append(f"  Naive EV total:      ${stats.naive_total:.2f}")
    lines.append(f"  Actual PnL:          ${stats.total_pnl:.2f}")
    lines.append(f"  EV edge captured:    ${stats.ev_edge:+.2f}")
    
    # Decision — need 200+ trades AND Sharpe >= 2.0
    # Note: trades_ok counts total signals entered; sharpe requires resolved trades
    lines.append(f"\n--- GO/NO-GO DECISION ---")
    sharpe_ok = stats.sharpe_ratio >= 2.0
    trades_ok = all_trades_count >= 200
    resolved_ok = len(resolved_only) >= 200
    
    if sharpe_ok and resolved_ok:
        lines.append(f"  >>> GO LIVE — Sharpe {stats.sharpe_ratio:.2f} >= 2.0 with {len(resolved_only)} resolved trades")
    elif trades_ok and not resolved_ok:
        lines.append(f"  >>> GATHERING DATA — {all_trades_count} signals sent, waiting for {200 - len(resolved_only)} more resolved (markets still open)")
    elif resolved_ok and not sharpe_ok:
        lines.append(f"  >>> NOT READY — Sharpe {stats.sharpe_ratio:.2f} < 2.0 (need 200+ resolved trades)")
    else:
        lines.append(f"  >>> NOT READY — {len(resolved_only)}/200 resolved, Sharpe {stats.sharpe_ratio:.2f} < 2.0")
    
    # Recent trades
    if resolved_only:
        lines.append(f"\n--- RECENT TRADES (last 10) ---")
        recent = sorted(resolved_only, key=lambda t: t.resolved_at or "", reverse=True)[:10]
        for t in recent:
            emoji = "✅" if t.outcome == "win" else "❌"
            pnl_str = f"${t.pnl:+.2f}" if t.pnl else "?"
            lines.append(f"  {emoji} {t.market_slug[:45]:<45} {t.side.upper():<4} {pnl_str:>8} | {t.days_held:.1f}d")
    
    lines.append("=" * 70)
    return "\n".join(lines)


def print_status():
    """Show current tracking status."""
    resolved_trades = load_resolved()
    pending_trades = [t for t in load_trades() if t.outcome == "pending"]
    all_trades = resolved_trades + pending_trades
    
    print(f"\n{'='*50}")
    print("PAPER TRADE STATUS")
    print(f"{'='*50}")
    print(f"  Resolved trades:  {len(resolved_trades)}")
    print(f"  Pending trades:    {len(pending_trades)}")
    print(f"  Total logged:      {len(all_trades)}")
    print(f"  Trades log:        {TRADES_LOG}")
    print(f"  Resolved dir:      {RESOLVED_DIR}")
    print()
    
    if len(resolved_trades) > 0:
        stats = compute_strategy_stats(resolved_trades)
        print(f"  Win rate:          {stats.win_rate:.1%}")
        print(f"  Total PnL:         ${stats.total_pnl:+.2f}")
        print(f"  Sharpe ratio:      {stats.sharpe_ratio:.2f}")
        print(f"  Go/no-go:          {'GO LIVE' if stats.sharpe_ratio >= 2.0 and stats.total_trades >= 200 else 'NOT READY'}")
    else:
        print("  No resolved trades yet.")
    print()


# =============================================================================
# MAIN RUN LOOP
# =============================================================================

def run_discovery_and_signals():
    """Run wallet discovery and signal generation (paper mode)."""
    print("\n=== PAPER TRADE CYCLE ===")
    print(f"Time: {datetime.utcnow().isoformat()}")
    
    # Step 1: Discover wallets (if DB is empty or stale)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM wallets")
    wallet_count = c.fetchone()[0]
    conn.close()
    
    if wallet_count == 0:
        print("No wallets tracked — running discovery...")
        wallets = fetch_top_wallets(limit=20)
        save_wallets(wallets)
        print(f"Saved {len(wallets)} wallets")
    else:
        print(f"Already tracking {wallet_count} wallets — skipping discovery")
    
    # Step 2: Generate and execute signals (paper trade)
    signals = generate_signals()
    print(f"Generated {len(signals)} signals")
    
    accepted = run_challenges(signals)
    print(f"{len(accepted)} signals passed challenge rounds")
    
    if accepted:
        execute_signals(accepted)
        
        # Log each as a paper trade
        for sig in accepted:
            trade = PaperTrade(
                trade_id=f"pt_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{sig.market_slug[:20]}",
                signal_id=0,
                wallet_address=sig.wallet_address,
                market_slug=sig.market_slug,
                market_question=sig.market_question,
                side=sig.side,
                entry_price=sig.entry_price,
                exit_price=None,
                size=sig.bet_size,
                ev_at_entry=sig.entry_price * sig.bet_size,  # Expected value = prob * payout
                confidence=sig.confidence,
                category=sig.category,
                entered_at=datetime.utcnow().isoformat(),
                resolved_at=None,
                market_end_date=sig.end_date,
                pnl=None,
                outcome="pending",
                days_held=None,
                strategy="whale_follower"
            )
            log_trade(trade)
            print(f"  LOGGED: {trade.trade_id[:20]}... {sig.side.upper()} ${sig.bet_size:.2f} on {sig.market_slug[:40]}")
    else:
        print("No signals — holding capital.")
    
    # Step 3: Resolve pending trades
    resolve_pending_trades()


def continuous_loop(interval_minutes: int = 15):
    """Run the paper trade loop continuously."""
    print(f"\nStarting paper trade loop — interval: {interval_minutes} minutes")
    print("Press Ctrl+C to stop\n")
    
    while True:
        try:
            run_discovery_and_signals()
            print(f"\nSleeping {interval_minutes} minutes until next cycle...")
            time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            print("\n\nStopping paper trade loop.")
            break
        except Exception as e:
            print(f"\nError in loop: {e}")
            print("Sleeping 5 minutes before retry...")
            time.sleep(300)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    init_db()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    
    if cmd == "run":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        continuous_loop(interval)
    elif cmd == "report":
        print(generate_report())
    elif cmd == "status":
        print_status()
    elif cmd == "resolve":
        resolve_pending_trades()
    elif cmd == "help":
        print("""
Paper Trade Runner Commands:
  python paper_trade_runner.py run     — Run continuous paper trade loop
  python paper_trade_runner.py report  — Generate paper trade report
  python paper_trade_runner.py status  — Show current tracking status
  python paper_trade_runner.py resolve — Check and resolve pending trades
        """)
    else:
        print(f"Unknown command: {cmd}")
