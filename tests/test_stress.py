"""
Tests for engine/stress.py
Pure functions — no DB, no Redis, no Docker needed.
Run with: pytest tests/test_stress.py -v
"""

from app.engine.stress import apply_shock, run_stress_test
from app.engine.pnl import calculate_position_pnl, calculate_portfolio_pnl


SECTOR_MAP = {
    "JPM":  "Banking",
    "GS":   "Banking",
    "NVDA": "Semiconductors",
    "AMZN": "Technology",
}

POSITIONS = [
    {"symbol": "JPM",  "quantity": 100, "avg_cost": 195.0},
    {"symbol": "NVDA", "quantity": 10,  "avg_cost": 200.0},
    {"symbol": "AMZN", "quantity": 5,   "avg_cost": 180.0},
]

CURRENT_PRICES = {
    "JPM":  312.47,
    "NVDA": 198.0,
    "AMZN": 200.0,
}


# ── apply_shock ───────────────────────────────────────────────────────────────

def test_sector_crash_only_hits_target_sector():
    """
    Banking crash -20% → JPM price drops.
    NVDA and AMZN must be completely untouched.
    """
    shocked = apply_shock(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        scenario_type  = "SECTOR_CRASH",
        target         = "Banking",
        shock_pct      = -20,
    )

    assert round(shocked["JPM"], 2)  == round(312.47 * 0.80, 2)
    assert shocked["NVDA"] == 198.0
    assert shocked["AMZN"] == 200.0


def test_market_crash_hits_every_position():
    """
    Market crash -15% → every single position drops.
    No stock is untouched.
    """
    shocked = apply_shock(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        scenario_type  = "MARKET_CRASH",
        target         = None,
        shock_pct      = -15,
    )

    assert round(shocked["JPM"],  2) == round(312.47 * 0.85, 2)
    assert round(shocked["NVDA"], 2) == round(198.0  * 0.85, 2)
    assert round(shocked["AMZN"], 2) == round(200.0  * 0.85, 2)


def test_single_stock_only_hits_target():
    """
    NVDA single stock shock -30% → only NVDA drops.
    JPM and AMZN must be completely untouched.
    """
    shocked = apply_shock(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        scenario_type  = "SINGLE_STOCK",
        target         = "NVDA",
        shock_pct      = -30,
    )

    assert round(shocked["NVDA"], 2) == round(198.0 * 0.70, 2)
    assert shocked["JPM"]  == 312.47
    assert shocked["AMZN"] == 200.0


def test_shock_does_not_mutate_original_prices():
    """
    Original prices dict must be unchanged after shock.
    apply_shock must work on a copy, never the original.
    """
    original = dict(CURRENT_PRICES)

    apply_shock(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        scenario_type  = "MARKET_CRASH",
        target         = None,
        shock_pct      = -50,
    )

    assert CURRENT_PRICES == original


# ── run_stress_test ───────────────────────────────────────────────────────────

def test_stress_test_sector_crash_response_structure():
    """
    Full stress test response must contain all required fields.
    """
    result = run_stress_test(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        open_value     = 40_000,
        scenario_type  = "SECTOR_CRASH",
        target         = "Banking",
        shock_pct      = -20,
    )

    assert "scenario"           in result
    assert "current_value"      in result
    assert "stressed_value"     in result
    assert "simulated_loss"     in result
    assert "simulated_loss_pct" in result
    assert "rules_breached"     in result
    assert "positions_at_risk"  in result
    assert "stressed_positions" in result


def test_stress_test_sector_crash_value_drops():
    """
    Banking crash → stressed portfolio value must be
    less than current portfolio value.
    """
    result = run_stress_test(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        open_value     = 40_000,
        scenario_type  = "SECTOR_CRASH",
        target         = "Banking",
        shock_pct      = -20,
    )

    assert result["stressed_value"] < result["current_value"]
    assert result["simulated_loss"] < 0


def test_stress_test_market_crash_all_positions_affected():
    """
    Market crash → every position in stressed_positions
    must have a lower stressed_price than current_price.
    """
    result = run_stress_test(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        open_value     = 40_000,
        scenario_type  = "MARKET_CRASH",
        target         = None,
        shock_pct      = -15,
    )

    for p in result["stressed_positions"]:
        assert p["stressed_price"] < p["current_price"]


def test_stress_test_single_stock_others_unchanged():
    """
    NVDA single stock shock → JPM and AMZN stressed prices
    must equal their current prices exactly.
    """
    result = run_stress_test(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        open_value     = 40_000,
        scenario_type  = "SINGLE_STOCK",
        target         = "NVDA",
        shock_pct      = -30,
    )

    for p in result["stressed_positions"]:
        if p["symbol"] != "NVDA":
            assert p["stressed_price"] == p["current_price"]


def test_stress_test_rules_breached_is_accurate():
    """
    With open_value much higher than current, daily loss
    limit should breach. DAILY_LOSS_LIMIT must be in
    rules_breached list.
    """
    result = run_stress_test(
        positions      = POSITIONS,
        current_prices = CURRENT_PRICES,
        open_value     = 10_000_000,  # absurdly high → loss > 2%
        scenario_type  = "MARKET_CRASH",
        target         = None,
        shock_pct      = -15,
    )

    assert "DAILY_LOSS_LIMIT" in result["rules_breached"]


def test_stress_test_invalid_scenario_raises():
    """Invalid scenario_type must raise ValueError."""
    import pytest
    with pytest.raises(ValueError):
        run_stress_test(
            positions      = POSITIONS,
            current_prices = CURRENT_PRICES,
            open_value     = 40_000,
            scenario_type  = "INVALID_SCENARIO",
            target         = None,
            shock_pct      = -20,
        )