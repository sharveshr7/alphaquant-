"""Verification script to test all backend endpoints and services."""

import asyncio
import sys
from pathlib import Path

# Add project root
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dashboard.backend.ai_assistant import generate_ai_response
from dashboard.backend.market_data import get_klines, get_ticker_24h
from dashboard.backend.paper_trader import paper_trader
from dashboard.backend.strategy_engine import evaluate_confluence
from dashboard.backend.technical_analysis import compute_indicators


async def test_all() -> None:
    print("1. Testing 24h Ticker...")
    ticker = await get_ticker_24h("BTC/USDT")
    price = ticker["last_price"]
    change = ticker["price_change_percent"]
    print(f"   -> BTC Price: ${price:,} (24h Change: {change}%)")

    print("\n2. Testing 15m Klines...")
    candles = await get_klines("BTC/USDT", "15m", limit=100)
    print(f"   -> Fetched {len(candles)} candles. Latest: O={candles[-1]['open']} C={candles[-1]['close']}")

    print("\n3. Testing Technical Indicators & Supply/Demand Zones...")
    ind = compute_indicators(candles)
    latest = ind["latest"]
    print(f"   -> RSI: {latest['rsi']}, EMA50: {latest['ema_50']:.2f}, EMA200: {latest['ema_200']:.2f}")
    print(f"   -> Demand Zones: {len(ind['zones']['demand'])}, Supply Zones: {len(ind['zones']['supply'])}")

    print("\n4. Testing Supply/Demand Confluence Strategy...")
    confluence = evaluate_confluence(candles)
    print(f"   -> Confluence Score: {confluence['confluence_score']} / {confluence['max_score']}")
    print(f"   -> Signal: {confluence['signal']} | Action: {confluence['action']}")
    for c in confluence["checklist"]:
        status = "X" if c["passed"] else " "
        print(f"      [{status}] {c['title']}: {c['detail']}")

    print("\n5. Testing AI Chat Assistant...")
    ai_res = await generate_ai_response(
        user_message="What is the current trend and key demand zones for BTC?",
        pair="BTC/USDT",
        interval="15m",
        current_price=candles[-1]["close"],
        indicators=latest,
        confluence=confluence,
    )
    print(f"   -> AI Engine: {ai_res['engine']} ({ai_res['model_used']})")
    print(f"   -> AI Response Preview (first 150 chars):\n{ai_res['response'][:150]}...")

    print("\n6. Testing Paper Trader...")
    summary = paper_trader.get_summary()
    print(f"   -> Initial Paper Balance: ${summary['balance']}")
    order = paper_trader.open_position("BTC/USDT", "BUY", 100.0, candles[-1]["close"])
    pos_id = order["position"]["id"]
    pos_qty = order["position"]["quantity"]
    print(f"   -> Opened simulated position: ID={pos_id} Qty={pos_qty:.6f}")
    close = paper_trader.close_position(pos_id, candles[-1]["close"] * 1.01)
    pnl = close["closed_trade"]["realized_pnl"]
    print(f"   -> Closed simulated position: Realized PnL=${pnl}")

    print("\nALL BACKEND CHECKS PASSED PERFECTLY!")


if __name__ == "__main__":
    asyncio.run(test_all())
