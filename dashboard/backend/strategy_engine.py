"""Supply/Demand Confluence Strategy evaluation engine."""

from __future__ import annotations

from typing import Any

from .technical_analysis import compute_indicators


def _build_checklist(
    price: float,
    latest: dict[str, Any],
    zones: dict[str, Any],
    candles_1h: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Constructs the 8 institutional confluence criteria items."""
    # Criteria 1: Demand Zone Proximity
    demands = zones.get("demand", [])
    nearest_demand = demands[0] if demands else None
    in_demand_zone = False
    dist_pct = 0.0
    zone_bot = "None"
    if nearest_demand:
        dist_pct = float(nearest_demand.get("distance_pct", 0.0))
        distal_val = nearest_demand.get("distal", nearest_demand.get("zone_bottom", "None"))
        zone_bot = str(distal_val)
        in_demand_zone = bool(-0.5 <= dist_pct <= 1.8)

    # Criteria 2 & 3: Trend & EMA structure (with 1H HTF confirmation when available)
    ema_50 = float(latest.get("ema_50") or price)
    ema_200 = float(latest.get("ema_200") or (price * 0.98))
    trend_bullish = bool(ema_50 > ema_200)

    if candles_1h and len(candles_1h) >= 30:
        ind_1h = compute_indicators(candles_1h)
        latest_1h = ind_1h.get("latest", {})
        h_ema50 = latest_1h.get("ema_50")
        h_ema200 = latest_1h.get("ema_200")
        if h_ema50 is not None and h_ema200 is not None:
            trend_bullish = bool(float(h_ema50) > float(h_ema200))

    price_above_ema50 = bool(price > ema_50)

    # Criteria 4: RSI between 45 and 68
    rsi = float(latest.get("rsi") or 50.0)
    rsi_valid = bool(45.0 <= rsi <= 68.0)

    # Criteria 5: ADX > 20
    adx = float(latest.get("adx") or 0.0)
    adx_valid = bool(adx >= 20.0)

    # Criteria 6: Price above Bollinger Middle
    bb_middle = float(latest.get("bb_middle") or price)
    bb_valid = bool(price >= bb_middle)

    # Criteria 7: MACD Momentum Reversal
    macdhist = float(latest.get("macdhist") or 0.0)
    macd_valid = bool(macdhist >= 0.0)

    # Criteria 8: Money Flow / Volume
    cmf = float(latest.get("cmf") or 0.0)
    cmf_valid = bool(cmf > 0.02)

    return [
        {
            "id": "demand_zone",
            "title": "Demand Zone Reaction",
            "passed": in_demand_zone,
            "detail": f"Nearest zone: {zone_bot} ({dist_pct}% away)",
        },
        {
            "id": "trend_ema",
            "title": "EMA 50 > EMA 200 (HTF Trend)",
            "passed": trend_bullish,
            "detail": f"EMA50: {round(ema_50, 2)} vs EMA200: {round(ema_200, 2)}",
        },
        {
            "id": "price_ema50",
            "title": "Price above 50 EMA",
            "passed": price_above_ema50,
            "detail": f"Price: {price} > EMA50: {round(ema_50, 2)}",
        },
        {
            "id": "rsi_range",
            "title": "RSI Filter (45 - 68)",
            "passed": rsi_valid,
            "detail": f"Current RSI: {rsi}",
        },
        {
            "id": "adx_trend",
            "title": "ADX Trend Strength (>= 20)",
            "passed": adx_valid,
            "detail": f"Current ADX: {adx}",
        },
        {
            "id": "bb_mid",
            "title": "Price >= Bollinger Midline",
            "passed": bb_valid,
            "detail": f"Price: {price} vs Midline: {round(bb_middle, 2)}",
        },
        {
            "id": "macd_momentum",
            "title": "MACD Histogram >= 0",
            "passed": macd_valid,
            "detail": f"Histogram: {macdhist}",
        },
        {
            "id": "cmf_flow",
            "title": "Chaikin Money Flow > 0.02",
            "passed": cmf_valid,
            "detail": f"CMF: {cmf}",
        },
    ]


def _derive_signal(score: int) -> tuple[str, str, str]:
    """Returns signal name, action description, and badge color based on score."""
    if score >= 7:
        return "STRONG_BUY", "High Probability Long Setup", "emerald"
    if score >= 5:
        return "MODERATE_BUY", "Watch for Trigger / Scalp Long", "blue"
    if score <= 2:
        return "BEARISH_OR_NEUTRAL", "No Long Signal (Wait for Demand test)", "rose"
    return "NEUTRAL", "Consolidation / Range Bound", "amber"


def evaluate_confluence(
    candles_15m: list[dict[str, Any]],
    candles_1h: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluates the 8 confluence criteria from SupplyDemandConfluence.py."""
    if not candles_15m or len(candles_15m) < 30:
        return {"error": "Need more 15m candles"}

    ind_15m = compute_indicators(candles_15m)
    if "error" in ind_15m:
        return ind_15m

    latest = ind_15m.get("latest", {})
    price = float(latest.get("close", 0.0))
    zones = ind_15m.get("zones", {})

    checklist = _build_checklist(price, latest, zones, candles_1h)
    score = int(sum(1 for item in checklist if item["passed"]))
    signal, action, badge_color = _derive_signal(score)

    stop_loss = round(price * (1 - 0.035), 2)
    risk_amount = price - stop_loss
    tp1 = round(price + (risk_amount * 1.5), 2)
    tp2 = round(price + (risk_amount * 2.5), 2)

    return {
        "confluence_score": score,
        "max_score": 8,
        "signal": signal,
        "action": action,
        "badge_color": badge_color,
        "current_price": price,
        "checklist": checklist,
        "trade_setup": {
            "entry": price,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "risk_reward_ratio": "1 : 2.0",
        },
        "indicators": latest,
        "zones": zones,
    }
