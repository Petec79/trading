#!/usr/bin/env python3
import requests
import json

BASE = "https://clob.polymarket.com"

# Check CLOB health
try:
    resp = requests.get(f"{BASE}/health", timeout=10)
    print(f"CLOB Health: {resp.status_code}")
    if resp.ok:
        print(resp.text[:200])
except Exception as e:
    print(f"CLOB Health error: {e}")

# Check markets
try:
    resp2 = requests.get(f"{BASE}/markets?limit=3", timeout=10)
    print(f"\nMarkets: {resp2.status_code}")
    if resp2.ok:
        data = resp2.json()
        print(f"Response type: {type(data)}")
        if isinstance(data, dict):
            print(f"Keys: {list(data.keys())[:10]}")
        elif isinstance(data, list):
            print(f"List len: {len(data)}, first: {str(data[0])[:200] if data else 'empty'}")
        else:
            print(str(data)[:300])
except Exception as e:
    print(f"Markets error: {e}")

# Try the trades endpoint
try:
    resp3 = requests.get(f"{BASE}/trades?limit=5", timeout=10)
    print(f"\nTrades: {resp3.status_code}")
    if resp3.ok:
        data = resp3.json()
        if isinstance(data, list):
            print(f"Trades count: {len(data)}")
            if data:
                print(f"First trade: {json.dumps(data[0], indent=2)[:500]}")
        else:
            print(str(data)[:300])
except Exception as e:
    print(f"Trades error: {e}")

# Check wallet endpoint
try:
    # Known whale address for CemeterySun from the post
    resp4 = requests.get(f"{BASE}/wallet/0x...", timeout=10)
    print(f"\nWallet: {resp4.status_code}")
except Exception as e:
    print(f"Wallet error: {e}")

# Check poly_data snapshots
try:
    resp5 = requests.get("https://data.polymarket.com/available", timeout=10)
    print(f"\nData server: {resp5.status_code}")
except Exception as e:
    print(f"Data server: {e}")
