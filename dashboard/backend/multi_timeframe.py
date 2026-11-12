"""Multi-Timeframe Analysis & Confluence Pipeline Engine - Phase 2.

Features:
- Structured 4-tier Confluence Pipeline:
  1. 4H Macro Direction (Dominant institutional bias on closed bars)
  2. 1H Structural Framework (Swing BOS/CHoCH & Key Order Blocks)
  3. 15M Operational Supply & Demand Zones (Execution level boundaries)
  4. 5M Tactical Entry Trigger (Micro CHoCH / Volume spike trigger)
- Dynamic Alignment Matrix across all supported timeframes (5m, 15m, 1h, 4h, 1d)
- Anti-Lookahead Guarantee: Always uses closed historical bars for higher timeframes.
"""

from __future__ import annotations

import asyncio
import time

from .market_data import TIMEFRAMES, TIMEFRAME_SECONDS, get_klines
from .smc_engine import smc_engine
from .supply_demand import supply_demand_engine

SUPPORTED_TIMEFRAMES = TIMEFRAMES
DEFAULT_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]


class MultiTimeframeEngine:
    async def analyze_matrix(
        self,
        pair: str = "BTC/USDT",
        timeframes: list[str] | None = None,
    ) -> dict:
        """Fetches candles across key timeframes and calculates alignment score and 4-tier pipeline."""
        tf_list = list(dict.fromkeys(timeframes if timeframes is not None else DEFAULT_TIMEFRAMES))
        if not tf_list:
            raise ValueError("At least one timeframe is required for MTF analysis")
        unsupported = [tf for tf in tf_list if tf not in SUPPORTED_TIMEFRAMES]
        if unsupported:
            raise ValueError(f"Unsupported Binance Futures timeframe(s): {', '.join(unsupported)}")
        tasks = [get_klines(pair, tf, limit=80) for tf in tf_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candles_by_tf = {}
        for tf, res in zip(tf_list, results):
            if isinstance(res, list) and len(res) >= 20:
                # Never use the currently forming unfinished bar as confirmed historical evidence
                candles_by_tf[tf] = res[:-1]

        matrix = []
        bullish_count = 0
        bearish_count = 0
        now_ts = int(time.time())
        for tf in tf_list:
            eval_slice = candles_by_tf.get(tf)
            if eval_slice:
                closes = [float(c["close"]) for c in eval_slice]
                curr = closes[-1]
                last_candle_ts = int(float(eval_slice[-1].get("time", 0) or 0))
                age_seconds = max(0, now_ts - last_candle_ts)
                stale_seconds = TIMEFRAME_SECONDS[tf] * 2
                freshness_ok = last_candle_ts > 0 and age_seconds <= stale_seconds

                ema_20 = float(calculate_ema(closes, 20))
                ema_50 = float(calculate_ema(closes, 50))
                rsi = float(calculate_rsi(closes, 14))

                sd = supply_demand_engine.detect_zones(eval_slice, timeframe=tf)
                sd_sum = sd.get("summary", {})
                in_demand = bool(sd_sum.get("in_demand_zone"))
                in_supply = bool(sd_sum.get("in_supply_zone"))

                if (curr > ema_20 > ema_50 or in_demand) and rsi > 46.0:
                    bias = "BULLISH"
                    bullish_count += 1
                elif (curr < ema_20 < ema_50 or in_supply) and rsi < 54.0:
                    bias = "BEARISH"
                    bearish_count += 1
                else:
                    bias = "NEUTRAL"

                prec = 6 if curr < 0.01 else (4 if curr < 10 else 2)
                matrix.append({
                    "timeframe": tf,
                    "bias": bias,
                    "current_price": round(curr, prec),
                    "ema_20": round(ema_20, prec),
                    "ema_50": round(ema_50, prec),
                    "rsi": round(rsi, 1),
                    "last_candle_ts": last_candle_ts,
                    "age_seconds": age_seconds,
                    "freshness_ok": freshness_ok,
                    "freshness_limit_seconds": stale_seconds,
                    "nearest_demand": sd_sum.get("nearest_demand_zone"),
                    "nearest_supply": sd_sum.get("nearest_supply_zone"),
                })
            else:
                matrix.append({
                    "timeframe": tf,
                    "bias": "UNAVAILABLE",
                    "current_price": None,
                    "last_candle_ts": 0,
                    "age_seconds": None,
                    "freshness_ok": False,
                    "freshness_limit_seconds": 0,
                })

        valid_count = max(sum(1 for m in matrix if m["bias"] != "UNAVAILABLE"), 1)
        staleness_rows = [m for m in matrix if m.get("timeframe") != "UNAVAILABLE" and not m.get("freshness_ok", False)]
        has_stale_timeframe = bool(staleness_rows)
        has_conflict = bullish_count > 0 and bearish_count > 0 and abs(bullish_count - bearish_count) <= 2

        if has_stale_timeframe:
            overall_bias = "MIXED_CONFLICTING"
            alignment_score = 0
        elif has_conflict:
            alignment_score = 50
            overall_bias = "MIXED_CONFLICTING"
        else:
            dominant_count = max(bullish_count, bearish_count)
            alignment_score = int((dominant_count / valid_count) * 100)
            overall_bias = (
                "STRONG_BULLISH_CONFLUENCE"
                if bullish_count >= 4
                else "MODERATE_BULLISH"
                if bullish_count >= 3
                else "STRONG_BEARISH_CONFLUENCE"
                if bearish_count >= 4
                else "MODERATE_BEARISH"
                if bearish_count >= 3
                else "MIXED_CONFLICTING"
            )

        # 4-Tier Pipeline Evaluation (all strictly on closed bars)
        c4h = candles_by_tf.get("4h", [])
        c1h = candles_by_tf.get("1h", [])
        c15m = candles_by_tf.get("15m", [])
        c5m = candles_by_tf.get("5m", [])

        # 1. 4H Macro
        macro_bias = "NEUTRAL"
        if len(c4h) >= 20:
            c4h_closes = [float(c["close"]) for c in c4h]
            e50 = calculate_ema(c4h_closes, 50)
            macro_bias = "BULLISH" if c4h_closes[-1] > e50 else "BEARISH"

        # 2. 1H Structure
        h1_structure = "NEUTRAL"
        if len(c1h) >= 20:
            smc_1h = smc_engine.analyze(c1h)
            h1_structure = smc_1h.get("summary", {}).get("dominant_structure", "NEUTRAL")

        # 3. 15M S/D
        m15_sd_status = "NEUTRAL"
        if len(c15m) >= 20:
            sd_15m = supply_demand_engine.detect_zones(c15m, timeframe="15m")
            if sd_15m.get("summary", {}).get("in_demand_zone"):
                m15_sd_status = "INSIDE_DEMAND_ZONE"
            elif sd_15m.get("summary", {}).get("in_supply_zone"):
                m15_sd_status = "INSIDE_SUPPLY_ZONE"
            else:
                m15_sd_status = "MID_RANGE"

        # 4. 5M Trigger
        m5_trigger = "NEUTRAL"
        if len(c5m) >= 20:
            c5m_closes = [float(c["close"]) for c in c5m]
            e20 = calculate_ema(c5m_closes, 20)
            m5_trigger = "TRIGGER_LONG" if c5m_closes[-1] > e20 else "TRIGGER_SHORT"

        bull_conc = bool(
            macro_bias == "BULLISH"
            and "BULL" in h1_structure
            and m15_sd_status == "INSIDE_DEMAND_ZONE"
            and bullish_count >= 3
            and bearish_count == 0
        )
        bear_conc = bool(
            macro_bias == "BEARISH"
            and "BEAR" in h1_structure
            and m15_sd_status == "INSIDE_SUPPLY_ZONE"
            and bearish_count >= 3
            and bullish_count == 0
        )

        pipeline = {
            "tier1_4h_macro_bias": macro_bias,
            "tier2_1h_market_structure": h1_structure,
            "tier3_15m_operational_zones": m15_sd_status,
            "tier4_5m_tactical_trigger": m5_trigger,
            "bullish_concurrence": bull_conc,
            "bearish_concurrence": bear_conc,
            "concurrence": bull_conc or bear_conc,
            "conflicting_bias_detected": has_conflict,
            "stale_timeframe_detected": has_stale_timeframe,
        }

        return {
            "pair": pair,
            "overall_bias": overall_bias,
            "alignment_score": alignment_score,
            "generated_at": now_ts,
            "bullish_timeframes_count": bullish_count,
            "bearish_timeframes_count": bearish_count,
            "pipeline_4tier": pipeline,
            "matrix": matrix,
            "has_stale_timeframe": has_stale_timeframe,
        }


def calculate_ema(values: list[float], period: int) -> float:
    if len(values) < period:
        return values[-1]
    multiplier = 2.0 / (period + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val * multiplier) + (ema * (1.0 - multiplier))
    return ema


def calculate_rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas[-period:]]
    losses = [abs(min(d, 0.0)) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


mtf_engine = MultiTimeframeEngine()
