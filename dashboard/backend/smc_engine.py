"""Smart Money Concepts (SMC) Quantitative Engine.

Calculates Market Structure:
- BOS (Break of Structure) & CHoCH (Change of Character)
- Swing Highs & Lows (HH, HL, LH, LL)
- Order Blocks (OB) & Volumized Order Blocks with mitigation tracking
- Fair Value Gaps (FVG) with gap size and mitigation index
- Liquidity levels, Equal Highs (EQH) & Equal Lows (EQL), and Liquidity Sweeps
- Premium, Discount, and Optimal Trade Entry (OTE 0.618 - 0.786) zones
"""

import io
from contextlib import redirect_stdout

import pandas as pd

try:
    with redirect_stdout(io.StringIO()):
        from smartmoneyconcepts import smc
except Exception:
    from smartmoneyconcepts import smc


class SMCEngine:
    def __init__(self, swing_length: int = 5) -> None:
        self.swing_length = swing_length

    def analyze(self, candles: list[dict]) -> dict:
        """Analyzes candles and returns comprehensive SMC structures."""
        if not candles or len(candles) < (self.swing_length * 2 + 10):
            return {
                "swings": [],
                "bos_choch": [],
                "order_blocks": [],
                "fvg": [],
                "liquidity": [],
                "premium_discount": {},
                "summary": {},
            }

        df = pd.DataFrame(candles)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])

        current_price = float(df["close"].iloc[-1])
        times = df["time"].tolist()

        # 1. Swing Highs & Lows
        swings_df = smc.swing_highs_lows(df, swing_length=self.swing_length)
        swings = []
        last_high = None
        last_low = None
        current_structure = "NEUTRAL"

        for idx, row in swings_df.iterrows():
            hl = row.get("HighLow")
            lvl = row.get("Level")
            if pd.notna(hl) and hl != 0 and pd.notna(lvl):
                is_high = hl == 1
                level_val = round(float(lvl), 4)
                time_val = int(times[idx])

                # Determine structure classification: HH, HL, LH, LL
                classification = ""
                if is_high:
                    if last_high is not None:
                        classification = "HH" if level_val > last_high else "LH"
                    else:
                        classification = "HIGH"
                    last_high = level_val
                else:
                    if last_low is not None:
                        classification = "HL" if level_val > last_low else "LL"
                    else:
                        classification = "LOW"
                    last_low = level_val

                swings.append({
                    "index": int(idx),
                    "time": time_val,
                    "type": "SWING_HIGH" if is_high else "SWING_LOW",
                    "level": level_val,
                    "classification": classification,
                })

        if swings:
            # Trend structure assessment from recent 4 swings
            high_types = [s["classification"] for s in swings if s["type"] == "SWING_HIGH"][-2:]
            low_types = [s["classification"] for s in swings if s["type"] == "SWING_LOW"][-2:]
            if "HH" in high_types and "HL" in low_types:
                current_structure = "BULLISH_EXPANSION"
            elif "LH" in high_types and "LL" in low_types:
                current_structure = "BEARISH_EXPANSION"
            else:
                current_structure = "RANGING_CONSOLIDATION"

        # 2. Break of Structure (BOS) & Change of Character (CHoCH)
        bos_df = smc.bos_choch(df, swing_highs_lows=swings_df)
        bos_choch_events = []
        for idx, row in bos_df.iterrows():
            is_bos = row.get("BOS")
            is_choch = row.get("CHOCH")
            lvl = row.get("Level")
            broken_idx = row.get("BrokenIndex")

            event_type = None
            direction = None
            if pd.notna(is_bos) and is_bos != 0:
                event_type = "BOS"
                direction = "BULLISH" if is_bos == 1 else "BEARISH"
            elif pd.notna(is_choch) and is_choch != 0:
                event_type = "CHOCH"
                direction = "BULLISH" if is_choch == 1 else "BEARISH"

            if event_type and pd.notna(lvl):
                bos_choch_events.append({
                    "index": int(idx),
                    "time": int(times[idx]),
                    "type": event_type,
                    "direction": direction,
                    "level": round(float(lvl), 4),
                    "broken_index": int(broken_idx) if pd.notna(broken_idx) else None,
                })

        # 3. Order Blocks (OB) & Volumized Order Blocks
        ob_df = smc.ob(df, swing_highs_lows=swings_df)
        order_blocks = []
        for idx, row in ob_df.iterrows():
            ob_val = row.get("OB")
            if pd.notna(ob_val) and ob_val != 0:
                is_bullish = ob_val == 1
                top = round(float(row.get("Top", 0.0)), 4)
                bottom = round(float(row.get("Bottom", 0.0)), 4)
                vol = round(float(row.get("OBVolume", 0.0)), 2)
                mit_idx = row.get("MitigatedIndex")
                is_mitigated = pd.notna(mit_idx) and int(mit_idx) != 0

                # Only keep unmitigated or recently formed order blocks
                order_blocks.append({
                    "id": f"ob_{idx}",
                    "index": int(idx),
                    "time": int(times[idx]),
                    "type": "BULLISH_OB" if is_bullish else "BEARISH_OB",
                    "top": top,
                    "bottom": bottom,
                    "volume": vol,
                    "is_mitigated": is_mitigated,
                    "mitigated_index": int(mit_idx) if is_mitigated else None,
                })

        # Filter active order blocks (unmitigated, closest to price)
        active_bullish_ob = [o for o in order_blocks if o["type"] == "BULLISH_OB" and not o["is_mitigated"]]
        active_bearish_ob = [o for o in order_blocks if o["type"] == "BEARISH_OB" and not o["is_mitigated"]]

        # 4. Fair Value Gaps (FVG)
        fvg_df = smc.fvg(df)
        fvg_list = []
        for idx, row in fvg_df.iterrows():
            fvg_val = row.get("FVG")
            if pd.notna(fvg_val) and fvg_val != 0:
                is_bullish = fvg_val == 1
                top = round(float(row.get("Top", 0.0)), 4)
                bottom = round(float(row.get("Bottom", 0.0)), 4)
                mit_idx = row.get("MitigatedIndex")
                is_mitigated = pd.notna(mit_idx) and int(mit_idx) != 0
                gap_size = round(abs(top - bottom), 4)

                fvg_list.append({
                    "id": f"fvg_{idx}",
                    "index": int(idx),
                    "time": int(times[idx]),
                    "type": "BULLISH_FVG" if is_bullish else "BEARISH_FVG",
                    "top": top,
                    "bottom": bottom,
                    "gap_size": gap_size,
                    "is_mitigated": is_mitigated,
                })

        # Active FVGs
        active_fvgs = [f for f in fvg_list if not f["is_mitigated"]]

        # 5. Liquidity Pools & Sweeps
        liq_df = smc.liquidity(df, swing_highs_lows=swings_df)
        liquidity_levels = []
        for idx, row in liq_df.iterrows():
            liq_val = row.get("Liquidity")
            if pd.notna(liq_val) and liq_val != 0:
                lvl = round(float(row.get("Level", 0.0)), 4)
                swept = bool(row.get("Swept", False))
                liquidity_levels.append({
                    "index": int(idx),
                    "time": int(times[idx]),
                    "type": "BUY_SIDE_LIQUIDITY" if liq_val == 1 else "SELL_SIDE_LIQUIDITY",
                    "level": lvl,
                    "is_swept": swept,
                })

        # 6. Premium & Discount Retracement Zones
        # Based on dealing range between most recent valid swing high and swing low
        recent_highs = [s["level"] for s in swings if s["type"] == "SWING_HIGH"]
        recent_lows = [s["level"] for s in swings if s["type"] == "SWING_LOW"]

        premium_discount = {}
        if recent_highs and recent_lows:
            high_range = max(recent_highs[-3:])
            low_range = min(recent_lows[-3:])
            span = high_range - low_range
            if span > 0:
                equilibrium = low_range + (span * 0.5)

                # Bullish OTE is in the DISCOUNT portion of the dealing range (61.8% to 78.6% retracement from high)
                bull_ote_proximal = high_range - (span * 0.618)
                bull_ote_distal = high_range - (span * 0.786)

                # Bearish OTE is in the PREMIUM portion of the dealing range (61.8% to 78.6% retracement from low)
                bear_ote_proximal = low_range + (span * 0.618)
                bear_ote_distal = low_range + (span * 0.786)

                in_discount = current_price < equilibrium
                in_premium = current_price > equilibrium
                in_bull_ote = bool(bull_ote_distal <= current_price <= bull_ote_proximal)
                in_bear_ote = bool(bear_ote_proximal <= current_price <= bear_ote_distal)

                premium_discount = {
                    "range_high": round(high_range, 4),
                    "range_low": round(low_range, 4),
                    "equilibrium_50": round(equilibrium, 4),
                    "bull_ote_618": round(bull_ote_proximal, 4),
                    "bull_ote_786": round(bull_ote_distal, 4),
                    "bear_ote_618": round(bear_ote_proximal, 4),
                    "bear_ote_786": round(bear_ote_distal, 4),
                    "optimal_trade_entry": {
                        "bullish_ote_proximal": round(bull_ote_proximal, 4),
                        "bullish_ote_distal": round(bull_ote_distal, 4),
                        "bearish_ote_proximal": round(bear_ote_proximal, 4),
                        "bearish_ote_distal": round(bear_ote_distal, 4),
                    },
                    "in_premium": in_premium,
                    "in_discount": in_discount,
                    "current_regime": (
                        "DISCOUNT (Favorable for Longs)"
                        if in_discount
                        else "PREMIUM (Favorable for Shorts)"
                    ),
                    "in_ote_buy_zone": in_bull_ote,
                    "in_ote_sell_zone": in_bear_ote,
                }

        latest_bos_choch = bos_choch_events[-1] if bos_choch_events else None
        dom_struct = (
            "BULLISH"
            if "BULL" in current_structure or (latest_bos_choch and latest_bos_choch.get("direction") == "BULLISH")
            else ("BEARISH" if "BEAR" in current_structure or (latest_bos_choch and latest_bos_choch.get("direction") == "BEARISH") else "NEUTRAL")
        )

        summary = {
            "current_structure": current_structure,
            "dominant_structure": dom_struct,
            "last_structure_event": latest_bos_choch,
            "active_bullish_ob_count": len(active_bullish_ob),
            "active_bearish_ob_count": len(active_bearish_ob),
            "active_fvg_count": len(active_fvgs),
            "nearest_bullish_ob": active_bullish_ob[-1] if active_bullish_ob else None,
            "nearest_bearish_ob": active_bearish_ob[-1] if active_bearish_ob else None,
            "nearest_fvg": active_fvgs[-1] if active_fvgs else None,
        }

        return {
            "swings": swings[-12:],  # recent 12 swings
            "bos_choch": bos_choch_events[-10:],
            "order_blocks": order_blocks[-15:],
            "fvg": fvg_list[-15:],
            "liquidity": liquidity_levels[-10:],
            "premium_discount": premium_discount,
            "summary": summary,
        }


smc_engine = SMCEngine()
