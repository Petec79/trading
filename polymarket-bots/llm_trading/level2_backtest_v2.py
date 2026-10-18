#!/usr/bin/env python3
"""
Enhanced Level 2: Backtesting Framework V2
With real Polymarket data integration and proper signal testing

Key insight: Split by DATE (Train=Jan-Oct, Val=Nov, Test=Dec)
"""
import json
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Tuple
import random

# Configuration
PAPER_MODE = True
INITIAL_CAPITAL = 10000

# Polymarket API
POLYMARKET_API = "https://gamma-api.polymarket.com"

class Backtester:
    def __init__(self, initial_capital=10000):
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.positions = []
        self.trades = []
        self.equity_curve = []
        self.daily_stats = defaultdict(list)
    
    def buy(self, date, price, quantity, reason=""):
        cost = price * quantity
        if cost > self.capital:
            quantity = self.capital / price
        
        self.positions.append({
            "entry_date": date,
            "entry_price": price,
            "quantity": quantity,
            "reason": reason
        })
        self.capital -= price * quantity
        self.trades.append({"type": "BUY", "date": date, "price": price, "qty": quantity, "reason": reason, "pnl": 0})
    
    def sell(self, date, price, reason=""):
        pnl = 0
        for pos in self.positions:
            pnl += (price - pos["entry_price"]) * pos["quantity"]
        
        self.capital += price * sum(p["quantity"] for p in self.positions)
        sell_trade = {"type": "SELL", "date": date, "price": price, "reason": reason, "pnl": pnl}
        self.trades.append(sell_trade)
        self.positions = []
        return pnl
    
    def get_equity(self, current_price):
        position_value = sum(p["quantity"] * current_price for p in self.positions)
        return self.capital + position_value
    
    def summary(self) -> Dict:
        final_equity = self.capital
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100
        
        closed_trades = [t for t in self.trades if t["type"] == "SELL"]
        wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in closed_trades if t.get("pnl", 0) < 0)
        total_trades = len(closed_trades)
        
        avg_win = sum(t["pnl"] for t in closed_trades if t.get("pnl", 0) > 0) / max(wins, 1)
        avg_loss = sum(abs(t["pnl"]) for t in closed_trades if t.get("pnl", 0) < 0) / max(losses, 1)
        
        return {
            "initial_capital": self.initial_capital,
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total_trades * 100) if total_trades > 0 else 0,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": avg_win / max(avg_loss, 0.01) if avg_loss > 0 else 0
        }


def get_polymarket_history(condition_id: str, days: int = 30) -> List[Dict]:
    """Get historical price data for a Polymarket market."""
    # In production: query historical data
    # For now: return simulated historical data
    import time
    base_price = 0.5
    data = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days-i)).strftime("%Y-%m-%d")
        # Simulate realistic price movement
        change = random.uniform(-0.05, 0.05)
        base_price = max(0.1, min(0.9, base_price + change))
        data.append({
            "date": date,
            "price": base_price,
            "volume": random.randint(10000, 100000)
        })
    return data


def get_historical_markets(limit: int = 20) -> List[Dict]:
    """Get markets with historical data."""
    try:
        resp = requests.get(
            f"{POLYMARKET_API}/markets",
            params={"limit": limit, "closed": "false"},
            timeout=10
        )
        if resp.ok:
            markets = resp.json()
            return [
                {
                    "question": m.get("question", ""),
                    "condition_id": m.get("conditionId"),
                    "volume": float(m.get("volume", 0)),
                    "slug": m.get("slug", "")
                }
                for m in markets if float(m.get("volume", 0)) > 10000
            ]
    except:
        pass
    return []


def run_strategy_backtest(
    strategy_func, 
    historical_data: List[Dict],
    train_end_idx: int,
    val_end_idx: int
) -> Tuple[Dict, Dict, Dict]:
    """
    Run backtest with proper date splitting.
    
    Returns: (train_results, val_results, test_results)
    """
    # Split data
    train_data = historical_data[:train_end_idx]
    val_data = historical_data[train_end_idx:val_end_idx]
    test_data = historical_data[val_end_idx:]
    
    # Run on train
    bt_train = Backtester(INITIAL_CAPITAL)
    for day in train_data:
        signal = strategy_func(day["price"], day)
        if signal == "BUY" and not bt_train.positions:
            bt_train.buy(day["date"], day["price"], 0.1)
        elif signal == "SELL" and bt_train.positions:
            bt_train.sell(day["date"], day["price"])
    
    # Run on validation
    bt_val = Backtester(INITIAL_CAPITAL)
    for day in val_data:
        signal = strategy_func(day["price"], day)
        if signal == "BUY" and not bt_val.positions:
            bt_val.buy(day["date"], day["price"], 0.1)
        elif signal == "SELL" and bt_val.positions:
            bt_val.sell(day["date"], day["price"])
    
    # Run on test
    bt_test = Backtester(INITIAL_CAPITAL)
    for day in test_data:
        signal = strategy_func(day["price"], day)
        if signal == "BUY" and not bt_test.positions:
            bt_test.buy(day["date"], day["price"], 0.1)
        elif signal == "SELL" and bt_test.positions:
            bt_test.sell(day["date"], day["price"])
    
    return bt_train.summary(), bt_val.summary(), bt_test.summary()


# === STRATEGY TEMPLATES ===

def random_strategy(price: float, data: Dict) -> str:
    """Random baseline - 33% each."""
    return random.choice(["BUY", "SELL", "HOLD"])

def momentum_strategy(price: float, data: Dict, lookback: int = 3) -> str:
    """Buy when price rising, sell when falling."""
    # Simplified - in production would check actual momentum
    if random.random() > 0.5:
        return "BUY"
    elif random.random() < 0.3:
        return "SELL"
    return "HOLD"

def mean_reversion_strategy(price: float, data: Dict) -> str:
    """Buy low, sell high."""
    if price < 0.4:
        return "BUY"
    elif price > 0.6:
        return "SELL"
    return "HOLD"

def breakout_strategy(price: float, data: Dict) -> str:
    """Buy breakouts, sell breakdowns."""
    if price > 0.7:
        return "SELL"  # Overbought
    elif price < 0.3:
        return "BUY"  # Oversold
    return "HOLD"


# === MAIN ===
if __name__ == "__main__":
    print("=" * 60)
    print("ENHANCED BACKTESTING FRAMEWORK V2")
    print("=" * 60)
    
    # Get real markets
    print("\n--- Fetching Markets ---")
    markets = get_historical_markets()
    print(f"Found {len(markets)} active markets")
    
    # Select a market to backtest
    if markets:
        test_market = markets[0]
        print(f"\nBacktesting: {test_market['question'][:50]}...")
        print(f"Volume: ${test_market['volume']:,.0f}")
        
        # Get historical data (simulated for now)
        history = get_polymarket_history(test_market["condition_id"], days=60)
        print(f"Historical data points: {len(history)}")
        
        # Test different strategies
        strategies = [
            ("Random (baseline)", random_strategy),
            ("Mean Reversion", mean_reversion_strategy),
            ("Breakout", breakout_strategy),
        ]
        
        print(f"\n--- Strategy Comparison ---")
        for name, strategy in strategies:
            train_r, val_r, test_r = run_strategy_backtest(
                strategy, history, 
                train_end_idx=40,  # Train: first 40 days
                val_end_idx=50     # Val: days 40-50
            )
            
            print(f"\n{name}:")
            print(f"  Train: {train_r['total_return_pct']:+.1f}% | Win {train_r['win_rate']:.0f}%")
            print(f"  Val:   {val_r['total_return_pct']:+.1f}% | Win {val_r['win_rate']:.0f}%")
            print(f"  Test:  {test_r['total_return_pct']:+.1f}% | Win {test_r['win_rate']:.0f}%")
    
    print(f"\n{'='*60}")
    print("KEY INSIGHT: Split by DATE, not randomly!")
    print(f"Train=Days 0-40, Val=40-50, Test=50-60")
    print(f"{'='*60}")