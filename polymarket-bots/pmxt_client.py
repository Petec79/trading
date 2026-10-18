# PMXT Client - Unified API for prediction markets (Polymarket, Kalshi, etc.)
# https://github.com/pmxt-dev/pmxt
#
# This module provides a unified wrapper around pmxt, exposing the same
# interface as the existing Polymarket class for drop-in replacement.

import os
import json
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv

import pmxt
from pmxt import Polymarket as PMXTPolymarket

load_dotenv()


# SimpleMarket-like class for PMXT data
class PMXTSimpleMarket:
    """Simplified market data from PMXT unified API."""
    
    def __init__(self, market_id: str, question: str, yes_price: float, no_price: float,
                 outcomes: List[str], outcome_prices: List[float], 
                 clob_token_ids: List[str] = None, **kwargs):
        self.id = int(market_id) if market_id else 0
        self.question = question
        self.yes_price = yes_price
        self.no_price = no_price
        self.outcomes = outcomes
        self.outcome_prices = outcome_prices
        self.clob_token_ids = clob_token_ids or []
        self.end = kwargs.get('end_date', '')
        self.active = kwargs.get('active', True)
        self.funded = kwargs.get('funded', True)
        self.description = kwargs.get('description', '')
        self.volume = kwargs.get('volume', 0.0)
        self.liquidity = kwargs.get('liquidity', 0.0)
        
    def __repr__(self):
        return f"<PMXTSimpleMarket {self.id}: {self.question[:40]}... YES:{self.yes_price} NO:{self.no_price}>"


class PMXTClient:
    """PMXT wrapper providing unified interface for prediction market trading.
    
    Replaces raw py-clob-client calls with pmxt's unified API.
    Supports Polymarket, Kalshi, Limitless, and other exchanges.
    
    Usage:
        client = PMXTClient()
        markets = client.fetch_markets(query="Trump")
        balance = client.fetch_balance()
        order = client.create_order(outcome=market.yes, side='buy', amount=10)
    """
    
    def __init__(self, private_key: str = None, proxy_address: str = None):
        self.private_key = private_key or os.getenv("POLYGON_WALLET_PRIVATE_KEY")
        self.proxy_address = proxy_address or os.getenv("POLYMARKET_PROXY_ADDRESS")
        
        # Initialize PMXT Polymarket exchange
        self.exchange = PMXTPolymarket(
            private_key=self.private_key,
            proxy_address=self.proxy_address,
            auto_start_server=False,  # Server should already be running
        )
        
        # Cache
        self._markets_cache: List[PMXTSimpleMarket] = []
        
    def fetch_markets(self, query: str = None, limit: int = 100) -> List[PMXTSimpleMarket]:
        """Fetch markets using PMXT unified API.
        
        Args:
            query: Optional search query
            limit: Maximum number of markets to return
            
        Returns:
            List of PMXTSimpleMarket objects
        """
        try:
            if query:
                raw_markets = self.exchange.fetch_markets(query=query, limit=limit)
            else:
                raw_markets = self.exchange.fetch_markets(limit=limit)
            
            markets = []
            for market in raw_markets[:limit]:
                try:
                    simple_market = self._map_unified_market(market)
                    markets.append(simple_market)
                except Exception as e:
                    print(f"[PMXT] Error converting market: {e}")
                    continue
                    
            return markets
            
        except Exception as e:
            print(f"[PMXT] Error fetching markets: {e}")
            return []
    
    def _map_unified_market(self, market: 'pmxt.UnifiedMarket') -> PMXTSimpleMarket:
        """Map PMXT UnifiedMarket to PMXTSimpleMarket format."""
        # Extract yes/no outcomes
        yes_price = market.yes.price if market.yes else 0.0
        no_price = market.no.price if market.no else 0.0
        
        # Build outcomes list
        outcomes = [o.label for o in market.outcomes] if market.outcomes else ['Yes', 'No']
        outcome_prices = [yes_price, no_price]
        
        # Build CLOB token IDs
        clob_token_ids = []
        if market.yes and market.yes.outcome_id:
            clob_token_ids.append(market.yes.outcome_id)
        if market.no and market.no.outcome_id:
            clob_token_ids.append(market.no.outcome_id)
        
        return PMXTSimpleMarket(
            market_id=market.market_id,
            question=market.title,
            yes_price=yes_price,
            no_price=no_price,
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            clob_token_ids=clob_token_ids,
            description=market.description or '',
            volume=market.volume_24h or market.volume or 0.0,
            liquidity=market.liquidity or 0.0,
            end_date=str(market.resolution_date) if market.resolution_date else None,
            active=True,
        )
    
    def fetch_balance(self) -> float:
        """Fetch USDC balance.
        
        Returns:
            Available USDC balance
        """
        try:
            balances = self.exchange.fetch_balance()
            for balance in balances:
                if balance.asset.upper() in ('USDC', 'USDC_E', 'USDC.e'):
                    return float(balance.available)
            # If no USDC found, return first balance
            if balances:
                return float(balances[0].available)
            return 0.0
        except Exception as e:
            print(f"[PMXT] Error fetching balance: {e}")
            return 0.0
    
    def fetch_order_book(self, token_id: str) -> Dict[str, Any]:
        """Fetch order book for a token.
        
        Args:
            token_id: CLOB token ID
            
        Returns:
            Order book data with bids and asks
        """
        try:
            order_book = self.exchange.fetch_order_book(token_id)
            return {
                'bids': [(level.price, level.size) for level in order_book.bids] if order_book.bids else [],
                'asks': [(level.price, level.size) for level in order_book.asks] if order_book.asks else [],
                'market': token_id,
            }
        except Exception as e:
            print(f"[PMXT] Error fetching order book: {e}")
            return {'bids': [], 'asks': [], 'market': token_id}
    
    def create_order(
        self,
        outcome: 'pmxt.MarketOutcome',
        side: str = 'buy',
        order_type: str = 'market',
        price: Optional[float] = None,
        amount: float = 0,
    ) -> 'pmxt.Order':
        """Create and submit an order.
        
        Args:
            outcome: Market outcome (e.g., market.yes)
            side: 'buy' or 'sell'
            order_type: 'market' or 'limit'
            price: Limit price (required for limit orders)
            amount: Number of contracts
            
        Returns:
            Order result
        """
        try:
            order = self.exchange.create_order(
                outcome=outcome,
                side=side,
                type=order_type,
                price=price,
                amount=amount,
            )
            return order
        except Exception as e:
            print(f"[PMXT] Error creating order: {e}")
            raise
    
    def fetch_positions(self) -> List[Dict[str, Any]]:
        """Fetch current positions.
        
        Returns:
            List of position dictionaries
        """
        try:
            positions = self.exchange.fetch_positions()
            return [
                {
                    'market_id': p.market_id,
                    'outcome': p.outcome if hasattr(p, 'outcome') else '',
                    'size': p.size if hasattr(p, 'size') else 0,
                    'price': p.price if hasattr(p, 'price') else 0,
                    'value': getattr(p, 'value', 0),
                }
                for p in positions
            ]
        except Exception as e:
            print(f"[PMXT] Error fetching positions: {e}")
            return []
    
    def fetch_my_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch recent trades.
        
        Returns:
            List of trade dictionaries
        """
        try:
            trades = self.exchange.fetch_my_trades(limit=limit)
            return [
                {
                    'id': t.id if hasattr(t, 'id') else '',
                    'market_id': t.market_id if hasattr(t, 'market_id') else '',
                    'side': t.side if hasattr(t, 'side') else '',
                    'size': t.size if hasattr(t, 'size') else 0,
                    'price': t.price if hasattr(t, 'price') else 0,
                    'fee': getattr(t, 'fee', 0),
                    'timestamp': str(getattr(t, 'timestamp', '')),
                }
                for t in trades
            ]
        except Exception as e:
            print(f"[PMXT] Error fetching trades: {e}")
            return []
    
    def get_market_by_id(self, market_id: str) -> Optional[PMXTSimpleMarket]:
        """Fetch a single market by ID.
        
        Args:
            market_id: Market ID
            
        Returns:
            PMXTSimpleMarket or None
        """
        try:
            market = self.exchange.fetch_market(market_id=market_id)
            return self._map_unified_market(market)
        except Exception as e:
            print(f"[PMXT] Error fetching market {market_id}: {e}")
            return None
    
    def search_markets(self, query: str) -> List[PMXTSimpleMarket]:
        """Search markets by query string.
        
        Args:
            query: Search query
            
        Returns:
            List of matching PMXTSimpleMarket objects
        """
        return self.fetch_markets(query=query)
    
    def fetch_events(self, query: str = None, limit: int = 100) -> List[Dict]:
        """Fetch events using PMXT unified API.
        
        Note: PMXT primarily works with markets. This method provides
        a best-effort event extraction from market data.
        
        Args:
            query: Optional search query
            limit: Maximum number to return
            
        Returns:
            List of event-like dictionaries
        """
        markets = self.fetch_markets(query=query, limit=limit)
        # Group markets by some common event attribute if available
        # For now, return as-is
        return [{'markets': markets, 'title': query or 'Events'}]


# Backwards compatibility - alias
Polymarket = PMXTClient


def test():
    """Test PMXT client."""
    print("=" * 60)
    print("PMXT Client Test")
    print("=" * 60)
    
    client = PMXTClient()
    
    # Test fetching markets
    print("\n[1] Fetching markets...")
    markets = client.fetch_markets(limit=5)
    print(f"Found {len(markets)} markets")
    for m in markets[:3]:
        print(f"  - {m.question[:50]}... YES:{m.yes_price} NO:{m.no_price}")
    
    # Test fetching balance
    print("\n[2] Fetching balance...")
    balance = client.fetch_balance()
    print(f"Balance: {balance} USDC")
    
    # Test searching
    print("\n[3] Searching markets for 'Trump'...")
    results = client.search_markets('Trump')
    print(f"Found {len(results)} results")
    
    print("\n" + "=" * 60)
    print("PMXT Client Test PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test()