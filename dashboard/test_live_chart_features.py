"""Automated integration verification script for Live TradingView Chart Upgrades."""

import json
import urllib.request


def test_suite():
    print("=== ALPHAQUANT PRO LIVE CHART SUITE TEST ===")

    # 1. System Health
    resp = urllib.request.urlopen("http://127.0.0.1:8000/api/health")
    data = json.loads(resp.read().decode())
    assert resp.status == 200, "Health check failed"
    print(f"[PASS] 1. System Health: OK (Version: {data['version']}, Gemini Active: {data['gemini_active']})")

    # 2. 365 Days Historical Data
    resp = urllib.request.urlopen("http://127.0.0.1:8000/api/candles?pair=BTC/USDT&interval=1d&days=365")
    c_data = json.loads(resp.read().decode())
    assert resp.status == 200, "365d fetch failed"
    count = c_data.get("count", 0)
    assert count >= 365, f"Expected at least 365 daily candles, got {count}"
    print(f"[PASS] 2. 365+ Days Historical Data: {count} daily candles fetched from Binance.")

    # 3. Backward Pagination by endTime
    oldest_ts = c_data["candles"][0]["time"]
    resp = urllib.request.urlopen(f"http://127.0.0.1:8000/api/candles?pair=BTC/USDT&interval=1d&limit=100&endTime={oldest_ts - 1}")
    p_data = json.loads(resp.read().decode())
    assert resp.status == 200, "Pagination fetch failed"
    p_count = p_data.get("count", 0)
    assert p_count > 0, "Expected older paginated candles"
    assert p_data["candles"][-1]["time"] < oldest_ts, "Paginated chunk must precede oldest timestamp"
    print(f"[PASS] 3. Historical Backward Pagination: Retrieved {p_count} older candles prior to {oldest_ts}.")

    # 4. Manual Zone Analysis + Gemini AI Integration
    payload = json.dumps({
        "pair": "BTC/USDT",
        "timeframe": "15m",
        "zone_type": "DEMAND",
        "proximal": 73500.0,
        "distal": 72800.0,
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/analyze-zone",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    z_res = json.loads(resp.read().decode())
    assert resp.status == 200, "Zone analysis failed"
    assert "classification" in z_res, "Missing classification"
    assert "quality_score" in z_res, "Missing quality_score"
    assert "ai_analysis" in z_res and len(z_res["ai_analysis"]) > 50, "Missing AI analysis narrative"
    print(f"[PASS] 4. 'Analyze My Zone' with Gemini AI: {z_res['classification']} (Score: {z_res['quality_score']}/100)")
    print(f"       Entry: ${z_res['recommended_entry']:,.2f} | SL: ${z_res['structural_sl']:,.2f} | TP1: ${z_res['tp1']:,.2f}")
    print(f"       AI Analysis: {len(z_res['ai_analysis'])} characters generated.")

    # 5. Static Assets Verification
    static_files = [
        "http://127.0.0.1:8000/",
        "http://127.0.0.1:8000/static/js/indexeddb_cache.js",
        "http://127.0.0.1:8000/static/js/indicators_engine.js",
        "http://127.0.0.1:8000/static/js/chart.js",
        "http://127.0.0.1:8000/static/js/drawing_tools.js",
        "http://127.0.0.1:8000/static/js/live_feed.js",
    ]
    for url in static_files:
        r = urllib.request.urlopen(url)
        assert r.status == 200, f"Static asset failed: {url}"
    print(f"[PASS] 5. All Frontend Assets & Scripts Verified Accessible (HTTP 200).")

    print("\n>>> ALL 5 INTERACTIVE TRADINGVIEW CHART CRITERIA VERIFIED OPERATIONAL! <<<")


if __name__ == "__main__":
    test_suite()
