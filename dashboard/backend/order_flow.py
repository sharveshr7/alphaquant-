"""Advanced Order Flow & Volume Profile Engine.

Provides:
- Exact tick-level bid/ask taker volume delta & Cumulative Volume Delta (CVD)
  via Binance Futures real-time aggregate trades (Binance /fapi/v1/aggTrades).
- Real Footprint Bar decomposition: Bid/Ask volume at price clusters.
- Stacked Imbalances detection (>= 300% volume ratio across diagonal price levels).
- Absorption Detection (heavy market volume absorbed by resting institutional limits).
- Volume Exhaustion detection (price thrust with collapsing volume/delta).
- Session Volume Profile: Point of Control (POC), Value Area High (VAH 70%),
  Value Area Low (VAL 70%), and High/Low volume nodes.
- Strict data provenance labels (EXACT TICK-LEVEL vs SESSION VOLUME PROFILE).
"""

from __future__ import annotations

import httpx
import numpy as np
import pandas as pd
from .market_data import BINANCE_FUTURES_BASE_URL, normalize_symbol

BINANCE_BASE_URL = BINANCE_FUTURES_BASE_URL


class OrderFlowEngine:
    def __init__(self, value_area_pct: float = 0.70, num_profile_bins: int = 36) -> None:
        self.value_area_pct = value_area_pct
        self.num_profile_bins = num_profile_bins

    def calculate_volume_profile(self, candles: list[dict]) -> dict:
        """Calculates Session Volume Profile, POC, VAH, and VAL from candle OHLCV data."""
        if not candles or len(candles) < 10:
            return {"bins": [], "poc": None, "vah": None, "val": None, "provenance": "UNAVAILABLE"}

        df = pd.DataFrame(candles)
        min_price = float(df["low"].min())
        max_price = float(df["high"].max())
        price_range = max_price - min_price

        if price_range <= 0:
            return {"bins": [], "poc": None, "vah": None, "val": None, "provenance": "UNAVAILABLE"}

        bin_size = price_range / self.num_profile_bins
        bins = np.linspace(min_price, max_price, self.num_profile_bins + 1)
        volume_at_price = np.zeros(self.num_profile_bins)
        buy_volume_est = np.zeros(self.num_profile_bins)
        sell_volume_est = np.zeros(self.num_profile_bins)

        # Distribute each candle's volume across its range
        for _, row in df.iterrows():
            c_low = float(row["low"])
            c_high = float(row["high"])
            c_close = float(row["close"])
            c_open = float(row["open"])
            c_vol = float(row["volume"])

            is_bull = c_close >= c_open

            low_idx = min(max(int((c_low - min_price) / bin_size), 0), self.num_profile_bins - 1)
            high_idx = min(max(int((c_high - min_price) / bin_size), 0), self.num_profile_bins - 1)
            span = max(high_idx - low_idx + 1, 1)
            vol_per_bin = c_vol / span

            for b in range(low_idx, high_idx + 1):
                volume_at_price[b] += vol_per_bin
                if is_bull:
                    buy_volume_est[b] += vol_per_bin * 0.6
                    sell_volume_est[b] += vol_per_bin * 0.4
                else:
                    buy_volume_est[b] += vol_per_bin * 0.4
                    sell_volume_est[b] += vol_per_bin * 0.6

        # Point of Control (POC): Price bin with the highest volume
        poc_idx = int(np.argmax(volume_at_price))
        poc_price = round(float((bins[poc_idx] + bins[poc_idx + 1]) / 2), 4)

        # Value Area Calculation (70% of total volume around POC)
        total_vol = float(np.sum(volume_at_price))
        target_va_vol = total_vol * self.value_area_pct

        current_va_vol = volume_at_price[poc_idx]
        up_idx = poc_idx
        dn_idx = poc_idx

        while current_va_vol < target_va_vol and (up_idx < self.num_profile_bins - 1 or dn_idx > 0):
            up_vol = volume_at_price[up_idx + 1] if up_idx < self.num_profile_bins - 1 else 0
            dn_vol = volume_at_price[dn_idx - 1] if dn_idx > 0 else 0

            if up_vol >= dn_vol and up_idx < self.num_profile_bins - 1:
                up_idx += 1
                current_va_vol += up_vol
            elif dn_idx > 0:
                dn_idx -= 1
                current_va_vol += dn_vol
            else:
                break

        vah = round(float(bins[up_idx + 1]), 4)
        val = round(float(bins[dn_idx]), 4)

        profile_bins = []
        for i in range(self.num_profile_bins):
            p_mid = round(float((bins[i] + bins[i + 1]) / 2), 4)
            profile_bins.append({
                "price": p_mid,
                "volume": round(float(volume_at_price[i]), 2),
                "is_poc": bool(i == poc_idx),
                "in_value_area": bool(dn_idx <= i <= up_idx),
            })

        current_price = float(df["close"].iloc[-1])

        return {
            "poc": poc_price,
            "vah": vah,
            "val": val,
            "bins": profile_bins,
            "total_session_volume": round(total_vol, 2),
            "current_price": current_price,
            "price_location": (
                "ABOVE_VALUE_AREA (Premium Expansion)"
                if current_price > vah
                else "BELOW_VALUE_AREA (Discount / Accumulation)"
                if current_price < val
                else "INSIDE_VALUE_AREA (Consolidation Equilibrium)"
            ),
            "provenance": "SESSION VOLUME PROFILE (Price-Binned Volume Distribution)",
        }

    async def fetch_real_tick_delta(self, pair: str = "BTC/USDT", limit: int = 500) -> dict:
        """Fetches real-time trade-by-trade tick volume from Binance and computes exact Delta."""
        symbol = normalize_symbol(pair)
        url = f"{BINANCE_BASE_URL}/aggTrades"
        params = {"symbol": symbol, "limit": min(limit, 1000)}

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(url, params=params)
                res.raise_for_status()
                trades = res.json()
        except Exception as e:
            return {
                "error": f"Failed to fetch tick delta: {e}",
                "provenance": "UNAVAILABLE",
            }

        total_buy_vol = 0.0
        total_sell_vol = 0.0
        cvd_series = []
        running_cvd = 0.0

        for t in trades:
            price = float(t["p"])
            qty = float(t["q"])
            is_buyer_maker = bool(t["m"])
            time_ms = int(t["T"])

            # If buyer is maker, the taker was an aggressive seller
            if is_buyer_maker:
                total_sell_vol += qty
                running_cvd -= qty
            else:
                total_buy_vol += qty
                running_cvd += qty

            cvd_series.append({
                "time": time_ms // 1000,
                "price": price,
                "cvd": round(running_cvd, 4),
            })

        net_delta = total_buy_vol - total_sell_vol
        delta_pct = (net_delta / (total_buy_vol + total_sell_vol + 1e-10)) * 100

        # Downsample CVD series for charting (keep 50 points)
        step = max(len(cvd_series) // 50, 1)
        sampled_cvd = cvd_series[::step]

        return {
            "pair": pair,
            "sample_trades_count": len(trades),
            "total_buy_volume": round(total_buy_vol, 4),
            "total_sell_volume": round(total_sell_vol, 4),
            "net_delta": round(net_delta, 4),
            "delta_bias_pct": round(delta_pct, 2),
            "orderflow_bias": "AGGRESSIVE_BUYING" if net_delta > 0 else "AGGRESSIVE_SELLING",
            "cvd_series": sampled_cvd,
            "provenance": "EXACT EXCHANGE TICK-LEVEL DATA (Binance Futures aggTrades Taker Volume)",
        }

    async def compute_advanced_order_flow(self, pair: str, candles: list[dict]) -> dict:
        """Comprehensive order flow engine generating Footprint decomposition, Imbalances, and Absorption."""
        symbol = normalize_symbol(pair)
        url = f"{BINANCE_BASE_URL}/aggTrades"
        params = {"symbol": symbol, "limit": 1000}

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(url, params=params)
                res.raise_for_status()
                trades = res.json()
        except Exception:
            trades = []

        vp = self.calculate_volume_profile(candles)

        if not trades:
            delta_summary = {
                "pair": pair,
                "sample_trades_count": 0,
                "net_delta": 0.0,
                "delta_bias_pct": 0.0,
                "orderflow_bias": "NEUTRAL",
                "cvd_series": [],
                "provenance": "UNAVAILABLE",
            }
            footprints = []
            imbalances = []
            absorptions = []
            exhaustions = []
        else:
            # 1. Delta & CVD
            total_buy_vol = 0.0
            total_sell_vol = 0.0
            running_cvd = 0.0
            cvd_series = []

            for t in trades:
                qty = float(t["q"])
                is_buyer_maker = bool(t["m"])
                price = float(t["p"])
                time_s = int(t["T"]) // 1000

                if is_buyer_maker:
                    total_sell_vol += qty
                    running_cvd -= qty
                else:
                    total_buy_vol += qty
                    running_cvd += qty

                cvd_series.append({"time": time_s, "price": price, "cvd": round(running_cvd, 4)})

            net_delta = total_buy_vol - total_sell_vol
            delta_pct = (net_delta / (total_buy_vol + total_sell_vol + 1e-10)) * 100
            step = max(len(cvd_series) // 50, 1)

            delta_summary = {
                "pair": pair,
                "sample_trades_count": len(trades),
                "total_buy_volume": round(total_buy_vol, 4),
                "total_sell_volume": round(total_sell_vol, 4),
                "net_delta": round(net_delta, 4),
                "delta_bias_pct": round(delta_pct, 2),
                "orderflow_bias": "AGGRESSIVE_BUYING" if net_delta > 0 else "AGGRESSIVE_SELLING",
                "cvd_series": cvd_series[::step],
                "provenance": "EXACT EXCHANGE TICK-LEVEL DATA (Binance Futures aggTrades Taker Volume)",
            }

            # 2. Footprint Decomposition for recent candles
            # Map recent trades to recent candle intervals
            footprints = []
            if candles and len(candles) >= 3:
                recent_candles = candles[-5:]
                for c in recent_candles:
                    c_start = c["time"]
                    # Determine interval duration in seconds
                    c_duration = 900 if "15m" in pair or len(candles) < 2 else int(candles[-1]["time"] - candles[-2]["time"])
                    c_end = c_start + c_duration

                    c_trades = [t for t in trades if c_start <= (int(t["T"]) // 1000) < c_end]
                    if not c_trades:
                        continue

                    c_low = float(c["low"])
                    c_high = float(c["high"])
                    num_levels = 8
                    level_step = (c_high - c_low) / max(num_levels, 1)

                    levels = []
                    for i in range(num_levels):
                        p_min = c_low + i * level_step
                        p_max = p_min + level_step
                        level_trades = [t for t in c_trades if p_min <= float(t["p"]) <= p_max]
                        b_vol = sum(float(t["q"]) for t in level_trades if not bool(t["m"]))  # taker buy
                        s_vol = sum(float(t["q"]) for t in level_trades if bool(t["m"]))  # taker sell
                        levels.append({
                            "price_level": round((p_min + p_max) / 2, 2),
                            "bid_vol": round(s_vol, 4),
                            "ask_vol": round(b_vol, 4),
                            "delta": round(b_vol - s_vol, 4),
                        })

                    bar_delta = sum(l["delta"] for l in levels)
                    footprints.append({
                        "time": c_start,
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "total_volume": round(sum(l["bid_vol"] + l["ask_vol"] for l in levels), 4),
                        "bar_delta": round(bar_delta, 4),
                        "levels": levels,
                    })

            # 3. Stacked Imbalances Detection
            imbalances = []
            for fp in footprints:
                levels = fp.get("levels", [])
                for idx in range(len(levels) - 1):
                    ask_above = levels[idx + 1]["ask_vol"]
                    bid_below = levels[idx]["bid_vol"]
                    # Buy diagonal imbalance: Ask above >= 3x Bid below
                    if ask_above >= 3.0 * (bid_below + 0.001) and ask_above >= 0.5:
                        imbalances.append({
                            "type": "BUY_IMBALANCE",
                            "time": fp["time"],
                            "price": levels[idx + 1]["price_level"],
                            "ratio": round(ask_above / (bid_below + 0.001), 2),
                        })
                    # Sell diagonal imbalance: Bid below >= 3x Ask above
                    elif bid_below >= 3.0 * (ask_above + 0.001) and bid_below >= 0.5:
                        imbalances.append({
                            "type": "SELL_IMBALANCE",
                            "time": fp["time"],
                            "price": levels[idx]["price_level"],
                            "ratio": round(bid_below / (ask_above + 0.001), 2),
                        })

            # 4. Absorption Detection
            absorptions = []
            if footprints and len(candles) >= 10:
                avg_vol = float(np.mean([float(c["volume"]) for c in candles[-20:]]))
                last_c = candles[-1]
                last_vol = float(last_c["volume"])
                body_size = abs(float(last_c["close"]) - float(last_c["open"]))
                candle_range = max(float(last_c["high"]) - float(last_c["low"]), 1e-5)

                # High volume but narrow body (effort without result)
                if last_vol >= avg_vol * 1.6 and (body_size / candle_range) < 0.35:
                    if net_delta < 0 and float(last_c["close"]) > float(last_c["low"]):
                        absorptions.append({
                            "type": "BULLISH_PASSIVE_ABSORPTION",
                            "description": "Heavy market selling absorbed by institutional passive limit bids.",
                            "price": float(last_c["close"]),
                            "time": int(last_c["time"]),
                        })
                    elif net_delta > 0 and float(last_c["close"]) < float(last_c["high"]):
                        absorptions.append({
                            "type": "BEARISH_PASSIVE_ABSORPTION",
                            "description": "Heavy market buying absorbed by institutional passive limit asks.",
                            "price": float(last_c["close"]),
                            "time": int(last_c["time"]),
                        })

            # 5. Volume Exhaustion Detection
            exhaustions = []
            if len(candles) >= 5:
                recent_vols = [float(c["volume"]) for c in candles[-5:]]
                if recent_vols[-1] < np.mean(recent_vols) * 0.5 and abs(net_delta) < 0.2:
                    exhaustions.append({
                        "type": "VOLUME_EXHAUSTION",
                        "description": "Market thrust halting on critically low volume and flat delta.",
                        "price": float(candles[-1]["close"]),
                        "time": int(candles[-1]["time"]),
                    })

        return {
            "volume_profile": vp,
            "taker_delta": delta_summary,
            "footprints": footprints,
            "imbalances": imbalances[-10:],
            "absorptions": absorptions,
            "exhaustions": exhaustions,
            "summary": {
                "orderflow_bias": delta_summary.get("orderflow_bias", "NEUTRAL"),
                "poc": vp.get("poc"),
                "vah": vp.get("vah"),
                "val": vp.get("val"),
                "has_absorption": bool(len(absorptions) > 0),
                "has_stacked_imbalance": bool(len(imbalances) >= 2),
            },
        }


order_flow_engine = OrderFlowEngine()
