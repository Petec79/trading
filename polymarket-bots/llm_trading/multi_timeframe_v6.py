#!/usr/bin/env python3
"""
Multi-Timeframe BTC Trading Bot v6 - FIXED RESOLUTION TRACKING
Uses BTC price movement as proxy for market resolution

Resolution Logic:
- Record BTC price at entry
- When market endDate passes, check if BTC went up or down
- This mirrors how Polymarket resolves (Chainlink BTC/USD)
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
CAPITAL = 10000
RISK_PER_TRADE = 0.05
MAX_POSITIONS = 2

# APIs
GAMMA_API = "https://gamma-api.polymarket.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

STATE_FILE = "/tmp/trading_state_v6.pkl"


def get_btc_price() -> float:
    """Get current BTC price."""
    try:
        r = _session.get(f"{COINGECKO_API}/simple/price", 
                       params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=5)
        if r.ok:
            return r.json()["bitcoin"]["usd"]
    except:
        pass
    return 67000


class TradingState:
    def __init__(self):
        self.capital = CAPITAL
        self.initial_capital = CAPITAL
        # Positions now include BTC price at entry for resolution
        self.positions = {}  # {timeframe: {"signal": x, "entry_price": x, "shares": x, "cost": x, "entry_time": x, "entry_btc": x, "window_end": x}}
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
        risk_amount = self.capital * RISK_PER_TRADE
        shares = int(risk_amount / price)
        total_cost = shares * price
        return shares, total_cost
    
    def open_position(self, timeframe: str, signal: str, price: float, window_end: datetime, epoch: int, market_question: str = "", window_start: datetime = None):
        shares, cost = self.calculate_position_size(price)
        
        if cost > self.capital:
            return False
        
        self.positions[timeframe] = {
            "signal": signal,
            "entry_price": price,
            "shares": shares,
            "cost": cost,
            "entry_time": datetime.now(),
            "entry_btc_price": get_btc_price(),
            "window_end": window_end,
            "window_start": window_start,
            "epoch": epoch,
            "market_question": market_question
        }
        
        self.capital -= cost
        self.save()
        return True
    
    def check_resolutions(self):
        """Check all positions for resolution using Polymarket's actual result."""
        now = datetime.now(timezone.utc)
        closed = []
        
        for timeframe, pos in list(self.positions.items()):
            epoch = pos.get("epoch")
            if not epoch:
                # Fallback to old time-based check
                window_end = pos.get("window_end")
                if window_end:
                    if isinstance(window_end, str):
                        window_end = datetime.fromisoformat(window_end.replace('Z', '+00:00'))
                    if now > window_end:
                        current_btc = get_btc_price()
                        entry_btc = pos.get("entry_btc_price", current_btc)
                        outcome = "UP" if current_btc > entry_btc else ("DOWN" if current_btc < entry_btc else "PUSH")
                        trade = self._close_position(timeframe, outcome, current_btc)
                        if trade:
                            closed.append(trade)
                continue
            
            # Get the market data to check actual resolution
            interval = timeframe.replace("-min", "")
            slug = f"btc-updown-{interval}m-{epoch}"
            
            try:
                r = _session.get(f"{GAMMA_API}/events/slug/{slug}", timeout=10)
                if r.ok:
                    event = r.json()
                    markets = event.get("markets", [])
                    if markets and markets[0].get("closed"):
                        m = markets[0]
                        
                        # Parse prices - winner has price >= 0.99
                        prices = [float(p) for p in json.loads(m.get("outcomePrices", "[]"))]
                        outcomes = json.loads(m.get("outcomes", "[]"))
                        
                        # Find winner
                        outcome = None
                        for i, p in enumerate(prices):
                            if p >= 0.99:
                                outcome = outcomes[i]
                                break
                        
                        if outcome:
                            # Use Polymarket's actual result
                            trade = self._close_position(timeframe, outcome, prices[0] if outcome == "Up" else prices[1])
                            if trade:
                                closed.append(trade)
            except Exception as e:
                print(f"Resolution check error: {e}")
        
        return closed
    
    def _close_position(self, timeframe: str, outcome: str, exit_btc_price: float):
        if timeframe not in self.positions:
            return None
        
        pos = self.positions[timeframe]
        shares = pos["shares"]
        entry_price = pos["entry_price"]
        
        # Calculate PnL
        if pos["signal"] == "BUY_UP":
            if outcome == "UP":
                payout = shares * 1.0
                pnl = payout - pos["cost"]
            elif outcome == "PUSH":
                pnl = 0  # Return of capital
            else:  # DOWN
                pnl = -pos["cost"]
        else:  # BUY_DOWN
            if outcome == "DOWN":
                payout = shares * 1.0
                pnl = payout - pos["cost"]
            elif outcome == "PUSH":
                pnl = 0
            else:  # UP
                pnl = -pos["cost"]
        
        # Log return
        if pos["cost"] > 0 and (pos["cost"] + pnl) > 0:
            log_ret = math.log((pos["cost"] + pnl) / pos["cost"])
            self.log_returns.append(log_ret)
        
        # Update capital
        self.capital += pos["cost"] + pnl
        
        trade = {
            "timeframe": timeframe,
            "signal": pos["signal"],
            "entry_price": entry_price,
            "exit_price": 1.0 if outcome in ["UP", "DOWN"] else 0.5,  # Simplified
            "shares": shares,
            "cost": pos["cost"],
            "entry_btc": pos["entry_btc_price"],
            "exit_btc": exit_btc_price,
            "outcome": outcome,
            "pnl": pnl,
            "pnl_pct": (pnl / pos["cost"] * 100) if pos["cost"] > 0 else 0,
            "log_return": log_ret if 'log_ret' in dir() else 0,
            "entry_time": pos["entry_time"].isoformat(),
            "exit_time": datetime.now().isoformat(),
            # New fields for market tracking
            "market_question": pos.get("market_question", "Unknown"),
            "window_start": pos.get("window_start", "").isoformat() if hasattr(pos.get("window_start", ""), "isoformat") else str(pos.get("window_start", "")),
            "window_end": pos.get("window_end", "").isoformat() if hasattr(pos.get("window_end", ""), "isoformat") else str(pos.get("window_end", "")),
            "epoch": pos.get("epoch", 0)
        }
        
        self.closed_trades.append(trade)
        del self.positions[timeframe]
        self.save()
        
        return trade
    
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


def get_market(timeframe_minutes: int) -> Optional[Dict]:
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
        
        return {
            "timeframe": f"{timeframe_minutes}-min",
            "epoch": epoch,
            "question": m.get("question", ""),
            "up_price": float(outcome_prices[0]) if outcome_prices else 0.5,
            "down_price": float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5,
            "volume": float(m.get("volume", 0)),
            "window_start": window,
            "window_end": window_end,
        }
    except:
        return None


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


def run_cycle():
    state = TradingState.load()
    
    print("\n" + "="*70)
    print("MULTI-TIMEFRAME BTC TRADING v6 (FIXED RESOLUTION)")
    print("="*70)
    
    print(f"\n💰 Capital: ${CAPITAL:,.0f} | Risk: {RISK_PER_TRADE*100:.0f}%")
    print(f"   Current Capital: ${state.capital:,.2f}")
    
    btc_price = get_btc_price()
    print(f"\nBTC: ${btc_price:,.2f}")
    
    # Check for resolved positions FIRST
    print("\n🔍 Checking for resolved positions...")
    resolved_trades = state.check_resolutions()
    if resolved_trades:
        for t in resolved_trades:
            print(f"  ✅ Resolved: {t['timeframe']} | {t['outcome']} | PnL: ${t['pnl']:+.2f}")
    
    # Show open positions
    print("\n📡 Open Positions:")
    for tf, pos in state.positions.items():
        window_end = pos.get("window_end")
        if isinstance(window_end, datetime):
            window_end = window_end.strftime("%H:%M UTC")
        print(f"  {tf}: {pos['signal']} | {pos['shares']} shares @ ${pos['entry_price']:.3f}")
        print(f"           Entry BTC: ${pos['entry_btc_price']:,.0f} | Window ends: {window_end}")
    
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
        
        # Don't process resolved markets
        now = datetime.now(timezone.utc)
        if market["window_end"] < now:
            print(f"  Market already resolved, skipping signal generation")
            continue
        
        print(f"Question: {market['question']}")
        print(f"UP: ${market['up_price']:.3f} | DOWN: ${market['down_price']:.3f}")
        print(f"Volume: ${market['volume']:,.0f}")
        print(f"Ends: {market['window_end'].strftime('%H:%M UTC')}")
        
        timeframe_key = f"{interval}-min"
        
        # Generate signal
        signal, confidence, reason = generate_signal(market)
        print(f"Signal: {signal} ({confidence:.0%}) - {reason}")
        
        results.append((timeframe_key, signal, confidence, market))
        
        # Open position
        if signal.startswith("BUY") and len(state.positions) < MAX_POSITIONS:
            if timeframe_key not in state.positions:
                epoch = market.get("epoch")
                market_question = market.get("question", "")
                window_start = market.get("window_start")
                window_end = market.get("window_end")
                success = state.open_position(timeframe_key, signal, market["up_price"], window_end, epoch, market_question, window_start)
                if success:
                    pos = state.positions[timeframe_key]
                    print(f"  ✅ Opened: {signal}")
                    print(f"      Market: {market_question}")
                    print(f"      Window: {window_start.strftime('%H:%M')} - {window_end.strftime('%H:%M UTC')}")
                    print(f"      Shares: {pos['shares']} | Cost: ${pos['cost']:.2f}")
                    print(f"      Entry BTC: ${pos['entry_btc_price']:,.0f}")
    
    # Stats
    stats = state.get_stats()
    print(f"\n{'='*70}")
    print("📊 TRADING STATS")
    print("="*70)
    print(f"Capital: ${stats['capital']:,.2f} ({stats['return_pct']:+.2f}%)")
    print(f"Open: {len(state.positions)} | Closed: {stats['trades']}")
    
    if stats['trades'] > 0:
        print(f"Win rate: {stats['win_rate']:.0%} ({stats['wins']}W)")
        print(f"Total PnL: ${stats['total_pnl']:+.2f}")
        print(f"Sharpe: {stats['sharpe']:.2f}")
        print(f"Profit Factor: {stats['profit_factor']:.2f}")
    
    # Log
    log_line = (f"{datetime.now().isoformat()} | "
                f"Capital:${stats['capital']:,.0f} | "
                f"Trades:{stats['trades']} | "
                f"WR:{stats['win_rate']:.0%} | "
                f"PnL:${stats['total_pnl']:+.2f}")
    with open("/tmp/trading_v6.log", "a") as f:
        f.write(log_line + "\n")
    
    print("\n" + "="*70)
    return state, results


if __name__ == "__main__":
    print("="*70)
    print("MULTI-TIMEFRAME TRADER v6 (FIXED RESOLUTION)")
    print(f"Capital: ${CAPITAL:,.0f} | Risk: {RISK_PER_TRADE*100:.0f}%")
    print("="*70)
    
    state, results = run_cycle()
    
    if state.closed_trades:
        print("\n📋 CLOSED TRADES:")
        for t in state.closed_trades[-5:]:
            window_info = t.get('window_start', '')[:16] if t.get('window_start') else 'Unknown'
            print(f"  {t['timeframe']} | {t['signal']}")
            print(f"    Market: {t.get('market_question', 'Unknown')[:50]}...")
            print(f"    Window: {window_info}")
            print(f"    Entry: {t['entry_time'][:19]} | Price: ${t['entry_price']:.3f}")
            print(f"    BTC: ${t['entry_btc']:,.0f}→${t['exit_btc']:,.0f} | Outcome: {t['outcome']}")
            print(f"    PnL: ${t['pnl']:+.2f} | Cost: ${t['cost']:.2f}")