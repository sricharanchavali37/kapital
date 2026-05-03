"""
Fetches historical daily returns from Yahoo Finance.
Caches results in Redis for 4 hours to avoid hammering yfinance.

Uses yf.Ticker(symbol).history() per symbol — same approach as price_feed.py.
yf.download() is avoided because it uses a different endpoint that fails
inside Docker containers due to Yahoo's rate limiting on batch requests.

Cache key: var_history:{SYMBOL}
Cache TTL: 4 hours (14400 seconds)
"""

import json
import logging
from datetime import datetime

import yfinance as yf
import pandas as pd
import redis

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 14400  # 4 hours
HISTORY_PERIOD    = "1y"   # ~252 trading days


def _cache_key(symbol: str) -> str:
    return f"var_history:{symbol}"


def fetch_single_symbol_history(symbol: str) -> tuple[list[float], list[str]]:
    """
    Fetches 1 year of daily returns for one symbol using yf.Ticker().history().
    This is the same Ticker API that price_feed.py uses successfully.

    Returns:
        (returns_list, dates_list)
        returns as decimals: 0.02 = +2%, -0.015 = -1.5%
        Empty lists if fetch fails.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=HISTORY_PERIOD, interval="1d")

        if hist.empty:
            logger.warning(f"[var_data] Empty history for {symbol}")
            return [], []

        close = hist["Close"].dropna()

        if len(close) < 10:
            logger.warning(f"[var_data] Too few data points for {symbol}: {len(close)}")
            return [], []

        daily_returns = close.pct_change().dropna()

        dates = [
            d.strftime("%Y-%m-%d")
            for d in daily_returns.index.to_list()
        ]
        returns = [round(float(v), 6) for v in daily_returns.to_list()]

        return returns, dates

    except Exception as e:
        logger.error(f"[var_data] Failed to fetch history for {symbol}: {e}")
        return [], []


def fetch_historical_returns_from_yfinance(
    symbols: list[str],
) -> tuple[dict, list[str]]:
    """
    Fetches 1 year of daily returns for all symbols.
    Fetches each symbol individually (avoids batch endpoint failures in Docker).
    Aligns all series to their common dates so correlation is preserved.

    Returns:
        returns_dict: {symbol: [daily_return_day1, day2, ...]}
        dates:        list of date strings aligned to the return lists
    """
    if not symbols:
        return {}, []

    raw: dict[str, tuple[list[float], list[str]]] = {}
    for symbol in symbols:
        returns, dates = fetch_single_symbol_history(symbol)
        if returns:
            raw[symbol] = (returns, dates)
        else:
            logger.warning(f"[var_data] Skipping {symbol} — no data returned")

    if not raw:
        return {}, []

    if len(raw) == 1:
        symbol = list(raw.keys())[0]
        returns, dates = raw[symbol]
        return {symbol: returns}, dates

    # Align to common dates using pandas
    series_map = {}
    for symbol, (returns, dates) in raw.items():
        series_map[symbol] = pd.Series(
            returns,
            index=pd.to_datetime(dates),
            name=symbol,
        )

    df = pd.concat(series_map.values(), axis=1)
    df.columns = list(series_map.keys())
    df = df.dropna(axis=0, how="any")

    if df.empty:
        logger.warning("[var_data] No common dates across symbols after alignment")
        return {}, []

    aligned_dates = [d.strftime("%Y-%m-%d") for d in df.index.to_list()]
    returns_dict = {
        symbol: [round(float(v), 6) for v in df[symbol].to_list()]
        for symbol in df.columns
    }

    return returns_dict, aligned_dates


def get_historical_returns(
    symbols: list[str],
    redis_client: redis.Redis,
) -> tuple[dict, list[str]]:
    """
    Returns historical returns for all symbols.
    Checks Redis cache first. Fetches from yfinance if cache miss.
    """
    if not symbols:
        return {}, []

    cached_returns  = {}
    cached_dates    = None
    cache_miss_syms = []

    for symbol in symbols:
        key = _cache_key(symbol)
        try:
            raw = redis_client.get(key)
            if raw:
                data = json.loads(raw)
                cached_returns[symbol] = data["returns"]
                if cached_dates is None:
                    cached_dates = data.get("dates", [])
            else:
                cache_miss_syms.append(symbol)
        except Exception as e:
            logger.warning(f"[var_data] Cache read failed for {symbol}: {e}")
            cache_miss_syms.append(symbol)

    if not cache_miss_syms:
        return cached_returns, cached_dates or []

    logger.info(f"[var_data] Cache miss: {cache_miss_syms}. Fetching from yfinance.")
    fetched_returns, fetched_dates = fetch_historical_returns_from_yfinance(
        cache_miss_syms
    )

    if not fetched_returns:
        logger.warning("[var_data] yfinance returned nothing. Using cache only.")
        return cached_returns, cached_dates or []

    for symbol, returns in fetched_returns.items():
        key = _cache_key(symbol)
        try:
            payload = json.dumps({"returns": returns, "dates": fetched_dates})
            redis_client.setex(key, CACHE_TTL_SECONDS, payload)
        except Exception as e:
            logger.warning(f"[var_data] Cache write failed for {symbol}: {e}")

    all_returns = {**cached_returns, **fetched_returns}
    all_dates   = fetched_dates if fetched_dates else (cached_dates or [])

    return all_returns, all_dates


def invalidate_cache(symbols: list[str], redis_client: redis.Redis):
    """Force-clear Redis cache for given symbols."""
    for symbol in symbols:
        try:
            redis_client.delete(_cache_key(symbol))
        except Exception:
            pass