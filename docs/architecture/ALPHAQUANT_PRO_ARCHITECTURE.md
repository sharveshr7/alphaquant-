# AlphaQuant AI Pro: System Architecture & Technical Specifications

## 1. System Overview

AlphaQuant AI Pro is a real-time quantitative trading and technical analysis platform designed for crypto asset trading, market structure profiling, and institutional supply/demand analytics.

The application pairs a high-throughput **FastAPI backend** with a **TradingView Lightweight Charts v5** client interface.

```
                           ┌───────────────────────────────┐
                           │    Binance Futures Public     │
                           │  WebSocket (@kline & @ticker) │
                           └───────────────┬───────────────┘
                                           │
                                           ▼
┌─────────────────────────┐       ┌─────────────────┐       ┌────────────────────────┐
│     FastAPI Backend     │       │ Browser Client  │       │   CoinGecko REST API   │
│ (127.0.0.1:8000)        │◄─────►│ (TradingView v5)│◄─────►│ (Market Cap & Metadata)│
│                         │ HTTP  │                 │ HTTP  │                        │
│ • S&D & SMC Engines     │  WS   │ • Candle Engine │       └────────────────────────┘
│ • Paper Trading Engine  │       │ • Vector Drawing│
│ • Technical Analysis    │       │ • Multi-TF HUD  │
└────────────┬────────────┘       └─────────────────┘
             │
             ▼
┌─────────────────────────┐
│     Persistent Data     │
│       (data/*.json)     │
│ • paper_trades.json     │
│ • signal_history.json   │
│ • drawings.json         │
└─────────────────────────┘
```

---

## 2. Backend Modules (`dashboard/backend/`)

| Module | Core Purpose |
|---|---|
| `app.py` | FastAPI application, HTTP REST endpoints, WebSocket live stream, static file serving |
| `config.py` | Environment variable resolution (`.env`), API key persistence |
| `market_data.py` | Binance Futures public REST (`/fapi/v1/`) and WebSocket connector |
| `market_scanner.py` | Real-time multi-pair scanning across 24h volume, volatility, and trend |
| `coin_analyzer.py` | Technical health scoring, momentum, and correlation profiles |
| `coingecko_provider.py` | CoinGecko token rankings, valuations, and disk cache manager |
| `technical_analysis.py` | Technical indicators: EMA, SMA, Bollinger Bands, RSI, MACD, Supertrend, ATR |
| `multi_timeframe.py` | Multi-timeframe confluence and trend alignment across 1m, 5m, 15m, 1h, 4h |
| `smc_engine.py` | Smart Money Concepts: Break of Structure (BOS), Change of Character (CHoCH), Order Blocks |
| `supply_demand.py` | Institutional Supply & Demand zones, distal/proximal boundaries |
| `liquidity_engine.py` | Liquidity pool discovery, buy/sell stops, and liquidity sweep detection |
| `signal_engine.py` | Multi-factor quantitative trade idea and confluence generation |
| `signal_tracker.py` | Signal lifecycle tracking (CONFIRMED, HIT_TP, HIT_SL, EXPIRED) |
| `strategy_engine.py` | Confluence evaluation checklist scoring |
| `risk_manager.py` | Position sizing, account balance percentage allocation, and risk limits |
| `paper_trader.py` | Dry-run simulated trading execution with real-time mark-to-market PnL |
| `backtest_engine.py` | Historical strategy execution against archived Binance Futures data |
| `ai_assistant.py` | Context-aware AI market analyst powered by Google Gemini |

---

## 3. Frontend Architecture (`dashboard/frontend/`)

* **Lightweight Charts v5 Engine (`js/chart.js`):**
  * Candlestick series with sub-second live tick updates.
  * Isolated `volume` price scale with `scaleMargins: { top: 0.82, bottom: 0 }`.
  * `autoscaleInfoProvider: () => null` on all overlays (Bollinger Bands, Ichimoku, Supertrend, Volume SMA, EMAs, SMAs, Anchored VWAP, and Session VWAP) to protect the main candlestick scale from vertical compression.
  * Backward historical pagination hook via REST `/api/candles` with timestamp-keyed deduplication.
* **Vector Drawing & Anchor Engine (`js/drawing_tools.js`):**
  * SVG vector overlay synchronized to chart price and timestamp coordinates.
  * Guaranteed timestamp extrapolation prevents null anchors from creating off-screen artifacts.
  * Supported tools: Trendline, Horizontal Line, Vertical Line, Ray, Rectangle, Fibonacci Retracement, Long/Short Position R:R, and Text Annotations.
* **WebSocket Live Feed (`js/live_feed.js`):**
  * Direct Binance USDⓈ-M Futures combined stream (`<symbol>@kline_<tf>/<symbol>@ticker`).
  * Automatic fallback proxy through backend (`/ws/live`) if direct public WebSocket is firewalled.
  * Exponential backoff and auto-reconnect listeners.
