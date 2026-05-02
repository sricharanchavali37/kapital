SECTOR_MAP = {
    "JPM":  "Banking",
    "GS":   "Banking",
    "WFC":  "Banking",
    "BAC":  "Banking",
    "AMZN": "Technology",
    "MSFT": "Technology",
    "AAPL": "Technology",
    "GOOGL":"Technology",
    "QCOM": "Semiconductors",
    "NVDA": "Semiconductors",
    "AMD":  "Semiconductors",
    "EA":   "Gaming",
    "TTWO": "Gaming",
    "XOM":  "Energy",
    "CVX":  "Energy",
}

# Risk rule thresholds
DAILY_LOSS_LIMIT_PCT      = 2.0    # portfolio drops >2% from open value → HALTED
CONCENTRATION_STOCK_PCT   = 30.0   # single stock >30% of portfolio → WARNING
CONCENTRATION_SECTOR_PCT  = 50.0   # single sector >50% of portfolio → WARNING
STOP_LOSS_PCT             = 7.0    # position down >7% from avg_cost → WARNING

# Alert cooldown windows (seconds)
COOLDOWN_DAILY_LOSS       = 3600   # 60 minutes
COOLDOWN_CONCENTRATION    = 600    # 10 minutes
COOLDOWN_STOP_LOSS        = 300    # 5 minutes

# Price feed
PRICE_FEED_INTERVAL_SEC   = 5
STALE_PRICE_THRESHOLD_SEC = 30