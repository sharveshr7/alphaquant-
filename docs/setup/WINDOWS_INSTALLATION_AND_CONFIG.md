# AlphaQuant AI Pro: Windows Installation & Setup Guide

## 1. Prerequisites

* **Operating System:** Windows 10 or Windows 11 (64-bit)
* **Python Runtime:** Python 3.10 to 3.12 (Python 3.11 recommended)
* **Web Browser:** Google Chrome, Microsoft Edge, or Mozilla Firefox (modern Chromium browser recommended)

---

## 2. Directory Layout & Environments

The application operates from `C:\bot`:

```cmd
C:\bot
├── dashboard/               # Production dashboard backend & frontend
├── config/                  # Configuration & exchange templates
├── data/                    # Persistent trade, signal, and drawing data
├── runtime/                 # Ephemeral logs, caches, and temp files
├── tests/                   # Backend, frontend, integration & regression tests
├── scripts/                 # Launch, diagnostics, and maintenance tools
├── docs/                    # Architecture, setup, and verification reports
├── screenshots/             # Interface and stability screenshots
├── .env                     # User environment settings & API keys
├── run.bat                  # One-click Windows launcher
└── run_dashboard.py         # Standard Python launcher
```

---

## 3. Environment Configuration (`.env`)

Configure your environment settings in `C:\bot\.env`:

```env
# Google Gemini API Key for Context-Aware AI Chat Assistant
GEMINI_API_KEY=your_gemini_api_key_here

# AI Model Selection (gemini-flash-lite-latest recommended for high throughput)
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

## 4. Launching the Application

### Option A: One-Click Windows Launcher (Recommended)
Double-click:
```cmd
C:\bot\run.bat
```
*(Or run `.\run.bat` in Command Prompt / PowerShell).*

### Option B: Terminal Command with Virtual Environment
```cmd
cd C:\bot
.\.venv\Scripts\python.exe dashboard\run_dashboard.py
```

### Option C: Root Python Script
```cmd
cd C:\bot
python run_dashboard.py
```

The server starts immediately and opens your browser at:
`http://127.0.0.1:8000`

---

## 5. Running the Test Suite

```cmd
# Run backend service & endpoint tests
.\.venv\Scripts\python.exe -m unittest tests/backend/test_dashboard_backend.py

# Run live chart feature tests (with server running)
.\.venv\Scripts\python.exe tests/frontend/test_live_chart_features.py

# Run Binance Futures integration audit
.\.venv\Scripts\python.exe -m unittest tests/integration/test_futures_audit.py
```
