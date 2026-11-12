"""Enterprise runner for AlphaQuant AI Dashboard with port conflict guard and PID management."""

import argparse
import atexit
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# Safe fallback for headless pythonw execution where stdout/stderr are None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dashboard.backend.config import config
from dashboard.backend.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("server")

PID_FILE = PROJECT_ROOT / "runtime" / "alphaquant.pid"


def check_port_status(host: str, port: int) -> tuple[str, dict | None]:
    """Check if the target port is free, occupied by AlphaQuant, or occupied by another app.
    
    Returns:
        ('free', None)
        ('alphaquant', health_data_dict)
        ('foreign', None)
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        res = s.connect_ex((host, port))
        if res != 0:
            return "free", None

    # Port is in use, probe /api/health
    url = f"http://{host}:{port}/api/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AlphaQuant-Preflight/1.0"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("app") == "AlphaQuant AI Pro Trading Terminal":
                    return "alphaquant", data
    except Exception:
        pass

    return "foreign", None


def write_pid():
    """Records server PID to runtime/alphaquant.pid."""
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write PID file: %s", exc)


def cleanup_pid():
    """Removes runtime/alphaquant.pid on clean shutdown."""
    try:
        if PID_FILE.exists():
            content = PID_FILE.read_text(encoding="utf-8").strip()
            if content == str(os.getpid()):
                PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="AlphaQuant AI Pro Trading Dashboard Runner")
    parser.add_argument("--host", default=None, help="Host to bind (default from config/127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default from config/8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser on startup")
    parser.add_argument("--check-only", action="store_true", help="Only verify port and health status, then exit")
    args = parser.parse_args()

    host = args.host or config.host or "127.0.0.1"
    port = args.port or config.port or 8000
    url = f"http://{host}:{port}"

    # 1. Pre-flight Port Guard Check
    status, health_data = check_port_status(host, port)

    if args.check_only:
        print(f"PORT_STATUS:{status}")
        if status == "alphaquant":
            print(json.dumps(health_data, indent=2))
            sys.exit(0)
        elif status == "free":
            print(f"Port {port} on {host} is free.")
            sys.exit(0)
        else:
            print(f"Port {port} is occupied by an unrelated application.")
            sys.exit(1)

    if status == "alphaquant":
        pid_info = health_data.get("pid") or (health_data.get("process") or {}).get("pid")
        print("=" * 65)
        print("   INFO: AlphaQuant AI Pro is ALREADY RUNNING")
        print("=" * 65)
        print(f" * URL:         {url}")
        print(f" * Active PID:  {pid_info}")
        print(f" * Status:      {health_data.get('status', 'ok').upper()}")
        print(f" * Market:      {health_data.get('market_status', 'UNKNOWN')}")
        print("=" * 65)
        print("Duplicate instance was NOT started. Exiting cleanly.")
        logger.info("AlphaQuant AI Pro already running at %s (PID %s). Exiting.", url, pid_info)
        sys.exit(0)

    elif status == "foreign":
        print("=" * 65)
        print("   ERROR: PORT OCCUPIED BY ANOTHER APPLICATION")
        print("=" * 65)
        print(f" * Port {port} is currently bound by another non-AlphaQuant application.")
        print(" * Please terminate the conflicting process or choose a different port:")
        print(f"     python dashboard\\run_dashboard.py --port {port + 1}")
        print("=" * 65)
        logger.critical("Port %d is already in use by another application. Startup aborted.", port)
        sys.exit(1)

    # 2. Free port -> Start Server
    write_pid()
    atexit.register(cleanup_pid)

    def handle_signal(sig, frame):
        logger.info("Received termination signal %s. Shutting down gracefully...", sig)
        cleanup_pid()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
    except Exception:
        pass

    print("=" * 65)
    print("   STARTING: AlphaQuant AI - Real-Time Trading & AI Chat Dashboard")
    print("=" * 65)
    print(f" * Local Web UI:    {url}")
    print(f" * Process PID:     {os.getpid()}")
    print(
        f" * AI Engine:       {config.ai_model} ({'API Key Configured' if config.gemini_api_key else 'Built-in Quant Engine (Add Gemini Key in UI)'})"
    )
    print(f" * Paper Portfolio: ${config.paper_balance:.2f} USDT")
    print("=" * 65)
    print("Starting server... Press Ctrl+C to stop.\n")
    logger.info("Starting AlphaQuant AI server on %s (PID: %d)", url, os.getpid())

    # Open browser automatically if not suppressed
    if not args.no_browser and os.environ.get("ALPHAQUANT_NO_BROWSER") != "1":
        def open_browser():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    try:
        uvicorn.run(
            "dashboard.backend.app:app",
            host=host,
            port=port,
            reload=False,
            log_level="info",
        )
    finally:
        cleanup_pid()


if __name__ == "__main__":
    main()
