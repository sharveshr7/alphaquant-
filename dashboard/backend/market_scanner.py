"""Dynamic Market Scanner for AlphaQuant AI Pro.

Scans Binance USDⓈ-M Futures pairs, filters eligible perpetual contracts with
24h volume > $1,000,000 USD, enriches with CoinGecko market-cap data, computes
multi-factor composite quantitative rankings, and identifies high-probability trading setups.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import numpy as np
import pandas as pd

from .coingecko_provider import coingecko_provider
from .market_data import (
    BINANCE_BASE_URL,
    get_all_futures_tickers,
    get_exchange_info,
    get_klines,
    get_open_interest as md_get_open_interest,
    get_premium_index as md_get_premium_index,
    normalize_symbol,
)

logger = logging.getLogger("market_scanner")


class MarketScanner:
    """Core market scanner for discovering, ranking, and identifying setups across Binance Futures."""

    def __init__(self) -> None:
        self._universe_cache: tuple[float, dict[str, Any]] = (0.0, {})
        self._scanner_data_cache: tuple[float, dict[str, Any]] = (0.0, {})
        self._funding_rates_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._cache_ttl = 300.0  # 5 minutes cache for universe and scanner data
        self._funding_cache_ttl = 15.0  # 15 seconds cache for funding rates

    async def discover_universe(self) -> dict[str, Any]:
        """Discover all eligible perpetual trading pairs from Binance USDⓈ-M Futures.
        
        Filters:
        - contractType == 'PERPETUAL'
        - status == 'TRADING'
        - symbol.endswith('USDT')
        - 24h quote volume > $1,000,000 USD
        
        Cached for 5 minutes.
        """
        now = time.time()
        c_time, c_data = self._universe_cache
        if now - c_time < self._cache_ttl and c_data:
            return c_data

        # 1. Fetch exchange info & 24h tickers concurrently
        try:
            exchange_info, tickers = await asyncio.gather(
                get_exchange_info(),
                get_all_futures_tickers(),
                return_exceptions=True,
            )

            if isinstance(exchange_info, Exception) or not isinstance(exchange_info, dict):
                logger.error(f"Failed to fetch exchange_info: {exchange_info}")
                if c_data:
                    return c_data
                return {"count": 0, "pairs": [], "timestamp": int(now)}

            if isinstance(tickers, Exception) or not isinstance(tickers, list):
                logger.error(f"Failed to fetch all futures tickers: {tickers}")
                if c_data:
                    return c_data
                return {"count": 0, "pairs": [], "timestamp": int(now)}

            ticker_map = {t.get("symbol"): t for t in tickers if "symbol" in t}

            symbols_meta = exchange_info.get("symbols", [])
            eligible_pairs: list[dict[str, Any]] = []

            for s in symbols_meta:
                sym = s.get("symbol", "")
                contract_type = s.get("contractType", "")
                status = s.get("status", "")
                quote_asset = s.get("quoteAsset", "")
                base_asset = s.get("baseAsset", "")

                # Must be perpetual contract trading against USDT
                if contract_type != "PERPETUAL" or status != "TRADING" or quote_asset != "USDT":
                    continue

                ticker = ticker_map.get(sym)
                if not ticker:
                    continue

                quote_volume = float(ticker.get("quote_volume_24h", 0.0))
                # Volume filter > $1,000,000 USD
                if quote_volume < 1_000_000.0:
                    continue

                pair_name = f"{base_asset}/USDT"
                eligible_pairs.append(
                    {
                        "symbol": sym,
                        "pair_name": pair_name,
                        "base_asset": base_asset,
                        "quote_asset": quote_asset,
                        "last_price": float(ticker.get("last_price", 0.0)),
                        "change_24h_pct": float(ticker.get("price_change_percent", 0.0)),
                        "high_24h": float(ticker.get("high_24h", 0.0)),
                        "low_24h": float(ticker.get("low_24h", 0.0)),
                        "volume_24h": float(ticker.get("volume_24h", 0.0)),
                        "volume_24h_quote": quote_volume,
                        "open_price": float(ticker.get("open_price", 0.0)),
                        "price_precision": s.get("pricePrecision", 2),
                        "quantity_precision": s.get("quantityPrecision", 2),
                        "status": status,
                    }
                )

            result = {
                "count": len(eligible_pairs),
                "pairs": eligible_pairs,
                "timestamp": int(now),
            }

            if eligible_pairs:
                self._universe_cache = (now, result)

            return result

        except Exception as e:
            logger.error(f"Error in discover_universe: {e}", exc_info=True)
            if c_data:
                return c_data
            return {"count": 0, "pairs": [], "timestamp": int(now)}

    async def get_scanner_data(self) -> dict[str, Any]:
        """Fetch full scanner data for the frontend.
        
        Enriches discovered universe with CoinGecko market-cap data, computes
        composite multi-factor rankings, and returns pairs sorted by rank.
        """
        now = time.time()
        c_time, c_data = self._scanner_data_cache
        if now - c_time < self._cache_ttl and c_data:
            return c_data

        universe_res = await self.discover_universe()
        pairs = universe_res.get("pairs", [])
        if not pairs:
            return {"count": 0, "pairs": [], "timestamp": int(now)}

        # Best-effort CoinGecko enrichment
        mc_map: dict[str, dict[str, Any]] = {}
        try:
            mc_map = await coingecko_provider.get_market_cap_map()
        except Exception as e:
            logger.warning(f"CoinGecko market-cap enrichment failed (proceeding without): {e}")

        enriched_pairs: list[dict[str, Any]] = []
        for p in pairs:
            sym = p["symbol"]
            base = p["base_asset"]

            # CoinGecko lookup strategies
            cg_info = (
                mc_map.get(sym)
                or mc_map.get(base)
                or mc_map.get(coingecko_provider.to_coingecko_symbol(sym))
                or {}
            )

            pair_dict = dict(p)
            pair_dict["market_cap"] = cg_info.get("market_cap")
            pair_dict["market_cap_rank"] = cg_info.get("market_cap_rank")
            pair_dict["coin_name"] = cg_info.get("coin_name") or base
            pair_dict["coin_id"] = cg_info.get("coin_id") or base.lower()
            pair_dict["circulating_supply"] = cg_info.get("circulating_supply")
            pair_dict["fully_diluted_valuation"] = cg_info.get("fully_diluted_valuation")

            enriched_pairs.append(pair_dict)

        # Compute multi-factor rankings
        ranked_pairs = await self.compute_rankings(enriched_pairs)

        result = {
            "count": len(ranked_pairs),
            "pairs": ranked_pairs,
            "timestamp": int(now),
        }

        if ranked_pairs:
            self._scanner_data_cache = (now, result)

        return result

    async def get_funding_rates(self) -> list[dict[str, Any]]:
        """Fetch funding rates and mark prices for all active symbols.
        
        Uses /fapi/v1/premiumIndex with short TTL cache.
        """
        now = time.time()
        c_time, c_data = self._funding_rates_cache
        if now - c_time < self._funding_cache_ttl and c_data:
            return c_data

        try:
            raw_data = await md_get_premium_index()
            if isinstance(raw_data, list):
                rates = []
                for item in raw_data:
                    last_rate = float(item.get("last_funding_rate", 0.0))
                    rates.append(
                        {
                            "symbol": item.get("symbol", ""),
                            "mark_price": float(item.get("mark_price", 0.0)),
                            "index_price": float(item.get("index_price", 0.0)),
                            "last_funding_rate": last_rate,
                            "funding_rate_pct": round(last_rate * 100, 4),
                            "annualized_funding_pct": round(last_rate * 3 * 365 * 100, 2),
                            "next_funding_time": int(item.get("next_funding_time", 0)),
                            "time": int(item.get("time", 0)),
                        }
                    )
                if rates:
                    self._funding_rates_cache = (now, rates)
                    return rates
            return c_data
        except Exception as e:
            logger.warning(f"Error fetching funding rates: {e}")
            return c_data

    async def get_open_interest(self, symbol: str) -> dict[str, Any]:
        """Fetch open interest for a specific symbol."""
        return await md_get_open_interest(symbol)

    async def compute_rankings(self, pairs_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rank pairs by composite quantitative score.
        
        Components:
        1. Liquidity Score (35%): Log-scaled & percentile-ranked quote volume
        2. Volume Momentum Score (25%): Relative volume intensity vs baseline/market-cap
        3. Volatility Score (20%): 24h absolute price change magnitude
        4. Market-Cap Score (20%): Market capitalization tier or fallback liquidity stability
        """
        if not pairs_data:
            return []

        n = len(pairs_data)
        if n == 1:
            item = dict(pairs_data[0])
            item.update({
                "composite_score": 100.0,
                "liquidity_score": 100.0,
                "volatility_score": 100.0,
                "market_cap_score": 100.0,
                "momentum_score": 100.0,
                "rank": 1,
            })
            return [item]

        # 1. Quote Volume / Liquidity
        quote_volumes = [float(p.get("volume_24h_quote", 0.0)) for p in pairs_data]
        vol_series = pd.Series(quote_volumes)
        vol_pctile = vol_series.rank(pct=True) * 100.0

        # Median volume baseline
        median_vol = float(vol_series.median()) if float(vol_series.median()) > 0 else 1.0

        # 2. Volatility
        abs_changes = [abs(float(p.get("change_24h_pct", 0.0))) for p in pairs_data]
        change_series = pd.Series(abs_changes)
        volat_pctile = change_series.rank(pct=True) * 100.0

        # 3. Market-Cap Scores
        mc_scores: list[float] = []
        for i, p in enumerate(pairs_data):
            mc_rank = p.get("market_cap_rank")
            mc_val = p.get("market_cap")

            if mc_rank is not None and mc_rank > 0:
                if mc_rank <= 10:
                    score = 95.0 + (10 - mc_rank) * 0.5
                elif mc_rank <= 50:
                    score = 80.0 + (50 - mc_rank) / 40.0 * 15.0
                elif mc_rank <= 100:
                    score = 65.0 + (100 - mc_rank) / 50.0 * 15.0
                elif mc_rank <= 250:
                    score = 45.0 + (250 - mc_rank) / 150.0 * 20.0
                else:
                    score = max(20.0, 45.0 - (mc_rank - 250) * 0.05)
            elif mc_val is not None and mc_val > 0:
                # Log10 scale for market cap (10M -> 7, 100B -> 11)
                log_mc = math.log10(mc_val)
                score = min(100.0, max(20.0, (log_mc - 7.0) / 4.0 * 80.0 + 20.0))
            else:
                # Fallback to liquidity percentile * 0.8
                score = float(vol_pctile.iloc[i]) * 0.8

            mc_scores.append(round(score, 2))

        # 4. Volume Momentum
        momentum_scores: list[float] = []
        for i, p in enumerate(pairs_data):
            q_vol = float(p.get("volume_24h_quote", 0.0))
            mc_val = p.get("market_cap")

            if mc_val and mc_val > 0:
                # Turnover ratio (24h volume / market cap)
                turnover = q_vol / mc_val
                # Typical healthy turnover is 0.05 to 0.50
                mom = min(100.0, (turnover / 0.50) * 100.0)
            else:
                # Relative volume vs median
                rel_vol = q_vol / median_vol
                mom = min(100.0, (rel_vol / 4.0) * 100.0)

            # Blend with absolute volume percentile for stability
            blended_mom = (mom * 0.5) + (float(vol_pctile.iloc[i]) * 0.5)
            momentum_scores.append(round(blended_mom, 2))

        # 5. Composite Ranking
        ranked_list: list[dict[str, Any]] = []
        for i, p in enumerate(pairs_data):
            liq = round(float(vol_pctile.iloc[i]), 2)
            volat = round(float(volat_pctile.iloc[i]), 2)
            mc = mc_scores[i]
            mom = momentum_scores[i]

            # Weighted composite score
            composite = (
                (liq * 0.35)
                + (mom * 0.25)
                + (volat * 0.20)
                + (mc * 0.20)
            )

            p_ranked = dict(p)
            p_ranked["liquidity_score"] = liq
            p_ranked["volatility_score"] = volat
            p_ranked["market_cap_score"] = mc
            p_ranked["momentum_score"] = mom
            p_ranked["composite_score"] = round(composite, 2)
            ranked_list.append(p_ranked)

        # Sort descending by composite score
        ranked_list.sort(key=lambda x: x["composite_score"], reverse=True)

        # Assign ordinal ranks
        for rank_idx, item in enumerate(ranked_list, start=1):
            item["rank"] = rank_idx

        return ranked_list

    async def scan_opportunities(self, top_n: int = 20) -> dict[str, Any]:
        """Scan top N pairs by volume / composite score for high-conviction trading setups.
        
        Fetches 200 candles on 15m timeframe for each pair and computes RSI & EMA alignment.
        Classifies setups into:
        - BULLISH_SETUP: Strong trend continuation or oversold bounce with EMA confluence
        - BEARISH_SETUP: Strong downtrend continuation or overbought rejection
        - NEUTRAL: Choppy or conflicting signals
        
        Returns pairs sorted by setup quality score.
        """
        scanner_res = await self.get_scanner_data()
        pairs = scanner_res.get("pairs", [])
        if not pairs:
            return {"count": 0, "opportunities": [], "timestamp": int(time.time())}

        selected_pairs = pairs[:top_n]
        semaphore = asyncio.Semaphore(5)  # Concurrency limit to protect API limits

        async def analyze_pair(pair_info: dict[str, Any]) -> dict[str, Any] | None:
            sym = pair_info.get("symbol", "")
            async with semaphore:
                try:
                    candles = await get_klines(pair=sym, interval="15m", limit=200)
                    if not candles or len(candles) < 50:
                        return None

                    df = pd.DataFrame(candles)
                    for col in ["open", "high", "low", "close", "volume"]:
                        df[col] = pd.to_numeric(df[col])

                    closes = df["close"]
                    # Calculate EMAs
                    ema_20 = closes.ewm(span=20, adjust=False).mean()
                    ema_50 = closes.ewm(span=50, adjust=False).mean()
                    ema_200 = closes.ewm(span=200, adjust=False).mean()

                    # Calculate RSI (14)
                    delta = closes.diff()
                    gain = delta.clip(lower=0)
                    loss = -delta.clip(upper=0)
                    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
                    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
                    rs = avg_gain / (avg_loss + 1e-10)
                    rsi_series = 100 - (100 / (1 + rs))

                    current_price = float(closes.iloc[-1])
                    cur_ema_20 = float(ema_20.iloc[-1])
                    cur_ema_50 = float(ema_50.iloc[-1])
                    cur_ema_200 = float(ema_200.iloc[-1])
                    cur_rsi = float(rsi_series.iloc[-1])

                    # Setup Evaluation Logic
                    signals: list[str] = []
                    classification = "NEUTRAL"
                    setup_quality = 40.0

                    is_bullish_stacked = cur_ema_20 > cur_ema_50 > cur_ema_200
                    is_bearish_stacked = cur_ema_20 < cur_ema_50 < cur_ema_200

                    change_24h = float(pair_info.get("change_24h_pct", 0.0))

                    if is_bullish_stacked and current_price > cur_ema_20:
                        signals.append("Perfect Bullish EMA Stack (Price > EMA20 > EMA50 > EMA200)")
                        if 48.0 <= cur_rsi <= 68.0:
                            classification = "BULLISH_SETUP"
                            setup_quality = 88.0
                            signals.append(f"RSI in Bullish Momentum Continuation Zone ({cur_rsi:.1f})")
                        elif cur_rsi > 70.0:
                            classification = "BULLISH_SETUP"
                            setup_quality = 74.0
                            signals.append(f"Strong Trend with High RSI ({cur_rsi:.1f}) - Extended")
                        else:
                            classification = "BULLISH_SETUP"
                            setup_quality = 78.0
                            signals.append(f"Bullish Trend Retracement to Support ({cur_rsi:.1f})")
                    elif is_bearish_stacked and current_price < cur_ema_20:
                        signals.append("Perfect Bearish EMA Stack (Price < EMA20 < EMA50 < EMA200)")
                        if 32.0 <= cur_rsi <= 52.0:
                            classification = "BEARISH_SETUP"
                            setup_quality = 88.0
                            signals.append(f"RSI in Bearish Momentum Breakdown Zone ({cur_rsi:.1f})")
                        elif cur_rsi < 30.0:
                            classification = "BEARISH_SETUP"
                            setup_quality = 74.0
                            signals.append(f"Strong Downtrend with Oversold RSI ({cur_rsi:.1f}) - Extended")
                        else:
                            classification = "BEARISH_SETUP"
                            setup_quality = 78.0
                            signals.append(f"Bearish Trend Relief Rally into Resistance ({cur_rsi:.1f})")
                    elif cur_rsi < 32.0 and current_price > float(closes.iloc[-2]):
                        classification = "BULLISH_SETUP"
                        setup_quality = 72.0
                        signals.append(f"Oversold Reversal Bounce (RSI {cur_rsi:.1f})")
                    elif cur_rsi > 72.0 and current_price < float(closes.iloc[-2]):
                        classification = "BEARISH_SETUP"
                        setup_quality = 72.0
                        signals.append(f"Overbought Exhaustion Reversal (RSI {cur_rsi:.1f})")
                    elif current_price > cur_ema_200 and change_24h > 3.0:
                        classification = "BULLISH_SETUP"
                        setup_quality = 62.0
                        signals.append("Price Above 200 EMA with Positive 24h Momentum")
                    elif current_price < cur_ema_200 and change_24h < -3.0:
                        classification = "BEARISH_SETUP"
                        setup_quality = 62.0
                        signals.append("Price Below 200 EMA with Negative 24h Momentum")
                    else:
                        classification = "NEUTRAL"
                        setup_quality = 45.0
                        signals.append("Consolidation / Mixed EMA Structure")

                    # Confluence adjustments
                    if classification == "BULLISH_SETUP" and change_24h > 0:
                        setup_quality = min(98.0, setup_quality + 5.0)
                    elif classification == "BEARISH_SETUP" and change_24h < 0:
                        setup_quality = min(98.0, setup_quality + 5.0)

                    # Liquidity bonus
                    quote_vol = float(pair_info.get("volume_24h_quote", 0.0))
                    if quote_vol > 50_000_000.0:
                        setup_quality = min(99.0, setup_quality + 3.0)

                    return {
                        "symbol": sym,
                        "pair_name": pair_info.get("pair_name", f"{sym[:-4]}/USDT"),
                        "classification": classification,
                        "setup_quality": round(setup_quality, 1),
                        "current_price": current_price,
                        "change_24h_pct": change_24h,
                        "volume_24h_quote": quote_vol,
                        "rsi": round(cur_rsi, 2),
                        "ema_20": round(cur_ema_20, 4),
                        "ema_50": round(cur_ema_50, 4),
                        "ema_200": round(cur_ema_200, 4),
                        "signals": signals,
                        "market_cap": pair_info.get("market_cap"),
                        "market_cap_rank": pair_info.get("market_cap_rank"),
                        "coin_name": pair_info.get("coin_name"),
                        "rank": pair_info.get("rank"),
                    }
                except Exception as e:
                    logger.warning(f"Error analyzing setup for {sym}: {e}")
                    return None

        tasks = [analyze_pair(p) for p in selected_pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_opportunities: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, dict) and r:
                valid_opportunities.append(r)

        # Sort opportunities by setup_quality descending
        valid_opportunities.sort(key=lambda x: x["setup_quality"], reverse=True)

        return {
            "count": len(valid_opportunities),
            "opportunities": valid_opportunities,
            "timestamp": int(time.time()),
        }


# Singleton instance
market_scanner = MarketScanner()
