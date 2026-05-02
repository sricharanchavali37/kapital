"""
Handles two things:
1. Cooldown check via Redis — should this alert fire or is it suppressed?
2. Persist alert to Postgres RiskAlert table if it passes cooldown.
"""

import json
import os
from datetime import datetime, timezone

import redis
from dotenv import load_dotenv

from app.db.database import SessionLocal
from app.db.models import RiskAlert, AuditLog
from app.config import (
    COOLDOWN_DAILY_LOSS,
    COOLDOWN_CONCENTRATION,
    COOLDOWN_STOP_LOSS,
)

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380")

COOLDOWN_MAP = {
    "DAILY_LOSS_LIMIT":     COOLDOWN_DAILY_LOSS,
    "CONCENTRATION_BREACH": COOLDOWN_CONCENTRATION,
    "STOP_LOSS_HIT":        COOLDOWN_STOP_LOSS,
}


def _cooldown_key(rule_name: str, symbol: str | None) -> str:
    """Redis key for cooldown. Symbol=None means portfolio-level."""
    sym = symbol or "PORTFOLIO"
    return f"alert_cooldown:{rule_name}:{sym}"


def process_alerts(alerts: list[dict], system_status: dict):
    """
    For each alert:
    1. Check Redis cooldown key — if exists, suppress (already fired recently)
    2. If not suppressed → save to RiskAlert table + AuditLog + set cooldown key
    3. If DAILY_LOSS_LIMIT fires → set system status to HALTED in Redis
    """
    if not alerts:
        return

    r = redis.from_url(REDIS_URL, decode_responses=True)
    db = SessionLocal()

    try:
        for alert in alerts:
            rule    = alert["rule_name"]
            symbol  = alert.get("symbol")
            cooldown_key = _cooldown_key(rule, symbol)

            # Check cooldown — if key exists in Redis, skip this alert
            if r.exists(cooldown_key):
                continue

            # Save to RiskAlert table
            db.add(RiskAlert(
                rule_name    = rule,
                symbol       = symbol,
                message      = alert["message"],
                severity     = alert["severity"],
                last_fired_at= datetime.now(timezone.utc),
                is_active    = True,
            ))

            # Save to AuditLog
            db.add(AuditLog(
                event_type  = "RULE_BREACHED",
                symbol      = symbol,
                description = alert["message"],
            ))

            # Set cooldown key with TTL
            ttl = COOLDOWN_MAP.get(rule, 300)
            r.setex(cooldown_key, ttl, "1")

            print(f"[alert] {alert['severity']} — {alert['message']}")

            # Rule 1 specifically → HALT the system
            if rule == "DAILY_LOSS_LIMIT":
                r.set("system:status", "HALTED")
                system_status["halted"] = True
                print("[alert] System status → HALTED")

        db.commit()

    except Exception as e:
        db.rollback()
        print(f"[alert_service] Error: {e}")
    finally:
        db.close()


def get_system_status() -> str:
    """Returns ACTIVE or HALTED."""
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        status = r.get("system:status")
        return status if status else "ACTIVE"
    except Exception:
        return "ACTIVE"


def reset_system_status():
    """Call this at start of each trading day."""
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.set("system:status", "ACTIVE")
    except Exception:
        pass