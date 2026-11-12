"""Deterministic Entry & Exit Signal Generation Engine - Phase 2.

Features:
- Three Specialized Institutional Entry Models:
  1. Model A (Aggressive Entry): Limit order at fresh S/D zone proximal boundary.
  2. Model B (Confirmation Entry): Triggered after Liquidity Sweep + confirmed CHoCH/BOS.
  3. Model C (Conservative Entry): Triggered after Displacement + FVG creation + Retest.
- Complete Setup Parameters:
  - Exact Entry & Entry Range [low, high]
  - Structural Stop Loss (beyond distal boundary + ATR buffer)
  - Multi-target Take Profits (TP1 at 1.5R, TP2 at 2.5R, TP3 at 4.0R)
  - Calculated Risk/Reward Ratio & Position Quantity
  - Dynamic 0-100 Quantitative Setup Score
  - Invalidation Level & Signal Expiration (candle countdown)
  - Duplicate Signal Suppression
- Strictly non-repainting: Evaluates on closed bar criteria.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from .market_data import get_price_precision, round_price_to_tick
from .risk_manager import validate_trade_plan


@dataclass
class EntryModelPlan:
    model_name: str  # "AGGRESSIVE_ZONE_LIMIT", "CONFIRMATION_SWEEP_CHOCH", "CONSERVATIVE_FVG_RETEST"
    is_triggered: bool
    status: str  # "READY", "PENDING_CONFIRMATION", "INACTIVE"
    entry_price: float
    entry_range: list[float]  # [min, max]
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward: float
    recommended_position_units: float
    invalidation_level: float
    setup_score: int
    confirmations_met: list[str]
    missing_confirmations: list[str]


@dataclass
class TradeSignal:
    id: str
    pair: str
    timeframe: str
    timestamp: int
    expires_at: int
    state: str  # WAITING, POTENTIAL LONG, CONFIRMED LONG, POTENTIAL SHORT, CONFIRMED SHORT, etc.
    direction: str  # 'LONG', 'SHORT', 'NEUTRAL'
    primary_model: str
    entry_price: float
    entry_range_low: float
    entry_range_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward_ratio: float
    invalidation_level: float
    setup_score: int
    models: dict  # Plans for Model A, B, and C
    score_breakdown: dict
    rationale: str


def _extract_mtf_context(mtf_data: dict | None, timeframe: str) -> dict:
    """Normalizes MTF evidence into higher-timeframe, entry-timeframe, and conflict metadata."""
    if not isinstance(mtf_data, dict) or not mtf_data:
        return {
            "has_data": False,
            "is_stale": True,
            "overall_bias": "MIXED_CONFLICTING",
            "higher_tf_bias": "NEUTRAL",
            "entry_tf_bias": "NEUTRAL",
            "alignment_score": 0,
            "conflicting_bias_detected": True,
            "reason": "MTF data missing or stale; no trade decision allowed.",
        }

    matrix = mtf_data.get("matrix") or []
    valid = [row for row in matrix if row.get("bias") not in (None, "UNAVAILABLE")]
    if not valid:
        return {
            "has_data": False,
            "is_stale": True,
            "overall_bias": "MIXED_CONFLICTING",
            "higher_tf_bias": "NEUTRAL",
            "entry_tf_bias": "NEUTRAL",
            "alignment_score": 0,
            "conflicting_bias_detected": True,
            "reason": "MTF matrix has no valid timeframe data; no trade decision allowed.",
        }

    higher_rows = [row for row in valid if row.get("timeframe") in {"1d", "4h", "1h"}]
    if higher_rows:
        bullish = sum(1 for row in higher_rows if row.get("bias") == "BULLISH")
        bearish = sum(1 for row in higher_rows if row.get("bias") == "BEARISH")
        higher_tf_bias = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"
    else:
        higher_tf_bias = "NEUTRAL"

    entry_row = next((row for row in valid if row.get("timeframe") == timeframe), valid[-1])
    entry_tf_bias = entry_row.get("bias") if isinstance(entry_row, dict) else "NEUTRAL"
    if entry_tf_bias not in {"BULLISH", "BEARISH", "NEUTRAL"}:
        entry_tf_bias = "NEUTRAL"

    overall_bias = mtf_data.get("overall_bias", "MIXED_CONFLICTING")
    alignment_score = int(float(mtf_data.get("alignment_score", 0) or 0))
    conflicting_bias_detected = bool(
        mtf_data.get("pipeline_4tier", {}).get("conflicting_bias_detected")
        or overall_bias == "MIXED_CONFLICTING"
        or (overall_bias in {"MIXED_CONFLICTING", "NEUTRAL"} and alignment_score <= 60)
    )

    tf_seconds = {
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
    now_ts = int(time.time())
    freshness_failures = []
    for row in valid:
        last_ts = int(float(row.get("last_candle_ts") or 0))
        tf_name = str(row.get("timeframe") or "")
        ttl = tf_seconds.get(tf_name, 3600) * 2
        if last_ts <= 0 or (now_ts - last_ts) > ttl:
            freshness_failures.append(tf_name)

    stale = bool(
        len(valid) < 3
        or mtf_data.get("pipeline_4tier") is None
        or (alignment_score == 0 and overall_bias == "MIXED_CONFLICTING")
        or bool(freshness_failures)
        or bool(mtf_data.get("has_stale_timeframe"))
    )

    return {
        "has_data": True,
        "is_stale": stale,
        "overall_bias": overall_bias,
        "higher_tf_bias": higher_tf_bias,
        "entry_tf_bias": entry_tf_bias,
        "alignment_score": alignment_score,
        "conflicting_bias_detected": conflicting_bias_detected,
        "reason": (
            f"MTF bias {overall_bias} ({alignment_score}% alignment); higher timeframe {higher_tf_bias}; "
            f"entry timeframe {entry_tf_bias}; conflict={str(conflicting_bias_detected).lower()}; "
            f"stale_timeframes={freshness_failures or 'none'}."
        ),
    }


def _no_trade_signal(pair: str, timeframe: str, reason: str) -> dict:
    now_sec = int(time.time())
    return {
        "id": f"sig_{pair.replace('/', '')}_{timeframe}_NO_TRADE",
        "pair": pair,
        "timeframe": timeframe,
        "timestamp": now_sec,
        "expires_at": now_sec + 3600,
        "state": "NO_TRADE",
        "direction": "NEUTRAL",
        "primary_model": "NO_VALIDATED_PLAN",
        "entry_price": None,
        "entry_range": None,
        "entry_range_low": None,
        "entry_range_high": None,
        "stop_loss": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "risk_reward_ratio": None,
        "recommended_position_units": 0.0,
        "invalidation_level": None,
        "setup_score": 0,
        "models": {},
        "mtf_context": _extract_mtf_context(None, timeframe),
        "score_breakdown": {},
        "trade_plan_valid": False,
        "validation_reason": reason,
        "rationale": reason,
    }


class SignalEngine:
    def __init__(self, min_risk_reward: float = 1.8) -> None:
        self.min_risk_reward = min_risk_reward
        self.signal_cache: dict[str, dict] = {}  # Cache by pair_timeframe for deduplication

    def evaluate(
        self,
        pair: str,
        timeframe: str,
        current_price: float,
        sd_data: dict,
        smc_data: dict,
        ind_data: dict,
        orderflow_data: dict | None = None,
        liquidity_data: dict | None = None,
        mtf_data: dict | None = None,
    ) -> dict:
        """Evaluates live market conditions and returns deterministic trade setups for all 3 entry models."""
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            return _no_trade_signal(pair, timeframe, "Market price is missing or invalid.")
        if not math.isfinite(current_price) or current_price <= 0:
            return _no_trade_signal(pair, timeframe, "Market price is missing or invalid.")
        price_precision = get_price_precision(pair)
        if price_precision is None:
            return _no_trade_signal(pair, timeframe, "Binance contract price tick size is unavailable.")
        current_price = round_price_to_tick(pair, current_price)

        latest_ind = ind_data.get("latest", {})
        try:
            ema_50, ema_200, atr, rsi = (
                float(latest_ind[key])
                for key in ("ema_50", "ema_200", "atr", "rsi")
            )
        except (KeyError, TypeError, ValueError):
            return _no_trade_signal(pair, timeframe, "Required indicator values are missing.")
        if (
            not all(math.isfinite(value) for value in (ema_50, ema_200, atr, rsi))
            or min(ema_50, ema_200, atr) <= 0
            or not 0 <= rsi <= 100
        ):
            return _no_trade_signal(pair, timeframe, "Required indicator values are invalid.")

        htf_bullish = bool(ema_50 > ema_200)
        htf_bearish = bool(ema_50 < ema_200)

        # 1. Supply and Demand
        demands = sd_data.get("demand", [])
        supplies = sd_data.get("supply", [])
        nearest_d = demands[0] if demands else None
        nearest_s = supplies[0] if supplies else None

        in_demand = bool(nearest_d and (nearest_d["distal"] * 0.999 <= current_price <= nearest_d["proximal"] * 1.015))
        in_supply = bool(nearest_s and (nearest_s["proximal"] * 0.985 <= current_price <= nearest_s["distal"] * 1.001))

        # 2. SMC Structures
        smc_summary = smc_data.get("summary", {})
        last_event = smc_summary.get("last_structure_event")
        has_bull_choch = bool(last_event and last_event.get("direction") == "BULLISH")
        has_bear_choch = bool(last_event and last_event.get("direction") == "BEARISH")
        active_bull_obs = smc_summary.get("active_bullish_ob_count", 0) > 0
        active_bear_obs = smc_summary.get("active_bearish_ob_count", 0) > 0
        active_fvgs = smc_summary.get("active_fvg_count", 0) > 0

        # 3. Liquidity Intelligence
        has_bull_sweep = False
        has_bear_sweep = False
        if liquidity_data:
            liq_sum = liquidity_data.get("summary", {})
            has_bull_sweep = bool(liq_sum.get("has_recent_bullish_sweep"))
            has_bear_sweep = bool(liq_sum.get("has_recent_bearish_sweep"))

        # 4. Order Flow Bias
        of_bias = "NEUTRAL"
        if orderflow_data:
            of_bias = orderflow_data.get("summary", {}).get("orderflow_bias") or orderflow_data.get("taker_delta", {}).get("orderflow_bias", "NEUTRAL")

        def build_plan(
            model_name: str,
            direction: str,
            triggered: bool,
            status: str,
            entry: float,
            stop: float,
            score: int,
            confirmations: list[str],
            missing: list[str],
        ) -> EntryModelPlan:
            is_long = direction == "LONG"
            try:
                entry = round_price_to_tick(pair, entry)
                stop = round_price_to_tick(pair, stop)
            except ValueError:
                return EntryModelPlan(
                    model_name=model_name,
                    is_triggered=False,
                    status="NO_VALID_PLAN",
                    entry_price=0.0,
                    entry_range=[0.0, 0.0],
                    stop_loss=0.0,
                    tp1=0.0,
                    tp2=0.0,
                    tp3=0.0,
                    risk_reward=0.0,
                    recommended_position_units=0.0,
                    invalidation_level=0.0,
                    setup_score=score,
                    confirmations_met=confirmations,
                    missing_confirmations=missing,
                )
            risk = entry - stop if is_long else stop - entry
            if not math.isfinite(risk) or risk <= 0:
                return EntryModelPlan(
                    model_name=model_name,
                    is_triggered=False,
                    status="NO_VALID_PLAN",
                    entry_price=0.0,
                    entry_range=[0.0, 0.0],
                    stop_loss=0.0,
                    tp1=0.0,
                    tp2=0.0,
                    tp3=0.0,
                    risk_reward=0.0,
                    recommended_position_units=0.0,
                    invalidation_level=0.0,
                    setup_score=score,
                    confirmations_met=confirmations,
                    missing_confirmations=missing,
                )

            sign = 1.0 if is_long else -1.0
            tp1 = round_price_to_tick(pair, entry + sign * risk * 1.5)
            tp2 = round_price_to_tick(pair, entry + sign * risk * 2.5)
            tp3 = round_price_to_tick(pair, entry + sign * risk * 4.0)
            valid, _, risk_reward = validate_trade_plan(
                direction, entry, stop, tp1, tp2, tp3, self.min_risk_reward
            )
            if not valid:
                return EntryModelPlan(
                    model_name=model_name,
                    is_triggered=False,
                    status="NO_VALID_PLAN",
                    entry_price=0.0,
                    entry_range=[0.0, 0.0],
                    stop_loss=0.0,
                    tp1=0.0,
                    tp2=0.0,
                    tp3=0.0,
                    risk_reward=0.0,
                    recommended_position_units=0.0,
                    invalidation_level=0.0,
                    setup_score=score,
                    confirmations_met=confirmations,
                    missing_confirmations=missing,
                )

            return EntryModelPlan(
                model_name=model_name,
                is_triggered=triggered,
                status="READY" if triggered else status,
                entry_price=entry,
                entry_range=[
                    round_price_to_tick(pair, entry * 0.998),
                    round_price_to_tick(pair, entry * 1.003),
                ],
                stop_loss=stop,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                risk_reward=round(risk_reward, 2),
                recommended_position_units=round(10.0 / risk, 4),
                invalidation_level=stop,
                setup_score=min(score, 100),
                confirmations_met=confirmations,
                missing_confirmations=missing,
            )

        long_a_sl = round_price_to_tick(pair, nearest_d["distal"] - atr * 0.4) if nearest_d else 0.0
        long_a_entry = round_price_to_tick(pair, nearest_d["proximal"]) if nearest_d else 0.0
        long_a_score = (
            40
            + (25 if in_demand else 0)
            + (15 if nearest_d and nearest_d.get("is_fresh") else 0)
            + (10 if htf_bullish else 0)
            + (10 if 35 <= rsi <= 68 else 0)
        )
        long_a_triggered = bool(in_demand and nearest_d and nearest_d.get("quality_score", 0) >= 60)
        model_a = build_plan(
            "MODEL_A_AGGRESSIVE_ZONE",
            "LONG",
            long_a_triggered,
            "WAITING_FOR_ZONE_TAP",
            long_a_entry,
            long_a_sl,
            long_a_score,
            [name for name, passed in [
                ("Fresh Demand Zone Present", bool(nearest_d)),
                ("Price in Zone", in_demand),
                ("HTF Trend Bullish", htf_bullish),
            ] if passed],
            [name for name, passed in [
                ("Fresh Demand Zone Present", bool(nearest_d)),
                ("Price in Zone", in_demand),
                ("HTF Trend Bullish", htf_bullish),
            ] if not passed],
        )

        long_b_triggered = bool(in_demand and (has_bull_sweep or has_bull_choch))
        long_b_score = 45 + (20 if has_bull_sweep else 0) + (20 if has_bull_choch else 0) + (15 if of_bias == "AGGRESSIVE_BUYING" else 0)
        model_b = build_plan(
            "MODEL_B_CONFIRMATION_SWEEP_CHOCH",
            "LONG",
            long_b_triggered,
            "WAITING_FOR_SWEEP_OR_CHOCH",
            round_price_to_tick(pair, current_price) if nearest_d else 0.0,
            long_a_sl,
            long_b_score,
            [name for name, passed in [
                ("Zone Reaction", in_demand),
                ("Liquidity Sweep Detected", has_bull_sweep),
                ("CHoCH Bullish Shift", has_bull_choch),
                ("Order Flow Buying", of_bias == "AGGRESSIVE_BUYING"),
            ] if passed],
            [name for name, passed in [
                ("Zone Reaction", in_demand),
                ("Liquidity Sweep Detected", has_bull_sweep),
                ("CHoCH Bullish Shift", has_bull_choch),
                ("Order Flow Buying", of_bias == "AGGRESSIVE_BUYING"),
            ] if not passed],
        )

        active_bull_fvgs = [
            fvg for fvg in smc_data.get("fvg", [])
            if fvg.get("type") == "BULLISH_FVG" and not fvg.get("is_mitigated")
        ]
        active_bear_fvgs = [
            fvg for fvg in smc_data.get("fvg", [])
            if fvg.get("type") == "BEARISH_FVG" and not fvg.get("is_mitigated")
        ]
        long_c_triggered = bool(
            in_demand and active_bull_obs and active_bull_fvgs and htf_bullish
        )
        long_c_score = 50 + (25 if active_bull_fvgs else 0) + (25 if active_bull_obs else 0)
        model_c = build_plan(
            "MODEL_C_CONSERVATIVE_FVG_RETEST",
            "LONG",
            long_c_triggered,
            "WAITING_FOR_FVG_RETEST",
            round_price_to_tick(pair, current_price) if nearest_d else 0.0,
            long_a_sl,
            long_c_score,
            [name for name, passed in [
                ("Active Bullish Order Block", active_bull_obs),
                ("Bullish Fair Value Gap Present", bool(active_bull_fvgs)),
                ("Trend Confluence", htf_bullish),
            ] if passed],
            [name for name, passed in [
                ("Active Bullish Order Block", active_bull_obs),
                ("Bullish Fair Value Gap Present", bool(active_bull_fvgs)),
                ("Trend Confluence", htf_bullish),
            ] if not passed],
        )

        short_a_sl = round_price_to_tick(pair, nearest_s["distal"] + atr * 0.4) if nearest_s else 0.0
        short_a_entry = round_price_to_tick(pair, nearest_s["proximal"]) if nearest_s else 0.0
        short_a_score = (
            40
            + (25 if in_supply else 0)
            + (15 if nearest_s and nearest_s.get("is_fresh") else 0)
            + (10 if htf_bearish else 0)
            + (10 if 32 <= rsi <= 65 else 0)
        )
        short_a_triggered = bool(in_supply and nearest_s and nearest_s.get("quality_score", 0) >= 60)
        model_a_short = build_plan(
            "MODEL_A_AGGRESSIVE_SUPPLY",
            "SHORT",
            short_a_triggered,
            "WAITING_FOR_ZONE_TAP",
            short_a_entry,
            short_a_sl,
            short_a_score,
            [name for name, passed in [
                ("Fresh Supply Zone Present", bool(nearest_s)),
                ("Price in Zone", in_supply),
                ("HTF Trend Bearish", htf_bearish),
            ] if passed],
            [name for name, passed in [
                ("Fresh Supply Zone Present", bool(nearest_s)),
                ("Price in Zone", in_supply),
                ("HTF Trend Bearish", htf_bearish),
            ] if not passed],
        )

        short_b_triggered = bool(in_supply and (has_bear_sweep or has_bear_choch))
        short_b_score = 45 + (20 if has_bear_sweep else 0) + (20 if has_bear_choch else 0) + (15 if of_bias == "AGGRESSIVE_SELLING" else 0)
        model_b_short = build_plan(
            "MODEL_B_CONFIRMATION_SWEEP_CHOCH_SHORT",
            "SHORT",
            short_b_triggered,
            "WAITING_FOR_SWEEP_OR_CHOCH",
            round_price_to_tick(pair, current_price) if nearest_s else 0.0,
            short_a_sl,
            short_b_score,
            [name for name, passed in [
                ("Zone Reaction", in_supply),
                ("Liquidity Sweep Detected", has_bear_sweep),
                ("CHoCH Bearish Shift", has_bear_choch),
                ("Order Flow Selling", of_bias == "AGGRESSIVE_SELLING"),
            ] if passed],
            [name for name, passed in [
                ("Zone Reaction", in_supply),
                ("Liquidity Sweep Detected", has_bear_sweep),
                ("CHoCH Bearish Shift", has_bear_choch),
                ("Order Flow Selling", of_bias == "AGGRESSIVE_SELLING"),
            ] if not passed],
        )

        short_c_triggered = bool(
            in_supply and active_bear_obs and active_bear_fvgs and htf_bearish
        )
        short_c_score = 50 + (25 if active_bear_fvgs else 0) + (25 if active_bear_obs else 0)
        model_c_short = build_plan(
            "MODEL_C_CONSERVATIVE_FVG_RETEST_SHORT",
            "SHORT",
            short_c_triggered,
            "WAITING_FOR_FVG_RETEST",
            round_price_to_tick(pair, current_price) if nearest_s else 0.0,
            short_a_sl,
            short_c_score,
            [name for name, passed in [
                ("Active Bearish Order Block", active_bear_obs),
                ("Bearish Fair Value Gap Present", bool(active_bear_fvgs)),
                ("Trend Confluence", htf_bearish),
            ] if passed],
            [name for name, passed in [
                ("Active Bearish Order Block", active_bear_obs),
                ("Bearish Fair Value Gap Present", bool(active_bear_fvgs)),
                ("Trend Confluence", htf_bearish),
            ] if not passed],
        )

        best_long_plan = model_c if model_c.is_triggered else model_b if model_b.is_triggered else model_a
        best_short_plan = (
            model_c_short
            if model_c_short.is_triggered
            else model_b_short
            if model_b_short.is_triggered
            else model_a_short
        )

        # Normalize the higher-timeframe context before deciding whether a paper trade is allowed.
        mtf_context = _extract_mtf_context(mtf_data, timeframe)
        strong_htf_bearish = bool(mtf_context["higher_tf_bias"] == "BEARISH" and mtf_context["alignment_score"] >= 70)
        strong_htf_bullish = bool(mtf_context["higher_tf_bias"] == "BULLISH" and mtf_context["alignment_score"] >= 70)
        entry_bias_bullish = bool(mtf_context["entry_tf_bias"] == "BULLISH")
        entry_bias_bearish = bool(mtf_context["entry_tf_bias"] == "BEARISH")
        conflicting_mtf = bool(mtf_context["conflicting_bias_detected"])

        # Determine Global State & Best Model.
        # Missing or conflicting evidence should not be forced into a trade decision.
        long_setup_qualified = bool(
            (model_a.is_triggered and model_a.setup_score >= 60)
            or (model_b.is_triggered and model_b.setup_score >= 65)
            or (model_c.is_triggered and model_c.setup_score >= 65)
            or (in_demand and (htf_bullish or not conflicting_mtf))
            or (entry_bias_bullish and not conflicting_mtf)
        )
        short_setup_qualified = bool(
            (model_a_short.is_triggered and model_a_short.setup_score >= 60)
            or (model_b_short.is_triggered and model_b_short.setup_score >= 65)
            or (model_c_short.is_triggered and model_c_short.setup_score >= 65)
            or (in_supply and (htf_bearish or not conflicting_mtf))
            or (entry_bias_bearish and not conflicting_mtf)
        )
        countertrend_long_valid = bool(
            entry_bias_bullish
            and in_demand
            and (has_bull_sweep or has_bull_choch or active_bull_obs)
            and not mtf_context["is_stale"]
        )
        countertrend_short_valid = bool(
            entry_bias_bearish
            and in_supply
            and (has_bear_sweep or has_bear_choch or active_bear_obs)
            and not mtf_context["is_stale"]
        )

        if not mtf_context["has_data"] or mtf_context["is_stale"]:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            primary_plan = model_b
            decision_reason = f"MTF evidence is missing or stale: {mtf_context['reason']}"
        elif strong_htf_bearish and countertrend_long_valid:
            state = "COUNTERTREND_LONG"
            direction = "LONG"
            primary_plan = best_long_plan
            decision_reason = (
                "Strong bearish higher-timeframe bias is opposing the bullish entry context. "
                "This is a countertrend LONG and requires independent confirmation, tighter risk controls, "
                "a valid demand-zone entry, and a confirmed reversal trigger. "
                f"MTF: {mtf_context['reason']}"
            )
        elif strong_htf_bullish and countertrend_short_valid:
            state = "COUNTERTREND_SHORT"
            direction = "SHORT"
            primary_plan = best_short_plan
            decision_reason = (
                "Strong bullish higher-timeframe bias is opposing the bearish entry context. "
                "This is a countertrend SHORT and requires independent confirmation, tighter risk controls, "
                "a valid supply-zone entry, and a confirmed reversal trigger. "
                f"MTF: {mtf_context['reason']}"
            )
        elif strong_htf_bearish and long_setup_qualified and not short_setup_qualified:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            primary_plan = best_long_plan
            decision_reason = (
                "Strong bearish higher-timeframe bias overrides a lower-timeframe long setup when no matching bearish confirmation exists. "
                "A long is disallowed until the higher-timeframe trend is re-accepted. "
                f"MTF: {mtf_context['reason']}"
            )
        elif strong_htf_bullish and short_setup_qualified and not long_setup_qualified:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            primary_plan = best_short_plan
            decision_reason = (
                "Strong bullish higher-timeframe bias overrides a lower-timeframe short setup when no matching bullish confirmation exists. "
                "A short is disallowed until the higher-timeframe trend is re-accepted. "
                f"MTF: {mtf_context['reason']}"
            )
        elif conflicting_mtf and not (long_setup_qualified or short_setup_qualified):
            state = "NO_TRADE"
            direction = "NEUTRAL"
            primary_plan = best_long_plan
            decision_reason = (
                "Conflicting MTF evidence without valid entry confirmation. "
                f"HTF={mtf_context['higher_tf_bias']}, entry={mtf_context['entry_tf_bias']}, "
                f"score={mtf_context['alignment_score']}. No trade is allowed until the conflict resolves."
            )
        elif strong_htf_bearish and (entry_bias_bullish or long_setup_qualified) and not countertrend_long_valid:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            primary_plan = best_long_plan
            decision_reason = (
                "Strong bearish higher-timeframe bias overrides a bullish lower-timeframe setup unless it is a valid countertrend reversal. "
                f"HTF={mtf_context['higher_tf_bias']}, entry={mtf_context['entry_tf_bias']}, score={mtf_context['alignment_score']}. "
                "No ordinary long is allowed under this regime."
            )
        elif strong_htf_bullish and (entry_bias_bearish or short_setup_qualified) and not countertrend_short_valid:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            primary_plan = best_short_plan
            decision_reason = (
                "Strong bullish higher-timeframe bias overrides a bearish lower-timeframe setup unless it is a valid countertrend reversal. "
                f"HTF={mtf_context['higher_tf_bias']}, entry={mtf_context['entry_tf_bias']}, score={mtf_context['alignment_score']}. "
                "No ordinary short is allowed under this regime."
            )
        elif model_b.is_triggered and model_b.setup_score >= 65 and (in_demand or has_bull_choch or has_bull_sweep):
            state = "CONFIRMED_LONG"
            direction = "LONG"
            primary_plan = model_b
            decision_reason = (
                "The lower-timeframe setup is confirmed by demand reaction or structural bullish confirmation, and "
                f"the MTF context does not contradict it. MTF: {mtf_context['reason']}"
            )
        elif model_c.is_triggered and model_c.setup_score >= 65 and (htf_bullish or in_demand):
            state = "CONFIRMED_LONG"
            direction = "LONG"
            primary_plan = model_c
            decision_reason = (
                "The conservative entry model is triggered with bullish structure and trend confluence. "
                f"MTF: {mtf_context['reason']}"
            )
        elif model_a.is_triggered and model_a.setup_score >= 60 and in_demand:
            state = "POTENTIAL_LONG"
            direction = "LONG"
            primary_plan = model_a
            decision_reason = (
                "The aggressive long model is triggered at a valid demand zone, but the setup is still weaker than a confirmed long. "
                f"MTF: {mtf_context['reason']}"
            )
        elif model_b_short.is_triggered and model_b_short.setup_score >= 65:
            state = "CONFIRMED_SHORT"
            direction = "SHORT"
            primary_plan = model_b_short
            decision_reason = (
                "The lower-timeframe setup is confirmed by supply reaction or structural bearish confirmation, and "
                f"the MTF context does not contradict it. MTF: {mtf_context['reason']}"
            )
        elif model_c_short.is_triggered and model_c_short.setup_score >= 65:
            state = "CONFIRMED_SHORT"
            direction = "SHORT"
            primary_plan = model_c_short
            decision_reason = (
                "The conservative entry model is triggered with bearish structure and trend confluence. "
                f"MTF: {mtf_context['reason']}"
            )
        elif model_a_short.is_triggered and model_a_short.setup_score >= 60:
            state = "POTENTIAL_SHORT"
            direction = "SHORT"
            primary_plan = model_a_short
            decision_reason = (
                "The aggressive short model is triggered at a valid supply zone, but the setup is still weaker than a confirmed short. "
                f"MTF: {mtf_context['reason']}"
            )
        elif long_setup_qualified:
            state = "POTENTIAL_LONG"
            direction = "LONG"
            primary_plan = model_c if model_c.is_triggered else model_b
            decision_reason = (
                "The setup is technically bullish, but the broader regime is not strong enough to upgrade to a confirmed long. "
                f"MTF: {mtf_context['reason']}"
            )
        elif short_setup_qualified:
            state = "POTENTIAL_SHORT"
            direction = "SHORT"
            primary_plan = best_short_plan
            decision_reason = (
                "The setup is technically bearish, but the broader regime is not strong enough to upgrade to a confirmed short. "
                f"MTF: {mtf_context['reason']}"
            )
        else:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            primary_plan = best_long_plan
            decision_reason = (
                "No valid trade trigger was present after filtering for MTF conflict, missing data, and entry confirmation. "
                f"MTF: {mtf_context['reason']}"
            )

        plan_valid, plan_reason, computed_risk_reward = validate_trade_plan(
            direction,
            primary_plan.entry_price,
            primary_plan.stop_loss,
            primary_plan.tp1,
            primary_plan.tp2,
            primary_plan.tp3,
            self.min_risk_reward,
        ) if direction in {"LONG", "SHORT"} else (False, "No directional trade plan.", 0.0)
        if direction in {"LONG", "SHORT"} and not plan_valid:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            decision_reason = f"Trade plan rejected: {plan_reason}"

        if state not in {"NO_TRADE", "WAITING", "POTENTIAL_LONG", "POTENTIAL_SHORT"} and primary_plan.risk_reward < self.min_risk_reward:
            state = "NO_TRADE"
            direction = "NEUTRAL"
            decision_reason = (
                f"Risk/reward {primary_plan.risk_reward} is below the minimum required {self.min_risk_reward}. "
                "No trade is allowed until risk controls are valid."
            )

        if state == "NO_TRADE":
            plan_valid = False
            computed_risk_reward = 0.0

        now_sec = int(time.time())
        signal_id = f"sig_{pair.replace('/', '')}_{timeframe}_{direction}_{state.replace(' ', '_')}"

        result = {
            "id": signal_id,
            "pair": pair,
            "timeframe": timeframe,
            "timestamp": now_sec,
            "expires_at": now_sec + 3600,  # 1 hour expiration
            "state": state,
            "direction": direction,
            "primary_model": primary_plan.model_name,
            "entry_price": primary_plan.entry_price if plan_valid else None,
            "entry_range": primary_plan.entry_range if plan_valid else None,
            "entry_range_low": primary_plan.entry_range[0] if plan_valid else None,
            "entry_range_high": primary_plan.entry_range[1] if plan_valid else None,
            "stop_loss": primary_plan.stop_loss if plan_valid else None,
            "tp1": primary_plan.tp1 if plan_valid else None,
            "tp2": primary_plan.tp2 if plan_valid else None,
            "tp3": primary_plan.tp3 if plan_valid else None,
            "risk_reward_ratio": round(computed_risk_reward, 2) if plan_valid else None,
            "recommended_position_units": primary_plan.recommended_position_units if plan_valid else 0.0,
            "invalidation_level": primary_plan.invalidation_level if plan_valid else None,
            "setup_score": primary_plan.setup_score,
            "price_precision": price_precision,
            "models": {
                "model_a_aggressive": asdict(model_a),
                "model_b_confirmation": asdict(model_b),
                "model_c_conservative": asdict(model_c),
                "model_a_short": asdict(model_a_short),
                "model_b_short": asdict(model_b_short),
                "model_c_short": asdict(model_c_short),
            },
            "mtf_context": {
                "has_data": mtf_context["has_data"],
                "is_stale": mtf_context["is_stale"],
                "overall_bias": mtf_context["overall_bias"],
                "higher_tf_bias": mtf_context["higher_tf_bias"],
                "entry_tf_bias": mtf_context["entry_tf_bias"],
                "alignment_score": mtf_context["alignment_score"],
                "conflicting_bias_detected": mtf_context["conflicting_bias_detected"],
                "reason": mtf_context["reason"],
            },
            "trade_plan_valid": plan_valid,
            "validation_reason": "Valid trade plan." if plan_valid else plan_reason,
            "score_breakdown": {
                "demand_reaction": {"passed": in_demand, "score": 25, "detail": "Price inside valid demand boundary"},
                "liquidity_sweep": {"passed": has_bull_sweep, "score": 20, "detail": "Buy-side or sell-side stops purged"},
                "structure_shift_choch": {"passed": has_bull_choch, "score": 20, "detail": "Bullish change of character confirmed"},
                "orderflow_pressure": {"passed": of_bias == "AGGRESSIVE_BUYING", "score": 15, "detail": f"Taker delta bias: {of_bias}"},
                "trend_alignment": {"passed": htf_bullish, "score": 20, "detail": f"EMA50 (${round(ema_50, 1)}) > EMA200 (${round(ema_200, 1)})"},
                "mtf_alignment": {
                    "passed": not mtf_context["conflicting_bias_detected"],
                    "score": mtf_context["alignment_score"],
                    "detail": mtf_context["reason"],
                },
            },
            "rationale": decision_reason,
        }

        # Deduplication cache
        self.signal_cache[f"{pair}_{timeframe}"] = result
        return result


signal_engine = SignalEngine()
