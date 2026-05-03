import asyncio
import os
import json
from datetime import datetime, timezone, timedelta

import redis
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
from dotenv import load_dotenv

from app.db.database import get_db
from app.db.models import Position, RiskAlert, AuditLog
from app.engine.pnl import (
    calculate_position_pnl,
    calculate_portfolio_pnl,
    calculate_sector_breakdown,
)
from app.engine.stress import run_stress_test
from app.engine.var import build_var_report
from app.services.var_data import get_historical_returns
from app.config import SECTOR_MAP
from app.services.alert_service import get_system_status

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380")
router = APIRouter(prefix="/risk", tags=["risk"])


def _get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


def _read_price(r, symbol: str) -> tuple[float | None, bool]:
    try:
        raw = r.get(f"price:{symbol}")
        if raw is None:
            return None, True
        data = json.loads(raw)
        return data["price"], data.get("is_stale", False)
    except Exception:
        return None, True


# ── Risk Report ───────────────────────────────────────────────────────────────

@router.get("/report")
def get_risk_report(db: Session = Depends(get_db)):
    r = _get_redis()

    positions = db.query(Position).filter(Position.status == "OPEN").all()

    if not positions:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ACTIVE",
            "portfolio_value": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "message": "No open positions.",
            "active_alerts": [],
            "positions": [],
            "sector_breakdown": {},
        }

    position_pnls = []
    any_stale = False
    last_updated = None

    for p in positions:
        price, is_stale = _read_price(r, p.symbol)
        if price is None:
            continue

        if is_stale:
            any_stale = True

        pnl = calculate_position_pnl(
            symbol=p.symbol,
            quantity=p.quantity,
            avg_cost=p.avg_cost,
            current_price=price,
            is_stale=is_stale,
        )

        pnl["sector"] = p.sector
        position_pnls.append(pnl)

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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ACTIVE",
            "portfolio_value": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "message": "Prices not yet available. Wait 5 seconds.",
            "active_alerts": [],
            "positions": [],
            "sector_breakdown": {},
        }

    portfolio_pnl = calculate_portfolio_pnl(position_pnls)
    total_value = portfolio_pnl["portfolio_value"]

    for p in position_pnls:
        if total_value > 0:
            p["portfolio_weight_pct"] = round(
                (p["current_value"] / total_value) * 100, 2
            )
        else:
            p["portfolio_weight_pct"] = 0.0

    sector_breakdown = calculate_sector_breakdown(position_pnls, SECTOR_MAP)

    active_alerts = db.query(RiskAlert).filter(
        RiskAlert.is_active == True
    ).order_by(RiskAlert.last_fired_at.desc()).all()

    alerts_output = []
    for a in active_alerts:
        alerts_output.append({
            "rule_name": a.rule_name,
            "symbol": a.symbol,
            "message": a.message,
            "severity": a.severity,
            "fired_at": a.last_fired_at.isoformat(),
        })

    system_status = get_system_status()

    if system_status == "HALTED":
        report_status = "HALTED"
    elif active_alerts:
        report_status = "WARNING"
    else:
        report_status = "ACTIVE"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": report_status,
        "portfolio_value": portfolio_pnl["portfolio_value"],
        "total_pnl": portfolio_pnl["total_pnl"],
        "total_pnl_pct": portfolio_pnl["total_pnl_pct"],
        "data_freshness": {
            "last_updated": last_updated,
            "is_stale": any_stale,
        },
        "active_alerts": alerts_output,
        "positions": [
            {
                "symbol": p["symbol"],
                "sector": p.get("sector"),
                "quantity": p["quantity"],
                "avg_cost": p["avg_cost"],
                "current_price": p["current_price"],
                "current_value": p["current_value"],
                "unrealized_pnl": p["unrealized_pnl"],
                "unrealized_pnl_pct": p["unrealized_pnl_pct"],
                "portfolio_weight_pct": p["portfolio_weight_pct"],
            }
            for p in position_pnls
        ],
        "sector_breakdown": sector_breakdown,
    }


# ── Stress Test ───────────────────────────────────────────────────────────────

class StressTestRequest(BaseModel):
    scenario_type: str = Field(
        ..., description="SECTOR_CRASH | MARKET_CRASH | SINGLE_STOCK"
    )
    target: str | None = Field(
        None,
        description="Sector or symbol depending on scenario",
    )
    shock_pct: float = Field(
        ..., description="Negative = drop, e.g. -20 means 20% crash"
    )


@router.post("/stress-test")
def stress_test(payload: StressTestRequest, db: Session = Depends(get_db)):
    r = _get_redis()

    positions = db.query(Position).filter(Position.status == "OPEN").all()

    if not positions:
        raise HTTPException(status_code=400, detail="No open positions.")

    positions_list = []
    current_prices = {}

    for p in positions:
        price, _ = _read_price(r, p.symbol)
        if price is None:
            continue

        positions_list.append({
            "symbol": p.symbol,
            "quantity": p.quantity,
            "avg_cost": p.avg_cost,
        })
        current_prices[p.symbol] = price

    if not positions_list:
        raise HTTPException(status_code=400, detail="No prices available.")

    open_value_raw = r.get("portfolio:open_value")
    open_value = float(open_value_raw) if open_value_raw else 0.0

    try:
        result = run_stress_test(
            positions=positions_list,
            current_prices=current_prices,
            open_value=open_value,
            scenario_type=payload.scenario_type,
            target=payload.target,
            shock_pct=payload.shock_pct,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(AuditLog(
        event_type="STRESS_TEST_RUN",
        symbol=None,
        description=json.dumps(result),
    ))
    db.commit()

    return result


# ── P&L History ───────────────────────────────────────────────────────────────

@router.get("/pnl-history")
def get_pnl_history(
    interval: str = "5m",
    hours: int = 6,
    db: Session = Depends(get_db),
):
    interval_map = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
    }

    if interval not in interval_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval. Choose from: {list(interval_map.keys())}"
        )

    interval_minutes = interval_map[interval]
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    query = text("""
        SELECT
            date_trunc('minute', calculated_at) -
            (EXTRACT(MINUTE FROM calculated_at)::int % :interval_minutes) * interval '1 minute'
                AS bucket,
            AVG(portfolio_value)  AS portfolio_value,
            AVG(unrealized_pnl)   AS unrealized_pnl,
            AVG(realized_pnl)     AS realized_pnl
        FROM pnl_records
        WHERE
            symbol IS NULL
            AND calculated_at >= :since
        GROUP BY bucket
        ORDER BY bucket ASC
    """)

    rows = db.execute(query, {
        "interval_minutes": interval_minutes,
        "since": since,
    }).fetchall()

    if not rows:
        return {
            "interval": interval,
            "hours": hours,
            "data_points": 0,
            "history": [],
            "message": "No data yet.",
        }

    history = []
    for row in rows:
        history.append({
            "timestamp": row.bucket.isoformat(),
            "portfolio_value": round(float(row.portfolio_value), 2),
            "unrealized_pnl": round(float(row.unrealized_pnl), 2),
            "realized_pnl": round(float(row.realized_pnl), 2),
            "total_pnl": round(float(row.unrealized_pnl + row.realized_pnl), 2),
        })

    return {
        "interval": interval,
        "hours": hours,
        "data_points": len(history),
        "history": history,
    }

# ── Value at Risk ─────────────────────────────────────────────────────────────

@router.get("/var")
def get_value_at_risk(
    confidence: float = 0.95,
    db: Session = Depends(get_db),
):
    """
    Computes Historical Simulation Value at Risk for the current portfolio.

    Method: For each of the last 252 trading days, we simulate what the
    portfolio would have made or lost if those exact market returns happened
    today (using current position sizes). VaR is the loss at the chosen
    confidence percentile.

    Args:
        confidence: 0.95 (default) or 0.99. Both are returned always.

    Returns full VaR report including:
      - VaR at 95% and 99% confidence (USD and %)
      - Per-symbol standalone VaR breakdown
      - Worst single historical day scenario
      - Number of trading days used in calculation
    """
    # Validate confidence
    if confidence not in (0.95, 0.99):
        raise HTTPException(
            status_code=400,
            detail="confidence must be 0.95 or 0.99",
        )

    r = _get_redis()

    # Load open positions
    positions = db.query(Position).filter(Position.status == "OPEN").all()
    if not positions:
        raise HTTPException(status_code=400, detail="No open positions.")

    # Build position_values: {symbol: current_dollar_value}
    position_values: dict[str, float] = {}
    for p in positions:
        price, is_stale = _read_price(r, p.symbol)
        if price is None:
            continue
        position_values[p.symbol] = round(price * p.quantity, 2)

    if not position_values:
        raise HTTPException(
            status_code=400,
            detail="No live prices available yet. Wait 5 seconds and retry.",
        )

    symbols = list(position_values.keys())

    # Fetch historical returns (Redis-cached, falls back to yfinance)
    historical_returns, dates = get_historical_returns(
        symbols=symbols,
        redis_client=r,
    )

    if not historical_returns:
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not fetch historical price data from Yahoo Finance. "
                "This may happen outside market hours or on weekends. "
                "Try again later."
            ),
        )

    # Build full report — pure engine call, no infrastructure
    report = build_var_report(
        position_values=position_values,
        historical_returns=historical_returns,
        dates=dates,
        confidence_levels=[0.95, 0.99],
    )

    # Write to AuditLog
    db.add(AuditLog(
        event_type="VAR_CALCULATED",
        symbol=None,
        description=(
            f"VaR calculated. Portfolio: ${report.get('portfolio_value', 0):,.2f}. "
            f"Days used: {report.get('trading_days_used', 0)}. "
            f"VaR 95%: {report.get('confidence_levels', {}).get('95%', {}).get('var_1day_usd', 'N/A')}."
        ),
    ))
    db.commit()

    return report


# ── WebSocket Live Feed ───────────────────────────────────────────────────────

@router.websocket("/ws/live")
async def websocket_live_feed(websocket: WebSocket):
    """
    WebSocket endpoint that pushes a portfolio snapshot every 5 seconds.

    Connect once. Data arrives automatically. No polling required.

    How it works:
      1. Client connects to ws://host/risk/ws/live
      2. We create a personal asyncio.Queue for this client
      3. We register the queue in CONNECTED_CLIENTS (main.py)
      4. The price_feed_loop (running in background) puts a snapshot
         into every queue in CONNECTED_CLIENTS after each cycle
      5. We read from our queue and send JSON to the browser
      6. On disconnect (browser closed / network drop), we remove
         our queue from CONNECTED_CLIENTS so the feed stops sending

    Message format:
      {
        "type": "price_update",
        "timestamp": "2026-05-03T09:42:02Z",
        "portfolio_value": 111711.30,
        "total_pnl": 35241.00,
        "total_unrealized": 35241.00,
        "total_realized": 0.00,
        "alert_count": 2,
        "status": "ACTIVE",
        "positions": [
          {
            "symbol": "JPM",
            "current_price": 312.47,
            "current_value": 93741.00,
            "unrealized_pnl": 35241.00,
            "unrealized_pnl_pct": 60.241,
            "is_stale": false
          },
          ...
        ]
      }

    On connect, sends an immediate acknowledgement so the client knows
    the connection is live before the first price cycle fires.
    """
    # Import here to avoid circular import
    # CONNECTED_CLIENTS lives in main.py which imports from this file
    from app.main import CONNECTED_CLIENTS

    await websocket.accept()

    # Create a personal queue for this client
    # maxsize=10: if client falls 10 messages behind, new ones are dropped
    # This prevents unbounded memory growth if a client goes slow
    client_queue: asyncio.Queue = asyncio.Queue(maxsize=10)

    # Register this client
    CONNECTED_CLIENTS.add(client_queue)

    # Send immediate acknowledgement — client knows connection is live
    await websocket.send_json({
        "type": "connected",
        "message": "kapital live feed connected. Updates every 5 seconds.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        while True:
            # Wait for the next snapshot from price_feed_loop
            # timeout=30s: if no data in 30s, send a heartbeat so the
            # connection doesn't get killed by proxies/load balancers
            try:
                snapshot = await asyncio.wait_for(
                    client_queue.get(),
                    timeout=30.0,
                )
                await websocket.send_json(snapshot)

            except asyncio.TimeoutError:
                # No price update in 30 seconds (market closed / weekend)
                # Send heartbeat to keep connection alive
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": "No price updates. Market may be closed.",
                })

    except WebSocketDisconnect:
        # Browser tab closed, network dropped, or client disconnected cleanly
        pass

    except Exception as e:
        # Unexpected error — log it, don't crash the server
        print(f"[ws/live] Unexpected error: {e}")

    finally:
        # Always remove this client's queue on any exit path
        # Without this, the price_feed_loop keeps trying to send
        # to a dead queue forever
        CONNECTED_CLIENTS.discard(client_queue)