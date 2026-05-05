import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import Base

load_dotenv()

_raw_url = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/riskengine"
)

# Railway provides URLs starting with "postgres://" (without ql)
# SQLAlchemy requires "postgresql://"
# This one-line fix handles both Railway and local setups
DATABASE_URL = _raw_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # auto-reconnect on stale connections
    pool_size=20,         # increased from default 10 (load test finding)
    max_overflow=40,      # increased from default 20 (load test finding)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()