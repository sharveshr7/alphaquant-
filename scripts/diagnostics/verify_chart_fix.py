import asyncio
import json
import base64
import subprocess
import urllib.request
import time
from pathlib import Path
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMP_DIR = PROJECT_ROOT / "runtime" / "temporary" / "cdp_chart_audit"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"

async def audit():
    port = 9222
    needs_spawn = False
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1.0)
    except Exception:
        needs_spawn = True

    chrome_proc = None
    if needs_spawn:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        chrome_proc = subprocess.Popen([
            chrome_exe,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={TEMP_DIR}",
            "--window-size=1600,900",
            "http://127.0.0.1:8000"
        ])
        await asyncio.sleep(3.0)

    tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read().decode())
    ws_url = [t['webSocketDebuggerUrl'] for t in tabs if '8000' in t.get('url','')][0]

    async with websockets.connect(ws_url, max_size=100_000_000) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        await ws.recv()
        await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
        await ws.recv()

        req_id = 100
        async def evaluate(js):
            nonlocal req_id
            req_id += 1
            mid = req_id
            await ws.send(json.dumps({
                "id": mid,
                "method": "Runtime.evaluate",
                "params": {"expression": js, "awaitPromise": True, "returnByValue": True}
            }))
            while True:
                msg = await ws.recv()
                d = json.loads(msg)
                if d.get("id") == mid:
                    res = d.get("result", {}).get("result", {})
                    return res.get("value", res)

        async def capture_screen(filename):
            await ws.send(json.dumps({"id": 9999, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
            while True:
                msg = await ws.recv()
                d = json.loads(msg)
                if d.get("id") == 9999:
                    img_data = d.get("result", {}).get("data")
                    out_p = SCREENSHOTS_DIR / filename
                    with open(out_p, "wb") as f:
                        f.write(base64.b64decode(img_data))
                    print(f"Captured screenshot: {out_p}")
                    return out_p

        print("Waiting 4s for chart data & websocket feed to settle...")
        await asyncio.sleep(4.0)

        # 1. Check Initial State & Price Synchronization
        init_state = await evaluate("""
        (() => {
            const headerPrice = document.getElementById("ticker-price")?.textContent?.trim() || "";
            const activeWatchlistPrice = document.querySelector("#watchlist-tbody tr.active td.font-mono")?.textContent?.trim() || "";
            const hudClose = document.getElementById("hud-c")?.textContent?.trim() || "";
            const chart = window.chartManager?.charts[0];
            const lastCandle = chart?.allCandles?.slice(-1)[0] || null;
            const hasVerticalLines = document.querySelectorAll("#drawing-canvas-overlay line[stroke='#a855f7']").length;
            const autoZonesCount = document.querySelectorAll("#chart-auto-zones-group rect").length;
            const sessionRectsCount = document.querySelectorAll("#chart-sessions-overlay rect").length;

            return {
                headerPrice,
                activeWatchlistPrice,
                hudClose,
                lastCandleClose: lastCandle?.close,
                barsCount: chart?.allCandles?.length,
                hasVerticalLines,
                autoZonesCount,
                sessionRectsCount,
                sessionIndicatorEnabled: window.indicatorsEngine?.isEnabled("session_shading"),
            };
        })()
        """)
        print("[AUDIT 1] Initial Chart State:", json.dumps(init_state, indent=2))
        await capture_screen("after_fix_01_initial_clean_chart.png")

        # 2. Test Enabling Session Shading explicitly
        print("\n[AUDIT 2] Enabling Session Shading explicitly...")
        await evaluate("""
        (() => {
            window.indicatorsEngine.toggle("session_shading");
            window.chartManager.charts[0].reloadActiveIndicators();
        })()
        """)
        await asyncio.sleep(1.0)
        session_state = await evaluate("""
        (() => {
            const rects = Array.from(document.querySelectorAll("#chart-sessions-overlay rect")).map(r => ({
                x: parseFloat(r.getAttribute("x")),
                width: parseFloat(r.getAttribute("width")),
                height: parseFloat(r.getAttribute("height")),
                fill: r.getAttribute("fill")
            }));
            return {
                enabled: window.indicatorsEngine.isEnabled("session_shading"),
                rectsCount: rects.length,
                sampleRects: rects.slice(0, 4)
            };
        })()
        """)
        print("Session Shading Active:", json.dumps(session_state, indent=2))
        await capture_screen("after_fix_02_session_shading_enabled.png")

        # 3. Test Disabling Session Shading
        print("\n[AUDIT 3] Disabling Session Shading...")
        await evaluate("""
        (() => {
            window.indicatorsEngine.toggle("session_shading");
            window.chartManager.charts[0].reloadActiveIndicators();
        })()
        """)
        await asyncio.sleep(0.5)
        disabled_state = await evaluate("""
        (() => {
            return {
                enabled: window.indicatorsEngine.isEnabled("session_shading"),
                rectsCount: document.querySelectorAll("#chart-sessions-overlay rect").length
            };
        })()
        """)
        print("Session Shading Disabled:", json.dumps(disabled_state, indent=2))

        # 4. Symbol Switching: ETH/USDT, SOL/USDT, DOGE/USDT, BTC/USDT
        for sym in ["ETH/USDT", "SOL/USDT", "DOGE/USDT", "BTC/USDT"]:
            print(f"\n[AUDIT 4] Switching symbol to {sym}...")
            await evaluate(f"window.selectPair('{sym}')")
            await asyncio.sleep(3.0)
            st = await evaluate("""
            (() => {
                const c = window.chartManager.charts[0];
                const headerPrice = document.getElementById("ticker-price")?.textContent?.trim() || "";
                const wlPrice = document.querySelector("#watchlist-tbody tr.active td.font-mono")?.textContent?.trim() || "";
                return {
                    pair: c?.activePair,
                    bars: c?.allCandles?.length,
                    headerPrice,
                    wlPrice,
                    lastClose: c?.allCandles?.slice(-1)[0]?.close,
                    autoZones: document.querySelectorAll("#chart-auto-zones-group rect").length
                };
            })()
            """)
            print(f"State for {sym}:", json.dumps(st, indent=2))
            clean_sym = sym.replace("/", "").lower()
            await capture_screen(f"after_fix_03_symbol_{clean_sym}.png")

        # 5. Timeframe Switching: 1m, 5m, 15m, 1h, 4h
        for tf in ["1m", "5m", "15m", "1h", "4h"]:
            print(f"\n[AUDIT 5] Switching timeframe to {tf}...")
            await evaluate(f"window.selectTimeframe('{tf}')")
            await asyncio.sleep(2.5)
            tf_st = await evaluate("""
            (() => {
                const c = window.chartManager.charts[0];
                return {
                    timeframe: c?.timeframe,
                    bars: c?.allCandles?.length,
                    lastClose: c?.allCandles?.slice(-1)[0]?.close
                };
            })()
            """)
            print(f"State for {tf}:", json.dumps(tf_st, indent=2))

        # Switch back to 15m for final capture
        await evaluate("window.selectTimeframe('15m')")
        await asyncio.sleep(2.0)
        await capture_screen("after_fix_04_final_stable_15m.png")

        print("\nAll verification scenarios completed successfully!")

    if chrome_proc:
        chrome_proc.terminate()

if __name__ == "__main__":
    asyncio.run(audit())
