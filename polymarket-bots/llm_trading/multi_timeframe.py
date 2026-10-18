#!/usr/bin/env python3
"""
Multi-Timeframe BTC Trading Bot
Trades both 5-min and 15-min BTC Up/Down markets simultaneously

Uses Noisy's Framework:
- Random Forest style voting
- 2x Edge Entry (market <= model * 0.5)
- 70% confidence threshold
- Log returns for Sharpe Ratio
"""
import os
import sys
import json
import math
import random
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

# APIs
GAMMA_API = "https://gamma-api.polymarket.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

# === FEATURE ENGINEERING ===

def get_features(market: Dict, btc_price: float) -> Dict:
    """Extract features for the model."""
    now = datetime.now(timezone.utc)
    window_end = datetime.fromisoformat(market['window_end'].replace('Z', '+00:00'))
    days_to_expiry = (window_end - now).total_seconds() / 86400
    
    return {
        "price": market["up_price"],
        "volume_24h": market["volume"],
        "liquidity": market["liquidity"],
        "btc_price": btc_price,
        "spread": market["best_ask"] - market["best_bid"],
        "minutes_to_expiry": days_to_expiry * 24 * 60
    }

# === RANDOM FOREST MODEL ===

def run_random_forest(features: Dict) -> float:
    """Multiple models vote on final probability."""
    votes = []
    price = features["price"]
    volume = features["volume_24h"]
    liquidity = features["liquidity"]
    
    # Model 1: Price-based
    if price < 0.25:
        votes.append(0.8)
    elif price > 0.75:
        votes.append(0.2)
    else:
        votes.append(price + random.uniform(-0.1, 0.1))
    
    # Model 2: Volume-based
    if volume > 5000:
        votes.append(price * 1.1)
    else:
        votes.append(price)
    
    # Model 3: Liquidity-based
    if liquidity > 20000:
        votes.append(price * 1.05)
    else:
        votes.append(price * 0.95)
    
    # Model 4: Time decay
    mins = features.get("minutes_to_expiry", 60)
    if mins < 30:  # Near resolution
        votes.append(price)
    else:
        votes.append(price * 1.1)
    
    return max(0.05, min(0.95, sum(votes) / len(votes)))

# === ENTRY/EXIT ===

def check_entry(market_price: float, model_prob: float, min_conf: float = 0.7) -> Tuple[str, str]:
    """Only enter when 2x edge and 70%+ confident."""
    if model_prob < min_conf:
        return "HOLD", f"Low confidence {model_prob:.0%}"
    
    threshold = model_prob * 0.5
    
    if market_price <= threshold:
        edge = model_prob / market_price
        return "BUY_UP", f"Edge: {edge:.1f}x"
    
    return "HOLD", f"No edge"

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
        return 67000


class MarketFetcher:
    """Fetch 5-min or 15-min BTC markets."""
    
    def __init__(self, timeframe_minutes: int):
        self.interval = timeframe_minutes
    
    def get_epoch(self) -> int:
        now = datetime.now(timezone.utc)
        if self.interval == 5:
            minute = (now.minute // 5) * 5
            window = now.replace(minute=minute, second=0, microsecond=0)
        else:  # 15
            minute = (now.minute // 15) * 15
            window = now.replace(minute=minute, second=0, microsecond=0)
        return int(window.timestamp())
    
    def get_slug(self, epoch: int = None) -> str:
        if epoch is None:
            epoch = self.get_epoch()
        return f"btc-updown-{self.interval}m-{epoch}"
    
    def get_market(self) -> Optional[Dict]:
        slug = self.get_slug()
        
        try:
            r = _session.get(f"{GAMMA_API}/events/slug/{slug}", timeout=10)
            if not r.ok:
                return None
            
            event = r.json()
            markets = event.get("markets", [])
            if not markets:
                return None
            
            m = markets[0]
            outcome_prices = json.loads(m.get("outcomePrices", "[]"))
            
            window_start = datetime.now(timezone.utc)
            window_end = window_start + timedelta(minutes=self.interval)
            
            return {
                "question": m.get("question", ""),
                "timeframe": f"{self.interval}-min",
                "slug": slug,
                "up_price": float(outcome_prices[0]) if outcome_prices else 0.5,
                "down_price": float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5,
                "volume": float(m.get("volume", 0)),
                "liquidity": float(m.get("liquidity", 0)),
                "best_bid": float(m.get("bestBid", 0)),
                "best_ask": float(m.get("bestAsk", 0)),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            }
        except Exception as e:
            print(f"Error: {e}")
            return None


class HourlyMarketFetcher:
    """Fetch hourly BTC markets (one-time daily events)."""
    
    def get_market(self, hour_pm: int) -> Optional[Dict]:
        # hour_pm: 8 = 8PM ET, 9 = 9PM ET, etc.
        slug = f"bitcoin-up-or-down-april-4-2026-{hour_pm}pm-et"
        
        try:
            r = _session.get(f"{GAMMA_API}/events/slug/{slug}", timeout=10)
            if not r.ok:
                return None
            
            event = r.json()
            markets = event.get("markets", [])
            if not markets:
                return None
            
            m = markets[0]
            outcome_prices = json.loads(m.get("outcomePrices", "[]"))
            
            return {
                "question": m.get("question", ""),
                "timeframe": "1-hour",
                "slug": slug,
                "up_price": float(outcome_prices[0]) if outcome_prices else 0.5,
                "down_price": float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5,
                "volume": float(m.get("volume", 0)),
                "liquidity": float(m.get("liquidity", 0)),
                "best_bid": float(m.get("bestBid", 0)),
                "best_ask": float(m.get("bestAsk", 0)),
                "window_start": event.get("startTime", ""),
                "window_end": event.get("endDate", ""),
            }
        except Exception as e:
            return None


# === MAIN ===

def run_cycle():
    print("\n" + "="*70)
    print("MULTI-TIMEFRAME BTC TRADING SYSTEM")
    print("="*70)
    
    # Get BTC price
    btc_price = BTCPriceFetcher().get_price()
    print(f"\nBTC: ${btc_price:,.2f}")
    
    results = []
    
    # === 5-MIN MARKETS ===
    print("\n" + "-"*50)
    print("📊 5-MINUTE MARKET")
    print("-"*50)
    
    fetcher_5m = MarketFetcher(5)
    market_5m = fetcher_5m.get_market()
    
    if market_5m:
        print(f"Question: {market_5m['question']}")
        print(f"UP: ${market_5m['up_price']:.3f} | DOWN: ${market_5m['down_price']:.3f}")
        print(f"Volume: ${market_5m['volume']:,.0f}")
        
        features_5m = get_features(market_5m, btc_price)
        model_prob_5m = run_random_forest(features_5m)
        print(f"Model: {model_prob_5m:.1%}")
        
        signal_5m, reason_5m = check_entry(market_5m['up_price'], model_prob_5m)
        print(f"Signal: {signal_5m} ({reason_5m})")
        
        results.append(("5-min", market_5m, signal_5m, model_prob_5m))
    else:
        print("❌ No 5-min market found")
    
    # === 15-MIN MARKETS ===
    print("\n" + "-"*50)
    print("📊 15-MINUTE MARKET")
    print("-"*50)
    
    fetcher_15m = MarketFetcher(15)
    market_15m = fetcher_15m.get_market()
    
    if market_15m:
        print(f"Question: {market_15m['question']}")
        print(f"UP: ${market_15m['up_price']:.3f} | DOWN: ${market_15m['down_price']:.3f}")
        print(f"Volume: ${market_15m['volume']:,.0f}")
        
        features_15m = get_features(market_15m, btc_price)
        model_prob_15m = run_random_forest(features_15m)
        print(f"Model: {model_prob_15m:.1%}")
        
        signal_15m, reason_15m = check_entry(market_15m['up_price'], model_prob_15m)
        print(f"Signal: {signal_15m} ({reason_15m})")
        
        results.append(("15-min", market_15m, signal_15m, model_prob_15m))
    else:
        print("❌ No 15-min market found")
    
    # === HOURLY MARKETS ===
    print("\n" + "-"*50)
    print("📊 1-HOUR MARKET (9PM ET)")
    print("-"*50)
    
    fetcher_h = HourlyMarketFetcher()
    market_h = fetcher_h.get_market(9)
    
    if market_h:
        print(f"Question: {market_h['question']}")
        print(f"UP: ${market_h['up_price']:.3f} | DOWN: ${market_h['down_price']:.3f}")
        print(f"Volume: ${market_h['volume']:,.0f}")
        
        features_h = get_features(market_h, btc_price)
        model_prob_h = run_random_forest(features_h)
        print(f"Model: {model_prob_h:.1%}")
        
        signal_h, reason_h = check_entry(market_h['up_price'], model_prob_h)
        print(f"Signal: {signal_h} ({reason_h})")
        
        results.append(("1-hour", market_h, signal_h, model_prob_h))
    else:
        print("❌ No hourly market found")
    
    # === SUMMARY ===
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    for tf, market, signal, prob in results:
        print(f"{tf:8} | UP:{market['up_price']:.3f} | Model:{prob:.0%} | Signal:{signal}")
    
    # Log
    log_line = f"{datetime.now().isoformat()} | " + " | ".join(
        f"{tf}:{signal}" for tf, _, signal, _ in results
    )
    with open("/tmp/trading_multi.log", "a") as f:
        f.write(log_line + "\n")
    
    print("\n" + "="*70)
    return results


if __name__ == "__main__":
    print("="*70)
    print("MULTI-TIMEFRAME BTC TRADER")
    print("5-min + 15-min + 1-hour markets")
    print("="*70)
    
    run_cycle()