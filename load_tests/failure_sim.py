"""
kapital — Failure Simulation
==============================
Kills Redis while the API is under load. Observes how kapital degrades.
Then restarts Redis and verifies recovery.

This script tests the most important property of a production system:
  "What happens when something breaks?"

Expected behavior:
  1. Redis dies
  2. Price feed loop: can't write new prices → logs error
  3. GET /risk/report: reads stale prices from last known state
                       returns data_warning: true in response
  4. GET /risk/var: Redis cache miss → tries yfinance → slower but works
  5. POST /positions: still works (reads/writes Postgres only)
  6. System does NOT crash. It degrades gracefully.
  7. Redis restarts → system recovers automatically within 5 seconds

HOW TO RUN
-----------
1. Docker must be running: docker-compose up
2. Open a SECOND CMD window (keep Docker running in the first)
3. Activate venv: venv\\Scripts\\activate
4. Run: python load_tests/failure_sim.py

WHAT TO WATCH
-------------
Watch the Docker logs in your first CMD window during the test.
You will see the price_feed error messages when Redis is down.
You will see it recover when Redis comes back.

WHAT TO RECORD IN README
-------------------------
  - Did the API return 500 errors when Redis was down? (It should NOT)
  - Did it return data_warning: true? (It SHOULD)
  - How long until recovery after Redis restart? (should be ≤ 10 seconds)
"""

import time
import subprocess
import requests
import json

API_BASE = "http://localhost:8100"
REDIS_CONTAINER = "kapital-redis-1"


def log(msg: str):
    print(f"\n[failure_sim] {msg}")


def call_risk_report() -> dict:
    """Hit /risk/report and return the response."""
    try:
        r = requests.get(f"{API_BASE}/risk/report", timeout=5)
        return {"status_code": r.status_code, "body": r.json()}
    except requests.exceptions.ConnectionError:
        return {"status_code": "CONNECTION_ERROR", "body": None}
    except Exception as e:
        return {"status_code": "ERROR", "body": str(e)}


def call_positions() -> dict:
    """Hit /positions/ — should always work (Postgres only)."""
    try:
        r = requests.get(f"{API_BASE}/positions/", timeout=5)
        return {"status_code": r.status_code, "count": len(r.json())}
    except Exception as e:
        return {"status_code": "ERROR", "body": str(e)}


def call_stress_test() -> dict:
    """Hit /risk/stress-test — in-memory, should survive Redis loss."""
    try:
        r = requests.post(
            f"{API_BASE}/risk/stress-test",
            json={"scenario_type": "MARKET_CRASH", "target": None, "shock_pct": -15},
            timeout=5,
        )
        return {"status_code": r.status_code}
    except Exception as e:
        return {"status_code": "ERROR", "body": str(e)}


def docker_stop_redis():
    """Stop the Redis container."""
    result = subprocess.run(
        ["docker", "stop", REDIS_CONTAINER],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        log(f"Redis container STOPPED: {REDIS_CONTAINER}")
    else:
        log(f"Failed to stop Redis: {result.stderr}")


def docker_start_redis():
    """Start the Redis container."""
    result = subprocess.run(
        ["docker", "start", REDIS_CONTAINER],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        log(f"Redis container STARTED: {REDIS_CONTAINER}")
    else:
        log(f"Failed to start Redis: {result.stderr}")


def run_observation_round(label: str):
    """
    Hit all key endpoints and print results.
    Called before kill, during outage, and after recovery.
    """
    log(f"=== {label} ===")

    report = call_risk_report()
    print(f"  /risk/report      → HTTP {report['status_code']}", end="")
    if report["body"] and isinstance(report["body"], dict):
        status = report["body"].get("status", "?")
        pv = report["body"].get("portfolio_value", "?")
        # Check for any stale price warnings in positions
        positions = report["body"].get("positions", [])
        stale_count = sum(
            1 for p in positions
            if isinstance(p, dict) and p.get("data_warning")
        )
        print(f" | status={status} | portfolio=${pv:,.2f}" if isinstance(pv, float)
              else f" | status={status}", end="")
        if stale_count:
            print(f" | ⚠ {stale_count} stale price(s)", end="")
    print()

    positions = call_positions()
    print(f"  /positions/       → HTTP {positions['status_code']}"
          + (f" | {positions.get('count', '?')} positions" if "count" in positions else ""))

    stress = call_stress_test()
    print(f"  /risk/stress-test → HTTP {stress['status_code']}")


def main():
    print("=" * 60)
    print("kapital — Failure Simulation")
    print("Testing graceful degradation when Redis goes down")
    print("=" * 60)

    # ── Phase 1: Baseline ────────────────────────────────────────────────────
    log("Phase 1: Baseline — everything should be working")
    run_observation_round("BEFORE KILL")
    time.sleep(2)

    # ── Phase 2: Kill Redis ──────────────────────────────────────────────────
    log("Phase 2: Killing Redis container NOW")
    docker_stop_redis()

    # Give the system 3 seconds to feel the loss
    log("Waiting 3 seconds for system to detect Redis is gone...")
    time.sleep(3)

    run_observation_round("IMMEDIATELY AFTER KILL")

    # Wait longer — let the price feed loop try and fail a few times
    log("Waiting 10 more seconds — price feed will try and fail multiple times...")
    time.sleep(10)

    run_observation_round("10 SECONDS INTO OUTAGE")

    # ── Phase 3: Restart Redis ───────────────────────────────────────────────
    log("Phase 3: Restarting Redis NOW")
    docker_start_redis()

    log("Waiting 8 seconds for system to reconnect and recover...")
    time.sleep(8)

    run_observation_round("AFTER RECOVERY")

    # One more check after another price feed cycle
    time.sleep(6)
    run_observation_round("FULL RECOVERY CONFIRMED")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Failure simulation complete.")
    print()
    print("What to document in README:")
    print("  1. Did /risk/report return 500 during outage? (should be NO)")
    print("  2. Did data_warning appear during outage?     (should be YES)")
    print("  3. Did /positions/ work during outage?        (should be YES)")
    print("  4. Did /risk/stress-test work during outage?  (should be YES)")
    print("  5. How fast did system recover after restart? (should be ≤10s)")
    print()
    print("Paste your actual observations above into README.md")
    print("=" * 60)


if __name__ == "__main__":
    main()