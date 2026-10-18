"""
PICO Entry/Exit Module

Translates action signals F(m,t) {YES, NO, HOLD} into trades.
Implements:
- Stop-loss and take-profit thresholds
- Liquidity-aware slippage constraints
- Cooldown enforcement
- Daily trade cap pacing
"""

from dataclasses import dataclass
from typing import Optional, Dict
from enum import Enum


class TradeDirection(Enum):
    YES = "YES"
    NO = "NO"
    HOLD = "HOLD"


@dataclass
class Position:
    market_id: str
    direction: TradeDirection
    entry_price: float
    stake: float
    entered_at_epoch: int
    unrealized_pnl: float = 0.0


@dataclass
class EntryResult:
    executed: bool
    direction: TradeDirection
    stake: float
    entry_price: float
    reason: str  # Why executed or rejected


@dataclass
class ExitResult:
    executed: bool
    pnl: float
    exit_price: float
    reason: str


class CooldownManager:
    """Manages per-market cooldowns after trades."""

    def __init__(self, min_cooldown_epochs: int = 4, cooldown_decay: float = 0.5):
        self.min_cooldown = min_cooldown_epochs
        self.decay = cooldown_decay
        self.cooldowns: Dict[str, int] = {}  # market_id -> epochs remaining
        self.trade_log: Dict[str, int] = {}  # market_id -> last trade epoch

    def can_enter(self, market_id: str, current_epoch: int) -> bool:
        """Check if market is eligible for entry."""
        if market_id not in self.cooldowns:
            return True
        return self.cooldowns[market_id] <= 0

    def record_trade(self, market_id: str, current_epoch: int) -> None:
        """Record a trade and start cooldown."""
        self.cooldowns[market_id] = self.min_cooldown
        self.trade_log[market_id] = current_epoch

    def tick(self, markets: list = None) -> None:
        """Decay cooldowns by one epoch. If markets provided, only decay those."""
        if markets is None:
            markets = list(self.cooldowns.keys())

        for market_id in markets:
            if market_id in self.cooldowns and self.cooldowns[market_id] > 0:
                self.cooldowns[market_id] -= 1
                if self.cooldowns[market_id] < 0:
                    self.cooldowns[market_id] = 0

    def get_cooldown(self, market_id: str) -> int:
        """Get remaining cooldown epochs."""
        return self.cooldowns.get(market_id, 0)


class EntryExitEngine:
    """
    PICO Entry/Exit module per Section 3 of the paper.

    - Evaluates entry eligibility based on signals + cooldowns + liquidity
    - Implements threshold-based stop-loss and take-profit
    - Enforces daily trade caps
    - Returns EntryResult/ExitResult with full metadata
    """

    def __init__(
        self,
        stop_loss_pct: float = 0.02,      # θ_loss: stop-loss fraction (2%)
        take_profit_pct: float = 0.06,    # θ_profit: take-profit multiple (6%)
        max_daily_trades: int = 2,        # T_max: daily entry cap
        cooldown_manager: CooldownManager = None,
    ):
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_daily_trades = max_daily_trades
        self.cooldown_mgr = cooldown_manager or CooldownManager()

        # Daily trade tracking
        self.daily_trades: Dict[int, int] = {}  # day -> count

        # Current positions
        self.positions: Dict[str, Position] = {}

    def evaluate_entry(
        self,
        market_id: str,
        signal: TradeDirection,
        price: float,
        liquidity_score: float,
        min_liquidity: float,
        current_epoch: int,
    ) -> EntryResult:
        """
        Evaluate whether to enter a trade.

        Args:
            market_id: Market identifier
            signal: YES/NO/HOLD signal from predictor
            price: Current price
            liquidity_score: L_m(t) from market selector
            min_liquidity: Minimum L_m threshold
            current_epoch: Current decision epoch

        Returns EntryResult with execution details.
        """
        # Reject HOLD signals
        if signal == TradeDirection.HOLD:
            return EntryResult(
                executed=False,
                direction=TradeDirection.HOLD,
                stake=0,
                entry_price=0,
                reason="no_signal",
            )

        # Check liquidity
        if liquidity_score < min_liquidity:
            return EntryResult(
                executed=False,
                direction=signal,
                stake=0,
                entry_price=price,
                reason=f"liquidity_too_low:{liquidity_score:.3f}<{min_liquidity}",
            )

        # Check cooldown
        if not self.cooldown_mgr.can_enter(market_id, current_epoch):
            cooldown = self.cooldown_mgr.get_cooldown(market_id)
            return EntryResult(
                executed=False,
                direction=signal,
                stake=0,
                entry_price=price,
                reason=f"cooldown_active:{cooldown}_epochs",
            )

        # Check daily trade cap
        day = current_epoch // 24  # rough day approximation
        if self.daily_trades.get(day, 0) >= self.max_daily_trades:
            return EntryResult(
                executed=False,
                direction=signal,
                stake=0,
                entry_price=price,
                reason=f"daily_cap_reached:{self.max_daily_trades}",
            )

        # Entry allowed
        return EntryResult(
            executed=True,
            direction=signal,
            stake=0,  # Size computed by SizingEngine
            entry_price=price,
            reason="entry_allowed",
        )

    def execute_entry(
        self,
        market_id: str,
        direction: TradeDirection,
        price: float,
        stake: float,
        current_epoch: int,
    ) -> Position:
        """Record an entry trade and update cooldown/daily cap."""
        position = Position(
            market_id=market_id,
            direction=direction,
            entry_price=price,
            stake=stake,
            entered_at_epoch=current_epoch,
        )
        self.positions[market_id] = position

        # Update cooldown
        self.cooldown_mgr.record_trade(market_id, current_epoch)

        # Update daily trades
        day = current_epoch // 24
        self.daily_trades[day] = self.daily_trades.get(day, 0) + 1

        return position

    def evaluate_exit(
        self,
        market_id: str,
        current_price: float,
        fees: float = 0.01,
    ) -> ExitResult:
        """
        Evaluate whether to exit a position based on stop-loss/take-profit.

        Args:
            market_id: Market to check
            current_price: Current market price
            fees: Fee rate per unit

        Returns ExitResult with PnL if exited.
        """
        if market_id not in self.positions:
            return ExitResult(executed=False, pnl=0, exit_price=0, reason="no_position")

        pos = self.positions[market_id]

        # Calculate unrealized PnL
        if pos.direction == TradeDirection.YES:
            # PnL = stake * (1 - price/entry_price) if YES
            # Actually: cost = entry_price * stake, payout = stake if YES, so:
            # For YES at price p: cost = p*stake, payout = stake if YES → profit = stake - p*stake
            # But we paid entry_price, so profit = stake*(1 - entry_price) - fees
            pnl = pos.stake * (1 - pos.entry_price) - (fees * pos.stake * 2)
        else:
            # For NO: cost = (1-entry_price)*stake, payout = stake if NO
            pnl = pos.stake * pos.entry_price - (fees * pos.stake * 2)

        pos.unrealized_pnl = pnl

        # Check stop-loss (loss threshold is fraction of stake, stored as negative)
        stop_trigger = -pos.stake * self.stop_loss_pct
        if pnl <= stop_trigger:
            return ExitResult(
                executed=True,
                pnl=pnl,
                exit_price=current_price,
                reason=f"stop_loss:{pnl:.2f}<={stop_trigger:.2f}",
            )

        # Check take-profit
        profit_trigger = pos.stake * self.take_profit_pct
        if pnl >= profit_trigger:
            return ExitResult(
                executed=True,
                pnl=pnl,
                exit_price=current_price,
                reason=f"take_profit:{pnl:.2f}>={profit_trigger:.2f}",
            )

        return ExitResult(
            executed=False,
            pnl=pnl,
            exit_price=current_price,
            reason="thresholds_not_breached",
        )

    def force_close(
        self,
        market_id: str,
        current_price: float,
        reason: str = "manual",
    ) -> ExitResult:
        """Force close a position (e.g., event resolved)."""
        if market_id not in self.positions:
            return ExitResult(executed=False, pnl=0, exit_price=0, reason="no_position")

        pos = self.positions[market_id]
        pnl = self._compute_pnl(pos, current_price)

        del self.positions[market_id]

        return ExitResult(
            executed=True,
            pnl=pnl,
            exit_price=current_price,
            reason=reason,
        )

    def _compute_pnl(self, pos: Position, current_price: float) -> float:
        """Compute realized PnL for a position."""
        if pos.direction == TradeDirection.YES:
            pnl = pos.stake * (1 - pos.entry_price)
        else:
            pnl = pos.stake * pos.entry_price
        return pnl

    def get_position(self, market_id: str) -> Optional[Position]:
        """Get current position for a market."""
        return self.positions.get(market_id)

    def get_all_positions(self) -> Dict[str, Position]:
        """Get all current positions."""
        return self.positions.copy()

    def tick_cooldowns(self) -> None:
        """Decay all cooldowns by one epoch."""
        self.cooldown_mgr.tick()
