"""
Simple Market Scanner - Monitors Polymarket for opportunities
No API keys needed - reads public data only
"""
import time
import requests
from datetime import datetime

POLYMARKET_API = "https://gamma-api.polymarket.com"

def get_markets(limit=100):
    """Fetch markets from Polymarket."""
    try:
        resp = requests.get(
            f"{POLYMARKET_API}/markets",
            params={"limit": limit, "closed": "false"},
            timeout=10
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"Error: {e}")
    return []

def get_market_price(condition_id):
    """Get current price from CLOB."""
    try:
        resp = requests.get(
            f"{POLYMARKET_API}/markets/{condition_id}",
            timeout=5
        )
        if resp.ok:
            return resp.json()
    except:
        pass
    return None

def scan():
    """Scan and display markets."""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
    markets = get_markets(100)
    
    if not markets:
        print("No markets found")
        return
    
    # Filter and sort by volume
    active = [m for m in markets if m.get('volume') and float(m.get('volume', 0)) > 5000]
    active.sort(key=lambda x: float(x.get('volume', 0)), reverse=True)
    
    print(f"Found {len(active)} active markets with $5K+ volume\n")
    
    for m in active[:15]:
        vol = float(m.get('volume', 0))
        q = m.get('question', 'N/A')[:55]
        print(f"${vol:>10,.0f} | {q}")

if __name__ == "__main__":
    print("=" * 60)
    print("POLYMARKET MARKET SCANNER")
    print("=" * 60)
    
    while True:
        scan()
        time.sleep(30)