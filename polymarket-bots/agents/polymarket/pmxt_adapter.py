"""
PMXT Adapter for Polymarket Bot

Wraps PMXT (github.com/pmxt-dev/pmxt) to provide unified trading
across Polymarket, Kalshi, and other prediction markets.

Replace raw API calls with PMXT for:
- fetch_markets() - unified market search  
- fetch_order_book() - unified order book
- fetch_ohlcv() - unified historical data
- create_order() - unified trading
"""

import os
from typing import Optional

import pmxt
from dotenv import load_dotenv

load_dotenv()


class PMXTPolymarketAdapter:
    """
    Adapter that wraps PMXT Polymarket client.

    Provides the same interface as the legacy Polymarket class
    but uses PMXT underneath for unified multi-exchange support.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        proxy_address: Optional[str] = None,
        auto_start_server: bool = True,
    ):
        """
        Initialize PMXT Polymarket adapter.

        Args:
            private_key: Polygon wallet private key for trading
            proxy_address: Optional proxy/smart wallet address
            auto_start_server: Auto-start PMXT sidecar server (default True)
        """
        self.private_key = private_key or os.getenv("POLYGON_WALLET_PRIVATE_KEY")
        self.proxy_address = proxy_address or os.getenv("POLYMARKET_PROXY_ADDRESS")

        # Initialize PMXT Polymarket client
        self.pmxt_client = pmxt.Polymarket(
            private_key=self.private_key,
            proxy_address=self.proxy_address,
            auto_start_server=auto_start_server,
        )

    # === Market Data Methods ===

    def fetch_markets(
        self,
        query: Optional[str] = None,
        limit: int = 100,
        sort: str = "volume",
    ) -> list:
        """
        Fetch markets using PMXT.

        Args:
            query: Optional search query
            limit: Max markets to return
            sort: Sort by 'volume', 'newest', or 'liquidity'

        Returns:
            List of UnifiedMarket objects
        """
        params = {"limit": limit, "sort": sort}
        if query:
            params["query"] = query
        return self.pmxt_client.fetch_markets(**params)

    def fetch_events(
        self,
        query: Optional[str] = None,
        limit: int = 100,
    ) -> list:
        """
        Fetch events using PMXT.

        Args:
            query: Optional search query
            limit: Max events to return

        Returns:
            List of UnifiedEvent objects
        """
        params = {"limit": limit}
        if query:
            params["query"] = query
        return self.pmxt_client.fetch_events(**params)

    def filter_markets(self, markets: list, query: str) -> list:
        """
        Filter markets by keyword using PMXT.

        Args:
            markets: List of UnifiedMarket objects
            query: Keyword to filter by

        Returns:
            Filtered list of markets
        """
        return self.pmxt_client.filter_markets(markets, query)

    def fetch_order_book(self, outcome_id: str) -> object:
        """
        Fetch order book for an outcome using PMXT.

        Args:
            outcome_id: CLOB token ID

        Returns:
            OrderBook object with bids/asks
        """
        return self.pmxt_client.fetch_order_book(outcome_id)

    def fetch_ohlcv(
        self,
        outcome_id: str,
        resolution: str = "1h",
        limit: int = 100,
    ) -> list:
        """
        Fetch OHLCV candles using PMXT.

        Args:
            outcome_id: CLOB token ID
            resolution: Candle resolution (1m, 5m, 15m, 1h, 6h, 1d)
            limit: Max candles to return

        Returns:
            List of candle objects
        """
        return self.pmxt_client.fetch_ohlcv(outcome_id, resolution=resolution, limit=limit)

    def fetch_trades(
        self,
        outcome_id: str,
        limit: int = 100,
    ) -> list:
        """
        Fetch trade history using PMXT.

        Args:
            outcome_id: CLOB token ID
            limit: Max trades to return

        Returns:
            List of trade objects
        """
        return self.pmxt_client.fetch_trades(outcome_id, limit=limit)

    # === Trading Methods ===

    def create_order(
        self,
        outcome,
        side: str,
        order_type: str,
        amount: float,
        price: float,
    ) -> object:
        """
        Create and submit an order using PMXT.

        Args:
            outcome: MarketOutcome object (e.g., market.yes or market.no)
            side: 'buy' or 'sell'
            order_type: 'limit' or 'market'
            amount: Order size
            price: Limit price (0.0 to 1.0)

        Returns:
            Order result object
        """
        return self.pmxt_client.create_order(
            outcome=outcome,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
        )

    def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an open order.

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation result
        """
        return self.pmxt_client.cancel_order(order_id)

    def fetch_open_orders(self, market_id: Optional[str] = None) -> list:
        """
        Fetch open orders.

        Args:
            market_id: Optional market ID to filter by

        Returns:
            List of open order objects
        """
        return self.pmxt_client.fetch_open_orders(market_id=market_id)

    def fetch_order(self, order_id: str) -> object:
        """
        Fetch details of a specific order.

        Args:
            order_id: Order ID

        Returns:
            Order object
        """
        return self.pmxt_client.fetch_order(order_id)

    # === Account Methods ===

    def fetch_balance(self) -> list:
        """
        Fetch account balances.

        Returns:
            List of Balance objects
        """
        return self.pmxt_client.fetch_balance()

    def fetch_positions(self) -> list:
        """
        Fetch current positions.

        Returns:
            List of Position objects
        """
        return self.pmxt_client.fetch_positions()

    def fetch_my_trades(self, market_id: Optional[str] = None) -> list:
        """
        Fetch user's trade history.

        Args:
            market_id: Optional market ID to filter by

        Returns:
            List of trade objects
        """
        return self.pmxt_client.fetch_my_trades(market_id=market_id)

    # === Utility Methods ===

    def get_execution_price(self, order_book, side: str, amount: float) -> float:
        """
        Calculate execution price for a given order size.

        Args:
            order_book: OrderBook from fetch_order_book()
            side: 'buy' or 'sell'
            amount: Order size

        Returns:
            Expected execution price
        """
        return self.pmxt_client.get_execution_price(order_book, side, amount)

    def get_execution_price_detailed(
        self, order_book, side: str, amount: float
    ) -> dict:
        """
        Calculate detailed execution price info.

        Args:
            order_book: OrderBook from fetch_order_book()
            side: 'buy' or 'sell'
            amount: Order size

        Returns:
            Dict with price, total cost, fees, etc.
        """
        return self.pmxt_client.get_execution_price_detailed(order_book, side, amount)


# Convenience function for quick testing
def test_pmxt_connection():
    """Test PMXT connection and basic functionality."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    client = PMXTPolymarketAdapter()

    # Test fetching markets
    print("Fetching markets...")
    markets = client.fetch_markets(limit=5)
    print(f"Found {len(markets)} markets")

    if markets:
        m = markets[0]
        print(f"  Sample: {m.title}")
        if hasattr(m, 'yes') and m.yes:
            print(f"  Yes price: {m.yes.price * 100:.1f}%")
        if hasattr(m, 'no') and m.no:
            print(f"  No price: {m.no.price * 100:.1f}%")

    # Test order book
    if markets and hasattr(markets[0], 'yes'):
        print("\nFetching order book...")
        outcome_id = markets[0].yes.outcome_id
        ob = client.fetch_order_book(outcome_id)
        if hasattr(ob, 'asks') and ob.asks:
            print(f"  Best ask: {ob.asks[0].price}")
        if hasattr(ob, 'bids') and ob.bids:
            print(f"  Best bid: {ob.bids[0].price}")

    print("\nPMXT connection test passed!")


if __name__ == "__main__":
    test_pmxt_connection()
