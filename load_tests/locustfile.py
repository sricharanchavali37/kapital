"""
kapital — Load Test
====================
Simulates 100 concurrent users hitting the API simultaneously.
Measures response times, throughput, and error rates.

HOW TO RUN
----------
1. Make sure Docker is running: docker-compose up
2. Make sure you have open positions (JPM, NVDA, AMZN)
3. Activate venv: venv\\Scripts\\activate
4. Install locust if not already: pip install locust
5. Run headless (no browser UI, results in terminal):

   locust -f load_tests/locustfile.py --headless -u 100 -r 10 --run-time 60s --host http://localhost:8100

   Flags:
     -u 100       → spawn 100 total users
     -r 10        → spawn 10 new users per second (ramp-up)
     --run-time   → run for 60 seconds then stop
     --host       → your kapital API

6. For a visual dashboard in browser (optional):

   locust -f load_tests/locustfile.py --host http://localhost:8100

   Then open: http://localhost:8089

WHAT TO RECORD IN README
-------------------------
After the run, record these numbers from the terminal output:
  - Requests/second (RPS) — how many requests your API handled per second
  - p50 response time     — median latency
  - p95 response time     — 95th percentile (most important number)
  - p99 response time     — worst 1% of requests
  - Failure rate          — must be 0% for all endpoints

EXPECTED RESULTS (approximate, market hours)
----------------------------------------------
  GET /risk/report      → p95 under 50ms   (reads Redis + Postgres)
  GET /positions/       → p95 under 30ms   (reads Redis + Postgres)
  GET /risk/pnl-history → p95 under 20ms   (Postgres index query)
  GET /risk/var         → p95 under 500ms  (first call: yfinance + compute)
                          p95 under 50ms   (subsequent: Redis cache hit)
  POST /risk/stress-test→ p95 under 100ms  (in-memory calculation)
"""

import random
from locust import HttpUser, task, between


# Symbols we know exist in the portfolio
# If you added different symbols, update this list
PORTFOLIO_SYMBOLS = ["JPM", "NVDA", "AMZN"]

# Stress test scenarios to rotate through
STRESS_SCENARIOS = [
    {"scenario_type": "SECTOR_CRASH",  "target": "Banking",       "shock_pct": -15},
    {"scenario_type": "SECTOR_CRASH",  "target": "Semiconductors","shock_pct": -20},
    {"scenario_type": "MARKET_CRASH",  "target": None,            "shock_pct": -10},
    {"scenario_type": "SINGLE_STOCK",  "target": "JPM",           "shock_pct": -25},
    {"scenario_type": "SINGLE_STOCK",  "target": "NVDA",          "shock_pct": -30},
]


class KapitalUser(HttpUser):
    """
    Simulates one user of the kapital risk engine.
    Each user waits 0.1 to 0.5 seconds between requests —
    representing a dashboard refreshing aggressively.

    100 users × average 0.3s wait = ~333 requests/second theoretical max.
    Real throughput will be lower due to API processing time.
    """
    wait_time = between(0.1, 0.5)

    # ── Read-heavy endpoints (high weight) ───────────────────────────────────

    @task(4)
    def get_risk_report(self):
        """
        The most important endpoint.
        Every dashboard, every monitoring tool calls this.
        Weight 4 = fires most frequently.
        """
        with self.client.get(
            "/risk/report",
            catch_response=True,
            name="GET /risk/report",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(
                    f"Expected 200, got {response.status_code}: {response.text[:100]}"
                )

    @task(3)
    def get_all_positions(self):
        """
        Position list — called by dashboards to render the portfolio table.
        """
        with self.client.get(
            "/positions/",
            catch_response=True,
            name="GET /positions/",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Expected 200, got {response.status_code}")

    @task(2)
    def get_pnl_history(self):
        """
        P&L chart data. Tests the Postgres time-series query with index.
        This is where the 680ms → 4ms benchmark shows up under load.
        """
        with self.client.get(
            "/risk/pnl-history?interval=5m&hours=6",
            catch_response=True,
            name="GET /risk/pnl-history",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Expected 200, got {response.status_code}")

    @task(2)
    def get_single_position(self):
        """
        Per-symbol drill-down. Rotates through known portfolio symbols.
        """
        symbol = random.choice(PORTFOLIO_SYMBOLS)
        with self.client.get(
            f"/positions/{symbol}",
            catch_response=True,
            name="GET /positions/{symbol}",  # grouped in Locust output
        ) as response:
            if response.status_code in (200, 404):
                # 404 is acceptable — position may not exist yet
                response.success()
            else:
                response.failure(f"Unexpected {response.status_code}")

    # ── Compute-heavy endpoints (lower weight) ────────────────────────────────

    @task(2)
    def post_stress_test(self):
        """
        Stress test — in-memory calculation, no DB write except AuditLog.
        Rotates through all scenario types to test different code paths.
        """
        scenario = random.choice(STRESS_SCENARIOS)
        with self.client.post(
            "/risk/stress-test",
            json=scenario,
            catch_response=True,
            name="POST /risk/stress-test",
        ) as response:
            if response.status_code in (200, 400):
                # 400 acceptable — no positions open edge case
                response.success()
            else:
                response.failure(f"Unexpected {response.status_code}: {response.text[:100]}")

    @task(1)
    def get_var(self):
        """
        VaR endpoint — weight 1 (lowest frequency).
        First call: fetches from yfinance (~2-5 seconds).
        Subsequent calls: Redis cache hit (~50ms).

        This tests that the Redis cache works correctly under concurrent load.
        If 100 users all call this simultaneously, only the first
        should hit yfinance. The other 99 should get Redis cache.
        """
        with self.client.get(
            "/risk/var?confidence=0.95",
            catch_response=True,
            name="GET /risk/var",
            timeout=30,  # yfinance can be slow on first call
        ) as response:
            if response.status_code in (200, 400, 503):
                # 503 acceptable — yfinance unavailable outside market hours
                response.success()
            else:
                response.failure(f"Unexpected {response.status_code}")

    # ── Health check (always runs) ────────────────────────────────────────────

    @task(1)
    def health_check(self):
        """
        Health endpoint — lightweight, always available.
        Also shows live_clients count which should stay stable under load.
        """
        with self.client.get(
            "/health",
            catch_response=True,
            name="GET /health",
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("status") != "ok":
                    response.failure("Health status is not ok")
                else:
                    response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")