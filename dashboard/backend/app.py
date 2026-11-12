"""FastAPI backend server for Advanced AI Supply & Demand Trading Terminal."""

import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Optional

import httpx
import psutil
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ai_assistant import generate_ai_response
from .backtest_engine import backtest_engine
from .config import config
from .logging_config import setup_logging
from .market_data import (
    AVAILABLE_PAIRS,
    PAIR_MAP,
    PAIR_RULES,
    TIMEFRAMES,
    TIMEFRAME_SECONDS,
    compute_market_levels,
    get_all_tickers,
    get_connection_status,
    get_historical_klines_paginated,
    get_klines,
    get_next_market_stream_event,
    get_ticker_24h,
    ping_binance,
    refresh_available_pairs,
    set_market_stream_priority_symbols,
    start_market_data_stream,
    start_market_data_heartbeat,
    stop_market_data_stream,
)
from .coin_analyzer import coin_analyzer
from .coingecko_provider import coingecko_provider, get_market_cap_provider
from .liquidity_engine import liquidity_engine
from .market_scanner import market_scanner
from .multi_timeframe import mtf_engine
from .order_flow import order_flow_engine
from .paper_trader import paper_trader
from .risk_manager import risk_manager, validate_trade_plan
from .signal_engine import signal_engine
from .signal_tracker import signal_tracker
from .smc_engine import smc_engine
from .storage import atomic_write_json, safe_load_json
from .strategy_engine import evaluate_confluence
from .supply_demand import supply_demand_engine
from .technical_analysis import compute_indicators

setup_logging()
logger = logging.getLogger("app")
ws_logger = logging.getLogger("websocket")
SERVER_START_TIME = time.time()
SIGNAL_SCAN_INTERVAL_SECONDS = 180.0
SIGNAL_UPDATE_INTERVAL_SECONDS = 10.0
BACKGROUND_TASKS: list[asyncio.Task] = []
SIGNAL_EVENT_SUBSCRIBERS: set[asyncio.Queue] = set()
SIGNAL_EVENT_PUBLISHED = 0
SIGNAL_EVENT_DROPPED = 0
SIGNAL_SCAN_STATUS = {
    "status": "WAITING",
    "last_started_at": None,
    "last_completed_at": None,
    "last_duration_ms": None,
    "eligible_contracts": 0,
    "analyzed_contracts": 0,
    "candidate_contracts": 0,
    "confirmed_signals": 0,
    "event_count": 0,
    "last_event_type": None,
    "last_event_symbol": None,
    "last_event_latency_ms": None,
    "last_analysis_latency_ms": None,
    "last_error": None,
}
_EVENT_ANALYSIS_LAST_CANDLE: dict[str, int] = {}


def _publish_signal_event(event: dict) -> None:
    global SIGNAL_EVENT_PUBLISHED, SIGNAL_EVENT_DROPPED
    event_id = (
        f"{event.get('symbol', '')}:{event.get('timeframe', '')}:"
        f"{event.get('version', 0)}"
    )
    message = {"type": "signal_event", "event_id": event_id, "event": dict(event)}
    SIGNAL_EVENT_PUBLISHED += 1
    for queue in tuple(SIGNAL_EVENT_SUBSCRIBERS):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            SIGNAL_EVENT_DROPPED += 1
            try:
                queue.get_nowait()
                queue.put_nowait(message)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                SIGNAL_EVENT_DROPPED += 1


signal_tracker.add_lifecycle_listener(_publish_signal_event)

app = FastAPI(title="AlphaQuant Advanced AI Trading Terminal", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DRAWINGS_FILE = DATA_DIR / "drawings.json"
if not DRAWINGS_FILE.exists() and (Path(__file__).resolve().parent.parent / "drawings.json").exists():
    DRAWINGS_FILE = Path(__file__).resolve().parent.parent / "drawings.json"

WATCHLIST_FILE = DATA_DIR / "watchlist.json"
if not WATCHLIST_FILE.exists() and (Path(__file__).resolve().parent.parent / "watchlist.json").exists():
    WATCHLIST_FILE = Path(__file__).resolve().parent.parent / "watchlist.json"


# Pydantic models
class ChatRequest(BaseModel):
    message: str
    pair: str = "BTC/USDT"
    interval: str = "15m"


class TradeRequest(BaseModel):
    pair: str = "BTC/USDT"
    side: str = "BUY"
    amount_usdt: float = 50.0
    price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class CloseTradeRequest(BaseModel):
    position_id: str
    price: float


class SettingsRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    ai_model: Optional[str] = None
    binance_api_key: Optional[str] = None
    binance_api_secret: Optional[str] = None


class PositionSizeRequest(BaseModel):
    entry_price: float
    stop_loss: float
    capital: Optional[float] = None
    account_balance: Optional[float] = None
    risk_pct: Optional[float] = None


class BacktestRequest(BaseModel):
    pair: str = "BTC/USDT"
    timeframe: Optional[str] = None
    interval: Optional[str] = None
    starting_capital: Optional[float] = None
    initial_capital: Optional[float] = None
    risk_pct: float = 1.5
    limit: Optional[int] = None
    candle_limit: Optional[int] = None
    strategy: Optional[str] = None


class SaveDrawingsRequest(BaseModel):
    pair: str
    timeframe: str
    drawings: list[dict]


class AnalyzeZoneRequest(BaseModel):
    pair: str = "BTC/USDT"
    timeframe: str = "15m"
    zone_type: str = "DEMAND"
    proximal: float
    distal: float
    notes: Optional[str] = None


class ScannerConfigRequest(BaseModel):
    min_quote_volume_usd: Optional[float] = None
    max_spread_pct: Optional[float] = None
    min_candles: Optional[int] = None
    max_candidates: Optional[int] = None
    scan_timeframe: Optional[str] = None


async def _signal_scan_loop() -> None:
    """Screen the full eligible universe, then confirm candidates with the 11-tool analyzer."""
    while True:
        started = time.monotonic()
        SIGNAL_SCAN_STATUS.update(
            {"status": "SCANNING", "last_started_at": int(time.time()), "last_error": None}
        )
        try:
            scan_data = await market_scanner.get_scanner_data()
            pairs = scan_data.get("pairs", [])
            if scan_data.get("stale"):
                raise RuntimeError("Signal scan paused because exchange data is stale")
            candidates = [p for p in pairs if p.get("signal") in {"LONG", "SHORT"}]
            semaphore = asyncio.Semaphore(3)

            async def analyze_candidate(pair_data: dict) -> dict | None:
                async with semaphore:
                    try:
                        return await coin_analyzer.analyze_coin(
                            pair=pair_data.get("pair_name", ""),
                            timeframe=pair_data.get("scan_timeframe", "1h"),
                        )
                    except Exception:
                        logger.exception(
                            "Signal confirmation failed for %s",
                            pair_data.get("pair_name", "unknown"),
                        )
                        return None

            results = await asyncio.gather(
                *(analyze_candidate(p) for p in candidates),
                return_exceptions=True,
            )
            confirmed = sum(
                1
                for result in results
                if isinstance(result, dict) and result.get("decision") in {"LONG", "SHORT"}
            )
            completed = time.time()
            SIGNAL_SCAN_STATUS.update(
                {
                    "status": "READY",
                    "last_completed_at": int(completed),
                    "last_duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "eligible_contracts": int(scan_data.get("eligible_contract_count", 0)),
                    "analyzed_contracts": len(pairs),
                    "candidate_contracts": len(candidates),
                    "confirmed_signals": confirmed,
                    "last_error": None,
                }
            )
        except Exception as exc:
            SIGNAL_SCAN_STATUS.update(
                {
                    "status": "ERROR",
                    "last_completed_at": int(time.time()),
                    "last_duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "last_error": str(exc),
                }
            )
            logger.exception("Background signal scan failed")

        await asyncio.sleep(max(1.0, SIGNAL_SCAN_INTERVAL_SECONDS - (time.monotonic() - started)))


async def _signal_update_loop() -> None:
    """Update tracked signal outcomes from current eligible-contract prices."""
    while True:
        try:
            signal_tracker.expire_stale_signals()
            signal_tracker.expire_stale_setups()
            active = signal_tracker.get_active_signals()
            set_market_stream_priority_symbols(
                {signal["symbol"] for signal in active if signal.get("symbol")}
            )
            if active:
                tickers = await get_all_tickers()
                price_map = {
                    ticker["pair"]: (float(ticker["last_price"]), int(ticker.get("timestamp") or 0))
                    for ticker in tickers
                    if ticker.get("last_price") and not ticker.get("stale", False)
                }
                for signal in active:
                    quote = price_map.get(signal["symbol"])
                    if quote:
                        signal_tracker.update_signal(
                            signal["signal_id"], quote[0], quote[1]
                        )
        except Exception:
            logger.exception("Tracked signal refresh failed")
        await asyncio.sleep(SIGNAL_UPDATE_INTERVAL_SECONDS)


async def _market_event_loop() -> None:
    """Run fast screening on closed candles and update tracked outcomes on price events."""
    while True:
        event = await get_next_market_stream_event()
        received_at_ms = int(event.get("received_at_ms") or time.time() * 1000)
        event_latency_ms = max(0, int(time.time() * 1000) - received_at_ms)
        SIGNAL_SCAN_STATUS.update(
            {
                "event_count": SIGNAL_SCAN_STATUS["event_count"] + 1,
                "last_event_type": event.get("type"),
                "last_event_symbol": event.get("symbol"),
                "last_event_latency_ms": event_latency_ms,
            }
        )

        try:
            symbol = str(event.get("symbol") or "")
            if event.get("type") == "PRICE":
                for signal in signal_tracker.get_active_signals():
                    price = event.get("price")
                    if (
                        price
                        and float(price) > 0
                        and signal.get("symbol", "").replace("/", "").upper() == symbol
                    ):
                        signal_tracker.update_signal(
                            signal["signal_id"],
                            float(price),
                            int(event.get("event_time_ms") or 0) // 1000,
                        )
                continue

            if event.get("type") != "KLINE_CLOSED":
                continue
            screening = await market_scanner.evaluate_market_event(
                symbol, str(event.get("interval") or "")
            )
            if not screening:
                continue
            candle_time = int(screening["closed_candle_time"])
            if _EVENT_ANALYSIS_LAST_CANDLE.get(symbol) == candle_time:
                continue
            _EVENT_ANALYSIS_LAST_CANDLE[symbol] = candle_time
            if not screening["preliminary_candidate"]:
                signal_tracker.record_lifecycle_state(
                    screening["pair"],
                    screening["timeframe"],
                    "NO_TRADE",
                    "NEUTRAL",
                    "Closed-candle fast screen did not meet candidate thresholds.",
                    only_if_exists=True,
                )
                SIGNAL_SCAN_STATUS["last_analysis_latency_ms"] = event_latency_ms
                continue

            signal_tracker.record_lifecycle_state(
                screening["pair"],
                screening["timeframe"],
                "POTENTIAL",
                screening["trend"].upper(),
                (
                    "Closed-candle technical screen passed; "
                    f"RSI {screening['rsi']:.1f}, ADX {screening['adx']:.1f}, "
                    f"relative volume {screening['relative_volume']:.2f}."
                ),
            )
            analysis_started = time.perf_counter()
            analysis = await coin_analyzer.analyze_coin(
                pair=screening["pair"], timeframe=screening["timeframe"]
            )
            decision = str(analysis.get("decision") or "NO TRADE")
            lifecycle_state = str(
                analysis.get("lifecycle_state") or decision.replace(" ", "_")
            )
            direction = decision if decision in {"LONG", "SHORT"} else screening["trend"].upper()
            signal_tracker.record_lifecycle_state(
                screening["pair"],
                screening["timeframe"],
                lifecycle_state,
                direction,
                str(analysis.get("narrative") or lifecycle_state),
            )
            analysis_latency_ms = round((time.perf_counter() - analysis_started) * 1000, 1)
            SIGNAL_SCAN_STATUS["last_analysis_latency_ms"] = round(
                event_latency_ms + analysis_latency_ms, 1
            )
            if decision in {"LONG", "SHORT"}:
                SIGNAL_SCAN_STATUS["confirmed_signals"] += 1
        except Exception:
            logger.exception(
                "Event-driven signal analysis failed for %s", event.get("symbol", "unknown")
            )


@app.on_event("startup")
async def _startup_background_init():
    """Initialize dynamic exchange data, scanner, and independent signal monitors."""
    async def _bg():
        try:
            await refresh_available_pairs()
            await ping_binance()
            start_market_data_stream()
            BACKGROUND_TASKS.extend(
                [
                    asyncio.create_task(start_market_data_heartbeat()),
                    asyncio.create_task(_signal_scan_loop()),
                    asyncio.create_task(_signal_update_loop()),
                    asyncio.create_task(_market_event_loop()),
                ]
            )
        except Exception:
            logger.exception("Background market-data initialization failed")
    BACKGROUND_TASKS.append(asyncio.create_task(_bg()))


@app.on_event("shutdown")
async def _shutdown_background_tasks():
    for task in BACKGROUND_TASKS:
        task.cancel()
    if BACKGROUND_TASKS:
        await asyncio.gather(*BACKGROUND_TASKS, return_exceptions=True)
    BACKGROUND_TASKS.clear()
    await stop_market_data_stream()


# --- REST Endpoints ---

@app.get("/api/health")
async def health_check():
    m_stat = get_connection_status()
    proc = psutil.Process()
    mem_info = proc.memory_info()
    uptime = round(time.time() - SERVER_START_TIME, 1)

    trades_mtime = None
    trades_path = DATA_DIR / "paper_trades.json"
    if trades_path.exists():
        try:
            trades_mtime = int(trades_path.stat().st_mtime)
        except Exception:
            pass

    return {
        "status": "ok",
        "healthy": True,
        "app": "AlphaQuant AI Pro Trading Terminal",
        "version": "2.0.0",
        "uptime_seconds": uptime,
        "pid": os.getpid(),
        "process": {
            "pid": os.getpid(),
            "memory_mb": round(mem_info.rss / (1024 * 1024), 2),
            "cpu_percent": round(proc.cpu_percent(interval=None), 1),
            "uptime_seconds": uptime,
        },
        "market_data": {
            "status": m_stat.get("status", "UNKNOWN"),
            "is_connected": m_stat.get("is_connected", False),
            "pairs_loaded": m_stat.get("pairs_count", 0),
            "binance_connectivity": "CONNECTED" if m_stat.get("status") == "LIVE" else m_stat.get("status", "DISCONNECTED"),
            "stale_seconds": m_stat.get("stale_seconds"),
            "consecutive_failures": m_stat.get("consecutive_failures", 0),
            "last_error": m_stat.get("last_error"),
            "last_heartbeat": m_stat.get("last_update_ts"),
            "exchange_info_stale": m_stat.get("exchange_info_stale"),
            "exchange_info_updated_ts": m_stat.get("exchange_info_updated_ts"),
            "market_stream": m_stat.get("market_stream", {}),
        },
        "signal_scanner": dict(SIGNAL_SCAN_STATUS),
        "signal_push": {
            "connected_clients": len(SIGNAL_EVENT_SUBSCRIBERS),
            "published_events": SIGNAL_EVENT_PUBLISHED,
            "dropped_events": SIGNAL_EVENT_DROPPED,
        },
        "scanner": {
            "status": SIGNAL_SCAN_STATUS["status"],
            "last_scan_timestamp": SIGNAL_SCAN_STATUS["last_completed_at"],
            "scan_duration_ms": SIGNAL_SCAN_STATUS["last_duration_ms"],
            "eligible_contracts": SIGNAL_SCAN_STATUS["eligible_contracts"],
            "analyzed_contracts": SIGNAL_SCAN_STATUS["analyzed_contracts"],
            "candidate_contracts": SIGNAL_SCAN_STATUS["candidate_contracts"],
        },
        "ai_engine": {
            "gemini_active": bool(config.gemini_api_key),
            "model": config.ai_model,
            "status": "CONFIGURED" if config.gemini_api_key else "BUILTIN_ONLY",
        },
        "paper_trading": {
            "balance": round(paper_trader.balance, 2),
            "open_positions": len(paper_trader.positions),
            "total_trades": len(paper_trader.trade_history),
        },
        "data_persistence": {
            "paper_trades_file": trades_path.exists(),
            "signal_history_file": (DATA_DIR / "signal_history.json").exists(),
            "drawings_file": DRAWINGS_FILE.exists(),
            "watchlist_file": WATCHLIST_FILE.exists(),
            "last_trades_modified": trades_mtime,
        },
        # Preserved fields for backward compatibility
        "gemini_active": bool(config.gemini_api_key),
        "market_status": m_stat["status"],
        "pairs_loaded": m_stat["pairs_count"],
    }


@app.get("/api/market-status")
async def market_status_endpoint():
    """Exchange connectivity telemetry (LIVE, DELAYED, DISCONNECTED) & staleness info."""
    return get_connection_status()


@app.get("/api/pairs")
async def list_pairs():
    await refresh_available_pairs()
    return {
        "pairs": AVAILABLE_PAIRS,
        "contracts": [PAIR_RULES[symbol] for symbol in PAIR_MAP.values() if symbol in PAIR_RULES],
        "default_pair": "BTC/USDT",
        "timeframes": TIMEFRAMES,
        "default_timeframe": "15m",
    }


@app.get("/api/ticker")
async def get_ticker(pair: Optional[str] = None):
    try:
        if pair:
            return await get_ticker_24h(pair)
        return await get_all_tickers()
    except Exception as e:
        logger.error(f"Error getting ticker for {pair}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/candles")
async def get_candles(
    pair: str = "BTC/USDT",
    interval: str = "15m",
    limit: int = 250,
    endTime: Optional[int] = None,
    startTime: Optional[int] = None,
    days: Optional[int] = None,
):
    try:
        if days and days > 1:
            candles = await get_historical_klines_paginated(pair, interval, days=days)
        else:
            candles = await get_klines(pair, interval, limit, end_time=endTime, start_time=startTime)
        return {
            "pair": pair,
            "interval": interval,
            "candles": candles,
            "count": len(candles),
            "oldest_timestamp": candles[0]["time"] if candles else None,
            "newest_timestamp": candles[-1]["time"] if candles else None,
        }
    except Exception as e:
        logger.error(f"Error fetching candles: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze-zone")
async def analyze_manual_zone_endpoint(req: AnalyzeZoneRequest):
    """Institutional Zone Analysis powered by Gemini AI and Quantitative SMC Engine."""
    try:
        candles = await get_klines(req.pair, req.timeframe, limit=120)
        confirmed_candles = candles[:-1] if len(candles) > 35 else candles

        # Check existing drawings for overlap analysis
        user_drawings = []
        try:
            all_drawings = safe_load_json(DRAWINGS_FILE, default={})
            if isinstance(all_drawings, dict):
                user_drawings = all_drawings.get(f"{req.pair}_{req.timeframe}", []) or all_drawings.get(req.pair, [])
        except Exception:
            pass

        quant_result = supply_demand_engine.evaluate_manual_zone(
            confirmed_candles,
            zone_type=req.zone_type,
            val1=req.proximal,
            val2=req.distal,
            timeframe=req.timeframe,
            overlapping_zones_count=len([d for d in user_drawings if d.get("zoneType") == req.zone_type]),
        )

        # Overlap analysis across all drawings if multiple exist
        overlap_summary = []
        if user_drawings:
            full_analysis = supply_demand_engine.analyze_manual_zones_and_overlaps(
                candles=confirmed_candles,
                manual_drawings=user_drawings,
                timeframe=req.timeframe,
            )
            overlap_summary = full_analysis.get("overlaps", [])
            quant_result["overlap_details"] = overlap_summary

        # Build prompt for Gemini AI
        ai_narrative = ""
        if config.gemini_api_key:
            try:
                system_instruction = (
                    "You are an Elite Institutional Supply & Demand and Smart Money Concepts (SMC) quantitative trader. "
                    "Analyze the user's manually drawn chart zone based on order flow displacement, freshness, "
                    "mitigation status, and institutional liquidity."
                )
                user_prompt = f"""
Pair: {req.pair} | Timeframe: {req.timeframe}
Zone Type: {quant_result['zone_type']}
Proximal Boundary (First entry edge): ${quant_result['proximal']:,.2f}
Distal Boundary (Stop invalidation edge): ${quant_result['distal']:,.2f}
Zone Height: ${quant_result['zone_height']:,.2f} ({quant_result['zone_height_pct']}%, {quant_result['height_in_atr']}x ATR)
Current Market Price: ${quant_result['current_price']:,.2f} ({quant_result['distance_pct']}% away)
Retest Count: {quant_result['retest_count']}
Mitigation Depth: {quant_result['mitigation_pct']}%
Classification: {quant_result['classification']} (Score: {quant_result['quality_score']}/100)
Reason: {quant_result.get('classification_reason', '')}

Provide a concise, professional institutional assessment:
1. Executive Verdict (Is this an institutional order block or retail liquidity trap?)
2. Structural Confirmation needed before entry (e.g., LTF CHoCH or sweep)
3. Risk Management & Invalidation guidance.
"""
                async with httpx.AsyncClient(timeout=15.0) as client:
                    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.ai_model}:generateContent?key={config.gemini_api_key}"
                    payload = {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [{"text": f"{system_instruction}\n\n{user_prompt}"}],
                            }
                        ]
                    }
                    resp = await client.post(gemini_url, json=payload)
                    if resp.status_code == 200:
                        res_data = resp.json()
                        candidates = res_data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                ai_narrative = parts[0].get("text", "")
            except Exception as e:
                logger.warning(f"Gemini manual zone analysis fallback: {e}")

        if not ai_narrative:
            ai_narrative = (
                f"### Institutional Zone Evaluation: {quant_result['classification']}\n"
                f"* **Zone Structure**: {quant_result['zone_type']} from ${quant_result['distal']:,.2f} to ${quant_result['proximal']:,.2f} "
                f"({quant_result['zone_height_pct']}% width, {quant_result['height_in_atr']}x ATR).\n"
                f"* **Status**: Classified `{quant_result['classification']}` with {quant_result['mitigation_pct']}% mitigation across {quant_result['retest_count']} touch(es).\n"
                f"* **Reason**: {quant_result.get('classification_reason', '')}\n"
                f"* **Recommended Plan**: Entry at ${quant_result['recommended_entry']:,.2f} | Stop Loss at ${quant_result['structural_sl']:,.2f} "
                f"| TP1 at ${quant_result['tp1']:,.2f} | TP2 at ${quant_result['tp2']:,.2f}.\n"
                f"* **Invalidation**: Clean candle close beyond ${quant_result['invalidation_level']:,.2f} invalidates this setup."
            )

        quant_result["ai_analysis"] = ai_narrative
        return quant_result
    except Exception as e:
        logger.error(f"Error analyzing zone: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/validate-zones")
@app.post("/api/validate-zones")
async def validate_manual_zones_endpoint(pair: str = "BTC/USDT", timeframe: str = "15m"):
    """Validates all manual user drawings and returns overlap analysis."""
    try:
        candles = await get_klines(pair, timeframe, limit=120)
        confirmed_candles = candles[:-1] if len(candles) > 35 else candles
        user_drawings = []
        all_drawings = safe_load_json(DRAWINGS_FILE, default={})
        if isinstance(all_drawings, dict):
            user_drawings = all_drawings.get(f"{pair}_{timeframe}", []) or all_drawings.get(pair, [])
            if not user_drawings and "/" in pair:
                clean_p = pair.replace("/", "")
                user_drawings = all_drawings.get(f"{clean_p}_{timeframe}", []) or all_drawings.get(clean_p, [])

        return supply_demand_engine.analyze_manual_zones_and_overlaps(
            candles=confirmed_candles,
            manual_drawings=user_drawings,
            timeframe=timeframe,
        )
    except Exception as e:
        logger.error(f"Error validating zones for {pair}: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/indicators")
async def get_indicators_endpoint(pair: str = "BTC/USDT", interval: str = "15m", limit: int = 250):
    try:
        candles = await get_klines(pair, interval, limit)
        return compute_indicators(candles)
    except Exception as e:
        logger.error(f"Error calculating indicators: {e}")
        return {"series": {}, "latest": {}, "error": str(e)}


@app.get("/api/supply-demand")
async def get_supply_demand_endpoint(pair: str = "BTC/USDT", interval: str = "15m", limit: int = 250):
    """Institutional Supply & Demand Engine endpoint (uses confirmed closed candles)."""
    try:
        candles = await get_klines(pair, interval, limit)
        eval_candles = candles[:-1] if len(candles) > 35 else candles
        return supply_demand_engine.detect_zones(eval_candles, timeframe=interval)
    except Exception as e:
        logger.error(f"Error calculating supply/demand: {e}")
        return {"demand": [], "supply": [], "summary": {}, "error": str(e)}


@app.get("/api/smc")
async def get_smc_endpoint(pair: str = "BTC/USDT", interval: str = "15m", limit: int = 250):
    """Smart Money Concepts (BOS, CHoCH, OB, FVG, Liquidity) endpoint (confirmed closed bars)."""
    try:
        candles = await get_klines(pair, interval, limit)
        eval_candles = candles[:-1] if len(candles) > 35 else candles
        return smc_engine.analyze(eval_candles)
    except Exception as e:
        logger.error(f"Error calculating SMC: {e}")
        return {"swings": [], "bos_choch": [], "order_blocks": [], "fvg": [], "liquidity": [], "summary": {}, "error": str(e)}


@app.get("/api/orderflow")
async def get_orderflow_endpoint(pair: str = "BTC/USDT", interval: str = "15m", limit: int = 250):
    """Session Volume Profile, Footprint decomposition, Imbalances, and real tick delta."""
    try:
        candles = await get_klines(pair, interval, limit)
        return await order_flow_engine.compute_advanced_order_flow(pair, candles)
    except Exception as e:
        logger.error(f"Error calculating orderflow: {e}")
        return {"session_profile": {}, "footprint_bars": [], "taker_delta": {}, "summary": {}, "error": str(e)}


@app.get("/api/liquidity")
async def get_liquidity_endpoint(pair: str = "BTC/USDT", interval: str = "15m"):
    """L2 Order Book Depth, Walls, Imbalance, and EQH/EQL Liquidity Sweeps."""
    try:
        candles = await get_klines(pair, interval, limit=120)
        return await liquidity_engine.get_liquidity_summary(pair, candles)
    except Exception as e:
        logger.error(f"Error calculating liquidity: {e}")
        return {"order_book_depth": {}, "liquidity_walls": [], "summary": {}, "error": str(e)}


@app.get("/api/market-levels")
async def get_market_levels_endpoint(pair: str = "BTC/USDT", interval: str = "15m"):
    """Session VWAP series, PDH/PDL, PWH/PWL, and active Market Sessions."""
    try:
        intraday = await get_klines(pair, interval, limit=200)
        daily = await get_klines(pair, "1d", limit=15)
        return compute_market_levels(intraday, daily)
    except Exception as e:
        logger.error(f"Error calculating market levels: {e}")
        return {"pdh": None, "pdl": None, "pwh": None, "pwl": None, "vwap_series": [], "sessions": [], "error": str(e)}


@app.get("/api/signal")
@app.get("/api/signals")
async def get_signal_endpoint(pair: str = "BTC/USDT", interval: str = "15m"):
    """Deterministic Signal Engine with 3 entry models (A, B, C), multi-targets, and score."""
    try:
        candles = await get_klines(pair, interval, limit=200)
        eval_candles = (
            candles
            if candles and candles[-1].get("is_closed", False)
            else candles[:-1] if len(candles) > 35 else candles
        )
        ticker = await get_ticker_24h(pair)
        now_ts = int(time.time())
        interval_seconds = TIMEFRAME_SECONDS.get(interval)
        candle_fresh = bool(
            interval_seconds
            and len(eval_candles) >= 35
            and not eval_candles[-1].get("stale", False)
            and 0 <= now_ts - int(eval_candles[-1].get("time", 0)) <= interval_seconds * 2
        )
        ticker_fresh = bool(
            ticker.get("last_price")
            and not ticker.get("stale", False)
            and 0 <= now_ts - int(ticker.get("timestamp", 0)) <= 15
        )
        if not candle_fresh or not ticker_fresh:
            missing_data = []
            if not candle_fresh:
                missing_data.append("Latest closed candle data is missing or stale")
            if not ticker_fresh:
                missing_data.append("Market ticker data is missing or stale")
            return {
                "state": "NO_TRADE",
                "direction": "NEUTRAL",
                "setup_score": 0,
                "primary_model": "MODEL_B_CONFIRMATION",
                "entry_price": None,
                "stop_loss": None,
                "tp1": None,
                "tp2": None,
                "data_availability": "STALE_OR_MISSING",
                "missing_data": missing_data,
                "validation_reason": "; ".join(missing_data),
            }
        current_price = (eval_candles[-1]["close"] if eval_candles else (candles[-1]["close"] if candles else 0.0))

        sd = supply_demand_engine.detect_zones(eval_candles, timeframe=interval)
        smc = smc_engine.analyze(eval_candles)
        ind = compute_indicators(eval_candles)
        of_data = await order_flow_engine.compute_advanced_order_flow(pair, eval_candles)
        liq_data = await liquidity_engine.get_liquidity_summary(pair, eval_candles)
        mtf_data = await mtf_engine.analyze_matrix(pair, timeframes=["5m", "15m", "1h", "4h", "1d"])

        signal = signal_engine.evaluate(
            pair=pair,
            timeframe=interval,
            current_price=current_price,
            sd_data=sd,
            smc_data=smc,
            ind_data=ind,
            orderflow_data=of_data,
            liquidity_data=liq_data,
            mtf_data=mtf_data,
        )
        return signal
    except Exception as e:
        logger.error(f"Error generating signal: {e}")
        return {
            "state": "NO_TRADE",
            "direction": "NEUTRAL",
            "setup_score": 0,
            "primary_model": "MODEL_B_CONFIRMATION",
            "entry_price": None,
            "stop_loss": None,
            "tp1": None,
            "tp2": None,
            "trade_plan_valid": False,
            "validation_reason": f"Signal analysis failed: {e}",
            "error": str(e),
        }


@app.get("/api/mtf")
async def get_mtf_endpoint(pair: str = "BTC/USDT", tfs: Optional[str] = None):
    """Multi-Timeframe alignment matrix with configurable timeframes."""
    try:
        tf_list = [t.strip() for t in tfs.split(",") if t.strip()] if tfs else None
        return await mtf_engine.analyze_matrix(pair, timeframes=tf_list)
    except Exception as e:
        logger.error(f"Error generating MTF matrix: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Top 100 & Market Scanner Endpoints ---

@app.get("/api/top100")
async def get_top100_endpoint(limit: int = 100):
    """Dynamic Top 100 cryptocurrency universe ranked by transparent multi-factor quantitative model."""
    try:
        return await market_scanner.get_top100_universe(limit=limit)
    except Exception as e:
        logger.error(f"Error fetching top100 universe: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scanner")
async def get_scanner_endpoint():
    """Full 17-column quantitative cryptocurrency scanner with filters."""
    try:
        return await market_scanner.get_scanner_data()
    except Exception as e:
        logger.error(f"Error in scanner endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scanner/config")
async def get_scanner_config_endpoint():
    """Get configurable scanner eligibility thresholds and ranking methodology."""
    return market_scanner.get_config()


@app.post("/api/scanner/config")
async def update_scanner_config_endpoint(req: ScannerConfigRequest):
    """Update configurable scanner eligibility thresholds."""
    updates = req.model_dump(exclude_none=True)
    return {"success": True, "config": market_scanner.update_config(updates)}


@app.get("/api/scanner/opportunities")
async def get_scanner_opportunities_endpoint():
    """Categorized trading opportunities for AI Market Scanner."""
    try:
        return await market_scanner.get_categorized_opportunities()
    except Exception as e:
        logger.error(f"Error in scanner opportunities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Individual Coin Analysis & Exact Trade Plan ---

@app.get("/api/coin-analysis")
@app.get("/api/coin/{pair:path}/analysis")
async def get_coin_analysis_endpoint(pair: str = "BTC/USDT", timeframe: str = "15m", mtf_tfs: Optional[str] = None):
    """Comprehensive individual coin analysis with multi-timeframe synthesis, exact trade plan, and narrative."""
    try:
        # Standardize pair if path parameter is passed without slash (e.g. BTCUSDT)
        norm_pair = pair
        if "/" not in norm_pair and norm_pair.endswith("USDT"):
            norm_pair = f"{norm_pair[:-4]}/USDT"
        tfs = [t.strip() for t in mtf_tfs.split(",") if t.strip()] if mtf_tfs else None
        return await coin_analyzer.analyze_coin(pair=norm_pair, timeframe=timeframe, mtf_timeframes=tfs)
    except Exception as e:
        logger.error(f"Error analyzing coin {pair}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Signal Engine Tracking & Performance Analytics ---

@app.get("/api/signals/active")
@app.get("/api/scanner/signals")
async def get_active_signals_endpoint():
    """Returns currently active signals with lifecycle updates evaluated against current market prices."""
    try:
        signal_tracker.expire_stale_signals()
        active = signal_tracker.get_active_signals()
        if active:
            tickers = await get_all_tickers()
            price_map = {
                ticker["pair"]: (float(ticker["last_price"]), int(ticker.get("timestamp") or 0))
                for ticker in tickers
                if ticker.get("last_price") and not ticker.get("stale", False)
            }
            for s in active:
                quote = price_map.get(s["symbol"])
                if quote:
                    signal_tracker.update_signal(
                        s["signal_id"], quote[0], quote[1]
                    )
    except Exception as e:
        logger.debug(f"Active signal update warning: {e}")
    return {
        "signals": signal_tracker.get_active_signals(),
        "setups": signal_tracker.get_active_setups(),
    }


@app.get("/api/signals/history")
@app.get("/api/signal/history")
async def get_signals_history_endpoint(symbol: Optional[str] = None, limit: int = 100):
    """Returns historical tracked signals with outcomes."""
    return {"history": signal_tracker.get_history(symbol=symbol, limit=limit)}


@app.get("/api/signals/performance")
async def get_signals_performance_endpoint():
    """Returns statistically measured performance metrics (win rate, profit factor, max DD, R-multiples)."""
    return signal_tracker.get_performance_stats()


# --- Watchlist Management ---

class WatchlistRequest(BaseModel):
    category: str = "default"
    symbols: list[str] = []


@app.get("/api/watchlist")
async def get_watchlist_endpoint():
    """Returns user watchlists."""
    default_watchlists = {
        "Core 10": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "NEAR/USDT"],
        "DeFi": ["UNI/USDT", "AAVE/USDT", "MKR/USDT", "CRV/USDT", "PENDLE/USDT", "LDO/USDT"],
        "Layer 1": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT", "APT/USDT", "NEAR/USDT", "AVAX/USDT", "SEI/USDT"],
        "Meme": ["1000PEPE/USDT", "1000SHIB/USDT", "1000BONK/USDT", "1000FLOKI/USDT", "DOGE/USDT", "WIF/USDT"],
    }
    if WATCHLIST_FILE.exists():
        data = safe_load_json(WATCHLIST_FILE, default=None)
        if isinstance(data, dict):
            return {"watchlists": data}
    return {"watchlists": default_watchlists}


@app.post("/api/watchlist")
async def save_watchlist_endpoint(req: WatchlistRequest):
    """Saves or updates user watchlist category."""
    data = safe_load_json(WATCHLIST_FILE, default=dict)
    if not isinstance(data, dict):
        data = {}
    data[req.category] = req.symbols
    try:
        atomic_write_json(WATCHLIST_FILE, data, backup=True)
        return {"success": True, "watchlists": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# --- Risk Management Endpoints ---

@app.get("/api/risk")
async def get_risk_status():
    return risk_manager.get_status()


@app.post("/api/risk/size")
@app.post("/api/risk/position-size")
async def calculate_size(req: PositionSizeRequest):
    cap = req.capital or req.account_balance or 1000.0
    pct = req.risk_pct or 1.0
    return risk_manager.calculate_position_size(
        entry_price=req.entry_price,
        stop_loss=req.stop_loss,
        capital=cap,
        risk_pct=pct,
    )


@app.post("/api/risk/kill-switch")
async def trigger_kill_switch():
    return risk_manager.emergency_kill_switch()


@app.post("/api/risk/unlock")
async def reset_risk_lock():
    return risk_manager.unlock_trading()


# --- Historical Backtesting ---

@app.post("/api/backtest")
async def run_backtest_endpoint(req: BacktestRequest):
    try:
        tf = req.timeframe or req.interval or "15m"
        lim = min(req.limit or req.candle_limit or 250, 2000)
        cap = req.starting_capital or req.initial_capital or 1000.0
        candles = await get_klines(req.pair, tf, limit=min(lim, 1000))
        if lim > 1000 and candles:
            extra_need = min(lim - len(candles), 1000)
            if extra_need > 0:
                older = await get_klines(req.pair, tf, limit=extra_need, end_time=int(candles[0]["time"]) - 1)
                if older:
                    seen_t = set()
                    merged = []
                    for c in older + candles:
                        if c["time"] not in seen_t:
                            seen_t.add(c["time"])
                            merged.append(c)
                    candles = sorted(merged, key=lambda x: x["time"])
        if len(candles) > 51:
            candles = candles[:-1]
        return await asyncio.to_thread(
            backtest_engine.run_backtest,
            candles=candles,
            pair=req.pair,
            timeframe=tf,
            starting_capital=cap,
            risk_per_trade_pct=req.risk_pct,
        )
    except Exception as e:
        logger.error(f"Error running backtest: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Drawing Tools Persistence ---

@app.get("/api/drawings")
async def get_drawings(pair: str = "BTC/USDT", timeframe: str = "15m"):
    if not DRAWINGS_FILE.exists():
        return {"drawings": []}
    data = safe_load_json(DRAWINGS_FILE, default=dict)
    if isinstance(data, dict):
        key = f"{pair}_{timeframe}"
        return {"drawings": data.get(key, [])}
    return {"drawings": []}


@app.post("/api/drawings")
async def save_drawings(req: SaveDrawingsRequest):
    data = safe_load_json(DRAWINGS_FILE, default=dict)
    if not isinstance(data, dict):
        data = {}

    key = f"{req.pair}_{req.timeframe}"
    data[key] = req.drawings

    atomic_write_json(DRAWINGS_FILE, data, backup=True)
    return {"success": True, "count": len(req.drawings)}


# --- Context-Aware AI Chat ---

@app.post("/api/chat")
async def chat_with_ai(req: ChatRequest):
    try:
        coin_analysis = await coin_analyzer.analyze_coin(pair=req.pair, timeframe=req.interval)
        port = paper_trader.get_summary()

        response = await generate_ai_response(
            user_message=req.message,
            pair=req.pair,
            interval=req.interval,
            current_price=coin_analysis.get("current_price", 0.0),
            indicators=coin_analysis.get("technical", {}).get("indicators", {}),
            sd_data=coin_analysis.get("supply_demand"),
            smc_data=coin_analysis.get("market_structure"),
            orderflow_data=coin_analysis.get("order_flow"),
            liquidity_data=coin_analysis.get("liquidity"),
            mtf_data=coin_analysis.get("multi_timeframe"),
            signal_data=coin_analysis.get("trade_plan"),
            portfolio_data=port,
            manual_sd=coin_analysis.get("manual_zone_analysis"),
            tools=coin_analysis.get("tools"),
            scenarios=coin_analysis.get("future_scenarios"),
            combined_decision=coin_analysis.get("combined_decision"),
        )
        return response
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Paper Trading Endpoints ---

@app.get("/api/paper/summary")
async def get_paper_summary():
    price_refresh_error = None
    try:
        tickers = await get_all_tickers()
        now_ts = int(time.time())
        prices = {}
        for ticker in tickers:
            price = float(ticker.get("last_price") or 0.0)
            timestamp = int(ticker.get("timestamp") or 0)
            if (
                ticker.get("stale", True)
                or not math.isfinite(price)
                or price <= 0
                or timestamp <= 0
                or now_ts - timestamp < 0
                or now_ts - timestamp > 15
            ):
                continue
            prices[ticker["pair"]] = price
        paper_trader.update_prices(prices)
        missing_price_pairs = [
            position.get("pair", "Unknown")
            for position in paper_trader.positions
            if position.get("pair") not in prices
        ]
        if missing_price_pairs:
            price_refresh_error = (
                "Fresh market prices are unavailable for: "
                + ", ".join(sorted(set(missing_price_pairs)))
                + ". Position marks may be stale."
            )
    except Exception as exc:
        logger.warning("Paper portfolio price refresh failed: %s", exc)
        price_refresh_error = "Fresh market prices could not be loaded. Position marks may be stale."
    summary = paper_trader.get_summary()
    max_positions = risk_manager.settings.max_open_positions
    open_positions_count = summary["open_positions_count"]
    summary.update(
        {
            "current_open_positions": open_positions_count,
            "max_open_positions": max_positions,
            "available_position_slots": max(0, max_positions - open_positions_count),
            "closeable_positions": summary["open_positions"],
            "market_data_refresh_error": price_refresh_error,
        }
    )
    return summary


@app.post("/api/paper/trade")
async def execute_paper_trade(req: TradeRequest):
    side = req.side.upper()
    direction = "LONG" if side in {"BUY", "LONG"} else "SHORT" if side in {"SELL", "SHORT"} else ""
    if not direction:
        raise HTTPException(status_code=400, detail="Paper trade side must be BUY/LONG or SELL/SHORT.")
    if not math.isfinite(req.amount_usdt) or req.amount_usdt <= 0:
        raise HTTPException(status_code=400, detail="Position size must be greater than zero.")

    ticker = await get_ticker_24h(req.pair)
    price_timestamp = int(ticker.get("timestamp") or 0)
    now_ts = int(time.time())
    current_price = float(ticker.get("last_price") or 0.0)
    if (
        ticker.get("stale", True)
        or not math.isfinite(current_price)
        or current_price <= 0
        or price_timestamp <= 0
        or now_ts - price_timestamp < 0
        or now_ts - price_timestamp > 15
    ):
        raise HTTPException(
            status_code=400,
            detail="Paper trade rejected: current market price is missing or stale.",
        )

    matching_signals = [
        signal
        for signal in signal_tracker.get_active_signals()
        if signal.get("symbol") == req.pair
        and signal.get("direction") == direction
        and signal.get("state") == "CONFIRMED"
        and signal.get("filled") is True
        and signal.get("market_data_fresh") is True
        and signal.get("mtf_is_fresh") is True
    ]
    signal = max(matching_signals, key=lambda item: int(item.get("created_at") or 0), default=None)
    if signal is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Paper trade rejected: no fresh, MTF-validated, filled {direction} signal "
                f"exists for {req.pair}."
            ),
        )

    entry_range = signal.get("entry_range")
    if not isinstance(entry_range, (list, tuple)) or len(entry_range) != 2:
        raise HTTPException(
            status_code=400,
            detail="Paper trade rejected: the confirmed signal has no valid entry range.",
        )
    try:
        range_low, range_high = sorted(float(value) for value in entry_range)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Paper trade rejected: the confirmed signal has no valid entry range.",
        )
    if not range_low <= current_price <= range_high:
        raise HTTPException(
            status_code=400,
            detail="Paper trade rejected: current price is outside the confirmed signal entry range.",
        )

    plan_valid, plan_reason, trade_risk_reward = validate_trade_plan(
        direction,
        current_price,
        signal.get("stop_loss"),
        signal.get("tp1"),
        signal.get("tp2"),
        signal.get("tp3"),
        risk_manager.settings.min_risk_reward,
    )
    if not plan_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Paper trade rejected: {plan_reason}",
        )

    if any(position.get("signal_id") == signal["signal_id"] for position in paper_trader.positions):
        raise HTTPException(
            status_code=400,
            detail="Paper trade rejected: this signal already has an open paper position.",
        )

    current_open_positions_count = len(paper_trader.positions)
    valid, reason = risk_manager.validate_trade(
        trade_risk_reward=trade_risk_reward,
        current_open_positions_count=current_open_positions_count,
        requested_amount_usd=req.amount_usdt,
    )
    if not valid:
        if reason.startswith("Maximum open positions limit reached"):
            max_positions = risk_manager.settings.max_open_positions
            available_slots = max(0, max_positions - current_open_positions_count)
            closeable_positions = ", ".join(
                f"{position.get('pair', 'Unknown')} {position.get('side', '')} "
                f"({position.get('id', 'unknown id')})"
                for position in paper_trader.positions
            ) or "none"
            reason = (
                f"Maximum open positions limit reached. Current open positions: "
                f"{current_open_positions_count}; maximum allowed positions: {max_positions}; "
                f"available position slots: {available_slots}. "
                f"Positions that can be closed: {closeable_positions}."
            )
        raise HTTPException(status_code=400, detail=f"Risk check failed: {reason}")

    res = paper_trader.open_position(
        pair=req.pair,
        side=side,
        amount_usdt=req.amount_usdt,
        current_price=current_price,
        sl=float(signal["stop_loss"]),
        tp1=float(signal["tp1"]),
        tp2=float(signal["tp2"]),
        tp3=float(signal["tp3"]),
        signal_id=str(signal["signal_id"]),
        timeframe=str(signal["timeframe"]),
        invalidation_condition=str(signal.get("invalidation_condition") or ""),
    )
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res


@app.post("/api/paper/close")
async def close_paper_trade(req: CloseTradeRequest):
    res = paper_trader.close_position(req.position_id, req.price)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])

    # Record trade into risk manager
    pnl = res.get("closed_trade", {}).get("realized_pnl", 0.0)
    risk_manager.record_closed_trade(pnl)
    return res


# --- Settings & Configuration ---

@app.get("/api/settings")
async def get_settings():
    return config.to_dict()


@app.post("/api/settings")
async def save_settings(req: SettingsRequest):
    config.update_keys(
        gemini_api_key=req.gemini_api_key,
        ai_model=req.ai_model,
        binance_key=req.binance_api_key,
        binance_secret=req.binance_api_secret,
    )
    return {"success": True, "settings": config.to_dict()}


# --- Compatibility Aliases ---

@app.get("/api/confluence")
async def get_confluence_alias(pair: str = "BTC/USDT", interval: str = "15m"):
    try:
        candles = await get_klines(pair, interval, limit=100)
        return evaluate_confluence(candles)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/config")
async def get_config_alias():
    return config.to_dict()


@app.post("/api/config")
async def save_config_alias(req: SettingsRequest):
    return await save_settings(req)


@app.get("/api/paper/portfolio")
async def get_paper_portfolio_alias():
    return await get_paper_summary()


@app.post("/api/paper/close/{pos_id}")
async def close_paper_pos_alias(pos_id: str):
    target_pair = "BTC/USDT"
    for pos in paper_trader.positions:
        if pos.id == pos_id:
            target_pair = pos.pair
            break
    ticker = await get_ticker_24h(target_pair)
    price = ticker.get("last_price", 0.0) if ticker else 0.0
    res = paper_trader.close_position(pos_id, price)
    return res



# --- WebSocket Live Stream ---

@app.websocket("/ws/live")
async def websocket_live_stream(websocket: WebSocket):
    await websocket.accept()
    ws_logger.info("Client connected to /ws/live from %s", websocket.client)
    active_pair = "BTC/USDT"
    active_interval = "15m"
    signal_events_only = False
    next_tick_at = 0.0
    signal_queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    SIGNAL_EVENT_SUBSCRIBERS.add(signal_queue)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
            except asyncio.TimeoutError:
                msg = None
            if msg is not None:
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    ws_logger.warning("Ignoring invalid JSON from /ws/live client")
                    data = {}
                if isinstance(data, dict):
                    pair = data.get("pair")
                    interval = data.get("interval")
                    if isinstance(pair, str) and pair:
                        active_pair = pair
                    if isinstance(interval, str) and interval:
                        active_interval = interval
                    if "signal_events_only" in data:
                        new_signal_events_only = bool(data["signal_events_only"])
                        if signal_events_only and not new_signal_events_only:
                            next_tick_at = 0.0
                        signal_events_only = new_signal_events_only
                    ws_logger.debug(
                        "WS client subscription: %s %s (signal-only=%s)",
                        active_pair,
                        active_interval,
                        signal_events_only,
                    )

            while True:
                try:
                    await websocket.send_json(signal_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if not signal_events_only and time.monotonic() >= next_tick_at:
                try:
                    ticker = await get_ticker_24h(active_pair)
                    candles = await get_klines(active_pair, active_interval, limit=2)
                    latest_candle = candles[-1] if candles else None
                    conn = get_connection_status()

                    await websocket.send_json(
                        {
                            "type": "tick",
                            "pair": active_pair,
                            "interval": active_interval,
                            "ticker": ticker,
                            "candle": latest_candle,
                            "connection_status": conn["status"],
                        }
                    )
                except Exception:
                    ws_logger.exception("WS market-data push failed")
                next_tick_at = time.monotonic() + 2.0

            await asyncio.sleep(0.05)

    except WebSocketDisconnect:
        ws_logger.info("Client disconnected from /ws/live (%s)", websocket.client)
    except Exception as e:
        ws_logger.warning("WebSocket error for client %s: %s", websocket.client, e)
    finally:
        SIGNAL_EVENT_SUBSCRIBERS.discard(signal_queue)


# --- Serve Frontend Single Page App ---

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    if (FRONTEND_DIR / "css").exists():
        app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
    if (FRONTEND_DIR / "js").exists():
        app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")

    @app.get("/")
    async def serve_index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
