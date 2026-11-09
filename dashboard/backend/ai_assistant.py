"""Context-Aware AI Quantitative Trading Assistant with Gemini API - Phase 2.

Features:
- Live Market Explanation (Price, Session, Volume Profile POC/VAH/VAL, Taker Delta)
- Trade Setup Explanation (Models A, B, C with Entry Range, SL, TP1-3, Risk/Reward)
- Rejected Signal Explanation (Why signal was invalidated or score penalized)
- Entry Model Comparison (Aggressive vs Confirmation vs Conservative)
- Institutional Risk & Mathematical Position Sizing Analysis
- Post-Trade Review & Lessons from Paper Portfolio
- Strict Safety: Never fabricates prices, indicators, or trades.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import config


logger = logging.getLogger("ai_assistant")

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

CANDIDATE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
]


def _format_sd_telemetry(sd_data: dict[str, Any] | None) -> str:
    """Formats Supply and Demand zones into readable context."""
    if not sd_data:
        return "No active zones detected in current window."
    demands = sd_data.get("demand", [])
    supplies = sd_data.get("supply", [])
    lines: list[str] = []
    if demands:
        d_parts = [
            f"${d['distal']:,.2f}-${d['proximal']:,.2f} "
            f"(Score: {d['quality_score']}, State: {d.get('lifecycle_state', 'ACTIVE')}, "
            f"Mit: {d.get('mitigation_pct', 0)}%, {d['distance_pct']}% away)"
            for d in demands[:3]
        ]
        lines.append(f"- Demand Zones (Support): {'; '.join(d_parts)}")
    if supplies:
        s_parts = [
            f"${s['proximal']:,.2f}-${s['distal']:,.2f} "
            f"(Score: {s['quality_score']}, State: {s.get('lifecycle_state', 'ACTIVE')}, "
            f"Mit: {s.get('mitigation_pct', 0)}%, {s['distance_pct']}% away)"
            for s in supplies[:3]
        ]
        lines.append(f"- Supply Zones (Resistance): {'; '.join(s_parts)}")
    return "\n".join(lines) if lines else "No active zones detected in current window."


def _format_smc_telemetry(smc_data: dict[str, Any] | None) -> str:
    """Formats Smart Money Concepts market structure into context."""
    if not smc_data:
        return "No major structure events active."
    summary = smc_data.get("summary", {})
    prem_disc = smc_data.get("premium_discount", {})
    last_evt = summary.get("last_structure_event")
    evt_str = (
        f"{last_evt['type']} {last_evt['direction']} at ${last_evt['level']:,.2f}"
        if last_evt
        else "None"
    )
    regime = prem_disc.get("current_regime", "Neutral")
    eq_50 = prem_disc.get("equilibrium_50", 0.0)
    bull_ob = summary.get("active_bullish_ob_count", 0)
    bear_ob = summary.get("active_bearish_ob_count", 0)
    fvg_count = summary.get("active_fvg_count", 0)

    return (
        f"- Market Structure: {summary.get('current_structure', 'NEUTRAL')}\n"
        f"- Latest Structure Break: {evt_str}\n"
        f"- Valuation Regime: {regime} (50% Equilibrium: ${eq_50:,.2f})\n"
        f"- Active Unmitigated Order Blocks: {bull_ob} Bullish / {bear_ob} Bearish\n"
        f"- Active Fair Value Gaps (FVG): {fvg_count}\n"
    )


def _format_orderflow_telemetry(orderflow_data: dict[str, Any] | None) -> str:
    """Formats order flow and volume profile metrics."""
    if not orderflow_data:
        return "Order flow metrics unavailable."
    vp = orderflow_data.get("volume_profile", {})
    delta = orderflow_data.get("taker_delta", {})
    bias = delta.get("orderflow_bias", "N/A")
    net_d = delta.get("net_delta", 0)
    pct = delta.get("delta_bias_pct", 0)
    imb_cnt = len(orderflow_data.get("imbalances", []))
    abs_cnt = len(orderflow_data.get("absorptions", []))

    return (
        f"- Session POC (Point of Control): ${vp.get('poc', 'N/A')}\n"
        f"- Value Area: VAH ${vp.get('vah', 'N/A')} / VAL ${vp.get('val', 'N/A')} "
        f"({vp.get('price_location', 'In VA')})\n"
        f"- Order Flow Delta: {bias} (Net Delta: {net_d} units, {pct}%)\n"
        f"- Stacked Imbalances Count: {imb_cnt}\n"
        f"- Absorptions Detected: {abs_cnt}\n"
    )


def _format_liquidity_telemetry(liquidity_data: dict[str, Any] | None) -> str:
    """Formats depth and resting liquidity walls."""
    if not liquidity_data:
        return "Liquidity metrics unavailable."
    l2 = liquidity_data.get("orderbook_l2", {})
    sp = liquidity_data.get("structural_pools", {})
    l_sum = liquidity_data.get("summary", {})
    walls = l2.get("liquidity_walls", [])
    wall_str = (
        ", ".join(
            [f"{w['side']} ${w['price']:,.2f} (${w['total_value_usd']:,.0f})" for w in walls[:2]]
        )
        if walls
        else "None"
    )
    b_pools = len(sp.get("buy_side_pools", []))
    s_pools = len(sp.get("sell_side_pools", []))
    bsl = l_sum.get("nearest_buy_side_liquidity_level", "N/A")
    ssl = l_sum.get("nearest_sell_side_liquidity_level", "N/A")

    return (
        f"- L2 Depth Imbalance: {l2.get('imbalance_ratio', 0.0)} ({l2.get('bias', 'BALANCED')})\n"
        f"- Resting Liquidity Walls: {wall_str}\n"
        f"- Structural Pools: Buy-side {b_pools} pools, Sell-side {s_pools} pools\n"
        f"- Nearest Inferred Liquidity Pools: BSL ${bsl} | SSL ${ssl}\n"
        f"- Recent Liquidity Sweeps: Bullish={l_sum.get('has_recent_bullish_sweep', False)} | "
        f"Bearish={l_sum.get('has_recent_bearish_sweep', False)}\n"
    )


def _format_signal_telemetry(signal_data: dict[str, Any] | None) -> str:
    """Formats 3-tier models and deterministic signal."""
    if not signal_data:
        return "Signal state: WAITING"
    models = signal_data.get("models", {})
    mod_a = models.get("model_a_aggressive", {})
    mod_b = models.get("model_b_confirmation", {})
    mod_c = models.get("model_c_conservative", {})

    return (
        f"- Deterministic Signal State: {signal_data.get('state', 'WAITING')}\n"
        f"- Direction: {signal_data.get('direction', 'NEUTRAL')} | "
        f"Setup Score: {signal_data.get('setup_score', 0)}/100\n"
        f"- Primary Recommended Model: {signal_data.get('primary_model', 'N/A')}\n"
        f"  * Model A (Aggressive Limit): Status={mod_a.get('status', 'N/A')}, "
        f"Entry=${mod_a.get('entry_price', 0):,.2f}, SL=${mod_a.get('stop_loss', 0):,.2f}, "
        f"TP2=${mod_a.get('tp2', 0):,.2f}, Score={mod_a.get('setup_score', 0)}\n"
        f"  * Model B (Confirmation): Status={mod_b.get('status', 'N/A')}, "
        f"Entry=${mod_b.get('entry_price', 0):,.2f}, SL=${mod_b.get('stop_loss', 0):,.2f}, "
        f"TP2=${mod_b.get('tp2', 0):,.2f}, Score={mod_b.get('setup_score', 0)}\n"
        f"  * Model C (Conservative FVG): Status={mod_c.get('status', 'N/A')}, "
        f"Entry=${mod_c.get('entry_price', 0):,.2f}, SL=${mod_c.get('stop_loss', 0):,.2f}, "
        f"TP2=${mod_c.get('tp2', 0):,.2f}, Score={mod_c.get('setup_score', 0)}\n"
        f"- Invalidation Level: ${signal_data.get('invalidation_level', 0):,.2f}\n"
    )


def _build_full_context(
    pair: str,
    interval: str,
    current_price: float,
    sd_data: dict[str, Any] | None,
    smc_data: dict[str, Any] | None,
    orderflow_data: dict[str, Any] | None,
    liquidity_data: dict[str, Any] | None,
    mtf_data: dict[str, Any] | None,
    signal_data: dict[str, Any] | None,
    portfolio_data: dict[str, Any] | None,
) -> str:
    """Builds unified validated telemetry prompt for Gemini API."""
    mtf_str = ""
    if mtf_data:
        pipe = mtf_data.get("pipeline_4tier", {})
        bias = mtf_data.get("overall_bias", "MIXED")
        score = mtf_data.get("alignment_score", 50)
        mtf_str = (
            f"- 4H Macro Bias: {pipe.get('tier1_4h_macro_bias', 'N/A')}\n"
            f"- 1H Structure: {pipe.get('tier2_1h_market_structure', 'N/A')}\n"
            f"- 15M Operational S/D: {pipe.get('tier3_15m_operational_zones', 'N/A')}\n"
            f"- 5M Tactical Trigger: {pipe.get('tier4_5m_tactical_trigger', 'N/A')}\n"
            f"- Overall MTF Alignment: {bias} (Score: {score}%)\n"
        )

    port_str = ""
    if portfolio_data:
        open_pos = portfolio_data.get("open_positions", [])
        pos_summary = (
            ", ".join([f"{p['pair']} {p['side']} (PnL: ${p.get('pnl', 0):,.2f})" for p in open_pos])
            if open_pos
            else "No open positions"
        )
        port_str = (
            f"- Equity: ${portfolio_data.get('equity', 1000):,.2f} | "
            f"Balance: ${portfolio_data.get('balance', 1000):,.2f}\n"
            f"- Open Positions: {pos_summary}\n"
            f"- Realized PnL: ${portfolio_data.get('realized_pnl', 0):,.2f} | "
            f"Win Rate: {portfolio_data.get('win_rate', 0)}%\n"
        )

    return f"""=== LIVE VALIDATED MARKET DATA ===
Symbol: {pair} | Current Timeframe: {interval} | Live Price: ${current_price:,.2f}

=== 1. SUPPLY & DEMAND ZONES (PHASE 2) ===
{_format_sd_telemetry(sd_data)}

=== 2. SMART MONEY CONCEPTS (SMC) ===
{_format_smc_telemetry(smc_data)}

=== 3. ORDER FLOW & VOLUME PROFILE ===
{_format_orderflow_telemetry(orderflow_data)}

=== 4. LIQUIDITY INTELLIGENCE (L2 DEPTH & POOLS) ===
{_format_liquidity_telemetry(liquidity_data)}

=== 5. MULTI-TIMEFRAME 4-TIER PIPELINE ===
{mtf_str or "MTF data unavailable."}

=== 6. DETERMINISTIC SIGNAL & 3 ENTRY MODELS ===
{_format_signal_telemetry(signal_data)}

=== 7. PORTFOLIO & RISK STATE ===
{port_str or "Portfolio idle."}
"""


SYSTEM_INSTRUCTION = (
    "You are the Senior Quantitative AI Trading Intelligence Partner in the "
    "AlphaQuant AI Pro Trading Terminal.\n"
    "You specialize in Supply and Demand, Smart Money Concepts (SMC), Order Flow Footprint, "
    "and Liquidity Engineering.\n"
    "Rules:\n"
    "1. Always base your response strictly on the provided live telemetry. "
    "Never fabricate prices, zones, indicators, or setups.\n"
    "2. Structure your response with clean Markdown:\n"
    "   - **Executive Market Assessment**: (Dominant bias, 4H-1H-15M-5M alignment, price regime)\n"
    "   - **Key Levels & Liquidity Map**: (Exact proximal/distal zones, POC/Value Area, "
    "L2 depth walls, BSL/SSL pools)\n"
    "   - **Entry Models Breakdown**:\n"
    "     * Compare Model A (Aggressive), Model B (Confirmation), and Model C (Conservative).\n"
    "     * State exact entry ranges, structural stop-loss, and multi-targets (TP1/TP2/TP3).\n"
    "   - **Signal Audit & Rejection Analysis**: (If signal is WAITING or score < 65, "
    "explain mathematically which confirmations are missing).\n"
    "   - **Risk Management**: (Mathematical position sizing and invalidation level).\n"
    "3. Keep your answers direct, sharp, professional, and mathematically grounded.\n"
)


async def generate_ai_response(
    user_message: str,
    pair: str,
    interval: str,
    current_price: float,
    indicators: dict[str, Any],
    sd_data: dict[str, Any] | None = None,
    smc_data: dict[str, Any] | None = None,
    orderflow_data: dict[str, Any] | None = None,
    liquidity_data: dict[str, Any] | None = None,
    mtf_data: dict[str, Any] | None = None,
    signal_data: dict[str, Any] | None = None,
    portfolio_data: dict[str, Any] | None = None,
    confluence: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generates an institutional quantitative analysis response for the trader."""
    _ = (confluence, kwargs)
    api_key = config.gemini_api_key

    full_context = _build_full_context(
        pair,
        interval,
        current_price,
        sd_data,
        smc_data,
        orderflow_data,
        liquidity_data,
        mtf_data,
        signal_data,
        portfolio_data,
    )

    if api_key:
        for model_name in CANDIDATE_MODELS:
            endpoint = f"{GEMINI_API_BASE}/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"{SYSTEM_INSTRUCTION}\n\nContext:\n{full_context}\n\n"
                                    f"User Question: {user_message}"
                                )
                            }
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1000,
                },
            }

            try:
                async with httpx.AsyncClient(timeout=14.0) as client:
                    res = await client.post(endpoint, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            content = candidates[0].get("content", {})
                            parts = content.get("parts", [])
                            if parts:
                                return {
                                    "response": parts[0].get("text", ""),
                                    "model_used": model_name,
                                    "source": "gemini_api",
                                    "engine": "gemini_api",
                                }
                    logger.warning("Model %s returned HTTP %s", model_name, res.status_code)
            except Exception as e:
                logger.warning("Error requesting %s: %s", model_name, e)

    return rule_based_analyst(
        user_message,
        pair,
        interval,
        current_price,
        indicators,
        sd_data,
        smc_data,
        signal_data,
        liquidity_data,
    )


def rule_based_analyst(
    user_message: str,
    pair: str,
    interval: str,
    current_price: float,
    indicators: dict[str, Any],
    sd_data: dict[str, Any] | None,
    smc_data: dict[str, Any] | None,
    sig_data: dict[str, Any] | None,
    liq_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic institutional rule-based analyst for offline or keyless operation."""
    sig = sig_data or {}
    demands = (sd_data or {}).get("demand", [])
    supplies = (sd_data or {}).get("supply", [])

    d_info = f"${demands[0]['distal']:,.2f} - ${demands[0]['proximal']:,.2f}" if demands else "None"
    s_info = (
        f"${supplies[0]['proximal']:,.2f} - ${supplies[0]['distal']:,.2f}" if supplies else "None"
    )
    rsi_val = indicators.get("rsi", "N/A")
    ema50 = indicators.get("ema_50", "N/A")
    structure = (smc_data or {}).get("summary", {}).get("current_structure", "NEUTRAL")

    d_score = demands[0].get("quality_score", 0) if demands else 0
    d_state = demands[0].get("lifecycle_state", "ACTIVE") if demands else "N/A"
    s_score = supplies[0].get("quality_score", 0) if supplies else 0
    l2_bias = (liq_data or {}).get("orderbook_l2", {}).get("bias", "BALANCED")
    sig_state = sig.get("state", "WAITING")
    sig_dir = sig.get("direction", "NEUTRAL")
    entry_p = sig.get("entry_price", current_price)
    stop_p = sig.get("stop_loss", 0)
    tp1 = sig.get("tp1", 0)
    tp2 = sig.get("tp2", 0)
    tp3 = sig.get("tp3", 0)
    inv_p = sig.get("invalidation_level", 0)

    response = f"""### 🤖 AlphaQuant Quantitative Intelligence: **{pair}** ({interval})
*Analysis context for inquiry: "{user_message}"*

#### **Executive Assessment**:
* **Current Validated Price**: **${current_price:,.2f}**
* **Market Structure**: `{structure}` | RSI: `{rsi_val}` | EMA 50: `{ema50}`
* **Deterministic Signal State**: `{sig_state}` ({sig_dir})
* **Setup Score**: **{sig.get("setup_score", 0)} / 100**
* **Primary Entry Model**: `{sig.get("primary_model", "MODEL_B_CONFIRMATION_SWEEP_CHOCH")}`

#### **Key Institutional Levels & Liquidity**:
* **Nearest Demand (Support)**: **{d_info}** ({d_score}/100 Quality, {d_state})
* **Nearest Supply (Resistance)**: **{s_info}** ({s_score}/100 Quality)
* **L2 Depth Imbalance**: {l2_bias}

#### **Precision Entry Models Breakdown**:
* **Model A (Aggressive Limit)**: Entry near **${entry_p:,.2f}** | SL **${stop_p:,.2f}**
* **Model B (Confirmation Sweep)**: Triggered upon liquidity sweep + CHoCH close confirmation.
* **Model C (Conservative FVG)**: Triggered upon displacement + Fair Value Gap retest.
* **Structural Targets**: TP1 **${tp1:,.2f}** | TP2 **${tp2:,.2f}** | TP3 **${tp3:,.2f}**
* **Invalidation Level**: **${inv_p:,.2f}**

#### **Risk & Execution Guidelines**:
* Risk strictly <= 1.5% per trade. Position size must not exceed the calculated stop distance.
"""

    return {
        "response": response,
        "model_used": "AlphaQuant Deterministic Quant Engine",
        "source": "deterministic_quant_engine",
        "engine": "deterministic_quant_engine",
    }
