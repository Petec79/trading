"""
PICO Position Sizing Module

Per-market exposure cap E_m and portfolio-level risk cap.
Kelly-inspired sizing adjusted for spread size and fees.
"""

import math
from dataclasses import dataclass


@dataclass
class SizeResult:
    stake: float           # Final computed stake
    raw_size: float        # Before caps
    kelly_fraction: float # Kelly fraction used
    max_allowed: float    # Per-market cap
    fee_impact: float     # Fee-adjusted size reduction


class SizingEngine:
    """
    PICO Sizing module per Section 3 of the paper.
    
    - Kelly-inspired fraction of bankroll
    - Adjusted for spread size (larger spread = larger edge = bigger size)
    - Cost-aware: reduce for fees
    - Hard cap: E_m * B(t)
    """
    
    def __init__(
        self,
        initial_bankroll: float,
        per_market_cap: float = 0.04,  # E_m from paper (4%)
        risk_fraction: float = 0.03,   # α: worst-case loss fraction (3%)
        max_stake: float = 12.0,       # Hard cap in USDC
        fee_rate: float = 0.01,        # f: per-unit fee
    ):
        self.initial_bankroll = initial_bankroll
        self.per_market_cap = per_market_cap      # E_m
        self.risk_fraction = risk_fraction         # α
        self.max_stake = max_stake                  # Hard cap
        self.fee_rate = fee_rate                   # f
    
    def kelly_fraction(
        self,
        prob_win: float,
        prob_lose: float,
        odds: float,
    ) -> float:
        """
        Full Kelly criterion: f* = (bp - q) / b
        where b = odds ratio, p = prob win, q = prob lose
        
        For binary outcomes:
        - YES: payout = (1-p)/p if yes, lose stake if no
        - NO: payout = p/(1-p) if no, lose stake if yes
        
        We use a fractional Kelly (25%) to be conservative.
        """
        if prob_win <= 0 or prob_lose <= 0:
            return 0.0
        
        # For YES: odds = 1/p - 1 (profit/stake)
        b = (1 - prob_win) / prob_win if prob_win > 0.01 else 0
        
        kelly = (b * prob_win - prob_lose) / b if b > 0 else 0
        
        # Fractional Kelly (25% = half-Kelly squared)
        kelly = kelly * 0.25
        
        return max(0, min(kelly, 0.20))  # Cap at 20% of bankroll
    
    def compute_size(
        self,
        bankroll: float,
        prob: float,
        price: float,
        spread_bps: float,
        depth: float,
        current_portfolio_exposure: float = 0.0,
    ) -> SizeResult:
        """
        Compute position size for a single market.
        
        Args:
            bankroll: Current bankroll B(t)
            prob: Estimated P(yes)
            price: Current price
            spread_bps: Bid-ask spread in bps
            depth: Order book depth
            current_portfolio_exposure: Sum of all current exposures
        
        Returns SizeResult with stake and metadata.
        """
        # Probability inputs
        prob_win = prob
        prob_lose = 1 - prob
        
        # Edge calculation
        # For a YES trade at price p: expected value = p*profit + (1-p)*loss
        # profit = (1-p)/p, loss = -1
        # EV = p * ((1-p)/p) - (1-p) = (1-p) - (1-p) = 0 (fair price)
        # So edge = 1 - |price - 0.50| / 0.50 (deviation from fair)
        edge = 1 - abs(price - 0.50) / 0.50
        
        # Kelly size from edge + probability
        odds = (1 - price) / price if price > 0.01 else 0
        kelly_frac = self.kelly_fraction(prob_win, prob_lose, odds)
        
        # Spread bonus: larger spread = more potential edge capture
        # but also higher transaction costs
        spread_multiplier = min(3.0, max(0.5, spread_bps / 30))
        
        # Depth bonus: deeper book = can size up slightly
        depth_multiplier = min(1.5, max(0.5, math.log1p(depth) / 5))
        
        # Fee drag: fees eat into returns
        fee_multiplier = max(0, 1 - self.fee_rate * 10)
        
        # Raw Kelly-based size
        raw_size = (
            bankroll
            * kelly_frac
            * spread_multiplier
            * depth_multiplier
            * fee_multiplier
        )
        
        # Per-market cap: E_m * B(t)
        per_market_max = bankroll * self.per_market_cap
        
        # Hard cap
        hard_cap = min(self.max_stake, per_market_max)
        
        # Portfolio risk cap check: leave room for diversification
        remaining_risk_budget = bankroll * 0.10 - current_portfolio_exposure
        portfolio_cap = max(0, remaining_risk_budget)
        
        # Final stake: min of all constraints
        stake = min(raw_size, hard_cap, portfolio_cap)
        stake = max(stake, 1.0)  # Minimum $1 stake
        
        return SizeResult(
            stake=round(stake, 2),
            raw_size=round(raw_size, 2),
            kelly_fraction=round(kelly_frac, 4),
            max_allowed=round(hard_cap, 2),
            fee_impact=round(raw_size - stake, 2) if raw_size > stake else 0.0,
        )
    
    def check_portfolio_risk(
        self,
        proposed_stake: float,
        current_exposures: float,
        bankroll: float,
    ) -> tuple:
        """
        Check if proposed stake respects portfolio-level risk limit.
        
        Returns (allowed: bool, reason: str)
        """
        total_exposure = current_exposures + proposed_stake
        max_total = bankroll * 0.10  # 10% max portfolio exposure
        
        if total_exposure > max_total:
            return False, f"portfolio_risk_limit: {total_exposure:.2f} > {max_total:.2f}"
        
        return True, "ok"
