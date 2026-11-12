# AlphaQuant AI Pro: Advanced AI Trading Terminal & Quant Platform

AlphaQuant AI Pro is a professional-grade quantitative crypto trading terminal featuring real-time Binance USDⓈ-M Futures data, institutional Smart Money Concepts (SMC) & Supply/Demand profiling, multi-timeframe analytics, paper trading, and an AI chat assistant powered by Google Gemini.

---

## 1. Directory Structure

```
C:\bot
│
├── dashboard/                          # Production Web Application
│   ├── backend/                        # FastAPI Application & Analytics Engines
│   │   ├── app.py                      # Application entrypoint & HTTP/WS routes
│   │   ├── config.py                   # Centralized configuration resolver
│   │   ├── ai_assistant.py             # Gemini AI Contextual Analysis Assistant
│   │   ├── market_data.py              # Binance Futures REST & WebSocket data provider
│   │   ├── market_scanner.py           # Real-time multi-pair market scanner
│   │   ├── coin_analyzer.py            # Deep token technical & on-chain analysis
│   │   ├── coingecko_provider.py       # CoinGecko market cap & metadata provider
│   │   ├── technical_analysis.py       # Technical indicator suite (EMA, BB, RSI, etc.)
│   │   ├── multi_timeframe.py          # Multi-timeframe confluence engine
│   │   ├── order_flow.py               # Orderbook depth & CVD delta calculations
│   │   ├── smc_engine.py               # Smart Money Concepts (BOS, CHoCH, OB)
│   │   ├── supply_demand.py            # Institutional supply/demand zone detector
│   │   ├── liquidity_engine.py         # Liquidity pool & sweep detector
│   │   ├── signal_engine.py            # Quantitative signal generator
│   │   ├── signal_tracker.py           # Signal performance & lifecycle tracker
│   │   ├── strategy_engine.py          # Confluence evaluation checklist
│   │   ├── risk_manager.py             # Position sizing & risk calculator
│   │   ├── paper_trader.py             # Simulated paper trading execution engine
│   │   └── backtest_engine.py          # Historical strategy backtester
│   │
│   ├── frontend/                       # Web Client Single-Page App
│   │   ├── index.html                  # Main terminal layout
│   │   ├── css/                        # Stylesheets (style.css)
│   │   └── js/                         # Frontend application modules
│   │       ├── lib/                    # Vendor libraries (Lightweight Charts, Marked)
│   │       ├── app.js                  # View router & application state
│   │       ├── chart.js                # Lightweight Charts v5 manager
│   │       ├── drawing_tools.js        # Vector SVG drawing & anchor engine
│   │       ├── live_feed.js            # Binance WebSocket client
│   │       ├── indicators_engine.js    # Client-side indicator overlay/subchart math
│   │       ├── indexeddb_cache.js      # Client 365-day candle cache
│   │       ├── chat.js                 # AI chat drawer interface
│   │       ├── coin_analysis.js        # Deep coin analysis view controller
│   │       ├── scanner.js              # Market scanner view controller
│   │       ├── signals_view.js         # Signals & trade ideas view controller
│   │       ├── strategy_view.js        # Strategy builder view controller
│   │       ├── watchlist.js            # Watchlist controller
│   │       ├── performance.js          # Paper trading performance analytics
│   │       └── settings_view.js        # User settings & API key management
│   │
│   └── run_dashboard.py                # Dashboard-local runner
│
├── config/                             # Central Configuration Hub
│   ├── settings.py                     # Python environment settings bridge
│   └── config_examples/                # Exchange & bot configuration templates
│
├── data/                               # Persistent Application JSON Store
│   ├── paper_trades.json               # Paper trading positions & trade history
│   ├── signal_history.json             # Generated signal database
│   └── drawings.json                   # User chart drawings & anchor coordinates
│
├── runtime/                            # Ephemeral Application Data
│   ├── cache/                          # Disk caches (CoinGecko top coins, tickers)
│   ├── logs/                           # Server application & diagnostic logs
│   └── temporary/                      # Chrome CDP profiles & temporary workspaces
│
├── tests/                              # Structured Test Hierarchy
│   ├── backend/                        # Backend API & service tests
│   │   └── test_dashboard_backend.py   # FastAPI endpoint & provider test suite
│   ├── frontend/                       # Chart & UI integration tests
│   │   └── test_live_chart_features.py # Frontend chart & HUD verification
│   ├── integration/                    # External exchange & network tests
│   │   └── test_futures_audit.py       # Binance Futures connectivity & volume audit
│   └── regression/                     # Regression & stability test suites
│       └── test_chart_regression.py    # 12-scenario stability & scale test suite
│
├── scripts/                            # Operational Scripts
│   ├── launch/                         # Startup batch & python scripts
│   ├── diagnostics/                    # WebSocket & REST diagnostic utilities
│   └── maintenance/                    # Repository validation & code checks
│
├── docs/                               # Documentation Suite
│   ├── architecture/                   # AlphaQuant AI Pro system & chart design
│   ├── setup/                          # Installation & environment configuration
│   └── reports/                        # Verification, audit & soak test reports
│
├── screenshots/                        # Curated UI & Validation Screenshots
│
├── archive/                            # Archived Legacy Backups (Read-Only)
│
├── adapters/                           # Agent model instructions
├── build_helpers/                      # Build & schema scripts
├── .env                                # Active environment configuration
├── .env.example                        # Template environment configuration
├── .gitignore                          # Git ignore rules
├── requirements.txt                    # Python dependencies
├── run.bat                             # Windows one-click launcher
└── run_dashboard.py                    # Root Python launcher
```

---

## 2. Installation & Setup

### Prerequisites
* Windows 10/11 (64-bit)
* Python 3.10 to 3.12 (Python 3.11 recommended)
* Google Chrome, Microsoft Edge, or another modern Chromium browser

### Environment Configuration (`.env`)
Copy `.env.example` to `.env` if not already present:

```env
# Google Gemini API Key for Context-Aware AI Chat Assistant
GEMINI_API_KEY=your_gemini_api_key_here

# AI Model Selection
AI_MODEL=gemini-flash-lite-latest

# Optional Binance API Keys (Binance public market data requires no keys)
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Initial Paper Portfolio Balance (USDT)
PAPER_BALANCE=1000.0

# Local Server Binding
HOST=127.0.0.1
PORT=8000
```

---

## 3. Launch Instructions

### Option 1: One-Click Windows Launcher (Recommended)
Double-click `run.bat` in the project root:
```cmd
cd C:\bot
run.bat
```

### Option 2: Virtual Environment Command
```cmd
cd C:\bot
.\.venv\Scripts\python.exe dashboard\run_dashboard.py
```

### Option 3: Root Python Launcher
```cmd
cd C:\bot
python run_dashboard.py
```

The terminal opens the dashboard automatically at:
**`http://127.0.0.1:8000`**

---

## 4. Architecture Overview

### Backend Architecture
* **FastAPI Async Server (`dashboard/backend/app.py`):** High-concurrency REST endpoints and WebSocket stream.
* **Binance Public Futures Connector (`dashboard/backend/market_data.py`):** Real-time `/fapi/v1/` klines, 24h ticker metrics, and combined WebSocket stream.
* **Institutional Engines:**
  * `supply_demand.py`: Automated proximal and distal demand/supply zone identification.
  * `smc_engine.py`: Break of Structure (BOS), Change of Character (CHoCH), Order Blocks, and fair-value liquidity voids.
  * `paper_trader.py`: In-memory paper execution with mark-to-market PnL and persistent JSON state.

### Frontend Architecture
* **TradingView Lightweight Charts v5 (`dashboard/frontend/js/chart.js`):**
  * Sub-second live candle rendering.
  * Isolated volume price scale (`scaleMargins: { top: 0.82, bottom: 0 }`).
  * `autoscaleInfoProvider: () => null` on all overlays, guaranteeing the candlestick price scale cannot be vertically distorted or compressed.
  * Backward pagination hook with timestamp deduplication.
* **Vector Drawing Engine (`dashboard/frontend/js/drawing_tools.js`):**
  * SVG vector overlay synchronized to chart (time, price) coordinates.
  * Guaranteed timestamp extrapolation prevents null anchors from creating off-screen artifacts.
  * Long/Short position tools with calculated Entry, Stop Loss, and Take Profit lines.

---

## 5. Persistent Data Locations

All state is preserved in `C:\bot\data/`:

| File | Purpose |
|---|---|
| `data/paper_trades.json` | Simulated account balance, open positions, and closed trade history |
| `data/signal_history.json` | Generated trade signals and lifecycle outcomes |
| `data/drawings.json` | User vector drawings (trendlines, boxes, positions) with chart coordinate anchors |

---

## 6. Testing Commands

```cmd
# Run backend service & endpoint tests
.\.venv\Scripts\python.exe -m unittest tests/backend/test_dashboard_backend.py

# Run live chart feature tests (server running on 8000)
.\.venv\Scripts\python.exe tests/frontend/test_live_chart_features.py

# Run Binance Futures integration audit
.\.venv\Scripts\python.exe -m unittest tests/integration/test_futures_audit.py

# Run full 12-scenario chart regression suite
.\.venv\Scripts\python.exe tests/regression/test_chart_regression.py
```

---

## 7. Troubleshooting

* **Chart Vertical Scale Glitch / Compression:**
  * Ensure all overlay series retain `autoscaleInfoProvider: () => null`.
  * Ensure volume series uses `priceScaleId: "volume"`.
* **Binance WebSocket Disconnection:**
  * The client automatically switches to the `/ws/live` backend proxy and reconnects to Binance with exponential backoff.
* **Gemini AI Responses:**
  * Verify `GEMINI_API_KEY` is present in `C:\bot\.env` or enter it directly in the UI via the Settings drawer.
