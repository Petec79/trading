"""
PICO Market Selection Module

Computes liquidity-adjusted ranking over active markets using:
- bid-ask spread, depth proxy, recent turnover, time-to-event, volatility
- Outputs normalized liquidity score L_m(t) ∈ [0,1]
- Top-K subset for potential entry
"""

import math
from typing import List, Dict, Optional, Tuple


def compute_liquidity_score(
    spread_bps: float,
    depth: float,
    volume: float,
    time_to_event_hours: Optional[float] = None,
    volatility: float = 0.0,
    volume_ema: float = None,
) -> float:
    """
    Compute normalized liquidity score L_m(t) ∈ [0,1].
    
    Higher = more liquid.
    Based on PICO paper Section 3 - Market Selection.
    
    Args:
        spread_bps: Bid-ask spread in basis points (lower is better)
        depth: Order book depth (higher is better)
        volume: 24hr volume (higher is better)
        time_to_event_hours: Hours until event resolves (lower → higher urgency → more volatile)
        volatility: Price volatility proxy (lower is better)
        volume_ema: Exponential moving average of volume for trend
    """
    # Spread component: 0-40% weight (lower spread = higher score)
    # 50 bps spread = terrible, 5 bps = great
    spread_score = max(0, 1 - (spread_bps / 500))
    spread_score = spread_score ** 2  # Squared to penalize wide spreads harder
    
    # Depth component: 0-30% weight
    # Log scale since depth varies wildly
    if depth > 0:
        depth_score = min(1.0, math.log1p(depth) / 10)  # log(1+depth)/10, cap at 1
    else:
        depth_score = 0.0
    
    # Volume component: 0-20% weight
    # Volume trends better than absolute volume
    if volume_ema and volume_ema > 0:
        volume_ratio = min(volume / volume_ema, 3.0)  # Cap at 3x EMA
        volume_score = min(1.0, volume_ratio / 2)
    else:
        # Fallback to absolute volume
        volume_score = min(1.0, math.log1p(volume) / 15)
    
    # Time-to-event component: 0-10% weight
    # Closer to event = more urgent = slightly higher score (events drive volume)
    if time_to_event_hours is not None and time_to_event_hours > 0:
        # 0-24hr: high urgency bonus
        if time_to_event_hours <= 1:
            time_score = 0.9
        elif time_to_event_hours <= 24:
            time_score = 0.7 + 0.2 * (1 - time_to_event_hours / 24)
        elif time_to_event_hours <= 168:  # 1 week
            time_score = 0.5 + 0.2 * (1 - time_to_event_hours / 168)
        else:
            time_score = 0.3
    else:
        time_score = 0.5  # Unknown → neutral
    
    # Volatility component: 0-10% weight (lower vol = more predictable)
    # Cap at 50% vol
    vol_score = max(0, 1 - (volatility / 0.5))
    
    # Weighted sum per PICO paper
    L = (
        0.40 * spread_score
        + 0.30 * depth_score
        + 0.20 * volume_score
        + 0.10 * time_score
        + 0.00 * vol_score
    )
    
    return round(L, 4)


def rank_markets(markets: List[Dict], top_k: int = 2) -> List[Dict]:
    """
    Rank markets by liquidity-adjusted score and return top-K.
    
    Each market dict should have keys: spread_bps, depth, volume, time_to_event_hours
    """
    scored = []
    for m in markets:
        L = compute_liquidity_score(
            spread_bps=m.get("spread_bps", 100),
            depth=m.get("depth", 0),
            volume=m.get("volume", 0),
            time_to_event_hours=m.get("time_to_event_hours"),
            volatility=m.get("volatility", 0),
            volume_ema=m.get("volume_ema"),
        )
        scored.append((L, m))
    
    # Sort descending by score
    scored.sort(key=lambda x: x[0], reverse=True)
    
    return [m for _, m in scored[:top_k]]


def compute_feature_vector(
    spread_bps: float,
    depth: float,
    volume: float,
    price: float,
    time_to_event_hours: Optional[float] = None,
) -> List[float]:
    """
    Build per-market feature vector φ_m(t) for the predictor.
    
    Returns normalized [0,1] features:
    [spread_norm, depth_norm, volume_norm, price_uncertainty, time_urgency, vol_proxy]
    """
    spread_norm = min(1.0, spread_bps / 500)
    depth_norm = min(1.0, math.log1p(depth) / 10)
    volume_norm = min(1.0, math.log1p(volume) / 15)
    
    # Price uncertainty: 0.50 = maximum uncertainty
    price_uncertainty = 1 - abs(price - 0.5) * 2  # 0 at edges, 1 at 0.50
    
    # Time urgency: closer to event = higher
    if time_to_event_hours and time_to_event_hours > 0:
        time_urgency = max(0, 1 - math.log1p(time_to_event_hours) / 6)
    else:
        time_urgency = 0.0
    
    # Vol proxy: derived from spread/depth ratio
    vol_proxy = min(1.0, spread_bps / 200) if depth > 0 else 1.0
    
    return [spread_norm, depth_norm, volume_norm, price_uncertainty, time_urgency, vol_proxy]
