"""
Spread Harvester Bot - PICO-Style Pure Math Strategy
No AI prediction - harvests tiny spreads and inefficiencies

Now using PMXT (github.com/pmxt-dev/pmxt) for unified Polymarket API.

Run: python3 spread_harvester_pmxt.py
"""
import time
import sys
from datetime import datetime
from collections import defaultdict

# Add pmxt to path
sys.path.insert(0, '/home/openclaw/workspace/polymarket-tools/pmxt/sdks/python')

from dotenv import load_dotenv
load_dotenv()

import pmxt
from pmxt import Polymarket as PMXTPolymarket

# Configuration
MIN_SPREAD = 0.01  # 1% minimum spread to act on
MIN_VOLUME = 10000  # Only markets with $10k+ volume
CHECK_INTERVAL = 60  # Check every 60 seconds

class SpreadHarvester:
    def __init__(self):
        self.spreads_found = []
        self.total_harvested = 0
        
        # Initialize PMXT Polymarket client (read-only, no private key needed for market data)
        self.exchange = PMXTPolymarket(
            private_key=None,
            auto_start_server=False,
        )
        
    def get_markets(self, limit=100):
        """Fetch active markets from Polymarket via PMXT."""
        try:
            markets = self.exchange.fetch_markets(limit=limit)
            return markets
        except Exception as e:
            print(f"[ERROR] Fetching markets: {e}")
        return []
    
    def get_order_book(self, outcome_id):
        """Get order book for an outcome via PMXT."""
        try:
            return self.exchange.fetch_order_book(outcome_id)
        except Exception as e:
            print(f"[ERROR] Fetching order book: {e}")
        return None
    
    def calculate_spread(self, order_book):
        """Calculate bid-ask spread from PMXT order book."""
        if not order_book:
            return None, None, None
        
        bids = order_book.bids if hasattr(order_book, 'bids') else []
        asks = order_book.asks if hasattr(order_book, 'asks') else []
        
        if not bids or not asks:
            return None, None, None
        
        best_bid = float(bids[0].price) if hasattr(bids[0], 'price') else float(bids[0][0])
        best_ask = float(asks[0].price) if hasattr(asks[0], 'price') else float(asks[0][0])
        
        if best_bid > 0 and best_ask > 0:
            spread = best_ask - best_bid
            spread_pct = (spread / best_ask) * 100
            return best_bid, best_ask, spread_pct
        
        return None, None, None
    
    def analyze_market(self, market):
        """Analyze a single market for spread opportunities using PMXT."""
        # Get volume
        volume = float(market.volume_24h or market.volume or 0)
        if volume < MIN_VOLUME:
            return None
        
        # Get outcome IDs for order book
        outcome_id = None
        if market.yes and hasattr(market.yes, 'outcome_id'):
            outcome_id = market.yes.outcome_id
        elif market.no and hasattr(market.no, 'outcome_id'):
            outcome_id = market.no.outcome_id
        
        if not outcome_id:
            return None
        
        order_book = self.get_order_book(outcome_id)
        bid, ask, spread_pct = self.calculate_spread(order_book)
        
        if bid and ask and spread_pct >= MIN_SPREAD * 100:
            slug = market.url.split("/")[-1] if market.url else market.market_id
            return {
                "question": market.title or "Unknown",
                "outcome_id": outcome_id,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "spread_pct": spread_pct,
                "volume": volume,
                "url": f"https://polymarket.com/market/{slug}"
            }
        
        return None
    
    def scan(self):
        """Scan all markets for spread opportunities."""
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning markets via PMXT...")
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
        print("SPREAD HARVESTER BOT - PMXT Version")
        print("=" * 60)
        print(f"Min spread: {MIN_SPREAD*100}%")
        print(f"Min volume: ${MIN_VOLUME:,}")
        print(f"Check interval: {CHECK_INTERVAL}s")
        print("Using PMXT (github.com/pmxt-dev/pmxt)")
        print("=" * 60)
        
        while True:
            try:
                opportunities = self.scan()
                
                if opportunities:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] FOUND {len(opportunities)} opportunities:")
                    print("-" * 60)
                    
                    for i, opp in enumerate(opportunities[:10], 1):
                        print(f"{i}. {opp['question'][:50]}...")
                        print(f"   Bid: {opp['bid']:.4f} | Ask: {opp['ask']:.4f} | Spread: {opp['spread_pct']:.2f}%")
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
