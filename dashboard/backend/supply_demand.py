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
        htf_candles: list[dict] | None = None,
        zone_id: str | None = None,
        overlapping_zones_count: int = 0,
    ) -> dict:
        """Evaluates a user-drawn manual Supply or Demand rectangle zone across all 12 institutional criteria."""
        if not candles or len(candles) < 15:
            return {
                "error": "Insufficient candle data for zone evaluation (minimum 15 required)",
                "classification": "INSUFFICIENT DATA",
                "classification_reason": "Less than 15 historical candles available to analyze zone origin and mitigation.",
            }

        df = pd.DataFrame(candles)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])

        current_price = float(df["close"].iloc[-1])
        atr_series = self.calculate_atr(df)
        current_atr = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else (current_price * 0.005)
        df["vol_sma"] = df["volume"].rolling(20).mean().fillna(df["volume"])

        # Standardize proximal and distal based on zone type
        z_type = zone_type.upper().strip()
        if z_type == "DEMAND":
            proximal = max(val1, val2)  # Top edge of demand zone (first entry)
            distal = min(val1, val2)    # Bottom edge of demand zone (structural invalidation)
        else:
            z_type = "SUPPLY"
            proximal = min(val1, val2)  # Bottom edge of supply zone (first entry)
            distal = max(val1, val2)    # Top edge of supply zone (structural invalidation)

        zone_height = max(abs(proximal - distal), 1e-6)
        zone_height_pct = round((zone_height / max(current_price, 1e-4)) * 100, 2)
        height_in_atr = round(zone_height / max(current_atr, 1e-4), 2)

        # 1. Zone Origin & Base Candle Structure Search
        # Locate the historical candle window where price was inside this zone
        in_zone_mask = (df["low"] <= max(proximal, distal)) & (df["high"] >= min(proximal, distal))
        zone_indices = df.index[in_zone_mask].tolist()

        origin_idx = zone_indices[0] if zone_indices else max(0, len(df) - 30)
        # Look for the last consolidation base before departure
        base_candidates = [idx for idx in zone_indices if idx < len(df) - 2]
        base_idx = base_candidates[-1] if base_candidates else origin_idx

        # Measure base candles (small bodies inside range)
        base_count = 1
        b_i = base_idx
        while b_i > 0 and base_count < self.max_base_candles:
            b_range = df["high"].iloc[b_i] - df["low"].iloc[b_i]
            b_body = abs(df["close"].iloc[b_i] - df["open"].iloc[b_i])
            if b_range > 0 and (b_body / b_range) < 0.65:
                base_count += 1
                b_i -= 1
            else:
                break

        # 2. Strength of Departure & Impulsive Displacement
        departure_idx = min(base_idx + 1, len(df) - 1)
        dep_candle = df.iloc[departure_idx]
        dep_body = abs(dep_candle["close"] - dep_candle["open"])
        dep_atr = max(float(atr_series.iloc[departure_idx]) if not atr_series.empty else current_atr, 1e-6)
        displacement_factor = round(dep_body / dep_atr, 2)

        # Also inspect cumulative 3-bar departure impulse
        three_bar_end = min(departure_idx + 3, len(df))
        cum_move = abs(df["close"].iloc[three_bar_end - 1] - df["open"].iloc[departure_idx])
        cum_displacement = round(cum_move / dep_atr, 2)
        is_impulsive = displacement_factor >= 1.2 or cum_displacement >= 2.0

        # 3. Volume Confirmation
        dep_vol = float(dep_candle["volume"])
        dep_sma = float(df["vol_sma"].iloc[departure_idx])
        volume_ratio = round(dep_vol / max(dep_sma, 1e-4), 2)
        volume_confirmed = volume_ratio >= 1.25

        # 4. Liquidity Sweep at Zone Origin
        # Check if the base candle wicked beyond the previous 10-bar swing high/low
        liquidity_sweep = False
        prior_window = df.iloc[max(0, base_idx - 10) : base_idx]
        if not prior_window.empty:
            if z_type == "DEMAND":
                prior_lowest = prior_window["low"].min()
                if df["low"].iloc[base_idx] < prior_lowest and df["close"].iloc[base_idx] > prior_lowest:
                    liquidity_sweep = True
            else:
                prior_highest = prior_window["high"].max()
                if df["high"].iloc[base_idx] > prior_highest and df["close"].iloc[base_idx] < prior_highest:
                    liquidity_sweep = True

        # 5. BOS / CHoCH Confirmation on Departure
        bos_confirmed = False
        choch_confirmed = False
        if not prior_window.empty:
            if z_type == "DEMAND":
                prior_swing_high = prior_window["high"].max()
                subsequent_highs = df["high"].iloc[departure_idx : min(departure_idx + 15, len(df))]
                if not subsequent_highs.empty and subsequent_highs.max() > prior_swing_high:
                    bos_confirmed = True
                    choch_confirmed = True
            else:
                prior_swing_low = prior_window["low"].min()
                subsequent_lows = df["low"].iloc[departure_idx : min(departure_idx + 15, len(df))]
                if not subsequent_lows.empty and subsequent_lows.min() < prior_swing_low:
                    bos_confirmed = True
                    choch_confirmed = True

        # 6. Touches & Mitigation Tracking
        # Scan subsequent candles after departure
        subsequent_candles = df.iloc[departure_idx + 1 :]
        retest_count = 0
        deepest_mitigation = 0.0
        is_breached = False
        bounce_count = 0

        for _, row in subsequent_candles.iterrows():
            c_high = float(row["high"])
            c_low = float(row["low"])
            c_close = float(row["close"])

            if z_type == "DEMAND":
                # Price touched into demand
                if c_low <= proximal and c_high >= distal:
                    retest_count += 1
                    penetration = (proximal - c_low) / zone_height
                    deepest_mitigation = max(deepest_mitigation, penetration * 100.0)
                    if c_close > proximal:
                        bounce_count += 1
                if c_close < distal:
                    is_breached = True
            else:  # SUPPLY
                # Price touched into supply
                if c_high >= proximal and c_low <= distal:
                    retest_count += 1
                    penetration = (c_high - proximal) / zone_height
                    deepest_mitigation = max(deepest_mitigation, penetration * 100.0)
                    if c_close < proximal:
                        bounce_count += 1
                if c_close > distal:
                    is_breached = True

        deepest_mitigation = round(min(deepest_mitigation, 100.0), 1)

        # 7. Higher-Timeframe Alignment
        htf_bias = "NEUTRAL"
        if htf_candles and len(htf_candles) >= 20:
            htf_closes = [float(c["close"]) for c in htf_candles]
            htf_ema20 = float(pd.Series(htf_closes).ewm(span=20).mean().iloc[-1])
            htf_ema50 = float(pd.Series(htf_closes).ewm(span=50).mean().iloc[-1])
            if htf_closes[-1] > htf_ema20 > htf_ema50:
                htf_bias = "BULLISH"
            elif htf_closes[-1] < htf_ema20 < htf_ema50:
                htf_bias = "BEARISH"
        htf_aligned = (z_type == "DEMAND" and htf_bias == "BULLISH") or (z_type == "SUPPLY" and htf_bias == "BEARISH") or htf_bias == "NEUTRAL"

        # 8. Multi-Label Classification
        # Required categories: STRONG, VALID, WEAK, TOO WIDE, OVERLAPPING, FRESH, TESTED, PARTIALLY MITIGATED, FULLY MITIGATED, INVALIDATED, INSUFFICIENT DATA
        labels: list[str] = []
        classification = "VALID"
        reason_parts: list[str] = []

        if is_breached or deepest_mitigation >= 100.0:
            classification = "INVALIDATED"
            labels.append("INVALIDATED")
            reason_parts.append(f"Price decisively closed beyond structural distal boundary (${distal:,.2f}) with {deepest_mitigation}% mitigation.")
        elif deepest_mitigation >= 90.0:
            classification = "FULLY MITIGATED"
            labels.append("FULLY MITIGATED")
            reason_parts.append(f"Zone liquidity depleted ({deepest_mitigation}% depth penetration across {retest_count} touches).")
        elif height_in_atr > 3.5:
            classification = "TOO WIDE"
            labels.append("TOO WIDE")
            reason_parts.append(f"Zone thickness ({height_in_atr:.1f}x ATR, {zone_height_pct}%) is excessively wide for safe risk/reward positioning.")
        elif overlapping_zones_count > 0:
            labels.append("OVERLAPPING")
            reason_parts.append(f"Zone overlaps with {overlapping_zones_count} other drawn {z_type.lower()} zone(s).")

        if classification not in ("INVALIDATED", "FULLY MITIGATED", "TOO WIDE"):
            if deepest_mitigation == 0.0:
                labels.append("FRESH")
                if displacement_factor >= 1.5 and volume_confirmed and (bos_confirmed or liquidity_sweep):
                    classification = "STRONG"
                    labels.append("STRONG")
                    reason_parts.append(f"Virgin fresh {z_type} zone with explosive {displacement_factor}x ATR displacement, {volume_ratio}x volume confirmation, and structural break.")
                else:
                    classification = "FRESH"
                    reason_parts.append(f"Virgin untouched {z_type} zone with no prior retests ({displacement_factor}x ATR departure).")
            elif deepest_mitigation <= 40.0:
                labels.append("TESTED")
                labels.append("PARTIALLY MITIGATED")
                if bounce_count >= 1 and displacement_factor >= 1.2:
                    classification = "VALID"
                    labels.append("VALID")
                    reason_parts.append(f"Valid tested zone ({deepest_mitigation}% mitigation, {retest_count} touch(es)) showing clear rejection wicks.")
                else:
                    classification = "PARTIALLY MITIGATED"
                    reason_parts.append(f"Partially mitigated ({deepest_mitigation}% penetration) with moderate institutional response.")
            elif deepest_mitigation < 90.0:
                labels.append("PARTIALLY MITIGATED")
                if displacement_factor < 1.0 or not volume_confirmed:
                    classification = "WEAK"
                    labels.append("WEAK")
                    reason_parts.append(f"Weak zone: deep penetration ({deepest_mitigation}%) combined with sub-ATR departure or lack of volume confirmation.")
                else:
                    classification = "PARTIALLY MITIGATED"
                    reason_parts.append(f"Deeply mitigated zone ({deepest_mitigation}% penetration, {retest_count} touches); requires lower-timeframe confirmation.")

        # If overlapping was flagged and not already assigned
        if overlapping_zones_count > 0 and classification in ("VALID", "FRESH"):
            labels.append("OVERLAPPING")

        classification_reason = " ".join(reason_parts) if reason_parts else f"{classification} {z_type} zone evaluated on closed candle history."

        # Quantitative Score (0 to 100)
        score = 50
        if classification == "STRONG":
            score = 92
        elif classification == "FRESH":
            score = 85
        elif classification == "VALID":
            score = 75
        elif classification == "TESTED":
            score = 70
        elif classification == "PARTIALLY MITIGATED":
            score = 55
        elif classification == "TOO WIDE":
            score = 40
        elif classification == "WEAK":
            score = 35
        elif classification in ("FULLY MITIGATED", "INVALIDATED"):
            score = 15

        if liquidity_sweep:
            score = min(100, score + 8)
        if bos_confirmed:
            score = min(100, score + 8)
        if volume_confirmed:
            score = min(100, score + 6)
        if htf_aligned:
            score = min(100, score + 5)
        score = max(5, min(100, score))

        # Trade Plan Levels
        prec = 6 if current_price < 0.01 else (4 if current_price < 1 else (3 if current_price < 50 else 2))
        if z_type == "DEMAND":
            distance_pct = round(((current_price - proximal) / current_price) * 100, 2)
            equilibrium = round((proximal + distal) / 2.0, prec)
            structural_sl = round(distal - (zone_height * 0.20 + current_atr * 0.25), prec)
            risk = max(current_price - structural_sl, zone_height)
            tp1 = round(current_price + (risk * 1.5), prec)
            tp2 = round(current_price + (risk * 2.5), prec)
            tp3 = round(current_price + (risk * 4.0), prec)
            invalidation = round(distal, prec)
            recommended_entry = round(proximal if "FRESH" in labels else equilibrium, prec)
        else:
            distance_pct = round(((proximal - current_price) / current_price) * 100, 2)
            equilibrium = round((proximal + distal) / 2.0, prec)
            structural_sl = round(distal + (zone_height * 0.20 + current_atr * 0.25), prec)
            risk = max(structural_sl - current_price, zone_height)
            tp1 = round(current_price - (risk * 1.5), prec)
            tp2 = round(current_price - (risk * 2.5), prec)
            tp3 = round(current_price - (risk * 4.0), prec)
            invalidation = round(distal, prec)
            recommended_entry = round(proximal if "FRESH" in labels else equilibrium, prec)

        return {
            "zone_id": zone_id,
            "zone_type": z_type,
            "timeframe": timeframe,
            "proximal": round(proximal, prec),
            "distal": round(distal, prec),
            "equilibrium": equilibrium,
            "zone_height": round(zone_height, prec),
            "zone_height_pct": zone_height_pct,
            "height_in_atr": height_in_atr,
            "current_price": round(current_price, prec),
            "distance_pct": distance_pct,
            "retest_count": retest_count,
            "bounce_count": bounce_count,
            "mitigation_pct": deepest_mitigation,
            "lifecycle_state": "INVALIDATED" if is_breached else ("FULLY_MITIGATED" if deepest_mitigation >= 90 else ("FRESH" if deepest_mitigation == 0 else "PARTIALLY_TESTED")),
            "quality_score": score,
            "classification": classification,
            "labels": labels,
            "classification_reason": classification_reason,
            "evaluation_criteria": {
                "base_candle_structure": f"{base_count} consolidation candles at origin",
                "departure_strength": f"{displacement_factor}x ATR displacement ({'Impulsive' if is_impulsive else 'Gradual'})",
                "impulsive_price_movement": is_impulsive,
                "previous_touches": retest_count,
                "freshness": "VIRGIN_FRESH" if deepest_mitigation == 0 else f"{deepest_mitigation}% MITIGATED",
                "mitigation_status": f"{deepest_mitigation}% penetration depth",
                "liquidity_sweep": liquidity_sweep,
                "bos_confirmation": bos_confirmed,
                "choch_confirmation": choch_confirmed,
                "volume_confirmation": f"{volume_ratio}x 20-period SMA volume ({'Confirmed' if volume_confirmed else 'Unconfirmed'})",
                "higher_timeframe_alignment": f"{htf_bias} ({'Aligned' if htf_aligned else 'Unaligned'})",
                "zone_width_relative_to_atr": f"{height_in_atr}x ATR ({'Acceptable' if height_in_atr <= 3.5 else 'Too Wide'})",
                "historical_reactions": f"{bounce_count} clean rejection bounces",
            },
            "recommended_entry": recommended_entry,
            "structural_sl": structural_sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "invalidation_level": invalidation,
        }

    def analyze_manual_zones_and_overlaps(
        self,
        candles: list[dict],
        manual_drawings: list[dict],
        timeframe: str = "15m",
        htf_candles: list[dict] | None = None,
    ) -> dict:
        """Evaluates all manual user zones and performs intelligent overlap & consolidation analysis."""
        if not manual_drawings:
            return {
                "zones": [],
                "overlaps": [],
                "overlap_count": 0,
                "summary": "No manual zones drawn on current chart.",
            }

        # Filter for user supply and demand drawings
        valid_zones: list[dict] = []
        for d in manual_drawings:
            tool = (d.get("tool") or "").lower()
            z_type = (d.get("zoneType") or "").upper()
            if tool in ("demand_zone", "supply_zone") or z_type in ("DEMAND", "SUPPLY"):
                top = d.get("priceTop", max(float(d.get("price1", 0) or 0), float(d.get("price2", 0) or 0)))
                bot = d.get("priceBottom", min(float(d.get("price1", 0) or 0), float(d.get("price2", 0) or 0)))
                if top and bot and top > bot:
                    valid_zones.append({
                        "id": d.get("id", f"draw_{int(time.time()*1000)}"),
                        "zone_type": "DEMAND" if ("demand" in tool or z_type == "DEMAND") else "SUPPLY",
                        "top": float(top),
                        "bottom": float(bot),
                        "raw_drawing": d,
                    })

        if not valid_zones:
            return {"zones": [], "overlaps": [], "overlap_count": 0, "summary": "No manual supply or demand zones found."}

        # Step 1: Detect overlaps among zones of the same type
        overlaps: list[dict] = []
        overlap_counts = {z["id"]: 0 for z in valid_zones}

        for i in range(len(valid_zones)):
            for j in range(i + 1, len(valid_zones)):
                z1 = valid_zones[i]
                z2 = valid_zones[j]
                if z1["zone_type"] == z2["zone_type"]:
                    # Overlap interval: [max(bot1, bot2), min(top1, top2)]
                    overlap_bot = max(z1["bottom"], z2["bottom"])
                    overlap_top = min(z1["top"], z2["top"])
                    if overlap_top > overlap_bot:
                        overlap_height = overlap_top - overlap_bot
                        h1 = z1["top"] - z1["bottom"]
                        h2 = z2["top"] - z2["bottom"]
                        pct1 = round((overlap_height / max(h1, 1e-6)) * 100, 1)
                        pct2 = round((overlap_height / max(h2, 1e-6)) * 100, 1)
                        max_pct = max(pct1, pct2)

                        overlap_counts[z1["id"]] += 1
                        overlap_counts[z2["id"]] += 1

                        is_duplicate = max_pct >= 75.0
                        broader_range = [min(z1["bottom"], z2["bottom"]), max(z1["top"], z2["top"])]

                        overlaps.append({
                            "zone_id_1": z1["id"],
                            "zone_id_2": z2["id"],
                            "zone_type": z1["zone_type"],
                            "overlap_price_range": [round(overlap_bot, 4), round(overlap_top, 4)],
                            "overlap_height": round(overlap_height, 4),
                            "overlap_pct_z1": pct1,
                            "overlap_pct_z2": pct2,
                            "is_duplicate_or_nearly_identical": is_duplicate,
                            "consolidation_suggestion": {
                                "action": "MERGE_INTO_BROADER_AREA" if not is_duplicate else "REMOVE_DUPLICATE_KEEP_STRONGER",
                                "broader_range": [round(broader_range[0], 4), round(broader_range[1], 4)],
                                "recommendation": (
                                    f"Suggest merging overlapping {z1['zone_type'].lower()} zones into broader range "
                                    f"[${broader_range[0]:,.2f} - ${broader_range[1]:,.2f}] or keeping the stronger historical POI."
                                ),
                            },
                        })

        # Step 2: Individually evaluate each zone
        evaluated_zones: list[dict] = []
        for vz in valid_zones:
            ev = self.evaluate_manual_zone(
                candles=candles,
                zone_type=vz["zone_type"],
                val1=vz["top"],
                val2=vz["bottom"],
                timeframe=timeframe,
                htf_candles=htf_candles,
                zone_id=vz["id"],
                overlapping_zones_count=overlap_counts.get(vz["id"], 0),
            )
            ev["original_drawing_id"] = vz["id"]
            evaluated_zones.append(ev)

        # Step 3: Compare historical evidence for each overlap pair
        eval_map = {z["zone_id"]: z for z in evaluated_zones}
        for ov in overlaps:
            z1_eval = eval_map.get(ov["zone_id_1"], {})
            z2_eval = eval_map.get(ov["zone_id_2"], {})

            s1 = z1_eval.get("quality_score", 50)
            s2 = z2_eval.get("quality_score", 50)
            stronger_id = ov["zone_id_1"] if s1 >= s2 else ov["zone_id_2"]
            weaker_id = ov["zone_id_2"] if s1 >= s2 else ov["zone_id_1"]

            reason = (
                f"Zone {stronger_id} exhibits stronger technical evidence (Score {max(s1, s2)}/100 vs {min(s1, s2)}/100) "
                f"due to superior departure displacement and freshness. "
                f"{'Identical duplicate zone detected; suggest consolidating.' if ov['is_duplicate_or_nearly_identical'] else 'Partial overlap detected; keep separate for precise entries or treat as unified POI cluster.'}"
            )
            ov["stronger_zone_id"] = stronger_id
            ov["weaker_zone_id"] = weaker_id
            ov["assessment_reason"] = reason

        return {
            "zones": evaluated_zones,
            "overlaps": overlaps,
            "overlap_count": len(overlaps),
            "summary": f"Evaluated {len(evaluated_zones)} manual zones with {len(overlaps)} overlapping relationship(s).",
        }


supply_demand_engine = SupplyDemandEngine()
