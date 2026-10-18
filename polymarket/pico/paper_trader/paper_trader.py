"""
PICO Framework — Paper Trading Engine
Realistic cost modeling for paper trading and backtesting.
Models fees, slippage, and settlement delays per PICO paper.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path


@dataclass
class PaperTrade:
    """A single paper trade."""
    trade_id: str
    condition_id: str
    question: str
    side: str           # 'YES' or 'NO'
    entry_price: float
    exit_price: Optional[float]
    size: float         # dollar amount
    entry_time: datetime
    exit_time: Optional[datetime]
    pnl: Optional[float]
    outcome: str         # 'pending', 'win', 'loss', 'resolved'
    ev_at_entry: float
    fees: float         # total fees paid
    slippage: float     # slippage cost
    settlement_delay: float  # hours to settlement


@dataclass
class CostModel:
    """
    Cost model components per PICO paper Section 3.
    - f: per-unit trading fee
    - s_m(t): slippage proxy (proportional to liquidity)
    - g_m(t): settlement delay (hours)
    """
    fee: float = 0.01           # 1% per unit (typical Polymarket)
    slippage_base: float = 0.005 # 0.5% base slippage
    settlement_hours: float = 1.0  # 1 hour settlement delay


@dataclass
class Portfolio:
    """Portfolio state for paper trading."""
    bankroll: float
    initial_bankroll: float
    positions: dict[str, float]  # condition_id -> size
    trades: list[PaperTrade]
    daily_trades: int = 0
    daily_reset: datetime = field(default_factory=datetime.now)
    
    @property
    def total_exposure(self) -> float:
        return sum(abs(v) for v in self.positions.values())
    
    @property
    def unrealized_pnl(self) -> float:
        return 0.0  # Computed per-market with current prices


@dataclass
class TradeResult:
    """Result of a simulated trade."""
    success: bool
    executed_price: float
    slippage_cost: float
    fee_cost: float
    message: str


class PaperTrader:
    """
    Paper trading engine with realistic cost modeling.
    
    Implements the PICO decision loop:
    1. Compute liquidity features
    2. Derive predictor signal
    3. Evaluate eligibility (cooldowns, exposure caps)
    4. Market selection (top-K by liquidity-adjusted score)
    5. Compute stake (risk budget + sizing)
    6. Execute trade with costs
    7. Record PnL at resolution
    """
    
    def __init__(
        self,
        initial_bankroll: float = 300.0,
        costs: Optional[CostModel] = None
    ):
        self.initial_bankroll = initial_bankroll
        self.bankroll = initial_bankroll
        self.costs = costs or CostModel()
        self.positions: dict[str, float] = {}  # condition_id -> size
        self.trades: list[PaperTrade] = []
        self.daily_trades = 0
        self.daily_reset = datetime.now(timezone.utc)
        self.trade_counter = 0
    
    def reset_daily_trades(self):
        """Reset daily trade counter if new day."""
        now = datetime.now(timezone.utc)
        if (now - self.daily_reset).days >= 1:
            self.daily_trades = 0
            self.daily_reset = now
    
    def compute_slippage(
        self,
        side: str,
        price: float,
        size: float,
        liquidity_score: float
    ) -> float:
        """
        Compute slippage cost.
        s_m(t) proportional to 1/liquidity.
        Thinner markets = more slippage.
        """
        # Slippage inversely proportional to liquidity score
        if liquidity_score > 0:
            slippage_factor = (1.0 - liquidity_score) / liquidity_score
        else:
            slippage_factor = 2.0
        
        base_slip = self.costs.slippage_base * slippage_factor
        
        # Slippage per unit times size
        slippage_cost = base_slip * size
        
        # Cap at reasonable fraction of size
        return min(slippage_cost, size * 0.05)  # max 5% slippage
    
    def compute_fees(self, price: float, size: float) -> float:
        """
        Compute fee cost.
        f per unit (includes trading fee).
        """
        # Fee on the position value
        fee_cost = self.costs.fee * price * size
        
        # Plus flat fee component
        flat_fee = 0.01  # $0.01 minimum
        
        return fee_cost + flat_fee
    
    def can_trade(
        self,
        condition_id: str,
        size: float,
        max_daily_trades: int = 2,
        max_daily_pct: float = 0.10
    ) -> tuple[bool, str]:
        """
        Check if we can place a new trade.
        Returns (can_trade, reason).
        """
        self.reset_daily_trades()
        
        # Check daily trade limit
        if self.daily_trades >= max_daily_trades:
            return False, f"daily trade limit reached: {self.daily_trades}/{max_daily_trades}"
        
        # Check daily spend limit (% of bankroll)
        max_daily_spend = self.bankroll * max_daily_pct
        if size > max_daily_spend:
            return False, f"size ${size:.2f} exceeds daily limit ${max_daily_spend:.2f}"
        
        # Check bankroll
        if self.bankroll <= 0:
            return False, "bankroll depleted"
        
        return True, "ok"
    
    def execute_trade(
        self,
        condition_id: str,
        question: str,
        side: str,
        price: float,
        size: float,
        liquidity_score: float = 0.5,
        ev: float = 0.0
    ) -> TradeResult:
        """
        Execute a paper trade with cost modeling.
        Returns TradeResult with execution details.
        """
        can_trade, reason = self.can_trade(condition_id, size)
        if not can_trade:
            return TradeResult(False, 0, 0, 0, reason)
        
        # Compute costs
        slippage = self.compute_slippage(side, price, size, liquidity_score)
        fees = self.compute_fees(price, size)
        
        # Executed price (accounting for slippage)
        if side == 'YES':
            executed_price = price + slippage / size  # worse for buyer
        else:
            executed_price = price - slippage / size  # worse for seller
        
        executed_price = max(0.01, min(0.99, executed_price))
        
        # Update bankroll
        cost = size + fees + slippage
        if self.bankroll < cost:
            return TradeResult(False, 0, 0, 0, f"insufficient bankroll: ${self.bankroll:.2f} < ${cost:.2f}")
        
        self.bankroll -= cost
        
        # Record position
        current_size = self.positions.get(condition_id, 0)
        if side == 'YES':
            self.positions[condition_id] = current_size + size
        else:
            self.positions[condition_id] = current_size - size
        
        # Record trade
        self.trade_counter += 1
        trade = PaperTrade(
            trade_id=f"pt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{condition_id[:16]}",
            condition_id=condition_id,
            question=question,
            side=side,
            entry_price=executed_price,
            exit_price=None,
            size=size,
            entry_time=datetime.now(timezone.utc),
            exit_time=None,
            pnl=None,
            outcome='pending',
            ev_at_entry=ev,
            fees=fees,
            slippage=slippage,
            settlement_delay=self.costs.settlement_hours
        )
        self.trades.append(trade)
        self.daily_trades += 1
        
        return TradeResult(
            success=True,
            executed_price=executed_price,
            slippage_cost=slippage,
            fee_cost=fees,
            message="ok"
        )
    
    def resolve_trade(
        self,
        condition_id: str,
        outcome: bool,  # True = YES won
        settlement_price: float = 1.0
    ) -> Optional[PaperTrade]:
        """Resolve a pending trade and update bankroll."""
        # Find pending trade for this condition
        for trade in reversed(self.trades):
            if trade.condition_id == condition_id and trade.outcome == 'pending':
                # Compute PnL
                if trade.side == 'YES':
                    if outcome:
                        trade.pnl = trade.size * settlement_price - trade.size - trade.fees - trade.slippage
                        trade.outcome = 'win'
                    else:
                        trade.pnl = -trade.size - trade.fees - trade.slippage
                        trade.outcome = 'loss'
                else:  # NO
                    if not outcome:
                        trade.pnl = trade.size * settlement_price - trade.size - trade.fees - trade.slippage
                        trade.outcome = 'win'
                    else:
                        trade.pnl = -trade.size - trade.fees - trade.slippage
                        trade.outcome = 'loss'
                
                trade.exit_price = settlement_price
                trade.exit_time = datetime.now(timezone.utc)
                
                # Update bankroll with PnL
                self.bankroll += trade.size + trade.pnl
                
                # Clear position
                if condition_id in self.positions:
                    self.positions[condition_id] = 0
                
                return trade
        
        return None
    
    def get_stats(self) -> dict:
        """Compute performance statistics."""
        resolved = [t for t in self.trades if t.outcome in ('win', 'loss')]
        
        if not resolved:
            return {
                'bankroll': self.bankroll,
                'total_trades': len(self.trades),
                'resolved_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0
            }
        
        wins = sum(1 for t in resolved if t.outcome == 'win')
        losses = sum(1 for t in resolved if t.outcome == 'loss')
        
        total_pnl = sum(t.pnl for t in resolved if t.pnl is not None)
        
        return {
            'bankroll': self.bankroll,
            'initial_bankroll': self.initial_bankroll,
            'total_trades': len(self.trades),
            'resolved_trades': len(resolved),
            'wins': wins,
            'losses': losses,
            'win_rate': wins / len(resolved) if resolved else 0,
            'total_pnl': total_pnl,
            'avg_pnl': total_pnl / len(resolved) if resolved else 0,
            'daily_trades': self.daily_trades
        }
    
    def save_trades(self, path: Path):
        """Save trades to JSONL file."""
        with open(path, 'w') as f:
            for trade in self.trades:
                d = {
                    'trade_id': trade.trade_id,
                    'condition_id': trade.condition_id,
                    'question': trade.question,
                    'side': trade.side,
                    'entry_price': trade.entry_price,
                    'exit_price': trade.exit_price,
                    'size': trade.size,
                    'entry_time': trade.entry_time.isoformat(),
                    'exit_time': trade.exit_time.isoformat() if trade.exit_time else None,
                    'pnl': trade.pnl,
                    'outcome': trade.outcome,
                    'ev_at_entry': trade.ev_at_entry,
                    'fees': trade.fees,
                    'slippage': trade.slippage,
                    'settlement_delay': trade.settlement_delay
                }
                f.write(json.dumps(d) + '\n')
