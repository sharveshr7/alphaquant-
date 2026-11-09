"""FastAPI backend server for Advanced AI Supply & Demand Trading Terminal."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ai_assistant import generate_ai_response
from .backtest_engine import backtest_engine
from .config import config
from .market_data import (
    AVAILABLE_PAIRS,
    TIMEFRAMES,
    compute_market_levels,
    get_all_tickers,
    get_historical_klines_paginated,
    get_klines,
    get_ticker_24h,
)
from .liquidity_engine import liquidity_engine
from .multi_timeframe import mtf_engine
from .order_flow import order_flow_engine
from .paper_trader import paper_trader
from .risk_manager import risk_manager
from .signal_engine import signal_engine
from .smc_engine import smc_engine
from .strategy_engine import evaluate_confluence
from .supply_demand import supply_demand_engine
from .technical_analysis import compute_indicators

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")

app = FastAPI(title="AlphaQuant Advanced AI Trading Terminal", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DRAWINGS_FILE = Path(__file__).resolve().parent.parent / "drawings.json"


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


# --- REST Endpoints ---

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "app": "AlphaQuant Advanced AI Trading Terminal",
        "version": "2.0.0",
        "gemini_active": bool(config.gemini_api_key),
    }


@app.get("/api/pairs")
async def list_pairs():
    return {
        "pairs": AVAILABLE_PAIRS,
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
        quant_result = supply_demand_engine.evaluate_manual_zone(
            candles,
            zone_type=req.zone_type,
            val1=req.proximal,
            val2=req.distal,
            timeframe=req.timeframe,
        )

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
Calculated Lifecycle: {quant_result['lifecycle_state']}
Deterministic Score: {quant_result['quality_score']}/100

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
                f"* **State**: Currently `{quant_result['lifecycle_state']}` with {quant_result['mitigation_pct']}% mitigation across {quant_result['retest_count']} retests.\n"
                f"* **Recommended Plan**: Entry at ${quant_result['recommended_entry']:,.2f} | Stop Loss at ${quant_result['structural_sl']:,.2f} "
                f"| TP1 at ${quant_result['tp1']:,.2f} | TP2 at ${quant_result['tp2']:,.2f}.\n"
                f"* **Invalidation**: Clean candle close beyond ${quant_result['invalidation_level']:,.2f} invalidates this setup."
            )

        quant_result["ai_analysis"] = ai_narrative
        return quant_result
    except Exception as e:
        logger.error(f"Error analyzing zone: {e}")
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
    """Institutional Supply & Demand Engine endpoint."""
    try:
        candles = await get_klines(pair, interval, limit)
        return supply_demand_engine.detect_zones(candles)
    except Exception as e:
        logger.error(f"Error calculating supply/demand: {e}")
        return {"demand": [], "supply": [], "summary": {}, "error": str(e)}


@app.get("/api/smc")
async def get_smc_endpoint(pair: str = "BTC/USDT", interval: str = "15m", limit: int = 250):
    """Smart Money Concepts (BOS, CHoCH, OB, FVG, Liquidity) endpoint."""
    try:
        candles = await get_klines(pair, interval, limit)
        return smc_engine.analyze(candles)
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
        current_price = candles[-1]["close"] if candles else 0.0

        sd = supply_demand_engine.detect_zones(candles, timeframe=interval)
        smc = smc_engine.analyze(candles)
        ind = compute_indicators(candles)
        of_data = await order_flow_engine.compute_advanced_order_flow(pair, candles)
        liq_data = await liquidity_engine.get_liquidity_summary(pair, candles)

        signal = signal_engine.evaluate(
            pair=pair,
            timeframe=interval,
            current_price=current_price,
            sd_data=sd,
            smc_data=smc,
            ind_data=ind,
            orderflow_data=of_data,
            liquidity_data=liq_data,
        )
        return signal
    except Exception as e:
        logger.error(f"Error generating signal: {e}")
        return {
            "state": "WAITING",
            "direction": "NEUTRAL",
            "setup_score": 50,
            "primary_model": "MODEL_B_CONFIRMATION",
            "entry_price": 0,
            "stop_loss": 0,
            "tp1": 0,
            "tp2": 0,
            "error": str(e),
        }


@app.get("/api/mtf")
async def get_mtf_endpoint(pair: str = "BTC/USDT"):
    """Multi-Timeframe alignment matrix."""
    try:
        return await mtf_engine.analyze_matrix(pair)
    except Exception as e:
        logger.error(f"Error generating MTF matrix: {e}")
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
        lim = req.limit or req.candle_limit or 250
        cap = req.starting_capital or req.initial_capital or 1000.0
        candles = await get_klines(req.pair, tf, limit=min(lim, 1000))
        return backtest_engine.run_backtest(
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
    try:
        with DRAWINGS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            key = f"{pair}_{timeframe}"
            return {"drawings": data.get(key, [])}
    except Exception:
        return {"drawings": []}


@app.post("/api/drawings")
async def save_drawings(req: SaveDrawingsRequest):
    data = {}
    if DRAWINGS_FILE.exists():
        try:
            with DRAWINGS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    key = f"{req.pair}_{req.timeframe}"
    data[key] = req.drawings

    with DRAWINGS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return {"success": True, "count": len(req.drawings)}


# --- Context-Aware AI Chat ---

@app.post("/api/chat")
async def chat_with_ai(req: ChatRequest):
    try:
        candles = await get_klines(req.pair, req.interval, limit=120)
        current_price = candles[-1]["close"] if candles else 0.0

        sd = supply_demand_engine.detect_zones(candles, timeframe=req.interval)
        smc = smc_engine.analyze(candles)
        ind = compute_indicators(candles)
        latest_ind = ind.get("latest", {})
        of_data = await order_flow_engine.compute_advanced_order_flow(req.pair, candles)
        liq_data = await liquidity_engine.get_liquidity_summary(req.pair, candles)
        mtf_data = await mtf_engine.analyze_matrix(req.pair)

        sig = signal_engine.evaluate(
            pair=req.pair,
            timeframe=req.interval,
            current_price=current_price,
            sd_data=sd,
            smc_data=smc,
            ind_data=ind,
            orderflow_data=of_data,
            liquidity_data=liq_data,
        )
        port = paper_trader.get_summary()

        response = await generate_ai_response(
            user_message=req.message,
            pair=req.pair,
            interval=req.interval,
            current_price=current_price,
            indicators=latest_ind,
            sd_data=sd,
            smc_data=smc,
            orderflow_data=of_data,
            liquidity_data=liq_data,
            mtf_data=mtf_data,
            signal_data=sig,
            portfolio_data=port,
        )
        return response
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Paper Trading Endpoints ---

@app.get("/api/paper/summary")
async def get_paper_summary():
    try:
        tickers = await get_all_tickers()
        prices = {t["pair"]: t["last_price"] for t in tickers}
        paper_trader.update_prices(prices)
    except Exception:
        pass
    return paper_trader.get_summary()


@app.post("/api/paper/trade")
async def execute_paper_trade(req: TradeRequest):
    # Risk manager validation
    valid, reason = risk_manager.validate_trade(
        trade_risk_reward=2.0,
        current_open_positions_count=len(paper_trader.positions),
        requested_amount_usd=req.amount_usdt,
    )
    if not valid:
        raise HTTPException(status_code=400, detail=f"Risk check failed: {reason}")

    res = paper_trader.open_position(
        pair=req.pair,
        side=req.side,
        amount_usdt=req.amount_usdt,
        current_price=req.price,
        sl=req.stop_loss,
        tp=req.take_profit,
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
    ticker = await get_ticker_24h("BTC/USDT")
    price = ticker.get("last_price", 0.0) if ticker else 0.0
    res = paper_trader.close_position(pos_id, price)
    return res



# --- WebSocket Live Stream ---

@app.websocket("/ws/live")
async def websocket_live_stream(websocket: WebSocket):
    await websocket.accept()
    active_pair = "BTC/USDT"
    active_interval = "15m"
    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                data = json.loads(msg)
                if "pair" in data:
                    active_pair = data["pair"]
                if "interval" in data:
                    active_interval = data["interval"]
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

            try:
                ticker = await get_ticker_24h(active_pair)
                candles = await get_klines(active_pair, active_interval, limit=2)
                latest_candle = candles[-1] if candles else None

                await websocket.send_json({
                    "type": "tick",
                    "pair": active_pair,
                    "interval": active_interval,
                    "ticker": ticker,
                    "candle": latest_candle,
                })
            except Exception as e:
                logger.debug(f"WS push warning: {e}")

            await asyncio.sleep(2.0)

    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket")
    except Exception as e:
        logger.debug(f"WebSocket closed: {e}")


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
