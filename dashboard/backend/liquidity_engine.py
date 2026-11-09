"""Liquidity Intelligence Engine for AlphaQuant AI Pro.

Combines:
1. Real-time Exchange-Observed L2 Order Book Depth (Binance /api/v3/depth)
   - Order-book depth visualization
   - Bid/Ask depth imbalance ratio
   - Liquidity concentration and resting liquidity walls
2. Inferred Structural Liquidity Pools
   - Equal Highs (EQH) and Equal Lows (EQL)
   - Retail stop-loss concentration zones (Stop-hunt candidates)
   - Real-time Liquidity Sweep / Turtle Soup detection (wick breach + close back inside)
Strictly distinguishes exchange-observed data from inferred structural liquidity.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
import httpx
import numpy as np
from .market_data import BINANCE_FUTURES_BASE_URL, normalize_symbol

logger = logging.getLogger("liquidity_engine")

BINANCE_DEPTH_URL = f"{BINANCE_FUTURES_BASE_URL}/depth"


@dataclass
class LiquidityWall:
    side: str  # "BID" or "ASK"
    price: float
    volume: float
    total_value_usd: float
    distance_pct: float
    is_wall: bool  # >= 2.8x median volume


@dataclass
class StructuralLiquidityPool:
    pool_type: str  # "EQH_BUY_STOPS" or "EQL_SELL_STOPS"
    level: float
    tolerance_pct: float
    touch_count: int
    is_swept: bool
    sweep_time: int | None
    sweep_candle_high_or_low: float | None
    estimated_stop_hunt_risk: str  # "HIGH", "MEDIUM", "LOW"


class LiquidityEngine:
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=6.0)

    async def fetch_l2_depth(self, pair: str, limit: int = 100) -> dict:
        """Fetches live L2 order book depth from Binance Futures."""
        symbol = normalize_symbol(pair)
        try:
            res = await self.http_client.get(
                BINANCE_DEPTH_URL,
                params={"symbol": symbol, "limit": limit},
            )
            if res.status_code == 200:
                return res.json()
            logger.warning(f"Binance depth returned {res.status_code} for {pair}")
            return {"bids": [], "asks": []}
        except Exception as e:
            logger.error(f"Error fetching Binance depth for {pair}: {e}")
            return {"bids": [], "asks": []}

    def analyze_orderbook_depth(self, depth_data: dict, current_price: float) -> dict:
        """Analyzes exchange-observed L2 order book depth, walls, and depth imbalance."""
        raw_bids = depth_data.get("bids", [])
        raw_asks = depth_data.get("asks", [])

        if not raw_bids or not raw_asks:
            return {
                "provenance": "EXCHANGE-OBSERVED L2 DEPTH (UNAVAILABLE)",
                "bid_depth_usd": 0.0,
                "ask_depth_usd": 0.0,
                "imbalance_ratio": 0.0,
                "bias": "NEUTRAL",
                "liquidity_walls": [],
                "top_bids": [],
                "top_asks": [],
            }

        # Parse bids & asks
        bids = [{"price": float(p), "qty": float(q), "value": float(p) * float(q)} for p, q in raw_bids]
        asks = [{"price": float(p), "qty": float(q), "value": float(p) * float(q)} for p, q in raw_asks]

        total_bid_usd = sum(b["value"] for b in bids)
        total_ask_usd = sum(a["value"] for a in asks)
        total_depth = total_bid_usd + total_ask_usd

        # Depth Imbalance (-1.0 to +1.0)
        imbalance = (total_bid_usd - total_ask_usd) / total_depth if total_depth > 0 else 0.0

        if imbalance > 0.25:
            bias = "BULLISH_SUPPORT (Heavy Bid Depth)"
        elif imbalance < -0.25:
            bias = "BEARISH_RESISTANCE (Heavy Ask Wall)"
        else:
            bias = "BALANCED_DEPTH"

        # Detect Liquidity Walls (>= 2.8x median volume of the top 50 levels)
        all_values = [b["value"] for b in bids] + [a["value"] for a in asks]
        median_val = float(np.median(all_values)) if all_values else 1.0
        wall_threshold = max(median_val * 2.8, 10000.0)

        walls: list[LiquidityWall] = []

        # Check Bids
        for b in bids[:40]:
            if b["value"] >= wall_threshold:
                dist = round(((current_price - b["price"]) / current_price) * 100, 2)
                walls.append(
                    LiquidityWall(
                        side="BID",
                        price=round(b["price"], 2),
                        volume=round(b["qty"], 4),
                        total_value_usd=round(b["value"], 2),
                        distance_pct=dist,
                        is_wall=True,
                    )
                )

        # Check Asks
        for a in asks[:40]:
            if a["value"] >= wall_threshold:
                dist = round(((a["price"] - current_price) / current_price) * 100, 2)
                walls.append(
                    LiquidityWall(
                        side="ASK",
                        price=round(a["price"], 2),
                        volume=round(a["qty"], 4),
                        total_value_usd=round(a["value"], 2),
                        distance_pct=dist,
                        is_wall=True,
                    )
                )

        # Sort walls by value descending
        walls.sort(key=lambda w: w.total_value_usd, reverse=True)

        return {
            "provenance": "EXCHANGE-OBSERVED L2 DEPTH (Binance Level 2 Snapshot)",
            "bid_depth_usd": round(total_bid_usd, 2),
            "ask_depth_usd": round(total_ask_usd, 2),
            "imbalance_ratio": round(imbalance, 3),
            "bias": bias,
            "liquidity_walls": [asdict(w) for w in walls[:8]],
            "top_bids": bids[:10],
            "top_asks": asks[:10],
        }

    def detect_structural_liquidity_pools(self, candles: list[dict], tolerance_pct: float = 0.15) -> dict:
        """Detects Equal Highs (EQH), Equal Lows (EQL), and Liquidity Sweeps.
        
        EQH creates buy-side liquidity (BSL) pools where retail breakout buyers and short-seller stops cluster.
        EQL creates sell-side liquidity (SSL) pools where retail breakdown sellers and long-buyer stops cluster.
        """
        if not candles or len(candles) < 20:
            return {"pools": [], "sweeps": [], "active_stop_hunt_candidates": []}

        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        times = [int(c["time"]) for c in candles]
        n = len(candles)

        # 1. Identify Swing Highs and Swing Lows (pivot left/right = 2)
        swing_highs = []
        swing_lows = []

        for i in range(2, n - 2):
            if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] >= highs[i + 1] and highs[i] >= highs[i + 2]:
                swing_highs.append({"index": i, "price": highs[i], "time": times[i]})
            if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]:
                swing_lows.append({"index": i, "price": lows[i], "time": times[i]})

        pools: list[StructuralLiquidityPool] = []
        sweeps = []

        # 2. Group swing highs into Equal Highs (EQH)
        for i in range(len(swing_highs)):
            for j in range(i + 1, len(swing_highs)):
                sh1 = swing_highs[i]
                sh2 = swing_highs[j]
                if abs(sh1["price"] - sh2["price"]) / sh1["price"] * 100 <= tolerance_pct:
                    avg_level = (sh1["price"] + sh2["price"]) / 2.0
                    idx2 = sh2["index"]

                    # Check if swept by subsequent candles
                    is_swept = False
                    sweep_time = None
                    sweep_high = None
                    for k in range(idx2 + 1, n):
                        if highs[k] > avg_level:
                            # A true sweep/turtle soup: wick exceeded avg_level, but close is below avg_level
                            if closes[k] <= avg_level * 1.001:
                                is_swept = True
                                sweep_time = times[k]
                                sweep_high = highs[k]
                                sweeps.append({
                                    "type": "BEARISH_LIQUIDITY_SWEEP",
                                    "pool_type": "EQH_BUY_STOPS",
                                    "level": round(avg_level, 2),
                                    "wick_high": round(highs[k], 2),
                                    "time": times[k],
                                    "description": f"Buy-side stop run swept at ${highs[k]:,.2f} with rejection close back below ${avg_level:,.2f}"
                                })
                            break

                    pools.append(
                        StructuralLiquidityPool(
                            pool_type="EQH_BUY_STOPS",
                            level=round(avg_level, 2),
                            tolerance_pct=tolerance_pct,
                            touch_count=2,
                            is_swept=is_swept,
                            sweep_time=sweep_time,
                            sweep_candle_high_or_low=sweep_high,
                            estimated_stop_hunt_risk="HIGH" if not is_swept else "MITIGATED",
                        )
                    )

        # 3. Group swing lows into Equal Lows (EQL)
        for i in range(len(swing_lows)):
            for j in range(i + 1, len(swing_lows)):
                sl1 = swing_lows[i]
                sl2 = swing_lows[j]
                if abs(sl1["price"] - sl2["price"]) / sl1["price"] * 100 <= tolerance_pct:
                    avg_level = (sl1["price"] + sl2["price"]) / 2.0
                    idx2 = sl2["index"]

                    # Check if swept
                    is_swept = False
                    sweep_time = None
                    sweep_low = None
                    for k in range(idx2 + 1, n):
                        if lows[k] < avg_level:
                            if closes[k] >= avg_level * 0.999:
                                is_swept = True
                                sweep_time = times[k]
                                sweep_low = lows[k]
                                sweeps.append({
                                    "type": "BULLISH_LIQUIDITY_SWEEP",
                                    "pool_type": "EQL_SELL_STOPS",
                                    "level": round(avg_level, 2),
                                    "wick_low": round(lows[k], 2),
                                    "time": times[k],
                                    "description": f"Sell-side stop run swept at ${lows[k]:,.2f} with rejection close back above ${avg_level:,.2f}"
                                })
                            break

                    pools.append(
                        StructuralLiquidityPool(
                            pool_type="EQL_SELL_STOPS",
                            level=round(avg_level, 2),
                            tolerance_pct=tolerance_pct,
                            touch_count=2,
                            is_swept=is_swept,
                            sweep_time=sweep_time,
                            sweep_candle_high_or_low=sweep_low,
                            estimated_stop_hunt_risk="HIGH" if not is_swept else "MITIGATED",
                        )
                    )

        # Filter active stop-hunt candidates (unswept pools closest to current price)
        current_price = closes[-1]
        active_pools = [p for p in pools if not p.is_swept]
        active_pools.sort(key=lambda p: abs(p.level - current_price))

        return {
            "provenance": "INFERRED STRUCTURAL LIQUIDITY POOLS (EQH/EQL Algorithmic Scan)",
            "pools": [asdict(p) for p in pools[-12:]],
            "active_stop_hunt_candidates": [asdict(p) for p in active_pools[:6]],
            "sweeps": sweeps[-8:],
        }

    async def get_liquidity_summary(self, pair: str, candles: list[dict]) -> dict:
        """Full unified liquidity assessment combining L2 book and structural pools."""
        current_price = candles[-1]["close"] if candles else 0.0
        depth_raw = await self.fetch_l2_depth(pair, limit=100)
        l2_analysis = self.analyze_orderbook_depth(depth_raw, current_price)
        structural_pools = self.detect_structural_liquidity_pools(candles)

        # Summary flags
        has_bullish_sweep = any(s["type"] == "BULLISH_LIQUIDITY_SWEEP" for s in structural_pools["sweeps"][-3:])
        has_bearish_sweep = any(s["type"] == "BEARISH_LIQUIDITY_SWEEP" for s in structural_pools["sweeps"][-3:])

        nearest_bsl = next((p for p in structural_pools["active_stop_hunt_candidates"] if p["pool_type"] == "EQH_BUY_STOPS"), None)
        nearest_ssl = next((p for p in structural_pools["active_stop_hunt_candidates"] if p["pool_type"] == "EQL_SELL_STOPS"), None)

        return {
            "pair": pair,
            "current_price": current_price,
            "orderbook_l2": l2_analysis,
            "structural_pools": structural_pools,
            "summary": {
                "has_recent_bullish_sweep": bool(has_bullish_sweep),
                "has_recent_bearish_sweep": bool(has_bearish_sweep),
                "depth_imbalance_ratio": l2_analysis["imbalance_ratio"],
                "depth_bias": l2_analysis["bias"],
                "nearest_buy_side_liquidity_level": nearest_bsl["level"] if nearest_bsl else None,
                "nearest_sell_side_liquidity_level": nearest_ssl["level"] if nearest_ssl else None,
            },
        }


liquidity_engine = LiquidityEngine()
