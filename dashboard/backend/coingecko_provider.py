"""CoinGecko market-cap data provider for AlphaQuant AI Pro.

Fetches market-cap rankings, valuations, and coin metadata from CoinGecko API
with local TTL caching and exponential backoff rate-limit handling.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("coingecko_provider")

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Common CoinGecko symbol -> Binance USDⓈ-M Futures symbol overrides
# Especially for 1000x multiplier contracts, rebrands, and non-standard tickers
SYMBOL_MAP: dict[str, str] = {
    # Large caps
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "bnb": "BNBUSDT",
    "xrp": "XRPUSDT",
    "doge": "DOGEUSDT",
    "ada": "ADAUSDT",
    "avax": "AVAXUSDT",
    "link": "LINKUSDT",
    "sui": "SUIUSDT",
    "near": "NEARUSDT",
    "dot": "DOTUSDT",
    "apt": "APTUSDT",
    "trx": "TRXUSDT",
    "ltc": "LTCUSDT",
    "bch": "BCHUSDT",
    "uni": "UNIUSDT",
    "xlm": "XLMUSDT",
    "hbar": "HBARUSDT",
    "etc": "ETCUSDT",
    "atom": "ATOMUSDT",
    "fil": "FILUSDT",
    "algo": "ALGOUSDT",
    "icp": "ICPUSDT",
    "vet": "VETUSDT",
    "tao": "TAOUSDT",
    "kas": "KASUSDT",
    "fet": "FETUSDT",
    "arb": "ARBUSDT",
    "op": "OPUSDT",
    "tia": "TIAUSDT",
    "sei": "SEIUSDT",
    "inj": "INJUSDT",
    "stx": "STXUSDT",
    "imx": "IMXUSDT",
    "mkr": "MKRUSDT",
    "aave": "AAVEUSDT",
    "ftm": "FTMUSDT",
    "sonic": "SUSDT",
    "s": "SUSDT",
    "ton": "TONUSDT",
    "wif": "WIFUSDT",
    "ondo": "ONDOUSDT",
    "pendle": "PENDLEUSDT",
    "wld": "WLDUSDT",
    "ena": "ENAUSDT",
    "jup": "JUPUSDT",
    "strk": "STRKUSDT",
    "pyth": "PYTHUSDT",
    "gala": "GALAUSDT",
    "sand": "SANDUSDT",
    "mana": "MANAUSDT",
    "axs": "AXSUSDT",
    "flow": "FLOWUSDT",
    "theta": "THETAUSDT",
    "egld": "EGLDUSDT",
    "rune": "RUNEUSDT",
    "kava": "KAVAUSDT",
    "chz": "CHZUSDT",
    "crv": "CRVUSDT",
    "dydx": "DYDXUSDT",
    "snx": "SNXUSDT",
    "gmx": "GMXUSDT",
    "ldo": "LDOUSDT",
    "blur": "BLURUSDT",
    "ordi": "ORDIUSDT",
    "beam": "BEAMXUSDT",
    "mina": "MINAUSDT",
    "woo": "WOOUSDT",
    "ape": "APEUSDT",
    "qnt": "QNTUSDT",
    "neo": "NEOUSDT",
    "eos": "EOSUSDT",
    "iota": "IOTAUSDT",
    "cfx": "CFXUSDT",
    "ar": "ARUSDT",
    "cake": "CAKEUSDT",
    "twt": "TWTUSDT",
    "rose": "ROSEUSDT",
    # Rebrands & migrations
    "matic": "POLUSDT",
    "pol": "POLUSDT",
    "rndr": "RENDERUSDT",
    "render": "RENDERUSDT",
    "btt": "BTTCUSDT",
    "bttc": "BTTCUSDT",
    # 1000x multiplier perpetual contracts on Binance Futures
    "pepe": "1000PEPEUSDT",
    "shib": "1000SHIBUSDT",
    "bonk": "1000BONKUSDT",
    "floki": "1000FLOKIUSDT",
    "lunc": "1000LUNCUSDT",
    "xec": "1000XECUSDT",
    "rats": "1000RATSUSDT",
    "sats": "1000SATSUSDT",
    "cat": "1000CATUSDT",
    "cheems": "1000CHEEMSUSDT",
    "mogu": "1000MOGUSDT",
    "mog": "1000MOGUSDT",
    "neiro": "1000NEIROCTOUSDT",
}

# Reverse lookup: Binance Futures symbol -> CoinGecko lowercase symbol
REVERSE_SYMBOL_MAP: dict[str, str] = {v: k for k, v in SYMBOL_MAP.items()}


class CoinGeckoProvider:
    """Market-cap and coin metadata provider using CoinGecko REST API with TTL cache."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (api_key or os.getenv("COINGECKO_API_KEY", "")).strip()
        self.base_url = COINGECKO_BASE_URL
        self._cache_ttl = 300.0  # 5 minutes cache
        self._top_coins_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._coin_details_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._market_cap_map_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "AlphaQuantPro/1.0",
        }
        if self.api_key:
            # Demo keys start with CG-
            if self.api_key.startswith("CG-"):
                headers["x-cg-demo-api-key"] = self.api_key
            else:
                headers["x-cg-pro-api-key"] = self.api_key
        return headers

    def to_binance_symbol(self, cg_symbol: str) -> str:
        """Translates a CoinGecko symbol to a Binance USDⓈ-M Futures symbol."""
        sym_lower = cg_symbol.lower().strip()
        if sym_lower in SYMBOL_MAP:
            return SYMBOL_MAP[sym_lower]
        return f"{sym_lower.upper()}USDT"

    def to_coingecko_symbol(self, binance_symbol: str) -> str:
        """Translates a Binance Futures symbol to CoinGecko symbol representation."""
        sym_upper = binance_symbol.upper().strip()
        if sym_upper in REVERSE_SYMBOL_MAP:
            return REVERSE_SYMBOL_MAP[sym_upper]

        cleaned = sym_upper.removesuffix("USDT")
        if cleaned.startswith("1000"):
            cleaned = cleaned[4:]
        return cleaned.lower()

    async def fetch_top_coins(self, limit: int = 250) -> list[dict[str, Any]]:
        """Fetch top coins by market cap from CoinGecko.
        
        Cached for 5 minutes. Handles 429 rate limits with exponential backoff.
        """
        now = time.time()
        c_time, c_data = self._top_coins_cache
        if now - c_time < self._cache_ttl and c_data:
            return c_data[:limit]

        url = f"{self.base_url}/coins/markets"
        headers = self._get_headers()

        # Paginate if limit > 250 (max per page is 250)
        per_page = min(limit, 250)
        pages_needed = (limit + per_page - 1) // per_page
        all_coins: list[dict[str, Any]] = []

        max_retries = 3
        base_backoff = 2.0

        for page in range(1, pages_needed + 1):
            params: dict[str, Any] = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
                "price_change_percentage": "24h,7d",
            }

            page_data: list[dict[str, Any]] | None = None
            for attempt in range(max_retries):
                try:
                    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                        response = await client.get(url, params=params)

                        if response.status_code == 429:
                            backoff = base_backoff * (2 ** attempt)
                            logger.warning(
                                f"CoinGecko 429 rate limit reached. Backing off for {backoff:.1f}s "
                                f"(attempt {attempt + 1}/{max_retries})"
                            )
                            await asyncio.sleep(backoff)
                            continue

                        response.raise_for_status()
                        raw = response.json()
                        if isinstance(raw, list):
                            page_data = raw
                            break
                        else:
                            logger.warning(f"Unexpected CoinGecko response: {raw}")
                            break
                except httpx.HTTPError as e:
                    logger.warning(f"CoinGecko HTTP error on page {page} (attempt {attempt + 1}): {e}")
                    await asyncio.sleep(base_backoff * (attempt + 1))
                except Exception as e:
                    logger.warning(f"CoinGecko unexpected error on page {page}: {e}")
                    break

            if page_data:
                all_coins.extend(page_data)
            else:
                # If a page fails, exit pagination loop
                break

        if all_coins:
            self._top_coins_cache = (now, all_coins)
            # Invalidate market_cap_map cache so it rebuilds from fresh data
            self._market_cap_map_cache = (0.0, {})
            return all_coins[:limit]

        # Fallback to expired cache if available
        if c_data:
            logger.info("Returning expired CoinGecko top_coins cache due to fetch failure")
            return c_data[:limit]

        return []

    async def get_coin_details(self, coin_id: str) -> dict[str, Any]:
        """Fetch detailed information for a specific coin by its CoinGecko ID.
        
        Cached for 5 minutes.
        """
        clean_id = coin_id.lower().strip()
        now = time.time()

        if clean_id in self._coin_details_cache:
            c_time, c_data = self._coin_details_cache[clean_id]
            if now - c_time < self._cache_ttl and c_data:
                return c_data

        url = f"{self.base_url}/coins/{clean_id}"
        headers = self._get_headers()
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        }

        max_retries = 3
        base_backoff = 2.0
        details: dict[str, Any] = {}

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                    response = await client.get(url, params=params)

                    if response.status_code == 429:
                        backoff = base_backoff * (2 ** attempt)
                        logger.warning(
                            f"CoinGecko rate limit (429) fetching {clean_id}. Waiting {backoff:.1f}s..."
                        )
                        await asyncio.sleep(backoff)
                        continue

                    response.raise_for_status()
                    data = response.json()
                    market_data = data.get("market_data", {})

                    details = {
                        "id": data.get("id"),
                        "symbol": data.get("symbol", "").lower(),
                        "name": data.get("name"),
                        "market_cap_rank": data.get("market_cap_rank"),
                        "market_cap": market_data.get("market_cap", {}).get("usd"),
                        "fully_diluted_valuation": market_data.get("fully_diluted_valuation", {}).get("usd"),
                        "current_price": market_data.get("current_price", {}).get("usd"),
                        "total_volume": market_data.get("total_volume", {}).get("usd"),
                        "high_24h": market_data.get("high_24h", {}).get("usd"),
                        "low_24h": market_data.get("low_24h", {}).get("usd"),
                        "price_change_percentage_24h": market_data.get("price_change_percentage_24h"),
                        "price_change_percentage_7d": market_data.get("price_change_percentage_7d"),
                        "price_change_percentage_30d": market_data.get("price_change_percentage_30d"),
                        "circulating_supply": market_data.get("circulating_supply"),
                        "total_supply": market_data.get("total_supply"),
                        "max_supply": market_data.get("max_supply"),
                        "ath": market_data.get("ath", {}).get("usd"),
                        "ath_change_percentage": market_data.get("ath_change_percentage", {}).get("usd"),
                    }
                    break
            except httpx.HTTPError as e:
                logger.warning(f"Error fetching CoinGecko coin {clean_id} (attempt {attempt + 1}): {e}")
                await asyncio.sleep(base_backoff * (attempt + 1))
            except Exception as e:
                logger.warning(f"Unexpected error fetching CoinGecko coin {clean_id}: {e}")
                break

        if details:
            self._coin_details_cache[clean_id] = (now, details)
            return details

        # Fallback to expired cache if available
        if clean_id in self._coin_details_cache:
            return self._coin_details_cache[clean_id][1]

        return {}

    async def get_market_cap_map(self) -> dict[str, dict[str, Any]]:
        """Returns a mapping of Binance Futures symbols to CoinGecko coin market data.
        
        Useful for high-speed enrichment in market scanner.
        """
        now = time.time()
        c_time, c_map = self._market_cap_map_cache
        if now - c_time < self._cache_ttl and c_map:
            return c_map

        coins = await self.fetch_top_coins(limit=250)
        mapping: dict[str, dict[str, Any]] = {}

        for c in coins:
            cg_symbol = str(c.get("symbol", "")).lower()
            coin_id = str(c.get("id", "")).lower()

            # Map through SYMBOL_MAP or standard uppercase + USDT
            binance_sym = self.to_binance_symbol(cg_symbol)

            coin_summary = {
                "coin_id": coin_id,
                "coin_name": c.get("name"),
                "cg_symbol": cg_symbol,
                "market_cap": c.get("market_cap"),
                "market_cap_rank": c.get("market_cap_rank"),
                "current_price": c.get("current_price"),
                "total_volume": c.get("total_volume"),
                "price_change_24h": c.get("price_change_percentage_24h"),
                "price_change_7d": c.get("price_change_percentage_7d_in_currency"),
                "circulating_supply": c.get("circulating_supply"),
                "fully_diluted_valuation": c.get("fully_diluted_valuation"),
            }

            mapping[binance_sym] = coin_summary

            # Also index by base asset without USDT
            base_asset = binance_sym.removesuffix("USDT")
            mapping[base_asset] = coin_summary

        if mapping:
            self._market_cap_map_cache = (now, mapping)

        return mapping


# Singleton instance
coingecko_provider = CoinGeckoProvider()

# Module-level aliases for direct imports
fetch_top_coins = coingecko_provider.fetch_top_coins
get_coin_details = coingecko_provider.get_coin_details
