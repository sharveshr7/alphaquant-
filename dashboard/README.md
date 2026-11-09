# AlphaQuant AI - Local Browser Trading & AI Chat Dashboard

A modern, local browser-based quantitative trading workstation and AI assistant running directly in `C:\bot`. It connects a live crypto market feed and the `SupplyDemandConfluence` strategy with real-time TradingView charts, technical indicators, and a conversational AI chat bot.

---

## 🌟 Key Features

1. **Live Interactive Candlestick Charts**:
   - Built with TradingView's official **Lightweight Charts v4.2+**.
   - Real-time candlestick and volume updates streamed via Binance public API.
   - Multi-asset support: `BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `BNB/USDT`, `DOGE/USDT`, `XRP/USDT`, etc.
   - Multi-timeframe switching: `1m`, `5m`, `15m`, `1h`, `4h`, `1D`.

2. **Supply & Demand Confluence Signals**:
   - Real-time computation of unbroken **Demand (Support)** and **Supply (Resistance)** zones with impulsive departure validation.
   - 8-criteria Confluence Checklist (0 to 8 score):
     1. Demand Zone Reaction
     2. HTF Trend (EMA 50 > EMA 200)
     3. Price above 50 EMA
     4. RSI filter (45–68)
     5. ADX trend strength (≥ 20)
     6. Bollinger midline confirmation
     7. MACD momentum reversal
     8. Positive money flow (CMF > 0.02)
   - Dynamic trade setup card: Recommended Entry, Stop Loss (3.5%), Target 1 (1.5R), Target 2 (2.5R).

3. **AI Quantitative Assistant ("AlphaQuant AI")**:
   - Conversational AI powered by **Google Gemini** (`gemini-2.5-flash` via the official `google-genai` SDK).
   - Automatically injects live asset price, 24h ticker, recent candles, technical indicators, zones, and strategy score into the AI context for grounded answers.
   - Built-in rule-based fallback: Works immediately even before entering an API key.
   - Quick prompt chips: *Trend Analysis*, *Key Zones*, *Check Signal*, *Risk / Reward*.

4. **Paper Trading Simulator (Dry Run)**:
   - Starting balance of **$1,000.00 USDT** (configurable).
   - One-click **Quick Long** and **Quick Short** execution directly from the chart toolbar.
   - Live tracking of open positions, unrealized PnL, win rate, and trade history.

5. **Settings & API Key Management**:
   - Visual settings modal to paste and save API keys safely to `.env`.
   - No server restart required when updating keys.

---

## 📁 Project Structure

```
C:\bot\dashboard\
├── backend\
│   ├── __init__.py
│   ├── config.py              # Environment variables (.env) & runtime settings
│   ├── market_data.py         # Binance public REST & WebSocket candle / ticker streamer
│   ├── technical_analysis.py  # EMA, RSI, MACD, Bollinger Bands, ATR, ADX, CMF, Zones
│   ├── strategy_engine.py     # SupplyDemandConfluence 8-point checklist & signal scorer
│   ├── ai_assistant.py        # Gemini AI reasoning engine + Quant fallback
│   ├── paper_trader.py        # Simulated trade manager (Balance, positions, PnL)
│   └── app.py                 # FastAPI server (REST endpoints, WebSocket, static files)
├── frontend\
│   ├── index.html             # Pro dark trading interface
│   ├── css\
│   │   └── style.css          # Dark financial glassmorphism styling
│   └── js\
│       ├── lib\
│       │   ├── lightweight-charts.js  # Offline / local TradingView chart library
│       │   └── marked.min.js          # Offline / local Markdown parser
│       ├── chart.js           # TradingView candlestick chart & indicator overlays
│       ├── live_feed.js       # Live ticker & WebSocket price streamer
│       ├── strategy_view.js   # Strategy checklist, score gauge & zone cards
│       ├── chat.js            # AI Chat UI with prompt chips & markdown bubbles
│       └── app.js             # Main orchestrator & paper trading controller
├── .env                       # Local environment file (API keys)
├── .env.example               # Template environment file
├── test_backend.py            # Automated end-to-end verification script
├── run_dashboard.py           # Python server runner (auto-opens browser)
└── run.bat                    # 1-click Windows batch launcher
```

---

## 🚀 How to Run

### Method 1: Double-Click (Easiest)
Simply double-click `run.bat` in `C:\bot\dashboard\`.

### Method 2: From PowerShell / Terminal
```powershell
cd C:\bot
.\.venv\Scripts\python.exe dashboard\run_dashboard.py
```

The server starts on `http://127.0.0.1:8000` and automatically opens your browser.

---

## 🔑 How to Add Your API Keys

You can add your **Gemini API Key** in either of two ways:

1. **Directly in the Web UI**:
   - Click the **⚙️ API & Settings** button in the top-right corner of the dashboard.
   - Paste your Gemini API key in the **Gemini API Key** input.
   - Select your preferred model (`gemini-2.5-flash`, `gemini-1.5-flash`, etc.).
   - Click **Save & Connect**. The AI chat assistant immediately switches to live Gemini AI!

2. **In `C:\bot\dashboard\.env`**:
   Open `dashboard\.env` and paste:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   AI_MODEL=gemini-2.5-flash
   ```
