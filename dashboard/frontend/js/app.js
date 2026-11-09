/**
 * AlphaQuant Pro - Main TradingView Application Orchestrator
 *
 * Coordinates:
 * - Full-screen dominant TradingView Chart (~80% workspace width)
 * - Direct Binance Public WebSocket & Real-time Live Ticker
 * - Searchable Indicators Engine & Sub-Chart Oscillator Panels
 * - Right Drawer Tabs (Watchlist, Gemini AI, Precision Signals, Paper Execution)
 * - Drawing Tools Suite & "Analyze Zone" Gemini AI integration
 */

window.appState = {
  pair: "BTC/USDT",
  interval: "15m",
  currentPrice: 0,
  activeSidebarPage: "page-watchlist",
};

document.addEventListener("DOMContentLoaded", async () => {
  // 1. Initialize Core Engines
  window.chartManager = new WorkspaceChartManager();
  window.drawingTools = new DrawingToolsManager();
  window.liveFeed = new LiveFeed();

  // 2. Setup All UI Event Handlers
  setupHeaderControls();
  setupTimeframeButtons();
  setupRightTrayNavigation();
  setupBottomTray();
  setupWatchlistInteractions();
  setupPaperTrading();
  setupChatAssistant();
  setupSettingsModal();

  // 3. Initial Market Load
  await reloadMarketData();
  await reloadWatchlistTickers();

  // 4. Connect Live Binance WebSocket
  window.liveFeed.connect();
  window.liveFeed.onTick((data) => {
    if (data.ticker && data.ticker.last_price) {
      window.appState.currentPrice = data.ticker.last_price;
      updateWatchlistRow(window.appState.pair, data.ticker);
    }
    if (data.candle) {
      window.chartManager.updateLiveCandle(data.candle);
    }
  });

  // 5. Periodic Refresh for telemetry (every 10s)
  setInterval(() => {
    reloadWatchlistTickers();
    fetchActivePageData();
  }, 10000);
});

// --- HEADER CONTROLS ---

function setupHeaderControls() {
  // Symbol search / switch
  const symbolBtn = document.getElementById("btn-open-symbols");
  if (symbolBtn) {
    symbolBtn.addEventListener("click", () => {
      const current = window.appState.pair;
      const pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT"];
      const nextIdx = (pairs.indexOf(current) + 1) % pairs.length;
      switchSymbol(pairs[nextIdx]);
    });
  }

  // Return to live bar
  const btnReturnLive = document.getElementById("btn-return-live");
  if (btnReturnLive) {
    btnReturnLive.addEventListener("click", () => {
      if (window.chartManager) window.chartManager.scrollToLive();
    });
  }

  // Fullscreen
  const btnFullscreen = document.getElementById("btn-toggle-fullscreen");
  if (btnFullscreen) {
    btnFullscreen.addEventListener("click", () => {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen().catch(() => {});
      }
    });
  }

  // Settings Modal
  const btnSettings = document.getElementById("btn-open-settings");
  const modalSettings = document.getElementById("modal-settings");
  const btnCloseSettings = document.getElementById("btn-close-settings");
  const btnSaveSettings = document.getElementById("btn-save-settings");

  if (btnSettings && modalSettings) {
    btnSettings.addEventListener("click", () => modalSettings.classList.add("active"));
  }
  if (btnCloseSettings && modalSettings) {
    btnCloseSettings.addEventListener("click", () => modalSettings.classList.remove("active"));
  }
  if (btnSaveSettings) {
    btnSaveSettings.addEventListener("click", async () => {
      const key = document.getElementById("setting-gemini-key").value;
      const model = document.getElementById("setting-ai-model").value;
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gemini_api_key: key, ai_model: model }),
      });
      modalSettings.classList.remove("active");
    });
  }

  // Layout Buttons (1, 2, 4)
  const b1 = document.getElementById("btn-layout-1");
  const b2 = document.getElementById("btn-layout-2");
  const b4 = document.getElementById("btn-layout-4");

  const setLayout = (activeBtn, layoutId) => {
    [b1, b2, b4].forEach((b) => b && b.classList.remove("active"));
    if (activeBtn) activeBtn.classList.add("active");
    if (window.chartManager) {
      window.chartManager.setLayout(layoutId);
    }
  };

  if (b1) b1.addEventListener("click", () => setLayout(b1, "layout-1"));
  if (b2) b2.addEventListener("click", () => setLayout(b2, "layout-2"));
  if (b4) b4.addEventListener("click", () => setLayout(b4, "layout-4"));
}

function setupTimeframeButtons() {
  const tfButtons = document.querySelectorAll(".tv-tf-btn[data-tf]");
  tfButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tfButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tf = btn.getAttribute("data-tf");
      window.appState.interval = tf;
      document.getElementById("hud-symbol").textContent = `${window.appState.pair} ${tf}`;
      reloadMarketData();
    });
  });
}

function switchSymbol(symbol) {
  window.appState.pair = symbol;
  const symbolTxt = document.getElementById("header-symbol-text");
  if (symbolTxt) symbolTxt.textContent = symbol;
  const hudSym = document.getElementById("hud-symbol");
  if (hudSym) hudSym.textContent = `${symbol} ${window.appState.interval}`;

  // Highlight watchlist row
  document.querySelectorAll("#watchlist-tbody tr").forEach((tr) => {
    tr.classList.toggle("active", tr.getAttribute("data-symbol") === symbol);
  });

  reloadMarketData();
}

async function reloadMarketData() {
  const { pair, interval } = window.appState;
  if (window.liveFeed) {
    window.liveFeed.subscribe(pair, interval);
  }
  if (window.chartManager) {
    await window.chartManager.reloadAllCharts();
  }
  if (window.drawingTools) {
    window.drawingTools.loadDrawings();
  }
}

// --- RIGHT TRAY & DRAWER NAVIGATION ---

function setupRightTrayNavigation() {
  const trayButtons = document.querySelectorAll(".tv-tray-icon");
  const drawer = document.getElementById("sidebar-drawer");

  trayButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const pageId = btn.getAttribute("data-page");

      // If clicked on currently active tray icon, toggle collapsed
      if (btn.classList.contains("active") && !drawer.classList.contains("collapsed")) {
        drawer.classList.add("collapsed");
        btn.classList.remove("active");
        setTimeout(() => window.chartManager.resize(), 200);
        return;
      }

      drawer.classList.remove("collapsed");
      trayButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");

      document.querySelectorAll(".sidebar-page").forEach((p) => p.classList.remove("active"));
      const targetPage = document.getElementById(pageId);
      if (targetPage) targetPage.classList.add("active");

      window.appState.activeSidebarPage = pageId;
      setTimeout(() => window.chartManager.resize(), 200);

      fetchActivePageData();
    });
  });
}

function fetchActivePageData() {
  const page = window.appState.activeSidebarPage;
  if (page === "page-signals") loadSignalsData();
  else if (page === "page-watchlist") reloadWatchlistTickers();
}

async function loadSignalsData() {
  const container = document.getElementById("signals-container");
  if (!container) return;

  try {
    const res = await fetch(`/api/signals?pair=${encodeURIComponent(window.appState.pair)}&interval=${window.appState.interval}`);
    if (res.ok) {
      const d = await res.json();
      const plan = d.primary_plan || {};
      const score = plan.setup_score || d.setup_score || 70;
      const entry = plan.entry_price || d.entry_price;
      const sl = plan.stop_loss || d.stop_loss;
      const tp1 = plan.tp1 || d.tp1;
      const tp2 = plan.tp2 || d.tp2;
      const tp3 = plan.tp3 || d.tp3;

      container.innerHTML = `
        <div style="background:var(--tv-card); padding:12px; border-radius:6px; border:1px solid var(--tv-border); margin-bottom:10px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <strong style="color:var(--tv-blue); font-size:0.85rem;">${d.primary_model_name || 'MODEL_B_CONFIRMATION'}</strong>
            <span class="badge ${score >= 70 ? 'badge-emerald' : 'badge-amber'}">${score}/100</span>
          </div>
          <div style="font-family:var(--font-mono); font-size:0.78rem; display:flex; flex-direction:column; gap:6px;">
            <div style="display:flex; justify-content:space-between;">
              <span>Status:</span>
              <strong>${d.state || 'WAITING'}</strong>
            </div>
            <div style="display:flex; justify-content:space-between;">
              <span>Direction:</span>
              <strong class="${d.direction === 'LONG' ? 'up-color' : d.direction === 'SHORT' ? 'down-color' : ''}">${d.direction || 'NEUTRAL'}</strong>
            </div>
            <div style="display:flex; justify-content:space-between;">
              <span>Entry:</span>
              <strong>$${entry ? Number(entry).toLocaleString(undefined, {minimumFractionDigits: 2}) : '--'}</strong>
            </div>
            <div style="display:flex; justify-content:space-between;">
              <span>Stop Loss:</span>
              <strong class="down-color">$${sl ? Number(sl).toLocaleString(undefined, {minimumFractionDigits: 2}) : '--'}</strong>
            </div>
            <div style="display:flex; justify-content:space-between;">
              <span>TP1 / TP2:</span>
              <strong class="up-color">$${tp1 ? Number(tp1).toLocaleString(undefined, {minimumFractionDigits: 2}) : '--'} / $${tp2 ? Number(tp2).toLocaleString(undefined, {minimumFractionDigits: 2}) : '--'}</strong>
            </div>
            <div style="display:flex; justify-content:space-between;">
              <span>Risk/Reward:</span>
              <strong>1 : ${plan.risk_reward || '2.5'}</strong>
            </div>
          </div>
        </div>
      `;
    }
  } catch (e) {
    console.warn("Signals error:", e);
  }
}

// --- WATCHLIST ---

function setupWatchlistInteractions() {
  document.querySelectorAll("#watchlist-tbody tr").forEach((tr) => {
    tr.addEventListener("click", () => {
      const sym = tr.getAttribute("data-symbol");
      if (sym) switchSymbol(sym);
    });
  });
}

async function reloadWatchlistTickers() {
  try {
    const res = await fetch("/api/ticker");
    if (!res.ok) return;
    const tickers = await res.json();
    if (Array.isArray(tickers)) {
      tickers.forEach((t) => {
        updateWatchlistRow(t.pair, t);
      });
    }
  } catch (e) {}
}

function updateWatchlistRow(pair, ticker) {
  const clean = pair.replace(/[\/\-_]/g, "").toLowerCase();
  let prefix = "";
  if (clean.startsWith("btc")) prefix = "btc";
  else if (clean.startsWith("eth")) prefix = "eth";
  else if (clean.startsWith("sol")) prefix = "sol";
  else if (clean.startsWith("bnb")) prefix = "bnb";
  else if (clean.startsWith("xrp")) prefix = "xrp";
  else if (clean.startsWith("doge")) prefix = "doge";
  else if (clean.startsWith("ada")) prefix = "ada";
  else if (clean.startsWith("avax")) prefix = "avax";

  if (!prefix) return;

  const priceEl = document.getElementById(`wl-${prefix}-p`);
  const changeEl = document.getElementById(`wl-${prefix}-c`);

  if (priceEl && ticker.last_price) {
    priceEl.textContent = `$${Number(ticker.last_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
  }
  if (changeEl && ticker.price_change_percent !== undefined) {
    const isUp = ticker.price_change_percent >= 0;
    changeEl.textContent = `${isUp ? "+" : ""}${ticker.price_change_percent.toFixed(2)}%`;
    changeEl.className = isUp ? "up-color" : "down-color";
  }
}

// --- BOTTOM TRAY ---

function setupBottomTray() {
  const btnOsc = document.getElementById("btn-bottom-subcharts");
  const subContainer = document.getElementById("tv-subcharts-container");

  if (btnOsc && subContainer) {
    btnOsc.addEventListener("click", () => {
      const isVisible = subContainer.style.display !== "none";
      subContainer.style.display = isVisible ? "none" : "flex";
      btnOsc.classList.toggle("active", !isVisible);
      setTimeout(() => window.chartManager.resize(), 50);
    });
  }
}

// --- PAPER TRADING ---

function setupPaperTrading() {
  const btnBuy = document.getElementById("btn-quick-long");
  const btnSell = document.getElementById("btn-quick-short");
  const amountInput = document.getElementById("trade-amount");

  const executeTrade = async (side) => {
    const amt = parseFloat(amountInput.value) || 100;
    const curPrice = window.appState.currentPrice || 65000;
    try {
      const res = await fetch("/api/paper/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pair: window.appState.pair,
          side: side,
          amount_usdt: amt,
          price: curPrice,
        }),
      });
      if (res.ok) {
        alert(`Simulated Paper ${side} Order Executed for $${amt} @ $${curPrice}`);
        reloadPortfolio();
      }
    } catch (e) {}
  };

  if (btnBuy) btnBuy.addEventListener("click", () => executeTrade("BUY"));
  if (btnSell) btnSell.addEventListener("click", () => executeTrade("SELL"));
}

async function reloadPortfolio() {
  try {
    const res = await fetch("/api/paper/summary");
    if (res.ok) {
      const d = await res.json();
      const balEl = document.getElementById("header-balance");
      if (balEl && d.balance) {
        balEl.textContent = `$${Number(d.balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
      }
    }
  } catch (e) {}
}

// --- CHAT ASSISTANT ---

function setupChatAssistant() {
  const sendBtn = document.getElementById("btn-chat-send");
  const input = document.getElementById("chat-input-field");
  const container = document.getElementById("chat-messages-container");

  const send = async () => {
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";

    const userDiv = document.createElement("div");
    userDiv.className = "message-bubble user";
    userDiv.textContent = msg;
    container.appendChild(userDiv);

    const loadingDiv = document.createElement("div");
    loadingDiv.className = "message-bubble ai";
    loadingDiv.innerHTML = "<em>Analyzing market context with Gemini AI...</em>";
    container.appendChild(loadingDiv);
    container.scrollTop = container.scrollHeight;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          pair: window.appState.pair,
          interval: window.appState.interval,
        }),
      });
      if (res.ok) {
        const d = await res.json();
        const contentHtml = typeof marked !== "undefined" ? marked.parse(d.response || "") : (d.response || "");
        const modelBadge = d.model_used ? `<div style="margin-top:8px; font-size:0.7rem; color:var(--tv-blue); font-family:var(--font-mono);">⚡ ${d.model_used}</div>` : "";
        loadingDiv.innerHTML = contentHtml + modelBadge;
      } else {
        const errData = await res.json().catch(() => ({}));
        loadingDiv.innerHTML = `<span class="down-color">AI Assistant Error: ${errData.detail || 'Could not fetch response'}</span>`;
      }
    } catch (e) {
      loadingDiv.textContent = "Error connecting to AI assistant.";
    }
    container.scrollTop = container.scrollHeight;
  };

  if (sendBtn) sendBtn.addEventListener("click", send);
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") send();
    });
  }
}

function setupSettingsModal() {
  fetch("/api/settings")
    .then((r) => r.json())
    .then((d) => {
      const modelSelect = document.getElementById("setting-ai-model");
      if (modelSelect && d.ai_model) modelSelect.value = d.ai_model;
    })
    .catch(() => {});
}
