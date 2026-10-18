"""
Paper Trading Engine for Polymarket Market Making Bot

Simulates market making on Polymarket without real trades.
Tracks paper positions, P&L, and trade history.
"""

import json
import time
import asyncio
import pandas as pd
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List
from sortedcontainers import SortedDict
import requests

# ============================================================================
# Configuration
# ============================================================================

PAPER_MODE = True  # Always True for this module

# Market making parameters
DEFAULT_SPREAD_WIDTH = 0.02  # 2% spread
MIN_ORDER_SIZE = 10
MAX_POSITION_SIZE = 500
ORDER_REFRESH_INTERVAL = 5  # seconds

# Logging
TRADE_LOG_FILE = "paper_trades.csv"
POSITION_LOG_FILE = "paper_positions.json"

# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class PaperOrder:
    token: str
    side: str  # 'buy' or 'sell'
    price: float
    size: float
    timestamp: float
    market: str
    order_id: str

@dataclass
class PaperTrade:
    timestamp: float
    token: str
    side: str
    price: float
    size: float
    market: str
    mid_price: float
    spread: float
    realized: bool  # True if we got filled

@dataclass
class PaperPosition:
    token: str
    size: float  # positive = long, negative = short
    avg_price: float
    market: str

# ============================================================================
# Paper Trading State
# ============================================================================

class PaperTradingEngine:
    def __init__(self, initial_balance: float = 10000.0):
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.positions: Dict[str, PaperPosition] = {}
        self.orders: Dict[str, PaperOrder] = {}  # token -> active order
        self.trade_history: List[PaperTrade] = []
        self.market_prices: Dict[str, Dict] = {}  # market -> {mid_price, best_bid, best_ask}
        self.spread_width = DEFAULT_SPREAD_WIDTH
        self.total_trades = 0
        self.filled_trades = 0
        
    def set_spread(self, width: float):
        """Set the bid-ask spread width as a fraction (e.g., 0.02 for 2%)"""
        self.spread_width = width
        
    def update_market_price(self, market: str, best_bid: float, best_ask: float):
        """Update the current market prices"""
        mid_price = (best_bid + best_ask) / 2
        self.market_prices[market] = {
            'mid_price': mid_price,
            'best_bid': best_bid,
            'best_ask': best_ask,
            'spread': best_ask - best_bid
        }
        
    def calculate_paper_orders(self, market: str, token: str, size: float) -> tuple:
        """
        Calculate paper buy and sell orders given current market prices.
        Returns (buy_price, sell_price) or (None, None) if insufficient liquidity
        """
        if market not in self.market_prices:
            return None, None
            
        data = self.market_prices[market]
        mid = data['mid_price']
        spread = self.spread_width * mid
        
        # Sell slightly above mid, buy slightly below mid
        sell_price = round(mid + spread/2, 4)
        buy_price = round(mid - spread/2, 4)
        
        # Validate prices are reasonable (between 0.01 and 0.99)
        if buy_price < 0.01 or sell_price > 0.99:
            return None, None
            
        return buy_price, sell_price
        
    def simulate_fill(self, market: str, token: str, side: str, price: float, size: float) -> bool:
        """
        Simulate whether a paper order gets filled based on market liquidity.
        Returns True if filled.
        """
        if market not in self.market_prices:
            return False
            
        data = self.market_prices[market]
        
        if side == 'buy':
            # We get filled if market price moved up to our buy price
            # In paper trading, we simulate fills probabilistically based on position in book
            market_ask = data['best_ask']
            fill_probability = 0.7 if price >= market_ask else 0.3
        else:
            # We get filled if market price moved down to our sell price
            market_bid = data['best_bid']
            fill_probability = 0.7 if price <= market_bid else 0.3
            
        import random
        return random.random() < fill_probability
        
    def place_paper_order(self, token: str, side: str, price: float, 
                         size: float, market: str) -> PaperOrder:
        """Place a paper order"""
        order = PaperOrder(
            token=token,
            side=side,
            price=price,
            size=size,
            timestamp=time.time(),
            market=market,
            order_id=f"PAPER_{int(time.time()*1000)}"
        )
        self.orders[token] = order
        self.total_trades += 1
        return order
        
    def execute_paper_trade(self, token: str, side: str, price: float, 
                            size: float, market: str) -> Optional[PaperTrade]:
        """Execute a paper trade (simulated fill)"""
        if market not in self.market_prices:
            return None
            
        data = self.market_prices[market]
        mid_price = data['mid_price']
        spread = data['spread']
        
        trade = PaperTrade(
            timestamp=time.time(),
            token=token,
            side=side,
            price=price,
            size=size,
            market=market,
            mid_price=mid_price,
            spread=spread,
            realized=False
        )
        
        # Simulate fill
        if self.simulate_fill(market, token, side, price, size):
            trade.realized = True
            self.filled_trades += 1
            self._update_position(trade)
            
        self.trade_history.append(trade)
        self._save_trade(trade)
        
        return trade
        
    def _update_position(self, trade: PaperTrade):
        """Update paper position after a filled trade"""
        pos_key = trade.token
        
        if pos_key not in self.positions:
            self.positions[pos_key] = PaperPosition(
                token=trade.token,
                size=0,
                avg_price=0,
                market=trade.market
            )
            
        pos = self.positions[pos_key]
        
        if trade.side == 'buy':
            # Buying increases position (or decreases short)
            cost = trade.price * trade.size
            self.balance -= cost
            
            if pos.size < 0:
                # Closing a short position
                if abs(pos.size) <= trade.size:
                    # Fully close short
                    profit = (pos.avg_price - trade.price) * abs(pos.size)
                    self.balance += abs(pos.size) * pos.avg_price + profit
                    pos.size = 0
                    pos.avg_price = 0
                else:
                    # Partially close short
                    profit = (pos.avg_price - trade.price) * trade.size
                    self.balance += trade.size * pos.avg_price + profit
                    pos.size += trade.size
            else:
                # Adding to long position
                total_cost = pos.avg_price * pos.size + trade.price * trade.size
                pos.size += trade.size
                pos.avg_price = total_cost / pos.size if pos.size > 0 else 0
        else:
            # Selling increases short (or decreases long)
            revenue = trade.price * trade.size
            self.balance += revenue
            
            if pos.size > 0:
                # Closing a long position
                if pos.size <= trade.size:
                    # Fully close long
                    profit = (trade.price - pos.avg_price) * pos.size
                    self.balance += pos.size * trade.price - profit
                    pos.size = 0
                    pos.avg_price = 0
                else:
                    # Partially close long
                    profit = (trade.price - pos.avg_price) * trade.size
                    self.balance += trade.size * trade.price - profit
                    pos.size -= trade.size
            else:
                # Adding to short position
                total_revenue = pos.avg_price * abs(pos.size) + trade.price * trade.size
                pos.size -= trade.size
                pos.avg_price = total_revenue / abs(pos.size) if pos.size != 0 else 0
                
    def get_unrealized_pnl(self) -> float:
        """Calculate unrealized P&L from current positions"""
        pnl = 0
        for token, pos in self.positions.items():
            if pos.size != 0 and pos.market in self.market_prices:
                data = self.market_prices[pos.market]
                current_price = data['mid_price']
                
                if pos.size > 0:
                    pnl += (current_price - pos.avg_price) * pos.size
                else:
                    pnl += (pos.avg_price - current_price) * abs(pos.size)
        return pnl
        
    def get_total_pnl(self) -> float:
        """Get total P&L (realized + unrealized)"""
        realized = self.balance - self.initial_balance
        unrealized = self.get_unrealized_pnl()
        return realized + unrealized
        
    def get_stats(self) -> dict:
        """Get trading statistics"""
        return {
            'balance': self.balance,
            'initial_balance': self.initial_balance,
            'realized_pnl': self.balance - self.initial_balance,
            'unrealized_pnl': self.get_unrealized_pnl(),
            'total_pnl': self.get_total_pnl(),
            'total_trades': self.total_trades,
            'filled_trades': self.filled_trades,
            'fill_rate': self.filled_trades / max(self.total_trades, 1),
            'open_positions': len([p for p in self.positions.values() if p.size != 0]),
            'spread_width': self.spread_width
        }
        
    def print_status(self):
        """Print current trading status"""
        stats = self.get_stats()
        print("\n" + "="*60)
        print("PAPER TRADING STATUS")
        print("="*60)
        print(f"Balance:        ${stats['balance']:.2f}")
        print(f"Initial:        ${stats['initial_balance']:.2f}")
        print(f"Realized P&L:   ${stats['realized_pnl']:.2f}")
        print(f"Unrealized P&L: ${stats['unrealized_pnl']:.2f}")
        print(f"Total P&L:      ${stats['total_pnl']:.2f}")
        print("-"*60)
        print(f"Total Orders:  {stats['total_trades']}")
        print(f"Filled Orders: {stats['filled_trades']}")
        print(f"Fill Rate:     {stats['fill_rate']*100:.1f}%")
        print(f"Spread Width:  {stats['spread_width']*100:.2f}%")
        print("-"*60)
        print("Open Positions:")
        for token, pos in self.positions.items():
            if pos.size != 0:
                market_data = self.market_prices.get(pos.market, {})
                current = market_data.get('mid_price', 0)
                if pos.size > 0:
                    unrealized = (current - pos.avg_price) * pos.size
                else:
                    unrealized = (pos.avg_price - current) * abs(pos.size)
                print(f"  {token[:20]:20s} {'LONG' if pos.size > 0 else 'SHORT':5s} {abs(pos.size):6.1f} @ {pos.avg_price:.4f} (current: {current:.4f}) unrealized: ${unrealized:.2f}")
        print("="*60 + "\n")
        
    def _save_trade(self, trade: PaperTrade):
        """Append trade to CSV log"""
        import csv
        from pathlib import Path
        
        log_path = Path(TRADE_LOG_FILE)
        file_exists = log_path.exists()
        
        with open(log_path, 'a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['timestamp', 'datetime', 'token', 'side', 'price', 'size', 
                              'market', 'mid_price', 'spread', 'realized'])
            writer.writerow([
                trade.timestamp,
                datetime.fromtimestamp(trade.timestamp).isoformat(),
                trade.token,
                trade.side,
                trade.price,
                trade.size,
                trade.market,
                trade.mid_price,
                trade.spread,
                trade.realized
            ])
            
    def save_state(self):
        """Save current positions to JSON"""
        positions_data = {
            token: asdict(pos) for token, pos in self.positions.items()
        }
        state = {
            'timestamp': time.time(),
            'balance': self.balance,
            'positions': positions_data,
            'total_trades': self.total_trades,
            'filled_trades': self.filled_trades
        }
        with open(POSITION_LOG_FILE, 'w') as f:
            json.dump(state, f, indent=2)
            
    def load_state(self) -> bool:
        """Load positions from JSON if exists"""
        try:
            with open(POSITION_LOG_FILE, 'r') as f:
                state = json.load(f)
            self.balance = state['balance']
            self.positions = {
                token: PaperPosition(**pos) for token, pos in state['positions'].items()
            }
            self.total_trades = state['total_trades']
            self.filled_trades = state['filled_trades']
            return True
        except:
            return False


# ============================================================================
# Global paper trading engine instance
# ============================================================================

paper_engine = PaperTradingEngine()


# ============================================================================
# Market Data Fetching (no auth required)
# ============================================================================

def fetch_markets(limit: int = 10) -> list:
    """Fetch active markets from Polymarket"""
    try:
        resp = requests.get(
            "https://clob.polymarket.com/markets",
            params={"closed": "false", "limit": limit},
            timeout=10
        )
        if resp.ok:
            return resp.json().get('data', [])
    except Exception as e:
        print(f"Error fetching markets: {e}")
    return []


def fetch_order_book(token_id: str) -> dict:
    """Fetch order book for a specific token"""
    try:
        resp = requests.get(
            f"https://clob.polymarket.com/orderbook",
            params={"market": token_id},
            timeout=10
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"Error fetching order book for {token_id}: {e}")
    return {'bids': [], 'asks': []}


def get_token_prices(market: dict) -> tuple:
    """Extract YES/NO token prices from market data"""
    yes_price = float(market.get('yes_price', 0))
    no_price = float(market.get('no_price', 0))
    return yes_price, no_price


# ============================================================================
# Main Paper Trading Loop
# ============================================================================

async def paper_trade_loop(markets: list, update_interval: int = 5):
    """
    Main paper trading loop.
    
    For each market:
    1. Fetch order book
    2. Calculate bid/ask prices
    3. Place paper orders on both sides
    4. Simulate fills
    5. Track positions and P&L
    """
    print(f"Starting paper trading loop for {len(markets)} markets")
    print(f"Update interval: {update_interval} seconds")
    print(f"Spread width: {paper_engine.spread_width*100:.2f}%")
    
    iteration = 0
    
    while True:
        iteration += 1
        print(f"\n--- Iteration {iteration} ---")
        
        for market in markets:
            question = market.get('question', 'Unknown')[:60]
            condition_id = market.get('condition_id', '')
            
            if not condition_id:
                continue
                
            # Get YES token info
            yes_token = market.get('id', '')
            yes_price, no_price = get_token_prices(market)
            
            if yes_price <= 0 or no_price <= 0:
                continue
                
            # Update market prices
            paper_engine.update_market_price(
                condition_id, 
                best_bid=1-no_price,  # NO price is effectively the bid for the other side
                best_ask=yes_price
            )
            
            # Calculate paper orders
            buy_price, sell_price = paper_engine.calculate_paper_orders(
                condition_id, yes_token, size=MIN_ORDER_SIZE
            )
            
            if buy_price and sell_price:
                # Place paper orders on both sides
                print(f"  {question}: YES={yes_price:.4f}, NO={no_price:.4f}")
                print(f"    Placing BUY @ {buy_price:.4f}, SELL @ {sell_price:.4f}")
                
                # Simulate executing the orders
                trade1 = paper_engine.execute_paper_trade(
                    yes_token, 'buy', buy_price, MIN_ORDER_SIZE, condition_id
                )
                if trade1 and trade1.realized:
                    print(f"    [FILLED] BUY {MIN_ORDER_SIZE} @ {buy_price:.4f}")
                    
                trade2 = paper_engine.execute_paper_trade(
                    yes_token, 'sell', sell_price, MIN_ORDER_SIZE, condition_id
                )
                if trade2 and trade2.realized:
                    print(f"    [FILLED] SELL {MIN_ORDER_SIZE} @ {sell_price:.4f}")
        
        # Print status every 10 iterations
        if iteration % 10 == 0:
            paper_engine.print_status()
            paper_engine.save_state()
            
        await asyncio.sleep(update_interval)


if __name__ == "__main__":
    print("Fetching markets...")
    markets = fetch_markets(limit=10)
    print(f"Found {len(markets)} active markets")
    
    for m in markets[:5]:
        print(f"  - {m.get('question', 'N/A')[:60]}")
        print(f"    YES: {m.get('yes_price')}, NO: {m.get('no_price')}")
        print(f"    Volume: ${float(m.get('volume', 0)):,.0f}")
    
    print("\nStarting paper trading simulation...")
    asyncio.run(paper_trade_loop(markets))
