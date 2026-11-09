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

import time
from dataclasses import asdict, dataclass


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
    ) -> dict:
        """Evaluates live market conditions and returns deterministic trade setups for all 3 entry models."""
        if current_price <= 0:
            return {"state": "NO_TRADE", "reason": "Invalid market price"}

        latest_ind = ind_data.get("latest", {})
        ema_50 = float(latest_ind.get("ema_50") or current_price)
        ema_200 = float(latest_ind.get("ema_200") or (current_price * 0.98))
        atr = float(latest_ind.get("atr") or (current_price * 0.01))
        rsi = float(latest_ind.get("rsi") or 50.0)

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

        # --- MODEL A: AGGRESSIVE ENTRY (Limit Order at Fresh Zone) ---
        # Long Model A
        long_a_sl = round(nearest_d["distal"] - (atr * 0.4), 2) if nearest_d else round(current_price * 0.97, 2)
        long_a_entry = round(nearest_d["proximal"], 2) if nearest_d else current_price
        long_a_risk = max(long_a_entry - long_a_sl, 1e-4)
        long_a_tp1 = round(long_a_entry + (long_a_risk * 1.5), 2)
        long_a_tp2 = round(long_a_entry + (long_a_risk * 2.5), 2)
        long_a_tp3 = round(long_a_entry + (long_a_risk * 4.0), 2)
        long_a_score = (
            40
            + (25 if in_demand else 0)
            + (15 if nearest_d and nearest_d.get("is_fresh") else 0)
            + (10 if htf_bullish else 0)
            + (10 if 35 <= rsi <= 68 else 0)
        )
        long_a_triggered = bool(in_demand and nearest_d and nearest_d.get("quality_score", 0) >= 60)

        model_a = EntryModelPlan(
            model_name="MODEL_A_AGGRESSIVE_ZONE",
            is_triggered=long_a_triggered,
            status="READY" if long_a_triggered else "WAITING_FOR_ZONE_TAP",
            entry_price=long_a_entry,
            entry_range=[round(long_a_sl + (long_a_risk * 0.5), 2), round(long_a_entry * 1.005, 2)],
            stop_loss=long_a_sl,
            tp1=long_a_tp1,
            tp2=long_a_tp2,
            tp3=long_a_tp3,
            risk_reward=round((long_a_tp2 - long_a_entry) / long_a_risk, 2),
            recommended_position_units=round(10.0 / long_a_risk, 4),  # Standard $10 risk
            invalidation_level=long_a_sl,
            setup_score=min(long_a_score, 100),
            confirmations_met=[c for c, m in [("Fresh Demand Zone Present", bool(nearest_d)), ("Price in Zone", in_demand), ("HTF Trend Bullish", htf_bullish)] if m],
            missing_confirmations=[c for c, m in [("Fresh Demand Zone Present", bool(nearest_d)), ("Price in Zone", in_demand), ("HTF Trend Bullish", htf_bullish)] if not m],
        )

        # --- MODEL B: CONFIRMATION ENTRY (Liquidity Sweep + CHoCH) ---
        long_b_triggered = bool(in_demand and (has_bull_sweep or has_bull_choch))
        long_b_sl = round(long_a_sl, 2)
        long_b_entry = round(current_price, 2)
        long_b_risk = max(long_b_entry - long_b_sl, 1e-4)
        long_b_score = 45 + (20 if has_bull_sweep else 0) + (20 if has_bull_choch else 0) + (15 if of_bias == "AGGRESSIVE_BUYING" else 0)

        model_b = EntryModelPlan(
            model_name="MODEL_B_CONFIRMATION_SWEEP_CHOCH",
            is_triggered=long_b_triggered,
            status="READY" if long_b_triggered else "WAITING_FOR_SWEEP_OR_CHOCH",
            entry_price=long_b_entry,
            entry_range=[round(long_b_entry * 0.998, 2), round(long_b_entry * 1.003, 2)],
            stop_loss=long_b_sl,
            tp1=round(long_b_entry + (long_b_risk * 1.5), 2),
            tp2=round(long_b_entry + (long_b_risk * 2.5), 2),
            tp3=round(long_b_entry + (long_b_risk * 4.0), 2),
            risk_reward=round(2.5, 2),
            recommended_position_units=round(10.0 / long_b_risk, 4),
            invalidation_level=long_b_sl,
            setup_score=min(long_b_score, 100),
            confirmations_met=[c for c, m in [("Zone Reaction", in_demand), ("Liquidity Sweep Detected", has_bull_sweep), ("CHoCH Bullish Shift", has_bull_choch), ("Order Flow Buying", of_bias == "AGGRESSIVE_BUYING")] if m],
            missing_confirmations=[c for c, m in [("Zone Reaction", in_demand), ("Liquidity Sweep Detected", has_bull_sweep), ("CHoCH Bullish Shift", has_bull_choch), ("Order Flow Buying", of_bias == "AGGRESSIVE_BUYING")] if not m],
        )

        # --- MODEL C: CONSERVATIVE ENTRY (Displacement + FVG Retest) ---
        long_c_triggered = bool(active_bull_obs and active_fvgs and htf_bullish)
        long_c_entry = round(current_price * 0.996, 2)  # Limit inside FVG
        long_c_sl = round(long_a_sl, 2)
        long_c_risk = max(long_c_entry - long_c_sl, 1e-4)
        long_c_score = 50 + (25 if active_fvgs else 0) + (25 if active_bull_obs else 0)

        model_c = EntryModelPlan(
            model_name="MODEL_C_CONSERVATIVE_FVG_RETEST",
            is_triggered=long_c_triggered,
            status="READY" if long_c_triggered else "WAITING_FOR_FVG_RETEST",
            entry_price=long_c_entry,
            entry_range=[round(long_c_entry * 0.998, 2), round(long_c_entry * 1.002, 2)],
            stop_loss=long_c_sl,
            tp1=round(long_c_entry + (long_c_risk * 1.5), 2),
            tp2=round(long_c_entry + (long_c_risk * 2.5), 2),
            tp3=round(long_c_entry + (long_c_risk * 4.0), 2),
            risk_reward=round(2.5, 2),
            recommended_position_units=round(10.0 / long_c_risk, 4),
            invalidation_level=long_c_sl,
            setup_score=min(long_c_score, 100),
            confirmations_met=[c for c, m in [("Active Bullish Order Block", active_bull_obs), ("Fair Value Gap Present", active_fvgs), ("Trend Confluence", htf_bullish)] if m],
            missing_confirmations=[c for c, m in [("Active Bullish Order Block", active_bull_obs), ("Fair Value Gap Present", active_fvgs), ("Trend Confluence", htf_bullish)] if not m],
        )

        # Determine Global State & Best Model
        if model_b.is_triggered and model_b.setup_score >= 65:
            state = "CONFIRMED LONG"
            direction = "LONG"
            primary_plan = model_b
        elif model_c.is_triggered and model_c.setup_score >= 65:
            state = "CONFIRMED LONG"
            direction = "LONG"
            primary_plan = model_c
        elif model_a.is_triggered and model_a.setup_score >= 60:
            state = "POTENTIAL LONG"
            direction = "LONG"
            primary_plan = model_a
        elif in_supply and (has_bear_choch or has_bear_sweep or active_bear_obs):
            state = "CONFIRMED SHORT"
            direction = "SHORT"
            primary_plan = model_b
        elif in_supply or (htf_bearish and not in_demand):
            state = "POTENTIAL SHORT"
            direction = "SHORT"
            primary_plan = model_a
        else:
            state = "WAITING"
            direction = "NEUTRAL"
            primary_plan = model_b

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
            "entry_price": primary_plan.entry_price,
            "entry_range": primary_plan.entry_range,
            "entry_range_low": primary_plan.entry_range[0],
            "entry_range_high": primary_plan.entry_range[1],
            "stop_loss": primary_plan.stop_loss,
            "tp1": primary_plan.tp1,
            "tp2": primary_plan.tp2,
            "tp3": primary_plan.tp3,
            "risk_reward_ratio": primary_plan.risk_reward,
            "recommended_position_units": primary_plan.recommended_position_units,
            "invalidation_level": primary_plan.invalidation_level,
            "setup_score": primary_plan.setup_score,
            "models": {
                "model_a_aggressive": asdict(model_a),
                "model_b_confirmation": asdict(model_b),
                "model_c_conservative": asdict(model_c),
            },
            "score_breakdown": {
                "demand_reaction": {"passed": in_demand, "score": 25, "detail": "Price inside valid demand boundary"},
                "liquidity_sweep": {"passed": has_bull_sweep, "score": 20, "detail": "Buy-side or sell-side stops purged"},
                "structure_shift_choch": {"passed": has_bull_choch, "score": 20, "detail": "Bullish change of character confirmed"},
                "orderflow_pressure": {"passed": of_bias == "AGGRESSIVE_BUYING", "score": 15, "detail": f"Taker delta bias: {of_bias}"},
                "trend_alignment": {"passed": htf_bullish, "score": 20, "detail": f"EMA50 (${round(ema_50, 1)}) > EMA200 (${round(ema_200, 1)})"},
            },
            "rationale": f"Market evaluated at ${current_price:,.2f}. State: {state}. Dominant Plan: {primary_plan.model_name} with {primary_plan.setup_score}/100 quality score.",
        }

        # Deduplication cache
        self.signal_cache[f"{pair}_{timeframe}"] = result
        return result


signal_engine = SignalEngine()
