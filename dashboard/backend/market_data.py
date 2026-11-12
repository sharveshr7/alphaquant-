"""Market data provider using Binance USDⓈ-M Futures public endpoints with fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import quote

import httpx
import websockets

logger = logging.getLogger("market_data")

# Official Binance USDⓈ-M Futures Base Endpoints
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com/fapi/v1"
BINANCE_BASE_URL = BINANCE_FUTURES_BASE_URL
BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/stream"
EXCHANGE_INFO_CACHE_TTL = 300.0
MARKET_STREAM_TIMEFRAMES = ("1h", "4h")
MARKET_STREAM_MAX_STREAMS = 1024
MARKET_STREAM_STALE_SECONDS = 15.0
MARKET_STREAM_MIN_QUOTE_VOLUME = 1_000_000.0

# In-memory caches
_klines_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_klines_inflight: dict[tuple[int, str], asyncio.Task[list[dict[str, Any]]]] = {}
_klines_diagnostics = {
    "cache_hits": 0,
    "closed_cache_hits": 0,
    "superset_cache_hits": 0,
    "coalesced_requests": 0,
    "rest_requests": 0,
}
_ticker_24h_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_orderbook_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_all_tickers_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_all_futures_tickers_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_exchange_info_cache: tuple[float, dict[str, Any]] = (0.0, {})
_premium_index_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
PAIR_RULES: dict[str, dict[str, Any]] = {}
_exchange_info_stale = True
_exchange_info_updated_ts = 0.0
_market_stream_tickers: dict[str, dict[str, Any]] = {}
_market_stream_marks: dict[str, dict[str, Any]] = {}
_market_stream_candles: dict[tuple[str, str], deque[dict[str, Any]]] = {}
_market_stream_symbol_updates: dict[str, float] = {}
_market_stream_seen_events: dict[tuple[str, str, str], int] = {}
_market_stream_recent_messages: deque[float] = deque()
_market_stream_priority_symbols: set[str] = set()
_market_stream_queue: asyncio.Queue[dict[str, Any]] | None = None
_market_stream_stop: asyncio.Event | None = None
_market_stream_task: asyncio.Task | None = None
_market_stream_connection_count = 0
_market_stream_connected_at = 0.0
_market_stream_connection_attempts = 0
_market_stream_reconnections = 0
_market_stream_subscription_count = 0
_market_stream_monitored_contracts = 0
_market_stream_symbols: set[str] = set()
_market_stream_messages = 0
_market_stream_dropped_messages = 0
_market_stream_last_message_ts = 0.0
_market_stream_last_error: str | None = None
_market_stream_status = "STOPPED"

# Connection telemetry state
_conn_telemetry: dict[str, Any] = {
    "last_success_ts": 0.0,
    "last_error": None,
    "rate_limit_until": 0.0,
    "consecutive_failures": 0,
    "reconnect_attempts": 0,
}


def get_connection_status() -> dict[str, Any]:
    """Return current Binance USDⓈ-M Futures connection health (LIVE, DELAYED, DISCONNECTED)."""
    now = time.time()
    last_ok = float(_conn_telemetry["last_success_ts"])
    stale_sec = round(now - last_ok, 1) if last_ok > 0 else None
    failures = int(_conn_telemetry["consecutive_failures"])

    if now < float(_conn_telemetry["rate_limit_until"]):
        status = "DELAYED"
    elif last_ok == 0.0 or failures >= 3 or (stale_sec is not None and stale_sec > 45.0):
        status = "DISCONNECTED"
    elif stale_sec is not None and stale_sec > 15.0:
        status = "DELAYED"
    else:
        status = "LIVE"

    return {
        "status": status,
        "is_connected": status == "LIVE",
        "last_update_ts": int(last_ok) if last_ok > 0 else 0,
        "stale_seconds": stale_sec,
        "consecutive_failures": failures,
        "last_error": _conn_telemetry["last_error"],
        "pairs_count": len(AVAILABLE_PAIRS),
        "exchange_info_stale": _exchange_info_stale,
        "exchange_info_updated_ts": int(_exchange_info_updated_ts),
        "market_stream": get_market_stream_health(),
    }


def get_market_stream_health() -> dict[str, Any]:
    now = time.time()
    last_activity = _market_stream_last_message_ts or _market_stream_connected_at
    stream_age = round(now - last_activity, 1) if last_activity else None
    status = _market_stream_status
    if status in {"LIVE", "CONNECTING"} and (
        stream_age is None or stream_age > MARKET_STREAM_STALE_SECONDS
    ):
        status = "DELAYED" if _market_stream_connection_count else "DISCONNECTED"

    monotonic_now = time.monotonic()
    while _market_stream_recent_messages and monotonic_now - _market_stream_recent_messages[0] > 60:
        _market_stream_recent_messages.popleft()

    return {
        "status": status,
        "eligible_liquid_contracts": _market_stream_monitored_contracts,
        "connection_count": _market_stream_connection_count,
        "connection_attempts": _market_stream_connection_attempts,
        "reconnections": _market_stream_reconnections,
        "subscription_count": _market_stream_subscription_count,
        "messages_received": _market_stream_messages,
        "messages_per_second_60s": round(len(_market_stream_recent_messages) / 60.0, 2),
        "dropped_messages": _market_stream_dropped_messages,
        "queue_depth": _market_stream_queue.qsize() if _market_stream_queue else 0,
        "last_message_ts": int(_market_stream_last_message_ts) if _market_stream_last_message_ts else None,
        "stale_seconds": stream_age,
        "last_error": _market_stream_last_error,
    }


def set_market_stream_priority_symbols(symbols: set[str]) -> None:
    _market_stream_priority_symbols.clear()
    _market_stream_priority_symbols.update(normalize_symbol(symbol) for symbol in symbols)


def get_market_stream_candles(
    symbol: str, interval: str, limit: int
) -> list[dict[str, Any]] | None:
    candles = _market_stream_candles.get((normalize_symbol(symbol), interval))
    if not candles or len(candles) < limit or get_market_stream_health()["status"] != "LIVE":
        return None
    return [dict(candle) for candle in list(candles)[-limit:]]


async def get_next_market_stream_event() -> dict[str, Any]:
    global _market_stream_queue
    if _market_stream_queue is None:
        _market_stream_queue = asyncio.Queue(maxsize=5000)
    return await _market_stream_queue.get()


def start_market_data_stream() -> None:
    global _market_stream_stop, _market_stream_queue, _market_stream_task
    if _market_stream_task and not _market_stream_task.done():
        return
    _market_stream_stop = asyncio.Event()
    _market_stream_queue = asyncio.Queue(maxsize=5000)
    _market_stream_task = asyncio.create_task(_market_stream_supervisor())


async def stop_market_data_stream() -> None:
    global _market_stream_task, _market_stream_status, _market_stream_connected_at
    if _market_stream_stop:
        _market_stream_stop.set()
    task = _market_stream_task
    if task:
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _market_stream_task = None
    _market_stream_status = "STOPPED"
    _market_stream_connected_at = 0.0


def _queue_market_event(event: dict[str, Any]) -> None:
    global _market_stream_dropped_messages
    if _market_stream_queue is None:
        return
    try:
        _market_stream_queue.put_nowait(event)
    except asyncio.QueueFull:
        _market_stream_dropped_messages += 1
        try:
            _market_stream_queue.get_nowait()
            _market_stream_queue.put_nowait(event)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            _market_stream_dropped_messages += 1


def _merge_stream_ticker_rows(
    rows: list[dict[str, Any]], cached_at: float, now: float
) -> list[dict[str, Any]]:
    merged = []
    stream_live = get_market_stream_health()["status"] == "LIVE"
    for row in rows:
        symbol = row.get("symbol")
        stream_row = _market_stream_tickers.get(str(symbol))
        if stream_live and stream_row:
            age = now - float(stream_row.get("received_at", 0))
            if age <= MARKET_STREAM_STALE_SECONDS:
                merged.append(dict(row, **stream_row, stale=False))
                continue
        result = dict(row)
        age = round(now - cached_at, 1)
        if stream_live and now - cached_at > 3.0:
            result.update(stale=True, stale_seconds=age)
        merged.append(result)
    return merged


def _apply_market_stream_payload(payload: dict[str, Any]) -> None:
    global _market_stream_last_message_ts, _market_stream_messages, _market_stream_status
    received_at = time.time()
    valid_market_data_received = False

    stream = str(payload.get("stream") or "")
    data = payload.get("data")
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []

    for item in items:
        if not isinstance(item, dict):
            continue
        event_type = item.get("e")
        if stream.startswith("!ticker@arr") or event_type == "24hrTicker":
            symbol = str(item.get("s") or "")
            try:
                numeric_fields = {
                    key: float(item.get(source, 0))
                    for key, source in (
                        ("price", "c"),
                        ("price_change", "p"),
                        ("price_change_percent", "P"),
                        ("high_24h", "h"),
                        ("low_24h", "l"),
                        ("volume_24h", "v"),
                        ("quote_volume_24h", "q"),
                        ("open_price", "o"),
                    )
                }
                event_ms = int(item.get("E", 0))
                if (
                    not symbol
                    or not all(math.isfinite(value) for value in numeric_fields.values())
                    or numeric_fields["price"] <= 0
                    or numeric_fields["high_24h"] <= 0
                    or numeric_fields["low_24h"] <= 0
                    or numeric_fields["high_24h"] < numeric_fields["low_24h"]
                    or numeric_fields["open_price"] <= 0
                    or numeric_fields["volume_24h"] < 0
                    or numeric_fields["quote_volume_24h"] < 0
                    or event_ms <= 0
                ):
                    continue
                price = numeric_fields["price"]
                valid_market_data_received = True
                previous = _market_stream_tickers.get(symbol)
                seen_key = ("ticker", symbol, "")
                if event_ms <= _market_stream_seen_events.get(seen_key, 0):
                    continue
                _market_stream_seen_events[seen_key] = event_ms
                ticker = {
                    "pair": f"{symbol[:-4]}/USDT" if symbol.endswith("USDT") else symbol,
                    "symbol": symbol,
                    "last_price": price,
                    "price_change": numeric_fields["price_change"],
                    "price_change_percent": numeric_fields["price_change_percent"],
                    "high_24h": numeric_fields["high_24h"],
                    "low_24h": numeric_fields["low_24h"],
                    "volume_24h": numeric_fields["volume_24h"],
                    "quote_volume_24h": numeric_fields["quote_volume_24h"],
                    "open_price": numeric_fields["open_price"],
                    "timestamp": event_ms // 1000,
                    "event_time_ms": event_ms,
                    "received_at": received_at,
                    "stale": False,
                }
                _market_stream_tickers[symbol] = ticker
                _market_stream_symbol_updates[symbol] = received_at
                if symbol in _market_stream_priority_symbols and (
                    not previous or ticker["last_price"] != previous["last_price"]
                ):
                    _queue_market_event(
                        {
                            "type": "PRICE",
                            "symbol": symbol,
                            "price": price,
                            "event_time_ms": event_ms,
                            "received_at_ms": int(received_at * 1000),
                        }
                    )
            except (TypeError, ValueError):
                _market_stream_dropped_messages += 1
            continue

        if stream.startswith("!markPrice@arr") or event_type == "markPriceUpdate":
            symbol = str(item.get("s") or "")
            try:
                price = float(item.get("p", 0))
                index_price = float(item.get("i", 0))
                funding_rate = float(item.get("r", 0))
                event_ms = int(item.get("E", 0))
                if (
                    not symbol
                    or not all(math.isfinite(value) for value in (price, index_price, funding_rate))
                    or price <= 0
                    or index_price <= 0
                    or event_ms <= 0
                ):
                    continue
                valid_market_data_received = True
                seen_key = ("mark", symbol, "")
                if event_ms <= _market_stream_seen_events.get(seen_key, 0):
                    continue
                _market_stream_seen_events[seen_key] = event_ms
                _market_stream_marks[symbol] = {
                    "symbol": symbol,
                    "mark_price": price,
                    "index_price": index_price,
                    "funding_rate": funding_rate,
                    "next_funding_time": int(item.get("T", 0)),
                    "event_time_ms": event_ms,
                    "received_at": received_at,
                    "stale": False,
                }
            except (TypeError, ValueError):
                _market_stream_dropped_messages += 1
            continue

        if event_type == "kline" or "@kline_" in stream:
            kline = item.get("k")
            symbol = str(item.get("s") or (kline or {}).get("s") or "")
            if not isinstance(kline, dict) or not symbol:
                _market_stream_dropped_messages += 1
                continue
            interval = str(kline.get("i") or "")
            try:
                event_ms = int(item.get("E", 0))
                candle_time = int(kline.get("t", 0)) // 1000
                candle = {
                    "time": candle_time,
                    "open": float(kline.get("o", 0)),
                    "high": float(kline.get("h", 0)),
                    "low": float(kline.get("l", 0)),
                    "close": float(kline.get("c", 0)),
                    "volume": float(kline.get("v", 0)),
                    "is_closed": bool(kline.get("x", False)),
                    "event_time_ms": event_ms,
                }
                if (
                    interval not in MARKET_STREAM_TIMEFRAMES
                    or candle_time <= 0
                    or event_ms <= 0
                    or not all(
                        math.isfinite(candle[key])
                        for key in ("open", "high", "low", "close", "volume")
                    )
                    or min(candle["open"], candle["high"], candle["low"], candle["close"]) <= 0
                    or candle["high"] < candle["low"]
                    or candle["volume"] < 0
                ):
                    continue
                valid_market_data_received = True
                seen_key = ("kline", symbol, f"{interval}:{candle_time}")
                if event_ms <= _market_stream_seen_events.get(seen_key, 0):
                    continue
                _market_stream_seen_events[seen_key] = event_ms
                history = _market_stream_candles.setdefault(
                    (symbol, interval), deque(maxlen=250)
                )
                if history and history[-1]["time"] == candle_time:
                    history[-1] = candle
                elif not history or candle_time > history[-1]["time"]:
                    history.append(candle)
                else:
                    continue
                _market_stream_symbol_updates[symbol] = received_at
                normalized = {
                    key: value
                    for key, value in candle.items()
                    if key != "event_time_ms"
                }
                for cache_key, (_, cached) in list(_klines_cache.items()):
                    prefix = f"{symbol}_{interval}_"
                    if not cache_key.startswith(prefix) or not cache_key.endswith("_None_None"):
                        continue
                    updated = list(cached)
                    if updated and updated[-1]["time"] == candle_time:
                        updated[-1] = normalized
                    elif not updated or candle_time > updated[-1]["time"]:
                        updated.append(normalized)
                    if len(updated) > len(cached):
                        updated = updated[-len(cached):]
                    _klines_cache[cache_key] = (received_at, updated)
                if candle["is_closed"]:
                    _queue_market_event(
                        {
                            "type": "KLINE_CLOSED",
                            "symbol": symbol,
                            "interval": interval,
                            "candle_time": candle_time,
                            "close": candle["close"],
                            "event_time_ms": event_ms,
                            "received_at_ms": int(received_at * 1000),
                        }
                    )
            except (TypeError, ValueError):
                _market_stream_dropped_messages += 1

    if valid_market_data_received:
        _market_stream_last_message_ts = received_at
        _market_stream_status = "LIVE"
        _market_stream_messages += 1
        _market_stream_recent_messages.append(time.monotonic())


_http_clients: dict[int, httpx.AsyncClient] = {}


def _get_http_client() -> httpx.AsyncClient:
    """Return a loop-scoped keep-alive AsyncClient with generous pool limits."""
    loop_id = id(asyncio.get_running_loop())
    client = _http_clients.get(loop_id)
    if client is None or client.is_closed:
        limits = httpx.Limits(max_keepalive_connections=30, max_connections=50, keepalive_expiry=30.0)
        client = httpx.AsyncClient(timeout=10.0, limits=limits)
        _http_clients[loop_id] = client
    return client


def _recycle_http_client():
    """Recycles the active httpx client connection pool on network or timeout errors."""
    try:
        loop_id = id(asyncio.get_running_loop())
        old_client = _http_clients.pop(loop_id, None)
        if old_client and not old_client.is_closed:
            asyncio.create_task(old_client.aclose())
    except Exception:
        pass


async def _binance_get(path: str, params: dict | None = None, timeout: float = 8.0) -> Any:
    """Execute GET against Binance USDⓈ-M Futures with rate-limit handling, connection reuse, and telemetry."""
    now = time.time()
    if now < float(_conn_telemetry["rate_limit_until"]):
        wait_remain = round(float(_conn_telemetry["rate_limit_until"]) - now, 1)
        raise httpx.HTTPStatusError(
            f"Rate limited backoff active ({wait_remain}s remaining)",
            request=httpx.Request("GET", f"{BINANCE_BASE_URL}{path}"),
            response=httpx.Response(429),
        )

    url = f"{BINANCE_BASE_URL}{path}"
    client = _get_http_client()
    try:
        response = await client.get(url, params=params, timeout=timeout)
        if response.status_code in (418, 429):
            retry_after = float(response.headers.get("Retry-After", "10"))
            _conn_telemetry["rate_limit_until"] = time.time() + min(retry_after, 60.0)
            _conn_telemetry["last_error"] = f"HTTP {response.status_code} rate limit (retry after {retry_after}s)"
            _conn_telemetry["consecutive_failures"] += 1
            response.raise_for_status()

        response.raise_for_status()
        _conn_telemetry["last_success_ts"] = time.time()
        _conn_telemetry["last_error"] = None
        _conn_telemetry["consecutive_failures"] = 0
        return response.json()
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        _recycle_http_client()
        err_msg = f"{type(exc).__name__}: {exc or 'network timeout'}"
        _conn_telemetry["last_error"] = err_msg
        _conn_telemetry["consecutive_failures"] += 1
        raise
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        _conn_telemetry["last_error"] = err_msg
        _conn_telemetry["consecutive_failures"] += 1
        raise


async def ping_binance(timeout: float = 5.0) -> bool:
    """Explicitly tests and verifies live connectivity to Binance Futures API."""
    try:
        url = f"{BINANCE_BASE_URL}/ping"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                _conn_telemetry["last_success_ts"] = time.time()
                _conn_telemetry["consecutive_failures"] = 0
                _conn_telemetry["last_error"] = None
                return True
    except Exception as exc:
        _conn_telemetry["last_error"] = f"{type(exc).__name__}: {exc or 'ping failed'}"
        _conn_telemetry["consecutive_failures"] += 1
    return False


async def start_market_data_heartbeat():
    """Background monitoring loop that periodically audits and restores Binance connectivity."""
    last_pair_refresh = 0.0
    while True:
        try:
            now = time.time()
            if now - last_pair_refresh >= EXCHANGE_INFO_CACHE_TTL:
                await refresh_available_pairs()
                last_pair_refresh = now

            last_ok = float(_conn_telemetry["last_success_ts"])
            stale_sec = now - last_ok if last_ok > 0 else 999.0
            failures = int(_conn_telemetry["consecutive_failures"])

            if stale_sec > 15.0 or failures > 0:
                await ping_binance()
        except Exception as e:
            logger.debug(f"Market heartbeat error: {e}")

        await asyncio.sleep(10.0)

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

TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
    "1M": 2592000,
}
TIMEFRAMES = list(TIMEFRAME_SECONDS)


def normalize_symbol(pair: str) -> str:
    """Normalizes pair string (e.g. 'BTC/USDT', 'btc-usdt', 'BTCUSDT') to Binance Futures symbol."""
    if pair in PAIR_MAP:
        return PAIR_MAP[pair]
    cleaned = pair.replace("/", "").replace("-", "").upper()
    return cleaned


def get_price_precision(pair: str) -> int | None:
    """Return decimal precision implied by Binance's discovered price tick size."""
    tick_size = PAIR_RULES.get(normalize_symbol(pair), {}).get("tick_size")
    try:
        tick = Decimal(str(tick_size))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not tick.is_finite() or tick <= 0:
        return None
    return max(0, -tick.normalize().as_tuple().exponent)


def round_price_to_tick(pair: str, price: float) -> float:
    """Round a price to the discovered Binance tick, failing if the tick is unknown."""
    tick_size = PAIR_RULES.get(normalize_symbol(pair), {}).get("tick_size")
    try:
        tick = Decimal(str(tick_size))
        value = Decimal(str(price))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Price tick size is unavailable for {pair}.") from exc
    if not tick.is_finite() or tick <= 0:
        raise ValueError(f"Price tick size is unavailable for {pair}.")
    if not value.is_finite():
        raise ValueError(f"Price must be finite for {pair}.")
    units = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(units * tick)


async def refresh_available_pairs() -> list[str]:
    """Dynamically discover eligible perpetual trading pairs from Binance Futures exchangeInfo
    and update PAIR_MAP, PAIR_RULES, and AVAILABLE_PAIRS in-place.
    """
    global PAIR_MAP, PAIR_RULES, AVAILABLE_PAIRS
    info = await get_exchange_info()
    if _exchange_info_stale:
        return AVAILABLE_PAIRS
    symbols = info.get("symbols", [])
    if not isinstance(symbols, list):
        return AVAILABLE_PAIRS

    new_map: dict[str, str] = {}
    new_rules: dict[str, dict[str, Any]] = {}
    for s in symbols:
        base = s.get("baseAsset", "")
        quote = s.get("quoteAsset", "")
        sym = s.get("symbol", "")
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
            and quote == "USDT"
            and base
            and base.isascii()
            and base.isalnum()
            and sym
        ):
            pair_name = f"{base}/{quote}"
            new_map[pair_name] = sym

            filters = {item.get("filterType"): item for item in s.get("filters", [])}
            price_filter = filters.get("PRICE_FILTER", {})
            lot_filter = filters.get("LOT_SIZE", {})
            notional_filter = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))
            new_rules[sym] = {
                "pair": pair_name,
                "symbol": sym,
                "contract_type": s.get("contractType"),
                "status": s.get("status"),
                "base_asset": base,
                "quote_asset": quote,
                "margin_asset": s.get("marginAsset"),
                "price_precision": s.get("pricePrecision"),
                "quantity_precision": s.get("quantityPrecision"),
                "tick_size": float(price_filter.get("tickSize", 0) or 0),
                "step_size": float(lot_filter.get("stepSize", 0) or 0),
                "min_quantity": float(lot_filter.get("minQty", 0) or 0),
                "min_notional": float(
                    notional_filter.get("notional", notional_filter.get("minNotional", 0)) or 0
                ),
            }

    PAIR_MAP.clear()
    PAIR_MAP.update(new_map)
    PAIR_RULES.clear()
    PAIR_RULES.update(new_rules)
    AVAILABLE_PAIRS.clear()
    AVAILABLE_PAIRS.extend(new_map)
    logger.info("Refreshed AVAILABLE_PAIRS: %d eligible contracts loaded", len(AVAILABLE_PAIRS))

    return AVAILABLE_PAIRS


async def get_klines(
    pair: str = "BTC/USDT",
    interval: str = "15m",
    limit: int = 250,
    end_time: int | None = None,
    start_time: int | None = None,
    use_closed_candle_cache: bool = False,
) -> list[dict]:
    """Fetch candlestick OHLCV data with TTL cache and fallback protection."""
    symbol = normalize_symbol(pair)
    requested_limit = limit
    fetch_limit = (
        120
        if end_time is None and start_time is None and 60 <= limit <= 120
        else limit
    )
    cache_key = f"{symbol}_{interval}_{fetch_limit}_{end_time}_{start_time}"
    now = time.time()
    ttl = 300.0 if end_time is not None else 3.0

    if cache_key in _klines_cache:
        c_time, c_data = _klines_cache[cache_key]
        if now - c_time < ttl and c_data:
            _klines_diagnostics["cache_hits"] += 1
            return c_data[-requested_limit:]
        if (
            end_time is None
            and start_time is None
            and interval in MARKET_STREAM_TIMEFRAMES
            and symbol in _market_stream_symbols
            and get_market_stream_health()["status"] == "LIVE"
            and now - _market_stream_symbol_updates.get(symbol, 0.0) <= MARKET_STREAM_STALE_SECONDS
            and c_data
        ):
            cached = [dict(candle) for candle in c_data[-fetch_limit:]]
            _klines_cache[cache_key] = (now, cached)
            _klines_diagnostics["cache_hits"] += 1
            return cached[-requested_limit:]
        if use_closed_candle_cache and _closed_candle_snapshot_is_current(
            c_data, c_time, interval, now
        ):
            _klines_diagnostics["closed_cache_hits"] += 1
            return [dict(candle) for candle in c_data[-requested_limit:]]

    if end_time is None and start_time is None:
        prefix = f"{symbol}_{interval}_"
        suffix = f"_{end_time}_{start_time}"
        for candidate_key, (cached_at, cached_candles) in tuple(_klines_cache.items()):
            if candidate_key == cache_key or not candidate_key.startswith(prefix) or not candidate_key.endswith(suffix):
                continue
            candidate_limit = candidate_key[len(prefix):-len(suffix)]
            try:
                cached_limit = int(candidate_limit)
            except ValueError:
                continue
            if cached_limit < requested_limit or len(cached_candles) < requested_limit:
                continue
            if time.time() - cached_at < ttl:
                _klines_diagnostics["superset_cache_hits"] += 1
                return [dict(candle) for candle in cached_candles[-requested_limit:]]
            if use_closed_candle_cache and _closed_candle_snapshot_is_current(
                cached_candles, cached_at, interval, time.time()
            ):
                _klines_diagnostics["closed_cache_hits"] += 1
                _klines_diagnostics["superset_cache_hits"] += 1
                return [dict(candle) for candle in cached_candles[-requested_limit:]]

    task_key = (id(asyncio.get_running_loop()), cache_key)
    inflight = _klines_inflight.get(task_key)
    if inflight is not None:
        _klines_diagnostics["coalesced_requests"] += 1
        return [
            dict(candle)
            for candle in (await asyncio.shield(inflight))[-requested_limit:]
        ]

    task = asyncio.create_task(
        _fetch_klines_from_rest(
            symbol, interval, fetch_limit, end_time, start_time, cache_key
        )
    )
    _klines_inflight[task_key] = task

    def clear_inflight(completed: asyncio.Task[list[dict]]) -> None:
        if _klines_inflight.get(task_key) is completed:
            _klines_inflight.pop(task_key, None)

    task.add_done_callback(clear_inflight)
    return [
        dict(candle)
        for candle in (await asyncio.shield(task))[-requested_limit:]
    ]


def _closed_candle_snapshot_is_current(
    candles: list[dict[str, Any]], cached_at: float, interval: str, now: float
) -> bool:
    interval_seconds = TIMEFRAME_SECONDS.get(interval)
    if not interval_seconds or len(candles) < 2:
        return False
    if any(candle.get("stale", False) for candle in candles):
        return False
    latest_closed_start = (int(now) // interval_seconds - 1) * interval_seconds
    closed_times = [
        int(candle.get("time", 0))
        for candle in candles
        if int(candle.get("time", 0)) <= latest_closed_start
    ]
    return (
        bool(closed_times)
        and max(closed_times) == latest_closed_start
        and cached_at >= latest_closed_start + interval_seconds + 1
    )


def get_klines_diagnostics() -> dict[str, int]:
    return dict(_klines_diagnostics)


async def _fetch_klines_from_rest(
    symbol: str,
    interval: str,
    limit: int,
    end_time: int | None,
    start_time: int | None,
    cache_key: str,
) -> list[dict]:
    _klines_diagnostics["rest_requests"] += 1
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
        raw_klines = await _binance_get("/klines", params=params, timeout=8.0)

        by_time: dict[int, dict[str, Any]] = {}
        for k in raw_klines:
            try:
                t = int(k[0] // 1000)
                o = float(k[1])
                h = float(k[2])
                l = float(k[3])
                c = float(k[4])
                v = float(k[5])
                if not all(math.isfinite(x) for x in (o, h, l, c, v)):
                    continue
                if min(o, h, l, c) <= 0 or v < 0 or h < l:
                    continue
                h = max(h, o, c)
                l = min(l, o, c)
                by_time[t] = {
                    "time": t,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                }
            except (TypeError, ValueError, IndexError):
                continue
        candles = [by_time[t] for t in sorted(by_time.keys())]
        if candles:
            if interval in MARKET_STREAM_TIMEFRAMES:
                stream_key = (symbol, interval)
                streamed = _market_stream_candles.get(stream_key)
                if streamed:
                    by_time.update({candle["time"]: dict(candle) for candle in streamed})
                    candles = [by_time[t] for t in sorted(by_time)]
                history = _market_stream_candles.setdefault(
                    stream_key, deque(maxlen=250)
                )
                merged = {candle["time"]: dict(candle) for candle in history}
                merged.update({candle["time"]: dict(candle) for candle in candles})
                history.clear()
                history.extend(merged[t] for t in sorted(merged)[-250:])
            _klines_cache[cache_key] = (time.time(), candles)
        return candles
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e or 'request failed'}"
        _conn_telemetry["last_error"] = err_msg
        _conn_telemetry["consecutive_failures"] += 1
        logger.warning(f"Failed to fetch klines for {symbol} ({interval}): {err_msg}")
        if cache_key in _klines_cache:
            cached_at, cached_candles = _klines_cache[cache_key]
            stale_seconds = round(time.time() - cached_at, 1)
            return [
                dict(candle, stale=True, stale_seconds=stale_seconds)
                for candle in cached_candles
            ]
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
        "8h": 28800,
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
    """Fetch 24-hour ticker statistics with short-TTL cache & stale fallback."""
    symbol = normalize_symbol(pair)
    now = time.time()
    stream_ticker = _market_stream_tickers.get(symbol)
    if (
        stream_ticker
        and now - float(stream_ticker.get("received_at", 0)) <= MARKET_STREAM_STALE_SECONDS
    ):
        return dict(stream_ticker, pair=pair, stale=False)
    if symbol in _ticker_24h_cache:
        c_time, c_data = _ticker_24h_cache[symbol]
        if now - c_time < 2.0 and c_data:
            return c_data

    try:
        data = await _binance_get("/ticker/24hr", params={"symbol": symbol}, timeout=8.0)
        res = {
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
            "timestamp": int(now),
            "stale": False,
        }
        _ticker_24h_cache[symbol] = (now, res)
        return res
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e or 'request failed'}"
        _conn_telemetry["last_error"] = err_msg
        _conn_telemetry["consecutive_failures"] += 1
        logger.warning(f"Error fetching ticker_24h for {symbol}: {err_msg}")
        if symbol in _ticker_24h_cache:
            stale = dict(_ticker_24h_cache[symbol][1])
            stale["stale"] = True
            return stale
        return {
            "pair": pair,
            "symbol": symbol,
            "last_price": 0.0,
            "price_change": 0.0,
            "price_change_percent": 0.0,
            "high_24h": 0.0,
            "low_24h": 0.0,
            "volume_24h": 0.0,
            "quote_volume_24h": 0.0,
            "open_price": 0.0,
            "error": str(e),
            "stale": True,
        }


async def get_all_tickers() -> list[dict]:
    """Fetch 24h ticker for all watchlist pairs in 1 fast batch call with 3s cache."""
    global _all_tickers_cache
    now = time.time()
    c_time, c_data = _all_tickers_cache
    if now - c_time < 3.0 and c_data:
        return c_data
    if (
        c_data
        and now - c_time < 60.0
        and get_market_stream_health()["status"] == "LIVE"
    ):
        return _merge_stream_ticker_rows(c_data, c_time, now)

    try:
        raw = await _binance_get("/ticker/24hr", timeout=8.0)

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
                        "timestamp": int(now),
                        "stale": False,
                    }
                )
        if tickers:
            _all_tickers_cache = (now, tickers)
            return _merge_stream_ticker_rows(tickers, now, now)
    except Exception as e:
        _conn_telemetry["last_error"] = str(e)
        _conn_telemetry["consecutive_failures"] += 1
        logger.warning(f"Error fetching batch tickers: {e}")
        if c_data:
            if get_market_stream_health()["status"] == "LIVE":
                return _merge_stream_ticker_rows(c_data, c_time, now)
            age = round(now - c_time, 1)
            return [dict(ticker, stale=True, stale_seconds=age) for ticker in c_data]
    return []


async def get_all_futures_tickers() -> list[dict]:
    """Fetch 24h ticker for ALL Binance USDⓈ-M Futures pairs."""
    global _all_futures_tickers_cache
    now = time.time()
    c_time, c_data = _all_futures_tickers_cache
    if now - c_time < 3.0 and c_data:
        return c_data
    if (
        c_data
        and now - c_time < 60.0
        and get_market_stream_health()["status"] == "LIVE"
    ):
        return _merge_stream_ticker_rows(c_data, c_time, now)

    try:
        raw = await _binance_get("/ticker/24hr", timeout=10.0)

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
                    "timestamp": int(now),
                    "stale": False,
                }
            )
        if tickers:
            _all_futures_tickers_cache = (now, tickers)
            return _merge_stream_ticker_rows(tickers, now, now)
    except Exception as e:
        logger.warning(f"Error fetching all futures tickers: {e}")
        if c_data:
            if get_market_stream_health()["status"] == "LIVE":
                return _merge_stream_ticker_rows(c_data, c_time, now)
            age = round(now - c_time, 1)
            return [dict(ticker, stale=True, stale_seconds=age) for ticker in c_data]
    return []


async def _discover_liquid_stream_symbols() -> list[str]:
    exchange_info, tickers = await asyncio.gather(
        get_exchange_info(), get_all_futures_tickers()
    )
    if _exchange_info_stale or not isinstance(tickers, list) or not tickers:
        return []
    ticker_map = {
        row.get("symbol"): row
        for row in tickers
        if row.get("symbol") and not row.get("stale", False)
    }
    symbols = []
    for contract in exchange_info.get("symbols", []):
        symbol = str(contract.get("symbol") or "")
        base = str(contract.get("baseAsset") or "")
        if (
            contract.get("contractType") == "PERPETUAL"
            and contract.get("status") == "TRADING"
            and contract.get("quoteAsset") == "USDT"
            and base.isascii()
            and base.isalnum()
            and symbol in ticker_map
            and float(ticker_map[symbol].get("quote_volume_24h", 0.0))
            >= MARKET_STREAM_MIN_QUOTE_VOLUME
        ):
            symbols.append(symbol)
    return sorted(set(symbols))


async def _recv_market_stream_message(
    websocket: Any, stop: asyncio.Event
) -> str | bytes | None:
    receive_task = asyncio.create_task(websocket.recv())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            {receive_task, stop_task},
            timeout=20.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            return None
        if receive_task not in done:
            raise TimeoutError("No market stream messages received for 20 seconds")
        return receive_task.result()
    finally:
        pending = [
            task for task in (receive_task, stop_task) if not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _run_market_stream_partition(streams: list[str], stop: asyncio.Event) -> None:
    global _market_stream_connection_count, _market_stream_connected_at
    global _market_stream_connection_attempts, _market_stream_reconnections
    global _market_stream_last_error, _market_stream_status
    retry_delay = 1.0
    connected_once = False

    while not stop.is_set():
        connection_open = False
        query = "/".join(quote(stream, safe="@!") for stream in streams)
        uri = f"{BINANCE_FUTURES_WS_URL}?streams={query}"
        _market_stream_connection_attempts += 1
        websocket = None
        try:
            connection = websockets.connect(
                uri,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_queue=4096,
                max_size=2**20,
                compression=None,
            )
            websocket = await asyncio.wait_for(connection, timeout=12.0)
            if _market_stream_connection_count == 0:
                _market_stream_connected_at = time.time()
            _market_stream_connection_count += 1
            connection_open = True
            if not _market_stream_last_message_ts:
                _market_stream_status = "CONNECTING"
            if connected_once:
                _market_stream_reconnections += 1
            connected_once = True
            retry_delay = 1.0
            while not stop.is_set():
                raw = await _recv_market_stream_message(websocket, stop)
                if raw is None:
                    break
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    _market_stream_dropped_messages += 1
                    continue
                if isinstance(payload, dict):
                    _apply_market_stream_payload(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _market_stream_last_error = f"{type(exc).__name__}: {exc}"
            _market_stream_status = "DISCONNECTED"
            logger.warning("Binance market stream reconnect scheduled: %s", _market_stream_last_error)
        finally:
            if connection_open:
                _market_stream_connection_count -= 1
                if _market_stream_connection_count == 0:
                    _market_stream_connected_at = 0.0
            if websocket is not None:
                try:
                    await asyncio.wait_for(websocket.close(), timeout=6.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("Error closing Binance stream connection", exc_info=True)

        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=retry_delay)
            except asyncio.TimeoutError:
                pass
            retry_delay = min(retry_delay * 2, 30.0)


async def _market_stream_supervisor() -> None:
    global _market_stream_status, _market_stream_monitored_contracts
    global _market_stream_subscription_count, _market_stream_symbols
    global _market_stream_connection_count, _market_stream_last_error
    stop = _market_stream_stop
    if stop is None:
        return

    while not stop.is_set():
        stream_tasks: list[asyncio.Task] = []
        try:
            _market_stream_status = "DISCOVERING"
            symbols = await _discover_liquid_stream_symbols()
            if not symbols:
                _market_stream_status = "WAITING_FOR_ELIGIBLE_MARKETS"
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                return

            _market_stream_symbols = set(symbols)
            _market_stream_monitored_contracts = len(symbols)
            global_streams = ["!ticker@arr", "!markPrice@arr@1s"]
            symbol_streams = [
                f"{symbol.lower()}@kline_{interval}"
                for symbol in symbols
                for interval in MARKET_STREAM_TIMEFRAMES
            ]
            partition_size = MARKET_STREAM_MAX_STREAMS - len(global_streams)
            partitions = [
                symbol_streams[index:index + partition_size]
                for index in range(0, len(symbol_streams), partition_size)
            ]
            if not partitions:
                partitions = [[]]
            partitions[0] = global_streams + partitions[0]
            _market_stream_subscription_count = len(global_streams) + len(symbol_streams)
            _market_stream_status = "CONNECTING"
            stream_tasks = [
                asyncio.create_task(_run_market_stream_partition(partition, stop))
                for partition in partitions
            ]
            try:
                await asyncio.wait_for(stop.wait(), timeout=EXCHANGE_INFO_CACHE_TTL)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _market_stream_last_error = f"{type(exc).__name__}: {exc}"
            _market_stream_status = "DISCONNECTED"
            logger.exception("Binance market stream setup failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                pass
        finally:
            for task in stream_tasks:
                task.cancel()
            if stream_tasks:
                await asyncio.gather(*stream_tasks, return_exceptions=True)
            _market_stream_connection_count = 0


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


_derivatives_cache: dict[str, tuple[float, dict]] = {}


async def get_open_interest_hist(symbol: str, period: str = "15m", limit: int = 15) -> list[dict]:
    """Fetch real historical open interest for a specific symbol from Binance Futures data endpoint."""
    norm_sym = normalize_symbol(symbol)
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    params = {"symbol": norm_sym, "period": period, "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(url, params=params)
            if res.status_code == 200:
                raw = res.json()
                return [
                    {
                        "symbol": item.get("symbol", norm_sym),
                        "sum_open_interest": float(item.get("sumOpenInterest", 0.0)),
                        "sum_open_interest_value": float(item.get("sumOpenInterestValue", 0.0)),
                        "timestamp": int(item.get("timestamp", 0)),
                    }
                    for item in raw
                ]
    except Exception as e:
        logger.warning(f"Error fetching openInterestHist for {norm_sym}: {e}")
    return []


async def get_long_short_ratios(symbol: str, period: str = "15m", limit: int = 5) -> dict:
    """Fetch genuine Binance USDⓈ-M account, top trader, and taker ratios."""
    norm_sym = normalize_symbol(symbol)
    base = "https://fapi.binance.com/futures/data"
    params = {"symbol": norm_sym, "period": period, "limit": limit}
    out: dict[str, Any] = {
        "global_long_short_ratio": None,
        "top_trader_long_short_ratio": None,
        "taker_buy_sell_ratio": None,
        "taker_buy_vol": 0.0,
        "taker_sell_vol": 0.0,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r1, r2, r3 = await asyncio.gather(
                client.get(f"{base}/globalLongShortAccountRatio", params=params),
                client.get(f"{base}/topLongShortPositionRatio", params=params),
                client.get(f"{base}/takerlongshortRatio", params=params),
                return_exceptions=True,
            )
            if not isinstance(r1, Exception) and hasattr(r1, "status_code") and r1.status_code == 200 and r1.json():
                item = r1.json()[-1]
                out["global_long_short_ratio"] = float(item.get("longShortRatio", 0.0))
                out["global_long_pct"] = round(float(item.get("longAccount", 0.0)) * 100, 2)
                out["global_short_pct"] = round(float(item.get("shortAccount", 0.0)) * 100, 2)
            if not isinstance(r2, Exception) and hasattr(r2, "status_code") and r2.status_code == 200 and r2.json():
                item = r2.json()[-1]
                out["top_trader_long_short_ratio"] = float(item.get("longShortRatio", 0.0))
                out["top_trader_long_pct"] = round(float(item.get("longPosition", 0.0)) * 100, 2)
                out["top_trader_short_pct"] = round(float(item.get("shortPosition", 0.0)) * 100, 2)
            if not isinstance(r3, Exception) and hasattr(r3, "status_code") and r3.status_code == 200 and r3.json():
                item = r3.json()[-1]
                out["taker_buy_sell_ratio"] = float(item.get("buySellRatio", 0.0))
                out["taker_buy_vol"] = float(item.get("buyVol", 0.0))
                out["taker_sell_vol"] = float(item.get("sellVol", 0.0))
    except Exception as e:
        logger.warning(f"Error fetching long/short ratios for {norm_sym}: {e}")
    return out


async def get_futures_derivatives_data(symbol: str) -> dict:
    """Combines real Binance USDⓈ-M Futures data (Tool 9) with caching."""
    norm_sym = normalize_symbol(symbol)
    now = time.time()
    cache_entry = _derivatives_cache.get(norm_sym)
    if cache_entry and (now - cache_entry[0] < 15.0):
        return cache_entry[1]

    funding_task = get_funding_rate(norm_sym)
    oi_task = get_open_interest(norm_sym)
    oi_hist_task = get_open_interest_hist(norm_sym, period="15m", limit=12)
    ls_task = get_long_short_ratios(norm_sym, period="15m", limit=3)

    funding_data, oi_data, oi_hist, ls_data = await asyncio.gather(
        funding_task, oi_task, oi_hist_task, ls_task, return_exceptions=True
    )

    funding = funding_data if isinstance(funding_data, dict) else {}
    oi = oi_data if isinstance(oi_data, dict) else {}
    oi_history = oi_hist if isinstance(oi_hist, list) else []
    ls = ls_data if isinstance(ls_data, dict) else {}

    # Open interest change
    current_oi = float(oi.get("open_interest", 0.0))
    oi_change_1h = 0.0
    oi_change_1h_pct = 0.0
    if len(oi_history) >= 4:
        past_oi = oi_history[0].get("sum_open_interest", current_oi)
        if past_oi > 0:
            oi_change_1h = current_oi - past_oi
            oi_change_1h_pct = round((oi_change_1h / past_oi) * 100, 2)

    # Futures basis
    mark_p = float(funding.get("mark_price", 0.0))
    idx_p = float(funding.get("index_price", 0.0))
    basis = round(mark_p - idx_p, 4) if (mark_p > 0 and idx_p > 0) else 0.0
    basis_pct = round((basis / idx_p) * 100, 4) if idx_p > 0 else 0.0

    fr_pct = float(funding.get("funding_rate_pct", 0.0))
    fr_annualized = round(fr_pct * 3 * 365, 2)

    result = {
        "symbol": norm_sym,
        "funding_rate_pct": fr_pct,
        "funding_rate_annualized_pct": fr_annualized,
        "next_funding_time": funding.get("next_funding_time", 0),
        "mark_price": mark_p,
        "index_price": idx_p,
        "futures_basis": basis,
        "futures_basis_pct": basis_pct,
        "open_interest": current_oi,
        "open_interest_change_1h": round(oi_change_1h, 2),
        "open_interest_change_1h_pct": oi_change_1h_pct,
        "global_long_short_ratio": ls.get("global_long_short_ratio"),
        "global_long_pct": ls.get("global_long_pct"),
        "global_short_pct": ls.get("global_short_pct"),
        "top_trader_long_short_ratio": ls.get("top_trader_long_short_ratio"),
        "top_trader_long_pct": ls.get("top_trader_long_pct"),
        "top_trader_short_pct": ls.get("top_trader_short_pct"),
        "taker_buy_sell_ratio": ls.get("taker_buy_sell_ratio"),
        "taker_buy_vol": ls.get("taker_buy_vol", 0.0),
        "taker_sell_vol": ls.get("taker_sell_vol", 0.0),
        "data_freshness_ts": int(now),
        "is_live_binance_data": True,
    }
    _derivatives_cache[norm_sym] = (now, result)
    return result


async def get_market_context_and_correlation(pair: str) -> dict:
    """Tool 10: Market Context and BTC/ETH Correlation using actual Binance Futures data."""
    try:
        btc_candles, eth_candles, target_candles, btc_ticker, eth_ticker, target_ticker = await asyncio.gather(
            get_klines("BTC/USDT", "15m", limit=40),
            get_klines("ETH/USDT", "15m", limit=40),
            get_klines(pair, "15m", limit=40),
            get_ticker_24h("BTC/USDT"),
            get_ticker_24h("ETH/USDT"),
            get_ticker_24h(pair),
            return_exceptions=True,
        )

        btc_chg_24h = float(btc_ticker.get("price_change_percent", 0.0)) if isinstance(btc_ticker, dict) else 0.0
        eth_chg_24h = float(eth_ticker.get("price_change_percent", 0.0)) if isinstance(eth_ticker, dict) else 0.0
        tgt_chg_24h = float(target_ticker.get("price_change_percent", 0.0)) if isinstance(target_ticker, dict) else 0.0

        # BTC Market Direction
        btc_direction = "BULLISH" if btc_chg_24h > 1.0 else ("BEARISH" if btc_chg_24h < -1.0 else "NEUTRAL")
        eth_direction = "BULLISH" if eth_chg_24h > 1.0 else ("BEARISH" if eth_chg_24h < -1.0 else "NEUTRAL")

        # Crypto Market Regime
        if btc_direction == "BULLISH" and eth_direction == "BULLISH":
            market_regime = "RISK_ON_BULLISH"
        elif btc_direction == "BEARISH" and eth_direction == "BEARISH":
            market_regime = "RISK_OFF_BEARISH"
        else:
            market_regime = "ROTATIONAL_SELECTIVE"

        # Relative strength vs BTC
        relative_strength_vs_btc = round(tgt_chg_24h - btc_chg_24h, 2)
        relative_strength_label = "OUTPERFORMING" if relative_strength_vs_btc > 1.0 else ("UNDERPERFORMING" if relative_strength_vs_btc < -1.0 else "IN_LINE")

        # Correlation with BTC returns over 35 candles
        btc_corr = 1.0
        if pair.upper().startswith("BTC"):
            btc_corr = 1.0
        elif isinstance(target_candles, list) and isinstance(btc_candles, list) and len(target_candles) >= 20 and len(btc_candles) >= 20:
            import numpy as np
            tgt_closes = [float(c["close"]) for c in target_candles[-35:]]
            btc_closes = [float(c["close"]) for c in btc_candles[-35:]]
            min_l = min(len(tgt_closes), len(btc_closes))
            if min_l > 5:
                tgt_rets = np.diff(tgt_closes[:min_l]) / tgt_closes[:min_l-1]
                btc_rets = np.diff(btc_closes[:min_l]) / btc_closes[:min_l-1]
                if np.std(tgt_rets) > 1e-8 and np.std(btc_rets) > 1e-8:
                    c_matrix = np.corrcoef(tgt_rets, btc_rets)
                    btc_corr = round(float(c_matrix[0, 1]), 3)

        return {
            "btc_direction": btc_direction,
            "btc_24h_change_pct": btc_chg_24h,
            "eth_direction": eth_direction,
            "eth_24h_change_pct": eth_chg_24h,
            "market_regime": market_regime,
            "relative_strength_vs_btc": relative_strength_vs_btc,
            "relative_strength_label": relative_strength_label,
            "correlation_with_btc": btc_corr,
            "correlation_strength": "HIGH" if abs(btc_corr) >= 0.75 else ("MODERATE" if abs(btc_corr) >= 0.4 else "LOW"),
            "target_pair_24h_change_pct": tgt_chg_24h,
        }
    except Exception as e:
        logger.warning(f"Error computing market context for {pair}: {e}")
        return {
            "btc_direction": "NEUTRAL",
            "btc_24h_change_pct": 0.0,
            "eth_direction": "NEUTRAL",
            "eth_24h_change_pct": 0.0,
            "market_regime": "NEUTRAL",
            "relative_strength_vs_btc": 0.0,
            "relative_strength_label": "IN_LINE",
            "correlation_with_btc": 1.0,
            "correlation_strength": "UNKNOWN",
            "target_pair_24h_change_pct": 0.0,
            "error": str(e),
        }


def _adaptive_round(val: float) -> float:
    """Round prices preserving full precision on sub-dollar altcoins."""
    aval = abs(val)
    if aval == 0:
        return 0.0
    if aval < 0.01:
        return round(val, 6)
    if aval < 1.0:
        return round(val, 5)
    if aval < 50.0:
        return round(val, 4)
    return round(val, 2)


async def get_orderbook(pair: str = "BTC/USDT", limit: int = 10) -> dict:
    """Fetch top orderbook depth from Binance Futures with short-TTL cache & fallback."""
    symbol = normalize_symbol(pair)
    cache_key = f"{symbol}_{limit}"
    now = time.time()
    if cache_key in _orderbook_cache:
        c_time, c_data = _orderbook_cache[cache_key]
        if now - c_time < 2.0 and c_data:
            return c_data

    try:
        data = await _binance_get("/depth", params={"symbol": symbol, "limit": limit}, timeout=8.0)
        res = {
            "bids": [[float(p), float(q)] for p, q in data.get("bids", [])],
            "asks": [[float(p), float(q)] for p, q in data.get("asks", [])],
        }
        _orderbook_cache[cache_key] = (now, res)
        return res
    except Exception as e:
        _conn_telemetry["last_error"] = str(e)
        _conn_telemetry["consecutive_failures"] += 1
        logger.warning(f"Error fetching orderbook for {symbol}: {e}")
        if cache_key in _orderbook_cache:
            return _orderbook_cache[cache_key][1]
        return {"bids": [], "asks": [], "error": str(e)}


async def get_agg_trades(pair: str = "BTC/USDT", limit: int = 500) -> list[dict]:
    """Fetch real-time aggregated trades from Binance Futures."""
    symbol = normalize_symbol(pair)
    params = {"symbol": symbol, "limit": min(limit, 1000)}

    try:
        return await _binance_get("/aggTrades", params=params, timeout=8.0)
    except Exception as e:
        _conn_telemetry["last_error"] = str(e)
        _conn_telemetry["consecutive_failures"] += 1
        logger.warning(f"Error fetching Futures aggTrades for {symbol}: {e}")
        return []


async def get_exchange_info() -> dict:
    """Fetch Binance USDⓈ-M Futures exchange rules, symbols, and rate limits."""
    global _exchange_info_cache, _exchange_info_stale, _exchange_info_updated_ts
    now = time.time()
    c_time, c_data = _exchange_info_cache
    if now - c_time < EXCHANGE_INFO_CACHE_TTL and c_data:
        return c_data

    try:
        data = await _binance_get("/exchangeInfo", timeout=10.0)
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise ValueError("Binance exchangeInfo response is missing its symbols list")
        _exchange_info_cache = (time.time(), data)
        _exchange_info_updated_ts = time.time()
        _exchange_info_stale = False
        return data
    except Exception as e:
        _exchange_info_stale = True
        _conn_telemetry["last_error"] = str(e)
        _conn_telemetry["consecutive_failures"] += 1
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
        pdh = _adaptive_round(float(daily_candles[-2]["high"]))
        pdl = _adaptive_round(float(daily_candles[-2]["low"]))
        if len(daily_candles) >= 8:
            week_slice = daily_candles[-8:-1]
            pwh = _adaptive_round(float(max(c["high"] for c in week_slice)))
            pwl = _adaptive_round(float(min(c["low"] for c in week_slice)))

    # 2. Session VWAP (resets daily at 00:00 UTC)
    vwap_series = []
    cum_pv = 0.0
    cum_vol = 0.0
    current_day = None

    for c in intraday_candles:
        try:
            t_sec = int(c["time"])
            h_v = float(c["high"])
            l_v = float(c["low"])
            c_v = float(c["close"])
            vol = float(c.get("volume", 0.0))
        except (TypeError, ValueError, KeyError):
            continue
        if not all(math.isfinite(x) for x in (h_v, l_v, c_v, vol)) or min(h_v, l_v, c_v) <= 0 or vol < 0:
            continue

        dt = datetime.datetime.fromtimestamp(t_sec, tz=datetime.timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")

        if day_str != current_day:
            current_day = day_str
            cum_pv = 0.0
            cum_vol = 0.0

        typical_price = (h_v + l_v + c_v) / 3.0
        cum_pv += typical_price * vol
        cum_vol += vol

        raw_vwap = (cum_pv / cum_vol) if cum_vol > 0 else typical_price
        vwap_val = _adaptive_round(raw_vwap)
        if vwap_val > 0 and math.isfinite(vwap_val):
            vwap_series.append({
                "time": t_sec,
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
