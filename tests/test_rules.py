"""
Tests for engine/rules.py
Pure functions — no DB, no Redis, no Docker needed.
Run with: pytest tests/test_rules.py -v
"""

from app.engine.rules import (
    check_daily_loss,
    check_concentration,
    check_stop_loss,
    run_all_rules,
)
from app.engine.pnl import calculate_position_pnl, calculate_portfolio_pnl


# ── Rule 1 — Daily Loss Limit ─────────────────────────────────────────────────

def test_daily_loss_not_triggered_under_2_percent():
    """
    Portfolio opened at $100,000.
    Now worth $99,000 → 1% drop → safe, no alert.
    """
    result = check_daily_loss(
        current_value=99_000,
        open_value=100_000,
    )
    assert result is None


def test_daily_loss_triggered_at_exactly_2_percent():
    """
    Portfolio opened at $100,000.
    Now worth $98,000 → exactly 2% drop → alert fires.
    """
    result = check_daily_loss(
        current_value=98_000,
        open_value=100_000,
    )
    assert result is not None
    assert result["rule_name"] == "DAILY_LOSS_LIMIT"
    assert result["severity"] == "CRITICAL"


def test_daily_loss_triggered_above_2_percent():
    """
    Portfolio opened at $100,000.
    Now worth $95,000 → 5% drop → alert fires.
    """
    result = check_daily_loss(
        current_value=95_000,
        open_value=100_000,
    )
    assert result is not None
    assert result["rule_name"] == "DAILY_LOSS_LIMIT"


def test_daily_loss_zero_open_value():
    """Edge case — open value is zero → no division, no alert."""
    result = check_daily_loss(
        current_value=50_000,
        open_value=0,
    )
    assert result is None


# ── Rule 2 — Concentration Breach ────────────────────────────────────────────

def test_concentration_stock_not_triggered_under_30():
    """
    JPM = 25% of portfolio → safe, no alert.
    """
    sector_map = {"JPM": "Banking", "NVDA": "Semiconductors"}
    positions = [
        calculate_position_pnl("JPM",  100, 195.0, 200.0),
        calculate_position_pnl("NVDA", 100, 195.0, 600.0),
    ]
    alerts = check_concentration(positions, sector_map)
    stock_alerts = [a for a in alerts if a["symbol"] == "JPM"]
    assert len(stock_alerts) == 0


def test_concentration_stock_triggered_above_30():
    """
    JPM = 96% of portfolio → alert fires.
    """
    sector_map = {"JPM": "Banking", "NVDA": "Semiconductors"}
    positions = [
        calculate_position_pnl("JPM",  200, 195.0, 312.47),
        calculate_position_pnl("NVDA", 10,  2000.0, 198.0),
    ]
    alerts = check_concentration(positions, sector_map)
    stock_alerts = [
        a for a in alerts
        if a["rule_name"] == "CONCENTRATION_BREACH" and a["symbol"] == "JPM"
    ]
    assert len(stock_alerts) == 1
    assert stock_alerts[0]["severity"] == "WARNING"


def test_concentration_sector_triggered_above_50():
    """
    Banking sector = 96% of portfolio → sector alert fires.
    """
    sector_map = {"JPM": "Banking", "NVDA": "Semiconductors"}
    positions = [
        calculate_position_pnl("JPM",  200, 195.0, 312.47),
        calculate_position_pnl("NVDA", 10,  2000.0, 198.0),
    ]
    alerts = check_concentration(positions, sector_map)
    sector_alerts = [
        a for a in alerts
        if a["rule_name"] == "CONCENTRATION_BREACH" and a["symbol"] is None
    ]
    assert len(sector_alerts) == 1


def test_concentration_no_alerts_balanced_portfolio():
    """
    Balanced portfolio — no single stock or sector dominates.
    No alerts should fire.
    """
    sector_map = {
        "JPM":  "Banking",
        "AMZN": "Technology",
        "NVDA": "Semiconductors",
        "XOM":  "Energy",
    }
    positions = [
        calculate_position_pnl("JPM",  100, 150.0, 155.0),
        calculate_position_pnl("AMZN", 100, 150.0, 155.0),
        calculate_position_pnl("NVDA", 100, 150.0, 155.0),
        calculate_position_pnl("XOM",  100, 150.0, 155.0),
    ]
    alerts = check_concentration(positions, sector_map)
    assert len(alerts) == 0


# ── Rule 3 — Stop Loss ────────────────────────────────────────────────────────

def test_stop_loss_not_triggered_under_7_percent():
    """
    Position down 5% from avg cost → safe, no alert.
    """
    positions = [
        calculate_position_pnl("GS", 10, 400.0, 380.0),
    ]
    alerts = check_stop_loss(positions)
    assert len(alerts) == 0


def test_stop_loss_triggered_above_7_percent():
    """
    NVDA down 90% from avg cost → alert fires.
    """
    positions = [
        calculate_position_pnl("NVDA", 10, 2000.0, 198.0),
    ]
    alerts = check_stop_loss(positions)
    assert len(alerts) == 1
    assert alerts[0]["rule_name"] == "STOP_LOSS_HIT"
    assert alerts[0]["symbol"] == "NVDA"
    assert alerts[0]["severity"] == "WARNING"


def test_stop_loss_exactly_at_7_percent():
    """
    Position down exactly 7% → alert fires.
    avg_cost=100, current=93 → 7% drop.
    """
    positions = [
        calculate_position_pnl("AAPL", 10, 100.0, 93.0),
    ]
    alerts = check_stop_loss(positions)
    assert len(alerts) == 1


def test_stop_loss_multiple_positions_only_one_breached():
    """
    Two positions. Only NVDA breaches stop loss.
    JPM is fine.
    """
    positions = [
        calculate_position_pnl("JPM",  100, 195.0, 312.47),
        calculate_position_pnl("NVDA", 10,  2000.0, 198.0),
    ]
    alerts = check_stop_loss(positions)
    assert len(alerts) == 1
    assert alerts[0]["symbol"] == "NVDA"


# ── run_all_rules ─────────────────────────────────────────────────────────────

def test_run_all_rules_returns_combined_alerts():
    """
    All 3 rules should fire given the right conditions.
    """
    sector_map = {"JPM": "Banking", "NVDA": "Semiconductors"}
    position_pnls = [
        calculate_position_pnl("JPM",  200, 195.0, 312.47),
        calculate_position_pnl("NVDA", 10,  2000.0, 198.0),
    ]
    portfolio_pnl = calculate_portfolio_pnl(position_pnls)

    alerts = run_all_rules(
        position_pnls = position_pnls,
        portfolio_pnl = portfolio_pnl,
        open_value    = 100_000,
        sector_map    = sector_map,
    )

    rule_names = [a["rule_name"] for a in alerts]
    assert "STOP_LOSS_HIT" in rule_names
    assert "CONCENTRATION_BREACH" in rule_names


def test_run_all_rules_no_alerts_safe_portfolio():
    """Safe portfolio → no rules fire."""
    sector_map = {
        "JPM":  "Banking",
        "AMZN": "Technology",
        "NVDA": "Semiconductors",
        "XOM":  "Energy",
    }
    position_pnls = [
        calculate_position_pnl("JPM",  100, 150.0, 155.0),
        calculate_position_pnl("AMZN", 100, 150.0, 155.0),
        calculate_position_pnl("NVDA", 100, 150.0, 155.0),
        calculate_position_pnl("XOM",  100, 150.0, 155.0),
    ]
    portfolio_pnl = calculate_portfolio_pnl(position_pnls)

    alerts = run_all_rules(
        position_pnls = position_pnls,
        portfolio_pnl = portfolio_pnl,
        open_value    = portfolio_pnl["portfolio_value"],
        sector_map    = sector_map,
    )
    assert len(alerts) == 0