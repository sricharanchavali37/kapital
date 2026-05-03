"""
Tests for app/engine/var.py

Pure function tests. No yfinance. No Redis. No DB. No Docker.
All inputs are hand-crafted so we know exactly what the answer must be.

Run with: pytest tests/test_var.py -v
Expected: 12 passed in under 1 second.

Test structure:
  Section A — compute_portfolio_daily_pnl()  (4 tests)
  Section B — compute_var()                  (4 tests)
  Section C — build_var_report()             (4 tests)
"""

import pytest
from app.engine.var import (
    compute_portfolio_daily_pnl,
    compute_var,
    compute_individual_var,
    get_worst_scenario,
    build_var_report,
)


# ─────────────────────────────────────────────────────────────────────────────
# Section A — compute_portfolio_daily_pnl()
# ─────────────────────────────────────────────────────────────────────────────

class TestComputePortfolioDailyPnl:

    def test_single_position_single_day(self):
        """
        JPM: position value $10,000. Day 1 return = +2%.
        Expected P&L: $200.00
        """
        result = compute_portfolio_daily_pnl(
            position_values={"JPM": 10_000.0},
            historical_returns={"JPM": [0.02]},
        )
        assert len(result) == 1
        assert result[0] == pytest.approx(200.0, rel=1e-4)

    def test_two_positions_correlation_preserved(self):
        """
        On a bad day: JPM -2%, NVDA -3%.
        JPM value: $20,000 → loss $400
        NVDA value: $30,000 → loss $900
        Total portfolio loss: -$1,300

        This is the whole point of historical simulation —
        correlated crashes hit the portfolio simultaneously.
        """
        result = compute_portfolio_daily_pnl(
            position_values={"JPM": 20_000.0, "NVDA": 30_000.0},
            historical_returns={
                "JPM":  [-0.02],
                "NVDA": [-0.03],
            },
        )
        assert len(result) == 1
        assert result[0] == pytest.approx(-1300.0, rel=1e-4)

    def test_uses_shortest_series_length(self):
        """
        If JPM has 3 days of returns but NVDA only has 2,
        the result should have 2 data points (shortest wins).
        No IndexError should occur.
        """
        result = compute_portfolio_daily_pnl(
            position_values={"JPM": 10_000.0, "NVDA": 10_000.0},
            historical_returns={
                "JPM":  [0.01, 0.02, 0.03],
                "NVDA": [0.01, -0.02],
            },
        )
        assert len(result) == 2

    def test_empty_positions_returns_empty_list(self):
        """No positions → no scenarios. Must not crash."""
        result = compute_portfolio_daily_pnl(
            position_values={},
            historical_returns={"JPM": [0.01, 0.02]},
        )
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Section B — compute_var()
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeVar:

    def test_95_var_is_less_severe_than_99_var(self):
        """
        Core VaR property: VaR at 99% must be more negative (worse)
        than VaR at 95%. Higher confidence = larger worst-case loss.

        If this test fails, the percentile math is inverted.
        """
        # 20 scenarios, mix of gains and losses
        daily_pnl = [
            -500, -400, -350, -300, -250,
            -200, -150, -100,  -50,    0,
             100,  200,  300,  400,  500,
             600,  700,  800,  900, 1000,
        ]
        var_95 = compute_var(daily_pnl, 0.95)
        var_99 = compute_var(daily_pnl, 0.99)

        # Both should be negative (they represent losses)
        assert var_95 <= 0
        assert var_99 <= 0

        # 99% VaR must be more severe (more negative) than 95% VaR
        assert var_99 <= var_95

    def test_var_with_known_percentile(self):
        """
        100 scenarios: -100, -99, -98, ..., -1, 0, 1, ..., -1 + 99 positives.
        Actually: values from -100 to +99 (200 values, wait let's be precise).

        Use 100 values: [-100, -99, ..., -1, 0, 1, ..., 99].
        5th percentile of 100 values = the 5th value = -96 (0-indexed: index 4).
        numpy.percentile uses linear interpolation.
        We just check the sign and magnitude direction.
        """
        daily_pnl = list(range(-50, 50))  # 100 values: -50 to +49
        var_95 = compute_var(daily_pnl, 0.95)

        # 5th percentile of [-50..49] is around -45 to -47
        assert var_95 < -40
        assert var_95 > -50  # Can't be worse than the absolute worst

    def test_all_positive_returns_var_is_zero(self):
        """
        If every historical day was a gain, VaR = 0.
        There is no loss scenario in the data.
        min(value, 0) ensures VaR is never reported as positive.
        """
        daily_pnl = [100.0, 200.0, 50.0, 300.0, 150.0]
        var = compute_var(daily_pnl, 0.95)
        assert var == 0.0

    def test_invalid_confidence_raises_error(self):
        """
        Confidence of 1.5 or -0.1 makes no mathematical sense.
        Must raise ValueError, not silently produce wrong output.
        """
        with pytest.raises(ValueError, match="Confidence must be between"):
            compute_var([100.0, -100.0], confidence=1.5)

        with pytest.raises(ValueError):
            compute_var([100.0, -100.0], confidence=0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Section C — build_var_report()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildVarReport:

    def _make_returns(self, n: int, daily_return: float) -> list[float]:
        """Helper: n days all returning the same value."""
        return [daily_return] * n

    def test_report_has_all_required_fields(self):
        """
        The full report must contain all fields the API response promises.
        Missing fields = broken contract with whoever reads the response.
        """
        report = build_var_report(
            position_values={"JPM": 20_000.0, "NVDA": 30_000.0},
            historical_returns={
                "JPM":  self._make_returns(100, -0.01),
                "NVDA": self._make_returns(100, -0.015),
            },
            dates=["2025-01-" + str(i).zfill(2) for i in range(1, 101)],
            confidence_levels=[0.95, 0.99],
        )

        assert "portfolio_value" in report
        assert "trading_days_used" in report
        assert "confidence_levels" in report
        assert "95%" in report["confidence_levels"]
        assert "99%" in report["confidence_levels"]
        assert "per_symbol_var" in report
        assert "worst_historical_scenario" in report
        assert "method" in report
        assert "calculated_at" in report

        # Each confidence level block must have these keys
        for level in ["95%", "99%"]:
            block = report["confidence_levels"][level]
            assert "var_1day_usd" in block
            assert "var_1day_pct" in block
            assert "interpretation" in block

    def test_portfolio_value_is_sum_of_positions(self):
        """$20,000 + $30,000 = $50,000. Math must not drift."""
        report = build_var_report(
            position_values={"JPM": 20_000.0, "NVDA": 30_000.0},
            historical_returns={
                "JPM":  self._make_returns(50, -0.01),
                "NVDA": self._make_returns(50, -0.02),
            },
            dates=["2025-01-01"] * 50,
        )
        assert report["portfolio_value"] == pytest.approx(50_000.0, rel=1e-4)

    def test_larger_position_has_larger_standalone_var(self):
        """
        JPM: $100,000 position
        NVDA: $10,000 position
        Same historical returns for both.
        JPM standalone VaR must be ~10x larger than NVDA standalone VaR.
        """
        same_returns = self._make_returns(252, -0.01) + self._make_returns(50, 0.02)
        report = build_var_report(
            position_values={"JPM": 100_000.0, "NVDA": 10_000.0},
            historical_returns={
                "JPM":  same_returns,
                "NVDA": same_returns,
            },
            dates=["2025-01-01"] * len(same_returns),
        )

        jpm_var = abs(report["per_symbol_var"]["JPM"]["var_usd"])
        nvda_var = abs(report["per_symbol_var"]["NVDA"]["var_usd"])

        # JPM var should be approximately 10x NVDA var (same returns, 10x position)
        assert jpm_var == pytest.approx(nvda_var * 10, rel=0.01)

    def test_empty_positions_returns_error_response(self):
        """
        Zero positions should return a graceful error dict,
        not raise an unhandled exception.
        """
        report = build_var_report(
            position_values={},
            historical_returns={},
            dates=[],
        )
        assert "error" in report
        assert report["portfolio_value"] == 0.0