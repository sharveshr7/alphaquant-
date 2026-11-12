"""Comprehensive Individual Coin Analysis & 11-Tool Quantitative AI Trading Terminal.

Features:
- Tool 1: Market Structure (BOS, CHoCH, HH/HL, LH/LL, swings, trend, structural invalidation)
- Tool 2: Supply & Demand (Manual zone validation, overlap analysis, freshness, mitigation, strength)
- Tool 3: Smart Money Concepts (Order blocks, FVGs, liquidity sweeps, displacement, OTE 0.618-0.786)
- Tool 4: Liquidity Analysis (EQH/EQL, buy/sell side pools, stop clusters, liquidity grabs, targets)
- Tool 5: Volume Analysis (Relative volume RVOL, volume spikes, taker pressure, volume profile POC/VAH/VAL)
- Tool 6: Momentum Analysis (RSI, MACD, EMAs 9/21/50/200, momentum divergence, ADX exhaustion)
- Tool 7: Volatility Analysis (ATR, Bollinger squeeze/expansion, candle range, volatility SL bounds, regime)
- Tool 8: Multi-Timeframe Analysis (1D, 4H, 1H, 15M, 5M alignment matrix)
- Tool 9: Futures Derivatives Intelligence (Real Binance USDⓈ-M funding, open interest, OI delta, L/S ratios, basis)
- Tool 10: Market Context and Correlation (BTC/ETH direction, crypto regime, RS vs BTC, correlation)
- Tool 11: Trade Risk and Execution (Entry quality, structural SL, TP1-3, R:R, position sizing, fees, lifecycle)
- Transparent Combined AI Decision Engine: LONG, SHORT, or NO TRADE
- Future Market Scenario Analysis: Bullish, Bearish, and Neutral scenarios
- Professional Trading Signal Generation with full 11-tool audit.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from .coingecko_provider import get_market_cap_provider
from .liquidity_engine import liquidity_engine
from .market_data import (
    get_futures_derivatives_data,
    get_klines,
    get_market_context_and_correlation,
    get_open_interest,
    get_orderbook,
    get_premium_index,
    get_price_precision,
    get_ticker_24h,
    normalize_symbol,
    round_price_to_tick,
    TIMEFRAME_SECONDS,
)
from .multi_timeframe import mtf_engine
from .order_flow import order_flow_engine
from .paper_trader import paper_trader
from .risk_manager import validate_trade_plan
from .signal_engine import signal_engine
from .signal_tracker import signal_tracker
from .smc_engine import smc_engine
from .storage import safe_load_json
from .supply_demand import supply_demand_engine
from .technical_analysis import compute_indicators

logger = logging.getLogger("coin_analyzer")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DRAWINGS_FILE = DATA_DIR / "drawings.json"
if not DRAWINGS_FILE.exists() and (Path(__file__).resolve().parent.parent / "drawings.json").exists():
    DRAWINGS_FILE = Path(__file__).resolve().parent.parent / "drawings.json"


def _price_prec(val: float) -> int:
    aval = abs(val)
    if aval < 0.01:
        return 6
    if aval < 1.0:
        return 5
    if aval < 50.0:
        return 4
    return 2


def _fmt_usd(val: float) -> str:
    prec = _price_prec(val)
    return f"${val:,.{prec}f}"


class CoinAnalyzer:
    """Orchestrates 11-tool quantitative and institutional analysis for cryptocurrency markets."""

    async def analyze_coin(
        self,
        pair: str = "BTC/USDT",
        timeframe: str = "15m",
        candle_limit: int = 160,
        mtf_timeframes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Runs the complete 11-tool market intelligence pipeline strictly using closed bars."""
        norm_sym = normalize_symbol(pair)
        now_ts = int(time.time())

        # 1. Fetch real market feeds concurrently
        candles, ticker, oi_data, prem_data, ob_data, derivatives_data, market_ctx = await asyncio.gather(
            get_klines(pair, timeframe, limit=candle_limit),
            get_ticker_24h(pair),
            get_open_interest(norm_sym),
            get_premium_index(norm_sym),
            get_orderbook(pair, limit=10),
            get_futures_derivatives_data(norm_sym),
            get_market_context_and_correlation(pair),
            return_exceptions=True,
        )

        if isinstance(candles, Exception) or not candles or len(candles) < 30:
            candles = []

        ticker_dict = ticker if isinstance(ticker, dict) else {}
        current_price = (
            float(ticker_dict.get("last_price", 0.0))
            or (float(candles[-1]["close"]) if candles else 0.0)
        )
        quote_vol_24h = float(ticker_dict.get("quote_volume_24h", 0.0))

        # All analysis and signal decisions use closed candles only.
        confirmed_candles = (
            candles
            if candles and candles[-1].get("is_closed", False)
            else candles[:-1] if candles else []
        )
        interval_seconds = TIMEFRAME_SECONDS.get(timeframe)
        last_closed_timestamp = int(confirmed_candles[-1].get("time", 0)) if confirmed_candles else 0
        candle_is_fresh = bool(
            interval_seconds
            and last_closed_timestamp > 0
            and not confirmed_candles[-1].get("stale", False)
            and 0 <= now_ts - last_closed_timestamp <= interval_seconds * 2
        )
        ticker_timestamp = int(ticker_dict.get("timestamp", 0) or 0)
        ticker_is_fresh = bool(
            ticker_dict
            and not ticker_dict.get("stale", False)
            and ticker_timestamp > 0
            and 0 <= now_ts - ticker_timestamp <= 15
            and float(ticker_dict.get("last_price", 0.0)) > 0
        )
        data_valid = bool(
            len(confirmed_candles) >= 35
            and current_price > 0
            and candle_is_fresh
            and ticker_is_fresh
        )
        prec = _price_prec(current_price) if current_price > 0 else 2
        price_precision = get_price_precision(pair)

        # Spread & orderbook liquidity calculation
        spread_pct = None
        if isinstance(ob_data, dict):
            bids = ob_data.get("bids", [])
            asks = ob_data.get("asks", [])
            if bids and asks and bids[0][0] > 0 and asks[0][0] > bids[0][0]:
                spread_pct = round(((asks[0][0] - bids[0][0]) / bids[0][0]) * 100.0, 4)

        # 2. Market Cap & Metadata Enrichment
        coin_base = pair.split("/")[0].upper()
        mcap_provider = get_market_cap_provider()
        mc_map = {}
        try:
            mc_map = await mcap_provider.get_market_cap_map()
        except Exception:
            pass

        meta = mc_map.get(norm_sym) or mc_map.get(coin_base) or {}
        market_cap = meta.get("market_cap")
        market_cap_rank = meta.get("market_cap_rank")
        coin_name = meta.get("coin_name") or coin_base
        change_7d = meta.get("price_change_7d")
        change_30d = meta.get("price_change_30d")
        circulating_supply = meta.get("circulating_supply")
        fdv = meta.get("fully_diluted_valuation")

        # 3. Technical Indicators (on closed bars)
        ind_data = compute_indicators(confirmed_candles) if confirmed_candles else {"series": {}, "latest": {}, "trend": {}}
        latest_ind = ind_data.get("latest", {})
        trend_summary = ind_data.get("trend", {})
        required_indicators = ("atr", "rsi", "adx", "macdhist", "ema_50", "ema_200")
        try:
            indicator_values = {
                key: float(latest_ind[key])
                for key in required_indicators
            }
            indicators_valid = all(math.isfinite(value) for value in indicator_values.values())
        except (KeyError, TypeError, ValueError):
            indicator_values = {}
            indicators_valid = False
        indicators_valid = bool(
            indicators_valid
            and indicator_values["atr"] > 0
            and indicator_values["ema_50"] > 0
            and indicator_values["ema_200"] > 0
            and 0 <= indicator_values["rsi"] <= 100
        )
        data_valid = data_valid and indicators_valid

        # RVOL (Relative Volume on last closed bar vs 20-period volume SMA)
        rel_volume = 1.0
        if len(confirmed_candles) >= 21:
            vols = [float(c["volume"]) for c in confirmed_candles[-21:]]
            avg_v = sum(vols[:-1]) / max(len(vols[:-1]), 1)
            if avg_v > 0:
                rel_volume = round(vols[-1] / avg_v, 2)

        # 4. Supply & Demand (on closed bars)
        sd_data = supply_demand_engine.detect_zones(confirmed_candles, timeframe=timeframe) if confirmed_candles else {"demand": [], "supply": []}

        # 4b. Load & Evaluate User Manual Drawings (Manual S&D Validation + Overlap Analysis)
        user_drawings = []
        try:
            all_drawings = safe_load_json(DRAWINGS_FILE, default={})
            if isinstance(all_drawings, dict):
                # Check timeframe-specific key, then pair-wide
                user_drawings = all_drawings.get(f"{pair}_{timeframe}", []) or all_drawings.get(pair, [])
                if not user_drawings and "/" in pair:
                    clean_p = pair.replace("/", "")
                    user_drawings = all_drawings.get(f"{clean_p}_{timeframe}", []) or all_drawings.get(clean_p, [])
        except Exception as e:
            logger.debug(f"Manual drawings load notice: {e}")

        manual_sd_analysis = supply_demand_engine.analyze_manual_zones_and_overlaps(
            candles=confirmed_candles,
            manual_drawings=user_drawings,
            timeframe=timeframe,
        )

        # 5. Smart Money Concepts (SMC, on closed bars)
        smc_data = smc_engine.analyze(confirmed_candles) if confirmed_candles else {"swings": [], "bos_choch": [], "order_blocks": [], "fvg": [], "summary": {}}

        # 6. Order Flow & Liquidity
        of_data: dict[str, Any] = {}
        liq_data: dict[str, Any] = {}
        try:
            of_data = await order_flow_engine.compute_advanced_order_flow(pair, confirmed_candles)
            liq_data = await liquidity_engine.get_liquidity_summary(pair, confirmed_candles)
        except Exception as e:
            logger.debug(f"Orderflow/liquidity calculation warning: {e}")

        # 7. Multi-Timeframe Analysis Matrix
        mtf_data: dict[str, Any] = {}
        try:
            mtf_data = await mtf_engine.analyze_matrix(pair, timeframes=mtf_timeframes)
        except Exception as e:
            logger.debug(f"MTF matrix warning: {e}")
        required_mtf_timeframes = {"1d", "4h", "1h", "15m", "5m"}
        mtf_matrix = mtf_data.get("matrix")
        mtf_rows = {
            str(row.get("timeframe")): row
            for row in (mtf_matrix if isinstance(mtf_matrix, list) else [])
            if isinstance(row, dict)
        }
        mtf_context_fresh = bool(
            not mtf_data.get("has_stale_timeframe", True)
            and required_mtf_timeframes.issubset(mtf_rows)
            and all(
                mtf_rows[timeframe].get("freshness_ok") is True
                and mtf_rows[timeframe].get("bias") != "UNAVAILABLE"
                and int(mtf_rows[timeframe].get("last_candle_ts") or 0) > 0
                for timeframe in required_mtf_timeframes
            )
        )

        # 8. Derivatives Intelligence & Market Context (Tools 9 & 10)
        derivs = derivatives_data if isinstance(derivatives_data, dict) else {}
        ctx = market_ctx if isinstance(market_ctx, dict) else {}

        funding_rate = float(derivs.get("funding_rate_pct", 0.0))
        open_interest = float(derivs.get("open_interest", 0.0))
        oi_change_1h = float(derivs.get("open_interest_change_1h", 0.0))
        oi_change_1h_pct = float(derivs.get("open_interest_change_1h_pct", 0.0))
        futures_basis = float(derivs.get("futures_basis", 0.0))
        futures_basis_pct = float(derivs.get("futures_basis_pct", 0.0))
        ls_ratio = derivs.get("global_long_short_ratio")
        top_ls_ratio = derivs.get("top_trader_long_short_ratio")
        taker_ls_ratio = derivs.get("taker_buy_sell_ratio")

        # 9. Evaluate Signals & Confluence
        raw_signal = signal_engine.evaluate(
            pair=pair,
            timeframe=timeframe,
            current_price=current_price,
            sd_data=sd_data,
            smc_data=smc_data,
            ind_data=ind_data,
            orderflow_data=of_data,
            liquidity_data=liq_data,
            mtf_data=mtf_data,
        )

        # Core quantitative indicators
        atr = indicator_values.get("atr", 0.0)
        atr_pct = round((atr / current_price) * 100, 2) if current_price > 0 else 0.0
        rsi = indicator_values.get("rsi", 0.0)
        adx = indicator_values.get("adx", 0.0)
        macdhist = indicator_values.get("macdhist", 0.0)
        ema50 = indicator_values.get("ema_50", 0.0)
        ema200 = indicator_values.get("ema_200", 0.0)

        # Structure summary
        smc_sum = smc_data.get("summary", {})
        last_struct_event = smc_sum.get("last_structure_event")
        dominant_structure = smc_sum.get("dominant_structure", "NEUTRAL")
        swings = smc_data.get("swings", [])

        # Supply / Demand summary
        demands = sd_data.get("demand", [])
        supplies = sd_data.get("supply", [])
        nearest_d = demands[0] if demands else None
        nearest_s = supplies[0] if supplies else None

        in_demand = bool(nearest_d and current_price > 0 and (nearest_d["distal"] * 0.998 <= current_price <= nearest_d["proximal"] * 1.015))
        in_supply = bool(nearest_s and current_price > 0 and (nearest_s["proximal"] * 0.985 <= current_price <= nearest_s["distal"] * 1.002))

        mtf_bias = mtf_data.get("overall_bias", "MIXED_CONFLICTING")
        bull_htf_aligned = "BULLISH" in mtf_bias
        bear_htf_aligned = "BEARISH" in mtf_bias

        # Execution cost assumptions (Binance Futures round-trip)
        est_fees_roundtrip_pct = 0.08  # 0.04% taker x 2
        est_slippage_roundtrip_pct = (
            max(0.04, round(spread_pct * 1.5, 4))
            if spread_pct is not None
            else None
        )
        total_cost_pct = est_fees_roundtrip_pct + (est_slippage_roundtrip_pct or 0.0)

        # =========================================================================
        # BUILD THE 11 INDEPENDENT MARKET ANALYSIS TOOLS
        # =========================================================================

        # --- TOOL 1: Market Structure ---
        tool1_bias = "BULLISH" if dominant_structure == "BULLISH" else ("BEARISH" if dominant_structure == "BEARISH" else "NEUTRAL")
        tool1_evidence = []
        if last_struct_event:
            tool1_evidence.append(f"{last_struct_event.get('type')} ({last_struct_event.get('direction')}) at ${_fmt_usd(last_struct_event.get('level', 0))}")
        recent_swings_str = ", ".join([f"{s.get('classification', s.get('type'))} (${s.get('level'):,.2f})" for s in swings[-4:]]) if swings else "Forming"
        tool1_evidence.append(f"Recent Swings: {recent_swings_str}")
        struct_inval = swings[-1]["level"] if swings else (current_price * 0.95 if tool1_bias == "BULLISH" else current_price * 1.05)
        tool_1 = {
            "tool_id": 1,
            "name": "Market Structure",
            "bias": tool1_bias,
            "score": 85 if tool1_bias != "NEUTRAL" else 50,
            "evidence": tool1_evidence,
            "bos_choch_events": smc_data.get("bos_choch", [])[-5:],
            "swings": swings[-6:],
            "trend_direction": dominant_structure,
            "structural_invalidation_level": struct_inval,
            "data_availability": "AVAILABLE" if data_valid else "STALE_OR_MISSING",
            "last_update": now_ts,
        }

        # --- TOOL 2: Supply & Demand ---
        tool2_bias = "BULLISH" if in_demand else ("BEARISH" if in_supply else "NEUTRAL")
        tool2_evidence = []
        if manual_sd_analysis.get("zones"):
            m_count = len(manual_sd_analysis["zones"])
            tool2_evidence.append(f"{m_count} manual user zone(s) evaluated on chart")
            for mz in manual_sd_analysis["zones"][:2]:
                tool2_evidence.append(f"Manual {mz['zone_type']}: {mz['classification']} ({mz['mitigation_pct']}% mit, score {mz['quality_score']})")
        if manual_sd_analysis.get("overlaps"):
            tool2_evidence.append(f"Overlap Alert: {len(manual_sd_analysis['overlaps'])} overlapping zone pair(s) analyzed")
        if nearest_d:
            tool2_evidence.append(f"Nearest Demand: [{_fmt_usd(nearest_d['distal'])} - {_fmt_usd(nearest_d['proximal'])}] ({nearest_d['quality_score']} pts, {nearest_d['distance_pct']}% away)")
        if nearest_s:
            tool2_evidence.append(f"Nearest Supply: [{_fmt_usd(nearest_s['proximal'])} - {_fmt_usd(nearest_s['distal'])}] ({nearest_s['quality_score']} pts, {nearest_s['distance_pct']}% away)")

        tool_2 = {
            "tool_id": 2,
            "name": "Supply & Demand",
            "bias": tool2_bias,
            "score": (nearest_d["quality_score"] if in_demand and nearest_d else (nearest_s["quality_score"] if in_supply and nearest_s else 60)),
            "evidence": tool2_evidence,
            "manual_zones": manual_sd_analysis.get("zones", []),
            "overlap_analysis": manual_sd_analysis.get("overlaps", []),
            "active_demand_count": len(demands),
            "active_supply_count": len(supplies),
            "in_demand_zone": in_demand,
            "in_supply_zone": in_supply,
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 3: Smart Money Concepts ---
        obs = smc_data.get("order_blocks", [])
        fvgs = smc_data.get("fvg", [])
        active_bull_ob = [o for o in obs if o.get("type") == "BULLISH_OB" and not o.get("is_mitigated")]
        active_bear_ob = [o for o in obs if o.get("type") == "BEARISH_OB" and not o.get("is_mitigated")]
        active_bull_fvg = [f for f in fvgs if f.get("type") == "BULLISH_FVG" and not f.get("is_mitigated")]
        active_bear_fvg = [f for f in fvgs if f.get("type") == "BEARISH_FVG" and not f.get("is_mitigated")]

        tool3_bias = "BULLISH" if len(active_bull_ob) > len(active_bear_ob) else ("BEARISH" if len(active_bear_ob) > len(active_bull_ob) else "NEUTRAL")
        tool3_evidence = [
            f"Active Order Blocks: {len(active_bull_ob)} Bullish / {len(active_bear_ob)} Bearish",
            f"Active Fair Value Gaps: {len(active_bull_fvg)} Bullish / {len(active_bear_fvg)} Bearish",
            f"Dealing Range: {smc_data.get('premium_discount', {}).get('state', 'EQUILIBRIUM')} (OTE: 0.618 - 0.786 retracement)",
        ]
        tool_3 = {
            "tool_id": 3,
            "name": "Smart Money Concepts",
            "bias": tool3_bias,
            "score": 75 if (active_bull_ob or active_bear_ob) else 50,
            "evidence": tool3_evidence,
            "active_bullish_ob_count": len(active_bull_ob),
            "active_bearish_ob_count": len(active_bear_ob),
            "active_fvg_count": len(active_bull_fvg) + len(active_bear_fvg),
            "premium_discount": smc_data.get("premium_discount", {}),
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 4: Liquidity Analysis ---
        liq_summary = liq_data.get("summary", {}) if isinstance(liq_data, dict) else {}
        eqh_count = liq_summary.get("eqh_count", 0)
        eql_count = liq_summary.get("eql_count", 0)
        recent_sweeps = liq_summary.get("recent_sweeps_count", 0)
        tool4_bias = "BULLISH" if eql_count > eqh_count or liq_summary.get("last_sweep_type") == "EQL_SELL_STOPS" else (
            "BEARISH" if eqh_count > eql_count or liq_summary.get("last_sweep_type") == "EQH_BUY_STOPS" else "NEUTRAL"
        )
        tool4_evidence = [
            f"Equal Highs (Buy-Side Liq): {eqh_count} pools | Equal Lows (Sell-Side Liq): {eql_count} pools",
            f"Liquidity Sweeps Detected: {recent_sweeps} institutional turtle soup / stop hunts",
        ]
        if liq_summary.get("nearest_target"):
            tool4_evidence.append(f"Nearest Liquidity Target: {liq_summary.get('nearest_target')}")

        tool_4 = {
            "tool_id": 4,
            "name": "Liquidity Analysis",
            "bias": tool4_bias,
            "score": 70 if recent_sweeps > 0 else 55,
            "evidence": tool4_evidence,
            "eqh_count": eqh_count,
            "eql_count": eql_count,
            "recent_sweeps": recent_sweeps,
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 5: Volume Analysis ---
        of_vp = of_data.get("volume_profile", {}) if isinstance(of_data, dict) else {}
        poc = of_vp.get("poc")
        vah = of_vp.get("vah")
        val = of_vp.get("val")

        vol_bias = "BULLISH" if (rel_volume >= 1.2 and tool1_bias == "BULLISH") else (
            "BEARISH" if (rel_volume >= 1.2 and tool1_bias == "BEARISH") else "NEUTRAL"
        )
        tool5_evidence = [
            f"Relative Volume (RVOL): {rel_volume}x 20-period SMA volume",
            f"Volume Spikes: {'Detected (>1.5x)' if rel_volume >= 1.5 else 'Normal volume activity'}",
        ]
        if taker_ls_ratio is not None:
            tool5_evidence.append(f"Binance Taker Buy/Sell Ratio: {taker_ls_ratio:.2f} ({'Buyer aggression' if taker_ls_ratio > 1.05 else 'Seller aggression'})")
        if poc:
            tool5_evidence.append(f"Session Volume Profile: POC ${_fmt_usd(poc)} | VAH ${_fmt_usd(vah)} | VAL ${_fmt_usd(val)}")

        tool_5 = {
            "tool_id": 5,
            "name": "Volume Analysis",
            "bias": vol_bias,
            "score": 80 if rel_volume >= 1.3 else 60,
            "evidence": tool5_evidence,
            "relative_volume": rel_volume,
            "taker_buy_sell_ratio": taker_ls_ratio,
            "volume_profile": {"poc": poc, "vah": vah, "val": val} if poc else {},
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 6: Momentum Analysis ---
        mom_bias = "BULLISH" if (rsi > 52.0 and macdhist >= 0) else ("BEARISH" if (rsi < 48.0 and macdhist <= 0) else "NEUTRAL")
        rsi_exhausted = rsi >= 75.0 or rsi <= 25.0
        tool6_evidence = [
            f"RSI (14): {rsi:.1f} ({'Overbought' if rsi > 70 else ('Oversold' if rsi < 30 else 'Equilibrium range')})",
            f"MACD Histogram: {macdhist:.4f} ({'Expanding Bullish' if macdhist > 0 else 'Expanding Bearish'})",
            f"EMA Trend Alignment: EMA50 ${_fmt_usd(ema50)} vs EMA200 ${_fmt_usd(ema200)} ({'Bullish Golden Cross' if ema50 > ema200 else 'Bearish Death Cross'})",
            f"ADX Trend Strength: {adx:.1f} ({'Strong Trend' if adx >= 25 else 'Chop / Range Bound'})",
        ]
        if rsi_exhausted:
            tool6_evidence.append("Momentum Exhaustion Warning: RSI at extreme threshold, risk of mean-reversion")

        tool_6 = {
            "tool_id": 6,
            "name": "Momentum Analysis",
            "bias": mom_bias,
            "score": 80 if (not rsi_exhausted and adx >= 22) else 50,
            "evidence": tool6_evidence,
            "rsi": round(rsi, 2),
            "macd_histogram": round(macdhist, 4),
            "adx": round(adx, 2),
            "is_exhausted": rsi_exhausted,
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 7: Volatility Analysis ---
        bb_width_pct = round(float(latest_ind.get("bb_width", 0.0) or 0.0), 2)
        if bb_width_pct > 0 and bb_width_pct < 2.0:
            vol_regime = "COMPRESSED_SQUEEZE"
        elif adx >= 25:
            vol_regime = "EXPANDING_TREND"
        elif atr_pct > 3.0:
            vol_regime = "HIGH_VOLATILITY_CHOP"
        else:
            vol_regime = "STABLE_TREND"

        tool7_evidence = [
            f"ATR (14): ${_fmt_usd(atr)} ({atr_pct}% of price)",
            f"Market Volatility Regime: {vol_regime}",
            f"Safe Volatility-Adjusted SL Buffer: ${_fmt_usd(atr * 0.35)} to ${_fmt_usd(atr * 1.5)}",
        ]
        tool_7 = {
            "tool_id": 7,
            "name": "Volatility Analysis",
            "bias": "NEUTRAL",
            "score": 75 if vol_regime in ("EXPANDING_TREND", "STABLE_TREND") else 55,
            "evidence": tool7_evidence,
            "atr": round(atr, prec),
            "atr_pct": atr_pct,
            "regime": vol_regime,
            "bb_width_pct": bb_width_pct,
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 8: Multi-Timeframe Analysis ---
        tool8_bias = "BULLISH" if bull_htf_aligned else ("BEARISH" if bear_htf_aligned else "MIXED_CONFLICTING")
        matrix_list = mtf_data.get("matrix", [])
        tool8_evidence = [
            f"Multi-Timeframe Dominant Bias: {mtf_bias}",
            f"Cross-Timeframe Directional Alignment: {mtf_data.get('alignment_score', 0)}%",
        ]
        if matrix_list:
            tfs_str = ", ".join([f"{m['timeframe'].upper()}:{m['bias']}" for m in matrix_list[:5]])
            tool8_evidence.append(f"Timeframe Matrix: {tfs_str}")

        tool_8 = {
            "tool_id": 8,
            "name": "Multi-Timeframe Analysis",
            "bias": tool8_bias,
            "score": mtf_data.get("alignment_score", 50),
            "evidence": tool8_evidence,
            "timeframes_analyzed": ["1d", "4h", "1h", "15m", "5m"],
            "matrix": matrix_list,
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 9: Futures Derivatives Intelligence (Real Binance USDⓈ-M) ---
        deriv_bias = "BULLISH" if (funding_rate < 0.01 and oi_change_1h > 0 and (taker_ls_ratio or 1.0) > 1.0) else (
            "BEARISH" if (funding_rate > 0.04 and oi_change_1h > 0 and (taker_ls_ratio or 1.0) < 0.95) else "NEUTRAL"
        )
        tool9_evidence = [
            f"Funding Rate: {funding_rate:+.4f}% (Annualized: {funding_rate * 3 * 365:+.2f}%)",
            f"Open Interest: {open_interest:,.2f} contracts ({oi_change_1h:+,.2f} contracts / {oi_change_1h_pct:+.2f}% 1h change)",
        ]
        if ls_ratio is not None:
            tool9_evidence.append(f"Global Long/Short Ratio: {ls_ratio:.2f} ({derivs.get('global_long_pct', 50)}% Long / {derivs.get('global_short_pct', 50)}% Short)")
        if top_ls_ratio is not None:
            tool9_evidence.append(f"Top Trader Long/Short Ratio: {top_ls_ratio:.2f}")
        if futures_basis != 0.0:
            tool9_evidence.append(f"Futures Basis: {futures_basis:+.4f} ({futures_basis_pct:+.3f}%)")

        tool_9 = {
            "tool_id": 9,
            "name": "Futures Derivatives Intelligence",
            "bias": deriv_bias,
            "score": 85 if open_interest > 0 else 40,
            "evidence": tool9_evidence,
            "funding_rate_pct": funding_rate,
            "open_interest": open_interest,
            "open_interest_change_1h": oi_change_1h,
            "open_interest_change_1h_pct": oi_change_1h_pct,
            "global_long_short_ratio": ls_ratio,
            "top_trader_long_short_ratio": top_ls_ratio,
            "taker_buy_sell_ratio": taker_ls_ratio,
            "futures_basis": futures_basis,
            "futures_basis_pct": futures_basis_pct,
            "data_source": "Binance USDⓈ-M Futures Official REST API",
            "data_availability": "LIVE_BINANCE_FUTURES",
            "last_update": derivs.get("data_freshness_ts", now_ts),
        }

        # --- TOOL 10: Market Context and Correlation ---
        btc_dir = ctx.get("btc_direction", "NEUTRAL")
        eth_dir = ctx.get("eth_direction", "NEUTRAL")
        m_regime = ctx.get("market_regime", "NEUTRAL")
        rs_vs_btc = float(ctx.get("relative_strength_vs_btc", 0.0))
        btc_corr = float(ctx.get("correlation_with_btc", 1.0))

        tool10_bias = "BULLISH" if btc_dir == "BULLISH" and rs_vs_btc >= 0 else ("BEARISH" if btc_dir == "BEARISH" and rs_vs_btc <= 0 else "NEUTRAL")
        tool10_evidence = [
            f"BTC Market Direction: {btc_dir} ({ctx.get('btc_24h_change_pct', 0.0):+.2f}% 24h)",
            f"ETH Market Direction: {eth_dir} ({ctx.get('eth_24h_change_pct', 0.0):+.2f}% 24h)",
            f"Overall Crypto Regime: {m_regime}",
            f"Relative Strength vs BTC: {rs_vs_btc:+.2f}% ({ctx.get('relative_strength_label', 'IN_LINE')})",
            f"Correlation with BTC (35 closed candles): {btc_corr:.3f} ({ctx.get('correlation_strength', 'HIGH')})",
        ]
        tool_10 = {
            "tool_id": 10,
            "name": "Market Context & Correlation",
            "bias": tool10_bias,
            "score": 80 if abs(btc_corr) > 0.3 else 60,
            "evidence": tool10_evidence,
            "btc_direction": btc_dir,
            "eth_direction": eth_dir,
            "market_regime": m_regime,
            "relative_strength_vs_btc": rs_vs_btc,
            "correlation_with_btc": btc_corr,
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        # --- TOOL 11: Trade Risk and Execution ---
        entry_quality_score = 40
        if in_demand and tool1_bias == "BULLISH":
            entry_quality_score = 90
        elif in_supply and tool1_bias == "BEARISH":
            entry_quality_score = 90
        elif in_demand or in_supply:
            entry_quality_score = 65

        # Safe position sizing based on real paper portfolio
        current_paper_balance = float(paper_trader.balance)
        risk_per_trade_usdt = round(current_paper_balance * 0.015, 2)  # 1.5% capital risk
        est_sl_distance = max(atr * 1.5, current_price * 0.015)
        pos_size_units = round(risk_per_trade_usdt / max(est_sl_distance, 1e-4), 4)
        pos_size_usdt = round(pos_size_units * current_price, 2)

        tool11_evidence = [
            f"Execution Quality: Spread {f'{spread_pct:.3f}%' if spread_pct is not None else 'N/A'} | 24h Vol ${quote_vol_24h:,.0f} ({'Liquid & Safe' if spread_pct is not None and spread_pct <= 0.20 else 'Elevated spread'})",
            f"Estimated Fees & Slippage: Round-trip {f'{total_cost_pct:.2f}%' if est_slippage_roundtrip_pct is not None else 'N/A'} (Taker {est_fees_roundtrip_pct}% + Slippage {f'{est_slippage_roundtrip_pct:.2f}%' if est_slippage_roundtrip_pct is not None else 'N/A'})",
            f"Capital Preservation Sizing: Risk ${risk_per_trade_usdt:.2f} (1.5% of ${current_paper_balance:.2f}) -> {pos_size_units} units (${pos_size_usdt:.2f} notional)",
        ]
        tool_11 = {
            "tool_id": 11,
            "name": "Trade Risk & Execution",
            "bias": "NEUTRAL",
            "score": entry_quality_score,
            "evidence": tool11_evidence,
            "spread_pct": spread_pct,
            "total_cost_pct": total_cost_pct,
            "recommended_risk_usdt": risk_per_trade_usdt,
            "recommended_position_units": pos_size_units,
            "recommended_notional_usdt": pos_size_usdt,
            "entry_quality_score": entry_quality_score,
            "data_availability": "AVAILABLE",
            "last_update": now_ts,
        }

        eleven_tools = {
            "tool_1_market_structure": tool_1,
            "tool_2_supply_demand": tool_2,
            "tool_3_smart_money_concepts": tool_3,
            "tool_4_liquidity_analysis": tool_4,
            "tool_5_volume_analysis": tool_5,
            "tool_6_momentum_analysis": tool_6,
            "tool_7_volatility_analysis": tool_7,
            "tool_8_multi_timeframe": tool_8,
            "tool_9_futures_derivatives": tool_9,
            "tool_10_market_context": tool_10,
            "tool_11_trade_risk": tool_11,
        }

        # =========================================================================
        # COMBINED AI DECISION ENGINE: WEIGHTED SCORING & CONFLICT AUDIT
        # =========================================================================
        bullish_evidence: list[str] = []
        bearish_evidence: list[str] = []
        conflicting_evidence: list[str] = []
        missing_data: list[str] = []

        # Audit feeds for missing data
        if len(confirmed_candles) < 35:
            missing_data.append("Insufficient closed candles (need >= 35)")
        if not candle_is_fresh:
            missing_data.append("Latest closed candle is missing or stale")
        if not ticker_is_fresh:
            missing_data.append("Market ticker feed is missing or stale")
        if not indicators_valid:
            missing_data.append("Required technical indicator values are missing or invalid")
        if not mtf_context_fresh:
            missing_data.append("Required multi-timeframe data is missing or stale")
        if spread_pct is None:
            missing_data.append("Order-book spread data is unavailable")
        if open_interest == 0.0:
            missing_data.append("Futures Open Interest feed unavailable")
        if price_precision is None:
            missing_data.append("Binance contract price tick size is unavailable")

        # Compile evidence
        if tool1_bias == "BULLISH":
            bullish_evidence.append(f"Tool 1: Bullish market structure ({dominant_structure})")
        elif tool1_bias == "BEARISH":
            bearish_evidence.append(f"Tool 1: Bearish market structure ({dominant_structure})")

        if in_demand:
            bullish_evidence.append(f"Tool 2: Price testing Demand zone [{_fmt_usd(nearest_d['distal'])} - {_fmt_usd(nearest_d['proximal'])}]")
        if in_supply:
            bearish_evidence.append(f"Tool 2: Price testing Supply zone [{_fmt_usd(nearest_s['proximal'])} - {_fmt_usd(nearest_s['distal'])}]")

        if tool3_bias == "BULLISH":
            bullish_evidence.append(f"Tool 3: Net bullish order blocks ({len(active_bull_ob)} vs {len(active_bear_ob)})")
        elif tool3_bias == "BEARISH":
            bearish_evidence.append(f"Tool 3: Net bearish order blocks ({len(active_bear_ob)} vs {len(active_bull_ob)})")

        if tool4_bias == "BULLISH":
            bullish_evidence.append("Tool 4: Sell-side liquidity sweep / equal lows swept")
        elif tool4_bias == "BEARISH":
            bearish_evidence.append("Tool 4: Buy-side liquidity sweep / equal highs swept")

        if vol_bias == "BULLISH":
            bullish_evidence.append(f"Tool 5: High relative volume ({rel_volume}x) confirming bullish impulse")
        elif vol_bias == "BEARISH":
            bearish_evidence.append(f"Tool 5: High relative volume ({rel_volume}x) confirming bearish breakdown")

        if mom_bias == "BULLISH":
            bullish_evidence.append(f"Tool 6: Bullish momentum (RSI {rsi:.1f}, MACD Hist {macdhist:+.3f})")
        elif mom_bias == "BEARISH":
            bearish_evidence.append(f"Tool 6: Bearish momentum (RSI {rsi:.1f}, MACD Hist {macdhist:+.3f})")

        if bull_htf_aligned:
            bullish_evidence.append(f"Tool 8: Higher timeframe alignment bullish ({mtf_bias})")
        elif bear_htf_aligned:
            bearish_evidence.append(f"Tool 8: Higher timeframe alignment bearish ({mtf_bias})")

        if deriv_bias == "BULLISH":
            bullish_evidence.append(f"Tool 9: Favorable derivatives posture (OI {oi_change_1h:+,.0f}, Taker ratio {taker_ls_ratio or 1.0:.2f})")
        elif deriv_bias == "BEARISH":
            bearish_evidence.append(f"Tool 9: Bearish derivatives posture (Elevated funding {funding_rate:.4f}%, short pressure)")

        if tool10_bias == "BULLISH":
            bullish_evidence.append(f"Tool 10: Crypto market context risk-on (BTC {btc_dir}, RS {rs_vs_btc:+.1f}%)")
        elif tool10_bias == "BEARISH":
            bearish_evidence.append(f"Tool 10: Crypto market context risk-off (BTC {btc_dir}, RS {rs_vs_btc:+.1f}%)")

        # Conflict Detection: reduce confidence if indicators clash
        if tool1_bias == "BULLISH" and bear_htf_aligned:
            conflicting_evidence.append(f"Conflict: 15M structure is BULLISH but HTF alignment is BEARISH ({mtf_bias})")
        if tool1_bias == "BEARISH" and bull_htf_aligned:
            conflicting_evidence.append(f"Conflict: 15M structure is BEARISH but HTF alignment is BULLISH ({mtf_bias})")
        if in_demand and tool1_bias == "BEARISH":
            conflicting_evidence.append("Conflict: Testing Demand while dominant structure remains Bearish (Falling knife risk)")
        if in_supply and tool1_bias == "BULLISH":
            conflicting_evidence.append("Conflict: Testing Supply while dominant structure remains Bullish (Breakout continuation risk)")
        if tool1_bias == "BULLISH" and rsi > 72:
            conflicting_evidence.append(f"Conflict: Overbought RSI ({rsi:.1f}) opposing long entry")
        if tool1_bias == "BEARISH" and rsi < 28:
            conflicting_evidence.append(f"Conflict: Oversold RSI ({rsi:.1f}) opposing short entry")

        # Weighted Confluence Score (0 to 100)
        tool_weights = {
            "tool_1_market_structure": 0.18,
            "tool_2_supply_demand": 0.18,
            "tool_3_smart_money_concepts": 0.10,
            "tool_4_liquidity_analysis": 0.10,
            "tool_5_volume_analysis": 0.08,
            "tool_6_momentum_analysis": 0.08,
            "tool_7_volatility_analysis": 0.05,
            "tool_8_multi_timeframe": 0.15,
            "tool_9_futures_derivatives": 0.04,
            "tool_10_market_context": 0.04,
        }
        weighted_score = sum(eleven_tools[k]["score"] * w for k, w in tool_weights.items())

        # Penalize for conflicting evidence and missing data
        conflict_penalty = len(conflicting_evidence) * 12
        missing_penalty = len(missing_data) * 20
        overall_confluence_score = int(max(5, min(100, round(weighted_score - conflict_penalty - missing_penalty))))

        # Risk Classification
        if overall_confluence_score >= 75 and not conflicting_evidence and not missing_data:
            risk_classification = "LOW_RISK"
        elif overall_confluence_score >= 60 and len(conflicting_evidence) <= 1:
            risk_classification = "MODERATE_RISK"
        elif overall_confluence_score >= 40:
            risk_classification = "HIGH_RISK"
        else:
            risk_classification = "EXTREME_RISK"

        # Final 3-way Decision Synthesis: strictly LONG, SHORT, or NO TRADE
        decision = "NO TRADE"
        lifecycle_state = "SCANNING"
        what_must_happen_next: list[str] = []
        invalidation_condition = "No active setup; await clear zone test + structural confirmation."
        entry_price = None
        entry_range = None
        stop_loss = None
        inval_level = None
        tp1 = None
        tp2 = None
        tp3 = None
        rr_ratio = None
        net_rr_ratio = None

        liquidity_ok = (
            quote_vol_24h >= 1_000_000.0
            and spread_pct is not None
            and spread_pct <= 0.30
        )

        if not data_valid or missing_data:
            decision = "NO TRADE"
            lifecycle_state = "NO_TRADE"
            what_must_happen_next.extend(missing_data)
            what_must_happen_next.append("Restore fresh exchange data and required closed-bar history.")
        elif not liquidity_ok:
            decision = "NO TRADE"
            lifecycle_state = "NO_TRADE"
            what_must_happen_next.append("Trade contracts with >= $1M 24h volume and spread <= 0.30%")
        elif in_demand and tool1_bias == "BULLISH" and bull_htf_aligned and not conflicting_evidence and overall_confluence_score >= 70:
            # Verified Institutional LONG
            cand_entry = round_price_to_tick(pair, float(nearest_d["proximal"]))
            cand_sl = round_price_to_tick(pair, float(nearest_d["distal"]) - (atr * 0.35))
            risk_dist = cand_entry - cand_sl

            if risk_dist > 0 and risk_dist >= (atr * 0.35) and risk_dist <= (atr * 4.0):
                cand_tp1 = round_price_to_tick(pair, cand_entry + risk_dist * 1.5)
                cand_tp2 = round_price_to_tick(pair, cand_entry + risk_dist * 2.5)
                cand_tp3 = round_price_to_tick(pair, cand_entry + risk_dist * 4.0)
                risk_pct = (risk_dist / cand_entry) * 100.0
                gross_rr = round((cand_tp2 - cand_entry) / risk_dist, 2)
                net_rr = round(((cand_tp2 - cand_entry) / cand_entry * 100.0 - total_cost_pct) / max(risk_pct + total_cost_pct, 1e-4), 2)

                if net_rr >= 1.5:
                    decision = "LONG"
                    lifecycle_state = "CONFIRMED LONG"
                    entry_price = cand_entry
                    entry_range = [
                        round_price_to_tick(
                            pair,
                            float(nearest_d["distal"])
                            + (float(nearest_d["proximal"]) - float(nearest_d["distal"])) * 0.25,
                        ),
                        round_price_to_tick(pair, cand_entry * 1.002),
                    ]
                    stop_loss = cand_sl
                    inval_level = cand_sl
                    tp1 = cand_tp1
                    tp2 = cand_tp2
                    tp3 = cand_tp3
                    rr_ratio = gross_rr
                    net_rr_ratio = net_rr
                    what_must_happen_next.append("Execute limit order in entry range; trail SL to breakeven at TP1")
                    invalidation_condition = f"Confirmed candle close below {_fmt_usd(cand_sl)} invalidates LONG."

        elif in_supply and tool1_bias == "BEARISH" and bear_htf_aligned and not conflicting_evidence and overall_confluence_score >= 70:
            # Verified Institutional SHORT
            cand_entry = round_price_to_tick(pair, float(nearest_s["proximal"]))
            cand_sl = round_price_to_tick(pair, float(nearest_s["distal"]) + (atr * 0.35))
            risk_dist = cand_sl - cand_entry

            if risk_dist > 0 and risk_dist >= (atr * 0.35) and risk_dist <= (atr * 4.0):
                cand_tp1 = round_price_to_tick(pair, cand_entry - risk_dist * 1.5)
                cand_tp2 = round_price_to_tick(pair, cand_entry - risk_dist * 2.5)
                cand_tp3 = round_price_to_tick(pair, cand_entry - risk_dist * 4.0)
                risk_pct = (risk_dist / cand_entry) * 100.0
                gross_rr = round((cand_entry - cand_tp2) / risk_dist, 2)
                net_rr = round(((cand_entry - cand_tp2) / cand_entry * 100.0 - total_cost_pct) / max(risk_pct + total_cost_pct, 1e-4), 2)

                if net_rr >= 1.5:
                    decision = "SHORT"
                    lifecycle_state = "CONFIRMED SHORT"
                    entry_price = cand_entry
                    entry_range = [
                        round_price_to_tick(pair, cand_entry * 0.998),
                        round_price_to_tick(
                            pair,
                            float(nearest_s["distal"])
                            - (float(nearest_s["distal"]) - float(nearest_s["proximal"])) * 0.25,
                        ),
                    ]
                    stop_loss = cand_sl
                    inval_level = cand_sl
                    tp1 = cand_tp1
                    tp2 = cand_tp2
                    tp3 = cand_tp3
                    rr_ratio = gross_rr
                    net_rr_ratio = net_rr
                    what_must_happen_next.append("Confirm bearish rejection in entry zone; trail SL to breakeven at TP1")
                    invalidation_condition = f"Confirmed candle close above {_fmt_usd(cand_sl)} invalidates SHORT."

        plan_valid = False
        plan_validation_reason = "No actionable trade plan was generated."
        if decision in {"LONG", "SHORT"}:
            plan_valid, plan_validation_reason, validated_rr = validate_trade_plan(
                decision,
                entry_price,
                stop_loss,
                tp1,
                tp2,
                tp3,
            )
            if plan_valid:
                rr_ratio = round(validated_rr, 2)
            else:
                decision = "NO TRADE"
                lifecycle_state = "NO_TRADE"
                entry_price = entry_range = stop_loss = inval_level = None
                tp1 = tp2 = tp3 = rr_ratio = net_rr_ratio = None
                what_must_happen_next.append(plan_validation_reason)
        if not plan_valid and missing_data:
            plan_validation_reason = "Required evidence unavailable: " + "; ".join(missing_data)

        if decision == "NO TRADE" and lifecycle_state == "SCANNING":
            if in_demand:
                lifecycle_state = "WAITING FOR CONFIRMATION"
                what_must_happen_next.append(
                    f"Testing Demand: wait for confirmed Bullish CHoCH + expanding volume (>1.2x) on 5m/15m. Conflicting items: {len(conflicting_evidence)}"
                )
                invalidation_condition = f"Clean candle close below {_fmt_usd(nearest_d['distal'])} invalidates Demand."
            elif in_supply:
                lifecycle_state = "WAITING FOR CONFIRMATION"
                what_must_happen_next.append(
                    f"Testing Supply: wait for confirmed Bearish CHoCH + rejection wick. Conflicting items: {len(conflicting_evidence)}"
                )
                invalidation_condition = f"Clean candle close above {_fmt_usd(nearest_s['distal'])} invalidates Supply."
            else:
                lifecycle_state = "SCANNING"
                what_must_happen_next.append(
                    f"Wait for pullback to Demand ({_fmt_usd(nearest_d['proximal']) if nearest_d else 'N/A'}) "
                    f"or rally into Supply ({_fmt_usd(nearest_s['proximal']) if nearest_s else 'N/A'})"
                )
                invalidation_condition = "Mid-range equilibrium: no active position permitted until zone arrival."

        combined_decision = {
            "decision": decision,
            "overall_confluence_score": overall_confluence_score,
            "risk_classification": risk_classification,
            "higher_timeframe_bias": mtf_bias,
            "lower_timeframe_confirmation": "CONFIRMED" if decision in ("LONG", "SHORT") else "AWAITING_CHTR",
            "bullish_evidence": bullish_evidence,
            "bearish_evidence": bearish_evidence,
            "conflicting_evidence": conflicting_evidence,
            "missing_data": missing_data,
            "trade_plan_valid": plan_valid,
            "trade_plan_validation_reason": plan_validation_reason,
            "reasoning": (
                f"Combined Decision is {decision} based on confluence score of {overall_confluence_score}/100. "
                f"Bullish indicators ({len(bullish_evidence)}), Bearish indicators ({len(bearish_evidence)}), "
                f"Conflicts ({len(conflicting_evidence)} items reducing score). "
                f"{'Trade conditions fully satisfied with positive net expectancy.' if decision in ('LONG', 'SHORT') else 'System recommends NO TRADE to preserve capital until clean zone reaction + HTF alignment align.'}"
            ),
        }

        # =========================================================================
        # 5. FUTURE MARKET SCENARIO ANALYSIS
        # =========================================================================
        bull_target_1 = round(nearest_s["proximal"], prec) if nearest_s else round(current_price * 1.035, prec)
        bull_target_2 = round(current_price + (atr * 3.5), prec)
        bear_target_1 = round(nearest_d["proximal"], prec) if nearest_d else round(current_price * 0.965, prec)
        bear_target_2 = round(current_price - (atr * 3.5), prec)

        range_low = round(nearest_d["distal"], prec) if nearest_d else round(current_price - atr * 1.5, prec)
        range_high = round(nearest_s["distal"], prec) if nearest_s else round(current_price + atr * 1.5, prec)

        future_scenarios = {
            "bullish_scenario": {
                "name": "Bullish Continuation / Demand Bounce",
                "potential_upside_areas": [_fmt_usd(bull_target_1), _fmt_usd(bull_target_2)],
                "conditions_required_for_confirmation": [
                    f"Decisive 15m candle close above {_fmt_usd(current_price + atr * 0.8)}",
                    "Relative volume expansion >= 1.25x confirming buyer displacement",
                    "RSI sustaining above 55 without bearish divergence",
                ],
                "liquidity_targets": [f"Buy-Side Liquidity (BSL) at {_fmt_usd(bull_target_1)}", f"Equal Highs pool at {_fmt_usd(bull_target_2)}"],
                "invalidation_conditions": f"Break and close below {_fmt_usd(range_low)} invalidates bullish thesis.",
                "expected_timeframe": "4h to 24h",
                "supporting_evidence": bullish_evidence[:3],
                "conflicting_evidence": conflicting_evidence[:2],
            },
            "bearish_scenario": {
                "name": "Bearish Breakdown / Supply Rejection",
                "potential_downside_areas": [_fmt_usd(bear_target_1), _fmt_usd(bear_target_2)],
                "conditions_required_for_confirmation": [
                    f"Decisive 15m candle close below {_fmt_usd(current_price - atr * 0.8)}",
                    "Bearish Change of Character (CHoCH) on 5m/15m with volume spike",
                    "RSI breaking below 45 with expanding negative MACD histogram",
                ],
                "liquidity_targets": [f"Sell-Side Liquidity (SSL) at {_fmt_usd(bear_target_1)}", f"Equal Lows cluster at {_fmt_usd(bear_target_2)}"],
                "invalidation_conditions": f"Break and close above {_fmt_usd(range_high)} invalidates bearish thesis.",
                "expected_timeframe": "4h to 24h",
                "supporting_evidence": bearish_evidence[:3],
                "conflicting_evidence": conflicting_evidence[:2],
            },
            "neutral_scenario": {
                "name": "Mean-Reverting Range Consolidation",
                "consolidation_range": [_fmt_usd(range_low), _fmt_usd(range_high)],
                "important_support_resistance": {
                    "support": _fmt_usd(range_low),
                    "resistance": _fmt_usd(range_high),
                    "point_of_control": _fmt_usd(poc) if poc else _fmt_usd((range_low + range_high) / 2.0),
                },
                "conditions_that_could_cause_breakout": [
                    f"Impulsive displacement candle exceeding {_fmt_usd(atr * 1.5)} range",
                    "Large Open Interest surge (>5% in 1 hour) signaling new positional commitment",
                    "Bitcoin/Ethereum macro regime shift breaking out of local consolidation",
                ],
                "expected_timeframe": "8h to 48h",
            },
        }

        # =========================================================================
        # 6. PROFESSIONAL TRADING SIGNAL GENERATION
        # =========================================================================
        trade_plan = {
            "symbol": pair,
            "exchange": "Binance USDⓈ-M Futures",
            "direction": decision,
            "current_price": current_price,
            "signal_timestamp": now_ts,
            "current_price_timestamp": ticker_timestamp,
            "market_data_fresh": data_valid,
            "price_precision": price_precision,
            "setup_timeframe": timeframe,
            "htf_bias": mtf_bias,
            "mtf_context_fresh": mtf_context_fresh,
            "mtf_context_reason": (
                "Required 1D/4H/1H/15M/5M candles are fresh."
                if mtf_context_fresh
                else "Required 1D/4H/1H/15M/5M candles are missing or stale."
            ),
            "entry_price": entry_price,
            "entry_range": entry_range,
            "stop_loss": stop_loss,
            "invalidation_level": inval_level,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "risk_reward_ratio": rr_ratio,
            "net_risk_reward_ratio": net_rr_ratio,
            "trade_plan_valid": plan_valid,
            "validation_reason": (
                plan_validation_reason
                if plan_valid
                else "; ".join(missing_data) or plan_validation_reason
            ),
            "estimated_fees_pct": est_fees_roundtrip_pct,
            "estimated_slippage_pct": est_slippage_roundtrip_pct,
            "spread_pct": spread_pct,
            "funding_rate_pct": funding_rate,
            "signal_expiry_seconds": 3600 if decision in ("LONG", "SHORT") else None,
            "reference_levels": {
                "nearest_demand": [round(float(nearest_d["distal"]), prec), round(float(nearest_d["proximal"]), prec)] if nearest_d else None,
                "nearest_supply": [round(float(nearest_s["proximal"]), prec), round(float(nearest_s["distal"]), prec)] if nearest_s else None,
            },
            "why_enter": bullish_evidence if decision == "LONG" else (bearish_evidence if decision == "SHORT" else ["Awaiting valid zone reaction + confluence"]),
            "why_avoid": conflicting_evidence if conflicting_evidence else (
                [plan_validation_reason] if not plan_valid else
                ["Standard stop-loss & risk discipline applies"] if decision in ("LONG", "SHORT") else
                ["Confluence criteria not satisfied; risk of chop"]
            ),
            "what_must_happen_next": what_must_happen_next,
            "invalidation_condition": invalidation_condition,
            "eleven_tool_summary": {k: {"bias": v["bias"], "score": v["score"]} for k, v in eleven_tools.items()},
        }

        # Record genuinely confirmed setups in signal tracker
        if decision in ["LONG", "SHORT"] and plan_valid:
            try:
                signal_tracker.record_signal({
                    "pair": pair,
                    "timeframe": timeframe,
                    "direction": decision,
                    "entry_price": entry_price,
                    "entry_range": entry_range,
                    "current_price_at_signal": current_price,
                    "current_price_timestamp": ticker_timestamp,
                    "market_data_fresh": data_valid,
                    "price_precision": price_precision,
                    "stop_loss": stop_loss,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "invalidation_level": inval_level,
                    "expires_at": int(raw_signal.get("expires_at") or now_ts + 3600),
                    "setup_state": raw_signal.get("state", lifecycle_state),
                    "setup_score": overall_confluence_score,
                    "reasons_for": bullish_evidence if decision == "LONG" else bearish_evidence,
                    "reasons_against": conflicting_evidence,
                    "htf_bias": mtf_bias,
                    "mtf_reason": str(mtf_data.get("reason") or ""),
                    "mtf_is_fresh": mtf_context_fresh,
                    "invalidation_condition": invalidation_condition,
                    "primary_model": raw_signal.get("primary_model", "MODEL_B_CONFIRMATION"),
                })
            except Exception:
                logger.exception("Could not record a validated signal for %s", pair)

        # Summary narrative
        summary_narrative = (
            f"**{pair} ({timeframe}) Terminal Verdict**: `{decision}` ({lifecycle_state})\n\n"
            f"- **Confluence Score**: {overall_confluence_score}/100 | Risk: `{risk_classification}`\n"
            f"- **Market Price**: {_fmt_usd(current_price)} | 24h Chg: {ticker_dict.get('price_change_percent', 0.0):+.2f}% | Spread: {f'{spread_pct:.3f}%' if spread_pct is not None else 'N/A'}\n"
            f"- **Market Structure**: {dominant_structure} (Tool 1: {tool1_bias})\n"
            f"- **Supply & Demand**: In Demand: {in_demand} | In Supply: {in_supply} ({len(manual_sd_analysis.get('zones', []))} manual zones evaluated)\n"
            f"- **Derivatives Intelligence**: Funding {funding_rate:+.4f}% | OI Change {oi_change_1h:+,.0f} ({oi_change_1h_pct:+.2f}%)\n"
            f"- **Multi-Timeframe Alignment**: {mtf_bias} ({mtf_data.get('alignment_score', 0)}% directional alignment)\n"
            f"- **Next Action**: {what_must_happen_next[0] if what_must_happen_next else 'Monitor key zones'}\n"
            f"- **Invalidation**: {invalidation_condition}\n"
        )

        return {
            "pair": pair,
            "timeframe": timeframe,
            "timestamp": now_ts,
            "current_price": current_price,
            "ticker": ticker_dict,
            "metadata": {
                "coin_name": coin_name,
                "market_cap": market_cap,
                "market_cap_rank": market_cap_rank,
                "change_7d": change_7d,
                "change_30d": change_30d,
                "circulating_supply": circulating_supply,
                "fully_diluted_valuation": fdv,
            },
            "technical": {
                "indicators": latest_ind,
                "trend": trend_summary,
                "adx": adx,
                "atr": atr,
                "rsi": rsi,
                "relative_volume": rel_volume,
                "spread_pct": spread_pct,
                "technical_confluence_score": overall_confluence_score,
                "model_confidence": "HIGH" if overall_confluence_score >= 75 else ("MODERATE" if overall_confluence_score >= 55 else "LOW"),
            },
            "market_structure": smc_data,
            "supply_demand": sd_data,
            "order_flow": of_data,
            "liquidity": liq_data,
            "multi_timeframe": mtf_data,
            "funding_rate_pct": funding_rate,
            "open_interest": open_interest,
            "decision": decision,
            "lifecycle_state": lifecycle_state,
            "trade_plan": trade_plan,
            "narrative": summary_narrative,
            "tools": eleven_tools,
            "combined_decision": combined_decision,
            "future_scenarios": future_scenarios,
            "manual_zone_analysis": manual_sd_analysis,
        }


# Singleton instance
coin_analyzer = CoinAnalyzer()
