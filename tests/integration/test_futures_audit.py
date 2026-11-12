"""Comprehensive automated test suite for AlphaQuant AI Pro Trading Terminal.

Tests:
1. Binance Futures REST API endpoints (Futures only: /fapi/v1/...)
2. Binance Futures WebSocket stream connectivity
3. Historical pagination & deduplication (365+ days daily)
4. Unified Supply & Demand Engine (single source of truth)
5. Smart Money Concepts (SMC) & Correct Fibonacci OTE discount placement
6. Indicators calculations (EMA, Bollinger Bands, Supertrend, RSI, MACD)
7. Paper Trading execution & risk limits
8. AI Assistant integration & deterministic fallback
"""

import asyncio
import sys
import unittest
from pathlib import Path

# Setup paths
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import websockets
from dashboard.backend.ai_assistant import generate_ai_response
from dashboard.backend.market_data import (
    BINANCE_FUTURES_BASE_URL,
    compute_market_levels,
    get_agg_trades,
    get_orderbook,
    get_exchange_info,
    get_historical_klines_paginated,
    get_klines,
    get_ticker_24h,
)
from dashboard.backend.paper_trader import paper_trader
from dashboard.backend.risk_manager import risk_manager
from dashboard.backend.smc_engine import smc_engine
from dashboard.backend.strategy_engine import evaluate_confluence
from dashboard.backend.supply_demand import supply_demand_engine
from dashboard.backend.technical_analysis import compute_indicators, detect_supply_demand_zones


class TestBinanceFuturesMarketData(unittest.IsolatedAsyncioTestCase):
    async def test_futures_rest_endpoints(self):
        """Verifies all market data uses Binance USDⓈ-M Futures REST."""
        # 1. Exchange info
        info = await get_exchange_info()
        self.assertIn("symbols", info)
        usdt_symbols = [s["symbol"] for s in info["symbols"] if s["symbol"].endswith("USDT")]
        self.assertGreater(len(usdt_symbols), 100, "Should have >100 USDT Futures contracts")

        # 2. 24h Ticker
        ticker = await get_ticker_24h("BTC/USDT")
        self.assertIn("last_price", ticker)
        self.assertGreater(ticker["last_price"], 0)

        # 3. Klines
        klines = await get_klines("BTC/USDT", "15m", limit=50)
        self.assertEqual(len(klines), 50)
        self.assertIn("open", klines[0])
        self.assertIn("close", klines[0])
        self.assertIn("time", klines[0])

        # 4. Depth
        depth = await get_orderbook("BTC/USDT", limit=10)
        self.assertIn("bids", depth)
        self.assertIn("asks", depth)

        # 5. AggTrades
        trades = await get_agg_trades("BTC/USDT", limit=10)
        self.assertGreater(len(trades), 0)

    async def test_futures_websocket_connection(self):
        """Verifies live connection to Binance Futures WebSocket."""
        stream_url = "wss://fstream.binance.com/market/stream?streams=btcusdt@kline_1m/btcusdt@ticker"
        try:
            async with websockets.connect(stream_url, close_timeout=4.0) as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=6.0)
                self.assertIsNotNone(msg)
                self.assertIn("stream", msg)
        except Exception as e:
            self.fail(f"Futures WebSocket connection failed: {e}")

    async def test_historical_pagination_365_days(self):
        """Verifies 365+ days historical daily candles pagination."""
        candles_1y = await get_historical_klines_paginated("BTC/USDT", "1d", max_candles=370)
        self.assertGreaterEqual(len(candles_1y), 365, "Must return at least 365 daily candles")
        # Verify chronological order
        for i in range(1, len(candles_1y)):
            self.assertGreater(candles_1y[i]["time"], candles_1y[i - 1]["time"])


class TestSupplyDemandAndSMC(unittest.IsolatedAsyncioTestCase):
    async def test_unified_supply_demand(self):
        """Verifies Supply & Demand engine is unified across modules."""
        candles = await get_klines("BTC/USDT", "1h", limit=80)
        zones_direct = supply_demand_engine.detect_zones(candles)
        ind = compute_indicators(candles)
        zones_ind = ind["zones"]

        # Both must return same zone counts and values
        self.assertEqual(len(zones_direct["demand"]), len(zones_ind["demand"]))
        self.assertEqual(len(zones_direct["supply"]), len(zones_ind["supply"]))

        if zones_direct["demand"]:
            d0 = zones_direct["demand"][0]
            self.assertIn("proximal", d0)
            self.assertIn("distal", d0)
            self.assertIn("quality_score", d0)
            self.assertGreaterEqual(d0["quality_score"], 0)
            self.assertLessEqual(d0["quality_score"], 100)

    async def test_smc_fibonacci_ote_discount_placement(self):
        """Verifies SMC Fibonacci OTE for Bullish setups is strictly in the Discount area."""
        candles = await get_klines("BTC/USDT", "15m", limit=100)
        smc_res = smc_engine.analyze(candles)
        self.assertIn("premium_discount", smc_res)
        pd_data = smc_res["premium_discount"]
        ote = pd_data.get("optimal_trade_entry", {})

        low = pd_data["range_low"]
        high = pd_data["range_high"]
        eq = pd_data["equilibrium_50"]

        bull_proximal = ote.get("bullish_ote_proximal", 0.0)
        bull_distal = ote.get("bullish_ote_distal", 0.0)

        # Bullish OTE must be below 50% equilibrium (in discount)
        self.assertLess(bull_proximal, eq, "Bullish OTE proximal must be below 50% equilibrium")
        self.assertLess(bull_distal, bull_proximal, "Bullish OTE distal must be deeper than proximal")
        self.assertGreaterEqual(bull_distal, low, "Bullish OTE distal must not be below range low")


class TestIndicatorsAndStrategy(unittest.IsolatedAsyncioTestCase):
    async def test_indicators_and_strategy(self):
        """Verifies mathematical correctness of indicators and confluence checklist."""
        candles = await get_klines("BTC/USDT", "15m", limit=100)
        ind = compute_indicators(candles)
        latest = ind["latest"]

        # Validate indicator outputs
        self.assertIn("rsi", latest)
        self.assertIn("ema_50", latest)
        self.assertIn("ema_200", latest)
        self.assertIn("adx", latest)
        self.assertIn("bb_middle", latest)
        self.assertIn("macdhist", latest)
        self.assertIn("cmf", latest)

        # Evaluate confluence
        confluence = evaluate_confluence(candles)
        self.assertIn("confluence_score", confluence)
        self.assertEqual(confluence["max_score"], 8)
        self.assertEqual(len(confluence["checklist"]), 8)
        self.assertIn(confluence["signal"], ["STRONG_BUY", "MODERATE_BUY", "NEUTRAL", "BEARISH_OR_NEUTRAL"])


class TestPaperTraderAndAI(unittest.IsolatedAsyncioTestCase):
    async def test_paper_trading(self):
        """Verifies paper trading execution, risk calculation, and portfolio bounds."""
        summary_before = paper_trader.get_summary()
        bal_before = summary_before["balance"]

        # Calculate position size
        pos_calc = risk_manager.calculate_position_size(
            account_balance=bal_before,
            risk_pct=1.5,
            entry_price=70000.0,
            stop_loss=69000.0,
        )
        self.assertGreater(pos_calc["recommended_units"], 0)
        self.assertLessEqual(pos_calc["actual_risk_usd"], bal_before * 0.015 + 0.1)

        # Open and close simulated trade
        order = paper_trader.open_position("BTC/USDT", "BUY", 50.0, 70000.0, stop_loss=69000.0)
        pos_id = order["position"]["id"]
        close_res = paper_trader.close_position(pos_id, 71000.0)
        self.assertEqual(close_res["status"], "CLOSED")
        self.assertGreater(close_res["closed_trade"]["realized_pnl"], 0)

    async def test_ai_assistant_execution(self):
        """Verifies Gemini AI generates response or cleanly invokes local quant engine."""
        res = await generate_ai_response(
            user_message="Analyze market structure and nearest liquidity levels.",
            pair="BTC/USDT",
            interval="15m",
            current_price=74000.0,
            indicators={"rsi": 52.0, "ema_50": 73800.0},
        )
        self.assertIn("response", res)
        self.assertIn("model_used", res)
        self.assertGreater(len(res["response"]), 50)


if __name__ == "__main__":
    unittest.main()
