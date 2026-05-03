"""
Value at Risk (VaR) — Historical Simulation Method.

Pure functions. No yfinance. No Redis. No DB. No FastAPI.
Input dicts → output dicts. Testable with plain pytest, no infrastructure.

Method: Historical Simulation
-------------------------------
For each of the last 252 trading days, we ask:
  "If that exact day's returns happened to our CURRENT portfolio,
   how much money would we have made or lost?"

We collect 252 such P&L scenarios, sort them worst to best,
and take the percentile that matches our confidence level.

VaR at 95% = the 5th-percentile scenario (12th worst out of 252).
VaR at 99% = the 1st-percentile scenario (2nd worst out of 252).

Why this method?
  - Uses real historical data, not assumptions about distributions
  - Preserves correlations automatically (if Banking crashed in 2022,
    all Banking positions fell together — that's captured here)
  - Fully explainable in an interview: "I applied 252 days of real
    market returns to my current positions and measured the tail"
"""

import numpy as np
from datetime import datetime, timezone


# ── Core Calculation ──────────────────────────────────────────────────────────

def compute_portfolio_daily_pnl(
    position_values: dict[str, float],
    historical_returns: dict[str, list[float]],
) -> list[float]:
    """
    For each historical day, compute total portfolio P&L if that day repeated.

    Args:
        position_values:    {symbol: current_dollar_value}
                            e.g. {"JPM": 39640.0, "NVDA": 29820.0}
        historical_returns: {symbol: [day1_return, day2_return, ...]}
                            returns as decimals: 0.02 = +2%, -0.015 = -1.5%
                            All lists must be the same length.

    Returns:
        List of portfolio P&L for each historical day (in dollars).
        Length = number of trading days in the history.

    Example:
        On day 42, JPM returned -2% and NVDA returned -3%.
        Portfolio P&L for day 42 = (39640 × -0.02) + (29820 × -0.03)
                                  = -792.80 + (-894.60)
                                  = -1687.40
    """
    if not position_values or not historical_returns:
        return []

    # Find symbols that exist in both position_values and historical_returns
    common_symbols = [
        s for s in position_values
        if s in historical_returns and len(historical_returns[s]) > 0
    ]

    if not common_symbols:
        return []

    # All return series must be same length — use the shortest
    n_days = min(len(historical_returns[s]) for s in common_symbols)
    if n_days == 0:
        return []

    portfolio_pnl = []
    for day_idx in range(n_days):
        day_pnl = sum(
            position_values[symbol] * historical_returns[symbol][day_idx]
            for symbol in common_symbols
        )
        portfolio_pnl.append(round(day_pnl, 2))

    return portfolio_pnl


def compute_var(
    portfolio_daily_pnl: list[float],
    confidence: float,
) -> float:
    """
    Compute VaR at a given confidence level from simulated daily P&Ls.

    Args:
        portfolio_daily_pnl: list of simulated daily P&Ls (dollars)
        confidence:          0.95 for 95% VaR, 0.99 for 99% VaR

    Returns:
        VaR as a negative dollar amount.
        e.g. -3240.50 means "with X% confidence, max 1-day loss is $3,240.50"

    The math:
        VaR at 95% = 5th percentile of the P&L distribution
        VaR at 99% = 1st percentile of the P&L distribution
        numpy.percentile(data, 5) gives us the 5th percentile directly.
    """
    if not portfolio_daily_pnl:
        return 0.0

    if not (0.0 < confidence < 1.0):
        raise ValueError(
            f"Confidence must be between 0 and 1 exclusive. Got: {confidence}"
        )

    # Convert confidence to the loss-tail percentile
    # 95% confidence → look at worst 5% → 5th percentile
    # 99% confidence → look at worst 1% → 1st percentile
    percentile = (1.0 - confidence) * 100
    var_value = float(np.percentile(portfolio_daily_pnl, percentile))

    # VaR is always expressed as a non-positive number (it is a loss measure)
    return round(min(var_value, 0.0), 2)


def compute_individual_var(
    symbol: str,
    position_value: float,
    returns: list[float],
    confidence: float,
) -> dict:
    """
    VaR for a single position in isolation.
    Used for the per-symbol breakdown in the full report.
    """
    if not returns or position_value <= 0:
        return {
            "symbol": symbol,
            "position_value": round(position_value, 2),
            "var_usd": 0.0,
            "var_pct": 0.0,
        }

    daily_pnl = [position_value * r for r in returns]
    var_usd = compute_var(daily_pnl, confidence)
    var_pct = round((var_usd / position_value) * 100, 4) if position_value > 0 else 0.0

    return {
        "symbol": symbol,
        "position_value": round(position_value, 2),
        "var_usd": var_usd,
        "var_pct": var_pct,
    }


def get_worst_scenario(
    portfolio_daily_pnl: list[float],
    dates: list[str],
) -> dict:
    """
    Returns the single worst historical day from all simulated scenarios.

    Args:
        portfolio_daily_pnl: daily P&L list from compute_portfolio_daily_pnl()
        dates:               matching list of date strings ["2024-01-02", ...]
                             Must be the same length as portfolio_daily_pnl.

    Returns:
        dict with the date and loss of the worst scenario.
    """
    if not portfolio_daily_pnl:
        return {"date": None, "simulated_loss_usd": 0.0, "simulated_loss_pct": 0.0}

    worst_idx = int(np.argmin(portfolio_daily_pnl))
    worst_loss = portfolio_daily_pnl[worst_idx]

    date_str = dates[worst_idx] if dates and worst_idx < len(dates) else "unknown"

    return {
        "date": date_str,
        "simulated_loss_usd": round(worst_loss, 2),
    }


# ── Full VaR Report Builder ───────────────────────────────────────────────────

def build_var_report(
    position_values: dict[str, float],
    historical_returns: dict[str, list[float]],
    dates: list[str],
    confidence_levels: list[float] | None = None,
) -> dict:
    """
    Builds the complete VaR report. This is the function the API calls.

    Args:
        position_values:    {symbol: current_dollar_value}
        historical_returns: {symbol: [daily_returns_as_decimals]}
        dates:              list of date strings matching the returns
        confidence_levels:  default [0.95, 0.99]

    Returns:
        Complete VaR report dict ready for JSON serialization.
    """
    if confidence_levels is None:
        confidence_levels = [0.95, 0.99]

    for c in confidence_levels:
        if not (0.0 < c < 1.0):
            raise ValueError(
                f"Confidence must be between 0 and 1 exclusive. Got: {c}"
            )

    portfolio_value = round(sum(position_values.values()), 2)

    if not position_values or portfolio_value <= 0:
        return {
            "error": "No open positions with valid values.",
            "portfolio_value": 0.0,
            "confidence_levels": {},
        }

    # Compute correlated portfolio daily P&L scenarios
    portfolio_daily_pnl = compute_portfolio_daily_pnl(
        position_values, historical_returns
    )

    n_days = len(portfolio_daily_pnl)

    if n_days == 0:
        return {
            "error": "No historical return data available.",
            "portfolio_value": portfolio_value,
            "confidence_levels": {},
        }

    # VaR at each confidence level
    var_by_confidence = {}
    for c in confidence_levels:
        var_usd = compute_var(portfolio_daily_pnl, c)
        var_pct = round((var_usd / portfolio_value) * 100, 4) if portfolio_value > 0 else 0.0
        label = f"{int(c * 100)}%"
        var_by_confidence[label] = {
            "var_1day_usd": var_usd,
            "var_1day_pct": var_pct,
            "interpretation": (
                f"With {label} confidence, the maximum 1-day portfolio loss "
                f"is ${abs(var_usd):,.2f} ({abs(var_pct):.2f}% of portfolio value)."
            ),
        }

    # Per-symbol VaR (standalone, ignoring correlations)
    per_symbol = {}
    for symbol, pos_value in position_values.items():
        if symbol in historical_returns:
            # Use the primary (first) confidence level for per-symbol display
            primary_c = confidence_levels[0]
            per_symbol[symbol] = compute_individual_var(
                symbol=symbol,
                position_value=pos_value,
                returns=historical_returns[symbol][:n_days],
                confidence=primary_c,
            )

    # Worst single historical day
    worst = get_worst_scenario(portfolio_daily_pnl, dates)
    if portfolio_value > 0 and worst["simulated_loss_usd"] != 0:
        worst["simulated_loss_pct"] = round(
            (worst["simulated_loss_usd"] / portfolio_value) * 100, 4
        )
    else:
        worst["simulated_loss_pct"] = 0.0

    return {
        "portfolio_value": portfolio_value,
        "trading_days_used": n_days,
        "confidence_levels": var_by_confidence,
        "per_symbol_var": per_symbol,
        "worst_historical_scenario": worst,
        "data_source": "Yahoo Finance via yfinance — historical daily returns",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "method": "Historical Simulation",
        "note": (
            "Per-symbol VaR assumes positions are independent. "
            "Portfolio VaR uses correlated historical returns — "
            "this is the number that matters."
        ),
    }