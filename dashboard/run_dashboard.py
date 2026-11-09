"""Runner script for AlphaQuant AI Dashboard."""

import sys
import webbrowser
from pathlib import Path

import uvicorn


# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dashboard.backend.config import config


def main():
    host = config.host or "127.0.0.1"
    port = config.port or 8000
    url = f"http://{host}:{port}"

    print("=" * 65)
    print("   🚀 AlphaQuant AI - Real-Time Trading & AI Chat Dashboard")
    print("=" * 65)
    print(f" * Local Web UI:    {url}")
    print(
        f" * AI Engine:       {config.ai_model} ({'API Key Configured' if config.gemini_api_key else 'Built-in Quant Engine (Add Gemini Key in UI)'})"
    )
    print(f" * Paper Portfolio: ${config.paper_balance:.2f} USDT")
    print("=" * 65)
    print("Starting server... Press Ctrl+C to stop.\n")

    # Open browser automatically after a short delay
    def open_browser():
        import time

        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    import threading

    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(
        "dashboard.backend.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
