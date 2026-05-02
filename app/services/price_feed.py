import asyncio
import json
import os
from datetime import datetime, timezone

import yfinance as yf
import redis.asyncio as aioredis
from dotenv import load_dotenv

from app.db.database import SessionLocal
from app.db.models import Position, PnLRecord
from app.engine.pnl import calculate_position_pnl, calculate_portfolio_pnl
from app.engine.rules import run_all_rules
from app.services.alert_service import process_alerts
from app.config import SECTOR_MAP

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380")
FEED_INTERVAL = 5
STALE_THRESHOLD = 30


def get_open_positions() -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.query(Position).filter(Position.status == "OPEN").all()
        return [
            {
                "symbol":   p.symbol,
                "quantity": p.quantity,
                "avg_cost": p.avg_cost,
            }
            for p in rows
        ]
    finally:
        db.close()


def fetch_prices(symbols: list[str]) -> dict[str, float | None]:
    if not symbols:
        return {}
    try:
        tickers = yf.Tickers(" ".join(symbols))
        result = {}
        for symbol in symbols:
            try:
                price = tickers.tickers[symbol].fast_info.last_price
                result[symbol] = float(price) if price else None
            except Exception:
                result[symbol] = None
        return result
    except Exception:
        return {s: None for s in symbols}


def save_pnl_records(position_pnls: list[dict], portfolio_pnl: dict):
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        for p in position_pnls:
            db.add(PnLRecord(
                symbol          = p["symbol"],
                unrealized_pnl  = p["unrealized_pnl"],
                realized_pnl    = p["realized_pnl"],
                portfolio_value = p["current_value"],
                calculated_at   = now,
            ))
        db.add(PnLRecord(
            symbol          = None,
            unrealized_pnl  = portfolio_pnl["total_unrealized"],
            realized_pnl    = portfolio_pnl["total_realized"],
            portfolio_value = portfolio_pnl["portfolio_value"],
            calculated_at   = now,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[pnl_record] Failed to save: {e}")
    finally:
        db.close()


async def price_feed_loop():
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    print("[price_feed] Started.")
    system_status = {"halted": False}

    while True:
        try:
            positions = get_open_positions()

            if not positions:
                await asyncio.sleep(FEED_INTERVAL)
                continue

            symbols = [p["symbol"] for p in positions]
            prices  = fetch_prices(symbols)
            now     = datetime.now(timezone.utc).isoformat()

            # ── Step 1: Write prices to Redis ─────────────────────────────
            for symbol, price in prices.items():
                is_stale = price is None
                if is_stale:
                    existing_raw = await r.get(f"price:{symbol}")
                    if existing_raw:
                        existing = json.loads(existing_raw)
                        existing["is_stale"]   = True
                        existing["fetched_at"] = now
                        await r.set(f"price:{symbol}", json.dumps(existing))
                    continue
                await r.set(f"price:{symbol}", json.dumps({
                    "symbol":     symbol,
                    "price":      price,
                    "fetched_at": now,
                    "is_stale":   False,
                }))

            # ── Step 2: Calculate P&L ─────────────────────────────────────
            position_pnls = []
            for p in positions:
                symbol = p["symbol"]
                price  = prices.get(symbol)
                if price is None:
                    raw = await r.get(f"price:{symbol}")
                    if raw:
                        price    = json.loads(raw)["price"]
                        is_stale = True
                    else:
                        continue
                is_stale = prices.get(symbol) is None
                pnl = calculate_position_pnl(
                    symbol        = symbol,
                    quantity      = p["quantity"],
                    avg_cost      = p["avg_cost"],
                    current_price = price,
                    is_stale      = is_stale,
                )
                position_pnls.append(pnl)

            if not position_pnls:
                await asyncio.sleep(FEED_INTERVAL)
                continue

            portfolio_pnl = calculate_portfolio_pnl(position_pnls)

            # ── Step 3: Save P&L to Postgres ──────────────────────────────
            save_pnl_records(position_pnls, portfolio_pnl)

            # ── Step 4: Set opening value if not set today ─────────────────
            open_value_raw = await r.get("portfolio:open_value")
            if open_value_raw is None:
                await r.set(
                    "portfolio:open_value",
                    portfolio_pnl["portfolio_value"]
                )
                open_value = portfolio_pnl["portfolio_value"]
            else:
                open_value = float(open_value_raw)

            # ── Step 5: Run rules engine ───────────────────────────────────
            alerts = run_all_rules(
                position_pnls = position_pnls,
                portfolio_pnl = portfolio_pnl,
                open_value    = open_value,
                sector_map    = SECTOR_MAP,
            )
            process_alerts(alerts, system_status)

            print(
                f"[price_feed] Updated {len(position_pnls)} symbols. "
                f"Portfolio: ${portfolio_pnl['portfolio_value']:,.2f} | "
                f"P&L: ${portfolio_pnl['total_pnl']:,.2f} | "
                f"Alerts: {len(alerts)} | "
                f"{now}"
            )

        except Exception as e:
            print(f"[price_feed] Error: {e}")

        await asyncio.sleep(FEED_INTERVAL)