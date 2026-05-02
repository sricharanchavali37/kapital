import os
import json
from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from app.db.database import get_db
from app.db.models import Position, RiskAlert
from app.engine.pnl import (
    calculate_position_pnl,
    calculate_portfolio_pnl,
    calculate_sector_breakdown,
)
from app.config import SECTOR_MAP
from app.services.alert_service import get_system_status

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380")
router = APIRouter(prefix="/risk", tags=["risk"])


def _get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


def _read_price(r, symbol: str) -> tuple[float | None, bool]:
    """Read price from Redis. Returns (price, is_stale)."""
    try:
        raw = r.get(f"price:{symbol}")
        if raw is None:
            return None, True
        data = json.loads(raw)
        return data["price"], data.get("is_stale", False)
    except Exception:
        return None, True


@router.get("/report")
def get_risk_report(db: Session = Depends(get_db)):
    """
    Full portfolio health snapshot.
    Reads positions from Postgres, prices from Redis,
    calculates everything in memory, returns combined report.
    """
    r = _get_redis()

    # ── 1. Load open positions ─────────────────────────────────────────
    positions = db.query(Position).filter(Position.status == "OPEN").all()

    if not positions:
        return {
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "status":          "ACTIVE",
            "portfolio_value": 0.0,
            "total_pnl":       0.0,
            "total_pnl_pct":   0.0,
            "message":         "No open positions.",
            "active_alerts":   [],
            "positions":       [],
            "sector_breakdown": {},
        }

    # ── 2. Calculate P&L for each position ────────────────────────────
    position_pnls  = []
    any_stale      = False
    last_updated   = None

    for p in positions:
        price, is_stale = _read_price(r, p.symbol)

        if price is None:
            continue

        if is_stale:
            any_stale = True

        pnl = calculate_position_pnl(
            symbol        = p.symbol,
            quantity      = p.quantity,
            avg_cost      = p.avg_cost,
            current_price = price,
            is_stale      = is_stale,
        )

        # Add portfolio weight — needs total value, calculated after loop
        pnl["sector"] = p.sector
        position_pnls.append(pnl)

        # Track last price update time
        try:
            raw = r.get(f"price:{p.symbol}")
            if raw:
                fetched_at = json.loads(raw).get("fetched_at")
                if last_updated is None or fetched_at > last_updated:
                    last_updated = fetched_at
        except Exception:
            pass

    if not position_pnls:
        return {
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "status":          "ACTIVE",
            "portfolio_value": 0.0,
            "total_pnl":       0.0,
            "total_pnl_pct":   0.0,
            "message":         "Prices not yet available. Wait 5 seconds.",
            "active_alerts":   [],
            "positions":       [],
            "sector_breakdown": {},
        }

    # ── 3. Portfolio totals ────────────────────────────────────────────
    portfolio_pnl = calculate_portfolio_pnl(position_pnls)
    total_value   = portfolio_pnl["portfolio_value"]

    # Add portfolio_weight_pct to each position
    for p in position_pnls:
        p["portfolio_weight_pct"] = round(
            (p["current_value"] / total_value * 100) if total_value > 0 else 0.0,
            2
        )

    # ── 4. Sector breakdown ────────────────────────────────────────────
    sector_breakdown = calculate_sector_breakdown(position_pnls, SECTOR_MAP)

    # ── 5. Active alerts from Postgres ────────────────────────────────
    active_alerts = db.query(RiskAlert).filter(
        RiskAlert.is_active == True
    ).order_by(RiskAlert.last_fired_at.desc()).all()

    alerts_output = [
        {
            "rule_name": a.rule_name,
            "symbol":    a.symbol,
            "message":   a.message,
            "severity":  a.severity,
            "fired_at":  a.last_fired_at.isoformat(),
        }
        for a in active_alerts
    ]

    # ── 6. System status ───────────────────────────────────────────────
    system_status = get_system_status()

    # Overall report status logic:
    # HALTED   → daily loss limit breached
    # WARNING  → any active alerts exist
    # ACTIVE   → all clear
    if system_status == "HALTED":
        report_status = "HALTED"
    elif active_alerts:
        report_status = "WARNING"
    else:
        report_status = "ACTIVE"

    # ── 7. Build final response ────────────────────────────────────────
    return {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "status":          report_status,
        "portfolio_value": portfolio_pnl["portfolio_value"],
        "total_pnl":       portfolio_pnl["total_pnl"],
        "total_pnl_pct":   portfolio_pnl["total_pnl_pct"],
        "data_freshness": {
            "last_updated": last_updated,
            "is_stale":     any_stale,
        },
        "active_alerts":   alerts_output,
        "positions": [
            {
                "symbol":              p["symbol"],
                "sector":              p.get("sector"),
                "quantity":            p["quantity"],
                "avg_cost":            p["avg_cost"],
                "current_price":       p["current_price"],
                "current_value":       p["current_value"],
                "unrealized_pnl":      p["unrealized_pnl"],
                "unrealized_pnl_pct":  p["unrealized_pnl_pct"],
                "portfolio_weight_pct":p["portfolio_weight_pct"],
            }
            for p in position_pnls
        ],
        "sector_breakdown": sector_breakdown,
    }