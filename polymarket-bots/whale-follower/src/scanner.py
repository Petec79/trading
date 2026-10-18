"""
Whale Scanner — Monitors approved whale wallets for new positions.
Uses The Graph subgraph for trade history + Gamma API for market data.
"""
import time
import json
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional
from .config import GRAPH_API, GAMMA_API, APPROVED_WHALES, KELLY_FRACTION


def gql_query(query: str, variables: dict = None, retries: int = 3) -> Optional[dict]:
    """Execute a GraphQL query against The Graph Polymarket subgraph."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    for attempt in range(retries):
        try:
            resp = requests.post(GRAPH_API, json=payload, timeout=30)
            if resp.ok:
                return resp.json()
            else:
                print(f"[scanner] HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            print(f"[scanner] Attempt {attempt+1} error: {e}")
            time.sleep(2)
    return None


def get_whale_trades(wallet_address: str, hours_back: int = 4) -> list:
    """
    Fetch recent trades by a specific wallet from The Graph.
    Returns list of trade dicts with keys: id, volume, price, side, timestamp, market.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    timestamp_ns = int(since.timestamp() * 1000)

    query = """
    query GetWhaleTrades($address: String!, $since: BigInt!) {
      fpmmTrades(
        first: 50
        orderBy: timestamp
        orderDirection: desc
        where: {
          creator: $address
          timestamp_gt: $since
        }
      ) {
        id
        volume
        price
        side
        timestamp
        creator { id }
        market {
          question
          slug
          id
        }
      }
    }
    """
    result = gql_query(query, {"address": wallet_address.lower(), "since": timestamp_ns})
    if not result or "data" not in result:
        return []
    return result["data"].get("fpmmTrades", [])


def get_market_orderbook(slug: str) -> Optional[dict]:
    """Fetch current orderbook for a market via Gamma API."""
    try:
        resp = requests.get(f"{GAMMA_API}/orderbook/{slug}", timeout=10)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"[scanner] orderbook error for {slug}: {e}")
    return None


def get_market_info(slug: str) -> Optional[dict]:
    """Fetch market metadata (question, volume, end date) via Gamma API."""
    try:
        resp = requests.get(f"{GAMMA_API}/events?limit=5", timeout=10)
        if not resp.ok:
            return None
        events = resp.json()
        for ev in events:
            if ev.get("slug") == slug:
                return ev
        # Fallback: direct event lookup
        resp2 = requests.get(f"{GAMMA_API}/events/{slug}", timeout=10)
        if resp2.ok:
            return resp2.json()
    except Exception as e:
        print(f"[scanner] market info error for {slug}: {e}")
    return None


def calculate_kelly_size(bankroll: float, win_rate: float, odds: float) -> float:
    """
    Calculate Kelly position size.
    f* = (p * b - q) / b  where b = decimal odds - 1, q = 1 - p
    Then apply KELLY_FRACTION ( Quarter Kelly = 0.25 ).
    """
    if odds <= 1:
        return 0.0
    b = odds - 1.0          # net odds
    q = 1.0 - win_rate
    p = win_rate
    full_kelly = (p * b - q) / b if b > 0 else 0.0
    if full_kelly <= 0:
        return 0.0
    quarter_kelly = full_kelly * KELLY_FRACTION
    position = bankroll * quarter_kelly
    # Never exceed MAX_BET_FRACTION of bankroll
    max_bet = bankroll * 0.10
    return min(position, max_bet)


def scan_whales(bankroll: float, seen_trade_ids: set) -> list:
    """
    Main scanner: poll all approved whales for new trades.
    Returns list of Signal dicts ready for challenge + execution.
    Skips trades already in seen_trade_ids.
    """
    signals = []
    now = datetime.now(timezone.utc)

    for whale_name, info in APPROVED_WHALES.items():
        address = info["address"]
        category = info["category"]
        min_size = info["min_size_usd"]

        trades = get_whale_trades(address, hours_back=4)
        if not trades:
            continue

        for trade in trades:
            trade_id = trade.get("id", "")
            if trade_id in seen_trade_ids:
                continue

            volume = float(trade.get("volume", 0))
            if volume < min_size:
                continue

            market = trade.get("market", {})
            slug = market.get("slug", "")
            question = market.get("question", "Unknown")
            price = float(trade.get("price", 0.5))
            side = trade.get("side", "BUY").upper()

            # Get orderbook to confirm current price
            orderbook = get_market_orderbook(slug)
            if orderbook:
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                if side == "BUY" and asks:
                    current_price = float(asks[0][0])
                elif side == "SELL" and bids:
                    current_price = float(bids[0][0])
                else:
                    current_price = price
            else:
                current_price = price

            # Calculate position size
            # win_rate estimate based on whale's historical performance per category
            win_rates = {
                "crypto_macro": 0.58,
                "sports": 0.55,
                "geopolitics": 0.60,
            }
            win_rate = win_rates.get(category, 0.55)
            # Use the market's implied probability as baseline
            implied_prob = price

            # Kelly sizing: use whale's price as entry, implied_prob as win rate proxy
            # odds = 1/current_price (for YES outcome)
            if current_price > 0.01:
                odds = 1.0 / current_price
            else:
                odds = 2.0

            kelly_size = calculate_kelly_size(bankroll, win_rate, odds)
            capped_size = min(kelly_size, info["max_copy_usd"])

            signal = {
                "trade_id": trade_id,
                "whale": whale_name,
                "category": category,
                "question": question,
                "slug": slug,
                "whale_side": side,
                "whale_price": price,
                "current_price": current_price,
                "volume_usd": volume,
                "kelly_size_usd": kelly_size,
                "capped_size_usd": capped_size,
                "win_rate": win_rate,
                "implied_prob": implied_prob,
                "timestamp": now.isoformat(),
            }
            signals.append(signal)
            seen_trade_ids.add(trade_id)

    return signals


def filter_by_category(signals: list, category: str) -> list:
    """Filter signals to only those matching the specified category."""
    return [s for s in signals if s["category"] == category]
