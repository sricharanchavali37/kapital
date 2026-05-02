from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.db.database import init_db
from app.api.positions import router as positions_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    # Background loops wired here in Feature 2
    yield
    # Shutdown (nothing to clean up yet)


app = FastAPI(
    title="kapital — Portfolio Risk Engine",
    description="Real-time P&L, risk rules, and stress testing for a 15-stock portfolio.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(positions_router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}