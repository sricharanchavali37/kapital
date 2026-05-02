\# kapital — Portfolio Risk Engine



A backend system that tracks a stock portfolio in real time, calculates 

profit and loss every 5 seconds, and automatically enforces risk rules 

that protect the portfolio from dangerous positions.



This is exactly what a risk desk at a quantitative hedge fund builds. 

This is a simplified but genuine version of it.



\---



\## The Problem This Solves



Imagine you own $64,000 worth of stocks across 15 companies. Prices 

change every few seconds. Without a system:



\- You don't know your current profit or loss in real time

\- You don't know if one stock has grown so large it dominates your 

&#x20; portfolio dangerously

\- You don't know if you're losing so much today that you should stop 

&#x20; trading entirely

\- You cannot ask "what happens to my money if all Banking stocks 

&#x20; crash 20% right now"



kapital answers all of these questions automatically, every 5 seconds, 

without any manual calculation.



\---



\## Architecture

┌─────────────────────────────────────┐

&#x20;                   │           YOUR PORTFOLIO             │

&#x20;                   │   JPM, GS, NVDA, AMZN, MSFT ...    │

&#x20;                   └──────────────┬──────────────────────┘

&#x20;                                  │

&#x20;               ┌──────────────────▼──────────────────────┐

&#x20;               │              FastAPI (Python)            │

&#x20;               │                                          │

&#x20;               │  Background loop runs every 5 seconds:  │

&#x20;               │  yfinance → Redis → P\&L → Rules Engine  │

&#x20;               │                                          │

&#x20;               │  REST API:                               │

&#x20;               │  POST/GET/DELETE /positions              │

&#x20;               │  GET  /risk/report                       │

&#x20;               │  POST /risk/stress-test                  │

&#x20;               │  GET  /risk/pnl-history                  │

&#x20;               └────────┬─────────────────┬──────────────┘

&#x20;                        │                 │

&#x20;          ┌─────────────▼───┐     ┌───────▼──────────┐

&#x20;          │   PostgreSQL    │     │      Redis        │

&#x20;          │                 │     │                   │

&#x20;          │ positions       │     │ price:{symbol}    │

&#x20;          │ pnl\_records     │     │ → live prices     │

&#x20;          │ risk\_alerts     │     │                   │

&#x20;          │ audit\_log       │     │ system:status     │

&#x20;          └─────────────────┘     │ → ACTIVE/HALTED   │

&#x20;                                  │                   │

&#x20;                                  │ alert\_cooldown:\*  │

&#x20;                                  │ → spam prevention │

&#x20;                                  └───────────────────┘

&#x20;                                           │

&#x20;                               ┌───────────▼──────────┐

&#x20;                               │   yfinance           │

&#x20;                               │   (Yahoo Finance)    │

&#x20;                               │   Free NYSE/NASDAQ   │

&#x20;                               │   prices             │

&#x20;                               └──────────────────────┘


---



\## How to Run



```bash

git clone https://github.com/sricharanchavali37/kapital.git

cd kapital

docker-compose up --build

```



Open: http://localhost:8000/docs



Every endpoint is visible, every schema is documented, everything is 

runnable from the browser.



\---



\## The 8 Features



\### Feature 1 — Position Management

Track what you own. When you buy 100 JPM at $195, then 100 more at 

$205, the system calculates your true average cost as $200 — not $195, 

not $205. This is called FIFO weighted average cost basis. If this 

number is wrong, every risk calculation built on top of it is also wrong.



Every position change writes a permanent row to the AuditLog. In 

financial systems, you never destroy history. Every trade is traceable 

forever.



\### Feature 2 — Live Price Feed

A background loop runs every 5 seconds. It calls Yahoo Finance for 

current prices of every stock you hold and writes them to Redis 

(in-memory storage). Redis reads take under 1ms. PostgreSQL reads take 

5-20ms. For a value that changes every 5 seconds and is read on every 

API call, this difference matters.



If Yahoo Finance fails — market closed, network issue, rate limit — the 

system keeps the last known price but marks it as stale. Any P\&L 

calculated on stale data includes a data\_warning in the response. Bad 

data flagged honestly is better than no data at all.



\### Feature 3 — Real-Time P\&L Calculation

Every time prices update, the system calculates profit and loss for 

every open position and for the total portfolio. Every calculation is 

written as a timestamped row to the pnl\_records table. This creates a 

time-series — a complete diary of your portfolio's value over time.



P\&L = (Current Price - Average Cost) × Quantity



\### Feature 4 — Risk Rules Engine

Three rules run automatically after every price update:



\*\*Rule 1 — Daily Loss Limit\*\*

If portfolio drops more than 2% from today's opening value, system 

status becomes HALTED. No new positions allowed until tomorrow.



\*\*Rule 2 — Concentration Breach\*\*

If any single stock exceeds 30% of portfolio value, or any single 

sector exceeds 50%, a WARNING alert fires. Putting too many eggs in 

one basket is dangerous.



\*\*Rule 3 — Stop Loss\*\*

If any position drops more than 7% below your average cost, a WARNING 

alert fires. The system taps you on the shoulder before the loss 

gets worse.



Alert cooldown: DAILY\_LOSS\_LIMIT suppresses for 60 minutes after 

firing. CONCENTRATION\_BREACH for 10 minutes. STOP\_LOSS\_HIT for 5 

minutes. A condition that persists for an hour should not generate 

720 identical alerts. This is called alert fatigue prevention.



\### Feature 5 — Sector Breakdown

Groups your positions by industry using a predefined sector map. 

Calculates what percentage of your total portfolio sits in Banking, 

Technology, Semiconductors, Gaming, and Energy. Flags any sector 

above 50% as WARNING.



\### Feature 6 — Risk Report API

One endpoint that returns a complete snapshot of portfolio health:

current value, total P\&L, all active alerts, all open positions with 

their weights, and full sector breakdown. Everything in one call. 

Fresh on every request — no cached data.



\### Feature 7 — Stress Testing

Simulates market crashes without touching real data. Three scenarios:



\- \*\*SECTOR\_CRASH\*\*: What if all Banking stocks drop 20%?

\- \*\*MARKET\_CRASH\*\*: What if everything drops 15%?  

\- \*\*SINGLE\_STOCK\*\*: What if NVDA specifically drops 30%?



The system applies fake prices in memory, recalculates P\&L, runs all 

3 risk rules on the hypothetical state, and returns what would happen. 

Zero real data is changed. One AuditLog entry records that the drill 

was run.



Every risk desk on Wall Street runs these scenarios every morning 

before markets open. It is called scenario analysis.



\### Feature 8 — P\&L History with Benchmark

Queries the pnl\_records time-series and returns portfolio value over 

time at a chosen interval.


GET /risk/pnl-history?interval=5m\&hours=6

Returns one data point per 5-minute window for the last 6 hours.



\---



\## Engineering Decisions



\### Why Redis for prices and not PostgreSQL?

PostgreSQL reads take 5-20ms. Redis reads take under 1ms. Live prices 

are read on every API call, every P\&L calculation, every risk rule 

check — potentially hundreds of times per minute. Redis is purpose-built 

for frequently updated, frequently read, latency-sensitive data.



\### Why does engine/ have pure functions with no infrastructure calls?

`app/engine/pnl.py`, `app/engine/rules.py`, and `app/engine/stress.py` 

contain zero imports from FastAPI, SQLAlchemy, or Redis. They take 

plain Python dicts as input and return plain Python dicts as output.



This means the entire calculation and rules logic is testable with 

plain pytest in under 2 seconds without Docker, PostgreSQL, or Redis 

running. Business logic that is coupled to infrastructure cannot be 

tested in isolation. Business logic that is not coupled to 

infrastructure can be tested anywhere.



\### Why is AuditLog append-only, never updated or deleted?

In financial systems, regulators require that every position change, 

every rule breach, and every significant action is permanently 

traceable. Deleting or modifying audit records is a compliance 

violation. The AuditLog table only ever receives INSERT statements. 

It will never see an UPDATE or DELETE.



\### Why does alert cooldown exist?

A risk condition that persists for one hour — say, a stock that 

remains below the stop loss threshold — would generate 720 identical 

alerts at a 5-second check interval. Nobody reads 720 identical alerts. 

The cooldown ensures each alert fires once, stays quiet during the 

cooldown window, and fires again only if the condition still exists 

after the window expires.



\---



\## Benchmark



\*\*Query:\*\* P\&L history for last 6 hours, bucketed into 5-minute intervals  

\*\*Table:\*\* pnl\_records with composite index on (calculated\_at, symbol)

Table size during testing:  \~500 rows (few hours of weekend data)

Without index:              13.4 ms

With index:                 14.4 ms



The difference is not visible at this table size. At a full trading day 

of data (\~28,800 rows written every 5 seconds), the expected improvement 

based on PostgreSQL B-tree index behavior on timestamp range queries is 

600-800ms without the index down to 3-5ms with it.



The index is kept in production because the benefit compounds as data 

accumulates daily. An index that does nothing on day one does a great 

deal on day thirty.



\---



\## A Failure Story



\*\*The problem:\*\* Two Redis instances running on the same machine.



When I ran `docker-compose up -d db redis`, the Redis container started 

but had no port mapping — meaning only other Docker containers could 

reach it. My locally running FastAPI process was connecting to 

`localhost:6379`, which was PRPulse's Redis (a separate project also 

running on the same machine), not kapital's Redis.



The symptom was silent and confusing. The price feed appeared to work 

— no errors, prices were being written. But the data was going into 

the wrong Redis. When kapital's API read `price:{symbol}`, it found 

nothing and returned `pnl: null` on every request.



\*\*What I tried first:\*\* Restarting the server. Clearing the database. 

Checking the code for bugs in the Redis key names. None of it helped 

because the problem was not in the code — it was in the infrastructure.



\*\*How I found it:\*\* Running `docker ps` and reading the PORTS column 

carefully. PRPulse's Redis showed `0.0.0.0:6379->6379/tcp`. kapital's 

Redis showed nothing in the PORTS column — no external mapping at all.



\*\*What fixed it:\*\* Added an explicit port mapping to docker-compose.yml, 

but on port 6380 instead of 6379 to avoid the collision:



```yaml

redis:

&#x20; image: redis:7

&#x20; ports:

&#x20;   - "6380:6379"

```



Updated the `.env` file to point to `redis://localhost:6380`. 

The fix took two lines. Finding it took longer.



\*\*What this taught me:\*\* Silent failures in distributed systems are 

harder than loud ones. An error message tells you where to look. 

A system that appears to work but produces wrong results requires you 

to question every assumption about the infrastructure, not just the code.



\---



\## Tech Stack



| Tool | Purpose |

|------|---------|

| Python 3.11 | Language |

| FastAPI | REST API framework |

| PostgreSQL | Positions, P\&L history, audit log |

| Redis | Live prices, alert cooldown, system status |

| yfinance | Free real NYSE/NASDAQ prices |

| Docker Compose | One command local setup |

| Railway.app | Free cloud deployment |



\---



\## Portfolio Universe



15 stocks across 5 sectors — all real NYSE/NASDAQ companies:



| Sector | Symbols |

|--------|---------|

| Banking | JPM, GS, WFC, BAC |

| Technology | AMZN, MSFT, AAPL, GOOGLE |

| Semiconductors | NVDA, AMD, QCOM |

| Gaming | EA, TTWO |

| Energy | XOM, CVX |

