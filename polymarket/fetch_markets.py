#!/usr/bin/env python3
import requests
import json

BASE = "https://clob.polymarket.com"

# Fetch active markets
resp = requests.get(f"{BASE}/markets?limit=10", timeout=10)
if resp.ok:
    data = resp.json()
    markets = data.get('data', [])
    print(f"=== Active Markets ({len(markets)} returned) ===\n")
    for m in markets[:10]:
        print(f"Question: {m.get('question', 'N/A')}")
        print(f"  ID: {m.get('market_slug', m.get('id', 'N/A'))}")
        print(f"  Volume: ${float(m.get('volume', 0)):.0f}")
        print(f"  Liquidity: ${float(m.get('liquidity', 0)):.0f}")
        closed = m.get('closedAt', m.get('end_date', 'open'))
        print(f"  Closes: {closed}")
        print()

# Check order book for a specific market
# First active YES market
if markets:
    first_market = markets[0]
    market_id = first_market.get('id') or first_market.get('condition_id', '')
    print(f"\n=== Order Book for: {first_market.get('question', 'N/A')[:60]} ===")
    print(f"Market ID: {market_id}")
    
    try:
        ob_resp = requests.get(f"{BASE}/orderbook/{market_id}", timeout=10)
        if ob_resp.ok:
            ob = ob_resp.json()
            print(f"Order book keys: {list(ob.keys())}")
            print(json.dumps(ob, indent=2)[:500])
        else:
            print(f"Order book error: {ob_resp.status_code} {ob_resp.text[:100]}")
    except Exception as e:
        print(f"Order book error: {e}")
