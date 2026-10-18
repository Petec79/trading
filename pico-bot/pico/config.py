"""
PICO Framework Configuration
Tailored for micro-budget Polymarket trading ($100-$500 bankroll)
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class PICOConfig:
    """Hyperparameters for the PICO trading framework."""

    # === BANKROLL ===
    initial_bankroll: float = 300.0       # Starting bankroll in USDC
    min_bankroll: float = 50.0            # Stop-trading floor

    # === MARKET UNIVERSE ===
    market_universe_size: int = 4          # Fixed small universe (per paper)
    top_k_markets: int = 2                 # How many markets to consider per epoch

    # === POSITION SIZING ===
    per_market_exposure_cap: float = 0.04  # E_m: max fraction of bankroll per market (0.02-0.04 per paper)
    max_stake_per_trade: float = 12.0     # Hard cap in USDC
    risk_fraction: float = 0.03           # α: worst-case loss fraction per trade (2-5%)

    # === RISK CONTROLS ===
    stop_loss_pct: float = 0.02           # Stop-loss threshold (fraction of stake)
    take_profit_pct: float = 0.06         # Take-profit threshold (multiple of stake)
    max_daily_trades: int = 2             # T_max: daily entry cap

    # === COOLDOWNS ===
    min_cooldown_epochs: int = 4           # θ_min: fixed cooldown after trade (in epochs)
    cooldown_decay: float = 0.5           # How much cooldown reduces each epoch

    # === LIQUIDITY FILTERS ===
    min_liquidity_score: float = 0.3       # L_m threshold (0-1)
    max_spread_pct: float = 0.08          # Max bid-ask spread to consider (8%)
    min_depth_usdc: float = 10.0          # Minimum orderbook depth ($10)

    # === COSTS ===
    fee_per_unit: float = 0.01            # f: per-unit trading fee (1%)
    slippage_rate: float = 0.005          # s_m: slippage multiplier (0.5% per unit)

    # === DECISION CADENCE ===
    epochs_per_run: int = 100             # Decision epochs per backtest run
    epoch_length_minutes: int = 60        # Time between decisions (1 hour default)

    # === BACKTESTING ===
    walk_forward_train_days: int = 30     # Calibration window
    walk_forward_eval_days: int = 60      # Evaluation window
    random_seeds: List[int] = field(default_factory=lambda: [42, 123, 456])

    # === SIGNAL GENERATION ===
    signal_threshold_yes: float = 0.65    # Enter YES if P(yes) > this
    signal_threshold_no: float = 0.35     # Enter NO if P(yes) < this
    # Use simple price-proximity heuristic: signal = 1 - |price - 0.5| * 2
    # i.e. closer to 0.50 = higher confidence (but note: in real markets 0.50 means true uncertainty)

    def __post_init__(self):
        assert 0 < self.per_market_exposure_cap <= 0.25, "Exposure cap must be 0-25%"
        assert self.min_bankroll < self.initial_bankroll
        assert 0 < self.risk_fraction <= 0.1, "Risk fraction must be 0-10%"
