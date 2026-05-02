import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from app.db.database import init_db
from app.api.positions import router as positions_router
from app.api.risk import router as risk_router   # ✅ added risk router import
from app.services.price_feed import price_feed_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    task = asyncio.create_task(price_feed_loop())
    yield
    # Shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="kapital — Portfolio Risk Engine",
    description="Real-time P&L, risk rules, and stress testing for a 15-stock portfolio.",
    version="0.3.0",   # ✅ bumped version
    lifespan=lifespan,
)

# Routers
app.include_router(positions_router)
app.include_router(risk_router)   # ✅ added risk router


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.3.0"}   # ✅ updated version
