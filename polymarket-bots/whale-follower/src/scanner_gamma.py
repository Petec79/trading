"""
Whale Scanner - Simplified Version
Shows high-volume markets as potential signals
"""
import time
import requests
from datetime import datetime, timezone
from .config import GAMMA_API, KELLY_FRACTION

def get_markets(limit=50):
    """Get active markets."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={"limit": limit, "closed": "false"},
            timeout=15
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"[scanner] Error: {e}")
    return []

def calculate_kelly_size(bankroll, win_rate, odds):
    """Calculate Kelly position size. Default to flat bet if uncertain."""
    # Default minimum bet if kelly is 0
    min_bet = 10  # $10 minimum
    
    if odds <= 1:
        return min_bet
    
    q = 1 - win_rate
    b = odds - 1
    
    if b <= 0:
        return min_bet
        
    kelly = (b * win_rate - q) / b
    return max(min_bet, kelly * bankroll * KELLY_FRACTION)

def scan_whales(bankroll=1000, seen_trade_ids=None):
    """
    Scan for high-volume markets.
    Note: Without API keys, we use volume as a proxy for whale interest.
    """
    seen_trade_ids = seen_trade_ids or set()
    signals = []
    
    print(f"[scanner] Fetching markets...")
    markets = get_markets(limit=100)
    
    if not markets:
        print("[scanner] No markets found")
        return []
    
    # Filter to active markets with volume
    active = [m for m in markets if m.get('volume') and float(m.get('volume', 0)) > 10000]
    active.sort(key=lambda x: float(x.get('volume', 0)), reverse=True)
    
    print(f"[scanner] Found {len(active)} active markets")
    
    for market in active[:10]:
        question = market.get('question', 'Unknown')
        volume = float(market.get('volume', 0))
        slug = market.get('slug', '')
        
        # Use volume as signal
        trade_id = f"vol_{slug[:30]}"
        
        if trade_id in seen_trade_ids:
            continue
        
        # Estimate 50/50 win rate (since we can't get real prices without API)
        current_price = 0.5
        win_rate = 0.5
        odds = 2.0
        
        kelly_size = calculate_kelly_size(bankroll, win_rate, odds)
        
        signal = {
            "trade_id": trade_id,
            "whale": "VOLUME_SCAN",
            "category": "high_volume",
            "question": question[:80],
            "slug": slug,
            "whale_side": "YES",  # Placeholder
            "whale_price": current_price,
            "current_price": current_price,
            "volume_usd": volume,
            "kelly_size_usd": kelly_size,
            "capped_size_usd": min(kelly_size, 500),
            "win_rate": win_rate,
            "implied_prob": current_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        signals.append(signal)
        seen_trade_ids.add(trade_id)
        print(f"[scanner] Signal: {question[:40]}... (${volume:,.0f})")
    
    return signals

def filter_by_category(signals, category):
    return [s for s in signals if s.get('category') == category]