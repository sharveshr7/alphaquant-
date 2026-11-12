"""Verification script to test all backend endpoints and services."""

import asyncio
import importlib
import sys
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

# Add project root
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dashboard.backend.ai_assistant import generate_ai_response
from dashboard.backend.market_data import get_klines, get_ticker_24h
from dashboard.backend.market_scanner import MarketScanner
from dashboard.backend.paper_trader import PaperTrader, paper_trader
from dashboard.backend.signal_tracker import SignalTracker, TrackedSignal
from dashboard.backend.strategy_engine import evaluate_confluence
from dashboard.backend.technical_analysis import compute_indicators


class TestMarketDataDiscovery(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_keeps_only_active_usdt_perpetual_contracts(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        original_map = dict(market_data.PAIR_MAP)
        original_pairs = list(market_data.AVAILABLE_PAIRS)
        original_rules = dict(market_data.PAIR_RULES)
        original_info_stale = market_data._exchange_info_stale
        exchange_info = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
                {
                    "symbol": "ETHUSDT_260925",
                    "baseAsset": "ETH",
                    "quoteAsset": "USDT",
                    "contractType": "CURRENT_QUARTER",
                    "status": "TRADING",
                    "filters": [],
                },
                {
                    "symbol": "OLDUSDT",
                    "baseAsset": "OLD",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "SETTLING",
                    "filters": [],
                },
                {
                    "symbol": "BTCUSDC",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDC",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "filters": [],
                },
            ]
        }
        try:
            with (
                patch.object(
                    market_data, "get_exchange_info", new=AsyncMock(return_value=exchange_info)
                ),
                patch.object(market_data, "_exchange_info_stale", False),
            ):
                pairs = await market_data.refresh_available_pairs()
            self.assertEqual(pairs, ["BTC/USDT"])
            self.assertEqual(market_data.PAIR_RULES["BTCUSDT"]["tick_size"], 0.1)
            self.assertEqual(market_data.PAIR_RULES["BTCUSDT"]["step_size"], 0.001)
            self.assertEqual(market_data.PAIR_RULES["BTCUSDT"]["min_quantity"], 0.001)
            self.assertEqual(market_data.PAIR_RULES["BTCUSDT"]["min_notional"], 5.0)
        finally:
            market_data.PAIR_MAP.clear()
            market_data.PAIR_MAP.update(original_map)
            market_data.AVAILABLE_PAIRS.clear()
            market_data.AVAILABLE_PAIRS.extend(original_pairs)
            market_data.PAIR_RULES.clear()
            market_data.PAIR_RULES.update(original_rules)
            market_data._exchange_info_stale = original_info_stale

    def test_all_requested_futures_intervals_are_supported(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        required = {
            "1m", "5m", "15m", "30m", "1h", "2h",
            "4h", "6h", "8h", "12h", "1d", "1w",
        }
        self.assertTrue(required.issubset(set(market_data.TIMEFRAMES)))

    def test_connected_stream_without_frames_is_marked_delayed(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        with (
            patch.object(market_data, "_market_stream_status", "CONNECTING"),
            patch.object(market_data, "_market_stream_connection_count", 1),
            patch.object(market_data, "_market_stream_connected_at", time.time() - 20),
            patch.object(market_data, "_market_stream_last_message_ts", 0.0),
        ):
            health = market_data.get_market_stream_health()

        self.assertEqual(health["status"], "DELAYED")
        self.assertGreaterEqual(health["stale_seconds"], 20)

    def test_only_valid_market_frames_mark_stream_live(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        patch_values = {
            "_market_stream_status": "CONNECTING",
            "_market_stream_last_message_ts": 0.0,
            "_market_stream_messages": 0,
            "_market_stream_tickers": {},
            "_market_stream_marks": {},
            "_market_stream_candles": {},
            "_market_stream_symbol_updates": {},
            "_market_stream_seen_events": {},
            "_market_stream_recent_messages": deque(),
        }
        with patch.multiple(market_data, **patch_values):
            market_data._apply_market_stream_payload({"result": None, "id": 1})
            self.assertEqual(market_data._market_stream_status, "CONNECTING")
            self.assertEqual(market_data._market_stream_messages, 0)

            market_data._apply_market_stream_payload({
                "stream": "!ticker@arr",
                "data": [{
                    "e": "24hrTicker",
                    "s": "BTCUSDT",
                    "E": int(time.time() * 1000),
                    "c": "70000.0",
                    "p": "100.0",
                    "P": "0.14",
                    "h": "71000.0",
                    "l": "69000.0",
                    "v": "1000.0",
                    "q": "70000000.0",
                    "o": "69900.0",
                }],
            })
            self.assertEqual(market_data._market_stream_status, "LIVE")
            self.assertEqual(market_data._market_stream_messages, 1)
            self.assertEqual(market_data._market_stream_tickers["BTCUSDT"]["last_price"], 70000.0)

            market_data._market_stream_status = "CONNECTING"
            market_data._market_stream_last_message_ts = 0.0
            market_data._market_stream_messages = 0
            market_data._apply_market_stream_payload({
                "stream": "!ticker@arr",
                "data": [{
                    "e": "24hrTicker",
                    "s": "ETHUSDT",
                    "E": int(time.time() * 1000),
                    "c": "NaN",
                    "p": "1",
                    "P": "0.1",
                    "h": "11",
                    "l": "9",
                    "v": "1",
                    "q": "10",
                    "o": "10",
                }],
            })
            self.assertEqual(market_data._market_stream_status, "CONNECTING")
            self.assertEqual(market_data._market_stream_messages, 0)

    def test_price_precision_and_rounding_follow_contract_tick_size(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        original_rules = dict(market_data.PAIR_RULES)
        try:
            market_data.PAIR_RULES.clear()
            market_data.PAIR_RULES["BTCUSDT"] = {"tick_size": 0.05}
            self.assertEqual(market_data.get_price_precision("BTC/USDT"), 2)
            self.assertEqual(market_data.round_price_to_tick("BTC/USDT", 100.126), 100.15)
            with self.assertRaises(ValueError):
                market_data.round_price_to_tick("UNKNOWN/USDT", 1.0)
        finally:
            market_data.PAIR_RULES.clear()
            market_data.PAIR_RULES.update(original_rules)

    async def test_market_stream_receive_stops_without_waiting_for_frame_timeout(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        stop = asyncio.Event()

        class SilentWebSocket:
            async def recv(self) -> str:
                await asyncio.Future()

        receive = asyncio.create_task(
            market_data._recv_market_stream_message(SilentWebSocket(), stop)
        )
        await asyncio.sleep(0)
        stop.set()

        self.assertIsNone(await asyncio.wait_for(receive, timeout=0.1))


class TestKlineCaching(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_limits_share_a_single_candle_request(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        raw_klines = [
            [index * 60_000, "10", "12", "9", "11", "100"]
            for index in range(1, 251)
        ]
        request = AsyncMock(return_value=raw_klines)
        with (
            patch.object(market_data, "_klines_cache", {}),
            patch.object(market_data, "_klines_inflight", {}),
            patch.object(market_data, "_klines_diagnostics", {
                "cache_hits": 0,
                "closed_cache_hits": 0,
                "superset_cache_hits": 0,
                "coalesced_requests": 0,
                "rest_requests": 0,
            }),
            patch.object(market_data, "_binance_get", new=request),
            patch.object(market_data, "_market_stream_symbols", set()),
        ):
            larger, smaller = await asyncio.gather(
                market_data.get_klines("BTC/USDT", "1m", limit=120),
                market_data.get_klines("BTC/USDT", "1m", limit=80),
            )

        request.assert_awaited_once()
        self.assertEqual(request.await_args.kwargs["params"]["limit"], 120)
        self.assertEqual(len(larger), 120)
        self.assertEqual(len(smaller), 80)

    async def test_small_candle_requests_keep_their_requested_limit(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        request = AsyncMock(return_value=[
            [60_000, "10", "12", "9", "11", "100"],
            [120_000, "11", "13", "10", "12", "110"],
        ])
        with (
            patch.object(market_data, "_klines_cache", {}),
            patch.object(market_data, "_klines_inflight", {}),
            patch.object(market_data, "_klines_diagnostics", {
                "cache_hits": 0,
                "closed_cache_hits": 0,
                "superset_cache_hits": 0,
                "coalesced_requests": 0,
                "rest_requests": 0,
            }),
            patch.object(market_data, "_binance_get", new=request),
            patch.object(market_data, "_market_stream_symbols", set()),
        ):
            result = await market_data.get_klines("BTC/USDT", "1m", limit=2)

        self.assertEqual(request.await_args.kwargs["params"]["limit"], 2)
        self.assertEqual(len(result), 2)

    async def test_closed_cache_reuses_only_a_current_closed_candle_snapshot(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        key = "BTCUSDT_1m_2_None_None"
        candles = [
            {"time": 1080, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"time": 1140, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"time": 1200, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        ]
        request = AsyncMock()
        with (
            patch.object(market_data, "_klines_cache", {key: (1202.0, candles)}),
            patch.object(market_data, "_klines_inflight", {}),
            patch.object(market_data, "_klines_diagnostics", {
                "cache_hits": 0,
                "closed_cache_hits": 0,
                "superset_cache_hits": 0,
                "coalesced_requests": 0,
                "rest_requests": 0,
            }),
            patch.object(market_data, "_binance_get", new=request),
            patch.object(market_data, "time") as clock,
        ):
            clock.time.return_value = 1205.0
            result = await market_data.get_klines(
                "BTC/USDT", "1m", limit=2, use_closed_candle_cache=True
            )

        request.assert_not_awaited()
        self.assertEqual([candle["time"] for candle in result], [1140, 1200])


class TestSignalExpiry(unittest.TestCase):
    @staticmethod
    def _new_tracker():
        tracker = SignalTracker.__new__(SignalTracker)
        tracker.active_signals = {}
        tracker.history = []
        tracker._lifecycle_states = {}
        tracker._lifecycle_listeners = set()
        tracker._save_history = lambda: None
        return tracker

    @staticmethod
    def _valid_signal(direction: str = "LONG", **overrides):
        now = int(time.time())
        signal = {
            "pair": "BTC/USDT",
            "timeframe": "1h",
            "direction": direction,
            "entry_price": 100.0,
            "entry_range": [99.0, 100.2] if direction == "LONG" else [99.8, 101.0],
            "current_price_at_signal": 100.0,
            "current_price_timestamp": now,
            "market_data_fresh": True,
            "mtf_is_fresh": True,
            "stop_loss": 90.0 if direction == "LONG" else 110.0,
            "tp1": 115.0 if direction == "LONG" else 85.0,
            "tp2": 125.0 if direction == "LONG" else 75.0,
            "tp3": 140.0 if direction == "LONG" else 60.0,
            "price_precision": 2,
        }
        signal.update(overrides)
        return signal

    def test_signal_uses_its_expiration_time(self) -> None:
        now = int(time.time())
        tracker = SignalTracker.__new__(SignalTracker)
        tracker.active_signals = {
            "BTC/USDT_15m_LONG": TrackedSignal(
                signal_id="signal-one",
                symbol="BTC/USDT",
                timeframe="15m",
                direction="LONG",
                state="CONFIRMED",
                entry_price=100.0,
                stop_loss=95.0,
                tp1=107.5,
                tp2=112.5,
                tp3=120.0,
                invalidation_level=95.0,
                setup_score=80,
                created_at=now - 120,
                expires_at=now - 1,
            )
        }
        tracker.history = []
        tracker._lifecycle_states = {}
        tracker._save_history = lambda: None
        tracker.record_lifecycle_state(
            "BTC/USDT", "15m", "CONFIRMED LONG", "LONG", "Confirmed setup."
        )

        self.assertEqual(tracker.expire_stale_signals(), 1)
        self.assertEqual(tracker.get_active_signals(), [])
        self.assertEqual(tracker.get_history()[0]["close_reason"], "EXPIRED")
        self.assertEqual(
            tracker._lifecycle_states[("BTC/USDT", "15m")]["state"], "EXPIRED"
        )

    def test_lifecycle_transitions_are_versioned_without_polluting_trade_history(self) -> None:
        tracker = SignalTracker.__new__(SignalTracker)
        tracker.active_signals = {}
        tracker.history = []
        tracker._lifecycle_states = {}
        tracker._save_history = lambda: None

        potential = tracker.record_lifecycle_state(
            "BTC/USDT", "1h", "POTENTIAL", "BULLISH", "Fast screen passed."
        )
        duplicate = tracker.record_lifecycle_state(
            "BTC/USDT", "1h", "POTENTIAL", "BULLISH", "Fast screen passed."
        )
        updated = tracker.record_lifecycle_state(
            "BTC/USDT", "1h", "POTENTIAL", "BULLISH", "New candle evidence."
        )
        waiting = tracker.record_lifecycle_state(
            "BTC/USDT", "1h", "WAITING FOR CONFIRMATION", "LONG", "Need confirmation."
        )

        self.assertEqual(potential["version"], 1)
        self.assertEqual(potential["transition"], "NEW_SIGNAL")
        self.assertIs(duplicate, potential)
        self.assertEqual(updated["transition"], "UPDATED")
        self.assertEqual(waiting["transition"], "UPDATED")
        self.assertEqual(waiting["version"], 3)
        self.assertEqual(waiting["reason"], "Need confirmation.")
        self.assertEqual(len(tracker.get_active_setups()), 1)
        self.assertEqual(tracker.get_history(), [])
        self.assertEqual(len([item for item in tracker.history if item["event_type"] == "LIFECYCLE"]), 3)
        tracker.record_lifecycle_state(
            "BTC/USDT", "1h", "INVALIDATED", "LONG", "Setup invalidated."
        )
        self.assertEqual(tracker.get_active_setups(), [])

    def test_inactive_potential_setups_expire(self) -> None:
        tracker = SignalTracker.__new__(SignalTracker)
        tracker.active_signals = {}
        tracker.history = []
        tracker._lifecycle_states = {}
        tracker._save_history = lambda: None
        setup = tracker.record_lifecycle_state(
            "BTC/USDT", "1h", "POTENTIAL", "BULLISH", "Screen passed."
        )
        setup["updated_at"] = int(time.time()) - 25 * 3600

        self.assertEqual(tracker.expire_stale_setups(), 1)
        self.assertEqual(tracker.get_active_setups(), [])
        self.assertEqual(tracker._lifecycle_states[("BTC/USDT", "1h")]["state"], "EXPIRED")

    def test_confirmed_opposite_signal_invalidates_prior_direction(self) -> None:
        tracker = self._new_tracker()
        common = {
            "pair": "BTC/USDT",
            "timeframe": "1h",
        }

        tracker.record_signal({**self._valid_signal("LONG"), **common})
        tracker.record_signal({
            **self._valid_signal("SHORT"),
            **common,
            "stop_loss": 110.0,
            "tp1": 85.0,
            "tp2": 75.0,
            "tp3": 60.0,
            "entry_range": [99.8, 101.0],
        })

        active = tracker.get_active_signals()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["direction"], "SHORT")
        self.assertEqual(tracker.get_history()[0]["close_reason"], "INVALIDATED")

    def test_lifecycle_listener_receives_new_events_only_once(self) -> None:
        tracker = self._new_tracker()
        received = []
        tracker.add_lifecycle_listener(received.append)

        tracker.record_lifecycle_state(
            "ETH/USDT", "1h", "POTENTIAL", "BULLISH", "Initial setup."
        )
        tracker.record_lifecycle_state(
            "ETH/USDT", "1h", "POTENTIAL", "BULLISH", "Initial setup."
        )
        tracker.record_lifecycle_state(
            "ETH/USDT", "1h", "CONFIRMED LONG", "LONG", "Confirmation received."
        )
        tracker.record_lifecycle_state(
            "ETH/USDT", "1h", "CONFIRMED LONG", "LONG", "Higher-timeframe evidence added."
        )
        tracker.record_lifecycle_state(
            "ETH/USDT", "1h", "NO TRADE", "NEUTRAL", "Risk checks rejected the setup."
        )

        self.assertEqual([event["version"] for event in received], [1, 2, 3, 4])
        self.assertEqual(received[0]["transition"], "NEW_SIGNAL")
        self.assertEqual(received[1]["transition"], "CONFIRMED")
        self.assertEqual(received[2]["transition"], "UPDATED")
        self.assertEqual(received[3]["transition"], "NO_TRADE")

    def test_recorded_confirmed_signal_publishes_confirmed_transition(self) -> None:
        tracker = SignalTracker.__new__(SignalTracker)
        tracker.active_signals = {}
        tracker.history = []
        tracker._lifecycle_states = {}
        tracker._lifecycle_listeners = set()
        tracker._save_history = lambda: None
        received = []
        tracker.add_lifecycle_listener(received.append)

        tracker.record_signal(self._valid_signal())

        self.assertEqual(received[0]["transition"], "CONFIRMED")

    def test_signal_recording_rejects_stale_market_or_mtf_data_and_bad_plans(self) -> None:
        tracker = self._new_tracker()
        rejected = (
            {"market_data_fresh": False},
            {"mtf_is_fresh": False},
            {"current_price_timestamp": int(time.time()) - 16},
            {"tp2": 105.0},
            {"entry_range": [101.0, 102.0]},
        )
        for overrides in rejected:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    tracker.record_signal(self._valid_signal(**overrides))
        self.assertEqual(tracker.get_active_signals(), [])

    def test_waiting_signal_confirms_only_after_price_enters_entry_range(self) -> None:
        tracker = self._new_tracker()
        signal = self._valid_signal(current_price_at_signal=101.0)
        tracker.record_signal(signal)
        active = tracker.get_active_signals()
        self.assertEqual(active[0]["state"], "WAITING_ENTRY_FILL")
        signal_id = active[0]["signal_id"]

        self.assertIsNone(tracker.update_signal(signal_id, 100.1, int(time.time())))
        self.assertEqual(tracker.get_active_signals()[0]["state"], "CONFIRMED")

    def test_saving_lifecycle_events_does_not_evict_trade_history(self) -> None:
        tracker = SignalTracker.__new__(SignalTracker)
        tracker.history = [
            {"signal_id": "trade-one"},
            {"event_type": "LIFECYCLE", "version": 1},
            {"signal_id": "trade-two"},
            {"event_type": "LIFECYCLE", "version": 2},
        ]
        with (
            patch("dashboard.backend.signal_tracker.MAX_HISTORY", 3),
            patch("dashboard.backend.signal_tracker.atomic_write_json") as save,
        ):
            tracker._save_history()

        persisted = save.call_args.args[1]
        self.assertEqual(
            [item["signal_id"] for item in persisted if "signal_id" in item],
            ["trade-one", "trade-two"],
        )
        self.assertEqual(
            [item["version"] for item in persisted if item.get("event_type") == "LIFECYCLE"],
            [2],
        )


class TestValidatedPaperTrades(unittest.TestCase):
    def test_paper_position_cannot_open_without_complete_stop_and_targets(self) -> None:
        trader = PaperTrader.__new__(PaperTrader)
        trader.balance = 1000.0
        trader.positions = []
        trader.trade_history = []
        result = trader.open_position(
            pair="BTC/USDT",
            side="BUY",
            amount_usdt=100.0,
            current_price=100.0,
        )

        self.assertIn("error", result)
        self.assertEqual(trader.balance, 1000.0)
        self.assertEqual(trader.positions, [])


class TestMarketScannerFreshness(unittest.IsolatedAsyncioTestCase):
    async def test_screened_scanner_opportunity_is_not_a_confirmed_signal(self) -> None:
        scanner = MarketScanner()
        scanner_data = {
            "pairs": [{
                "symbol": "BTCUSDT",
                "pair_name": "BTC/USDT",
                "signal": "LONG",
                "change_24h_pct": 1.0,
                "rsi": 55.0,
                "structure": "BOS",
                "dominant_structure": "BULLISH",
                "trend": "Bullish",
                "sd": "Demand",
                "volatility": 2.0,
                "relative_volume": 1.5,
                "scan_timeframe": "1h",
                "composite_score": 80,
                "last_price": 70000.0,
                "volume_24h_quote": 50_000_000.0,
            }],
        }
        with patch.object(
            scanner, "get_scanner_data", new=AsyncMock(return_value=scanner_data)
        ):
            result = await scanner.get_categorized_opportunities()

        self.assertEqual(len(result["best_long"]), 1)
        self.assertEqual(result["best_long"][0]["confirmation_status"], "POTENTIAL")
        self.assertEqual(result["best_long"][0]["entry_status"], "WAITING_CONFIRMATION")

    async def test_cached_scan_is_not_returned_as_fresh_when_market_is_disconnected(self) -> None:
        scanner = MarketScanner()
        scanner._scanner_data_cache = (
            time.time(),
            {"count": 1, "pairs": [{"signal": "LONG"}], "stale": False},
        )
        with patch("dashboard.backend.market_scanner.get_connection_status", return_value={
            "status": "DISCONNECTED",
            "exchange_info_stale": False,
        }):
            result = await scanner.get_scanner_data()

        self.assertTrue(result["stale"])

    async def test_cached_klines_are_marked_stale_after_fetch_failure(self) -> None:
        market_data = importlib.import_module("dashboard.backend.market_data")
        key = "BTCUSDT_15m_120_None_None"
        original_cache = market_data._klines_cache.get(key)
        original_telemetry = dict(market_data._conn_telemetry)
        market_data._klines_cache[key] = (
            time.time() - 10,
            [{"time": 1, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 5.0}],
        )
        try:
            with patch.object(
                market_data, "_binance_get", new=AsyncMock(side_effect=RuntimeError("offline"))
            ):
                candles = await get_klines("BTC/USDT", "15m", limit=100)

            self.assertTrue(candles[0]["stale"])
            self.assertGreaterEqual(candles[0]["stale_seconds"], 10)
        finally:
            if original_cache is None:
                market_data._klines_cache.pop(key, None)
            else:
                market_data._klines_cache[key] = original_cache
            market_data._conn_telemetry.clear()
            market_data._conn_telemetry.update(original_telemetry)

    async def test_concurrent_scan_requests_share_one_scan(self) -> None:
        scanner = MarketScanner()
        expected = {"count": 0, "pairs": []}

        async def scan_once() -> dict:
            await asyncio.sleep(0.01)
            return expected

        with patch.object(scanner, "_compute_scanner_data", new=AsyncMock(
            side_effect=scan_once
        )) as compute:
            results = await asyncio.gather(
                scanner.get_scanner_data(),
                scanner.get_scanner_data(),
            )

        self.assertEqual(results, [expected, expected])
        compute.assert_awaited_once()

    async def test_universe_excludes_stale_tickers_and_scans_all_candidates(self) -> None:
        scanner = MarketScanner()
        symbols = [
            {
                "symbol": "BTCUSDT",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
            },
            {
                "symbol": "ETHUSDT",
                "baseAsset": "ETH",
                "quoteAsset": "USDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
            },
        ]
        tickers = [
            {
                "symbol": "BTCUSDT",
                "last_price": 70000.0,
                "price_change_percent": 1.0,
                "high_24h": 71000.0,
                "low_24h": 69000.0,
                "volume_24h": 1000.0,
                "quote_volume_24h": 70_000_000.0,
                "open_price": 69300.0,
            },
            {
                "symbol": "ETHUSDT",
                "last_price": 3000.0,
                "quote_volume_24h": 50_000_000.0,
                "stale": True,
            },
        ]
        with (
            patch("dashboard.backend.market_scanner.get_exchange_info", new=AsyncMock(
                return_value={"symbols": symbols}
            )),
            patch("dashboard.backend.market_scanner.get_all_futures_tickers", new=AsyncMock(
                return_value=tickers
            )),
            patch("dashboard.backend.market_scanner.get_connection_status", return_value={
                "status": "LIVE",
                "exchange_info_stale": False,
            }),
        ):
            result = await scanner.discover_universe()

        self.assertEqual([pair["symbol"] for pair in result["pairs"]], ["BTCUSDT"])
        self.assertEqual(result["eligible_contract_count"], 2)
        self.assertEqual(scanner.get_config()["max_candidates"], 0)

    async def test_signal_endpoint_blocks_stale_candles(self) -> None:
        app_module = importlib.import_module("dashboard.backend.app")
        candles = [
            {"time": int(time.time()) - 60, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1.0, "stale": True}
            for _ in range(40)
        ]
        ticker = {"last_price": 100.0, "timestamp": int(time.time()), "stale": False}
        with (
            patch.object(app_module, "get_klines", new=AsyncMock(return_value=candles)),
            patch.object(app_module, "get_ticker_24h", new=AsyncMock(return_value=ticker)),
        ):
            result = await app_module.get_signal_endpoint("BTC/USDT", "15m")

        self.assertEqual(result["state"], "NO_TRADE")
        self.assertEqual(result["data_availability"], "STALE_OR_MISSING")
        self.assertIn("Latest closed candle data is missing or stale", result["missing_data"])


class TestPaperPositionLimit(unittest.IsolatedAsyncioTestCase):
    async def test_summary_and_rejection_include_open_position_details(self) -> None:
        app_module = importlib.import_module("dashboard.backend.app")
        trader = PaperTrader.__new__(PaperTrader)
        trader.balance = 700.0
        trader.positions = [
            {
                "id": "pos_one",
                "pair": "BTC/USDT",
                "side": "BUY",
                "entry_price": 70000.0,
                "current_price": 70100.0,
                "amount_usdt": 100.0,
                "quantity": 0.0014,
                "pnl": 0.14,
                "pnl_pct": 0.14,
            },
            {
                "id": "pos_two",
                "pair": "ETH/USDT",
                "side": "SELL",
                "entry_price": 3000.0,
                "current_price": 2990.0,
                "amount_usdt": 200.0,
                "quantity": 0.0667,
                "pnl": 0.67,
                "pnl_pct": 0.335,
            },
        ]
        trader.trade_history = [{"realized_pnl": 12.5}]
        active_signal = {
            "signal_id": "signal_one",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "direction": "LONG",
            "state": "CONFIRMED",
            "filled": True,
            "market_data_fresh": True,
            "mtf_is_fresh": True,
            "entry_range": [99.0, 101.0],
            "stop_loss": 90.0,
            "tp1": 115.0,
            "tp2": 125.0,
            "tp3": 140.0,
            "created_at": int(time.time()),
        }
        with (
            patch.object(app_module, "paper_trader", trader),
            patch.object(app_module, "get_all_tickers", new=AsyncMock(return_value=[])),
            patch.object(app_module, "get_ticker_24h", new=AsyncMock(return_value={
                "pair": "BTC/USDT",
                "last_price": 100.0,
                "timestamp": int(time.time()),
                "stale": False,
            })),
            patch.object(
                app_module.signal_tracker,
                "get_active_signals",
                return_value=[active_signal],
            ),
            patch.object(app_module.risk_manager.settings, "max_open_positions", 2),
            patch.object(
                app_module.risk_manager,
                "validate_trade",
                return_value=(False, "Maximum open positions limit reached (2)"),
            ),
        ):
            summary = await app_module.get_paper_summary()
            self.assertEqual(summary["current_open_positions"], 2)
            self.assertEqual(summary["max_open_positions"], 2)
            self.assertEqual(summary["available_position_slots"], 0)
            self.assertEqual(
                [pos["id"] for pos in summary["closeable_positions"]],
                ["pos_one", "pos_two"],
            )
            self.assertEqual(summary["available_margin"], 700.0)
            self.assertEqual(summary["equity"], 1000.81)
            self.assertEqual(summary["realized_pnl"], 12.5)
            self.assertEqual(summary["open_positions"][0]["mark_price"], 70100.0)
            self.assertEqual(summary["open_positions"][0]["unrealized_pnl"], 0.14)

            with self.assertRaises(HTTPException) as exc_info:
                await app_module.execute_paper_trade(app_module.TradeRequest(price=100.0))
            detail = exc_info.exception.detail
            self.assertIn("Current open positions: 2", detail)
            self.assertIn("maximum allowed positions: 2", detail)
            self.assertIn("available position slots: 0", detail)
            self.assertIn("BTC/USDT BUY (pos_one)", detail)
            self.assertIn("ETH/USDT SELL (pos_two)", detail)


async def test_all() -> None:
    print("1. Testing 24h Ticker...")
    ticker = await get_ticker_24h("BTC/USDT")
    price = ticker["last_price"]
    change = ticker["price_change_percent"]
    print(f"   -> BTC Price: ${price:,} (24h Change: {change}%)")

    print("\n2. Testing 15m Klines...")
    candles = await get_klines("BTC/USDT", "15m", limit=100)
    print(f"   -> Fetched {len(candles)} candles. Latest: O={candles[-1]['open']} C={candles[-1]['close']}")

    print("\n3. Testing Technical Indicators & Supply/Demand Zones...")
    ind = compute_indicators(candles)
    latest = ind["latest"]
    print(f"   -> RSI: {latest['rsi']}, EMA50: {latest['ema_50']:.2f}, EMA200: {latest['ema_200']:.2f}")
    print(f"   -> Demand Zones: {len(ind['zones']['demand'])}, Supply Zones: {len(ind['zones']['supply'])}")

    print("\n4. Testing Supply/Demand Confluence Strategy...")
    confluence = evaluate_confluence(candles)
    print(f"   -> Confluence Score: {confluence['confluence_score']} / {confluence['max_score']}")
    print(f"   -> Signal: {confluence['signal']} | Action: {confluence['action']}")
    for c in confluence["checklist"]:
        status = "X" if c["passed"] else " "
        print(f"      [{status}] {c['title']}: {c['detail']}")

    print("\n5. Testing AI Chat Assistant...")
    ai_res = await generate_ai_response(
        user_message="What is the current trend and key demand zones for BTC?",
        pair="BTC/USDT",
        interval="15m",
        current_price=candles[-1]["close"],
        indicators=latest,
        confluence=confluence,
    )
    print(f"   -> AI Engine: {ai_res['engine']} ({ai_res['model_used']})")
    print(f"   -> AI Response Preview (first 150 chars):\n{ai_res['response'][:150]}...")

    print("\n6. Testing Paper Trader...")
    summary = paper_trader.get_summary()
    print(f"   -> Initial Paper Balance: ${summary['balance']}")
    order = paper_trader.open_position("BTC/USDT", "BUY", 100.0, candles[-1]["close"])
    pos_id = order["position"]["id"]
    pos_qty = order["position"]["quantity"]
    print(f"   -> Opened simulated position: ID={pos_id} Qty={pos_qty:.6f}")
    close = paper_trader.close_position(pos_id, candles[-1]["close"] * 1.01)
    pnl = close["closed_trade"]["realized_pnl"]
    print(f"   -> Closed simulated position: Realized PnL=${pnl}")

    print("\nALL BACKEND CHECKS PASSED PERFECTLY!")


if __name__ == "__main__":
    asyncio.run(test_all())
