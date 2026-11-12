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

from .coingecko_provider import coingecko_provider, get_market_cap_provider
from .market_data import (
    BINANCE_BASE_URL,
    get_all_futures_tickers,
    get_exchange_info,
    get_klines,
    get_klines_diagnostics,
    get_open_interest as md_get_open_interest,
    get_premium_index as md_get_premium_index,
    get_connection_status,
    get_market_stream_candles,
    normalize_symbol,
)
from .smc_engine import smc_engine
from .supply_demand import supply_demand_engine
from .technical_analysis import compute_indicators

logger = logging.getLogger("market_scanner")


def _fast_technical_screen(
    closed: list[dict[str, Any]],
    last_price: float,
    indicator_data: dict[str, Any],
) -> dict[str, Any]:
    latest = indicator_data.get("latest", {})
    trend_info = indicator_data.get("trend", {})
    rsi = float(latest.get("rsi") or 50.0)
    atr = float(latest.get("atr") or 0.0)
    adx = float(latest.get("adx") or 20.0)
    ema_20 = float(latest.get("ema_20") or last_price)
    ema_50 = float(latest.get("ema_50") or last_price)
    ema_200 = float(latest.get("ema_200") or last_price)
    closed_close = float(closed[-1]["close"])
    atr_pct = round((atr / closed_close) * 100.0, 2) if closed_close > 0 and atr > 0 else 0.0
    volumes = [float(candle["volume"]) for candle in closed[-21:]]
    average_volume = (
        sum(volumes[:-1]) / max(len(volumes[:-1]), 1)
        if len(volumes) > 1
        else volumes[-1]
    )
    relative_volume = round(volumes[-1] / average_volume, 2) if average_volume > 0 else 1.0
    raw_direction = trend_info.get("trend_direction", "SIDEWAYS")
    if raw_direction in ("STRONG_BULLISH", "BULLISH") or (
        closed_close > ema_20 > ema_50
    ):
        trend = "Bullish"
    elif raw_direction in ("STRONG_BEARISH", "BEARISH") or (
        closed_close < ema_20 < ema_50
    ):
        trend = "Bearish"
    else:
        trend = "Sideways"
    preliminary_candidate = (
        relative_volume >= 0.9
        and adx >= 16.0
        and (
            (trend == "Bullish" and 35.0 <= rsi <= 68.0)
            or (trend == "Bearish" and 32.0 <= rsi <= 65.0)
        )
    )
    return {
        "rsi": rsi,
        "atr": atr,
        "adx": adx,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "volatility": atr_pct,
        "relative_volume": relative_volume,
        "trend": trend,
        "preliminary_candidate": preliminary_candidate,
        "closed_candle_time": int(closed[-1].get("time", 0)),
    }


class MarketScanner:
    """Core market scanner for discovering, ranking, and identifying setups across Binance Futures."""

    def __init__(self) -> None:
        self._universe_cache: tuple[float, dict[str, Any]] = (0.0, {})
        self._scanner_data_cache: tuple[float, dict[str, Any]] = (0.0, {})
        self._funding_rates_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._scanner_task: asyncio.Task | None = None
        self._indicator_cache: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._structure_cache: dict[
            tuple[str, str, int], tuple[dict[str, Any], dict[str, Any]]
        ] = {}
        self._cache_ttl = 180.0  # 3 minutes cache for technical scan & universe
        self._funding_cache_ttl = 15.0  # 15 seconds cache for funding rates
        self._config: dict[str, Any] = {
            "min_quote_volume_usd": 1_000_000.0,
            "max_spread_pct": 0.50,
            "min_candles": 60,
            "max_candidates": 0,
            "scan_timeframe": "1h",
        }

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        for k in ("min_quote_volume_usd", "max_spread_pct", "min_candles", "max_candidates", "scan_timeframe"):
            if k in updates and updates[k] is not None:
                self._config[k] = updates[k]
        self._universe_cache = (0.0, {})
        self._scanner_data_cache = (0.0, {})
        return self.get_config()

    async def discover_universe(self) -> dict[str, Any]:
        """Discover all eligible perpetual trading pairs from Binance USDⓈ-M Futures.
        
        Filters:
        - contractType == 'PERPETUAL'
        - status == 'TRADING'
        - alphanumeric base asset with quote asset USDT
        - 24h quote volume > $1,000,000 USD
        
        Cached for 3 minutes while market data remains live.
        """
        now = time.time()
        c_time, c_data = self._universe_cache
        connection_status = get_connection_status()
        if (
            now - c_time < self._cache_ttl
            and c_data
            and connection_status.get("status") == "LIVE"
            and not connection_status.get("exchange_info_stale")
        ):
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
                    return {**c_data, "stale": True}
                return {"count": 0, "pairs": [], "stale": True, "timestamp": int(now)}

            if get_connection_status().get("exchange_info_stale"):
                return {
                    "count": 0,
                    "eligible_contract_count": 0,
                    "pairs": [],
                    "stale": True,
                    "timestamp": int(now),
                }

            if isinstance(tickers, Exception) or not isinstance(tickers, list):
                logger.error(f"Failed to fetch all futures tickers: {tickers}")
                if c_data:
                    return {**c_data, "stale": True}
                return {"count": 0, "pairs": [], "stale": True, "timestamp": int(now)}

            ticker_map = {
                t.get("symbol"): t
                for t in tickers
                if t.get("symbol") and not t.get("stale", False)
            }

            symbols_meta = exchange_info.get("symbols", [])
            eligible_pairs: list[dict[str, Any]] = []
            eligible_contract_count = 0

            for s in symbols_meta:
                sym = s.get("symbol", "")
                contract_type = s.get("contractType", "")
                status = s.get("status", "")
                quote_asset = s.get("quoteAsset", "")
                base_asset = s.get("baseAsset", "")

                # Must be ASCII alphanumeric perpetual contract trading against USDT
                if (
                    contract_type != "PERPETUAL"
                    or status != "TRADING"
                    or quote_asset != "USDT"
                    or not base_asset.isascii()
                    or not base_asset.isalnum()
                ):
                    continue

                eligible_contract_count += 1
                ticker = ticker_map.get(sym)
                if not ticker:
                    continue

                quote_volume = float(ticker.get("quote_volume_24h", 0.0))
                # Configurable minimum quote volume filter (default > $1,000,000 USD)
                min_vol = float(self._config.get("min_quote_volume_usd", 1_000_000.0))
                if quote_volume < min_vol:
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
                "eligible_contract_count": eligible_contract_count,
                "pairs": eligible_pairs,
                "stale": (
                    not bool(ticker_map)
                    or get_connection_status().get("status") != "LIVE"
                ),
                "timestamp": int(now),
            }

            if eligible_pairs:
                self._universe_cache = (now, result)

            return result

        except Exception as e:
            logger.error(f"Error in discover_universe: {e}", exc_info=True)
            if c_data:
                return {**c_data, "stale": True}
            return {"count": 0, "pairs": [], "stale": True, "timestamp": int(now)}

    async def get_scanner_data(self) -> dict[str, Any]:
        task = self._scanner_task
        if task is None or task.done():
            task = asyncio.create_task(self._compute_scanner_data())
            self._scanner_task = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._scanner_task is task:
                self._scanner_task = None

    async def evaluate_market_event(self, symbol: str, interval: str) -> dict[str, Any] | None:
        scan_timeframe = str(self._config.get("scan_timeframe", "1h"))
        if interval not in {scan_timeframe, "4h"}:
            return None

        normalized_symbol = normalize_symbol(symbol)
        candles = get_market_stream_candles(normalized_symbol, scan_timeframe, 120)
        if candles is None:
            candles = await get_klines(
                normalized_symbol,
                scan_timeframe,
                limit=120,
                use_closed_candle_cache=True,
            )
        min_candles = int(self._config.get("min_candles", 60))
        if not candles or len(candles) < min_candles or candles[-1].get("stale", False):
            return None

        closed = candles if candles[-1].get("is_closed", False) else candles[:-1]
        if len(closed) < min_candles:
            return None
        candle_time = int(closed[-1].get("time", 0))
        if candle_time <= 0:
            return None

        cache_key = (normalized_symbol, scan_timeframe, candle_time)
        indicator_data = self._indicator_cache.get(cache_key)
        if indicator_data is None:
            computed = compute_indicators(closed)
            indicator_data = {
                "latest": computed.get("latest", {}),
                "trend": computed.get("trend", {}),
            }
            self._indicator_cache[cache_key] = indicator_data
            if len(self._indicator_cache) > 2000:
                self._indicator_cache.pop(next(iter(self._indicator_cache)))

        screen = _fast_technical_screen(
            closed, float(closed[-1]["close"]), indicator_data
        )
        return {
            "symbol": normalized_symbol,
            "pair": f"{normalized_symbol[:-4]}/USDT",
            "timeframe": scan_timeframe,
            "closed_candle_time": candle_time,
            **screen,
        }

    async def _compute_scanner_data(self) -> dict[str, Any]:
        """Fetch full scanner data with genuine closed-candle TA, SMC structure, and S&D evaluation."""
        scan_started = time.perf_counter()
        stage3_compute_seconds = 0.0
        stage2_cache_hits = 0
        stage3_shortlist_count = 0
        stage3_cache_hits = 0
        now = time.time()
        stage1_started = time.perf_counter()
        c_time, c_data = self._scanner_data_cache
        if now - c_time < self._cache_ttl and c_data:
            status = get_connection_status()
            market_is_live = (
                status.get("status") == "LIVE"
                and not status.get("exchange_info_stale")
            )
            if market_is_live and not c_data.get("stale", False):
                return c_data
            if not market_is_live:
                return {**c_data, "stale": True}

        universe_res = await self.discover_universe()
        pairs = universe_res.get("pairs", [])
        if not pairs:
            result = {
                "count": 0,
                "total_discovered": 0,
                "eligible_contract_count": int(universe_res.get("eligible_contract_count", 0)),
                "analysis_candidates": 0,
                "stage3_shortlist_count": 0,
                "scan_scope": "ALL_ELIGIBLE",
                "scan_duration_ms": round((time.perf_counter() - scan_started) * 1000, 1),
                "scan_stages_ms": {
                    "stage1_market_filter": round((time.perf_counter() - stage1_started) * 1000, 1),
                    "stage2_technical_screen": 0.0,
                    "stage3_deep_confirmation": 0.0,
                },
                "stale": bool(universe_res.get("stale", False)),
                "pairs": [],
                "timestamp": int(now),
            }
            self._scanner_data_cache = (now, result)
            return result

        # Market-Cap enrichment via configured provider (CoinGecko or CoinMarketCap)
        mcap_provider = get_market_cap_provider()
        mc_map: dict[str, dict[str, Any]] = {}
        try:
            mc_map = await mcap_provider.get_market_cap_map()
        except Exception as e:
            logger.warning(f"Market-cap enrichment failed (proceeding without): {e}")

        enriched_pairs: list[dict[str, Any]] = []
        for p in pairs:
            sym = p["symbol"]
            base = p["base_asset"]

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
            pair_dict["change_7d"] = cg_info.get("price_change_7d")
            pair_dict["change_30d"] = cg_info.get("price_change_30d")

            enriched_pairs.append(pair_dict)

        # Compute multi-factor rankings
        ranked_pairs = await self.compute_rankings(enriched_pairs)

        # Merge funding rates
        funding_map: dict[str, float] = {}
        try:
            rates = await self.get_funding_rates()
            for r in rates:
                funding_map[r["symbol"]] = float(r.get("last_funding_rate", 0.0)) * 100.0
        except Exception:
            pass
        stage1_duration_ms = round((time.perf_counter() - stage1_started) * 1000, 1)

        # A zero candidate limit means the signal scan covers the complete eligible universe.
        max_cand = int(self._config.get("max_candidates", 100))
        scan_tf = str(self._config.get("scan_timeframe", "1h"))
        min_candles = int(self._config.get("min_candles", 60))
        top_candidates = ranked_pairs if max_cand <= 0 else ranked_pairs[:max_cand]

        # Stage 2 screens all liquid contracts; expensive structure analysis is limited to candidates.
        sem = asyncio.Semaphore(12)
        stage2_started = time.perf_counter()
        stage2_klines_before = get_klines_diagnostics()

        async def enrich_technical_row(p: dict[str, Any]) -> dict[str, Any] | None:
            nonlocal stage3_compute_seconds, stage2_cache_hits
            nonlocal stage3_shortlist_count, stage3_cache_hits
            sym = p.get("symbol", "")
            pair_name = p.get("pair_name") or sym
            last_p = float(p.get("last_price", 0.0))
            p["funding"] = round(funding_map.get(sym, 0.0), 4)
            p["coin"] = p.get("coin_name") or p.get("base_asset")
            p["price"] = last_p
            p["change_24h"] = p.get("change_24h_pct")
            p["volume"] = p.get("volume_24h_quote")

            async with sem:
                try:
                    candles = await get_klines(
                        sym, scan_tf, limit=120, use_closed_candle_cache=True
                    )
                except Exception:
                    candles = []

            if not candles or len(candles) < min_candles or candles[-1].get("stale", False):
                return None

            closed = candles if candles[-1].get("is_closed", False) else candles[:-1]
            if len(closed) < min_candles:
                return None
            try:
                cache_key = (sym, scan_tf, int(closed[-1].get("time", 0)))
                cached_ind = self._indicator_cache.get(cache_key)
                if cached_ind:
                    stage2_cache_hits += 1
                    indicator_data = cached_ind
                else:
                    indicator_data = compute_indicators(closed)
                    self._indicator_cache[cache_key] = {
                        "latest": indicator_data.get("latest", {}),
                        "trend": indicator_data.get("trend", {}),
                    }
                    if len(self._indicator_cache) > 2000:
                        self._indicator_cache.pop(next(iter(self._indicator_cache)))
                screen = _fast_technical_screen(closed, last_p, indicator_data)
                rsi_val = screen["rsi"]
                atr_val = screen["atr"]
                adx_val = screen["adx"]
                ema_20 = screen["ema_20"]
                ema_50 = screen["ema_50"]
                ema_200 = screen["ema_200"]
                atr_pct = screen["volatility"]
                rel_vol = screen["relative_volume"]
                trend_col = screen["trend"]
                is_preliminary_candidate = screen["preliminary_candidate"]
                dom_struct = "NOT_EVALUATED"
                structure_col = "NOT_EVALUATED"
                sd_col = "NOT_EVALUATED"
                in_demand = False
                in_supply = False
                if is_preliminary_candidate:
                    stage3_shortlist_count += 1
                    cached_structure = self._structure_cache.get(cache_key)
                    if cached_structure:
                        stage3_cache_hits += 1
                        smc_res, sd_res = cached_structure
                    else:
                        stage3_started = time.perf_counter()
                        smc_res = smc_engine.analyze(closed)
                        sd_res = supply_demand_engine.detect_zones(closed, timeframe=scan_tf)
                        stage3_compute_seconds += time.perf_counter() - stage3_started
                        self._structure_cache[cache_key] = (smc_res, sd_res)
                        if len(self._structure_cache) > 2000:
                            self._structure_cache.pop(next(iter(self._structure_cache)))
                    smc_sum = smc_res.get("summary", {})
                    last_struct = smc_sum.get("last_structure_event") or {}
                    dom_struct = smc_sum.get("dominant_structure", "NEUTRAL")
                    raw_ev = (
                        last_struct.get("type") or last_struct.get("event") or ""
                    ) if last_struct else ""
                    structure_col = (
                        "CHoCH" if raw_ev.upper() == "CHOCH"
                        else "BOS" if raw_ev.upper() == "BOS"
                        else "Neutral"
                    )
                    demands = sd_res.get("demand", [])
                    supplies = sd_res.get("supply", [])
                    in_demand = any(
                        d["distal"] * 0.997 <= last_p <= d["proximal"] * 1.015
                        for d in demands[:2]
                    )
                    in_supply = any(
                        s["proximal"] * 0.985 <= last_p <= s["distal"] * 1.003
                        for s in supplies[:2]
                    )
                    sd_col = "Demand" if in_demand else ("Supply" if in_supply else "None")

                # Conservative Signal Decision (NEVER force a signal)
                signal_col = "NO TRADE"
                if is_preliminary_candidate and (
                    trend_col == "Bullish"
                    and dom_struct == "BULLISH"
                    and in_demand
                    and not in_supply
                    and 35.0 <= rsi_val <= 68.0
                    and rel_vol >= 0.9
                    and adx_val >= 16.0
                ):
                    signal_col = "LONG"
                elif is_preliminary_candidate and (
                    trend_col == "Bearish"
                    and dom_struct == "BEARISH"
                    and in_supply
                    and not in_demand
                    and 32.0 <= rsi_val <= 65.0
                    and rel_vol >= 0.9
                    and adx_val >= 16.0
                ):
                    signal_col = "SHORT"

                p["rsi"] = round(rsi_val, 1)
                p["atr"] = atr_val
                p["adx"] = round(adx_val, 1)
                p["ema_20"] = ema_20
                p["ema_50"] = ema_50
                p["ema_200"] = ema_200
                p["volatility"] = atr_pct
                p["relative_volume"] = rel_vol
                p["trend"] = trend_col
                p["structure"] = structure_col
                p["dominant_structure"] = dom_struct
                p["sd"] = sd_col
                p["signal"] = signal_col
                p["analysis_stage"] = (
                    "STAGE_3_DEEP_ANALYZED" if is_preliminary_candidate else "STAGE_2_SCREENED"
                )
                p["scan_timeframe"] = scan_tf
                p["ranking_reason"] = (
                    f"Rank #{p.get('rank')}: Composite {p.get('composite_score')}/100 | "
                    f"Liq {p.get('liquidity_score')}/100, Mom {p.get('momentum_score')}/100, "
                    f"Vol {p.get('volatility_score')}/100, MCap {p.get('market_cap_score')}/100 | "
                    f"{trend_col} ({structure_col}), RelVol {rel_vol}x, RSI {p['rsi']}."
                )
                return p
            except Exception as ex:
                logger.debug(f"Technical enrichment skipped for {sym}: {ex}")
                return None

        tasks = [enrich_technical_row(p) for p in top_candidates]
        enriched_tech = await asyncio.gather(*tasks, return_exceptions=True)

        valid_pairs: list[dict[str, Any]] = []
        for idx, item in enumerate(enriched_tech):
            if isinstance(item, dict):
                valid_pairs.append(item)

        # Re-number continuous ranks only across actually eligible pairs
        for new_rank, vp in enumerate(valid_pairs, start=1):
            vp["rank"] = new_rank
        technical_elapsed_ms = round((time.perf_counter() - stage2_started) * 1000, 1)
        stage3_compute_ms = round(stage3_compute_seconds * 1000, 1)
        stage2_klines_after = get_klines_diagnostics()
        stage2_klines = {
            key: stage2_klines_after[key] - stage2_klines_before.get(key, 0)
            for key in stage2_klines_after
        }

        methodology = {
            "weights": {
                "liquidity_quote_volume": "35%",
                "market_cap_tier": "30%",
                "relative_volume_momentum": "20%",
                "volatility_opportunity": "15%",
            },
            "eligibility_rules": {
                "contract_type": "USDⓈ-M PERPETUAL (TRADING)",
                "min_24h_quote_volume_usd": self._config["min_quote_volume_usd"],
                "min_closed_candles": min_candles,
                "scan_timeframe": scan_tf,
                "max_candidates": max_cand,
            },
            "note": "Only eligible Binance USDⓈ-M contracts passing all liquidity and candle history checks are displayed.",
        }

        result = {
            "count": len(valid_pairs),
            "total_discovered": len(ranked_pairs),
            "eligible_contract_count": int(
                universe_res.get("eligible_contract_count", len(ranked_pairs))
            ),
            "filtered_out_illiquid_count": max(
                0,
                int(universe_res.get("eligible_contract_count", len(ranked_pairs)))
                - len(ranked_pairs),
            ),
            "analysis_candidates": len(top_candidates),
            "stage3_shortlist_count": stage3_shortlist_count,
            "stage3_cache_hits": stage3_cache_hits,
            "stage2_cache_hits": stage2_cache_hits,
            "stage2_klines": stage2_klines,
            "scan_scope": "ALL_ELIGIBLE" if max_cand <= 0 else "LIMITED",
            "scan_duration_ms": round((time.perf_counter() - scan_started) * 1000, 1),
            "scan_stages_ms": {
                "stage1_market_filter": stage1_duration_ms,
                "stage2_technical_screen": round(max(0.0, technical_elapsed_ms - stage3_compute_ms), 1),
                "stage3_deep_confirmation": stage3_compute_ms,
            },
            "pairs": valid_pairs,
            "methodology": methodology,
            "timestamp": int(now),
        }

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
                # Cap unranked microcaps so genuine Top-100 market-cap assets rank properly
                score = min(30.0, float(vol_pctile.iloc[i]) * 0.30)

            mc_scores.append(round(score, 2))

        # 4. Volume Momentum
        momentum_scores: list[float] = []
        for i, p in enumerate(pairs_data):
            q_vol = float(p.get("volume_24h_quote", 0.0))
            mc_val = p.get("market_cap")

            if mc_val and mc_val > 0:
                turnover = q_vol / mc_val
                mom = min(100.0, (turnover / 0.25) * 100.0)
            else:
                rel_vol = q_vol / median_vol
                mom = min(80.0, (rel_vol / 4.0) * 80.0)

            # Blend heavily with absolute volume percentile so deep-orderbook coins retain stability
            blended_mom = (mom * 0.35) + (float(vol_pctile.iloc[i]) * 0.65)
            momentum_scores.append(round(blended_mom, 2))

        # 5. Composite Ranking
        ranked_list: list[dict[str, Any]] = []
        for i, p in enumerate(pairs_data):
            liq = round(float(vol_pctile.iloc[i]), 2)
            volat = round(float(volat_pctile.iloc[i]), 2)
            mc = mc_scores[i]
            mom = momentum_scores[i]

            # Weighted composite score prioritizing liquidity & market cap alongside setup momentum/volatility
            composite = (
                (liq * 0.35)
                + (mc * 0.30)
                + (mom * 0.20)
                + (volat * 0.15)
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

    async def get_top100_universe(self, limit: int = 100) -> dict[str, Any]:
        """Returns the dynamic top universe of up to 100 eligible cryptocurrencies."""
        data = await self.get_scanner_data()
        pairs = data.get("pairs", [])
        return {
            "count": min(len(pairs), limit),
            "total_discovered": data.get("total_discovered", len(pairs)),
            "pairs": pairs[:limit],
            "methodology": data.get("methodology", {}),
            "timestamp": data.get("timestamp", int(time.time())),
        }

    async def get_categorized_opportunities(self) -> dict[str, Any]:
        """Returns structured setup categories built strictly from closed-candle technical screening."""
        data = await self.get_scanner_data()
        pairs = data.get("pairs", [])
        now_ts = int(time.time())

        best_long = []
        best_short = []
        breakouts = []
        pullbacks = []
        demand_zones = []
        supply_zones = []
        waiting_confirmation = []
        avoid = []

        for p in pairs:
            sym = p.get("symbol", "")
            chg = float(p.get("change_24h_pct", 0.0))
            rsi = float(p.get("rsi", 50.0))
            sig = p.get("signal", "NO TRADE")
            struct = p.get("structure", "Neutral")
            dom_struct = p.get("dominant_structure", "NEUTRAL")
            trend = p.get("trend", "Sideways")
            sd = p.get("sd", "None")
            volat = float(p.get("volatility", 1.5))
            rel_vol = float(p.get("relative_volume", 1.0))
            scan_tf = p.get("scan_timeframe", "1h")

            direction = sig if sig in ("LONG", "SHORT") else ("BULLISH_BIAS" if trend == "Bullish" else ("BEARISH_BIAS" if trend == "Bearish" else "NEUTRAL"))
            setup_type = "ZONE_CONFLUENCE" if sd != "None" else ("STRUCTURAL_BOS" if struct == "BOS" else ("STRUCTURAL_CHOCH" if struct == "CHoCH" else "RANGE_EQUILIBRIUM"))

            item = {
                "rank": p.get("rank"),
                "symbol": p.get("pair_name") or f"{sym[:-4]}/USDT",
                "coin": p.get("coin_name") or p.get("base_asset"),
                "direction": direction,
                "timeframe": scan_tf,
                "setup_type": setup_type,
                "entry_status": "WAITING_CONFIRMATION" if sig in ("LONG", "SHORT") else "WAITING",
                "setup_quality": p.get("composite_score", 50),
                "confirmation_status": "POTENTIAL" if sig in ("LONG", "SHORT") or sd != "None" or struct != "Neutral" else "SCANNING",
                "short_reason": p.get("ranking_reason", ""),
                "price": p.get("last_price"),
                "change_24h": chg,
                "rsi": rsi,
                "trend": trend,
                "structure": struct,
                "sd": sd,
                "relative_volume": rel_vol,
                "volatility": volat,
                "volume": p.get("volume_24h_quote"),
                "last_analysis_time": now_ts,
            }

            if sig == "LONG":
                best_long.append(item)
            elif sig == "SHORT":
                best_short.append(item)

            if struct == "BOS" and rel_vol >= 1.2:
                breakouts.append(item)

            if (trend == "Bullish" and 38.0 <= rsi <= 55.0) or sd == "Demand":
                pullbacks.append(item)

            if sd == "Demand":
                demand_zones.append(item)
            elif sd == "Supply":
                supply_zones.append(item)

            if sig == "NO TRADE" and (sd != "None" or struct in ("BOS", "CHoCH")):
                waiting_confirmation.append(item)

            if rsi > 76.0 or rsi < 24.0 or volat > 6.5 or (trend == "Sideways" and rel_vol < 0.7):
                avoid_item = dict(item)
                reasons = []
                if rsi > 76.0:
                    reasons.append(f"Overbought RSI ({rsi})")
                if rsi < 24.0:
                    reasons.append(f"Oversold RSI ({rsi})")
                if volat > 6.5:
                    reasons.append(f"High ATR volatility ({volat}%)")
                if trend == "Sideways" and rel_vol < 0.7:
                    reasons.append("Low volume chop")
                avoid_item["avoid_reason"] = "; ".join(reasons)
                avoid.append(avoid_item)

        return {
            "best_long": best_long[:12],
            "best_short": best_short[:12],
            "breakouts": breakouts[:12],
            "pullbacks": pullbacks[:12],
            "demand_zones": demand_zones[:12],
            "supply_zones": supply_zones[:12],
            "waiting_confirmation": waiting_confirmation[:12],
            "avoid": avoid[:12],
            "timestamp": now_ts,
        }



# Singleton instance
market_scanner = MarketScanner()
