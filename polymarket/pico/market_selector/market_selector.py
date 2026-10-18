"""
PICO Framework — Market Selector Module
Identifies markets with favorable liquidity proxies.
"""

import math
from typing import Optional
from dataclasses import dataclass


@dataclass
class MarketFeatures:
    """Per-market feature vector for liquidity scoring."""
    condition_id: str
    question: str
    best_bid: float
    best_ask: float
    best_bid_size: float
    best_ask_size: float
    spread: float          # best_ask - best_bid
    depth: float           # combined bid+ask size within N%
    volume: float          # 24h volume
    liquidity: float       # available liquidity
    time_to_event: float   # hours until event
    volatility: float      # recent price volatility
    end_date: str


def compute_liquidity_score(mf: MarketFeatures) -> float:
    """
    Compute normalized liquidity score L(m) [0, 1].
    Higher = more liquid. Based on PICO paper methodology.
    
    L = w1 * spread_score + w2 * depth_score + w3 * volume_score + w4 * time_score
    
    Where:
    - spread_score: 1 - normalized_spread (lower spread = higher score)
    - depth_score: normalized_depth
    - volume_score: normalized_volume
    - time_score: 1 - normalized_time_to_event (closer = higher score)
    """
    W_SPREAD = 0.35
    W_DEPTH = 0.30
    W_VOLUME = 0.20
    W_TIME = 0.15
    
    # Spread score: 0.01 = great, 0.10 = terrible
    # spread is already in price units (0-1 range)
    spread_score = max(0, 1 - mf.spread / 0.10) if mf.spread > 0 else 0
    
    # Depth score: normalize by typical min_size (20)
    # score = 0 if size <= 20, score = 1 if size >= 200
    depth_score = max(0, min(1, (mf.depth - 20) / 180))
    
    # Volume score: normalize by typical volume
    # score = 0 if vol <= 1000, score = 1 if vol >= 100000
    volume_score = max(0, min(1, (mf.volume - 1000) / 99000)) if mf.volume > 0 else 0
    
    # Time score: closer to event = higher (within reason)
    # score = 1 if <= 1 day, score = 0 if >= 30 days
    time_score = max(0, min(1, (720 - mf.time_to_event) / 719)) if mf.time_to_event > 0 else 0.5
    
    return W_SPREAD * spread_score + W_DEPTH * depth_score + W_VOLUME * volume_score + W_TIME * time_score


def select_top_k_markets(markets: list[MarketFeatures], k: int = 4, min_score: float = 0.2) -> list[MarketFeatures]:
    """
    Select top-K markets by liquidity score, filtered by minimum threshold.
    Fixed small universe (K=4) per PICO paper recommendation.
    """
    scored = []
    for m in markets:
        if m.best_bid is None or m.best_ask is None:
            continue
        score = compute_liquidity_score(m)
        if score >= min_score:
            scored.append((score, m))
    
    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    
    return [m for _, m in scored[:k]]


def filter_by_signal(market: MarketFeatures, signal: str, signal_threshold: float = 0.55) -> bool:
    """
    Filter market by trading signal.
    signal: 'YES', 'NO', or 'HOLD'
    signal_threshold: minimum probability to trigger entry (default 0.55 = 5% edge)
    """
    if signal == 'HOLD':
        return False
    
    # Compute implied probability from price
    mid_price = (market.best_bid + market.best_ask) / 2
    
    if signal == 'YES' and mid_price >= signal_threshold:
        return True
    elif signal == 'NO' and mid_price <= (1 - signal_threshold):
        return True
    
    return False


def compute_ev(price: float, true_prob: float) -> float:
    """
    Compute expected value per dollar.
    EV = P_true * (1 - P_market) - (1 - P_true) * P_market
    EV > 0.05 means worthwhile trade per PICO paper.
    """
    p_market = price
    p_true = true_prob
    q_true = 1 - p_true
    
    ev = p_true * (1 - p_market) - q_true * p_market
    return ev
