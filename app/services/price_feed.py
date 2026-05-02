import asyncio
import json
import os
from datetime import datetime, timezone

import yfinance as yf
import redis.asyncio as aioredis
from dotenv import load_dotenv

from app.db.database import SessionLocal
from app.db.models import Position

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380")
FEED_INTERVAL = 5        # seconds between each fetch
STALE_THRESHOLD = 30     # seconds before a price is considered stale


def get_open_symbols() -> list[str]:
    """Read all open position symbols from Postgres."""
    db = SessionLocal()
    try:
        rows = db.query(Position.symbol).filter(Position.status == "OPEN").all()
        return [r.symbol for r in rows]
    finally:
        db.close()


def fetch_prices(symbols: list[str]) -> dict[str, float | None]:
    """
    Call yfinance for a batch of symbols.
    Returns {symbol: price} — price is None if fetch failed.
    """
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


async def price_feed_loop():
    """
    Background loop — runs every 5 seconds.
    Fetches prices for all open positions, writes to Redis.
    Marks stale if fetch failed.
    """
    r = aioredis.from_url(REDIS_URL, decode_responses=True)

    print("[price_feed] Started.")

    while True:
        try:
            symbols = get_open_symbols()

            if not symbols:
                await asyncio.sleep(FEED_INTERVAL)
                continue

            prices = fetch_prices(symbols)
            now = datetime.now(timezone.utc).isoformat()

            for symbol, price in prices.items():
                is_stale = price is None

                # If fetch failed, try to keep last known price but mark stale
                if is_stale:
                    existing_raw = await r.get(f"price:{symbol}")
                    if existing_raw:
                        existing = json.loads(existing_raw)
                        existing["is_stale"] = True
                        existing["fetched_at"] = now
                        await r.set(f"price:{symbol}", json.dumps(existing))
                    # No previous price either — skip
                    continue

                payload = {
                    "symbol":     symbol,
                    "price":      price,
                    "fetched_at": now,
                    "is_stale":   False,
                }
                await r.set(f"price:{symbol}", json.dumps(payload))

            print(f"[price_feed] Updated {len(prices)} symbols. {now}")

        except Exception as e:
            print(f"[price_feed] Error: {e}")

        await asyncio.sleep(FEED_INTERVAL)