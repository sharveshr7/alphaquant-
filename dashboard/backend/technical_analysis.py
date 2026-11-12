"""Technical indicator library and Supply/Demand zone integration for AlphaQuant AI Pro.

Zero external TA library dependencies — all calculations implemented with pandas and numpy.
Follows Binance USDⓈ-M Futures standards and lightweight-charts formatting.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .supply_demand import supply_demand_engine

logger = logging.getLogger("technical_analysis")


def _get_price_precision(sample_price: float) -> int:
    """Determine appropriate decimal places for price values based on magnitude."""
    if sample_price <= 0:
        return 2
    if sample_price < 0.0001:
        return 8
    elif sample_price < 0.01:
        return 6
    elif sample_price < 1.0:
        return 4
    elif sample_price < 100.0:
        return 3
    else:
        return 2


def compute_indicators(
    candles: list[dict[str, Any]],
    include_series: bool = True,
    include_zones: bool = True,
) -> dict[str, Any]:
    """Computes technical indicators and returns chart-ready series, latest values, trend summary, and zones.

    Args:
        candles: List of candlestick dicts containing 'time', 'open', 'high', 'low', 'close', 'volume'.

    Returns:
        dict: {
            "series": {indicator_name: [{"time": ..., "value": ...}, ...]},
            "latest": {indicator_name: value},
            "trend": {trend_direction, trend_strength, ema_alignment, momentum},
            "zones": {demand: [...], supply: [...]}
        }
    """
    if not candles or len(candles) < 30:
        return {
            "error": "Insufficient candle data",
            "series": {},
            "latest": {},
            "trend": {
                "trend_direction": "SIDEWAYS",
                "trend_strength": "WEAK",
                "ema_alignment": "MIXED",
                "momentum": "NEUTRAL",
            },
            "zones": {"demand": [], "supply": []},
        }

    df = pd.DataFrame(candles)

    # Convert core numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Clean rows missing critical price data
    df = df.dropna(subset=["close", "high", "low"]).reset_index(drop=True)
    if len(df) < 30:
        return {
            "error": "Insufficient candle data",
            "series": {},
            "latest": {},
            "trend": {
                "trend_direction": "SIDEWAYS",
                "trend_strength": "WEAK",
                "ema_alignment": "MIXED",
                "momentum": "NEUTRAL",
            },
            "zones": {"demand": [], "supply": []},
        }

    if "volume" not in df.columns or df["volume"].isnull().all():
        df["volume"] = 1.0
    else:
        df["volume"] = df["volume"].fillna(0.0)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    last_close = float(close.iloc[-1])
    price_dec = _get_price_precision(last_close)
    osc_dec = 1

    # ---------------------------------------------------------
    # 1. EMAs: 9, 20, 50, 100, 200
    # ---------------------------------------------------------
    df["ema_9"] = close.ewm(span=9, adjust=False).mean()
    df["ema_20"] = close.ewm(span=20, adjust=False).mean()
    df["ema_50"] = close.ewm(span=50, adjust=False).mean()
    df["ema_100"] = close.ewm(span=100, adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    # ---------------------------------------------------------
    # 2. SMAs: 20, 50, 200
    # ---------------------------------------------------------
    df["sma_20"] = close.rolling(20).mean()
    df["sma_50"] = close.rolling(50).mean()
    df["sma_200"] = close.rolling(200).mean()

    # ---------------------------------------------------------
    # 3. VWAP (Session-reset daily at 00:00 UTC)
    # ---------------------------------------------------------
    tp = (high + low + close) / 3.0
    t_first = float(df["time"].iloc[0]) if "time" in df.columns else 0.0
    time_unit = "ms" if t_first > 1e11 else "s"
    utc_dates = pd.to_datetime(df["time"], unit=time_unit, utc=True).dt.date
    cum_vol = volume.groupby(utc_dates).cumsum()
    cum_tp_vol = (tp * volume).groupby(utc_dates).cumsum()
    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    df["vwap"] = vwap.ffill().fillna(tp)

    # ---------------------------------------------------------
    # 4. Bollinger Bands (20, 2σ)
    # ---------------------------------------------------------
    sma_20 = df["sma_20"]
    std_20 = close.rolling(20).std()
    df["bb_upper"] = sma_20 + (2.0 * std_20)
    df["bb_middle"] = sma_20
    df["bb_lower"] = sma_20 - (2.0 * std_20)

    # ---------------------------------------------------------
    # 5. RSI (14, Wilder's smoothing alpha=1/14)
    # ---------------------------------------------------------
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    # ---------------------------------------------------------
    # 6. Stochastic RSI (14, 14, 3, 3)
    # ---------------------------------------------------------
    rsi_min = df["rsi"].rolling(14).min()
    rsi_max = df["rsi"].rolling(14).max()
    rsi_range = (rsi_max - rsi_min).replace(0, np.nan)
    stoch_rsi = (100.0 * (df["rsi"] - rsi_min) / rsi_range).fillna(50.0)
    df["stoch_rsi_k"] = stoch_rsi.rolling(3).mean()
    df["stoch_rsi_d"] = df["stoch_rsi_k"].rolling(3).mean()

    # ---------------------------------------------------------
    # 7. MACD (12, 26, 9)
    # ---------------------------------------------------------
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema_12 - ema_26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    # Aliases for backward compatibility
    df["macdsignal"] = df["macd_signal"]
    df["macdhist"] = df["macd_hist"]

    # ---------------------------------------------------------
    # 8. ATR (14) True Range
    # ---------------------------------------------------------
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # ---------------------------------------------------------
    # 9. ADX (14) — Smoothed Directional Movement
    # ---------------------------------------------------------
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_smooth = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_dm_smooth = pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean()
    minus_dm_smooth = pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean()
    df["plus_di"] = 100.0 * (plus_dm_smooth / (tr_smooth + 1e-10))
    df["minus_di"] = 100.0 * (minus_dm_smooth / (tr_smooth + 1e-10))
    dx = 100.0 * (df["plus_di"] - df["minus_di"]).abs() / (df["plus_di"] + df["minus_di"] + 1e-10)
    df["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()

    # ---------------------------------------------------------
    # 10. Supertrend (10, 3.0) — Trailing Bands & Direction
    # ---------------------------------------------------------
    hl2 = (high + low) / 2.0
    atr_10 = tr.ewm(alpha=1 / 10, adjust=False).mean()
    basic_ub = hl2 + (3.0 * atr_10)
    basic_lb = hl2 - (3.0 * atr_10)

    n_rows = len(df)
    final_ub = np.zeros(n_rows)
    final_lb = np.zeros(n_rows)
    st_val = np.zeros(n_rows)
    st_dir = np.zeros(n_rows)

    c_arr = close.values
    hl2_arr = hl2.values
    b_ub_arr = basic_ub.values
    b_lb_arr = basic_lb.values

    for i in range(n_rows):
        if i == 0:
            final_ub[0] = b_ub_arr[0]
            final_lb[0] = b_lb_arr[0]
            st_dir[0] = 1 if c_arr[0] >= hl2_arr[0] else -1
            st_val[0] = final_lb[0] if st_dir[0] == 1 else final_ub[0]
            continue

        c_prev = c_arr[i - 1]
        c_curr = c_arr[i]
        b_ub = b_ub_arr[i]
        b_lb = b_lb_arr[i]

        # Trailing upper band
        if b_ub < final_ub[i - 1] or c_prev > final_ub[i - 1]:
            final_ub[i] = b_ub
        else:
            final_ub[i] = final_ub[i - 1]

        # Trailing lower band
        if b_lb > final_lb[i - 1] or c_prev < final_lb[i - 1]:
            final_lb[i] = b_lb
        else:
            final_lb[i] = final_lb[i - 1]

        # Direction switch logic
        prev_dir = st_dir[i - 1]
        if prev_dir == 1:
            if c_curr < final_lb[i]:
                st_dir[i] = -1
                st_val[i] = final_ub[i]
            else:
                st_dir[i] = 1
                st_val[i] = final_lb[i]
        else:
            if c_curr > final_ub[i]:
                st_dir[i] = 1
                st_val[i] = final_lb[i]
            else:
                st_dir[i] = -1
                st_val[i] = final_ub[i]

    df["supertrend"] = st_val
    df["supertrend_direction"] = ["BULLISH" if d == 1 else "BEARISH" for d in st_dir]

    # ---------------------------------------------------------
    # 11. Ichimoku (9, 26, 52)
    # ---------------------------------------------------------
    df["tenkan"] = (high.rolling(9, min_periods=1).max() + low.rolling(9, min_periods=1).min()) / 2.0
    df["kijun"] = (high.rolling(26, min_periods=1).max() + low.rolling(26, min_periods=1).min()) / 2.0
    raw_span_a = (df["tenkan"] + df["kijun"]) / 2.0
    raw_span_b = (high.rolling(52, min_periods=1).max() + low.rolling(52, min_periods=1).min()) / 2.0
    # Senkou Span A & B shifted 26 periods to align the cloud over corresponding candles
    df["senkou_a"] = raw_span_a.shift(26)
    df["senkou_b"] = raw_span_b.shift(26)
    # Chikou Span: Close price plotted 26 periods back
    df["chikou"] = close.shift(-26)

    # ---------------------------------------------------------
    # 12. CCI (20) — Commodity Channel Index
    # ---------------------------------------------------------
    sma_tp = tp.rolling(20).mean()
    mad = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    df["cci"] = (tp - sma_tp) / (0.015 * mad + 1e-10)

    # ---------------------------------------------------------
    # 13. OBV — On-Balance Volume
    # ---------------------------------------------------------
    obv_dir = np.sign(close.diff()).fillna(0.0)
    df["obv"] = (obv_dir * volume).cumsum()

    # ---------------------------------------------------------
    # 14. MFI (14) — Money Flow Index
    # ---------------------------------------------------------
    rmf = tp * volume
    tp_diff = tp.diff()
    pmf = np.where(tp_diff > 0, rmf, 0.0)
    nmf = np.where(tp_diff < 0, rmf, 0.0)
    pmf_sum = pd.Series(pmf, index=df.index).rolling(14).sum()
    nmf_sum = pd.Series(nmf, index=df.index).rolling(14).sum()
    total_flow = pmf_sum + nmf_sum
    df["mfi"] = np.where(total_flow > 0, 100.0 * (pmf_sum / total_flow), 50.0)

    # ---------------------------------------------------------
    # 15. Volume SMA (20)
    # ---------------------------------------------------------
    df["volume_sma_20"] = volume.rolling(20).mean()

    # ---------------------------------------------------------
    # 16. Keltner Channels (20, 2)
    # ---------------------------------------------------------
    kc_middle = close.ewm(span=20, adjust=False).mean()
    kc_atr = tr.ewm(span=20, adjust=False).mean()
    df["kc_upper"] = kc_middle + (2.0 * kc_atr)
    df["kc_middle"] = kc_middle
    df["kc_lower"] = kc_middle - (2.0 * kc_atr)

    # ---------------------------------------------------------
    # 17. Chaikin Money Flow (CMF) (Backward compatibility)
    # ---------------------------------------------------------
    candle_range = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / candle_range
    mf_vol = mfm * volume
    df["cmf"] = mf_vol.rolling(20).sum() / (volume.rolling(20).sum() + 1e-10)

    # ---------------------------------------------------------
    # 18. Supply & Demand Zones
    # ---------------------------------------------------------
    if include_zones:
        try:
            zones = supply_demand_engine.detect_zones(candles)
        except Exception as e:
            logger.warning(f"Error detecting supply/demand zones: {e}")
            zones = {"demand": [], "supply": []}
    else:
        zones = {"demand": [], "supply": []}

    # ---------------------------------------------------------
    # Fast Series Formatting (drop NaNs, round appropriately)
    # ---------------------------------------------------------
    series: dict[str, Any] = {}
    if include_series:
        def to_series(col_name: str, precision: int = 2) -> list[dict[str, Any]]:
            valid = df[["time", col_name]].dropna()
            times = valid["time"].astype(int).tolist()
            vals = [round(float(v), precision) for v in valid[col_name].tolist()]
            return [{"time": t, "value": v} for t, v in zip(times, vals)]

        series = {
            "ema_9": to_series("ema_9", price_dec),
            "ema_20": to_series("ema_20", price_dec),
            "ema_50": to_series("ema_50", price_dec),
            "ema_100": to_series("ema_100", price_dec),
            "ema_200": to_series("ema_200", price_dec),
            "sma_20": to_series("sma_20", price_dec),
            "sma_50": to_series("sma_50", price_dec),
            "sma_200": to_series("sma_200", price_dec),
            "vwap": to_series("vwap", price_dec),
            "bb_upper": to_series("bb_upper", price_dec),
            "bb_middle": to_series("bb_middle", price_dec),
            "bb_lower": to_series("bb_lower", price_dec),
            "rsi": to_series("rsi", osc_dec),
            "stoch_rsi_k": to_series("stoch_rsi_k", osc_dec),
            "stoch_rsi_d": to_series("stoch_rsi_d", osc_dec),
            "macd": to_series("macd", price_dec),
            "macd_signal": to_series("macd_signal", price_dec),
            "macd_hist": [
                {
                    "time": int(t),
                    "value": round(float(v), price_dec),
                    "color": "#10b981" if v >= 0 else "#ef4444",
                }
                for t, v in zip(df["time"], df["macd_hist"])
                if not np.isnan(v)
            ],
            "macdsignal": to_series("macd_signal", price_dec),
            "macdhist": [
                {
                    "time": int(t),
                    "value": round(float(v), price_dec),
                    "color": "#10b981" if v >= 0 else "#ef4444",
                }
                for t, v in zip(df["time"], df["macd_hist"])
                if not np.isnan(v)
            ],
            "atr": to_series("atr", price_dec),
            "adx": to_series("adx", osc_dec),
            "plus_di": to_series("plus_di", osc_dec),
            "minus_di": to_series("minus_di", osc_dec),
            "supertrend": [
                {
                    "time": int(t),
                    "value": round(float(v), price_dec),
                    "color": "#10b981" if d == "BULLISH" else "#ef4444",
                }
                for t, v, d in zip(df["time"], df["supertrend"], df["supertrend_direction"])
                if not np.isnan(v)
            ],
            "tenkan": to_series("tenkan", price_dec),
            "kijun": to_series("kijun", price_dec),
            "senkou_a": to_series("senkou_a", price_dec),
            "senkou_b": to_series("senkou_b", price_dec),
            "chikou": to_series("chikou", price_dec),
            "cci": to_series("cci", osc_dec),
            "obv": to_series("obv", 1),
            "mfi": to_series("mfi", osc_dec),
            "volume_sma_20": to_series("volume_sma_20", 1),
            "kc_upper": to_series("kc_upper", price_dec),
            "kc_middle": to_series("kc_middle", price_dec),
            "kc_lower": to_series("kc_lower", price_dec),
            "cmf": to_series("cmf", 3),
        }

    # ---------------------------------------------------------
    # Latest Indicators Values
    # ---------------------------------------------------------
    latest_row = df.iloc[-1]

    def get_latest_num(col_name: str, precision: int = 2) -> float | None:
        if col_name not in df.columns:
            return None
        val = latest_row[col_name]
        if pd.isna(val) or np.isnan(val) or np.isinf(val):
            return None
        return round(float(val), precision)

    # Cloud boundary fallbacks for short series (< 78 candles)
    latest_senkou_a = get_latest_num("senkou_a", price_dec)
    if latest_senkou_a is None and len(raw_span_a) > 0:
        val = raw_span_a.iloc[-1]
        if not pd.isna(val):
            latest_senkou_a = round(float(val), price_dec)

    latest_senkou_b = get_latest_num("senkou_b", price_dec)
    if latest_senkou_b is None and len(raw_span_b) > 0:
        val = raw_span_b.iloc[-1]
        if not pd.isna(val):
            latest_senkou_b = round(float(val), price_dec)

    # Chikou span latest value corresponds to current close
    latest_chikou = round(float(latest_row["close"]), price_dec)

    latest = {
        "close": round(float(latest_row["close"]), price_dec),
        "ema_9": get_latest_num("ema_9", price_dec),
        "ema_20": get_latest_num("ema_20", price_dec),
        "ema_50": get_latest_num("ema_50", price_dec),
        "ema_100": get_latest_num("ema_100", price_dec),
        "ema_200": get_latest_num("ema_200", price_dec),
        "sma_20": get_latest_num("sma_20", price_dec),
        "sma_50": get_latest_num("sma_50", price_dec),
        "sma_200": get_latest_num("sma_200", price_dec),
        "vwap": get_latest_num("vwap", price_dec),
        "bb_upper": get_latest_num("bb_upper", price_dec),
        "bb_middle": get_latest_num("bb_middle", price_dec),
        "bb_lower": get_latest_num("bb_lower", price_dec),
        "rsi": get_latest_num("rsi", osc_dec),
        "stoch_rsi_k": get_latest_num("stoch_rsi_k", osc_dec),
        "stoch_rsi_d": get_latest_num("stoch_rsi_d", osc_dec),
        "macd": get_latest_num("macd", price_dec),
        "macd_signal": get_latest_num("macd_signal", price_dec),
        "macd_hist": get_latest_num("macd_hist", price_dec),
        "macdsignal": get_latest_num("macd_signal", price_dec),
        "macdhist": get_latest_num("macd_hist", price_dec),
        "atr": get_latest_num("atr", price_dec),
        "adx": get_latest_num("adx", osc_dec),
        "plus_di": get_latest_num("plus_di", osc_dec),
        "minus_di": get_latest_num("minus_di", osc_dec),
        "supertrend": get_latest_num("supertrend", price_dec),
        "supertrend_direction": str(latest_row["supertrend_direction"]),
        "tenkan": get_latest_num("tenkan", price_dec),
        "kijun": get_latest_num("kijun", price_dec),
        "senkou_a": latest_senkou_a,
        "senkou_b": latest_senkou_b,
        "chikou": latest_chikou,
        "cci": get_latest_num("cci", osc_dec),
        "obv": get_latest_num("obv", 1),
        "mfi": get_latest_num("mfi", osc_dec),
        "volume_sma_20": get_latest_num("volume_sma_20", 1),
        "kc_upper": get_latest_num("kc_upper", price_dec),
        "kc_middle": get_latest_num("kc_middle", price_dec),
        "kc_lower": get_latest_num("kc_lower", price_dec),
        "cmf": get_latest_num("cmf", 3),
    }

    trend = compute_trend_summary(latest)

    return {
        "series": series,
        "latest": latest,
        "trend": trend,
        "zones": zones,
    }


def compute_trend_summary(latest: dict[str, Any]) -> dict[str, str]:
    """Evaluates multi-indicator trend direction, strength, alignment, and momentum.

    Args:
        latest: Dictionary of latest indicator values.

    Returns:
        dict: {
            "trend_direction": "BULLISH" | "BEARISH" | "SIDEWAYS",
            "trend_strength": "STRONG" | "MODERATE" | "WEAK",
            "ema_alignment": "BULLISH" | "BEARISH" | "MIXED",
            "momentum": "BULLISH" | "BEARISH" | "NEUTRAL"
        }
    """
    if not latest:
        return {
            "trend_direction": "SIDEWAYS",
            "trend_strength": "WEAK",
            "ema_alignment": "MIXED",
            "momentum": "NEUTRAL",
        }

    # 1. EMA Alignment: Evaluates ordered stack of EMAs (9, 20, 50, 100, 200)
    e9 = latest.get("ema_9")
    e20 = latest.get("ema_20")
    e50 = latest.get("ema_50")
    e100 = latest.get("ema_100")
    e200 = latest.get("ema_200")

    avail_emas = [e for e in [e9, e20, e50, e100, e200] if e is not None and not np.isnan(e)]
    if len(avail_emas) >= 3:
        is_bullish = all(avail_emas[i] > avail_emas[i + 1] for i in range(len(avail_emas) - 1))
        is_bearish = all(avail_emas[i] < avail_emas[i + 1] for i in range(len(avail_emas) - 1))
        if is_bullish:
            ema_alignment = "BULLISH"
        elif is_bearish:
            ema_alignment = "BEARISH"
        else:
            ema_alignment = "MIXED"
    else:
        ema_alignment = "MIXED"

    # 2. Trend Strength from ADX
    adx = latest.get("adx")
    if adx is not None and not np.isnan(adx):
        if adx >= 25.0:
            trend_strength = "STRONG"
        elif adx >= 20.0:
            trend_strength = "MODERATE"
        else:
            trend_strength = "WEAK"
    else:
        trend_strength = "WEAK"

    # 3. Momentum from RSI and MACD Histogram Sign
    rsi = latest.get("rsi")
    macd_hist = latest.get("macd_hist")
    if macd_hist is None:
        macd_hist = latest.get("macdhist")

    rsi_bull = rsi is not None and not np.isnan(rsi) and rsi > 52.0
    rsi_bear = rsi is not None and not np.isnan(rsi) and rsi < 48.0
    macd_bull = macd_hist is not None and not np.isnan(macd_hist) and macd_hist > 0.0
    macd_bear = macd_hist is not None and not np.isnan(macd_hist) and macd_hist < 0.0

    if rsi_bull and macd_bull:
        momentum = "BULLISH"
    elif rsi_bear and macd_bear:
        momentum = "BEARISH"
    else:
        momentum = "NEUTRAL"

    # 4. Overall Trend Direction
    score = 0
    if ema_alignment == "BULLISH":
        score += 2
    elif ema_alignment == "BEARISH":
        score -= 2

    if momentum == "BULLISH":
        score += 1
    elif momentum == "BEARISH":
        score -= 1

    supertrend_dir = latest.get("supertrend_direction")
    if supertrend_dir == "BULLISH":
        score += 1
    elif supertrend_dir == "BEARISH":
        score -= 1

    close = latest.get("close")
    if close is not None and e50 is not None and not np.isnan(close) and not np.isnan(e50):
        if close > e50:
            score += 1
        elif close < e50:
            score -= 1

    if trend_strength == "WEAK" and abs(score) <= 1:
        trend_direction = "SIDEWAYS"
    elif score >= 2:
        trend_direction = "BULLISH"
    elif score <= -2:
        trend_direction = "BEARISH"
    else:
        trend_direction = "SIDEWAYS"

    return {
        "trend_direction": trend_direction,
        "trend_strength": trend_strength,
        "ema_alignment": ema_alignment,
        "momentum": momentum,
    }


def detect_supply_demand_zones(
    df: pd.DataFrame, window: int = 3, min_atr_factor: float = 0.75
) -> dict[str, Any]:
    """Find recent unmitigated demand and supply zones via the unified SupplyDemandEngine."""
    if df is None or len(df) < 15:
        return {"demand": [], "supply": []}
    candles = df.to_dict(orient="records")
    return supply_demand_engine.detect_zones(candles)
