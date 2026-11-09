"""Quantitative Supply & Demand Zone Detection Engine - Phase 2.

Features:
- Multi-timeframe Zone Mapping (4H, 1H, 15M, 5M)
- Impulsive ATR Departure Strength measurement
- Precise Zone Mitigation Percentage (0% to 100%)
- Full Zone Lifecycle tracking: FORMING -> FRESH -> PARTIALLY_TESTED -> DEEPLY_MITIGATED -> INVALIDATED
- Zone Overlap & Nested Multi-Timeframe Confluence
- Transparent 0-100 Zone Quality Ranking
- Non-repainting: uses strictly closed candles.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import numpy as np
import pandas as pd


@dataclass
class SupplyDemandZone:
    id: str
    timeframe: str
    type: str  # 'DEMAND' or 'SUPPLY'
    proximal: float  # Demand top / Supply bottom
    distal: float  # Demand bottom / Supply top
    base_candle_time: int
    confirmation_candle_time: int
    displacement_factor: float  # multiple of ATR
    base_candles_count: int
    volume_ratio: float  # departure volume / 20 SMA volume
    retest_count: int
    mitigation_pct: float  # 0% = virgin fresh, 100% = breached
    lifecycle_state: str  # 'FRESH', 'PARTIALLY_TESTED', 'DEEPLY_MITIGATED', 'INVALIDATED'
    is_fresh: bool
    is_mitigated: bool
    is_invalidated: bool
    quality_score: int  # 0 to 100
    distance_pct: float  # % distance from current close to proximal boundary
    has_htf_confluence: bool  # Overlaps with higher timeframe zone


class SupplyDemandEngine:
    def __init__(
        self,
        min_displacement_atr: float = 1.2,
        max_base_candles: int = 4,
        atr_period: int = 14,
    ) -> None:
        self.min_displacement_atr = min_displacement_atr
        self.max_base_candles = max_base_candles
        self.atr_period = atr_period

    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close_prev = (df["high"] - df["close"].shift(1)).abs()
        low_close_prev = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / self.atr_period, min_periods=self.atr_period, adjust=False).mean()

    def detect_zones(self, candles: list[dict], timeframe: str = "15m") -> dict:
        """Processes OHLCV candles and returns active, fresh, and historical S/D zones."""
        if not candles or len(candles) < 25:
            return {"demand": [], "supply": [], "summary": {}}

        df = pd.DataFrame(candles)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])

        df["atr"] = self.calculate_atr(df)
        df["vol_sma"] = df["volume"].rolling(20).mean().fillna(df["volume"])
        df["body_size"] = (df["close"] - df["open"]).abs()
        df["candle_range"] = (df["high"] - df["low"]).replace(0, np.nan)

        current_price = float(df["close"].iloc[-1])
        n_candles = len(df)
        zones: list[SupplyDemandZone] = []

        # Scan backwards through candles starting after startup warmup
        for i in range(15, n_candles - 1):
            atr = float(df["atr"].iloc[i])
            if atr <= 0 or np.isnan(atr):
                atr = current_price * 0.01

            # --- 1. DEMAND ZONE DETECTION ---
            is_bullish_departure = (
                df["close"].iloc[i] > df["open"].iloc[i]
                and (df["close"].iloc[i] - df["open"].iloc[i]) >= atr * self.min_displacement_atr
            )

            if is_bullish_departure:
                base_idx = i - 1
                base_count = 1
                while base_count < self.max_base_candles and base_idx > 0:
                    body = df["body_size"].iloc[base_idx]
                    rng = df["candle_range"].iloc[base_idx]
                    if (body / rng) < 0.60:
                        base_idx -= 1
                        base_count += 1
                    else:
                        break

                base_slice = df.iloc[base_idx : i]
                proximal = float(base_slice["high"].max())
                distal = float(base_slice["low"].min())
                disp_ratio = float((df["close"].iloc[i] - df["open"].iloc[i]) / atr)
                vol_ratio = float(df["volume"].iloc[i] / (df["vol_sma"].iloc[i] + 1e-10))

                subsequent = df.iloc[i + 1 :]
                retests = 0
                lowest_low = proximal
                invalidated = False

                for _, row in subsequent.iterrows():
                    l_val = float(row["low"])
                    c_val = float(row["close"])
                    if l_val < lowest_low:
                        lowest_low = l_val
                    if l_val <= proximal and l_val >= distal:
                        retests += 1
                    if c_val < distal:
                        invalidated = True

                # Precise mitigation percentage
                zone_height = max(proximal - distal, 1e-4)
                penetration = max(0.0, proximal - lowest_low)
                mitigation_pct = round(min(100.0, (penetration / zone_height) * 100), 1)

                if invalidated or mitigation_pct >= 100.0:
                    lifecycle = "INVALIDATED"
                elif mitigation_pct == 0.0:
                    lifecycle = "FRESH"
                elif mitigation_pct < 50.0:
                    lifecycle = "PARTIALLY_TESTED"
                else:
                    lifecycle = "DEEPLY_MITIGATED"

                if lifecycle != "INVALIDATED":
                    score = 40
                    if disp_ratio >= 2.2:
                        score += 25
                    elif disp_ratio >= 1.5:
                        score += 15
                    else:
                        score += 5

                    if vol_ratio >= 1.5:
                        score += 15
                    elif vol_ratio >= 1.1:
                        score += 10

                    if base_count <= 2:
                        score += 10
                    if retests == 0:
                        score += 10
                    elif retests == 1:
                        score += 5

                    # Deduction for deep mitigation
                    score -= int(mitigation_pct * 0.25)
                    score = max(10, min(100, score))

                    dist_pct = round(((current_price - proximal) / current_price) * 100, 2)
                    zone_id = f"dem_{timeframe}_{int(df['time'].iloc[base_idx])}"

                    zones.append(
                        SupplyDemandZone(
                            id=zone_id,
                            timeframe=timeframe,
                            type="DEMAND",
                            proximal=round(proximal, 4),
                            distal=round(distal, 4),
                            base_candle_time=int(df["time"].iloc[base_idx]),
                            confirmation_candle_time=int(df["time"].iloc[i]),
                            displacement_factor=round(disp_ratio, 2),
                            base_candles_count=base_count,
                            volume_ratio=round(vol_ratio, 2),
                            retest_count=retests,
                            mitigation_pct=mitigation_pct,
                            lifecycle_state=lifecycle,
                            is_fresh=(lifecycle == "FRESH"),
                            is_mitigated=(mitigation_pct > 30.0),
                            is_invalidated=False,
                            quality_score=score,
                            distance_pct=dist_pct,
                            has_htf_confluence=False,
                        )
                    )

            # --- 2. SUPPLY ZONE DETECTION ---
            is_bearish_departure = (
                df["close"].iloc[i] < df["open"].iloc[i]
                and (df["open"].iloc[i] - df["close"].iloc[i]) >= atr * self.min_displacement_atr
            )

            if is_bearish_departure:
                base_idx = i - 1
                base_count = 1
                while base_count < self.max_base_candles and base_idx > 0:
                    body = df["body_size"].iloc[base_idx]
                    rng = df["candle_range"].iloc[base_idx]
                    if (body / rng) < 0.60:
                        base_idx -= 1
                        base_count += 1
                    else:
                        break

                base_slice = df.iloc[base_idx : i]
                proximal = float(base_slice["low"].min())
                distal = float(base_slice["high"].max())
                disp_ratio = float((df["open"].iloc[i] - df["close"].iloc[i]) / atr)
                vol_ratio = float(df["volume"].iloc[i] / (df["vol_sma"].iloc[i] + 1e-10))

                subsequent = df.iloc[i + 1 :]
                retests = 0
                highest_high = proximal
                invalidated = False

                for _, row in subsequent.iterrows():
                    h_val = float(row["high"])
                    c_val = float(row["close"])
                    if h_val > highest_high:
                        highest_high = h_val
                    if h_val >= proximal and h_val <= distal:
                        retests += 1
                    if c_val > distal:
                        invalidated = True

                zone_height = max(distal - proximal, 1e-4)
                penetration = max(0.0, highest_high - proximal)
                mitigation_pct = round(min(100.0, (penetration / zone_height) * 100), 1)

                if invalidated or mitigation_pct >= 100.0:
                    lifecycle = "INVALIDATED"
                elif mitigation_pct == 0.0:
                    lifecycle = "FRESH"
                elif mitigation_pct < 50.0:
                    lifecycle = "PARTIALLY_TESTED"
                else:
                    lifecycle = "DEEPLY_MITIGATED"

                if lifecycle != "INVALIDATED":
                    score = 40
                    if disp_ratio >= 2.2:
                        score += 25
                    elif disp_ratio >= 1.5:
                        score += 15
                    else:
                        score += 5

                    if vol_ratio >= 1.5:
                        score += 15
                    elif vol_ratio >= 1.1:
                        score += 10

                    if base_count <= 2:
                        score += 10
                    if retests == 0:
                        score += 10
                    elif retests == 1:
                        score += 5

                    score -= int(mitigation_pct * 0.25)
                    score = max(10, min(100, score))

                    dist_pct = round(((proximal - current_price) / current_price) * 100, 2)
                    zone_id = f"sup_{timeframe}_{int(df['time'].iloc[base_idx])}"

                    zones.append(
                        SupplyDemandZone(
                            id=zone_id,
                            timeframe=timeframe,
                            type="SUPPLY",
                            proximal=round(proximal, 4),
                            distal=round(distal, 4),
                            base_candle_time=int(df["time"].iloc[base_idx]),
                            confirmation_candle_time=int(df["time"].iloc[i]),
                            displacement_factor=round(disp_ratio, 2),
                            base_candles_count=base_count,
                            volume_ratio=round(vol_ratio, 2),
                            retest_count=retests,
                            mitigation_pct=mitigation_pct,
                            lifecycle_state=lifecycle,
                            is_fresh=(lifecycle == "FRESH"),
                            is_mitigated=(mitigation_pct > 30.0),
                            is_invalidated=False,
                            quality_score=score,
                            distance_pct=dist_pct,
                            has_htf_confluence=False,
                        )
                    )

        # Separate demand & supply, sort by proximity to price
        demand_zones = [z for z in zones if z.type == "DEMAND"]
        supply_zones = [z for z in zones if z.type == "SUPPLY"]

        demand_zones.sort(key=lambda z: abs(current_price - z.proximal))
        supply_zones.sort(key=lambda z: abs(z.proximal - current_price))

        nearest_demand = asdict(demand_zones[0]) if demand_zones else None
        nearest_supply = asdict(supply_zones[0]) if supply_zones else None

        return {
            "demand": [asdict(z) for z in demand_zones[:6]],
            "supply": [asdict(z) for z in supply_zones[:6]],
            "summary": {
                "active_demand_count": len(demand_zones),
                "active_supply_count": len(supply_zones),
                "fresh_demand_count": sum(1 for z in demand_zones if z.is_fresh),
                "fresh_supply_count": sum(1 for z in supply_zones if z.is_fresh),
                "nearest_demand_zone": nearest_demand,
                "nearest_supply_zone": nearest_supply,
                "in_demand_zone": bool(
                    nearest_demand and nearest_demand["distal"] <= current_price <= nearest_demand["proximal"] * 1.002
                ),
                "in_supply_zone": bool(
                    nearest_supply and nearest_supply["proximal"] * 0.998 <= current_price <= nearest_supply["distal"]
                ),
            },
        }

    def detect_multi_timeframe_zones(self, candles_map: dict[str, list[dict]]) -> dict:
        """Runs S/D detection across multiple timeframes and finds nested confluence zones."""
        tf_results = {}
        for tf, candles in candles_map.items():
            tf_results[tf] = self.detect_zones(candles, timeframe=tf)

        # Check for nested confluence (e.g. 15m zone inside 1h or 4h zone)
        htf_demands = tf_results.get("1h", {}).get("demand", []) + tf_results.get("4h", {}).get("demand", [])
        htf_supplies = tf_results.get("1h", {}).get("supply", []) + tf_results.get("4h", {}).get("supply", [])

        # Annotate 15m zones
        m15_demands = tf_results.get("15m", {}).get("demand", [])
        for d in m15_demands:
            for h in htf_demands:
                # Overlap test: zone intervals intersect
                if max(d["distal"], h["distal"]) <= min(d["proximal"], h["proximal"]):
                    d["has_htf_confluence"] = True
                    d["quality_score"] = min(100, d["quality_score"] + 15)
                    break

        m15_supplies = tf_results.get("15m", {}).get("supply", [])
        for s in m15_supplies:
            for h in htf_supplies:
                if max(s["proximal"], h["proximal"]) <= min(s["distal"], h["distal"]):
                    s["has_htf_confluence"] = True
                    s["quality_score"] = min(100, s["quality_score"] + 15)
                    break

        return {
            "timeframes": tf_results,
            "confluence_detected": any(d.get("has_htf_confluence") for d in m15_demands) or any(s.get("has_htf_confluence") for s in m15_supplies),
        }


    def evaluate_manual_zone(
        self,
        candles: list[dict],
        zone_type: str,
        val1: float,
        val2: float,
        timeframe: str = "15m",
    ) -> dict:
        """Evaluates a user-drawn manual Supply or Demand rectangle zone."""
        if not candles or len(candles) < 15:
            return {"error": "Need at least 15 candles for zone evaluation"}

        current_price = float(candles[-1]["close"])
        df = pd.DataFrame(candles)
        atr_series = self.calculate_atr(df)
        current_atr = float(atr_series.iloc[-1]) if not atr_series.empty else (current_price * 0.005)

        # Standardize proximal and distal based on zone type
        z_type = zone_type.upper().strip()
        if z_type == "DEMAND":
            proximal = max(val1, val2)  # Top edge of demand zone (first entry)
            distal = min(val1, val2)    # Bottom edge of demand zone (structural invalidation)
        else:
            z_type = "SUPPLY"
            proximal = min(val1, val2)  # Bottom edge of supply zone (first entry)
            distal = max(val1, val2)    # Top edge of supply zone (structural invalidation)

        zone_height = abs(proximal - distal)
        zone_height_pct = round((zone_height / max(current_price, 1e-4)) * 100, 2)
        height_in_atr = round(zone_height / max(current_atr, 1e-4), 2)

        # Retest and mitigation analysis
        retests = 0
        deepest_mitigation = 0.0
        is_breached = False

        # Scan recent candles to check zone interaction
        for c in candles[-50:]:
            high = float(c["high"])
            low = float(c["low"])
            close = float(c["close"])

            if z_type == "DEMAND":
                if low <= proximal and high >= distal:
                    retests += 1
                    penetration = (proximal - low) / max(zone_height, 1e-4)
                    deepest_mitigation = max(deepest_mitigation, penetration * 100.0)
                if close < distal:
                    is_breached = True
            else:  # SUPPLY
                if high >= proximal and low <= distal:
                    retests += 1
                    penetration = (high - proximal) / max(zone_height, 1e-4)
                    deepest_mitigation = max(deepest_mitigation, penetration * 100.0)
                if close > distal:
                    is_breached = True

        deepest_mitigation = round(min(deepest_mitigation, 100.0), 1)

        # Lifecycle State
        if is_breached or deepest_mitigation >= 100.0:
            lifecycle = "INVALIDATED"
            score = 15
            classification = "INVALID/MITIGATED ZONE"
        elif deepest_mitigation >= 65.0:
            lifecycle = "DEEPLY_MITIGATED"
            score = 45
            classification = "WEAK/COUNTER-TREND ZONE"
        elif retests > 0:
            lifecycle = "PARTIALLY_TESTED"
            score = 75
            classification = "NEEDS CONFIRMATION"
        else:
            lifecycle = "FRESH"
            score = 92
            classification = "VALID HIGH-PROBABILITY ZONE"

        # Distance from current price
        if z_type == "DEMAND":
            distance_pct = round(((current_price - proximal) / current_price) * 100, 2)
            equilibrium = round((proximal + distal) / 2.0, 2)
            structural_sl = round(distal - (zone_height * 0.20), 2)
            risk = max(current_price - structural_sl, zone_height)
            tp1 = round(current_price + (risk * 1.5), 2)
            tp2 = round(current_price + (risk * 2.5), 2)
            invalidation = round(distal, 2)
        else:
            distance_pct = round(((proximal - current_price) / current_price) * 100, 2)
            equilibrium = round((proximal + distal) / 2.0, 2)
            structural_sl = round(distal + (zone_height * 0.20), 2)
            risk = max(structural_sl - current_price, zone_height)
            tp1 = round(current_price - (risk * 1.5), 2)
            tp2 = round(current_price - (risk * 2.5), 2)
            invalidation = round(distal, 2)

        return {
            "zone_type": z_type,
            "timeframe": timeframe,
            "proximal": round(proximal, 2),
            "distal": round(distal, 2),
            "equilibrium": equilibrium,
            "zone_height": round(zone_height, 2),
            "zone_height_pct": zone_height_pct,
            "height_in_atr": height_in_atr,
            "current_price": round(current_price, 2),
            "distance_pct": distance_pct,
            "retest_count": retests,
            "mitigation_pct": deepest_mitigation,
            "lifecycle_state": lifecycle,
            "quality_score": score,
            "classification": classification,
            "recommended_entry": round(proximal if lifecycle == "FRESH" else equilibrium, 2),
            "structural_sl": structural_sl,
            "tp1": tp1,
            "tp2": tp2,
            "invalidation_level": invalidation,
        }


supply_demand_engine = SupplyDemandEngine()
