#!/usr/bin/env python3
"""
Level 2: Backtesting Framework
Validates trading signals against historical data

Critical: Split by DATE (Train=Jan-Oct, Val=Nov, Test=Dec)
This is the #1 mistake in financial ML - splitting randomly leaks future info!
"""
import json
import requests
from datetime import datetime, timedelta
from collections import defaultdict

# Configuration
PAPER_MODE = True
INITIAL_CAPITAL = 10000

class Backtester:
    def __init__(self, initial_capital=10000):
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.positions = []
        self.trades = []
        self.equity_curve = []
    
    def buy(self, date, price, quantity, reason=""):
        """Open a long position."""
        cost = price * quantity
        if cost > self.capital:
            quantity = self.capital / price  # Max we can afford
        
        self.positions.append({
            "entry_date": date,
            "entry_price": price,
            "quantity": quantity,
            "reason": reason
        })
        self.capital -= price * quantity
        self.trades.append({"type": "BUY", "date": date, "price": price, "qty": quantity, "reason": reason})
    
    def sell(self, date, price, reason=""):
        """Close all positions."""
        pnl = 0
        for pos in self.positions:
            pnl += (price - pos["entry_price"]) * pos["quantity"]
        
        self.capital += price * sum(p["quantity"] for p in self.positions)
        self.trades.append({"type": "SELL", "date": date, "price": price, "reason": reason})
        self.positions = []
        return pnl
    
    def get_equity(self, current_price):
        """Current portfolio value."""
        position_value = sum(p["quantity"] * current_price for p in self.positions)
        return self.capital + position_value
    
    def summary(self):
        """Generate backtest summary."""
        final_equity = self.capital
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100
        
        wins = sum(1 for t in self.trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in self.trades if t.get("pnl", 0) < 0)
        total_trades = len([t for t in self.trades if t["type"] == "SELL"])
        
        return {
            "initial_capital": self.initial_capital,
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total_trades * 100) if total_trades > 0 else 0
        }


# === HISTORICAL DATA (Sample) ===
# In production, fetch from Polymarket API
HISTORICAL_DATA = [
    {"date": "2025-01-15", "price": 42000, "news": "SEC approves Bitcoin ETF"},
    {"date": "2025-02-01", "price": 45000, "news": "Institutional adoption grows"},
    {"date": "2025-02-15", "price": 43000, "news": "Regulatory concerns emerge"},
    {"date": "2025-03-01", "price": 48000, "news": "Major bank announces crypto desk"},
    {"date": "2025-03-15", "price": 52000, "news": "Bitcoin hits new high"},
    {"date": "2025-04-01", "price": 49000, "news": "Profit taking begins"},
    {"date": "2025-04-15", "price": 55000, "news": "Halving event approaches"},
    {"date": "2025-05-01", "price": 58000, "news": "Halving completes"},
    {"date": "2025-05-15", "price": 62000, "news": "Momentum continues"},
    {"date": "2025-06-01", "price": 65000, "news": "All time high broken"},
    {"date": "2025-07-01", "price": 60000, "news": "Correction begins"},
    {"date": "2025-08-01", "price": 56000, "news": "Fear in market"},
    {"date": "2025-09-01", "price": 59000, "news": "Recovery starts"},
    {"date": "2025-10-01", "price": 62000, "news": "Stable growth"},
    {"date": "2025-11-01", "price": 68000, "news": "Election uncertainty"},
    {"date": "2025-12-01", "price": 72000, "news": "Post-election rally"},
    {"date": "2025-12-15", "price": 75000, "news": "Year end surge"},
]

def split_data_by_date(data, train_end="2025-09", val_start="2025-10", val_end="2025-11"):
    """Split data by date - critical for valid backtesting!"""
    train = [d for d in data if d["date"] < val_start]
    val = [d for d in data if val_start <= d["date"] <= val_end]
    test = [d for d in data if d["date"] > val_end]
    return train, val, test

def simulate_signal(news):
    """Simulate LLM signal based on news (replace with real LLM calls)."""
    news_lower = news.lower()
    if "surge" in news_lower or "high" in news_lower or "approves" in news_lower or "rally" in news_lower:
        return "BUY"
    elif "correction" in news_lower or "fear" in news_lower or "concern" in news_lower or "delay" in news_lower:
        return "SELL"
    return "HOLD"

def run_backtest(data, strategy_func):
    """Run backtest with given strategy function."""
    bt = Backtester(INITIAL_CAPITAL)
    position = None
    
    for i, day in enumerate(data):
        signal = strategy_func(day["news"])
        price = day["price"]
        date = day["date"]
        
        if signal == "BUY" and not position:
            bt.buy(date, price, 0.1, day["news"])  # Buy 10%
            position = True
        elif signal == "SELL" and position:
            pnl = bt.sell(date, price, day["news"])
            # Record PnL on trade
            if bt.trades:
                bt.trades[-1]["pnl"] = pnl
            position = None
        
        # Track equity
        bt.equity_curve.append({"date": date, "equity": bt.get_equity(price)})
    
    return bt

# === MAIN ===
if __name__ == "__main__":
    print("=" * 60)
    print("LEVEL 2: Backtesting Framework")
    print("=" * 60)
    
    # Split data (critical: by date, not random!)
    train, val, test = split_data_by_date(HISTORICAL_DATA)
    print(f"\nData splits:")
    print(f"  Train: {len(train)} days ({train[0]['date']} to {train[-1]['date']})")
    print(f"  Val:   {len(val)} days ({val[0]['date']} to {val[-1]['date']})")
    print(f"  Test:  {len(test)} days ({test[0]['date']} to {test[-1]['date']})")
    
    # Run validation backtest
    print(f"\n--- Validation Backtest ---")
    bt_val = run_backtest(val, simulate_signal)
    summary_val = bt_val.summary()
    
    print(f"  Trades: {summary_val['total_trades']}")
    print(f"  Win Rate: {summary_val['win_rate']:.1f}%")
    print(f"  Return: {summary_val['total_return_pct']:.2f}%")
    
    # Run test backtest
    print(f"\n--- Test Backtest ---")
    bt_test = run_backtest(test, simulate_signal)
    summary_test = bt_test.summary()
    
    print(f"  Trades: {summary_test['total_trades']}")
    print(f"  Win Rate: {summary_test['win_rate']:.1f}%")
    print(f"  Return: {summary_test['total_return_pct']:.2f}%")
    
    # Multi-ticker dashboard
    print(f"\n--- Multi-Ticker Dashboard (Sample) ---")
    tickers = ["BTC", "ETH", "SOL"]
    for ticker in tickers:
        # Simulate different performance
        mock_summary = {
            "total_return_pct": 15.5 if ticker == "BTC" else 8.2,
            "win_rate": 62.3,
            "total_trades": 12
        }
        print(f"  {ticker}: Return {mock_summary['total_return_pct']:+.1f}% | Win {mock_summary['win_rate']:.0f}% | {mock_summary['total_trades']} trades")
    
    print(f"\n{'='*60}")
    print("KEY INSIGHT: Split by DATE, not randomly!")
    print("Train=Jan-Oct, Val=Nov, Test=Dec")
    print(f"{'='*60}")