#!/usr/bin/env python3
"""
Polymarket 15-Minute BTC Market Connector
Fetches and trades BTC up/down 15-minute prediction markets from Polymarket

Pattern: https://gamma-api.polymarket.com/events/slug/btc-updown-15m-{epoch}
Where epoch = Unix timestamp of the 15-min window

Resolution: Chainlink BTC/USD price at end of 15-min window vs start
"""
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

# Polymarket APIs
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Session for connection reuse
_session = requests.Session()
_session.headers.update({"Accept": "application/json"})


def get_current_15min_window() -> tuple:
    """Get the current 15-minute window timestamps."""
    now = datetime.now(timezone.utc)
    # Round down to nearest 15 min
    minute = (now.minute // 15) * 15
    window_start = now.replace(minute=minute, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=15)
    return window_start, window_end


def get_15min_market_slug(epoch: int = None) -> str:
    """Generate the slug for a 15-min BTC market."""
    if epoch is None:
        # Use current 15-min window
        window_start, _ = get_current_15min_window()
        epoch = int(window_start.timestamp())
    return f"btc-updown-15m-{epoch}"


def get_current_15min_market() -> Optional[Dict]:
    """
    Get the current active BTC 15-minute market.
    
    Returns dict with:
    - question, slug, condition_id
    - up_price, down_price (current probabilities)
    - volume, liquidity
    - window_start, window_end
    - clob_token_ids (for trading)
    """
    # Get current 15-min window
    window_start, window_end = get_current_15min_window()
    epoch = int(window_start.timestamp())
    slug = get_15min_market_slug(epoch)
    
    try:
        # Fetch market by slug
        resp = _session.get(f"{GAMMA_API}/events/slug/{slug}", timeout=10)
        if not resp.ok:
            print(f"Error fetching market: {resp.status_code}")
            return None
        
        event = resp.json()
        
        # Extract market data from the event
        markets = event.get("markets", [])
        if not markets:
            return None
        
        market = markets[0]
        
        # Parse outcome prices
        outcome_prices = market.get("outcomePrices", "[]")
        if isinstance(outcome_prices, str):
            import json
            outcome_prices = json.loads(outcome_prices)
        
        up_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
        down_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5
        
        # Get CLOB token IDs for trading
        clob_token_ids = market.get("clobTokenIds", "[]")
        if isinstance(clob_token_ids, str):
            import json
            clob_token_ids = json.loads(clob_token_ids)
        
        result = {
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
            "up_token_id": clob_token_ids[0] if len(clob_token_ids) > 0 else None,
            "down_token_id": clob_token_ids[1] if len(clob_token_ids) > 1 else None,
        }
        
        return result
        
    except Exception as e:
        print(f"Error getting 15min market: {e}")
        return None


def get_market_orderbook(condition_id: str) -> Dict:
    """Get order book for a market."""
    try:
        # Try CLOB API with condition ID
        resp = _session.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"Error getting orderbook: {e}")
    return {}


def get_market_price(condition_id: str, side: str = "BUY") -> Optional[float]:
    """Get best price for a market (YES/NO)."""
    try:
        # side: "BUY" means buy YES (up), "SELL" means sell YES (down)
        token_id = condition_id  # In practice, need to map condition to token
        resp = _session.get(
            f"{CLOB_API}/price",
            params={"token_id": token_id, "side": side},
            timeout=10
        )
        if resp.ok:
            data = resp.json()
            return float(data.get("price", 0))
    except:
        pass
    return None


def get_all_active_15min_markets() -> List[Dict]:
    """Get all currently active 15-min markets (current + next window)."""
    markets = []
    
    window_start, window_end = get_current_15min_window()
    
    # Get current window
    current_market = get_current_15min_market()
    if current_market:
        markets.append(current_market)
    
    # Also get next window
    next_epoch = int((window_start + timedelta(minutes=15)).timestamp())
    next_slug = get_15min_market_slug(next_epoch)
    
    try:
        resp = _session.get(f"{GAMMA_API}/events/slug/{next_slug}", timeout=10)
        if resp.ok:
            # Could parse next window too
            pass
    except:
        pass
    
    return markets


# === MAIN TEST ===
if __name__ == "__main__":
    print("=" * 60)
    print("POLYMARKET 15-MIN BTC MARKET FINDER")
    print("=" * 60)
    
    market = get_current_15min_market()
    
    if market:
        print(f"\n📊 Current 15-min Market:")
        print(f"   Question: {market['question']}")
        print(f"   Window: {market['window_start']} to {market['window_end']}")
        print(f"   Up Price: ${market['up_price']:.3f} ({market['up_price']*100:.1f}%)")
        print(f"   Down Price: ${market['down_price']:.3f} ({market['down_price']*100:.1f}%)")
        print(f"   Volume: ${market['volume']:,.0f}")
        print(f"   Liquidity: ${market['liquidity']:,.0f}")
        print(f"   Best Bid: ${market['best_bid']:.2f}")
        print(f"   Best Ask: ${market['best_ask']:.2f}")
        print(f"   Condition ID: {market['condition_id'][:40]}...")
        print(f"   Epoch: {market['epoch']}")
    else:
        print("\n❌ No market found")