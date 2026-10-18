#!/usr/bin/env python3
import requests
import json

GAMMA = "https://gamma-api.polymarket.com"

# Get active markets
print("=== Active Markets ===")
try:
    resp = requests.get(f"{GAMMA}/events?limit=10&closed=false", timeout=15)
    print(f"Status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        print(f"Type: {type(data)}")
        if isinstance(data, list):
            print(f"Count: {len(data)}")
            for m in data[:5]:
                print(f"\nQ: {m.get('question', 'N/A')[:80]}")
                print(f"  Volume: ${float(m.get('volume24hr', 0)):,.0f}")
                print(f"  Liquidity: ${float(m.get('liquidity', 0)):,.0f}")
                print(f"  Ends: {m.get('endDate', 'N/A')}")
                print(f"  Tags: {m.get('tags', [])}")
                print(f"  slug: {m.get('slug', 'N/A')}")
        else:
            print(str(data)[:500])
except Exception as e:
    print(f"Error: {e}")

# Get popular markets
print("\n=== Popular Markets ===")
try:
    resp = requests.get(f"{GAMMA}/events?limit=10&closed=false&order=volume", timeout=15)
    print(f"Status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        if isinstance(data, list):
            print(f"Count: {len(data)}")
            for m in data[:5]:
                vol = float(m.get('volume24hr', m.get('volume', 0)))
                print(f"\nQ: {m.get('question', 'N/A')[:80]}")
                print(f"  Volume: ${vol:,.0f}")
        else:
            print(str(data)[:300])
except Exception as e:
    print(f"Error: {e}")

# Check if we can get a specific market with orderbook
print("\n=== Sample Market Details ===")
try:
    resp = requests.get(f"{GAMMA}/events?limit=5", timeout=15)
    if resp.ok:
        markets = resp.json()
        if markets:
            m = markets[0]
            slug = m.get('slug', '')
            print(f"Market slug: {slug}")
            
            # Try to get orderbook
            if slug:
                ob_resp = requests.get(f"{GAMMA}/orderbook/{slug}", timeout=10)
                print(f"Orderbook: {ob_resp.status_code}")
                if ob_resp.ok:
                    print(json.dumps(ob_resp.json(), indent=2)[:500])
except Exception as e:
    print(f"Details error: {e}")
