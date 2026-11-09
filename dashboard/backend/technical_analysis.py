"""Technical indicators and Supply/Demand zone detection."""

import numpy as np
import pandas as pd
from .supply_demand import supply_demand_engine


def compute_indicators(candles: list[dict]) -> dict:
    """Computes technical indicators and returns chart-ready series & latest values."""
    if not candles or len(candles) < 30:
        return {"error": "Insufficient candle data"}

    df = pd.DataFrame(candles)
    # Ensure numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])

    # 1. EMAs
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

    # 2. RSI (14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    # 3. MACD (12, 26, 9)
    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema_12 - ema_26
    df["macdsignal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macdhist"] = df["macd"] - df["macdsignal"]

    # 4. Bollinger Bands (20, 2)
    sma_20 = df["close"].rolling(20).mean()
    std_20 = df["close"].rolling(20).std()
    df["bb_upper"] = sma_20 + (2 * std_20)
    df["bb_middle"] = sma_20
    df["bb_lower"] = sma_20 - (2 * std_20)

    # 5. ATR (14)
    high_low = df["high"] - df["low"]
    high_close_prev = (df["high"] - df["close"].shift(1)).abs()
    low_close_prev = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()

    # 6. ADX (14)
    up_move = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_series = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    plus_di = (
        100
        * pd.Series(plus_dm).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        / (tr_series + 1e-10)
    )
    minus_di = (
        100
        * pd.Series(minus_dm).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        / (tr_series + 1e-10)
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    df["adx"] = dx.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()

    # 7. Chaikin Money Flow (CMF)
    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / candle_range
    mf_vol = mfm * df["volume"]
    df["cmf"] = mf_vol.rolling(20).sum() / (df["volume"].rolling(20).sum() + 1e-10)

    # 8. Unified Supply & Demand Zones Detection (Single Source of Truth)
    zones = supply_demand_engine.detect_zones(candles)

    # Prepare lightweight-charts time-series format: [{"time": epoch, "value": float}]
    def to_series(col_name):
        valid = df[["time", col_name]].dropna()
        return [
            {"time": int(row["time"]), "value": round(float(row[col_name]), 4)}
            for _, row in valid.iterrows()
        ]

    latest_row = df.iloc[-1]

    return {
        "series": {
            "ema_20": to_series("ema_20"),
            "ema_50": to_series("ema_50"),
            "ema_200": to_series("ema_200"),
            "bb_upper": to_series("bb_upper"),
            "bb_middle": to_series("bb_middle"),
            "bb_lower": to_series("bb_lower"),
            "rsi": to_series("rsi"),
            "macd": to_series("macd"),
            "macdsignal": to_series("macdsignal"),
            "macdhist": [
                {
                    "time": int(r["time"]),
                    "value": round(float(r["macdhist"]), 4),
                    "color": "#10b981" if r["macdhist"] >= 0 else "#ef4444",
                }
                for _, r in df[["time", "macdhist"]].dropna().iterrows()
            ],
        },
        "latest": {
            "close": float(latest_row["close"]),
            "ema_20": float(latest_row["ema_20"]) if not np.isnan(latest_row["ema_20"]) else None,
            "ema_50": float(latest_row["ema_50"]) if not np.isnan(latest_row["ema_50"]) else None,
            "ema_200": float(latest_row["ema_200"])
            if not np.isnan(latest_row["ema_200"])
            else None,
            "rsi": round(float(latest_row["rsi"]), 2) if not np.isnan(latest_row["rsi"]) else None,
            "macd": round(float(latest_row["macd"]), 3)
            if not np.isnan(latest_row["macd"])
            else None,
            "macdsignal": round(float(latest_row["macdsignal"]), 3)
            if not np.isnan(latest_row["macdsignal"])
            else None,
            "macdhist": round(float(latest_row["macdhist"]), 3)
            if not np.isnan(latest_row["macdhist"])
            else None,
            "adx": round(float(latest_row["adx"]), 2) if not np.isnan(latest_row["adx"]) else None,
            "atr": round(float(latest_row["atr"]), 4) if not np.isnan(latest_row["atr"]) else None,
            "cmf": round(float(latest_row["cmf"]), 3) if not np.isnan(latest_row["cmf"]) else None,
            "bb_upper": float(latest_row["bb_upper"])
            if not np.isnan(latest_row["bb_upper"])
            else None,
            "bb_middle": float(latest_row["bb_middle"])
            if not np.isnan(latest_row["bb_middle"])
            else None,
            "bb_lower": float(latest_row["bb_lower"])
            if not np.isnan(latest_row["bb_lower"])
            else None,
        },
        "zones": zones,
    }


def detect_supply_demand_zones(
    df: pd.DataFrame, window: int = 3, min_atr_factor: float = 0.75
) -> dict:
    """Find recent unmitigated demand and supply zones via the unified SupplyDemandEngine."""
    if df is None or len(df) < 15:
        return {"demand": [], "supply": []}
    candles = df.to_dict(orient="records")
    return supply_demand_engine.detect_zones(candles)
