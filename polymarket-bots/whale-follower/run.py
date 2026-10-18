#!/usr/bin/env python3
"""
Whale Follower - Simple Runner
Run with: python3 run.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from src.scanner_gamma import scan_whales
from src.challenge import challenge
from src.portfolio import Portfolio
from src.config import APPROVED_WHALES

# Configuration
PAPER_MODE = True
BANKROLL = 1000  # Start with $1000 paper bankroll
POLL_INTERVAL = 60  # seconds

def run():
    print("=" * 60)
    print("WHALE FOLLOWER BOT - PAPER MODE")
    print("=" * 60)
    print(f"Bankroll: ${BANKROLL:,.2f}")
    print(f"Tracking: {len(APPROVED_WHALES)} whales")
    print("=" * 60)
    
    portfolio = Portfolio(initial_bankroll=BANKROLL)
    seen_ids = set()
    
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}] Starting...")
    
    # Single run to test
    signals = scan_whales(bankroll=portfolio.bankroll, seen_trade_ids=seen_ids)
    
    if not signals:
        print("  No whale signals found.")
        return
    
    print(f"  Found {len(signals)} signal(s)")
    
    for sig in signals:
        whale = sig["whale"]
        question = sig["question"][:60]
        size = sig["capped_size_usd"]
        
        print(f"\n  [{whale}] {question}...")
        print(f"    Side: {sig['whale_side']} | Size: ${size:.2f}")
        
        # Challenge the signal
        is_good, reason = challenge(sig)
        
        if is_good:
            print(f"    ✅ Accepted")
            seen_ids.add(sig["trade_id"])
        else:
            print(f"    ❌ Rejected")

if __name__ == "__main__":
    run()