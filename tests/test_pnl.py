"""
Tests for engine/pnl.py
Pure functions — no DB, no Redis, no Docker needed.
Run with: pytest tests/test_pnl.py -v
"""

from app.engine.pnl import (
    calculate_avg_cost,
    calculate_position_pnl,
    calculate_portfolio_pnl,
    calculate_sector_breakdown,
)


# ── FIFO Average Cost ─────────────────────────────────────────────────────────

def test_avg_cost_single_buy():
    """First buy — avg cost is just the buy price."""
    result = calculate_avg_cost(
        existing_qty=0,
        existing_avg=0,
        new_qty=100,
        new_price=195.0,
    )
    assert result == 195.0


def test_avg_cost_two_buys_equal_qty():
    """
    Buy 100 @ $195, then 100 @ $205.
    Correct avg = $200. Not $195. Not $205.
    """
    result = calculate_avg_cost(
        existing_qty=100,
        existing_avg=195.0,
        new_qty=100,
        new_price=205.0,
    )
    assert result == 200.0


def test_avg_cost_two_buys_unequal_qty():
    """
    Buy 100 @ $190, then 200 @ $210.
    Correct avg = (100×190 + 200×210) / 300 = $203.33
    """
    result = calculate_avg_cost(
        existing_qty=100,
        existing_avg=190.0,
        new_qty=200,
        new_price=210.0,
    )
    assert round(result, 2) == 203.33


def test_avg_cost_zero_total_qty():
    """Edge case — zero total quantity returns 0."""
    result = calculate_avg_cost(
        existing_qty=0,
        existing_avg=0,
        new_qty=0,
        new_price=100.0,
    )
    assert result == 0.0


# ── Position P&L ──────────────────────────────────────────────────────────────

def test_position_pnl_at_profit():
    """
    Bought 100 JPM @ $195. Now $312.47.
    Unrealized P&L = (312.47 - 195) × 100 = $11,747
    """
    result = calculate_position_pnl(
        symbol="JPM",
        quantity=100,
        avg_cost=195.0,
        current_price=312.47,
    )
    assert result["symbol"] == "JPM"
    assert result["unrealized_pnl"] == 11747.0
    assert result["unrealized_pnl_pct"] > 0
    assert result["current_value"] == 31247.0
    assert "data_warning" not in result


def test_position_pnl_at_loss():
    """
    Bought 10 NVDA @ $2000. Now $198.
    Unrealized P&L = (198 - 2000) × 10 = -$18,020
    """
    result = calculate_position_pnl(
        symbol="NVDA",
        quantity=10,
        avg_cost=2000.0,
        current_price=198.0,
    )
    assert result["symbol"] == "NVDA"
    assert result["unrealized_pnl"] == -18020.0
    assert result["unrealized_pnl_pct"] < 0
    assert "data_warning" not in result


def test_position_pnl_break_even():
    """Current price equals avg cost → P&L is exactly zero."""
    result = calculate_position_pnl(
        symbol="AAPL",
        quantity=50,
        avg_cost=150.0,
        current_price=150.0,
    )
    assert result["unrealized_pnl"] == 0.0
    assert result["unrealized_pnl_pct"] == 0.0


def test_position_pnl_stale_price():
    """Stale price → data_warning must appear in result."""
    result = calculate_position_pnl(
        symbol="GS",
        quantity=20,
        avg_cost=400.0,
        current_price=380.0,
        is_stale=True,
    )
    assert "data_warning" in result
    assert "stale" in result["data_warning"].lower()


def test_position_pnl_fresh_price_no_warning():
    """Fresh price → no data_warning in result."""
    result = calculate_position_pnl(
        symbol="GS",
        quantity=20,
        avg_cost=400.0,
        current_price=380.0,
        is_stale=False,
    )
    assert "data_warning" not in result


# ── Portfolio P&L ─────────────────────────────────────────────────────────────

def test_portfolio_pnl_aggregation():
    """
    Two positions:
      JPM: 100 shares, +$11,747 profit
      NVDA: 10 shares, -$18,020 loss
    Total P&L = 11,747 - 18,020 = -$6,273
    """
    positions = [
        calculate_position_pnl("JPM",  100, 195.0,  312.47),
        calculate_position_pnl("NVDA", 10,  2000.0, 198.0),
    ]
    result = calculate_portfolio_pnl(positions)

    assert result["position_count"] == 2
    assert result["total_pnl"] == round(11747.0 + (-18020.0), 2)
    assert result["portfolio_value"] == round(31247.0 + 1980.0, 2)
    assert "data_warning" not in result


def test_portfolio_pnl_stale_propagates():
    """
    If any position has stale price,
    portfolio-level data_warning must appear.
    """
    positions = [
        calculate_position_pnl("JPM",  100, 195.0, 312.47, is_stale=False),
        calculate_position_pnl("NVDA", 10, 2000.0, 198.0,  is_stale=True),
    ]
    result = calculate_portfolio_pnl(positions)
    assert "data_warning" in result


def test_portfolio_pnl_single_position():
    """Portfolio with one position — totals equal that position."""
    positions = [
        calculate_position_pnl("JPM", 100, 195.0, 312.47),
    ]
    result = calculate_portfolio_pnl(positions)

    assert result["position_count"] == 1
    assert result["portfolio_value"] == 31247.0
    assert result["total_unrealized"] == 11747.0


# ── Sector Breakdown ──────────────────────────────────────────────────────────

def test_sector_breakdown_grouping():
    """
    JPM → Banking, NVDA → Semiconductors.
    Should produce two sectors.
    """
    sector_map = {"JPM": "Banking", "NVDA": "Semiconductors"}
    positions = [
        calculate_position_pnl("JPM",  100, 195.0,  312.47),
        calculate_position_pnl("NVDA", 10,  2000.0, 198.0),
    ]
    result = calculate_sector_breakdown(positions, sector_map)

    assert "Banking" in result
    assert "Semiconductors" in result
    assert "JPM" in result["Banking"]["positions"]
    assert "NVDA" in result["Semiconductors"]["positions"]


def test_sector_breakdown_warning_above_50():
    """Sector above 50% of portfolio → status=WARNING."""
    sector_map = {"JPM": "Banking", "GS": "Banking"}
    positions = [
        calculate_position_pnl("JPM", 100, 195.0, 312.47),
        calculate_position_pnl("GS",  5,  380.0, 520.0),
    ]
    result = calculate_sector_breakdown(positions, sector_map)
    assert result["Banking"]["status"] == "WARNING"


def test_sector_breakdown_normal_below_50():
    """Sector below 50% of portfolio → status=NORMAL."""
    sector_map = {"JPM": "Banking", "NVDA": "Semiconductors"}
    positions = [
        calculate_position_pnl("JPM",  10,  195.0,  312.47),
        calculate_position_pnl("NVDA", 100, 200.0,  198.0),
    ]
    result = calculate_sector_breakdown(positions, sector_map)
    assert result["Banking"]["status"] == "NORMAL"