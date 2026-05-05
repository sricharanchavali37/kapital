import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv
from prometheus_fastapi_instrumentator import Instrumentator

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
CONNECTED_CLIENTS: set[asyncio.Queue] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(price_feed_loop(CONNECTED_CLIENTS))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="kapital — Portfolio Risk Engine",
    description="Real-time P&L, risk rules, stress testing, VaR, and live WebSocket feed.",
    version="0.5.0",
    lifespan=lifespan,
)

# ── Prometheus metrics — instruments every endpoint automatically ─────────────
#
# This single call does three things:
#   1. Tracks request count per endpoint, method, and status code
#   2. Tracks request duration as a histogram (gives us p50, p95, p99)
#   3. Exposes /metrics endpoint that Prometheus scrapes every 15 seconds
#
# No changes needed in any endpoint code. Everything is automatic.
#
Instrumentator(
    should_group_status_codes=False,   # track 200, 400, 500 separately
    should_ignore_untemplated=True,    # ignore one-off URLs that aren't routes
    should_respect_env_var=False,
    should_instrument_requests_inprogress=True,  # track in-flight requests
    inprogress_labels=True,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# Routers
app.include_router(positions_router)
app.include_router(risk_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.5.0",
        "live_clients": len(CONNECTED_CLIENTS),
    }
