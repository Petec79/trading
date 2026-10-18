"""
PMXT Integration Layer for Polymarket Bot
Replaces raw API calls with PMXT unified API
"""
import os
from typing import Optional, List, Dict, Any

# PMXT Python SDK
try:
    from pmxt import PMXT
    from pmxt.exceptions import PMXTError
    PMXT_AVAILABLE = True
except ImportError:
    PMXT_AVAILABLE = False
    print("[PMXT] Not installed. Run: pip install pmxt")


class PMXTClient:
    """Unified client for prediction market trading via PMXT."""
    
    def __init__(self, api_key: str = None, api_secret: str = None):
        if not PMXT_AVAILABLE:
            raise ImportError("PMXT not installed. Run: pip install pmxt")
        
        self.api_key = api_key or os.getenv("POLYMARKET_API_KEY", "")
        self.api_secret = api_secret or os.getenv("POLYMARKET_API_SECRET", "")
        
        # Initialize PMXT client
        self.client = PMXT(
            api_key=self.api_key,
            api_secret=self.api_secret,
            # default exchange is Polymarket
        )
    
    # =========================================================================
    # MARKET DATA
    # =========================================================================
    
    def get_markets(self, limit: int = 100) -> List[Dict]:
        """Get all markets."""
        return self.client.markets.list(limit=limit)
    
    def get_market(self, market_id: str) -> Dict:
        """Get single market by ID."""
        return self.client.markets.get(market_id)
    
    def search_markets(self, query: str) -> List[Dict]:
        """Search markets by keyword."""
        return self.client.markets.search(query)
    
    def get_market_orders(self, market_id: str) -> Dict:
        """Get order book for a market."""
        return self.client.markets.get_orders(market_id)
    
    def get_market_prices(self, market_id: str) -> Dict:
        """Get current prices (bid/ask) for a market."""
        return self.client.markets.get_prices(market_id)
    
    # =========================================================================
    # CONDITIONS (Polymarket-specific)
    # =========================================================================
    
    def get_condition(self, condition_id: str) -> Dict:
        """Get condition details."""
        return self.client.conditions.get(condition_id)
    
    def get_conditions(self, market_id: str = None) -> List[Dict]:
        """Get conditions, optionally filtered by market."""
        return self.client.conditions.list(market_id=market_id)
    
    # =========================================================================
    # TRADING
    # =========================================================================
    
    def place_order(
        self,
        market_id: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        amount: float,
        collateral: str = "USDCM"
    ) -> Dict:
        """Place a limit order."""
        return self.client.orders.create(
            market_id=market_id,
            side=side,
            price=price,
            amount=amount,
            collateral=collateral
        )
    
    def cancel_order(self, order_id: str) -> Dict:
        """Cancel an order."""
        return self.client.orders.cancel(order_id)
    
    def get_orders(self, market_id: str = None, status: str = None) -> List[Dict]:
        """Get orders, optionally filtered."""
        return self.client.orders.list(market_id=market_id, status=status)
    
    def get_fills(self, order_id: str = None) -> List[Dict]:
        """Get order fills."""
        return self.client.orders.get_fills(order_id=order_id)
    
    # =========================================================================
    # PORTFOLIO / POSITIONS
    # =========================================================================
    
    def get_positions(self) -> List[Dict]:
        """Get current positions."""
        return self.client.portfolio.get_positions()
    
    def get_balance(self, asset: str = "USDCM") -> float:
        """Get balance for an asset."""
        return self.client.portfolio.get_balance(asset)
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def get_best_price(self, market_id: str, side: str) -> Optional[float]:
        """Get best available price (simplified for spread harvesting)."""
        prices = self.get_market_prices(market_id)
        if side.upper() == "BUY":
            return prices.get("ask")  # Lowest ask
        return prices.get("bid")  # Highest bid


# Fallback if PMXT not installed
class PMXTFallback:
    """Fallback methods using raw API if PMXT unavailable."""
    
    def __init__(self, *args, **kwargs):
        print("[PMXT] Using fallback raw API methods")
    
    def get_markets(self):
        import requests
        resp = requests.get("https://gamma-api.polymarket.com/markets?limit=100", timeout=10)
        return resp.json() if resp.ok else []
    
    def search_markets(self, query: str):
        import requests
        resp = requests.get(f"https://gamma-api.polymarket.com/markets?search={query}", timeout=10)
        return resp.json() if resp.ok else []


def get_client() -> PMXTClient:
    """Factory function to get PMXT client."""
    if PMXT_AVAILABLE:
        return PMXTClient()
    return PMXTFallback()


# =============================================================================
# SPREAD HARVESTER (PICO-style)
# =============================================================================

class SpreadHarvester:
    """
    Pure math spread harvester - NO AI prediction.
    Harvests tiny spreads and inefficiencies from other traders.
    """
    
    def __init__(self, client: PMXTClient, min_spread: float = 0.01):
        self.client = client
        self.min_spread = min_spread  # Minimum spread to bother with
    
    def find_spreads(self, markets: List[Dict]) -> List[Dict]:
        """Find markets with profitable spreads."""
        opportunities = []
        
        for market in markets:
            try:
                prices = self.client.get_market_prices(market["id"])
                bid = prices.get("bid", 0)
                ask = prices.get("ask", 1)
                
                spread = ask - bid
                
                if spread >= self.min_spread:
                    opportunities.append({
                        "market": market,
                        "bid": bid,
                        "ask": ask,
                        "spread": spread,
                        "spread_pct": (spread / ask) * 100 if ask > 0 else 0
                    })
            except Exception as e:
                continue
        
        return sorted(opportunities, key=lambda x: x["spread"], reverse=True)
    
    def harvest(self, opportunity: Dict, amount: float = 10) -> Dict:
        """
        Execute spread harvest: buy at bid, sell at ask.
        Note: This is simplified - real implementation needs proper order handling.
        """
        market = opportunity["market"]
        
        # Buy at bid price
        buy_result = self.client.place_order(
            market_id=market["id"],
            side="BUY",
            price=opportunity["bid"],
            amount=amount
        )
        
        return {
            "buy_order": buy_result,
            "market_id": market["id"],
            "entry_price": opportunity["bid"],
            "spread": opportunity["spread"]
        }


# Usage example:
# client = get_client()
# harvester = SpreadHarvester(client, min_spread=0.02)
# opportunities = harvester.find_spreads(client.get_markets())