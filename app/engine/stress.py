"""
Pure functions. No DB. No Redis. No FastAPI.
Stress test scenarios run entirely in memory.
Zero real data is changed at any point.
"""

from app.engine.pnl import (
    calculate_position_pnl,
    calculate_portfolio_pnl,
)
from app.engine.rules import run_all_rules
from app.config import SECTOR_MAP


def apply_shock(
    positions: list[dict],
    current_prices: dict[str, float],
    scenario_type: str,
    target: str | None,
    shock_pct: float,
) -> dict[str, float]:
    """
    Apply price shock to relevant positions.
    Returns a NEW price dict — original prices untouched.

    scenario_type: SECTOR_CRASH | MARKET_CRASH | SINGLE_STOCK
    target:        sector name (SECTOR_CRASH) or symbol (SINGLE_STOCK) or None (MARKET_CRASH)
    shock_pct:     negative number e.g. -20 means 20% drop
    """
    multiplier   = 1 + (shock_pct / 100)
    shocked      = dict(current_prices)  # copy, never mutate original

    for pos in positions:
        symbol = pos["symbol"]
        if symbol not in shocked:
            continue

        if scenario_type == "MARKET_CRASH":
            shocked[symbol] = shocked[symbol] * multiplier

        elif scenario_type == "SECTOR_CRASH":
            if SECTOR_MAP.get(symbol) == target:
                shocked[symbol] = shocked[symbol] * multiplier

        elif scenario_type == "SINGLE_STOCK":
            if symbol == target:
                shocked[symbol] = shocked[symbol] * multiplier

    return shocked


def run_stress_test(
    positions: list[dict],
    current_prices: dict[str, float],
    open_value: float,
    scenario_type: str,
    target: str | None,
    shock_pct: float,
) -> dict:
    """
    Full stress test simulation.

    Steps:
    1. Apply shock to prices (in memory only)
    2. Recalculate P&L with shocked prices
    3. Run all 3 risk rules on shocked state
    4. Return comparison: current vs stressed

    Nothing written to DB or Redis here.
    Caller writes one AuditLog row after this returns.
    """
    # Validate scenario type
    valid_scenarios = {"SECTOR_CRASH", "MARKET_CRASH", "SINGLE_STOCK"}
    if scenario_type not in valid_scenarios:
        raise ValueError(f"Invalid scenario_type. Must be one of {valid_scenarios}")

    # Step 1: Apply shock
    shocked_prices = apply_shock(
        positions      = positions,
        current_prices = current_prices,
        scenario_type  = scenario_type,
        target         = target,
        shock_pct      = shock_pct,
    )

    # Step 2: Calculate P&L with real prices
    real_pnls = [
        calculate_position_pnl(
            symbol        = p["symbol"],
            quantity      = p["quantity"],
            avg_cost      = p["avg_cost"],
            current_price = current_prices[p["symbol"]],
        )
        for p in positions
        if p["symbol"] in current_prices
    ]

    # Step 3: Calculate P&L with shocked prices
    stressed_pnls = [
        calculate_position_pnl(
            symbol        = p["symbol"],
            quantity      = p["quantity"],
            avg_cost      = p["avg_cost"],
            current_price = shocked_prices[p["symbol"]],
        )
        for p in positions
        if p["symbol"] in shocked_prices
    ]

    if not real_pnls or not stressed_pnls:
        raise ValueError("No positions with available prices to stress test.")

    real_portfolio     = calculate_portfolio_pnl(real_pnls)
    stressed_portfolio = calculate_portfolio_pnl(stressed_pnls)

    current_value  = real_portfolio["portfolio_value"]
    stressed_value = stressed_portfolio["portfolio_value"]
    simulated_loss = stressed_value - current_value
    simulated_loss_pct = (
        (simulated_loss / current_value * 100) if current_value > 0 else 0.0
    )

    # Step 4: Run rules on shocked state
    alerts = run_all_rules(
        position_pnls = stressed_pnls,
        portfolio_pnl = stressed_portfolio,
        open_value    = open_value,
        sector_map    = SECTOR_MAP,
    )

    rules_breached    = list({a["rule_name"] for a in alerts})
    positions_at_risk = [
        p["symbol"] for p in stressed_pnls
        if p["unrealized_pnl"] < 0
    ]

    # Build scenario label
    if scenario_type == "MARKET_CRASH":
        scenario_label = f"MARKET_CRASH — all positions — {shock_pct}%"
    elif scenario_type == "SECTOR_CRASH":
        scenario_label = f"SECTOR_CRASH — {target} — {shock_pct}%"
    else:
        scenario_label = f"SINGLE_STOCK — {target} — {shock_pct}%"

    return {
        "scenario":            scenario_label,
        "current_value":       round(current_value, 2),
        "stressed_value":      round(stressed_value, 2),
        "simulated_loss":      round(simulated_loss, 2),
        "simulated_loss_pct":  round(simulated_loss_pct, 4),
        "rules_breached":      rules_breached,
        "positions_at_risk":   positions_at_risk,
        "stressed_positions": [
            {
                "symbol":          p["symbol"],
                "current_price":   current_prices.get(p["symbol"]),
                "stressed_price":  round(shocked_prices.get(p["symbol"], 0), 2),
                "unrealized_pnl":  p["unrealized_pnl"],
            }
            for p in stressed_pnls
        ],
    }