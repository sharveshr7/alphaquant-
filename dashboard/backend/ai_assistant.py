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


def _fmt_usd_opt(val: Any) -> str:
    """Safely formats optional USD prices with adaptive precision or N/A."""
    try:
        fv = float(val)
        if fv > 0:
            dec = 6 if fv < 0.01 else 4 if fv < 1 else 2
            return f"${fv:,.{dec}f}"
    except (TypeError, ValueError):
        pass
    return "N/A"

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
        f"{last_evt['type']} {last_evt['direction']} at {_fmt_usd_opt(last_evt.get('level'))}"
        if last_evt
        else "None"
    )
    regime = prem_disc.get("current_regime", "Neutral")
    eq_50 = _fmt_usd_opt(prem_disc.get("equilibrium_50"))
    bull_ob = summary.get("active_bullish_ob_count", 0)
    bear_ob = summary.get("active_bearish_ob_count", 0)
    fvg_count = summary.get("active_fvg_count", 0)
    struct_dir = summary.get("dominant_structure") or summary.get("current_structure") or "NEUTRAL"

    return (
        f"- Market Structure: {struct_dir}\n"
        f"- Latest Structure Break: {evt_str}\n"
        f"- Valuation Regime: {regime} (50% Equilibrium: {eq_50})\n"
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
        f"Entry={_fmt_usd_opt(mod_a.get('entry_price'))}, SL={_fmt_usd_opt(mod_a.get('stop_loss'))}, "
        f"TP2={_fmt_usd_opt(mod_a.get('tp2'))}, Score={mod_a.get('setup_score', 0)}\n"
        f"  * Model B (Confirmation): Status={mod_b.get('status', 'N/A')}, "
        f"Entry={_fmt_usd_opt(mod_b.get('entry_price'))}, SL={_fmt_usd_opt(mod_b.get('stop_loss'))}, "
        f"TP2={_fmt_usd_opt(mod_b.get('tp2'))}, Score={mod_b.get('setup_score', 0)}\n"
        f"  * Model C (Conservative FVG): Status={mod_c.get('status', 'N/A')}, "
        f"Entry={_fmt_usd_opt(mod_c.get('entry_price'))}, SL={_fmt_usd_opt(mod_c.get('stop_loss'))}, "
        f"TP2={_fmt_usd_opt(mod_c.get('tp2'))}, Score={mod_c.get('setup_score', 0)}\n"
        f"- Invalidation Level: {_fmt_usd_opt(signal_data.get('invalidation_level'))}\n"
    )


def _format_manual_zones_telemetry(manual_sd: dict[str, Any] | None) -> str:
    """Formats user-drawn manual Supply and Demand zones and overlap analysis."""
    if not manual_sd:
        return "No manual zones drawn by user."
    zones = manual_sd.get("zones", [])
    overlaps = manual_sd.get("overlaps", [])
    if not zones:
        return "No manual supply or demand zones drawn on current chart."

    lines = []
    lines.append(f"Total User Drawings Analyzed: {len(zones)}")
    for z in zones:
        lines.append(
            f"- Manual {z.get('zone_type')} [${z.get('distal', 0):,.2f} - ${z.get('proximal', 0):,.2f}]: "
            f"Classification: `{z.get('classification')}` (Score: {z.get('quality_score')}/100) | "
            f"Freshness: {z.get('evaluation_criteria', {}).get('freshness', 'N/A')} | "
            f"Mitigation: {z.get('mitigation_pct', 0)}% across {z.get('retest_count', 0)} touch(es) | "
            f"Displacement: {z.get('evaluation_criteria', {}).get('departure_strength', 'N/A')} | "
            f"Width: {z.get('height_in_atr', 0)}x ATR | Reason: {z.get('classification_reason')}"
        )
    if overlaps:
        lines.append(f"Overlap Intelligence ({len(overlaps)} overlapping relationship(s)):")
        for ov in overlaps:
            lines.append(
                f"  * Overlap between {ov.get('zone_id_1')} and {ov.get('zone_id_2')} ({ov.get('zone_type')}): "
                f"Range [${ov.get('overlap_price_range', [0, 0])[0]:,.2f} - ${ov.get('overlap_price_range', [0, 0])[1]:,.2f}]. "
                f"Stronger Zone: {ov.get('stronger_zone_id')}. Suggestion: {ov.get('consolidation_suggestion', {}).get('recommendation', 'Consolidate')}"
            )
    return "\n".join(lines)


def _format_11_tools_telemetry(tools: dict[str, Any] | None) -> str:
    """Formats the 11 independent analytical tools into telemetry."""
    if not tools:
        return "11-tool engine telemetry not provided."
    lines = []
    for k, v in tools.items():
        if isinstance(v, dict):
            name = v.get("name", k)
            bias = v.get("bias", "NEUTRAL")
            score = v.get("score", 50)
            ev = "; ".join(v.get("evidence", [])[:2])
            lines.append(f"- Tool [{name}]: Bias: `{bias}` | Score: {score}/100 | Evidence: {ev}")
    return "\n".join(lines)


def _format_future_scenarios_telemetry(scenarios: dict[str, Any] | None) -> str:
    """Formats Bullish, Bearish, and Neutral market scenarios."""
    if not scenarios:
        return "Scenario forecasting not available."
    lines = []
    bull = scenarios.get("bullish_scenario", {})
    if bull:
        lines.append(f"- Bullish Scenario: Targets {bull.get('potential_upside_areas')} | Confirm: {'; '.join(bull.get('conditions_required_for_confirmation', [])[:2])} | Invalidation: {bull.get('invalidation_conditions')}")
    bear = scenarios.get("bearish_scenario", {})
    if bear:
        lines.append(f"- Bearish Scenario: Targets {bear.get('potential_downside_areas')} | Confirm: {'; '.join(bear.get('conditions_required_for_confirmation', [])[:2])} | Invalidation: {bear.get('invalidation_conditions')}")
    neut = scenarios.get("neutral_scenario", {})
    if neut:
        lines.append(f"- Neutral Scenario: Range {neut.get('consolidation_range')} | Breakout conditions: {'; '.join(neut.get('conditions_that_could_cause_breakout', [])[:2])}")
    return "\n".join(lines)


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
    manual_sd: dict[str, Any] | None = None,
    tools: dict[str, Any] | None = None,
    scenarios: dict[str, Any] | None = None,
    combined_decision: dict[str, Any] | None = None,
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

    decision_str = ""
    if combined_decision:
        decision_str = (
            f"- Final Decision: `{combined_decision.get('decision', 'NO TRADE')}`\n"
            f"- Confluence Score: {combined_decision.get('overall_confluence_score', 0)}/100 | Risk: {combined_decision.get('risk_classification', 'MODERATE')}\n"
            f"- Bullish Confluences: {'; '.join(combined_decision.get('bullish_evidence', [])[:3]) or 'None'}\n"
            f"- Bearish Confluences: {'; '.join(combined_decision.get('bearish_evidence', [])[:3]) or 'None'}\n"
            f"- Conflicts Penalizing Score: {'; '.join(combined_decision.get('conflicting_evidence', [])[:3]) or 'None'}\n"
        )

    return f"""=== LIVE VALIDATED MARKET DATA ===
Symbol: {pair} | Current Timeframe: {interval} | Live Price: ${current_price:,.2f}

=== 1. USER MANUAL SUPPLY & DEMAND ZONES & OVERLAP DETECTION ===
{_format_manual_zones_telemetry(manual_sd)}

=== 2. ELEVEN INDEPENDENT MARKET ANALYSIS TOOLS ===
{_format_11_tools_telemetry(tools)}

=== 3. AUTO SUPPLY & DEMAND ZONES ===
{_format_sd_telemetry(sd_data)}

=== 4. SMART MONEY CONCEPTS (SMC) ===
{_format_smc_telemetry(smc_data)}

=== 5. ORDER FLOW & VOLUME PROFILE ===
{_format_orderflow_telemetry(orderflow_data)}

=== 6. LIQUIDITY INTELLIGENCE (L2 DEPTH & POOLS) ===
{_format_liquidity_telemetry(liquidity_data)}

=== 7. MULTI-TIMEFRAME 4-TIER PIPELINE ===
{mtf_str or "MTF data unavailable."}

=== 8. COMBINED DECISION & AUDIT ===
{decision_str or _format_signal_telemetry(signal_data)}

=== 9. FUTURE MARKET SCENARIOS (BULLISH / BEARISH / NEUTRAL) ===
{_format_future_scenarios_telemetry(scenarios)}

=== 10. PORTFOLIO & RISK STATE ===
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
    manual_sd: dict[str, Any] | None = None,
    tools: dict[str, Any] | None = None,
    scenarios: dict[str, Any] | None = None,
    combined_decision: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generates an institutional quantitative analysis response for the trader."""
    _ = (confluence, kwargs)
    api_key = config.gemini_api_key

    manual_sd = manual_sd or kwargs.get("manual_sd") or kwargs.get("manual_zone_analysis")
    tools = tools or kwargs.get("tools")
    scenarios = scenarios or kwargs.get("scenarios") or kwargs.get("future_scenarios")
    combined_decision = combined_decision or kwargs.get("combined_decision")

    full_context = _build_full_context(
        pair=pair,
        interval=interval,
        current_price=current_price,
        sd_data=sd_data,
        smc_data=smc_data,
        orderflow_data=orderflow_data,
        liquidity_data=liquidity_data,
        mtf_data=mtf_data,
        signal_data=signal_data,
        portfolio_data=portfolio_data,
        manual_sd=manual_sd,
        tools=tools,
        scenarios=scenarios,
        combined_decision=combined_decision,
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
        user_message=user_message,
        pair=pair,
        interval=interval,
        current_price=current_price,
        indicators=indicators,
        sd_data=sd_data,
        smc_data=smc_data,
        sig_data=signal_data,
        liq_data=liquidity_data,
        mtf_data=mtf_data,
        manual_sd=manual_sd,
        tools=tools,
        scenarios=scenarios,
        combined_decision=combined_decision,
    )


def rule_based_analyst(
    user_message: str,
    pair: str,
    interval: str,
    current_price: float,
    indicators: dict,
    sd_data: dict | None,
    smc_data: dict | None,
    sig_data: dict | None,
    liq_data: dict | None = None,
    mtf_data: dict | None = None,
    manual_sd: dict | None = None,
    tools: dict | None = None,
    scenarios: dict | None = None,
    combined_decision: dict | None = None,
) -> dict:
    """Deterministic institutional rule-based analyst grounded in live calculations."""
    q = user_message.lower()
    sig = sig_data or {}
    demands = (sd_data or {}).get("demand", [])
    supplies = (sd_data or {}).get("supply", [])

    d_info = f"${demands[0]['distal']:,.2f} - ${demands[0]['proximal']:,.2f}" if demands else "None"
    s_info = (
        f"${supplies[0]['proximal']:,.2f} - ${supplies[0]['distal']:,.2f}" if supplies else "None"
    )
    lat = indicators.get("latest", indicators) if isinstance(indicators, dict) else {}
    rsi_val = lat.get("rsi", "N/A")
    ema50 = lat.get("ema_50", "N/A")
    smc_sum = (smc_data or {}).get("summary", {})
    structure = smc_sum.get("dominant_structure") or smc_sum.get("current_structure") or smc_sum.get("current_trend", "NEUTRAL")

    d_score = demands[0].get("quality_score", 0) if demands else 0
    d_state = demands[0].get("lifecycle_state", "ACTIVE") if demands else "N/A"
    s_score = supplies[0].get("quality_score", 0) if supplies else 0
    l2_bias = (liq_data or {}).get("orderbook_l2", {}).get("bias", "BALANCED")
    sig_state = sig.get("state", "WAITING")
    sig_dir = sig.get("direction", "NEUTRAL")

    entry_txt = _fmt_usd_opt(sig.get("entry_price"))
    stop_txt = _fmt_usd_opt(sig.get("stop_loss"))
    tp1_txt = _fmt_usd_opt(sig.get("tp1"))
    tp2_txt = _fmt_usd_opt(sig.get("tp2"))
    tp3_txt = _fmt_usd_opt(sig.get("tp3"))
    inv_txt = _fmt_usd_opt(sig.get("invalidation_level"))

    manual_zones = (manual_sd or {}).get("zones", [])
    overlaps = (manual_sd or {}).get("overlaps", [])
    bull_scen = (scenarios or {}).get("bullish_scenario", {})
    bear_scen = (scenarios or {}).get("bearish_scenario", {})
    c_dec = combined_decision or {}

    # 1. Specialized Handler: Zone validation / freshness / mitigation inquiry
    if any(w in q for w in ["supply zone", "demand zone", "is my", "fresh", "mitigated", "reject", "break"]):
        zone_report = ""
        if manual_zones:
            z_lines = []
            for mz in manual_zones:
                crit = mz.get("evaluation_criteria", {})
                z_lines.append(
                    f"**Manual {mz.get('zone_type')} Zone** [${mz.get('distal', 0):,.2f} - ${mz.get('proximal', 0):,.2f}]:\n"
                    f"* **Classification**: `{mz.get('classification')}` (Quality Score: **{mz.get('quality_score')}/100**)\n"
                    f"* **Freshness & Mitigation**: {crit.get('freshness', 'N/A')} ({mz.get('mitigation_pct')}% penetration, {mz.get('retest_count')} touches)\n"
                    f"* **Departure Impulse**: {crit.get('departure_strength')} with {crit.get('volume_confirmation')}\n"
                    f"* **Structural Alignment**: {crit.get('higher_timeframe_alignment')} | {crit.get('bos_confirmation')}\n"
                    f"* **Assessment**: {mz.get('classification_reason')}\n"
                )
            zone_report = "\n".join(z_lines)
            if overlaps:
                zone_report += f"\n\n**⚠️ Overlapping Zones Alert**: {len(overlaps)} overlapping zone pair(s) detected. "
                for ov in overlaps:
                    zone_report += f"\n- Suggest consolidation into range [${ov.get('overlap_price_range', [0, 0])[0]:,.2f} - ${ov.get('overlap_price_range', [0, 0])[1]:,.2f}]. Primary institutional POI: {ov.get('stronger_zone_id')}."
        else:
            zone_report = (
                f"* **Nearest Auto Demand**: {d_info} (Score: {d_score}/100, State: `{d_state}`)\n"
                f"* **Nearest Auto Supply**: {s_info} (Score: {s_score}/100)\n"
                f"* *No manual zones drawn yet. Draw a green rectangle for Demand or red rectangle for Supply to receive instant quantitative verification.*"
            )

        response = f"""### 🛡️ Manual Supply & Demand Validation: **{pair}** ({interval})

{zone_report}

#### **Institutional Rejection vs Breakout Assessment**:
* **Current Market Price**: **${current_price:,.2f}**
* **Confirmation Required Before Entry**: Do not front-run limit orders in tested zones. Wait for 5m/15m Change of Character (CHoCH) wick rejection with expanding volume (>1.2x SMA).
* **Structural Invalidation**: Clean candle close beyond the distal boundary cancels the institutional setup.
"""
        return {"response": response, "model_used": "AlphaQuant Zone Validation Engine", "source": "zone_engine", "engine": "deterministic_quant_engine"}

    # 2. Specialized Handler: Invalidation inquiry
    if any(w in q for w in ["invalidate", "invalidation", "stop loss"]):
        response = f"""### 🛑 Structural Invalidation Analysis: **{pair}** ({interval})

* **Current Market Price**: **${current_price:,.2f}**
* **Active Direction**: `{sig_dir}` | Setup State: `{sig_state}`
* **Calculated Invalidation Level**: **{inv_txt}**
* **Structural Stop Loss**: **{stop_txt}**
* **Exact Invalidation Condition**:
  - For **LONG**: A confirmed 15m candle close below the Demand zone distal boundary (${demands[0]['distal']:,.2f} if demands else {stop_txt}) structurally breaks buyer defense and invalidates the setup.
  - For **SHORT**: A confirmed 15m candle close above the Supply zone distal boundary (${supplies[0]['distal']:,.2f} if supplies else {stop_txt}) indicates institutional absorption and invalidates the short.
"""
        return {"response": response, "model_used": "AlphaQuant Risk Engine", "source": "risk_engine", "engine": "deterministic_quant_engine"}

    # 3. Specialized Handler: Bearish / Bullish Alternative / Scenarios
    if any(w in q for w in ["bearish alternative", "bullish alternative", "scenario", "alternative"]):
        response = f"""### 🔮 Market Scenario Forecast: **{pair}** ({interval})

#### 🟢 **Bullish Scenario**:
* **Upside Target Areas**: {', '.join(bull_scen.get('potential_upside_areas', [])) or 'Nearest Supply Zone'}
* **Confirmation Triggers**: {'; '.join(bull_scen.get('conditions_required_for_confirmation', [])) or '15m Bullish CHoCH + Volume expansion'}
* **Liquidity Targets**: {', '.join(bull_scen.get('liquidity_targets', [])) or 'Buy-Side Liquidity Pool'}
* **Invalidation**: {bull_scen.get('invalidation_conditions', 'Break of local low')}

#### 🔴 **Bearish Alternative Scenario**:
* **Downside Target Areas**: {', '.join(bear_scen.get('potential_downside_areas', [])) or 'Nearest Demand Zone'}
* **Confirmation Triggers**: {'; '.join(bear_scen.get('conditions_required_for_confirmation', [])) or '15m Bearish CHoCH + Volume spike'}
* **Liquidity Targets**: {', '.join(bear_scen.get('liquidity_targets', [])) or 'Sell-Side Liquidity Pool'}
* **Invalidation**: {bear_scen.get('invalidation_conditions', 'Break of local high')}
"""
        return {"response": response, "model_used": "AlphaQuant Scenario Engine", "source": "scenario_engine", "engine": "deterministic_quant_engine"}

    # 4. Specialized Handler: Timeframe inquiry
    if any(w in q for w in ["which timeframe", "timeframe controls", "mtf", "controlling"]):
        mtf_bias = (mtf_data or {}).get("overall_bias", "MIXED")
        matrix = (mtf_data or {}).get("matrix", [])
        m_str = "\n".join([f"- **{m['timeframe'].upper()}**: Bias `{m['bias']}` | RSI {m.get('rsi')} | Trend vs EMA50" for m in matrix]) if matrix else "Multi-timeframe data syncing."
        response = f"""### ⏱️ Multi-Timeframe Structural Hierarchy: **{pair}**

* **Controlling Higher Timeframe (Macro Bias)**: **4H & 1D** govern institutional trend and major liquidity pools. Current 4H/1D bias: **`{mtf_bias}`**.
* **Intermediate Framework**: **1H & 15M** establish swing structure (BOS/CHoCH) and operational Supply & Demand zones.
* **Tactical Execution Timeframe**: **5M & 15M** provide entry triggers (micro-sweeps and order block retests).

#### **Alignment Matrix Across Closed Bars**:
{m_str}
"""
        return {"response": response, "model_used": "AlphaQuant MTF Engine", "source": "mtf_engine", "engine": "deterministic_quant_engine"}

    # 5. Specialized Handler: Why NO TRADE?
    if any(w in q for w in ["why no trade", "no trade", "recommend no trade", "why no"]):
        reasons_against = c_dec.get("conflicting_evidence", []) or ["Price is in mid-range equilibrium between key zones"]
        reasons_formatted = "\n".join([f"- ⚠️ {r}" for r in reasons_against])
        response = f"""### 🛡️ Why System Recommends NO TRADE: **{pair}** ({interval})

* **Combined Confluence Score**: **{c_dec.get('overall_confluence_score', sig.get('setup_score', 0))}/100** (Threshold for trade generation is >= 70)
* **Risk Classification**: `{c_dec.get('risk_classification', 'MODERATE_RISK')}`

#### **Conflicting Evidence & Missing Confirmations**:
{reasons_formatted}

#### **What Must Happen Next Before a Trade Is Validated**:
- Wait for price to cleanly arrive at Demand (${d_info}) or Supply (${s_info}).
- Require confirmed Lower-Timeframe Change of Character (CHoCH) on expanding volume (>1.2x).
- Avoid gambling in mid-range equilibrium chop. Capital preservation is priority #1.
"""
        return {"response": response, "model_used": "AlphaQuant Confluence Engine", "source": "confluence_engine", "engine": "deterministic_quant_engine"}

    # Default Comprehensive Response
    response = f"""### 🤖 AlphaQuant Quantitative Intelligence: **{pair}** ({interval})
*Analysis context for inquiry: \"{user_message}\"*

#### **Executive Assessment**:
* **Current Validated Price**: **${current_price:,.2f}**
* **Market Structure**: `{structure}` | RSI: `{rsi_val}` | EMA 50: `{ema50}`
* **Deterministic Signal State**: `{sig_state}` ({sig_dir})
* **Overall Confluence Score**: **{c_dec.get('overall_confluence_score', sig.get('setup_score', 0))} / 100**
* **Risk Classification**: `{c_dec.get('risk_classification', 'MODERATE')}`

#### **Key Institutional Levels & Liquidity**:
* **Nearest Demand (Support)**: **{d_info}** ({d_score}/100 Quality, {d_state})
* **Nearest Supply (Resistance)**: **{s_info}** ({s_score}/100 Quality)
* **L2 Depth Imbalance**: {l2_bias}

#### **Precision Entry Models Breakdown**:
* **Model A (Aggressive Limit)**: Entry **{entry_txt}** | SL **{stop_txt}**
* **Model B (Confirmation Sweep)**: Triggered upon liquidity sweep + CHoCH close confirmation.
* **Model C (Conservative FVG)**: Triggered upon displacement + Fair Value Gap retest.
* **Structural Targets**: TP1 **{tp1_txt}** | TP2 **{tp2_txt}** | TP3 **{tp3_txt}**
* **Invalidation Level**: **{inv_txt}**

#### **Risk & Execution Guidelines**:
* Risk strictly <= 1.5% per trade. Position sizing must match the distance to structural stop loss.
"""

    return {
        "response": response,
        "model_used": "AlphaQuant Deterministic Quant Engine",
        "source": "deterministic_quant_engine",
        "engine": "deterministic_quant_engine",
    }
