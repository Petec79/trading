"""
PICO Framework — Cooldown Module
Adaptive cooldowns driven by liquidity indicators.
Implements two-layer predictor from PICO paper.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CooldownState:
    """Per-market cooldown state."""
    condition_id: str
    cooldown_remaining: float    # seconds remaining
    last_trade_time: float     # unix timestamp
    liquidity_regime: str      # 'easy' or 'hard'
    cooldown_scalar: float     # c_m(t) [0, C_MAX]
    
    @property
    def is_eligible(self) -> bool:
        return self.cooldown_remaining <= 0


# Default hyperparameters from PICO paper
DEFAULT_COOLDOWN_MIN = 300        # 5 minutes minimum between trades
DEFAULT_COOLDOWN_MAX = 3600      # 1 hour max in stressed regime
C_MAX = 3600                      # Maximum cooldown scalar


@dataclass
class CooldownConfig:
    """Cooldown hyperparameters."""
    cooldown_min: float = DEFAULT_COOLDOWN_MIN  # seconds
    cooldown_max: float = DEFAULT_COOLDOWN_MAX
    c_max: float = C_MAX
    liquidity_threshold_easy: float = 0.6  # L(m) above this = easy regime
    liquidity_threshold_hard: float = 0.3  # L(m) below this = hard regime


class CooldownManager:
    """
    Manages adaptive cooldowns per PICO paper.
    
    Two-layer predictor:
    - Layer 1: liquidity score L(m,t)
    - Layer 2: cooldown scalar c_m(t) [0, C_MAX]
    
    A market is eligible only if:
    - c_m(t) <= cooldown_min
    - risk budget allows the tentative stake
    """
    
    def __init__(self, config: Optional[CooldownConfig] = None):
        self.config = config or CooldownConfig()
        self._states: dict[str, CooldownState] = {}
    
    def get_state(self, condition_id: str) -> CooldownState:
        """Get or create cooldown state for a market."""
        if condition_id not in self._states:
            self._states[condition_id] = CooldownState(
                condition_id=condition_id,
                cooldown_remaining=0,
                last_trade_time=0,
                liquidity_regime='easy',
                cooldown_scalar=0
            )
        return self._states[condition_id]
    
    def update_liquidity_regime(self, condition_id: str, liquidity_score: float):
        """Update liquidity regime based on market selection score."""
        state = self.get_state(condition_id)
        
        if liquidity_score >= self.config.liquidity_threshold_easy:
            state.liquidity_regime = 'easy'
        elif liquidity_score <= self.config.liquidity_threshold_hard:
            state.liquidity_regime = 'hard'
        else:
            state.liquidity_regime = 'medium'
    
    def compute_cooldown_scalar(
        self,
        condition_id: str,
        liquidity_score: float,
        volatility: float = 0.0
    ) -> float:
        """
        Compute adaptive cooldown scalar c_m(t).
        Higher liquidity = lower cooldown (faster recovery).
        High volatility = higher cooldown (slower recovery).
        """
        state = self.get_state(condition_id)
        
        # Base cooldown inversely proportional to liquidity
        if liquidity_score > 0:
            base_cooldown = self.config.cooldown_min / liquidity_score
        else:
            base_cooldown = self.config.cooldown_max
        
        # Add volatility adjustment (0-50% increase)
        volatility_factor = 1.0 + min(volatility * 5, 0.5)
        
        scalar = base_cooldown * volatility_factor
        
        # Hard cap at C_MAX
        return min(scalar, self.config.c_max)
    
    def record_trade(self, condition_id: str, liquidity_score: float = 0.5, volatility: float = 0.0):
        """Record a completed trade and set cooldown."""
        state = self.get_state(condition_id)
        state.last_trade_time = time.time()
        
        # Update cooldown scalar based on current conditions
        state.cooldown_scalar = self.compute_cooldown_scalar(
            condition_id, liquidity_score, volatility
        )
        
        # Set cooldown as minimum of scalar and max
        state.cooldown_remaining = min(
            state.cooldown_scalar,
            self.config.cooldown_max
        )
    
    def tick(self, condition_id: str, dt: float = 1.0) -> float:
        """
        Advance time for a market. Returns remaining cooldown.
        dt: time step in seconds
        """
        state = self.get_state(condition_id)
        
        if state.cooldown_remaining > 0:
            state.cooldown_remaining = max(0, state.cooldown_remaining - dt)
        
        return state.cooldown_remaining
    
    def is_eligible(self, condition_id: str) -> bool:
        """Check if market is eligible for new trade."""
        state = self.get_state(condition_id)
        return state.is_eligible and state.cooldown_remaining <= self.config.cooldown_min
    
    def get_wait_time(self, condition_id: str) -> float:
        """Get seconds until eligible."""
        state = self.get_state(condition_id)
        return max(0, state.cooldown_remaining)
    
    def check_and_record(
        self,
        condition_id: str,
        liquidity_score: float,
        volatility: float = 0.0
    ) -> tuple[bool, float]:
        """
        Check eligibility and record trade if eligible.
        Returns (eligible, wait_time).
        """
        state = self.get_state(condition_id)
        
        if not state.is_eligible:
            return False, state.cooldown_remaining
        
        # Record trade, triggering new cooldown
        self.record_trade(condition_id, liquidity_score, volatility)
        return True, 0.0
