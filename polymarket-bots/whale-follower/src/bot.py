#!/usr/bin/env python3
"""
Whale Follower Bot — Main Runner
Mirrors approved whale wallets with Quarter Kelly position sizing.
"""
import time
import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import (
    POLL_INTERVAL_SECONDS, APPROVED_WHALES, BASE_DIR
)
from src.scanner import scan_whales, filter_by_category
from src.challenge import challenge
from src.executor import paper_trade_order, get_polymarket_credentials
from src.portfolio import Portfolio


STATE_DIR = Path(BASE_DIR) / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_FILE = STATE_DIR / "seen_trade_ids.json"


def load_seen_ids() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()


def save_seen_ids(ids: set):
    SEEN_FILE.write_text(json.dumps(list(ids), indent=2))


def run_once(portfolio: Portfolio, seen_ids: set, paper: bool = True) -> int:
    """
    Run one scan/challenge/execute cycle.
    Returns number of signals accepted.
    """
    print(f"\n{'='*60}")
    print(f"  Whale Follower — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    print(f"  Bankroll: ${portfolio.bankroll:,.2f}  |  Positions: {len(portfolio.positions)}")
    print(f"{'='*60}")

    signals = scan_whales(bankroll=portfolio.bankroll, seen_trade_ids=seen_ids)

    if not signals:
        print("  No new whale signals found.")
        return 0

    print(f"  Found {len(signals)} new signal(s)")
    accepted_count = 0

    for sig in signals:
        whale = sig["whale"]
        slug = sig["slug"]
        question = sig["question"][:60]
        size = sig["capped_size_usd"]

        print(f"\n  [{whale}] {question}...")
        print(f"    Side: {sig['whale_side']}  |  Size: ${size:.2f}  |  Vol: ${sig['volume_usd']:.0f}")

        # Risk checks
        can_trade, reason = portfolio.can_trade()
        if not can_trade:
            print(f"    SKIP — {reason}")
            continue

        can_pos, pos_reason = portfolio.check_position_limits()
        if not can_pos:
            print(f"    SKIP — {pos_reason}")
            continue

        # Challenge phase
        accepted, challenge_reason = challenge(sig)
        print(f"    Challenge: {challenge_reason[:80]}")

        if not accepted:
            print(f"    REJECTED — not following whale")
            continue

        # Execute
        side = sig["whale_side"]
        price = sig["current_price"]
        shares = size / price if price > 0 else 0

        api_key, api_secret = get_polymarket_credentials()
        result = paper_trade_order(slug, side, price, shares)

        if result.success:
            portfolio.open_position(
                slug=slug,
                side=side,
                price=price,
                size=shares,
                cost=size,
                whale=whale,
            )
            sig["order_id"] = result.order_id
            sig["executed_at"] = datetime.now(timezone.utc).isoformat()
            sig["challenge_reason"] = challenge_reason
            portfolio.log_trade(sig)
            accepted_count += 1
            print(f"    EXECUTED — order {result.order_id}")
        else:
            print(f"    FAILED — {result.error}")

    return accepted_count


def main():
    portfolio = Portfolio(initial_bankroll=10_000.0)
    seen_ids = load_seen_ids()

    print("Whale Follower Bot — Started")
    print(f"  Monitoring: {', '.join(APPROVED_WHALES.keys())}")
    print(f"  Poll interval: {POLL_INTERVAL_SECONDS}s")
    print(f"  Mode: PAPER TRADING")
    print()

    run_count = 0
    while True:
        try:
            run_count += 1
            print(f"\n[Run #{run_count}]")
            accepted = run_once(portfolio, seen_ids)
            if accepted:
                print(f"  Completed: {accepted} trade(s) accepted")
            time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nWhale Follower — Stopped by user")
            save_seen_ids(seen_ids)
            break
        except Exception as e:
            print(f"  ERROR: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
