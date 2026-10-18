#!/usr/bin/env python3
import requests
import json

BASE = "https://clob.polymarket.com"

# Try API info endpoint
try:
    resp = requests.get(f"{BASE}/", timeout=10)
    print(f"Root: {resp.status_code} {resp.text[:200]}")
except Exception as e:
    print(f"Root error: {e}")

# Try info endpoint
try:
    resp = requests.get(f"{BASE}/info", timeout=10)
    print(f"Info: {resp.status_code} {resp.text[:200] if resp.ok else resp.text}")
except Exception as e:
    print(f"Info error: {e}")

# Check markets with active filter
try:
    resp = requests.get(f"{BASE}/markets?closed=false&limit=5", timeout=10)
    print(f"\nMarkets (active): {resp.status_code}")
    if resp.ok:
        data = resp.json()
        markets = data.get('data', [])
        print(f"Count: {len(markets)}")
        if markets:
            m = markets[0]
            print(f"First: {m.get('question', 'N/A')[:80]}")
            print(f"  volume: {m.get('volume')}")
            print(f"  Liquidity: {m.get('liquidity')}")
            print(f"  end_date: {m.get('end_date')}")
            print(f"  keys: {list(m.keys())}")
except Exception as e:
    print(f"Markets error: {e}")

# Check tags
try:
    resp = requests.get(f"{BASE}/tags?limit=20", timeout=10)
    print(f"\nTags: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        print(str(data)[:500])
except Exception as e:
    print(f"Tags error: {e}")
