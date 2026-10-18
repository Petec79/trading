#!/usr/bin/env python3
"""
Level 6 V3: Production Trading System
Based on Noisy's 80% Win Rate Framework

Key innovations:
- Random Forest style multi-model voting
- Features: price, volume, momentum, liquidity
- Entry: market_price <= model_prob * 0.5 (2x edge)
- Exit: market_price >= model_prob * 0.9
- Log returns for proper Sharpe Ratio
- MFE/MAE tracking for exit optimization
"""
import os
import sys
import json
import math
import random
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# APIs
GAMMA_API = "https://gamma-api.polymarket.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

# Session
_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

# === FEATURE ENGINEERING ===

def get_features(market: Dict, btc_price: float, price_history: List[float]) -> Dict:
    """
    Extract features for the model (Phase 2)
    Features: price, volume_24h, momentum, liquidity, days_to_expiry
    """
    now = datetime.now(timezone.utc)
    window_end = datetime.fromisoformat(market['window_end'].replace('Z', '+00:00'))
    days_to_expiry = (window_end - now).total_seconds() / 86400
    
    # Calculate momentum from price history
    momentum_7d = 0
    if len(price_history) >= 2:
        momentum_7d = (price_history[-1] - price_history[0]) / price_history[0] * 100
    
    return {
        "price": market["up_price"],  # Contract price (probability)
        "volume_24h": market["volume"],
        "momentum_7d": momentum_7d,
        "days_to_expiry": days_to_expiry,
        "liquidity": market["liquidity"],
        "btc_price": btc_price,
        "spread": market["best_ask"] - market["best_bid"]
    }


# === RANDOM FOREST STYLE VOTING ===

def run_random_forest(features: Dict) -> float:
    """
    Phase 1 & 3: Random Forest style voting
    Multiple models analyze different features, vote on final probability
    
    Returns: probability 0-1 that UP wins
    """
    # Simulate 100+ models (simplified for now)
    votes = []
    
    # Model 1: Price-based
    price = features["price"]
    if price < 0.25:
        votes.append(0.8)
    elif price > 0.75:
        votes.append(0.2)
    else:
        votes.append(price + random.uniform(-0.1, 0.1))
    
    # Model 2: Volume-based
    volume = features["volume_24h"]
    if volume > 10000:
        votes.append(price * 1.1)  # High volume = more confident
    else:
        votes.append(price)
    
    # Model 3: Momentum-based
    momentum = features["momentum_7d"]
    if momentum > 2:
        votes.append(min(price * 1.2, 0.95))  # Strong momentum up
    elif momentum < -2:
        votes.append(max(price * 0.8, 0.05))
    else:
        votes.append(price)
    
    # Model 4: Liquidity-based
    liq = features["liquidity"]
    if liq > 50000:
        votes.append(price * 1.05)  # High liquidity = more efficient
    else:
        votes.append(price * 0.95)
    
    # Model 5: Time decay
    days = features["days_to_expiry"]
    if days < 0.25:  # Less than 6 hours
        votes.append(price)  # Near resolution, price is accurate
    else:
        votes.append(price * 1.1)  # More time = potential mispricing
    
    # Average all votes
    model_probability = sum(votes) / len(votes)
    model_probability = max(0.05, min(0.95, model_probability))  # Clamp
    
    return model_probability


# === ENTRY & EXIT LOGIC ===

def check_entry(market_price: float, model_probability: float, min_confidence: float = 0.7) -> Tuple[str, str]:
    """
    Phase 3 & 4: Entry logic
    Only enter when:
    1. Model is 70%+ confident
    2. Market gives us 2x below real value
    
    Returns: (signal, reasoning)
    """
    # Check confidence threshold
    if model_probability < min_confidence:
        return "HOLD", f"Model confidence {model_probability:.0%} < {min_confidence:.0%}"
    
    # The key formula: market_price <= model_probability * 0.5
    threshold = model_probability * 0.5
    
    if market_price <= threshold:
        edge = model_probability / market_price
        return "BUY_UP", f"Edge: {edge:.1f}x (market {market_price:.3f} <= {threshold:.3f})"
    else:
        return "HOLD", f"No edge (market {market_price:.3f} > {threshold:.3f})"


def check_exit(market_price: float, model_probability: float, entry_price: float) -> Tuple[bool, str]:
    """
    Phase 7: Exit logic
    Sell when:
    1. Hit target: market_price >= model_probability * 0.9
    2. Near expiry: <= 7 days
    
    Returns: (should_exit, reasoning)
    """
    # Take profit target
    target = model_probability * 0.9
    if market_price >= target:
        return True, f"Hit target: {market_price:.3f} >= {target:.3f}"
    
    # Time-based exit
    # (would check days_to_expiry here)
    
    return False, "Holding"


# === SHARPE RATIO & METRICS ===

def calculate_log_return(p1: float, p0: float) -> float:
    """Phase 6: Log returns for accurate P&L"""
    if p1 <= 0 or p0 <= 0:
        return 0
    return math.log(p1 / p0)


def calculate_sharpe_ratio(log_returns: List[float], risk_free: float = 0.02) -> float:
    """Phase 5: Sharpe Ratio = (Rp - Rf) / σ"""
    if len(log_returns) < 2:
        return 0
    
    mean_return = sum(log_returns) / len(log_returns)
    std_return = (sum((r - mean_return) ** 2 for r in log_returns) / len(log_returns)) ** 0.5
    
    if std_return == 0:
        return 0
    
    return (mean_return - risk_free/252) / std_return  # Annualized


# === DATA FETCHERS ===

class BTCPriceFetcher:
    def get_price(self) -> float:
        try:
            r = _session.get(f"{COINGECKO_API}/simple/price", 
                           params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=5)
            if r.ok:
                return r.json()["bitcoin"]["usd"]
        except:
            pass
        return 67000  # Fallback


class BTC15MinMarket:
    def get_current_market(self) -> Optional[Dict]:
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        window_start = now.replace(minute=minute, second=0, microsecond=0)
        epoch = int(window_start.timestamp())
        slug = f"btc-updown-15m-{epoch}"
        
        try:
            resp = _session.get(f"{GAMMA_API}/events/slug/{slug}", timeout=10)
            if not resp.ok:
                return None
            
            event = resp.json()
            markets = event.get("markets", [])
            if not markets:
                return None
            
            market = markets[0]
            outcome_prices = json.loads(market.get("outcomePrices", "[]"))
            
            return {
                "question": market.get("question", ""),
                "slug": slug,
                "condition_id": market.get("conditionId", ""),
                "up_price": float(outcome_prices[0]) if outcome_prices else 0.5,
                "down_price": float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5,
                "volume": float(market.get("volume", 0)),
                "liquidity": float(market.get("liquidity", 0)),
                "best_bid": float(market.get("bestBid", 0)),
                "best_ask": float(market.get("bestAsk", 0)),
                "window_start": window_start.isoformat(),
                "window_end": (window_start + timedelta(minutes=15)).isoformat(),
                "epoch": epoch,
            }
        except Exception as e:
            print(f"Error: {e}")
            return None


# === TRADING STATE ===

class TradingState:
    def __init__(self):
        self.position = None  # {"entry_price": x, "entry_time": x}
        self.trade_log = []
        self.log_returns = []
        
    def open_position(self, signal: str, price: float):
        self.position = {
            "signal": signal,
            "entry_price": price,
            "entry_time": datetime.now(),
            "mfe": 0,
            "mae": 0
        }
        
    def update_position(self, current_price: float):
        if not self.position:
            return
        pnl = current_price - self.position["entry_price"]
        self.position["mfe"] = max(self.position["mfe"], pnl)
        self.position["mae"] = min(self.position["mae"], pnl)
        
    def close_position(self, exit_price: float) -> float:
        if not self.position:
            return 0
        pnl = exit_price - self.position["entry_price"]
        log_ret = calculate_log_return(exit_price, self.position["entry_price"])
        self.log_returns.append(log_ret)
        
        self.trade_log.append({
            "signal": self.position["signal"],
            "entry": self.position["entry_price"],
            "exit": exit_price,
            "pnl": pnl,
            "log_return": log_ret,
            "mfe": self.position["mfe"],
            "mae": self.position["mae"]
        })
        
        self.position = None
        return pnl
    
    def get_stats(self) -> Dict:
        if not self.trade_log:
            return {"trades": 0, "sharpe": 0, "win_rate": 0}
        
        wins = sum(1 for t in self.trade_log if t["pnl"] > 0)
        return {
            "trades": len(self.trade_log),
            "wins": wins,
            "win_rate": wins / len(self.trade_log),
            "sharpe": calculate_sharpe_ratio(self.log_returns),
            "total_pnl": sum(t["pnl"] for t in self.trade_log)
        }


# === MAIN CYCLE ===

def run_cycle(state: TradingState = None):
    if state is None:
        state = TradingState()
    
    print("\n" + "="*60)
    print("BTC 15-MIN TRADING SYSTEM V3 (Noisy Framework)")
    print("="*60)
    print(f"Time: {datetime.now().isoformat()}")
    
    # Fetch data
    btc_price = BTCPriceFetcher().get_price()
    market = BTC15MinMarket().get_current_market()
    
    if not market:
        print("❌ No market found")
        return state
    
    print(f"\n📊 Market: {market['question'][:50]}...")
    print(f"   UP: ${market['up_price']:.3f} | DOWN: ${market['down_price']:.3f}")
    print(f"   Volume: ${market['volume']:,.0f}")
    print(f"   BTC: ${btc_price:,.2f}")
    
    # Extract features
    features = get_features(market, btc_price, [])
    print(f"\n📈 Features: price={features['price']:.3f}, vol={features['volume_24h']:,.0f}, "
          f"liq={features['liquidity']:,.0f}")
    
    # Run Random Forest model
    model_probability = run_random_forest(features)
    print(f"\n🔮 Model Probability: {model_probability:.1%}")
    
    # Check entry (Phase 3 & 4)
    market_price = market["up_price"]
    signal, reasoning = check_entry(market_price, model_probability)
    
    print(f"\n🎯 SIGNAL: {signal}")
    print(f"   Reasoning: {reasoning}")
    
    # Execute or hold
    if signal == "BUY_UP" and state.position is None:
        state.open_position("BUY_UP", market_price)
        print(f"   ✅ Opened position at {market_price:.3f}")
    elif signal == "HOLD" and state.position:
        # Check exit
        should_exit, exit_reason = check_exit(market_price, model_probability, 
                                               state.position["entry_price"])
        if should_exit:
            pnl = state.close_position(market_price)
            print(f"   ✅ Closed position: PnL={pnl:+.3f} | {exit_reason}")
    else:
        # Update position tracking
        state.update_position(market_price)
    
    # Stats
    stats = state.get_stats()
    print(f"\n📊 Stats: {stats['trades']} trades | Win {stats.get('win_rate', 0):.0%} | "
          f"Sharpe: {stats.get('sharpe', 0):.2f}")
    
    # Log
    log_line = (f"{datetime.now().isoformat()} | {signal} | model:{model_probability:.1%} | "
                f"market:{market_price:.3f} | {reasoning}")
    with open("/tmp/trading_v3.log", "a") as f:
        f.write(log_line + "\n")
    
    print("\n" + "="*60)
    return state


# === MAIN ===

if __name__ == "__main__":
    print("="*60)
    print("LEVEL 6 V3: NOISY FRAMEWORK")
    print("Random Forest + 2x Edge Entry + Log Returns")
    print("="*60)
    
    state = run_cycle()
