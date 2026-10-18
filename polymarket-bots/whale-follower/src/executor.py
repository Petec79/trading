"""
Executor — Places orders on Polymarket CLOB via py-clob-client.
Only used for paper trading. Real money execution requires wallet signing.
"""
import os
import time
import json
import requests
from typing import Optional
from .config import CLOB_API, LOG_FILE


class ExecutionResult:
    def __init__(self, success: bool, order_id: str = "", error: str = "", fills: list = None):
        self.success = success
        self.order_id = order_id
        self.error = error
        self.fills = fills or []


def get_polymarket_credentials() -> tuple[str, str]:
    """Load API key/secret from environment or config file."""
    api_key = os.getenv("POLYMARKET_API_KEY", "")
    api_secret = os.getenv("POLYMARKET_API_SECRET", "")
    return api_key, api_secret


def get_condition_id(slug: str) -> Optional[str]:
    """
    Resolve a market slug to its condition ID (required for CLOB orders).
    """
    try:
        resp = requests.get(
            f"https://gamma-api.polymarket.com/events?limit=100",
            timeout=10
        )
        if resp.ok:
            events = resp.json()
            for ev in events:
                if ev.get("slug") == slug:
                    return ev.get("conditionId") or ev.get("condition_id")
    except Exception as e:
        print(f"[executor] condition_id lookup error: {e}")
    return None


def place_order(
    slug: str,
    side: str,       # "BUY" or "SELL"
    price: float,    # price per share (0.0 to 1.0)
    size: float,    # number of shares
    api_key: str,
    api_secret: str,
) -> ExecutionResult:
    """
    Place a limit order on Polymarket CLOB.
    Paper trading: just log the intended order, simulate fill.
    Real trading: sign and submit via CLOB API.
    """
    # Paper trading mode: no API credentials needed
    if not api_key or not api_secret:
        return paper_trade_order(slug, side, price, size)

    try:
        # Real CLOB order placement would use py-clob-client:
        # from clob_client import ClobClient
        # client = ClobClient(key=api_key, secret=api_secret)
        # order = client.create_order(
        #     condition_id=get_condition_id(slug),
        #     side=side,
        #     price=price,
        #     size=size,
        # )
        # return ExecutionResult(success=True, order_id=order["id"])
        raise NotImplementedError("Real execution not implemented — use paper trading")
    except Exception as e:
        return ExecutionResult(success=False, error=str(e))


def paper_trade_order(
    slug: str,
    side: str,
    price: float,
    size: float,
) -> ExecutionResult:
    """
    Paper trading: simulate order execution and log to trades.csv.
    """
    import csv
    from datetime import datetime, timezone

    # Simulate a fill at the requested price (no slippage for limit orders)
    cost = price * size
    order_id = f"paper_{slug[:16]}_{int(time.time())}"

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_id": order_id,
        "slug": slug,
        "side": side,
        "price": price,
        "size": size,
        "cost_usd": cost,
        "mode": "paper",
        "whale_followed": "",  # filled in by caller
        "pnl": 0.0,
        "closed": False,
    }

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_exists = LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"[executor] PAPER ORDER: {side} {size} shares of {slug} @ ${price:.4f} = ${cost:.2f}")
    return ExecutionResult(success=True, order_id=order_id, fills=[{"price": price, "size": size}])


def close_position(
    slug: str,
    side: str,
    api_key: str,
    api_secret: str,
) -> ExecutionResult:
    """Close an existing position by placing opposing order."""
    return place_order(slug, side, price=0.5, size=1.0, api_key=api_key, api_secret=api_secret)
