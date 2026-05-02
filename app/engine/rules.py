"""
Pure functions. No DB. No Redis. No FastAPI.
Input: portfolio state as plain dicts → Output: list of alert dicts.
Testable with plain pytest in <2s, no infrastructure needed.
"""

from app.config import (
    DAILY_LOSS_LIMIT_PCT,
    CONCENTRATION_STOCK_PCT,
    CONCENTRATION_SECTOR_PCT,
    STOP_LOSS_PCT,
)


def check_daily_loss(
    current_value: float,
    open_value: float,
) -> dict | None:
    """
    Rule 1: Portfolio drops >2% from today's opening value → HALTED.
    Returns alert dict if breached, None if safe.
    """
    if open_value <= 0:
        return None

    drop_pct = ((open_value - current_value) / open_value) * 100

    if drop_pct >= DAILY_LOSS_LIMIT_PCT:
        return {
            "rule_name": "DAILY_LOSS_LIMIT",
            "symbol":    None,
            "message":   f"Portfolio down {drop_pct:.2f}% today. Limit is {DAILY_LOSS_LIMIT_PCT}%. Trading HALTED.",
            "severity":  "CRITICAL",
        }
    return None


def check_concentration(
    position_pnls: list[dict],
    sector_map: dict,
) -> list[dict]:
    """
    Rule 2:
    - Single stock  > 30% of portfolio → WARNING
    - Single sector > 50% of portfolio → WARNING
    Returns list of alert dicts (can be multiple breaches at once).
    """
    alerts = []
    total_value = sum(p["current_value"] for p in position_pnls)

    if total_value <= 0:
        return alerts

    # Stock concentration check
    for p in position_pnls:
        stock_pct = (p["current_value"] / total_value) * 100
        if stock_pct > CONCENTRATION_STOCK_PCT:
            alerts.append({
                "rule_name": "CONCENTRATION_BREACH",
                "symbol":    p["symbol"],
                "message":   f"{p['symbol']} is {stock_pct:.1f}% of portfolio. Limit is {CONCENTRATION_STOCK_PCT}%.",
                "severity":  "WARNING",
            })

    # Sector concentration check
    sector_values: dict[str, float] = {}
    for p in position_pnls:
        sector = sector_map.get(p["symbol"], "Unknown")
        sector_values[sector] = sector_values.get(sector, 0) + p["current_value"]

    for sector, value in sector_values.items():
        sector_pct = (value / total_value) * 100
        if sector_pct > CONCENTRATION_SECTOR_PCT:
            alerts.append({
                "rule_name": "CONCENTRATION_BREACH",
                "symbol":    None,
                "message":   f"{sector} sector is {sector_pct:.1f}% of portfolio. Limit is {CONCENTRATION_SECTOR_PCT}%.",
                "severity":  "WARNING",
            })

    return alerts


def check_stop_loss(position_pnls: list[dict]) -> list[dict]:
    """
    Rule 3: Any position down >7% from avg_cost → WARNING.
    Returns list of alert dicts.
    """
    alerts = []

    for p in position_pnls:
        if p["avg_cost"] <= 0:
            continue

        drop_pct = ((p["avg_cost"] - p["current_price"]) / p["avg_cost"]) * 100

        if drop_pct >= STOP_LOSS_PCT:
            alerts.append({
                "rule_name": "STOP_LOSS_HIT",
                "symbol":    p["symbol"],
                "message":   f"{p['symbol']} is down {drop_pct:.1f}% from entry price of ${p['avg_cost']:.2f}.",
                "severity":  "WARNING",
            })

    return alerts


def run_all_rules(
    position_pnls: list[dict],
    portfolio_pnl: dict,
    open_value: float,
    sector_map: dict,
) -> list[dict]:
    """
    Runs all 3 rules. Returns combined list of all alerts triggered.
    This is the single entry point called by the price feed loop.
    """
    alerts = []

    # Rule 1
    daily_loss = check_daily_loss(
        current_value=portfolio_pnl["portfolio_value"],
        open_value=open_value,
    )
    if daily_loss:
        alerts.append(daily_loss)

    # Rule 2
    alerts.extend(check_concentration(position_pnls, sector_map))

    # Rule 3
    alerts.extend(check_stop_loss(position_pnls))

    return alerts