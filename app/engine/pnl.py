"""
Pure functions. No DB. No Redis. No FastAPI.
Input dicts → output dicts. Testable with plain pytest, no infrastructure.
"""

from typing import Optional


def calculate_avg_cost(
    existing_qty: float,
    existing_avg: float,
    new_qty: float,
    new_price: float,
) -> float:
    """
    FIFO weighted average cost basis.
    Buy 100 @ 195, then 100 @ 205 → avg = 200.00
    """
    if existing_qty + new_qty == 0:
        return 0.0
    return (existing_qty * existing_avg + new_qty * new_price) / (existing_qty + new_qty)


def calculate_position_pnl(
    symbol: str,
    quantity: float,
    avg_cost: float,
    current_price: float,
    realized_pnl: float = 0.0,
    is_stale: bool = False,
) -> dict:
    """
    Returns P&L for a single position.
    is_stale=True → data_warning included in response.
    """
    current_value   = current_price * quantity
    cost_basis      = avg_cost * quantity
    unrealized_pnl  = current_value - cost_basis
    unrealized_pct  = (unrealized_pnl / cost_basis * 100) if cost_basis != 0 else 0.0

    result = {
        "symbol":           symbol,
        "quantity":         quantity,
        "avg_cost":         avg_cost,
        "current_price":    current_price,
        "current_value":    round(current_value, 2),
        "cost_basis":       round(cost_basis, 2),
        "unrealized_pnl":   round(unrealized_pnl, 2),
        "unrealized_pnl_pct": round(unrealized_pct, 4),
        "realized_pnl":     round(realized_pnl, 2),
    }

    if is_stale:
        result["data_warning"] = "Price data is stale (>30s old). P&L may not reflect current market."

    return result


def calculate_portfolio_pnl(position_pnls: list[dict]) -> dict:
    """
    Aggregates individual position P&Ls into portfolio-level totals.
    position_pnls → list of dicts from calculate_position_pnl()
    """
    total_value       = sum(p["current_value"] for p in position_pnls)
    total_unrealized  = sum(p["unrealized_pnl"] for p in position_pnls)
    total_realized    = sum(p["realized_pnl"] for p in position_pnls)
    total_pnl         = total_unrealized + total_realized
    total_cost_basis  = sum(p["cost_basis"] for p in position_pnls)

    total_pnl_pct = (total_pnl / total_cost_basis * 100) if total_cost_basis != 0 else 0.0
    any_stale     = any("data_warning" in p for p in position_pnls)

    result = {
        "portfolio_value":    round(total_value, 2),
        "total_unrealized":   round(total_unrealized, 2),
        "total_realized":     round(total_realized, 2),
        "total_pnl":          round(total_pnl, 2),
        "total_pnl_pct":      round(total_pnl_pct, 4),
        "position_count":     len(position_pnls),
    }

    if any_stale:
        result["data_warning"] = "One or more prices are stale. Portfolio value may be inaccurate."

    return result