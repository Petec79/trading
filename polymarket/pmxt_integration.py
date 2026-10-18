#!/usr/bin/env python3
"""
PMXT Integration Layer — Polymarket Bots
Wraps PMXT (ccxt for prediction markets) as a drop-in replacement for raw API calls.

Provides:
- fetch_active_markets() → list[Market]
- get_orderbook(slug) → dict
- fetch_markets_via_clob() → raw markets

Replaces:
- fetch_active_markets() using GAMMA_API direct requests
- get_orderbook(slug) using GAMMA_API/orderbook/{slug}
- get_markets_from_clob() using POLYMARKET_API/markets
- gql_query() using The Graph subgraph

Author: Midas (Paperclip)
Task: MID-31 Integrate PMXT into Polymarket bot
"""

import sys
import os
from pathlib import Path
from typing import Optional, List

# PMXT is installed system-wide at this path
PMXT_PATH = "/home/openclaw/.local/lib/python3.12/site-packages"

def _get_pmxt():
    """Lazy-import PMXT with correct path."""
    if PMXT_PATH not in sys.path:
        sys.path.insert(0, PMXT_PATH)
    import pmxt
    return pmxt

# Lazy singleton exchange instance
_poly_instance = None

def get_polymarket() -> 'pmxt.Polymarket':
    """Get or create the Polymarket exchange instance (auto-starts sidecar)."""
    global _poly_instance
    if _poly_instance is None:
        pmxt = _get_pmxt()
        _poly_instance = pmxt.Polymarket()
    return _poly_instance


# =============================================================================
# DATA MODELS — Match whale_follower.py Market/Signal dataclass structure
# =============================================================================

class Market:
    """Compatible with whale_follower.py Market dataclass fields."""
    def __init__(self, slug, question, category, volume, liquidity, end_date, yes_price, no_price, tags=None):
        self.slug = slug
        self.question = question
        self.category = category or 'unknown'
        self.volume = volume
        self.liquidity = liquidity
        self.end_date = end_date
        self.yes_price = yes_price
        self.no_price = no_price
        self.tags = tags or []


# =============================================================================
# MARKET DATA — PMXT unified API
# =============================================================================

def fetch_active_markets(limit: int = 50) -> List[Market]:
    """
    Fetch active markets via PMXT Polymarket.
    Replaces: whale_follower.fetch_active_markets() using GAMMA_API direct requests.
    
    PMXT maps:
    - event.slug → slug
    - mkt.question / mkt.title → question
    - mkt.volume (total) / mkt.volume_24h → volume
    - mkt.liquidity → liquidity
    - mkt.resolution_date → end_date
    - mkt.yes.price / mkt.no.price → yes_price/no_price
    - mkt.yes.outcome_id → for orderbook
    """
    try:
        poly = get_polymarket()
        events = poly.fetch_events(limit=limit, closed=False)
        
        markets = []
        for event in events:
            event_slug = getattr(event, 'slug', None) or getattr(event, 'id', None) or ''
            
            for mkt in event.markets:
                try:
                    yes_out = mkt.yes
                    no_out = mkt.no
                    
                    # Build slug from event slug + market question snippet
                    slug = getattr(mkt, 'slug', None) or f"{event_slug}-{mkt.market_id}" if hasattr(mkt, 'market_id') else str(mkt)
                    question = getattr(mkt, 'question', '') or getattr(mkt, 'title', '')
                    
                    market = Market(
                        slug=slug,
                        question=question,
                        category=getattr(event, 'category', 'unknown') or 'unknown',
                        volume=float(getattr(mkt, 'volume_24h', 0) or getattr(mkt, 'volume', 0) or 0),
                        liquidity=float(getattr(mkt, 'liquidity', 0) or 0),
                        end_date=str(getattr(mkt, 'resolution_date', '') or ''),
                        yes_price=float(yes_out.price) if yes_out else 0.5,
                        no_price=float(no_out.price) if no_out else 0.5,
                        tags=list(getattr(mkt, 'tags', []) or []),
                    )
                    markets.append(market)
                except (ValueError, TypeError, AttributeError) as ex:
                    print(f"  Skipping market: {ex}")
                    continue
        
        return markets
        
    except Exception as e:
        print(f"PMXT fetch_active_markets error: {e}")
        return []


def get_orderbook(slug: str) -> Optional[dict]:
    """
    Get orderbook for a market by slug or market_id via PMXT.
    Replaces: whale_follower.get_orderbook(slug) using GAMMA_API/orderbook/{slug}.
    
    PMXT uses outcome_id for orderbook lookup. We search events/markets
    to find the matching outcome_id.
    
    Slug format: {event_slug}-{market_id} e.g. democratic-presidential-nominee-2028-559657
    """
    try:
        poly = get_polymarket()
        
        # Extract market_id from slug (last numeric segment)
        # e.g. "democratic-presidential-nominee-2028-559657" -> "559657"
        import re
        numeric_match = re.search(r'-(\d+)$', slug)
        slug_market_id = numeric_match.group(1) if numeric_match else None
        
        # Search all events for the market matching the slug
        events = poly.fetch_events(closed=False, limit=100)
        
        for event in events:
            event_slug = getattr(event, 'slug', '') or ''
            for mkt in event.markets:
                mkt_slug = getattr(mkt, 'slug', None)
                mkt_id = str(getattr(mkt, 'market_id', ''))
                
                # Match by:
                # 1. exact market_id match (extracted from slug or raw)
                # 2. mkt_slug match
                # 3. slug starts with event_slug and ends with market_id
                if (slug_market_id and slug_market_id == mkt_id) or \
                   (mkt_slug and mkt_slug == slug) or \
                   (slug.startswith(event_slug) and slug.endswith(f'-{mkt_id}')):
                    try:
                        outcome_id = mkt.yes.outcome_id
                        order_book = poly.fetch_order_book(outcome_id)
                        if order_book and order_book.bids and order_book.asks:
                            return {
                                'bids': [(float(b.price), float(b.size)) for b in order_book.bids[:10]],
                                'asks': [(float(a.price), float(a.size)) for a in order_book.asks[:10]],
                                'spread': float(order_book.asks[0].price - order_book.bids[0].price) if order_book.asks and order_book.bids else None,
                                'outcome_id': outcome_id,
                                'market_id': mkt_id,
                            }
                    except Exception as e:
                        print(f"  Orderbook fetch error for {slug}: {e}")
                        continue
        
        # If slug matching failed, try parsing as raw market_id (numeric only)
        if slug.isdigit():
            try:
                order_book = poly.fetch_order_book(slug)
                if order_book:
                    return {
                        'bids': [(float(b.price), float(b.size)) for b in order_book.bids[:10]],
                        'asks': [(float(a.price), float(a.size)) for a in order_book.asks[:10]],
                        'spread': float(order_book.asks[0].price - order_book.bids[0].price) if order_book.asks and order_book.bids else None,
                        'outcome_id': slug,
                    }
            except Exception:
                pass
        
        print(f"PMXT get_orderbook: market not found for slug: {slug}")
        return None
        
    except Exception as e:
        print(f"PMXT get_orderbook error for {slug}: {e}")
        return None


def fetch_markets_via_clob(limit: int = 20) -> Optional[List[Market]]:
    """
    Fetch markets via CLOB through PMXT (as fallback / raw data).
    Replaces: whale_follower.get_markets_from_clob(limit) using POLYMARKET_API/markets.
    """
    return fetch_active_markets(limit=limit)


# =============================================================================
# WALLET DATA — via PMXT (when available)
# =============================================================================

def fetch_top_wallets(limit: int = 20) -> list:
    """
    Fetch top wallets via PMXT if available.
    
    NOTE: PMXT v2.x focuses on market data. Wallet/position tracking
    would require additional API calls.暂时返回空列表，需要额外的PMXT功能或备用方案。
    """
    # TODO: PMXT doesn't yet expose wallet position history
    # Fall back to existing wallet discovery or manual list
    return []


# =============================================================================
# DIAGNOSTICS
# =============================================================================

def check_pmxt_status() -> dict:
    """Check PMXT connectivity and return status info."""
    try:
        poly = get_polymarket()
        events = poly.fetch_events(limit=5, closed=False)
        
        status = {
            'pmxt_connected': True,
            'pmxt_version': _get_pmxt().__version__ if hasattr(_get_pmxt(), '__version__') else 'unknown',
            'events_count': len(events),
            'total_markets': sum(len(e.markets) for e in events),
            'sidecar_running': True,
        }
        
        # Test order book on first market
        if events and events[0].markets:
            mkt = events[0].markets[0]
            try:
                ob = poly.fetch_order_book(mkt.yes.outcome_id)
                status['orderbook_working'] = ob is not None
                if ob:
                    status['orderbook_bids'] = len(ob.bids)
                    status['orderbook_asks'] = len(ob.asks)
            except Exception as e:
                status['orderbook_working'] = False
                status['orderbook_error'] = str(e)
        
        return status
        
    except Exception as e:
        return {
            'pmxt_connected': False,
            'error': str(e),
            'sidecar_running': False,
        }


if __name__ == '__main__':
    # Test script
    print("=== PMXT Integration Status ===")
    status = check_pmxt_status()
    for k, v in status.items():
        print(f"  {k}: {v}")
    
    print("\n=== Fetching Active Markets ===")
    markets = fetch_active_markets(limit=20)
    print(f"Got {len(markets)} markets")
    for m in markets[:5]:
        print(f"  [{m.slug[:40]}...] {m.question[:50]}... vol=${m.volume:,.0f} @ YES {m.yes_price:.4f}")
    
    print("\n=== Testing Orderbook ===")
    if markets:
        m = markets[0]
        ob = get_orderbook(m.slug)
        if ob:
            print(f"  Orderbook for {m.slug[:40]}:")
            print(f"    Bids: {ob['bids'][:3]}")
            print(f"    Asks: {ob['asks'][:3]}")
            print(f"    Spread: {ob['spread']:.4f}" if ob['spread'] else "  No spread")
        else:
            print(f"  No orderbook for {m.slug[:40]}")
