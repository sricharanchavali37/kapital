import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from app.db.database import init_db
from app.api.positions import router as positions_router
from app.api.risk import router as risk_router
from app.services.price_feed import price_feed_loop

# ── Shared pub/sub state ──────────────────────────────────────────────────────
#
# CONNECTED_CLIENTS is a set of asyncio.Queue objects.
# One queue per connected WebSocket client.
#
# price_feed_loop holds a reference to this set and puts a snapshot
# into every queue at the end of each 5-second cycle.
#
# The WebSocket handler in api/risk.py adds its queue on connect
# and removes it on disconnect.
#
# Why a set of queues instead of one shared queue?
#   asyncio.Queue is single-consumer — get() removes the item.
#   If 3 browsers are connected and we use one queue, only 1 browser
#   gets each message. With one queue per client, all 3 get every update.
#
# Why not Redis pub/sub?
#   We already have Redis. But adding a Redis subscription channel
#   for something that lives entirely within one process is unnecessary
#   infrastructure. asyncio.Queue is in-process, zero latency, zero cost.
#
CONNECTED_CLIENTS: set[asyncio.Queue] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    # Pass CONNECTED_CLIENTS into the price feed so it can broadcast
    task = asyncio.create_task(price_feed_loop(CONNECTED_CLIENTS))
    yield
    # Shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="kapital — Portfolio Risk Engine",
    description="Real-time P&L, risk rules, stress testing, and live WebSocket feed.",
    version="0.4.0",
    lifespan=lifespan,
)

# Routers
app.include_router(positions_router)
app.include_router(risk_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.4.0",
        "live_clients": len(CONNECTED_CLIENTS),
    }
