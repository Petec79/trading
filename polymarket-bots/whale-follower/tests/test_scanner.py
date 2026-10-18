"""
Whale Follower Bot — Test Suite
Run: python -m pytest tests/ -v
"""
import pytest
import json
from src.scanner import calculate_kelly_size, filter_by_category
from src.portfolio import Portfolio
from src.config import APPROVED_WHALES


class TestKellySizing:
    """Quarter Kelly sizing calculations."""

    def test_full_kelly_zero(self):
        """No edge = zero position size."""
        assert calculate_kelly_size(10_000, 0.50, 2.0) == 0.0

    def test_quarter_kelly_fraction(self):
        """Quarter Kelly = full Kelly / 4."""
        # 60% win rate, 2.0 odds: full Kelly = (0.6*1 - 0.4)/1 = 0.2
        # Quarter Kelly = 0.05
        # Bankroll $10k -> $500 max position
        result = calculate_kelly_size(10_000, 0.60, 2.0)
        assert result == pytest.approx(500.0, rel=1.0)

    def test_kelly_capped_at_max_bet_fraction(self):
        """Position never exceeds 10% of bankroll."""
        # Very high edge: full Kelly might be 50%, but we cap at 10%
        result = calculate_kelly_size(10_000, 0.90, 3.0)
        assert result <= 1_000.0  # 10% of $10k

    def test_kelly_low_win_rate(self):
        """Below 50% win rate = zero Kelly (no edge)."""
        assert calculate_kelly_size(10_000, 0.49, 2.0) == 0.0


class TestCategoryFilter:
    """Filter signals by whale category."""

    def test_filter_by_category(self):
        signals = [
            {"category": "crypto_macro", "whale": "HorizonSplendidView"},
            {"category": "sports", "whale": "beachboy4"},
            {"category": "geopolitics", "whale": "majorexploiter"},
        ]
        crypto = filter_by_category(signals, "crypto_macro")
        assert len(crypto) == 1
        assert crypto[0]["whale"] == "HorizonSplendidView"


class TestPortfolio:
    """Portfolio risk management."""

    def _isolated_portfolio(self, tmp_path, initial_bankroll):
        """Create a Portfolio with isolated temp state file."""
        from src.portfolio import STATE_FILE
        import src.portfolio
        backup = None
        if STATE_FILE.exists():
            backup = STATE_FILE.read_bytes()
        original_state_file = src.portfolio.STATE_FILE
        src.portfolio.STATE_FILE = tmp_path / "portfolio.json"
        p = Portfolio(initial_bankroll=initial_bankroll)
        src.portfolio.STATE_FILE = original_state_file
        if backup:
            STATE_FILE.write_bytes(backup)
        return p

    def test_initial_bankroll(self, tmp_path):
        p = self._isolated_portfolio(tmp_path, 5_000.0)
        assert p.bankroll == 5_000.0
        assert p.initial_bankroll == 5_000.0

    def test_open_position_debits_bankroll(self, tmp_path):
        p = self._isolated_portfolio(tmp_path, 10_000.0)
        success = p.open_position(
            slug="test-market",
            side="BUY",
            price=0.40,
            size=100.0,
            cost=40.0,
            whale="HorizonSplendidView",
        )
        assert success is True
        assert p.bankroll == 9_960.0
        assert "test-market" in p.positions

    def test_insufficient_bankroll(self, tmp_path):
        p = self._isolated_portfolio(tmp_path, 100.0)
        success = p.open_position(
            slug="test-market",
            side="BUY",
            price=0.40,
            size=1000.0,
            cost=400.0,
            whale="beachboy4",
        )
        assert success is False
        assert p.bankroll == 100.0

    def test_circuit_breaker(self, tmp_path):
        p = self._isolated_portfolio(tmp_path, 10_000.0)
        p.daily_start_bankroll = 10_000.0
        p.bankroll = 8_999.0  # Down 10.01%
        can_trade, _ = p.can_trade()
        assert can_trade is False

    def test_daily_trade_limit(self, tmp_path):
        p = self._isolated_portfolio(tmp_path, 10_000.0)
        p.daily_trades = 20
        can_trade, reason = p.can_trade()
        assert can_trade is False
        assert "Daily trade limit" in reason

    def test_max_concurrent_positions(self, tmp_path):
        from src.config import MAX_CONCURRENT_TRADES
        p = self._isolated_portfolio(tmp_path, 10_000.0)
        # Fill up positions
        for i in range(MAX_CONCURRENT_TRADES):
            p.positions[f"market_{i}"] = {"size": 10}
        can_pos, _ = p.check_position_limits()
        assert can_pos is False


class TestApprovedWhales:
    """Verify approved whale configuration."""

    def test_whales_defined(self):
        assert "HorizonSplendidView" in APPROVED_WHALES
        assert "beachboy4" in APPROVED_WHALES

    def test_whale_structure(self):
        for name, info in APPROVED_WHALES.items():
            assert "address" in info
            assert "category" in info
            assert "min_size_usd" in info
            assert "max_copy_usd" in info
            assert len(info["address"]) == 42  # Ethereum address length


class TestProbabilityBrain:
    """Probability estimation and EV calculation."""

    def test_calc_ev_positive(self):
        from src.probability_brain import calc_ev
        # 65% true prob, 50% market = positive EV
        ev = calc_ev(0.65, 0.50)
        assert abs(ev - 0.15) < 1e-9

    def test_calc_ev_negative(self):
        from src.probability_brain import calc_ev
        # 40% true prob, 50% market = negative EV
        ev = calc_ev(0.40, 0.50)
        assert abs(ev - (-0.10)) < 1e-9

    def test_calc_ev_at_threshold(self):
        from src.probability_brain import calc_ev
        # 55% true prob at 50% market = 5% EV
        ev = calc_ev(0.55, 0.50)
        assert abs(ev - 0.05) < 1e-9

    def test_bayesian_update(self):
        from src.probability_brain import bayesian_update
        result = bayesian_update(0.5, 0.7, 0.6)
        assert 0.58 < result < 0.59

    def test_bayesian_update_edge_zero_marginal(self):
        from src.probability_brain import bayesian_update
        result = bayesian_update(0.5, 0.7, 0.0)
        assert result == 0.5  # Falls back to prior

    def test_estimate_no_api_key_skips(self):
        import os
        from src.probability_brain import estimate
        # Ensure no API key
        old_key = os.environ.get("ANTHROPIC_API_KEY", "")
        os.environ["ANTHROPIC_API_KEY"] = ""
        try:
            result = estimate("Will BTC hit 100k?", 0.60)
            assert result.verdict == "SKIP"
            assert "No ANTHROPIC_API_KEY" in result.reasoning
        finally:
            if old_key:
                os.environ["ANTHROPIC_API_KEY"] = old_key

    def test_estimate_ev_threshold(self):
        from src.probability_brain import estimate
        import os
        old_key = os.environ.get("ANTHROPIC_API_KEY", "")
        os.environ["ANTHROPIC_API_KEY"] = ""
        try:
            # Low market price, high min_ev → SKIP
            result = estimate("Test question?", 0.02, min_ev=0.05)
            assert result.verdict == "SKIP"
            assert result.ev == 0.0
        finally:
            if old_key:
                os.environ["ANTHROPIC_API_KEY"] = old_key
