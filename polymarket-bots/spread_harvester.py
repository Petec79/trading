"""
Spread Harvester Bot - PICO-Style Pure Math Strategy
No AI prediction - harvests tiny spreads and inefficiencies

Run: python3 spread_harvester.py
"""
import time
import requests
from datetime import datetime
from collections import defaultdict

# Configuration
POLYMARKET_API = "https://gamma-api.polymarket.com"
MIN_SPREAD = 0.01  # 1% minimum spread to act on
MIN_VOLUME = 10000  # Only markets with $10k+ volume
CHECK_INTERVAL = 60  # Check every 60 seconds

class SpreadHarvester:
    def __init__(self):
        self.spreads_found = []
        self.total_harvested = 0
        
    def get_markets(self, limit=100):
        """Fetch active markets from Polymarket."""
        try:
            resp = requests.get(
                f"{POLYMARKET_API}/markets",
                params={
                    "limit": limit,
                    "closed": "false",
                    "active": "true"
                },
                timeout=10
            )
            if resp.ok:
                return resp.json()
        except Exception as e:
            print(f"[ERROR] Fetching markets: {e}")
        return []
    
    def get_order_book(self, condition_id):
        """Get order book for a market."""
        try:
            resp = requests.get(
                f"{POLYMARKET_API}/orderbooks",
                params={"conditionId": condition_id},
                timeout=5
            )
            if resp.ok:
                return resp.json()
        except:
            pass
        return None
    
    def calculate_spread(self, order_book):
        """Calculate bid-ask spread from order book."""
        if not order_book:
            return None, None, None
        
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])
        
        if not bids or not asks:
            return None, None, None
        
        best_bid = float(bids[0].get("price", 0))
        best_ask = float(asks[0].get("price", 0))
        
        if best_bid > 0 and best_ask > 0:
            spread = best_ask - best_bid
            spread_pct = (spread / best_ask) * 100
            return best_bid, best_ask, spread_pct
        
        return None, None, None
    
    def analyze_market(self, market):
        """Analyze a single market for spread opportunities."""
        condition_id = market.get("conditionId") or market.get("condition_id")
        if not condition_id:
            return None
        
        # Skip resolved markets
        if market.get("closed", False):
            return None
        
        volume = float(market.get("volume24hr", 0) or market.get("volume", 0))
        if volume < MIN_VOLUME:
            return None
        
        order_book = self.get_order_book(condition_id)
        bid, ask, spread_pct = self.calculate_spread(order_book)
        
        if bid and ask and spread_pct >= MIN_SPREAD * 100:
            return {
                "question": market.get("question", "Unknown"),
                "condition_id": condition_id,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "spread_pct": spread_pct,
                "volume": volume,
                "url": f"https://polymarket.com/market/{market.get('slug', condition_id)}"
            }
        
        return None
    
    def scan(self):
        """Scan all markets for spread opportunities."""
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning markets...")
        markets = self.get_markets(200)
        
        opportunities = []
        for market in markets:
            result = self.analyze_market(market)
            if result:
                opportunities.append(result)
        
        # Sort by spread size
        opportunities.sort(key=lambda x: x["spread_pct"], reverse=True)
        
        return opportunities
    
    def run(self):
        """Main loop."""
        print("=" * 60)
        print("SPREAD HARVESTER BOT - PICO Style")
        print("=" * 60)
        print(f"Min spread: {MIN_SPREAD*100}%")
        print(f"Min volume: ${MIN_VOLUME:,}")
        print(f"Check interval: {CHECK_INTERVAL}s")
        print("=" * 60)
        
        while True:
            try:
                opportunities = self.scan()
                
                if opportunities:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] FOUND {len(opportunities)} opportunities:")
                    print("-" * 60)
                    
                    for i, opp in enumerate(opportunities[:10], 1):
                        print(f"{i}. {opp['question'][:50]}...")
                        print(f"   Bid: {opp['bid']:.2f} | Ask: {opp['ask']:.2f} | Spread: {opp['spread_pct']:.2f}%")
                        print(f"   Volume: ${opp['volume']:,.0f}")
                        print(f"   URL: {opp['url']}")
                        print()
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] No opportunities found")
                
                # Store latest
                self.spreads_found = opportunities
                
            except KeyboardInterrupt:
                print("\nStopping...")
                break
            except Exception as e:
                print(f"[ERROR] {e}")
            
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    bot = SpreadHarvester()
    bot.run()