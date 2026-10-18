#!/usr/bin/env python3
"""
Multi-Timeframe BTC Trading Bot WITH POSITION SIZING
Tracks trades with proper risk management and position sizing

Features:
- Configurable capital and risk per trade
- Position sizing: Risk % × Capital / (Entry Price × Odds)
- Full PnL tracking in dollars
- Win/Loss tracking
- Sharpe Ratio calculation
"""
import os
import sys
import json
import math
import random
import requests
import pickle
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

# === CONFIGURATION ===
CAPITAL = 10000  # Starting capital in USD
RISK_PER_TRADE = 0.05  # 5% of capital per trade
MAX_POSITIONS = 2  # Max simultaneous positions

# APIs
GAMMA_API = "https://gamma-api.polymarket.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

# State file
STATE_FILE = "/tmp/trading_state.pkl"

class TradingState:
    def __init__(self):
        self.capital = CAPITAL
        self.initial_capital = CAPITAL
        self.positions = {}  # {timeframe: {"signal": x, "entry_price": x, "shares": x, "cost": x, "entry_time": x}}
        self.closed_trades = []
        self.log_returns = []
    
    def save(self):
        try:
            with open(STATE_FILE, 'wb') as f:
                pickle.dump(self, f)
        except:
            pass
    
    @classmethod
    def load(cls):
        try:
            with open(STATE_FILE, 'rb') as f:
                return pickle.load(f)
        except:
            return cls()
    
    def calculate_position_size(self, price: float) -> Tuple[int, float]:
        """
        Calculate position size based on risk management.
        
        Risk amount = CAPITAL × RISK_PER_TRADE
        Shares = Risk Amount / (Price × (1 - Price))  # Accounting for binary outcome
        
        Returns: (shares, total_cost)
        """
        risk_amount = self.capital * RISK_PER_TRADE
        
        # For binary options: cost per share = price, max payout = 1.0
        # We size based on losing the full stake if we lose
        # So: shares = risk_amount / price
        shares = int(risk_amount / price)
        total_cost = shares * price
        
        # Don't risk more than calculated
        return shares, total_cost
    
    def open_position(self, timeframe: str, signal: str, price: float):
        # Calculate position size
        shares, cost = self.calculate_position_size(price)
        
        if cost > self.capital:
            # Not enough capital
            return False
        
        self.positions[timeframe] = {
            "signal": signal,
            "entry_price": price,
            "shares": shares,
            "cost": cost,
            "entry_time": datetime.now(),
            "entry_btc_price": self._get_btc_price()
        }
        
        self.capital -= cost
        self.save()
        return True
    
    def close_position(self, timeframe: str, exit_price: float, outcome: str):
        if timeframe not in self.positions:
            return None
        
        pos = self.positions[timeframe]
        shares = pos["shares"]
        entry_price = pos["entry_price"]
        
        # Calculate PnL
        # If we bought UP (bet on UP outcome):
        # - If UP wins: profit = shares × (1 - entry_price)
        # - If UP loses: loss = shares × entry_price
        
        if pos["signal"] == "BUY_UP":
            if outcome == "UP":
                # Win: get 1.0 per share minus what we paid
                payout = shares * 1.0
                pnl = payout - pos["cost"]
            else:
                # Lose: lose our stake
                pnl = -pos["cost"]
        else:  # BUY_DOWN
            if outcome == "DOWN":
                # Win: get 1.0 per share minus what we paid for DOWN
                payout = shares * 1.0
                pnl = payout - pos["cost"]
            else:
                # Lose
                pnl = -pos["cost"]
        
        # Log return for Sharpe
        if pos["cost"] > 0 and (pos["cost"] + pnl) > 0:
            log_ret = math.log((pos["cost"] + pnl) / pos["cost"])
            self.log_returns.append(log_ret)
        
        # Update capital
        self.capital += pos["cost"] + pnl
        
        trade = {
            "timeframe": timeframe,
            "signal": pos["signal"],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "shares": shares,
            "cost": pos["cost"],
            "outcome": outcome,
            "pnl": pnl,
            "pnl_pct": (pnl / pos["cost"] * 100) if pos["cost"] > 0 else 0,
            "log_return": log_ret if 'log_ret' in dir() else 0,
            "entry_time": pos["entry_time"].isoformat(),
            "exit_time": datetime.now().isoformat()
        }
        
        self.closed_trades.append(trade)
        del self.positions[timeframe]
        self.save()
        
        return trade
    
    def _get_btc_price(self) -> float:
        try:
            r = _session.get(f"{COINGECKO_API}/simple/price", 
                           params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=5)
            if r.ok:
                return r.json()["bitcoin"]["usd"]
        except:
            pass
        return 67000
    
    def get_stats(self) -> Dict:
        if not self.closed_trades:
            return {
                "trades": 0, "wins": 0, "win_rate": 0, 
                "total_pnl": 0, "sharpe": 0,
                "capital": self.capital,
                "return_pct": 0
            }
        
        wins = sum(1 for t in self.closed_trades if t["pnl"] > 0)
        total_pnl = sum(t["pnl"] for t in self.closed_trades)
        wins_pnl = sum(t["pnl"] for t in self.closed_trades if t["pnl"] > 0)
        losses_pnl = sum(t["pnl"] for t in self.closed_trades if t["pnl"] < 0)
        
        # Sharpe ratio
        if len(self.log_returns) >= 2:
            mean_ret = sum(self.log_returns) / len(self.log_returns)
            std_ret = (sum((r - mean_ret) ** 2 for r in self.log_returns) / len(self.log_returns)) ** 0.5
            sharpe = (mean_ret - 0.02/252) / std_ret * math.sqrt(252) if std_ret > 0 else 0
        else:
            sharpe = 0
        
        return {
            "trades": len(self.closed_trades),
            "wins": wins,
            "win_rate": wins / len(self.closed_trades),
            "total_pnl": total_pnl,
            "return_pct": ((self.capital - self.initial_capital) / self.initial_capital) * 100,
            "sharpe": sharpe,
            "capital": self.capital,
            "avg_win": wins_pnl / max(wins, 1),
            "avg_loss": abs(losses_pnl) / max(len(self.closed_trades) - wins, 1),
            "profit_factor": abs(wins_pnl / losses_pnl) if losses_pnl != 0 else 0
        }
    
    def get_positions(self) -> Dict:
        return self.positions


# === DATA FETCHERS ===

def get_btc_price() -> float:
    try:
        r = _session.get(f"{COINGECKO_API}/simple/price", 
                       params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=5)
        if r.ok:
            return r.json()["bitcoin"]["usd"]
    except:
        pass
    return 67000


def get_market(timeframe_minutes: int, epoch: int = None) -> Optional[Dict]:
    if epoch is None:
        now = datetime.now(timezone.utc)
        if timeframe_minutes == 5:
            minute = (now.minute // 5) * 5
            window = now.replace(minute=minute, second=0, microsecond=0)
        else:
            minute = (now.minute // 15) * 15
            window = now.replace(minute=minute, second=0, microsecond=0)
        epoch = int(window.timestamp())
    
    slug = f"btc-updown-{timeframe_minutes}m-{epoch}"
    
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
        
        window_end = datetime.fromisoformat(m.get("endDate", "").replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        
        return {
            "timeframe": f"{timeframe_minutes}-min",
            "slug": slug,
            "epoch": epoch,
            "question": m.get("question", ""),
            "up_price": float(outcome_prices[0]) if outcome_prices else 0.5,
            "down_price": float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5,
            "volume": float(m.get("volume", 0)),
            "liquidity": float(m.get("liquidity", 0)),
            "window_end": window_end,
            "is_resolved": window_end < now,
        }
    except Exception as e:
        return None


# === SIGNAL GENERATION ===

def generate_signal(market: Dict) -> Tuple[str, float, str]:
    votes = []
    price = market["up_price"]
    volume = market["volume"]
    
    if price < 0.25:
        votes.append(0.8)
    elif price > 0.75:
        votes.append(0.2)
    else:
        votes.append(price + random.uniform(-0.1, 0.1))
    
    if volume > 5000:
        votes.append(price * 1.1)
    else:
        votes.append(price)
    
    model_prob = max(0.05, min(0.95, sum(votes) / len(votes)))
    
    if model_prob >= 0.7:
        threshold = model_prob * 0.5
        if price <= threshold:
            edge = model_prob / price
            return "BUY_UP", model_prob, f"Edge: {edge:.1f}x"
        elif (1 - price) <= threshold:
            return "BUY_DOWN", model_prob, f"Edge: {model_prob/(1-price):.1f}x"
    
    return "HOLD", model_prob, "No edge"


# === MAIN CYCLE ===

def run_cycle():
    # Load or create fresh state
    try:
        state = TradingState.load()
        # Ensure capital exists (for old state files)
        if not hasattr(state, 'capital'):
            state.capital = CAPITAL
    except:
        state = TradingState()
    
    print("\n" + "="*70)
    print("MULTI-TIMEFRAME BTC TRADING WITH POSITION SIZING")
    print("="*70)
    
    # Configuration display
    print(f"\n💰 CONFIG: Capital=${CAPITAL:,.0f} | Risk={RISK_PER_TRADE*100:.0f}% | Max Positions={MAX_POSITIONS}")
    print(f"   Current Capital: ${state.capital:,.2f}")
    
    btc_price = get_btc_price()
    print(f"\nBTC: ${btc_price:,.2f}")
    
    # Check open positions
    print("\n📡 Open Positions:")
    for tf, pos in state.get_positions().items():
        print(f"  {tf}: {pos['signal']} | {pos['shares']} shares @ ${pos['entry_price']:.3f} | Cost: ${pos['cost']:.2f}")
    
    results = []
    
    # Process each timeframe
    for interval in [5, 15]:
        print(f"\n{'='*50}")
        print(f"📊 {interval}-MINUTE MARKET")
        print("="*50)
        
        market = get_market(interval)
        
        if not market:
            print(f"❌ No {interval}-min market found")
            continue
        
        print(f"Question: {market['question']}")
        print(f"UP: ${market['up_price']:.3f} | DOWN: ${market['down_price']:.3f}")
        print(f"Volume: ${market['volume']:,.0f} | Resolved: {market['is_resolved']}")
        
        timeframe_key = f"{interval}-min"
        
        # Check for resolution
        if timeframe_key in state.get_positions() and market["is_resolved"]:
            print(f"  → Market resolved! Determining outcome...")
            # In production: query actual resolution
            # For now: use BTC price movement as proxy
            outcome = "UNKNOWN"  # Would need actual API to determine
            # Close with UNKNOWN for now
            trade = state.close_position(timeframe_key, market["up_price"], outcome)
            if trade:
                print(f"  ✅ Closed: {trade['outcome']} | PnL: ${trade['pnl']:+.2f}")
        
        # Generate signal
        signal, confidence, reason = generate_signal(market)
        print(f"Signal: {signal} ({confidence:.0%}) - {reason}")
        
        results.append((timeframe_key, signal, confidence, market))
        
        # Open position if signal is BUY and room for more positions
        if signal.startswith("BUY") and len(state.get_positions()) < MAX_POSITIONS:
            if timeframe_key not in state.get_positions():
                success = state.open_position(timeframe_key, signal, market["up_price"])
                if success:
                    pos = state.get_positions()[timeframe_key]
                    print(f"  ✅ Opened: {signal}")
                    print(f"      Shares: {pos['shares']} | Cost: ${pos['cost']:.2f}")
                    print(f"      Risk: ${state.capital * RISK_PER_TRADE:.2f} ({RISK_PER_TRADE*100:.0f}% of capital)")
                else:
                    print(f"  ❌ Insufficient capital")
    
    # Stats
    stats = state.get_stats()
    print(f"\n{'='*70}")
    print("📊 TRADING STATS")
    print("="*70)
    print(f"Capital: ${stats['capital']:,.2f} ({stats['return_pct']:+.2f}% return)")
    print(f"Open positions: {len(state.get_positions())}")
    print(f"Closed trades: {stats['trades']}")
    
    if stats['trades'] > 0:
        print(f"Win rate: {stats['win_rate']:.0%} ({stats['wins']}W/{stats['trades']-stats['wins']}L)")
        print(f"Total PnL: ${stats['total_pnl']:+.2f}")
        print(f"Sharpe: {stats['sharpe']:.2f}")
        print(f"Avg Win: ${stats['avg_win']:.2f} | Avg Loss: ${stats['avg_loss']:.2f}")
        print(f"Profit Factor: {stats['profit_factor']:.2f}")
    
    # Log
    log_line = (f"{datetime.now().isoformat()} | "
                f"Capital:${stats['capital']:,.0f} | "
                f"Trades:{stats['trades']} | "
                f"WR:{stats['win_rate']:.0%} | "
                f"PnL:${stats['total_pnl']:+.2f} | "
                f"Return:{stats['return_pct']:+.1f}%")
    with open("/tmp/trading_v5.log", "a") as f:
        f.write(log_line + "\n")
    
    print("\n" + "="*70)
    return state, results


if __name__ == "__main__":
    print("="*70)
    print("MULTI-TIMEFRAME TRADER v5 (POSITION SIZING)")
    print(f"Capital: ${CAPITAL:,.0f} | Risk: {RISK_PER_TRADE*100:.0f}% per trade")
    print("="*70)
    
    state, results = run_cycle()
    
    if state.closed_trades:
        print("\n📋 RECENT CLOSED TRADES:")
        for t in state.closed_trades[-5:]:
            print(f"  {t['timeframe']} | {t['signal']} | "
                  f"Cost:${t['cost']:.2f} | PnL:${t['pnl']:+.2f} ({t['pnl_pct']:+.1f}%)")