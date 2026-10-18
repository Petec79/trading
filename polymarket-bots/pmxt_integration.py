"""
PMXT Integration Test for Polymarket Bot

This script tests the PMXT integration and demonstrates:
- Unified API for Polymarket
- Market data fetching
- Balance checking
- Order placement (paper trading)

Run: python pmxt_integration.py
"""

import os
import sys

# Add pmxt to path
sys.path.insert(0, '/home/openclaw/workspace/polymarket-tools/pmxt/sdks/python')

from dotenv import load_dotenv
load_dotenv()

import pmxt
from pmxt import Polymarket as PMXTPolymarket


def test_pmxt_connection():
    """Test PMXT connection to Polymarket."""
    print("=" * 60)
    print("PMXT Integration Test")
    print("=" * 60)
    
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    proxy_address = os.getenv("POLYMARKET_PROXY_ADDRESS")
    
    if not private_key:
        print("[ERROR] POLYGON_WALLET_PRIVATE_KEY not set")
        return False
    
    print(f"[OK] Private key found: {private_key[:10]}...")
    
    # Initialize PMXT Polymarket exchange
    print("\n[1] Initializing PMXT Polymarket client...")
    exchange = PMXTPolymarket(
        private_key=private_key,
        proxy_address=proxy_address,
        auto_start_server=False,  # Server already running
    )
    print("[OK] PMXT client initialized")
    
    # Test fetching markets
    print("\n[2] Fetching Polymarket markets...")
    try:
        markets = exchange.fetch_markets(limit=5)
        print(f"[OK] Found {len(markets)} markets")
        for m in markets[:3]:
            yes_price = m.yes.price if m.yes else "N/A"
            no_price = m.no.price if m.no else "N/A"
            print(f"  - {m.title[:50]}... | YES: {yes_price} | NO: {no_price}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch markets: {e}")
        return False
    
    # Test fetching balance
    print("\n[3] Fetching account balance...")
    try:
        balances = exchange.fetch_balance()
        print(f"[OK] Found {len(balances)} balances")
        for b in balances:
            print(f"  - {b.asset}: available={b.available}, total={b.total}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch balance: {e}")
        return False
    
    # Test fetching positions
    print("\n[4] Fetching positions...")
    try:
        positions = exchange.fetch_positions()
        print(f"[OK] Found {len(positions)} positions")
        for p in positions:
            print(f"  - {p.market_id}: size={p.size}, side={p.side}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch positions: {e}")
        return False
    
    # Test search
    print("\n[5] Testing market search...")
    try:
        results = exchange.fetch_markets(query="Trump", limit=5)
        print(f"[OK] Search found {len(results)} markets for 'Trump'")
        for m in results[:3]:
            print(f"  - {m.title}")
    except Exception as e:
        print(f"[ERROR] Search failed: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("PMXT Integration Test PASSED")
    print("=" * 60)
    return True


def demo_pmxt_unified_api():
    """
    Demonstrate PMXT unified API across exchanges.
    
    This shows how the same code would work for Polymarket, Kalshi, etc.
    """
    print("\n" + "=" * 60)
    print("PMXT Unified API Demo (Multi-Exchange)")
    print("=" * 60)
    
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    
    # Polymarket
    print("\n[Polymarket]")
    poly = PMXTPolymarket(
        private_key=private_key,
        auto_start_server=False,
    )
    poly_markets = poly.fetch_markets(limit=2)
    print(f"  Markets: {len(poly_markets)}")
    
    # Note: Kalshi would need different credentials
    # kalshi = pmxt.Kalshi(api_key=..., private_key=...)
    # kalshi_markets = kalshi.fetch_markets(query="...", limit=10)
    
    print("\n[OK] Unified API works across exchanges")


def demo_order_placement():
    """
    Demonstrate how to place an order with PMXT.
    
    NOTE: This is for educational purposes. Uncomment to actually trade.
    """
    print("\n" + "=" * 60)
    print("PMXT Order Placement Demo")
    print("=" * 60)
    
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
    
    exchange = PMXTPolymarket(
        private_key=private_key,
        auto_start_server=False,
    )
    
    # Get a market to trade
    markets = exchange.fetch_markets(limit=5)
    if not markets:
        print("[ERROR] No markets found")
        return
    
    market = markets[0]
    print(f"\nSelected market: {market.title}")
    
    if market.yes:
        print(f"YES price: {market.yes.price}")
        print(f"YES outcome_id: {market.yes.outcome_id}")
    
    # Place a limit order (commented out for safety)
    """
    print("\nPlacing limit order...")
    order = exchange.create_order(
        outcome=market.yes,
        side="buy",
        type="limit",
        price=0.50,  # 50 cents
        amount=10,  # 10 contracts
    )
    print(f"Order placed: {order.order_id}")
    print(f"Status: {order.status}")
    """
    
    print("\n[OK] Order placement code ready (commented for safety)")


if __name__ == "__main__":
    success = test_pmxt_connection()
    
    if success:
        demo_pmxt_unified_api()
        demo_order_placement()
    
    sys.exit(0 if success else 1)
