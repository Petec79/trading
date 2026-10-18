#!/usr/bin/env python3
import requests
import json
import time

# The Graph subgraph for Polymarket on Polygon
SUBGRAPH = "https://api.thegraph.com/subgraphs/name/polymarket/matic-markets"

def gql_query(query, variables=None, retries=3):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    for attempt in range(retries):
        try:
            resp = requests.post(SUBGRAPH, json=payload, timeout=30)
            if resp.ok:
                return resp.json()
            else:
                print(f"HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            print(f"Attempt {attempt+1} error: {e}")
            time.sleep(2)
    return None

# Query 1: Get total volume and trader count
q1 = """
{
 金融市场(id: "1") {
    totalVolume
    totalTrades
    uniqueAddresses
  }
}
"""

# Try the actual schema
q2 = """
{
  tradingEvents(first: 5, orderBy: timestamp, orderDirection: desc) {
    id
    volume
    timestamp
  }
}
"""

# Query for large trades (whale activity)
q3 = """
{
  fpmmTrades(first: 20, orderBy: timestamp, orderDirection: desc) {
    id
    volume
    price
    side
    timestamp
    creator {
      id
    }
    bondEscrow
    market {
      question
      slug
    }
  }
}
"""

print("=== Query 1: Market stats ===")
result = gql_query(q1)
if result:
    print(json.dumps(result, indent=2)[:500])

print("\n=== Query 2: Recent trading events ===")
result = gql_query(q2)
if result:
    print(json.dumps(result, indent=2)[:500])

print("\n=== Query 3: Recent trades ===")
result = gql_query(q3)
if result:
    data = result.get('data', {})
    trades = data.get('fpmmTrades', [])
    print(f"Got {len(trades)} trades")
    if trades:
        print(f"First trade keys: {list(trades[0].keys())}")
        print(f"First trade: {json.dumps(trades[0], indent=2)}")
