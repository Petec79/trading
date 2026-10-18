#!/usr/bin/env python3
"""
BTC 5-Minute Market Connector
Fetches live BTC up/down 5-minute prediction markets from Polymarket

Resolution: Chainlink BTC/USD price at end of 5-min window vs start
"""
import requests
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

# Polymarket APIs
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

def get_current_5min_window() -> tuple:
    """Get the current 5-minute window timestamps."""
    now = datetime.now(timezone.utc)
    # Round down to nearest 5 min
    minute = (now.minute // 5) * 5
    start = now.replace(minute=minute, second=0, microsecond=0)
    end = start + timedelta(minutes=5)
    return start, end

def get_btc_5min_markets() -> List[Dict]:
    """
    Get current BTC 5-minute up/down markets.
    
    These markets resolve to "Up" if BTC price at end > start, else "Down".
    Uses Chainlink BTC/USD for resolution.
    """
    from datetime import timedelta
    
    markets = []
    
    try:
        # Get current 5-min window
        now = datetime.now(timezone.utc)
        minute = (now.minute // 5) * 5
        window_start = now.replace(minute=minute, second=0, microsecond=0)
        window_end = window_start + timedelta(minutes=5)
        
        # Format times for API
        start_ts = int(window_start.timestamp())
        end_ts = int(window_end.timestamp())
        
        # The condition ID format from the URL suggests time-based IDs
        # Try searching for markets with these timestamps
        
        # First, get all markets and filter
        resp = requests.get(f"{CLOB_API}/markets?limit=500", timeout=10)
        if resp.ok:
            all_markets = resp.json().get('data', [])
            
            for m in all_markets:
                q = m.get('question', '')
                # Look for BTC 5-min markets
                if 'btc' in q.lower() and ('up' in q.lower() or 'down' in q.lower()):
                    # Check if it's a 5-min market (contains time pattern)
                    if '5' in q.lower() and ('minute' in q.lower() or 'min' in q.lower()):
                        markets.append({
                            'question': q,
                            'condition_id': m.get('condition_id'),
                            'volume': float(m.get('volume24hr', 0)),
                            'window_start': window_start.isoformat(),
                            'window_end': window_end.isoformat(),
                            'type': 'btc_5min'
                        })
                        
    except Exception as e:
        print(f"Error fetching BTC 5min markets: {e}")
    
    # If no markets found via API, return current window info for manual trading
    if not markets:
        # Create placeholder for the current window
        markets = [{
            'question': f"BTC Up or Down - {now.strftime('%H:%M')} to {(now + timedelta(minutes=5)).strftime('%H:%M')} ET",
            'condition_id': None,  # Would need to be fetched from the event page
            'volume': 0,
            'window_start': window_start.isoformat(),
            'window_end': window_end.isoformat(),
            'type': 'btc_5min',
            'note': 'Manual fetch required - API may not expose ephemeral markets'
        }]
    
    return markets


def get_market_prices(condition_id: str) -> Optional[Dict]:
    """Get current prices for a specific market."""
    try:
        # Get order book / prices
        resp = requests.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        if resp.ok:
            m = resp.json()
            return {
                'yes_price': float(m.get('yes_price', 0.5)),
                'no_price': float(m.get('no_price', 0.5)),
                'volume': float(m.get('volume24hr', 0)),
            }
    except Exception as e:
        print(f"Error getting prices for {condition_id}: {e}")
    return None


def parse_condition_id_from_url(url: str) -> Optional[str]:
    """Extract condition ID from Polymarket URL."""
    # URL format: /event/btc-updown-5m-1775338500
    # The last part is the timestamp
    parts = url.strip('/').split('-')
    if parts:
        return parts[-1]
    return None


# For direct testing
if __name__ == "__main__":
    print("=== BTC 5-Minute Markets ===")
    print(f"Time: {datetime.now()}")
    print()
    
    markets = get_btc_5min_markets()
    print(f"Found {len(markets)} markets:")
    for m in markets:
        print(f"\n{m['question']}")
        print(f"  Window: {m['window_start']} to {m['window_end']}")
        print(f"  Volume: ${m['volume']:,.0f}")
        if m.get('condition_id'):
            prices = get_market_prices(m['condition_id'])
            if prices:
                print(f"  YES (Up): ${prices['yes_price']:.2f}")
                print(f"  NO (Down): ${prices['no_price']:.2f}")