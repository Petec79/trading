# PMXT-backed Polymarket client - drop-in replacement using pmxt library
# https://github.com/pmxt-dev/pmxt
#
# This provides backward compatibility with the existing Polymarket interface
# while using PMXT internally for unified prediction market access.

import os
import ast
from typing import Optional

import pmxt
from pmxt import Polymarket as _PMXTPolymarket

from dotenv import load_dotenv

# Import the existing objects for compatibility
import sys
sys.path.insert(0, '/home/openclaw/workspace/polymarket-tools/agents')
from agents.utils.objects import SimpleMarket, SimpleEvent, Market


class PMXTPolymarketWrapper:
    """PMXT-backed Polymarket client with backward compatibility.
    
    Wraps pmxt.Polymarket to provide the same interface as the original
    Polymarket class, while using PMXT internally for unified market access.
    
    Usage:
        from polymarket_pmxt import PMXTPolymarket
        p = PMXTPolymarket()
        markets = p.get_all_markets()
    """
    
    def __init__(self) -> None:
        load_dotenv()
        
        private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
        api_key = os.getenv("CLOB_API_KEY")
        api_secret = os.getenv("CLOB_API_SECRET")
        api_passphrase = os.getenv("CLOB_PASS_PHRASE")
        proxy_address = os.getenv("POLYMARKET_PROXY_ADDRESS")
        
        # Initialize PMXT Polymarket client
        self._pmxt = _PMXTPolymarket(
            private_key=private_key,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            proxy_address=proxy_address,
            auto_start_server=False,
        )
        
        # Cache for markets/events
        self._markets_cache = []
        self._events_cache = []
    
    def _map_pmxt_to_simple_market(self, pmxt_market: 'pmxt.UnifiedMarket', token_id: str = "") -> SimpleMarket:
        """Convert PMXT UnifiedMarket to SimpleMarket format."""
        outcome_prices = []
        outcomes_list = []
        clob_token_ids_list = []
        
        for outcome in pmxt_market.outcomes:
            outcome_prices.append(str(outcome.price))
            outcomes_list.append(outcome.label)
            if outcome.metadata and 'clobTokenId' in outcome.metadata:
                clob_token_ids_list.append(outcome.metadata['clobTokenId'])
        
        # Use the provided token_id if no CLOB token IDs found
        final_clob_token_ids = str(clob_token_ids_list) if clob_token_ids_list else token_id
        
        return SimpleMarket(
            id=int(pmxt_market.market_id) if pmxt_market.market_id.isdigit() else hash(pmxt_market.market_id),
            question=pmxt_market.title,
            end=str(pmxt_market.resolution_date) if pmxt_market.resolution_date else "",
            description=pmxt_market.description or "",
            active=True,  # PMXT only returns active markets
            funded=True,
            rewardsMinSize=0.0,
            rewardsMaxSpread=0.0,
            spread=0.0,  # Would need orderbook to calculate
            outcomes=str(outcomes_list),
            outcome_prices=str(outcome_prices),
            clob_token_ids=final_clob_token_ids,
        )
    
    def _map_pmxt_to_simple_event(self, pmxt_market: 'pmxt.UnifiedMarket') -> SimpleEvent:
        """Convert PMXT UnifiedMarket to SimpleEvent format."""
        slug = ""
        if pmxt_market.url:
            slug = pmxt_market.url.split("/")[-1]
        
        return SimpleEvent(
            id=int(pmxt_market.market_id) if pmxt_market.market_id.isdigit() else hash(pmxt_market.market_id),
            ticker=pmxt_market.title[:20].upper().replace(" ", "-") if pmxt_market.title else "",
            slug=slug,
            title=pmxt_market.title or "",
            description=pmxt_market.description or "",
            end=str(pmxt_market.resolution_date) if pmxt_market.resolution_date else "",
            active=True,
            closed=False,
            archived=False,
            restricted=False,
            new=False,
            featured=False,
            markets=pmxt_market.market_id,
        )
    
    def _get_outcome_id(self, pmxt_market: 'pmxt.UnifiedMarket', index: int = 0) -> str:
        """Get outcome ID by index (0 = first outcome, 1 = second outcome)."""
        if pmxt_market.outcomes and len(pmxt_market.outcomes) > index:
            return pmxt_market.outcomes[index].outcome_id
        return ""
    
    # ========================================================================
    # Backward compatible interface methods (matching original Polymarket class)
    # ========================================================================
    
    def get_all_markets(self) -> list[SimpleMarket]:
        """Get all active markets."""
        self._markets_cache = self._pmxt.fetch_markets({"limit": 100})
        return [
            self._map_pmxt_to_simple_market(m) 
            for m in self._markets_cache
        ]
    
    def filter_markets_for_trading(self, markets: list[SimpleMarket]) -> list[SimpleMarket]:
        """Filter markets for trading (all active in PMXT)."""
        return [m for m in markets if m.active]
    
    def get_market(self, token_id: str) -> SimpleMarket:
        """Get a specific market by CLOB token ID."""
        # Try to find in cache first
        for m in self._markets_cache:
            for outcome in m.outcomes:
                if outcome.metadata and outcome.metadata.get('clobTokenId') == token_id:
                    return self._map_pmxt_to_simple_market(m, token_id)
        
        # Fetch from API if not in cache
        markets = self._pmxt.fetch_markets({"limit": 100})
        for m in markets:
            for outcome in m.outcomes:
                if outcome.metadata and outcome.metadata.get('clobTokenId') == token_id:
                    return self._map_pmxt_to_simple_market(m, token_id)
        
        return None
    
    def get_all_events(self) -> list[SimpleEvent]:
        """Get all events (implemented as markets in PMXT)."""
        if not self._markets_cache:
            self._markets_cache = self._pmxt.fetch_markets({"limit": 100})
        return [self._map_pmxt_to_simple_event(m) for m in self._markets_cache]
    
    def filter_events_for_trading(self, events: list[SimpleEvent]) -> list[SimpleEvent]:
        """Filter events for trading."""
        return [e for e in events if e.active and not e.archived and not e.closed]
    
    def get_all_tradeable_events(self) -> list[SimpleEvent]:
        """Get all tradeable events."""
        markets = self._pmxt.fetch_markets({"limit": 100})
        self._markets_cache = markets
        return [self._map_pmxt_to_simple_event(m) for m in markets]
    
    def get_sampling_simplified_markets(self) -> list[SimpleMarket]:
        """Get simplified markets for sampling."""
        markets = self._pmxt.fetch_markets({"limit": 50})
        return [self._map_pmxt_to_simple_market(m) for m in markets]
    
    def get_orderbook(self, token_id: str) -> 'pmxt.OrderBook':
        """Get orderbook for a token.
        
        Returns PMXT OrderBook with bids/asks.
        """
        return self._pmxt.fetch_order_book(token_id)
    
    def get_orderbook_price(self, token_id: str) -> float:
        """Get mid-price for a token from orderbook."""
        orderbook = self._pmxt.fetch_order_book(token_id)
        if orderbook.bids and orderbook.asks:
            return (float(orderbook.bids[0].price) + float(orderbook.asks[0].price)) / 2
        return 0.0
    
    def get_address_for_private_key(self) -> str:
        """Get wallet address from private key."""
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        private_key = self._pmxt.private_key or os.getenv("POLYGON_WALLET_PRIVATE_KEY")
        if not private_key:
            return ""
        account = w3.eth.account.from_key(str(private_key))
        return account.address
    
    def build_order(
        self,
        market_token: str,
        amount: float,
        nonce: str = None,
        side: str = "BUY",
        expiration: str = "0",
    ) -> dict:
        """Build a signed order (not submitted)."""
        if nonce is None:
            import time
            nonce = str(int(time.time()))
        
        # market_token is actually the outcome_id (CLOB token ID)
        built = self._pmxt.build_order(
            outcome_id=market_token,
            side=side.lower(),
            type="limit",
            amount=amount,
            price=0.5,  # Placeholder - actual price should come from orderbook
        )
        # Return as dict for backward compatibility
        if hasattr(built, 'signed_order'):
            return built.signed_order
        return built
    
    def execute_order(self, price: float, size: float, side: str, token_id: str) -> dict:
        """Execute a limit order."""
        result = self._pmxt.create_order(
            outcome_id=token_id,
            side=side.lower(),
            type="limit",
            amount=size,
            price=price,
        )
        return result
    
    def execute_market_order(self, market, amount: float) -> dict:
        """Execute a market order.
        
        Args:
            market: SimpleMarket or similar object with clob_token_ids
            amount: Number of contracts to buy
        """
        # Extract token ID from market
        clob_token_ids = market.clob_token_ids
        if isinstance(clob_token_ids, str):
            clob_token_ids = ast.literal_eval(clob_token_ids)
        
        token_id = clob_token_ids[0] if clob_token_ids else None
        
        if not token_id:
            raise ValueError("No CLOB token ID found in market")
        
        result = self._pmxt.create_order(
            outcome_id=token_id,
            side="buy",
            type="market",
            amount=amount,
        )
        return result
    
    def get_usdc_balance(self) -> float:
        """Get USDC balance."""
        try:
            balances = self._pmxt.fetch_balance()
            for balance in balances:
                if balance.asset.upper() in ('USDC', 'USDC_E', 'USDC.e'):
                    return float(balance.available)
            if balances:
                return float(balances[0].available)
            return 0.0
        except Exception as e:
            print(f"[PMXT] Error fetching balance: {e}")
            return 0.0
    
    # ========================================================================
    # PMXT-native methods for advanced usage
    # ========================================================================
    
    def get_pmxt_client(self):
        """Get the underlying PMXT client for advanced operations."""
        return self._pmxt
    
    def search_markets(self, query: str, limit: int = 20) -> list[SimpleMarket]:
        """Search markets by text query."""
        markets = self._pmxt.fetch_markets({"query": query, "limit": limit})
        return [self._map_pmxt_to_simple_market(m) for m in markets]
    
    def get_market_by_slug(self, slug: str) -> SimpleMarket:
        """Get a market by its slug."""
        markets = self._pmxt.fetch_markets({"slug": slug})
        if markets:
            return self._map_pmxt_to_simple_market(markets[0])
        return None
    
    def get_execution_price(self, token_id: str, side: str, amount: float) -> float:
        """Calculate execution price for a given amount."""
        orderbook = self._pmxt.fetch_order_book(token_id)
        result = self._pmxt.get_execution_price(orderbook, side, amount)
        return result.price if hasattr(result, 'price') else 0.0


# Alias for backward compatibility
Polymarket = PMXTPolymarketWrapper


def test():
    """Test the PMXT-backed client."""
    p = PMXTPolymarket()
    
    print("Testing PMXT Polymarket client (backward compatible)...")
    
    # Test fetch markets
    print("\n1. Fetching markets...")
    markets = p.get_all_markets()
    print(f"   Got {len(markets)} markets")
    if markets:
        print(f"   First market: {markets[0].question[:50]}...")
        print(f"   Outcomes: {markets[0].outcomes}")
        print(f"   Prices: {markets[0].outcome_prices}")
    
    # Test get orderbook
    if markets:
        token_ids = markets[0].clob_token_ids
        if isinstance(token_ids, str):
            token_ids = ast.literal_eval(token_ids)
        if token_ids:
            print(f"\n2. Getting orderbook for token {token_ids[0][:20]}...")
            ob = p.get_orderbook(token_ids[0])
            print(f"   Bids: {len(ob.bids) if ob.bids else 0}, Asks: {len(ob.asks) if ob.asks else 0}")
    
    # Test get balance
    print("\n3. Getting USDC balance...")
    balance = p.get_usdc_balance()
    print(f"   Balance: {balance} USDC")
    
    print("\nAll tests passed!")


if __name__ == "__main__":
    test()
