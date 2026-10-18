"""
PICO Framework — Position Sizing Module
Calculates Kelly-based position sizes with conservative caps.
"""

import math
from dataclasses import dataclass


@dataclass
class SizingParams:
    """Sizing hyperparameters per PICO paper Table 1."""
    per_market_cap: float       # E_m: fraction of bankroll per market (0.02-0.04)
    max_trade_size: float       # hard cap to prevent over-concentration
    max_daily_trades: int       # T_max: daily entry cap
    max_loss_per_trade: float   # fraction of bankroll for worst-case one-step loss


def quarter_kelly(win_rate: float, odds: float) -> float:
    """
    Quarter Kelly position sizing.
    f* = (p * b - q) / b  [full Kelly]
    quarter_kelly = f* / 4
    Capped at 0.25 (25% of bankroll).
    """
    p = win_rate
    q = 1 - p
    b = odds - 1  # convert decimal to Kelly b
    
    if b <= 0:
        return 0.0
    
    full_kelly = (p * b - q) / b
    
    if full_kelly <= 0:
        return 0.0
    
    return min(full_kelly / 4.0, 0.25)


def calculate_stake(
    bankroll: float,
    price: float,
    sizing: SizingParams,
    ev: float = 0.0
) -> float:
    """
    Calculate position size respecting risk limits.
    
    Args:
        bankroll: total capital available
        price: current market price (0-1)
        sizing: SizingParams with risk limits
        ev: expected value (for Kelly calculation)
    
    Returns:
        Dollar stake for this trade
    """
    # Per-market exposure cap
    max_stake_per_market = sizing.per_market_cap * bankroll
    
    # Hard cap
    max_stake = min(max_stake_per_market, sizing.max_trade_size)
    
    if max_stake <= 0:
        return 0.0
    
    # Use Kelly if we have positive EV
    if ev > 0.05:  # minimum 5% EV threshold
        # Assume even odds (binary market) for simplicity
        kelly_fraction = min(ev * 2, 0.25)  # rough heuristic: EV*2 approximates Kelly
        stake = kelly_fraction * bankroll
    else:
        # Conservative default: fixed fraction of per-market cap
        stake = max_stake * 0.5
    
    return round(min(stake, max_stake), 2)


def compute_max_loss(avg_price: float, price_diff: float, size: float) -> float:
    """
    Compute worst-case one-step loss.
    Used for risk budget validation.
    """
    if avg_price <= 0:
        return 0.0
    
    loss_per_unit = abs(price_diff) / avg_price
    return loss_per_unit * size


def validate_risk_budget(
    stake: float,
    bankroll: float,
    sizing: SizingParams
) -> tuple[bool, str]:
    """
    Validate that proposed stake doesn't violate risk limits.
    Returns (valid, reason).
    """
    stake_fraction = stake / bankroll if bankroll > 0 else 1.0
    
    if stake_fraction > sizing.per_market_cap:
        return False, f"per-market cap exceeded: {stake_fraction:.2%} > {sizing.per_market_cap:.2%}"
    
    if stake > sizing.max_trade_size:
        return False, f"hard cap exceeded: ${stake:.2f} > ${sizing.max_trade_size:.2f}"
    
    if stake_fraction > sizing.max_loss_per_trade:
        return False, f"risk budget exceeded: {stake_fraction:.2%} > {sizing.max_loss_per_trade:.2%}"
    
    return True, "ok"
