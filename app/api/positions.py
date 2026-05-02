from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from datetime import datetime, timezone

from app.db.database import get_db
from app.db.models import Position, AuditLog
from app.config import SECTOR_MAP
from app.engine.pnl import calculate_avg_cost, calculate_position_pnl

router = APIRouter(prefix="/positions", tags=["positions"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AddPositionRequest(BaseModel):
    symbol:   str   = Field(..., description="Ticker symbol e.g. JPM")
    quantity: float = Field(..., gt=0, description="Number of shares to add")
    price:    float = Field(..., gt=0, description="Price per share at purchase")


class PositionResponse(BaseModel):
    symbol:       str
    sector:       str
    quantity:     float
    avg_cost:     float
    status:       str
    opened_at:    datetime
    current_price:    float | None = None
    current_value:    float | None = None
    unrealized_pnl:   float | None = None
    unrealized_pnl_pct: float | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_live_price(symbol: str) -> tuple[float | None, bool]:
    """
    Attempt to read price from Redis.
    Returns (price, is_stale). Returns (None, True) if Redis unavailable.
    Redis wired in Feature 2. For now returns (None, False) as safe stub.
    """
    try:
        import redis, json, os
        from datetime import timedelta
        r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
        raw = r.get(f"price:{symbol}")
        if raw is None:
            return None, True
        data = json.loads(raw)
        return data["price"], data.get("is_stale", False)
    except Exception:
        return None, True


def _audit(db: Session, event_type: str, symbol: str | None, description: str):
    db.add(AuditLog(event_type=event_type, symbol=symbol, description=description))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/", status_code=status.HTTP_201_CREATED)
def add_position(payload: AddPositionRequest, db: Session = Depends(get_db)):
    symbol = payload.symbol.upper()

    if symbol not in SECTOR_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"{symbol} is not in the tracked universe. Allowed: {list(SECTOR_MAP.keys())}"
        )

    existing: Position | None = db.query(Position).filter(
        Position.symbol == symbol,
        Position.status == "OPEN"
    ).first()

    if existing:
        # Add to existing position → recalculate FIFO avg cost
        new_avg = calculate_avg_cost(
            existing_qty=existing.quantity,
            existing_avg=existing.avg_cost,
            new_qty=payload.quantity,
            new_price=payload.price,
        )
        existing.quantity += payload.quantity
        existing.avg_cost  = new_avg
        db.commit()
        db.refresh(existing)
        position = existing
        event = "POSITION_ADDED"
        desc  = f"Added {payload.quantity} shares of {symbol} @ ${payload.price:.2f}. New avg cost: ${new_avg:.2f}"
    else:
        # New position
        position = Position(
            symbol    = symbol,
            quantity  = payload.quantity,
            avg_cost  = payload.price,
            sector    = SECTOR_MAP[symbol],
            opened_at = datetime.now(timezone.utc),
            status    = "OPEN",
        )
        db.add(position)
        db.commit()
        db.refresh(position)
        event = "POSITION_ADDED"
        desc  = f"Opened new position: {payload.quantity} shares of {symbol} @ ${payload.price:.2f}"

    _audit(db, event, symbol, desc)
    db.commit()

    price, is_stale = _get_live_price(symbol)
    pnl_data = None
    if price:
        pnl_data = calculate_position_pnl(
            symbol=symbol,
            quantity=position.quantity,
            avg_cost=position.avg_cost,
            current_price=price,
            is_stale=is_stale,
        )

    return {
        "symbol":      position.symbol,
        "sector":      position.sector,
        "quantity":    position.quantity,
        "avg_cost":    position.avg_cost,
        "status":      position.status,
        "opened_at":   position.opened_at,
        "pnl":         pnl_data,
    }


@router.get("/")
def get_all_positions(db: Session = Depends(get_db)):
    positions = db.query(Position).filter(Position.status == "OPEN").all()
    result = []
    for p in positions:
        price, is_stale = _get_live_price(p.symbol)
        pnl_data = None
        if price:
            pnl_data = calculate_position_pnl(
                symbol=p.symbol,
                quantity=p.quantity,
                avg_cost=p.avg_cost,
                current_price=price,
                is_stale=is_stale,
            )
        result.append({
            "symbol":   p.symbol,
            "sector":   p.sector,
            "quantity": p.quantity,
            "avg_cost": p.avg_cost,
            "status":   p.status,
            "opened_at": p.opened_at,
            "pnl":      pnl_data,
        })
    return result


@router.get("/{symbol}")
def get_position(symbol: str, db: Session = Depends(get_db)):
    symbol = symbol.upper()
    position = db.query(Position).filter(
        Position.symbol == symbol,
        Position.status == "OPEN"
    ).first()

    if not position:
        raise HTTPException(status_code=404, detail=f"No open position for {symbol}")

    price, is_stale = _get_live_price(symbol)
    pnl_data = None
    if price:
        pnl_data = calculate_position_pnl(
            symbol=symbol,
            quantity=position.quantity,
            avg_cost=position.avg_cost,
            current_price=price,
            is_stale=is_stale,
        )

    return {
        "symbol":   position.symbol,
        "sector":   position.sector,
        "quantity": position.quantity,
        "avg_cost": position.avg_cost,
        "status":   position.status,
        "opened_at": position.opened_at,
        "pnl":      pnl_data,
    }


@router.delete("/{symbol}", status_code=status.HTTP_200_OK)
def close_position(symbol: str, db: Session = Depends(get_db)):
    symbol = symbol.upper()
    position = db.query(Position).filter(
        Position.symbol == symbol,
        Position.status == "OPEN"
    ).first()

    if not position:
        raise HTTPException(status_code=404, detail=f"No open position for {symbol}")

    price, is_stale = _get_live_price(symbol)
    realized_pnl = 0.0
    if price:
        realized_pnl = (price - position.avg_cost) * position.quantity

    position.status = "CLOSED"
    db.commit()

    _audit(
        db, "POSITION_CLOSED", symbol,
        f"Closed {position.quantity} shares of {symbol}. "
        f"Realized P&L: ${realized_pnl:.2f}"
        + (" [stale price]" if is_stale else "")
    )
    db.commit()

    return {
        "symbol":       symbol,
        "status":       "CLOSED",
        "quantity":     position.quantity,
        "avg_cost":     position.avg_cost,
        "close_price":  price,
        "realized_pnl": round(realized_pnl, 2),
        "data_warning": "Price was stale at close time" if is_stale else None,
    }