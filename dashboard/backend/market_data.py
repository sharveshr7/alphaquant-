"""Market data provider using Binance USDⓈ-M Futures public endpoints with fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("market_data")

# Official Binance USDⓈ-M Futures Base Endpoints
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com/fapi/v1"
BINANCE_BASE_URL = BINANCE_FUTURES_BASE_URL
BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/market/stream"

# In-memory caches
_klines_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_all_tickers_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_all_futures_tickers_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_exchange_info_cache: tuple[float, dict[str, Any]] = (0.0, {})
_premium_index_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])

# Default 10 core pairs
DEFAULT_PAIR_MAP: dict[str, str] = {
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
    "SOL/USDT": "SOLUSDT",
    "BNB/USDT": "BNBUSDT",
    "DOGE/USDT": "DOGEUSDT",
    "XRP/USDT": "XRPUSDT",
    "ADA/USDT": "ADAUSDT",
    "AVAX/USDT": "AVAXUSDT",
    "LINK/USDT": "LINKUSDT",
    "NEAR/USDT": "NEARUSDT",
}

# Dynamic mapping updated via refresh_available_pairs()
PAIR_MAP: dict[str, str] = dict(DEFAULT_PAIR_MAP)

# Mutable list of available pairs updated in-place
AVAILABLE_PAIRS: list[str] = list(PAIR_MAP.keys())

TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"]


def normalize_symbol(pair: str) -> str:
    """Normalizes pair string (e.g. 'BTC/USDT', 'btc-usdt', 'BTCUSDT') to Binance Futures symbol."""
    if pair in PAIR_MAP:
        return PAIR_MAP[pair]
    cleaned = pair.replace("/", "").replace("-", "").upper()
    return cleaned


async def refresh_available_pairs() -> list[str]:
    """Dynamically discover eligible perpetual trading pairs from Binance Futures exchangeInfo
    and update PAIR_MAP and AVAILABLE_PAIRS in-place.
    """
    global PAIR_MAP, AVAILABLE_PAIRS
    info = await get_exchange_info()
    symbols = info.get("symbols", [])
    if not symbols:
        return AVAILABLE_PAIRS

    new_map: dict[str, str] = {}
    for s in symbols:
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
        ):
            base = s.get("baseAsset", "")
            quote = s.get("quoteAsset", "")
            sym = s.get("symbol", "")
            pair_name = f"{base}/{quote}"
            new_map[pair_name] = sym

    if new_map:
        # Keep original defaults prioritized/guaranteed
        for k, v in DEFAULT_PAIR_MAP.items():
            if k not in new_map:
                new_map[k] = v

        PAIR_MAP.clear()
        PAIR_MAP.update(new_map)

        AVAILABLE_PAIRS.clear()
        AVAILABLE_PAIRS.extend(list(PAIR_MAP.keys()))
        logger.info(f"Refreshed AVAILABLE_PAIRS: {len(AVAILABLE_PAIRS)} pairs loaded")

    return AVAILABLE_PAIRS


async def get_klines(
    pair: str = "BTC/USDT",
    interval: str = "15m",
    limit: int = 250,
    end_time: int | None = None,
    start_time: int | None = None,
) -> list[dict]:
    """Fetch candlestick OHLCV data with TTL cache and fallback protection."""
    symbol = normalize_symbol(pair)
    cache_key = f"{symbol}_{interval}_{limit}_{end_time}_{start_time}"
    now = time.time()
    ttl = 300.0 if end_time is not None else 3.0

    if cache_key in _klines_cache:
        c_time, c_data = _klines_cache[cache_key]
        if now - c_time < ttl and c_data:
            return c_data

    url = f"{BINANCE_BASE_URL}/klines"
    params: dict = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(limit, 1000),
    }
    if end_time is not None:
        end_ms = int(end_time * 1000) if end_time < 10_000_000_000 else int(end_time)
        params["endTime"] = end_ms
    if start_time is not None:
        start_ms = int(start_time * 1000) if start_time < 10_000_000_000 else int(start_time)
        params["startTime"] = start_ms

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            raw_klines = response.json()

        candles = []
        for k in raw_klines:
            candles.append(
                {
                    "time": int(k[0] // 1000),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
            )
        if candles:
            _klines_cache[cache_key] = (now, candles)
        return candles
    except Exception as e:
        logger.warning(f"Failed to fetch klines for {symbol} ({interval}): {e}")
        if cache_key in _klines_cache:
            return _klines_cache[cache_key][1]
        return []


async def get_historical_klines_paginated(
    pair: str = "BTC/USDT",
    interval: str = "15m",
    days: int = 365,
    max_candles: int = 15000,
) -> list[dict]:
    """Fetches up to `days` of historical candlestick data through chunked pagination.
    
    Binance returns up to 1000 candles per call. This function iteratively paginates backwards.
    """
    now_sec = int(time.time())
    target_start_sec = now_sec - (days * 86400)

    # Interval in seconds estimate
    tf_multipliers = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14400,
        "6h": 21600,
        "12h": 43200,
        "1d": 86400,
        "1w": 604800,
        "1M": 2592000,
    }
    step_sec = tf_multipliers.get(interval, 900)
    expected_candles = int((now_sec - target_start_sec) / step_sec)
    total_to_fetch = min(expected_candles, max_candles)

    # First fetch latest 1000
    chunk = await get_klines(pair, interval, limit=1000)
    if not chunk:
        return []
    all_candles = chunk

    # Iteratively paginate backwards
    while len(all_candles) < total_to_fetch and all_candles[0]["time"] > target_start_sec:
        oldest_time = all_candles[0]["time"]
        # endTime is exclusive/inclusive in ms: oldest_time * 1000 - 1
        older_chunk = await get_klines(pair, interval, limit=1000, end_time=(oldest_time - 1))
        if not older_chunk:
            break
        # Prepend older chunk
        all_candles = older_chunk + all_candles
        # Safeguard against duplicate or stuck pagination
        if older_chunk[-1]["time"] >= oldest_time:
            break
        # Small throttle to respect rate limits
        await asyncio.sleep(0.05)

    # Deduplicate and sort by time
    unique_candles = {c["time"]: c for c in all_candles}
    sorted_candles = [unique_candles[t] for t in sorted(unique_candles.keys())]
    return sorted_candles


async def get_ticker_24h(pair: str = "BTC/USDT") -> dict:
    """Fetch 24-hour ticker statistics."""
    symbol = normalize_symbol(pair)
    url = f"{BINANCE_BASE_URL}/ticker/24hr"
    params = {"symbol": symbol}

    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    return {
        "pair": pair,
        "symbol": symbol,
        "last_price": float(data.get("lastPrice", 0)),
        "price_change": float(data.get("priceChange", 0)),
        "price_change_percent": float(data.get("priceChangePercent", 0)),
        "high_24h": float(data.get("highPrice", 0)),
        "low_24h": float(data.get("lowPrice", 0)),
        "volume_24h": float(data.get("volume", 0)),
        "quote_volume_24h": float(data.get("quoteVolume", 0)),
        "open_price": float(data.get("openPrice", 0)),
    }


async def get_all_tickers() -> list[dict]:
    """Fetch 24h ticker for all watchlist pairs in 1 fast batch call with 3s cache."""
    global _all_tickers_cache
    now = time.time()
    c_time, c_data = _all_tickers_cache
    if now - c_time < 3.0 and c_data:
        return c_data

    url = f"{BINANCE_BASE_URL}/ticker/24hr"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            raw = res.json()

        raw_map = {item["symbol"]: item for item in raw if "symbol" in item}
        tickers = []
        for p in AVAILABLE_PAIRS:
            sym = normalize_symbol(p)
            data = raw_map.get(sym)
            if data:
                tickers.append(
                    {
                        "pair": p,
                        "symbol": sym,
                        "last_price": float(data.get("lastPrice", 0)),
                        "price_change": float(data.get("priceChange", 0)),
                        "price_change_percent": float(data.get("priceChangePercent", 0)),
                        "high_24h": float(data.get("highPrice", 0)),
                        "low_24h": float(data.get("lowPrice", 0)),
                        "volume_24h": float(data.get("volume", 0)),
                        "quote_volume_24h": float(data.get("quoteVolume", 0)),
                        "open_price": float(data.get("openPrice", 0)),
                    }
                )
        if tickers:
            _all_tickers_cache = (now, tickers)
            return tickers
    except Exception as e:
        logger.warning(f"Error fetching batch tickers: {e}")
        if c_data:
            return c_data
    return []


async def get_all_futures_tickers() -> list[dict]:
    """Fetch 24h ticker for ALL Binance USDⓈ-M Futures pairs."""
    global _all_futures_tickers_cache
    now = time.time()
    c_time, c_data = _all_futures_tickers_cache
    if now - c_time < 3.0 and c_data:
        return c_data

    url = f"{BINANCE_BASE_URL}/ticker/24hr"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            raw = res.json()

        tickers = []
        for data in raw:
            sym = data.get("symbol", "")
            # Derive human-friendly pair name if possible
            pair_name = sym
            if sym.endswith("USDT"):
                pair_name = f"{sym[:-4]}/USDT"

            tickers.append(
                {
                    "pair": pair_name,
                    "symbol": sym,
                    "last_price": float(data.get("lastPrice", 0)),
                    "price_change": float(data.get("priceChange", 0)),
                    "price_change_percent": float(data.get("priceChangePercent", 0)),
                    "high_24h": float(data.get("highPrice", 0)),
                    "low_24h": float(data.get("lowPrice", 0)),
                    "volume_24h": float(data.get("volume", 0)),
                    "quote_volume_24h": float(data.get("quoteVolume", 0)),
                    "open_price": float(data.get("openPrice", 0)),
                }
            )
        if tickers:
            _all_futures_tickers_cache = (now, tickers)
            return tickers
    except Exception as e:
        logger.warning(f"Error fetching all futures tickers: {e}")
        if c_data:
            return c_data
    return []


async def get_premium_index(symbol: str | None = None) -> list[dict] | dict:
    """Fetch premium index and funding rates from Binance USDⓈ-M Futures.
    
    If symbol is specified, returns single dict for that symbol.
    If symbol is None, returns list of all symbols.
    """
    global _premium_index_cache
    norm_sym = normalize_symbol(symbol) if symbol else None
    now = time.time()
    c_time, c_data = _premium_index_cache

    if now - c_time < 10.0 and c_data:
        if norm_sym:
            for item in c_data:
                if item.get("symbol") == norm_sym:
                    return item
        else:
            return c_data

    url = f"{BINANCE_BASE_URL}/premiumIndex"
    params = {"symbol": norm_sym} if norm_sym else None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, params=params)
            res.raise_for_status()
            raw = res.json()

        if isinstance(raw, list):
            formatted_list = [
                {
                    "symbol": item.get("symbol", ""),
                    "mark_price": float(item.get("markPrice", 0)),
                    "index_price": float(item.get("indexPrice", 0)),
                    "last_funding_rate": float(item.get("lastFundingRate", 0)),
                    "interest_rate": float(item.get("interestRate", 0)),
                    "next_funding_time": int(item.get("nextFundingTime", 0)),
                    "time": int(item.get("time", 0)),
                }
                for item in raw
            ]
            _premium_index_cache = (now, formatted_list)
            return formatted_list
        elif isinstance(raw, dict):
            formatted_item = {
                "symbol": raw.get("symbol", ""),
                "mark_price": float(raw.get("markPrice", 0)),
                "index_price": float(raw.get("indexPrice", 0)),
                "last_funding_rate": float(raw.get("lastFundingRate", 0)),
                "interest_rate": float(raw.get("interestRate", 0)),
                "next_funding_time": int(raw.get("nextFundingTime", 0)),
                "time": int(raw.get("time", 0)),
            }
            return formatted_item
    except Exception as e:
        logger.warning(f"Error fetching premium index: {e}")
        if norm_sym and c_data:
            for item in c_data:
                if item.get("symbol") == norm_sym:
                    return item
        elif not norm_sym and c_data:
            return c_data

    return {} if norm_sym else []


async def get_open_interest(symbol: str) -> dict:
    """Fetch current open interest for a specific symbol on Binance USDⓈ-M Futures."""
    norm_sym = normalize_symbol(symbol)
    url = f"{BINANCE_BASE_URL}/openInterest"
    params = {"symbol": norm_sym}

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(url, params=params)
            res.raise_for_status()
            raw = res.json()
            return {
                "symbol": raw.get("symbol", norm_sym),
                "open_interest": float(raw.get("openInterest", 0)),
                "time": int(raw.get("time", int(time.time() * 1000))),
            }
    except Exception as e:
        logger.warning(f"Error fetching open interest for {norm_sym}: {e}")
        return {
            "symbol": norm_sym,
            "open_interest": 0.0,
            "time": int(time.time() * 1000),
            "error": str(e),
        }


async def get_funding_rate(symbol: str) -> dict:
    """Fetch current funding rate details for a specific symbol."""
    norm_sym = normalize_symbol(symbol)
    p_data = await get_premium_index(norm_sym)
    if isinstance(p_data, dict) and p_data:
        rate = float(p_data.get("last_funding_rate", 0.0))
        return {
            "symbol": norm_sym,
            "funding_rate": rate,
            "funding_rate_pct": round(rate * 100, 4),
            "mark_price": float(p_data.get("mark_price", 0.0)),
            "index_price": float(p_data.get("index_price", 0.0)),
            "next_funding_time": int(p_data.get("next_funding_time", 0)),
            "time": int(p_data.get("time", 0)),
        }
    return {
        "symbol": norm_sym,
        "funding_rate": 0.0,
        "funding_rate_pct": 0.0,
        "mark_price": 0.0,
        "index_price": 0.0,
        "next_funding_time": 0,
        "time": 0,
    }


async def get_orderbook(pair: str = "BTC/USDT", limit: int = 10) -> dict:
    """Fetch top orderbook depth from Binance Futures."""
    symbol = normalize_symbol(pair)
    url = f"{BINANCE_BASE_URL}/depth"
    params = {"symbol": symbol, "limit": limit}

    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    return {
        "bids": [[float(p), float(q)] for p, q in data.get("bids", [])],
        "asks": [[float(p), float(q)] for p, q in data.get("asks", [])],
    }


async def get_agg_trades(pair: str = "BTC/USDT", limit: int = 500) -> list[dict]:
    """Fetch real-time aggregated trades from Binance Futures."""
    symbol = normalize_symbol(pair)
    url = f"{BINANCE_BASE_URL}/aggTrades"
    params = {"symbol": symbol, "limit": min(limit, 1000)}

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.warning(f"Error fetching Futures aggTrades for {symbol}: {e}")
        return []


async def get_exchange_info() -> dict:
    """Fetch Binance USDⓈ-M Futures exchange rules, symbols, and rate limits."""
    global _exchange_info_cache
    now = time.time()
    c_time, c_data = _exchange_info_cache
    if now - c_time < 3600.0 and c_data:
        return c_data

    url = f"{BINANCE_BASE_URL}/exchangeInfo"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            data = res.json()
            _exchange_info_cache = (now, data)
            return data
    except Exception as e:
        logger.warning(f"Error fetching Futures exchangeInfo: {e}")
        return c_data


def compute_market_levels(intraday_candles: list[dict], daily_candles: list[dict] | None = None) -> dict:
    """Computes Session VWAP series, PDH/PDL, PWH/PWL, and current active trading session."""
    import datetime

    # 1. Previous Day High (PDH) & Low (PDL)
    pdh = None
    pdl = None
    pwh = None
    pwl = None

    if daily_candles and len(daily_candles) >= 2:
        # daily_candles[-2] is the completed previous day
        pdh = round(float(daily_candles[-2]["high"]), 2)
        pdl = round(float(daily_candles[-2]["low"]), 2)
        if len(daily_candles) >= 8:
            week_slice = daily_candles[-8:-1]
            pwh = round(float(max(c["high"] for c in week_slice)), 2)
            pwl = round(float(min(c["low"] for c in week_slice)), 2)

    # 2. Session VWAP (resets daily at 00:00 UTC)
    vwap_series = []
    cum_pv = 0.0
    cum_vol = 0.0
    current_day = None

    for c in intraday_candles:
        dt = datetime.datetime.fromtimestamp(c["time"], tz=datetime.timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")

        if day_str != current_day:
            current_day = day_str
            cum_pv = 0.0
            cum_vol = 0.0

        typical_price = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        vol = float(c["volume"])
        cum_pv += typical_price * vol
        cum_vol += vol

        vwap_val = round(cum_pv / max(cum_vol, 1e-5), 2)
        vwap_series.append({
            "time": c["time"],
            "value": vwap_val,
        })

    # 3. Active Trading Sessions
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    current_hour = now_utc.hour + (now_utc.minute / 60.0)

    is_asian = 0.0 <= current_hour < 8.0
    is_london = 7.0 <= current_hour < 15.5
    is_ny = 12.5 <= current_hour < 21.0
    is_london_ny_overlap = 12.5 <= current_hour < 15.5

    active_sessions = []
    if is_asian:
        active_sessions.append("ASIAN (Tokyo/Sydney 00:00-08:00 UTC)")
    if is_london:
        active_sessions.append("LONDON (07:00-15:30 UTC)")
    if is_ny:
        active_sessions.append("NEW YORK (12:30-21:00 UTC)")
    if is_london_ny_overlap:
        active_sessions.append("⭐ LONDON-NY OVERLAP (Peak Institutional Volume)")

    current_price = intraday_candles[-1]["close"] if intraday_candles else 0.0
    latest_vwap = vwap_series[-1]["value"] if vwap_series else current_price

    return {
        "pdh": pdh,
        "pdl": pdl,
        "pwh": pwh,
        "pwl": pwl,
        "latest_vwap": latest_vwap,
        "vwap_series": vwap_series,
        "price_vs_vwap": "ABOVE_VWAP (Bullish Intraday Bias)" if current_price >= latest_vwap else "BELOW_VWAP (Bearish Intraday Bias)",
        "active_sessions": active_sessions,
        "utc_time": now_utc.strftime("%H:%M:%S UTC"),
    }
