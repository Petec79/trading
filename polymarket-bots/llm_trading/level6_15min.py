#!/usr/bin/env python3
"""
Level 6: Production Deployment - 15-Minute BTC Markets
Complete LLM Trading System for Polymarket BTC 15-min markets

Key update: Now uses real 15-min BTC up/down markets!
Pattern: btc-updown-15m-{epoch}
"""
import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

# Add parent directory to path for data_connectors
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Polymarket API
GAMMA_API = "https://gamma-api.polymarket.com"

class BTC15MinMarket:
    """BTC 15-minute market connector."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
    
    def get_current_15min_window(self) -> tuple:
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        window_start = now.replace(minute=minute, second=0, microsecond=0)
        window_end = window_start + timedelta(minutes=15)
        return window_start, window_end
    
    def get_slug(self, epoch: int = None) -> str:
        if epoch is None:
            window_start, _ = self.get_current_15min_window()
            epoch = int(window_start.timestamp())
        return f"btc-updown-15m-{epoch}"
    
    def get_current_market(self) -> Optional[Dict]:
        """Get the current 15-min BTC market."""
        window_start, window_end = self.get_current_15min_window()
        epoch = int(window_start.timestamp())
        slug = self.get_slug(epoch)
        
        try:
            resp = self.session.get(f"{GAMMA_API}/events/slug/{slug}", timeout=10)
            if not resp.ok:
                return None
            
            event = resp.json()
            markets = event.get("markets", [])
            if not markets:
                return None
            
            market = markets[0]
            
            # Parse prices
            outcome_prices = market.get("outcomePrices", "[]")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)
            
            up_price = float(outcome_prices[0]) if outcome_prices else 0.5
            down_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5
            
            clob_token_ids = market.get("clobTokenIds", "[]")
            if isinstance(clob_token_ids, str):
                clob_token_ids = json.loads(clob_token_ids)
            
            return {
                "question": market.get("question", ""),
                "slug": slug,
                "condition_id": market.get("conditionId", ""),
                "up_price": up_price,
                "down_price": down_price,
                "volume": float(market.get("volume", 0)),
                "liquidity": float(market.get("liquidity", 0)),
                "best_bid": float(market.get("bestBid", 0)),
                "best_ask": float(market.get("bestAsk", 0)),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "epoch": epoch,
                "up_token_id": clob_token_ids[0] if clob_token_ids else None,
                "down_token_id": clob_token_ids[1] if len(clob_token_ids) > 1 else None,
                "type": "btc_15min"
            }
        except Exception as e:
            print(f"Error: {e}")
            return None


def generate_signal(market: Dict) -> Dict:
    """Generate trading signal based on market data."""
    import random
    
    up_price = market.get("up_price", 0.5)
    down_price = market.get("down_price", 0.5)
    volume = market.get("volume", 0)
    
    # Signal logic: 
    # If market thinks DOWN (high down_price), but we think UP, buy UP
    # Up price is low = market expects down = potential value on UP
    
    signal = "HOLD"
    confidence = 0.5
    reasoning = ""
    
    # Simple momentum: if volume high, more confident
    if volume > 5000:
        confidence += 0.1
    
    # Mean reversion: if up_price very low (<0.2), likely to revert
    if up_price < 0.25:
        signal = "BUY_UP"
        confidence = 0.7
        reasoning = "Up price oversold, mean reversion expected"
    elif up_price > 0.75:
        signal = "BUY_DOWN"  # Buy "Down" outcome
        confidence = 0.7
        reasoning = "Up price overbought, fade the rally"
    elif up_price < 0.35:
        signal = "BUY_UP"
        confidence = 0.6
        reasoning = "Up price undervalued"
    elif up_price > 0.65:
        signal = "BUY_DOWN"
        confidence = 0.6
        reasoning = "Down price better value"
    else:
        # Add randomness for non-clear cases
        if random.random() < 0.2:
            signal = random.choice(["BUY_UP", "BUY_DOWN", "HOLD"])
            confidence = random.uniform(0.4, 0.55)
    
    return {
        "signal": signal,
        "confidence": confidence,
        "reasoning": reasoning,
        "up_price": up_price,
        "down_price": down_price,
        "volume": volume
    }


def run_cycle():
    """Run a trading cycle."""
    print("\n" + "="*60)
    print("BTC 15-MIN TRADING CYCLE")
    print("="*60)
    print(f"Time: {datetime.now().isoformat()}")
    
    # Get market
    btc = BTC15MinMarket()
    market = btc.get_current_market()
    
    if not market:
        print("❌ No 15-min market found")
        return
    
    print(f"\n📊 Market: {market['question']}")
    print(f"   Window: {market['window_start'][:19]} to {market['window_end'][:19]}")
    print(f"   Up: ${market['up_price']:.3f} | Down: ${market['down_price']:.3f}")
    print(f"   Volume: ${market['volume']:,.0f} | Liquidity: ${market['liquidity']:,.0f}")
    
    # Generate signal
    signal = generate_signal(market)
    
    print(f"\n🎯 Signal: {signal['signal']} ({signal['confidence']:.0%} confidence)")
    print(f"   Reasoning: {signal['reasoning']}")
    
    # Log result
    with open("/tmp/trading_15min.log", "a") as f:
        f.write(f"{datetime.now().isoformat()} | {signal['signal']} | {signal['confidence']:.0%} | "
                f"Up:{market['up_price']:.3f} Down:{market['down_price']:.3f} | Vol:${market['volume']:,.0f}\n")
    
    print("\n" + "="*60)
    return signal


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    
    print("="*60)
    print("LEVEL 6: BTC 15-MIN TRADING SYSTEM")
    print("="*60)
    
    run_cycle()
