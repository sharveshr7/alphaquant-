/**
 * AlphaQuant AI Pro - Professional Cryptocurrency Intelligence Terminal Orchestrator
 *
 * Coordinates:
 * - 11 Main Terminal Sections & Navigation (Overview, Main Chart, Top 100, Scanner, AI Analysis, Signals, Watchlist, Paper, Backtest, Performance, Settings)
 * - Main Candlestick Chart Workspace (~80% width with Lightweight Charts v5)
 * - Public Binance USDⓈ-M Futures WebSocket & REST API
 * - 17-Column Quantitative Market Scanner & 14 Dynamic Filters
 * - Individual Coin Multi-Timeframe Analysis & Exact Institutional Trade Plans
 * - Signal Tracking & Performance Analytics
 * - Simulated Paper Trading & Historical Backtesting
 */

window.appState = {
  currentView: "view-chart",
  currentPair: "BTC/USDT",
  currentInterval: "15m",
  currentPrice: 0,
  connectionStatus: "DISCONNECTED",
  lastTickTimestamp: 0,
  watchlist: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "NEAR/USDT"],
};

// Aliases for compatibility
Object.defineProperty(window.appState, "pair", {
  get() { return this.currentPair; },
  set(v) { this.currentPair = v; },
});
Object.defineProperty(window.appState, "interval", {
  get() { return this.currentInterval; },
  set(v) { this.currentInterval = v; },
});

// ============================================================================
// 1. Navigation Controller (11 Sections)
// ============================================================================

function switchView(viewId) {
  const targetId = viewId.startsWith("view-") ? viewId : `view-${viewId}`;
  const targetEl = document.getElementById(targetId);
  if (!targetEl) return;

  // Toggle active view container
  document.querySelectorAll(".terminal-view").forEach((v) => v.classList.remove("active"));
  targetEl.classList.add("active");

  // Toggle active top navigation tab
  document.querySelectorAll(".nav-tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-view") === targetId);
  });

  window.appState.currentView = targetId;

  // View-specific data fetching & initializers
  if (targetId === "view-chart") {
    setTimeout(() => {
      if (window.chartManager) window.chartManager.resize();
    }, 50);
  } else if (targetId === "view-overview") {
    loadOverviewData();
  } else if (targetId === "view-top100") {
    loadTop100Data();
  } else if (targetId === "view-scanner") {
    loadScannerData();
  } else if (targetId === "view-ai-analysis") {
    loadCoinAnalysisData();
  } else if (targetId === "view-signals") {
    loadSignalsData();
  } else if (targetId === "view-watchlist") {
    loadWatchlistData();
  } else if (targetId === "view-paper-trading") {
    loadPaperTradingData();
  } else if (targetId === "view-performance") {
    loadPerformanceData();
  } else if (targetId === "view-settings") {
    loadSettingsData();
  }
}

window.switchView = switchView;

// ============================================================================
// 2. Active Pair & Timeframe Switchers
// ============================================================================

function fmtUsd(val) {
  if (val === null || val === undefined || isNaN(val)) return "--";
  const n = Number(val);
  const absN = Math.abs(n);
  const dec = absN === 0 ? 2 : absN < 0.01 ? 6 : absN < 1 ? 5 : absN < 50 ? 4 : 2;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
}
window.fmtUsd = fmtUsd;

function selectPair(pair, targetView = null, interval = null) {
  if (!pair) return;
  if (interval && interval !== window.appState.currentInterval) {
    window.appState.currentInterval = interval;
    try { localStorage.setItem("alphaquant_interval", interval); } catch (e) {}
    document.querySelectorAll(".tv-tf-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-tf") === interval);
    });
    const aiSelect = document.getElementById("ai-timeframe-select");
    if (aiSelect) aiSelect.value = interval;
    const chatTfLabel = document.getElementById("chat-tf-label");
    if (chatTfLabel) chatTfLabel.textContent = interval;
  }
  window.appState.currentPair = pair;
  try { localStorage.setItem("alphaquant_pair", pair); } catch (e) {}

  // Update header text
  const symbolTxt = document.getElementById("header-symbol-text");
  if (symbolTxt) symbolTxt.textContent = pair;

  // Update chart HUD
  const hudSym = document.getElementById("hud-symbol");
  if (hudSym) hudSym.textContent = `${pair} ${window.appState.currentInterval}`;

  // Update AI Analysis header
  const aiTitle = document.getElementById("ai-coin-title");
  if (aiTitle) aiTitle.textContent = pair;

  // Update chat & paper labels
  const chatPairLabel = document.getElementById("chat-pair-label");
  if (chatPairLabel) chatPairLabel.textContent = pair;
  const chatTfLabel = document.getElementById("chat-tf-label");
  if (chatTfLabel) chatTfLabel.textContent = window.appState.currentInterval;
  const paperOrderLabel = document.getElementById("paper-order-pair-label");
  if (paperOrderLabel) paperOrderLabel.textContent = pair;

  // Highlight active row in Right Dock watchlist
  document.querySelectorAll("#watchlist-tbody tr").forEach((tr) => {
    tr.classList.toggle("active", tr.getAttribute("data-symbol") === pair);
  });

  // Re-subscribe WebSocket
  if (window.liveFeed) {
    window.liveFeed.subscribe(pair, window.appState.currentInterval);
  }

  // Reload chart candles, drawings & Right Dock summary
  if (window.chartManager) {
    window.chartManager.reloadAllCharts();
  }
  if (window.drawingTools) {
    window.drawingTools.loadDrawings();
  }
  refreshDockPairSignal();

  if (window.appState.currentView === "view-ai-analysis") {
    loadCoinAnalysisData();
  }

  if (targetView) {
    switchView(targetView);
  }
}

window.selectPair = selectPair;

function selectTimeframe(tf) {
  if (!tf) return;
  window.appState.currentInterval = tf;
  try { localStorage.setItem("alphaquant_interval", tf); } catch (e) {}

  document.querySelectorAll(".tv-tf-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-tf") === tf);
  });

  const aiSelect = document.getElementById("ai-timeframe-select");
  if (aiSelect) aiSelect.value = tf;

  const chatTfLabel = document.getElementById("chat-tf-label");
  if (chatTfLabel) chatTfLabel.textContent = tf;

  const hudSym = document.getElementById("hud-symbol");
  if (hudSym) hudSym.textContent = `${window.appState.currentPair} ${tf}`;

  if (window.liveFeed) {
    window.liveFeed.subscribe(window.appState.currentPair, tf);
  }

  if (window.chartManager) {
    window.chartManager.reloadAllCharts();
  }
  if (window.drawingTools) {
    window.drawingTools.loadDrawings();
  }
  refreshDockPairSignal();

  if (window.appState.currentView === "view-ai-analysis") {
    loadCoinAnalysisData();
  }
}

window.selectTimeframe = selectTimeframe;

// ============================================================================
// 3. Application Startup Pipeline
// ============================================================================

document.addEventListener("DOMContentLoaded", async () => {
  // Restore persisted chart layout (pair + timeframe)
  try {
    const savedPair = localStorage.getItem("alphaquant_pair");
    const savedTf = localStorage.getItem("alphaquant_interval");
    if (savedPair) window.appState.currentPair = savedPair;
    if (savedTf) window.appState.currentInterval = savedTf;
  } catch (e) {}

  document.querySelectorAll(".tv-tf-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-tf") === window.appState.currentInterval);
  });

  // 1. Setup Navigation Tabs
  document.querySelectorAll(".nav-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const viewId = btn.getAttribute("data-view");
      if (viewId) switchView(viewId);
    });
  });

  // 2. Initialize Core Engines
  if (typeof WorkspaceChartManager !== "undefined") {
    window.chartManager = new WorkspaceChartManager();
  }
  if (typeof DrawingToolsManager !== "undefined") {
    window.drawingTools = new DrawingToolsManager();
  }
  if (typeof LiveFeed !== "undefined") {
    window.liveFeed = new LiveFeed();
  }
  if (typeof ChatAssistant !== "undefined") {
    window.chatAssistant = new ChatAssistant();
  }

  // 3. Setup Controls across views
  setupHeaderControls();
  setupChartToolbar();
  setupRightDockControls();
  setupTop100AndOverviewControls();
  setupScannerControls();
  setupAiAnalysisControls();
  setupSignalsControls();
  setupWatchlistControls();
  setupPaperTradingControls();
  setupBacktestingControls();
  setupSettingsControls();

  // 4. Initial Load (selectPair already subscribes liveFeed + loads chart & drawings)
  selectPair(window.appState.currentPair);
  loadOverviewData();
  setInterval(() => {
    const overview = document.getElementById("view-overview");
    if (overview && overview.classList.contains("active")) {
      refreshOverviewTelemetry(false).catch((error) =>
        console.error("Error refreshing overview telemetry:", error)
      );
    }
  }, 60000);
  refreshDockWatchlist();
  refreshDockQuickPositions();
  fetch("/api/pairs")
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (d && Array.isArray(d.pairs) && allDiscoveredPairsCache.length === 0) {
        allDiscoveredPairsCache = d.pairs.map((pair) => ({
          pair_name: pair,
          coin_name: pair.split("/")[0],
          last_price: null,
          change_24h_pct: null,
        }));
      }
    })
    .catch(() => {});

  // 5. Bind Live WebSocket Tick Listener
  if (window.liveFeed) {
    window.liveFeed.onTick((data) => {
      handleLiveTick(data);
    });
    window.liveFeed.onSignalEvent((message) => {
      handleLiveSignalEvent(message);
    });
  }

  // 6. Global Window Resize
  window.addEventListener("resize", () => {
    if (window.appState.currentView === "view-chart" && window.chartManager) {
      window.chartManager.resize();
    }
  });

  // 7. Watchdog timer for connection status and telemetry clock
  setInterval(updateTelemetryTick, 1000);
  setInterval(refreshDockWatchlist, 15000);
});

// ============================================================================
// 4. Header, Searchable Symbol Modal & Telemetry Controls
// ============================================================================

let allDiscoveredPairsCache = [];
let symbolModalScannerEnriched = false;

async function openSymbolPickerModal() {
  const modal = document.getElementById("modal-symbols");
  const container = document.getElementById("symbol-list-container");
  const searchInput = document.getElementById("symbol-search-input");
  if (!modal || !container) return;

  modal.classList.add("active");
  if (searchInput) {
    searchInput.value = "";
    searchInput.focus();
  }

  if (allDiscoveredPairsCache.length === 0) {
    try {
      const pRes = await fetch("/api/pairs");
      if (pRes.ok) {
        const pData = await pRes.json();
        allDiscoveredPairsCache = (pData.pairs || []).map((pair) => ({
          pair_name: pair,
          coin_name: pair.split("/")[0],
          last_price: null,
          change_24h_pct: null,
        }));
      }
    } catch (e) {}
  }

  renderSymbolModalList(searchInput ? searchInput.value : "");

  if (!symbolModalScannerEnriched) {
    fetch("/api/scanner")
      .then((sRes) => (sRes.ok ? sRes.json() : null))
      .then((sData) => {
        if (!sData || !Array.isArray(sData.pairs)) return;
        const pMap = new Map();
        sData.pairs.forEach((p) => {
          pMap.set(p.pair_name || p.symbol, p);
        });
        allDiscoveredPairsCache.forEach((existing) => {
          const key = existing.pair_name || existing.symbol;
          if (!pMap.has(key)) pMap.set(key, existing);
        });
        allDiscoveredPairsCache = Array.from(pMap.values());
        symbolModalScannerEnriched = true;
        if (modal.classList.contains("active")) {
          renderSymbolModalList(searchInput ? searchInput.value : "");
        }
      })
      .catch(() => {});
  }
}

function renderSymbolModalList(query = "") {
  const container = document.getElementById("symbol-list-container");
  if (!container) return;
  const q = query.toLowerCase().trim();
  const filtered = allDiscoveredPairsCache.filter((p) => {
    const sym = (p.pair_name || p.symbol || "").toLowerCase();
    const coin = (p.coin_name || "").toLowerCase();
    return sym.includes(q) || coin.includes(q);
  }).slice(0, 120);

  container.innerHTML = "";
  if (filtered.length === 0) {
    container.innerHTML = `<div class="text-center text-dim py-16">No Binance USDⓈ-M contracts found matching "${query}"</div>`;
    return;
  }

  filtered.forEach((p) => {
    const pairName = p.pair_name || p.symbol;
    const row = document.createElement("div");
    row.className = `symbol-item-row ${pairName === window.appState.currentPair ? "active" : ""}`;
    const chg = p.change_24h_pct !== null && p.change_24h_pct !== undefined ? parseFloat(p.change_24h_pct) : null;
    row.innerHTML = `
      <div>
        <strong class="font-mono">${pairName}</strong>
        <span class="fs-xs text-dim ml-4">${p.coin_name || ""}</span>
      </div>
      <div class="font-mono fs-xs" style="display:flex; gap:12px; align-items:center;">
        <span>${p.last_price ? fmtUsd(p.last_price) : ""}</span>
        ${chg !== null ? `<span class="${chg >= 0 ? 'text-green' : 'text-red'}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span>` : ""}
      </div>
    `;
    row.addEventListener("click", () => {
      document.getElementById("modal-symbols")?.classList.remove("active");
      selectPair(pairName);
    });
    container.appendChild(row);
  });
}

function setupHeaderControls() {
  const btnSymbols = document.getElementById("btn-open-symbols");
  if (btnSymbols) {
    btnSymbols.addEventListener("click", () => openSymbolPickerModal());
  }

  const modalSymbols = document.getElementById("modal-symbols");
  const btnCloseSymbols = document.getElementById("btn-close-symbols");
  const symbolSearch = document.getElementById("symbol-search-input");
  if (btnCloseSymbols && modalSymbols) {
    btnCloseSymbols.addEventListener("click", () => modalSymbols.classList.remove("active"));
  }
  if (modalSymbols) {
    modalSymbols.addEventListener("click", (e) => {
      if (e.target === modalSymbols) modalSymbols.classList.remove("active");
    });
  }
  if (symbolSearch) {
    symbolSearch.addEventListener("input", (e) => renderSymbolModalList(e.target.value));
  }

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
}

function handleLiveTick(data) {
  if (!data) return;
  const activePair = window.appState ? (window.appState.currentPair || window.appState.pair) : null;
  const activeTf = window.appState ? (window.appState.currentInterval || window.appState.interval) : null;
  if (data.pair && activePair && data.pair !== activePair) return;

  window.appState.lastTickTimestamp = Date.now();
  setConnectionStatus(data.connectionStatus || "LIVE");

  if (data.ticker && data.ticker.last_price) {
    const p = parseFloat(data.ticker.last_price);
    window.syncLivePrice(p, data.pair || activePair, data.ticker);
  }
}

window.syncLivePrice = function (price, pair = null, tickerData = null) {
  if (price === null || price === undefined || !Number.isFinite(Number(price))) return;
  const p = Number(price);
  if (p <= 0) return;

  const currentPair = window.appState ? (window.appState.currentPair || window.appState.pair) : null;
  const activePair = pair || currentPair;
  if (currentPair && activePair && activePair !== currentPair) return;

  if (window.appState) {
    window.appState.currentPrice = p;
    window.appState.lastTickTimestamp = Date.now();
  }

  // 1. Synchronize Top Header Price
  const priceEl = document.getElementById("ticker-price");
  if (priceEl) priceEl.textContent = fmtUsd(p);

  const qaPrice = document.getElementById("qa-price");
  if (qaPrice) qaPrice.textContent = fmtUsd(p);

  // 2. Synchronize Change Badge
  if (tickerData && tickerData.price_change_percent !== undefined) {
    const chgEl = document.getElementById("ticker-change");
    if (chgEl) {
      const chg = parseFloat(tickerData.price_change_percent);
      const isUp = chg >= 0;
      chgEl.textContent = `${isUp ? "+" : ""}${chg.toFixed(2)}%`;
      chgEl.className = `live-change-badge ${isUp ? "bg-up" : "bg-down"}`;
    }
  }

  // 3. Synchronize Active Watchlist Row Price in Right Dock
  const idMap = {
    "BTC/USDT": ["wl-btc-p", "wl-btc-c"],
    "ETH/USDT": ["wl-eth-p", "wl-eth-c"],
    "SOL/USDT": ["wl-sol-p", "wl-sol-c"],
    "BNB/USDT": ["wl-bnb-p", "wl-bnb-c"],
    "XRP/USDT": ["wl-xrp-p", "wl-xrp-c"],
    "DOGE/USDT": ["wl-doge-p", "wl-doge-c"],
    "ADA/USDT": ["wl-ada-p", "wl-ada-c"],
    "AVAX/USDT": ["wl-avax-p", "wl-avax-c"],
  };
  const ids = idMap[activePair];
  if (ids) {
    const pEl = document.getElementById(ids[0]);
    if (pEl) pEl.textContent = fmtUsd(p);
    if (tickerData && ids[1]) {
      const cEl = document.getElementById(ids[1]);
      if (cEl) {
        const chg = parseFloat(tickerData.price_change_percent);
        cEl.textContent = `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
        cEl.className = chg >= 0 ? "text-right text-green font-mono" : "text-right text-red font-mono";
      }
    }
  }

  // 4. Update Quick Order Price placeholder
  const tradePrice = document.getElementById("trade-price");
  if (tradePrice && !tradePrice.value) {
    tradePrice.placeholder = p.toFixed(p < 1 ? 4 : 2);
  }
};

function setConnectionStatus(status) {
  window.appState.connectionStatus = status;
  const dot = document.getElementById("conn-dot");
  const text = document.getElementById("conn-text");
  if (!dot || !text) return;

  text.style.color = "";
  dot.style.background = "";
  if (status === "LIVE") {
    dot.className = "status-dot bg-up";
    text.className = "up-color";
    text.textContent = "LIVE";
  } else if (status === "DELAYED") {
    dot.className = "status-dot bg-warning";
    text.className = "warning-color";
    text.textContent = "DELAYED";
  } else {
    dot.className = "status-dot bg-down";
    text.className = "down-color";
    text.textContent = "DISCONNECTED";
  }
}
window.setConnectionStatus = setConnectionStatus;

function updateTelemetryTick() {
  const timeEl = document.getElementById("last-update-time");
  if (timeEl) {
    timeEl.textContent = new Date().toLocaleTimeString();
  }

  const now = Date.now();
  if (window.appState.lastTickTimestamp > 0) {
    const age = now - window.appState.lastTickTimestamp;
    if (age > 12000) setConnectionStatus("DISCONNECTED");
    else if (age > 4500) setConnectionStatus("DELAYED");
  }
}

// ============================================================================
// 4B. Main Chart Right Dock & Top 100 / Overview Controls
// ============================================================================

function setupRightDockControls() {
  document.querySelectorAll("#watchlist-tbody tr[data-symbol]").forEach((tr) => {
    tr.addEventListener("click", () => {
      const sym = tr.getAttribute("data-symbol");
      if (sym) selectPair(sym);
    });
  });

  const btnQLong = document.getElementById("btn-quick-long");
  const btnQShort = document.getElementById("btn-quick-short");
  if (btnQLong) {
    btnQLong.addEventListener("click", () => {
      const amt = parseFloat(document.getElementById("trade-amount")?.value) || 100;
      executePaperTrade(window.appState.currentPair, "BUY", amt).then(() => refreshDockQuickPositions());
    });
  }
  if (btnQShort) {
    btnQShort.addEventListener("click", () => {
      const amt = parseFloat(document.getElementById("trade-amount")?.value) || 100;
      executePaperTrade(window.appState.currentPair, "SELL", amt).then(() => refreshDockQuickPositions());
    });
  }
}

async function refreshDockWatchlist() {
  try {
    const res = await fetch("/api/ticker");
    if (!res.ok) return;
    const tickers = await res.json();
    const tMap = new Map();
    if (Array.isArray(tickers)) {
      tickers.forEach((t) => tMap.set(t.pair, t));
    }
    const idMap = {
      "BTC/USDT": ["wl-btc-p", "wl-btc-c"],
      "ETH/USDT": ["wl-eth-p", "wl-eth-c"],
      "SOL/USDT": ["wl-sol-p", "wl-sol-c"],
      "BNB/USDT": ["wl-bnb-p", "wl-bnb-c"],
      "XRP/USDT": ["wl-xrp-p", "wl-xrp-c"],
      "DOGE/USDT": ["wl-doge-p", "wl-doge-c"],
      "ADA/USDT": ["wl-ada-p", "wl-ada-c"],
      "AVAX/USDT": ["wl-avax-p", "wl-avax-c"],
    };
    Object.entries(idMap).forEach(([pair, [pId, cId]]) => {
      const t = tMap.get(pair);
      if (!t) return;
      const pEl = document.getElementById(pId);
      const cEl = document.getElementById(cId);
      if (pEl) pEl.textContent = fmtUsd(t.last_price);
      if (cEl) {
        const chg = parseFloat(t.price_change_percent || 0);
        cEl.textContent = `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
        cEl.className = chg >= 0 ? "text-green font-mono" : "text-red font-mono";
      }
    });
  } catch (e) {}
}

async function refreshDockPairSignal() {
  const container = document.getElementById("signals-container");
  const badge = document.getElementById("signal-state-badge");
  if (!container) return;

  try {
    const pair = window.appState.currentPair;
    const tf = window.appState.currentInterval;
    const res = await fetch(`/api/coin-analysis?pair=${encodeURIComponent(pair)}&timeframe=${tf}`);
    if (!res.ok) return;
    const d = await res.json();
    const dec = d.decision || "NO TRADE";
    const plan = d.trade_plan || {};

    if (badge) {
      badge.textContent = dec;
      badge.className = `badge ${dec === "LONG" ? "badge-emerald" : dec === "SHORT" ? "badge-rose" : "badge-neutral"}`;
    }

    container.innerHTML = `
      <div class="analysis-card">
        <div class="analysis-metric-row"><span>State</span><strong>${d.lifecycle_state || "SCANNING"}</strong></div>
        <div class="analysis-metric-row"><span>MTF Bias</span><strong>${d.multi_timeframe?.overall_bias || "--"}</strong></div>
        <div class="analysis-metric-row"><span>Entry</span><strong class="font-mono">${plan.entry_price ? fmtUsd(plan.entry_price) : "N/A (No Trade)"}</strong></div>
        <div class="analysis-metric-row"><span>Stop Loss</span><strong class="font-mono text-red">${plan.stop_loss ? fmtUsd(plan.stop_loss) : "--"}</strong></div>
        <div class="analysis-metric-row"><span>TP1 / TP2</span><strong class="font-mono text-green">${plan.tp1 ? fmtUsd(plan.tp1) + " / " + fmtUsd(plan.tp2) : "--"}</strong></div>
      </div>
      <div class="explanation-block mt-8">
        <span class="exp-title text-blue">Next Action</span>
        <p class="fs-xs text-bright">${(plan.what_must_happen_next || ["Monitor key zones"])[0]}</p>
      </div>
      <button type="button" class="btn-terminal-primary mt-8" style="width:100%;" onclick="switchView('view-ai-analysis')">Open Full AI Coin Analysis →</button>
    `;
  } catch (e) {}
}

async function refreshDockQuickPositions() {
  const listEl = document.getElementById("open-positions-list");
  const balEl = document.getElementById("header-balance");
  try {
    const res = await fetch("/api/paper/summary");
    if (!res.ok) return;
    const d = await res.json();
    if (balEl && d.available_margin !== undefined) balEl.textContent = fmtUsd(d.available_margin);
    if (!listEl) return;
    const positions = d.open_positions || [];
    if (positions.length === 0) {
      listEl.innerHTML = `<span class="text-dim fs-xs">No active paper positions.</span>`;
      return;
    }
    listEl.innerHTML = "";
    positions.forEach((pos) => {
      const div = document.createElement("div");
      div.className = "analysis-card mt-8";
      const pnl = Number(pos.unrealized_pnl || 0);
      div.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <strong class="fs-xs">${pos.pair} (${pos.side})</strong>
          <span class="font-mono fs-xs ${pnl >= 0 ? 'text-green' : 'text-red'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</span>
        </div>
        <div class="fs-xs text-dim mt-4">Entry: ${fmtUsd(pos.entry_price)} | Size: $${pos.amount_usdt}</div>
      `;
      listEl.appendChild(div);
    });
  } catch (e) {}
}

function setupTop100AndOverviewControls() {
  const btnOver = document.getElementById("btn-refresh-overview");
  if (btnOver) btnOver.addEventListener("click", () => loadOverviewData());

  const btnTop100 = document.getElementById("btn-refresh-top100");
  if (btnTop100) btnTop100.addEventListener("click", () => loadTop100Data());

  const searchTop100 = document.getElementById("top100-search-input");
  if (searchTop100) {
    searchTop100.addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase().trim();
      const filtered = top100CachedPairs.filter((p) => {
        const s = (p.pair_name || p.symbol || "").toLowerCase();
        const c = (p.coin_name || "").toLowerCase();
        return s.includes(q) || c.includes(q);
      });
      renderTop100Table(filtered);
    });
  }

  const btnPerf = document.getElementById("btn-refresh-performance");
  if (btnPerf) btnPerf.addEventListener("click", () => loadPerformanceData());
}

// ============================================================================
// 5. Section 1: Overview
// ============================================================================

async function loadOverviewData() {
  const statUniverse = document.getElementById("stat-universe-count");
  const statSentiment = document.getElementById("stat-market-sentiment");
  const statSentimentSub = document.getElementById("stat-sentiment-sub");
  const statActiveSig = document.getElementById("stat-active-signals");
  const statVolume = document.getElementById("stat-total-volume");
  const tbodyMovers = document.getElementById("tbody-overview-movers");
  const signalsStream = document.getElementById("overview-signals-stream");

  try {
    const [marketRes, healthRes, activeRes, historyRes] = await Promise.all([
      fetch("/api/market-status"),
      fetch("/api/health"),
      fetch("/api/signals/active"),
      fetch("/api/signals/history?limit=500"),
    ]);
    const marketStatus = marketRes.ok ? await marketRes.json() : {};
    const health = healthRes.ok ? await healthRes.json() : {};
    const activeData = activeRes.ok ? await activeRes.json() : {};
    const activeSignals = activeData.signals || [];
    const activeSetups = activeData.setups || [];
    const signalHistory = historyRes.ok ? (await historyRes.json()).history || [] : [];

    const setTelemetry = (id, value) => {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    };
    const scanStatus = health.signal_scanner || health.scanner || {};
    const streamStatus = health.market_data?.market_stream || {};
    const staleSeconds = marketStatus.stale_seconds;
    const marketText = `${marketStatus.status || "UNKNOWN"} · ${
      staleSeconds === null || staleSeconds === undefined ? "freshness unknown" : `${staleSeconds}s since update`
    }${marketStatus.exchange_info_stale ? " · contract list stale" : ""}`;
    const wsState = window.liveFeed ? window.liveFeed.state : "NOT CONNECTED";
    const lastScan = scanStatus.last_completed_at
      ? `${new Date(scanStatus.last_completed_at * 1000).toLocaleTimeString()} · ${
          scanStatus.last_duration_ms ?? "--"
        }ms`
      : "Waiting for first scan";
    const earlyEligibleCount = marketStatus.pairs_count ?? health.market_data?.pairs_loaded ?? 0;
    setTelemetry("telemetry-eligible", String(earlyEligibleCount));
    setTelemetry("telemetry-market-data", marketText);
    setTelemetry("telemetry-websocket", wsState);
    setTelemetry(
      "telemetry-scanner",
      `${scanStatus.status || "WAITING"} · ${scanStatus.analyzed_contracts ?? 0} analyzed · ${scanStatus.candidate_contracts ?? 0} candidates · stream ${streamStatus.status || "STOPPED"} (${streamStatus.messages_received ?? 0} msgs) · signal latency ${scanStatus.last_analysis_latency_ms ?? "--"}ms`
    );
    setTelemetry("telemetry-last-scan", lastScan);
    setTelemetry(
      "telemetry-signals",
      `active ${activeSignals.length} · potential ${activeSetups.length} · expired ${
        signalHistory.filter((s) => s.close_reason === "EXPIRED").length
      } · invalidated ${signalHistory.filter((s) => s.close_reason === "INVALIDATED").length}`
    );

    const res = await fetch("/api/scanner");
    if (!res.ok) return;
    const data = await res.json();
    const pairs = data.pairs || [];

    if (statUniverse) {
      statUniverse.textContent = data.eligible_contract_count ?? data.total_discovered ?? pairs.length;
    }

    let bullCount = 0;
    let bearCount = 0;
    let sigCount = 0;
    let totalVol = 0;
    pairs.forEach((p) => {
      totalVol += (parseFloat(p.volume_24h_quote) || 0);
      if (p.trend === "Bullish") bullCount++;
      else if (p.trend === "Bearish") bearCount++;
      if (p.signal === "LONG" || p.signal === "SHORT") sigCount++;
    });

    const potentialCount = pairs.filter(
      (p) =>
        p.signal === "NO TRADE" &&
        p.analysis_stage === "STAGE_3_DEEP_ANALYZED" &&
        (p.sd !== "None" || p.structure !== "Neutral")
    ).length;
    const countertrendCount = activeSignals.filter((s) =>
      (s.setup_state || "").includes("COUNTERTREND")
    ).length;
    const expiredCount = signalHistory.filter((s) => s.close_reason === "EXPIRED").length;
    const invalidatedCount = signalHistory.filter((s) => s.close_reason === "INVALIDATED").length;
    const noTradeCount = pairs.filter((p) => p.signal === "NO TRADE").length;
    setTelemetry("telemetry-eligible", String(data.eligible_contract_count ?? data.total_discovered ?? pairs.length));
    setTelemetry("telemetry-market-data", marketText);
    setTelemetry("telemetry-websocket", wsState);
    setTelemetry(
      "telemetry-scanner",
      `${scanStatus.status || "WAITING"} · ${scanStatus.analyzed_contracts ?? pairs.length} analyzed · ${scanStatus.candidate_contracts ?? 0} candidates`
    );
    setTelemetry("telemetry-last-scan", lastScan);
    setTelemetry(
      "telemetry-signals",
      `LONG ${pairs.filter((p) => p.signal === "LONG").length} / SHORT ${
        pairs.filter((p) => p.signal === "SHORT").length
      } / NO_TRADE ${noTradeCount} · potential ${potentialCount} · countertrend ${countertrendCount} · expired ${expiredCount} · invalidated ${invalidatedCount}`
    );

    if (statVolume) statVolume.textContent = `$${(totalVol / 1e9).toFixed(2)}B`;
    if (statActiveSig) statActiveSig.textContent = sigCount;

    if (statSentiment) {
      if (bullCount > bearCount * 1.3) {
        statSentiment.textContent = "BULLISH BIAS";
        statSentiment.className = "stat-value font-mono text-green";
      } else if (bearCount > bullCount * 1.3) {
        statSentiment.textContent = "BEARISH BIAS";
        statSentiment.className = "stat-value font-mono text-red";
      } else {
        statSentiment.textContent = "NEUTRAL / MIXED";
        statSentiment.className = "stat-value font-mono text-blue";
      }
      if (statSentimentSub) {
        statSentimentSub.textContent = `${bullCount} Bullish / ${bearCount} Bearish across eligible universe`;
      }
    }

    if (tbodyMovers) {
      tbodyMovers.innerHTML = "";
      const sortedMovers = [...pairs].sort((a, b) => Math.abs(b.change_24h_pct || 0) - Math.abs(a.change_24h_pct || 0)).slice(0, 7);

      sortedMovers.forEach((p) => {
        const tr = document.createElement("tr");
        const chg = parseFloat(p.change_24h_pct || 0);
        const isUp = chg >= 0;
        const sig = p.signal || "NO TRADE";
        const sigClass = sig === "LONG" ? "badge-emerald" : sig === "SHORT" ? "badge-rose" : "badge-neutral";

        tr.innerHTML = `
          <td>#${p.rank || "--"}</td>
          <td><strong>${p.pair_name || p.symbol}</strong></td>
          <td class="text-dim">${p.coin_name || p.base_asset || "--"}</td>
          <td style="text-align:right;" class="font-mono">${fmtUsd(p.last_price)}</td>
          <td style="text-align:right;" class="font-mono font-bold ${isUp ? 'text-green' : 'text-red'}">${isUp ? '+' : ''}${chg.toFixed(2)}%</td>
          <td style="text-align:right;" class="font-mono text-dim">$${((parseFloat(p.volume_24h_quote) || 0) / 1e6).toFixed(1)}M</td>
          <td><span class="badge ${sigClass}">${sig}</span></td>
          <td><button type="button" class="btn-terminal-sm btn-open-chart" data-pair="${p.pair_name || p.symbol}">Chart</button></td>
        `;

        tr.querySelector(".btn-open-chart").addEventListener("click", (e) => {
          e.stopPropagation();
          selectPair(p.pair_name || p.symbol, "view-chart");
        });

        tr.addEventListener("click", () => {
          selectPair(p.pair_name || p.symbol, "view-chart");
        });

        tbodyMovers.appendChild(tr);
      });
    }

    if (signalsStream) {
      signalsStream.innerHTML = "";
      const activeOps = pairs.filter((p) => p.signal === "LONG" || p.signal === "SHORT").slice(0, 4);

      if (activeOps.length === 0) {
        signalsStream.innerHTML = `<div class="text-center text-dim py-24">No forced trades: market in equilibrium. Conservative setup filtering active.</div>`;
      } else {
        activeOps.forEach((op) => {
          const div = document.createElement("div");
          div.className = "signal-stream-card";
          const isLong = op.signal === "LONG";

          div.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="font-bold fs-md">${op.pair_name || op.symbol}</span>
              <span class="badge ${isLong ? 'badge-emerald' : 'badge-rose'} font-bold">${op.signal}</span>
            </div>
            <div class="fs-xs text-dim mt-4">
              Price: <strong class="text-bright">${fmtUsd(op.last_price)}</strong> | RSI: <strong class="text-bright">${op.rsi || 50}</strong> | ATR%: <strong class="text-bright">${op.volatility || 0}%</strong>
            </div>
            <p class="fs-xs mt-6 text-dim">${op.ranking_reason || 'Multi-timeframe confluence confirmed.'}</p>
            <div style="margin-top:8px;">
              <button type="button" class="btn-terminal-sm btn-view-plan" data-pair="${op.pair_name || op.symbol}">View Trade Plan →</button>
            </div>
          `;

          div.querySelector(".btn-view-plan").addEventListener("click", () => {
            selectPair(op.pair_name || op.symbol, "view-ai-analysis");
          });

          signalsStream.appendChild(div);
        });
      }
    }
  } catch (e) {
    console.error("Error loading overview data:", e);
  }
}

async function refreshOverviewTelemetry(refreshSignals = true) {
  const [marketRes, healthRes, activeRes, historyRes] = await Promise.all([
    fetch("/api/market-status"),
    fetch("/api/health"),
    refreshSignals ? fetch("/api/signals/active") : Promise.resolve(null),
    refreshSignals ? fetch("/api/signals/history?limit=500") : Promise.resolve(null),
  ]);
  const marketStatus = marketRes.ok ? await marketRes.json() : {};
  const health = healthRes.ok ? await healthRes.json() : {};
  const activeData = activeRes && activeRes.ok ? await activeRes.json() : {};
  const activeSignals = activeData.signals || [];
  const activeSetups = activeData.setups || [];
  const signalHistory = historyRes && historyRes.ok
    ? (await historyRes.json()).history || []
    : [];
  const scanStatus = health.signal_scanner || health.scanner || {};
  const streamStatus = health.market_data?.market_stream || {};
  const staleSeconds = marketStatus.stale_seconds;
  const marketText = `${marketStatus.status || "UNKNOWN"} · ${
    staleSeconds === null || staleSeconds === undefined ? "freshness unknown" : `${staleSeconds}s since update`
  }${marketStatus.exchange_info_stale ? " · contract list stale" : ""}`;
  const wsState = window.liveFeed ? window.liveFeed.state : "NOT CONNECTED";
  const lastScan = scanStatus.last_completed_at
    ? `${new Date(scanStatus.last_completed_at * 1000).toLocaleTimeString()} · ${
        scanStatus.last_duration_ms ?? "--"
      }ms`
    : "Waiting for first scan";
  const setTelemetry = (id, value) => {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  };
  setTelemetry(
    "telemetry-eligible",
    String(marketStatus.pairs_count ?? health.market_data?.pairs_loaded ?? 0)
  );
  setTelemetry("telemetry-market-data", marketText);
  setTelemetry("telemetry-websocket", wsState);
  setTelemetry(
    "telemetry-scanner",
    `${scanStatus.status || "WAITING"} · ${scanStatus.analyzed_contracts ?? 0} analyzed · ${scanStatus.candidate_contracts ?? 0} candidates · stream ${streamStatus.status || "STOPPED"} (${streamStatus.messages_received ?? 0} msgs) · signal latency ${scanStatus.last_analysis_latency_ms ?? "--"}ms`
  );
  setTelemetry("telemetry-last-scan", lastScan);
  if (refreshSignals) {
    setTelemetry(
      "telemetry-signals",
      `active ${activeSignals.length} · potential ${activeSetups.length} · expired ${
        signalHistory.filter((signal) => signal.close_reason === "EXPIRED").length
      } · invalidated ${
        signalHistory.filter((signal) => signal.close_reason === "INVALIDATED").length
      }`
    );
  }
}

// ============================================================================
// 6. Section 2: Main Candlestick Chart Toolbar
// ============================================================================

function setupChartToolbar() {
  document.querySelectorAll("#chart-tf-bar .tv-tf-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tf = btn.getAttribute("data-tf");
      if (tf) selectTimeframe(tf);
    });
  });

  const btnLive = document.getElementById("btn-return-live");
  if (btnLive) {
    btnLive.addEventListener("click", () => {
      if (window.chartManager) window.chartManager.scrollToLive();
    });
  }

  const btnToggleDock = document.getElementById("btn-toggle-side-dock");
  const dockPanel = document.getElementById("tv-right-dock-panel");
  if (btnToggleDock && dockPanel) {
    btnToggleDock.addEventListener("click", () => {
      const isHidden = dockPanel.style.display === "none";
      dockPanel.style.display = isHidden ? "flex" : "none";
      btnToggleDock.classList.toggle("active", isHidden);
      setTimeout(() => {
        if (window.chartManager) window.chartManager.resize();
      }, 50);
    });
  }

  const btnChartFull = document.getElementById("btn-chart-fullscreen");
  if (btnChartFull) {
    btnChartFull.addEventListener("click", () => {
      const active = document.body.classList.toggle("chart-fullscreen-mode");
      btnChartFull.classList.toggle("active", active);
      btnChartFull.innerHTML = active ? "<span>⛶ Exit Expand</span>" : "<span>⛶ Workspace Expand</span>";
      setTimeout(() => {
        if (window.chartManager) window.chartManager.resize();
      }, 60);
    });
  }

  const btnOsc = document.getElementById("btn-bottom-subcharts");
  const subContainer = document.getElementById("tv-subcharts-container");
  if (btnOsc && subContainer) {
    btnOsc.addEventListener("click", () => {
      const isVisible = subContainer.style.display !== "none";
      subContainer.style.display = isVisible ? "none" : "flex";
      btnOsc.classList.toggle("active", !isVisible);
      setTimeout(() => {
        if (window.chartManager) window.chartManager.resize();
      }, 50);
    });
  }

  const btnBottomPos = document.getElementById("btn-bottom-positions");
  if (btnBottomPos) {
    btnBottomPos.addEventListener("click", () => switchView("view-paper-trading"));
  }

  document.querySelectorAll(".tv-right-tray .tv-tray-icon").forEach((icon) => {
    icon.addEventListener("click", () => {
      const pageId = icon.getAttribute("data-page");
      document.querySelectorAll(".tv-right-tray .tv-tray-icon").forEach((i) => i.classList.remove("active"));
      icon.classList.add("active");

      document.querySelectorAll(".tv-sidebar-panel .sidebar-page").forEach((p) => p.classList.remove("active"));
      const targetPage = document.getElementById(pageId);
      if (targetPage) targetPage.classList.add("active");
      if (pageId === "page-signals") refreshDockPairSignal();
      if (pageId === "page-trading") refreshDockQuickPositions();
    });
  });
}

// ============================================================================
// 7. Section 3: Top 100 Crypto Universe
// ============================================================================

let top100CachedPairs = [];

async function loadTop100Data() {
  const tbody = document.getElementById("tbody-top100");
  const eligibleCountEl = document.getElementById("top100-eligible-count");
  if (!tbody) return;

  try {
    const res = await fetch("/api/top100?limit=100");
    if (!res.ok) return;
    const data = await res.json();
    top100CachedPairs = data.pairs || [];
    if (eligibleCountEl) {
      eligibleCountEl.textContent = `Eligible Pairs: ${data.count || top100CachedPairs.length} / ${data.total_discovered || "--"} Active USDT Contracts`;
    }
    renderTop100Table(top100CachedPairs);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="12" class="text-center text-red py-20">Error loading Top 100 universe: ${e.message}</td></tr>`;
  }
}

function renderTop100Table(pairs) {
  const tbody = document.getElementById("tbody-top100");
  if (!tbody) return;

  tbody.innerHTML = "";
  if (pairs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="text-center text-dim py-20">No coins found matching criteria.</td></tr>`;
    return;
  }

  pairs.forEach((p) => {
    const tr = document.createElement("tr");
    tr.title = p.ranking_reason || "";
    const chg24 = parseFloat(p.change_24h_pct || 0);
    const chg7d = p.change_7d !== null && p.change_7d !== undefined ? parseFloat(p.change_7d) : null;
    const chg30d = p.change_30d !== null && p.change_30d !== undefined ? parseFloat(p.change_30d) : null;
    const mcap = p.market_cap ? `$${(p.market_cap / 1e9).toFixed(2)}B` : "--";
    const vol = p.volume_24h_quote ? `$${(p.volume_24h_quote / 1e6).toFixed(1)}M` : "--";

    tr.innerHTML = `
      <td class="font-bold">#${p.rank || "--"}</td>
      <td><strong>${p.pair_name || p.symbol}</strong></td>
      <td>${p.coin_name || p.base_asset || "--"}</td>
      <td style="text-align:right;" class="font-mono">${fmtUsd(p.last_price)}</td>
      <td style="text-align:right;" class="font-mono font-bold ${chg24 >= 0 ? 'text-green' : 'text-red'}">${chg24 >= 0 ? '+' : ''}${chg24.toFixed(2)}%</td>
      <td style="text-align:right;" class="font-mono ${chg7d !== null ? (chg7d >= 0 ? 'text-green' : 'text-red') : 'text-dim'}">${chg7d !== null ? (chg7d >= 0 ? '+' : '') + chg7d.toFixed(2) + '%' : '--'}</td>
      <td style="text-align:right;" class="font-mono ${chg30d !== null ? (chg30d >= 0 ? 'text-green' : 'text-red') : 'text-dim'}">${chg30d !== null ? (chg30d >= 0 ? '+' : '') + chg30d.toFixed(2) + '%' : '--'}</td>
      <td style="text-align:right;" class="font-mono text-dim">${mcap}</td>
      <td style="text-align:right;" class="font-mono">${vol}</td>
      <td style="text-align:right;" class="font-mono font-bold text-blue">${p.composite_score || 0}/100</td>
      <td><span class="badge badge-emerald">${p.status || "TRADING"}</span></td>
      <td><button type="button" class="btn-terminal-sm btn-row-chart" data-pair="${p.pair_name || p.symbol}">Trade</button></td>
    `;

    tr.querySelector(".btn-row-chart").addEventListener("click", (e) => {
      e.stopPropagation();
      selectPair(p.pair_name || p.symbol, "view-chart");
    });

    tr.addEventListener("click", () => {
      selectPair(p.pair_name || p.symbol, "view-chart");
    });

    tbody.appendChild(tr);
  });
}

// ============================================================================
// 8. Section 4: Quantitative Market Scanner (17 Columns, 14 Filters, Sortable + AI Ops)
// ============================================================================

let scannerCachedPairs = [];
let scannerActiveFilter = "all";
let scannerSearchQuery = "";
let scannerSortField = "rank";
let scannerSortAsc = true;
let scannerOpsCache = {};
let scannerActiveOpCat = "best_long";

function setupScannerControls() {
  const btnRefresh = document.getElementById("btn-refresh-scanner");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => loadScannerData());
  }

  const searchInput = document.getElementById("scanner-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      scannerSearchQuery = e.target.value.toLowerCase().trim();
      refreshRenderedScannerTable();
    });
  }

  document.querySelectorAll("#scanner-filters-container .filter-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#scanner-filters-container .filter-pill").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      scannerActiveFilter = btn.getAttribute("data-filter");
      refreshRenderedScannerTable();
    });
  });

  document.querySelectorAll("#table-scanner thead th[data-sort]").forEach((th) => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const col = th.getAttribute("data-sort");
      if (scannerSortField === col) scannerSortAsc = !scannerSortAsc;
      else {
        scannerSortField = col;
        scannerSortAsc = col === "rank" || col === "pair_name" || col === "coin_name";
      }
      refreshRenderedScannerTable();
    });
  });

  document.querySelectorAll("#scanner-ops-tabs .filter-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#scanner-ops-tabs .filter-pill").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      scannerActiveOpCat = btn.getAttribute("data-opcat") || "best_long";
      renderScannerOpsCards();
    });
  });
}

function refreshRenderedScannerTable() {
  let rows = scannerCachedPairs;
  if (scannerSearchQuery) {
    rows = rows.filter((p) => {
      const sym = (p.pair_name || p.symbol || "").toLowerCase();
      const coin = (p.coin_name || p.base_asset || "").toLowerCase();
      return sym.includes(scannerSearchQuery) || coin.includes(scannerSearchQuery);
    });
  }
  rows = applyFilter(rows, scannerActiveFilter);
  if (scannerActiveFilter === "all" || scannerSortField !== "rank") {
    rows = [...rows].sort((a, b) => {
      let va = a[scannerSortField];
      let vb = b[scannerSortField];
      if (va === null || va === undefined) va = scannerSortAsc ? Infinity : -Infinity;
      if (vb === null || vb === undefined) vb = scannerSortAsc ? Infinity : -Infinity;
      if (typeof va === "string" && typeof vb === "string") {
        return scannerSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      return scannerSortAsc ? va - vb : vb - va;
    });
  }
  renderScannerTable(rows);
}

async function loadScannerData() {
  const tbody = document.getElementById("tbody-scanner");
  if (!tbody) return;

  try {
    const [scanRes, opsRes] = await Promise.all([
      fetch("/api/scanner"),
      fetch("/api/scanner/opportunities"),
    ]);
    if (scanRes.ok) {
      const data = await scanRes.json();
      scannerCachedPairs = data.pairs || [];
      refreshRenderedScannerTable();
    }
    if (opsRes.ok) {
      scannerOpsCache = await opsRes.json();
      renderScannerOpsCards();
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="17" class="text-center text-red py-20">Error running scanner: ${e.message}</td></tr>`;
  }
}

function renderScannerOpsCards() {
  const listEl = document.getElementById("scanner-ai-ops-list");
  if (!listEl) return;
  const items = scannerOpsCache[scannerActiveOpCat] || [];
  listEl.innerHTML = "";
  if (items.length === 0) {
    listEl.innerHTML = `<div class="text-dim fs-xs" style="grid-column:1 / -1;">No setups currently qualified under "${scannerActiveOpCat.replace("_", " ")}". No forced entries.</div>`;
    return;
  }

  items.slice(0, 8).forEach((item) => {
    const card = document.createElement("div");
    card.style.cssText = "background:#0f1521; border:1px solid #1e2638; border-radius:5px; padding:10px; cursor:pointer;";
    const isLong = item.direction === "LONG" || item.direction === "BULLISH_BIAS";
    const isShort = item.direction === "SHORT" || item.direction === "BEARISH_BIAS";
    const bCls = isLong ? "badge-emerald" : isShort ? "badge-rose" : "badge-neutral";
    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <strong class="font-mono fs-sm">${item.symbol}</strong>
        <span class="badge ${bCls}">${item.direction}</span>
      </div>
      <div class="fs-xs text-dim mt-4">
        TF: ${item.timeframe || "1h"} • ${item.setup_type} • RSI ${item.rsi}
      </div>
      <div class="fs-xs text-bright mt-4">${item.avoid_reason || item.short_reason || ""}</div>
    `;
    card.addEventListener("click", () => selectPair(item.symbol, "view-chart"));
    listEl.appendChild(card);
  });
}

function applyFilter(pairs, filterType) {
  if (filterType === "all") return pairs;
  if (filterType === "mcap") return [...pairs].sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0));
  if (filterType === "liquidity") return [...pairs].sort((a, b) => (b.volume_24h_quote || 0) - (a.volume_24h_quote || 0));
  if (filterType === "volume") return [...pairs].sort((a, b) => (b.volume_24h_quote || 0) - (a.volume_24h_quote || 0));
  if (filterType === "performers") return [...pairs].sort((a, b) => (b.change_24h_pct || 0) - (a.change_24h_pct || 0));
  if (filterType === "bullish") return pairs.filter((p) => p.trend === "Bullish" || p.signal === "LONG");
  if (filterType === "bearish") return pairs.filter((p) => p.trend === "Bearish" || p.signal === "SHORT");
  if (filterType === "breakout") return pairs.filter((p) => p.structure === "BOS");
  if (filterType === "pullback") return pairs.filter((p) => p.structure === "CHoCH" || p.sd === "Demand");
  if (filterType === "rel_vol") return pairs.filter((p) => (p.relative_volume || 1.0) > 1.4);
  if (filterType === "volatility") return [...pairs].sort((a, b) => (b.volatility || 0) - (a.volatility || 0));
  if (filterType === "demand") return pairs.filter((p) => p.sd === "Demand");
  if (filterType === "supply") return pairs.filter((p) => p.sd === "Supply");
  if (filterType === "watchlist") {
    const activeCat = watchlistCategories[activeWatchlistCategory] || window.appState.watchlist;
    const set = new Set(activeCat);
    return pairs.filter((p) => set.has(p.pair_name || p.symbol));
  }
  return pairs;
}

function renderScannerTable(pairs) {
  const tbody = document.getElementById("tbody-scanner");
  if (!tbody) return;

  tbody.innerHTML = "";
  if (pairs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="17" class="text-center text-dim py-20">No trading pairs matched the selected filter criteria.</td></tr>`;
    return;
  }

  pairs.forEach((p) => {
    const tr = document.createElement("tr");
    tr.title = p.ranking_reason || "";
    const chg24 = parseFloat(p.change_24h_pct || 0);
    const chg7d = p.change_7d !== null && p.change_7d !== undefined ? parseFloat(p.change_7d) : null;
    const chg30d = p.change_30d !== null && p.change_30d !== undefined ? parseFloat(p.change_30d) : null;
    const sig = p.signal || "NO TRADE";
    const sigClass = sig === "LONG" ? "badge-emerald" : sig === "SHORT" ? "badge-rose" : "badge-neutral";
    const structClass = p.structure === "BOS" ? "badge-blue" : p.structure === "CHoCH" ? "badge-amber" : "text-dim";
    const sdClass = p.sd === "Demand" ? "text-green font-bold" : p.sd === "Supply" ? "text-red font-bold" : "text-dim";

    tr.innerHTML = `
      <td class="font-bold">#${p.rank || "--"}</td>
      <td><strong>${p.pair_name || p.symbol}</strong></td>
      <td class="text-dim">${p.coin_name || p.base_asset || "--"}</td>
      <td style="text-align:right;" class="font-mono">${fmtUsd(p.last_price)}</td>
      <td style="text-align:right;" class="font-mono font-bold ${chg24 >= 0 ? 'text-green' : 'text-red'}">${chg24 >= 0 ? '+' : ''}${chg24.toFixed(2)}%</td>
      <td style="text-align:right;" class="font-mono ${chg7d !== null ? (chg7d >= 0 ? 'text-green' : 'text-red') : 'text-dim'}">${chg7d !== null ? (chg7d >= 0 ? '+' : '') + chg7d.toFixed(1) + '%' : '--'}</td>
      <td style="text-align:right;" class="font-mono ${chg30d !== null ? (chg30d >= 0 ? 'text-green' : 'text-red') : 'text-dim'}">${chg30d !== null ? (chg30d >= 0 ? '+' : '') + chg30d.toFixed(1) + '%' : '--'}</td>
      <td style="text-align:right;" class="font-mono text-dim">${p.market_cap ? '$' + (p.market_cap / 1e9).toFixed(1) + 'B' : '--'}</td>
      <td style="text-align:right;" class="font-mono">$${((p.volume_24h_quote || 0) / 1e6).toFixed(1)}M</td>
      <td style="text-align:right;" class="font-mono font-bold ${p.relative_volume > 1.5 ? 'text-blue' : ''}">${p.relative_volume ?? 1.0}x</td>
      <td style="text-align:right;" class="font-mono ${p.rsi > 70 ? 'text-red' : p.rsi < 30 ? 'text-green' : ''}">${p.rsi ?? 50}</td>
      <td><span class="${p.trend === 'Bullish' ? 'text-green font-bold' : p.trend === 'Bearish' ? 'text-red font-bold' : 'text-dim'}">${p.trend}</span></td>
      <td><span class="${structClass}">${p.structure}</span></td>
      <td><span class="${sdClass}">${p.sd}</span></td>
      <td style="text-align:right;" class="font-mono">${p.volatility ?? 0}%</td>
      <td style="text-align:right;" class="font-mono ${p.funding > 0.03 ? 'text-red' : p.funding < -0.01 ? 'text-green' : ''}">${p.funding ?? 0}%</td>
      <td><span class="badge ${sigClass}">${sig}</span></td>
    `;

    tr.addEventListener("click", () => {
      selectPair(p.pair_name || p.symbol, "view-chart");
    });

    tbody.appendChild(tr);
  });
}

// ============================================================================
// 9. Section 5: Individual Coin AI Analysis & Exact Trade Plan
// ============================================================================

function setupAiAnalysisControls() {
  const btnRefresh = document.getElementById("btn-refresh-ai-analysis");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => loadCoinAnalysisData());
  }

  const tfSelect = document.getElementById("ai-timeframe-select");
  if (tfSelect) {
    tfSelect.addEventListener("change", (e) => {
      selectTimeframe(e.target.value);
    });
  }

  const btnSend = document.getElementById("btn-ai-workspace-send");
  const inputEl = document.getElementById("ai-workspace-input");
  if (btnSend && inputEl) {
    btnSend.addEventListener("click", () => sendAiWorkspaceMessage(inputEl.value));
    inputEl.addEventListener("keypress", (e) => {
      if (e.key === "Enter") sendAiWorkspaceMessage(inputEl.value);
    });
  }

  // Quick chips
  document.querySelectorAll(".ai-quick-questions .btn-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.getAttribute("data-q");
      if (q) sendAiWorkspaceMessage(q);
    });
  });
}

async function loadCoinAnalysisData() {
  const pair = window.appState.currentPair;
  const tf = window.appState.currentInterval;

  const aiTitle = document.getElementById("ai-coin-title");
  if (aiTitle) aiTitle.textContent = `${pair} (${tf})`;

  try {
    const res = await fetch(`/api/coin-analysis?pair=${encodeURIComponent(pair)}&timeframe=${tf}`);
    if (!res.ok) return;
    const d = await res.json();

    // 1. MTF Matrix Banner
    const mtfData = d.multi_timeframe || {};
    const matrix = mtfData.matrix || [];
    const getTfBias = (timeframe) => {
      const found = matrix.find((m) => m.timeframe === timeframe);
      return found ? found.bias : "--";
    };

    const setMtfVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = val;
      el.className = val === "BULLISH" ? "text-green font-bold" : val === "BEARISH" ? "text-red font-bold" : "text-dim";
    };

    setMtfVal("mtf-1d-val", getTfBias("1d"));
    setMtfVal("mtf-4h-val", getTfBias("4h"));
    setMtfVal("mtf-1h-val", getTfBias("1h"));
    setMtfVal("mtf-15m-val", getTfBias("15m"));
    setMtfVal("mtf-5m-val", getTfBias("5m"));

    // 2. Technical & Structural Metrics
    const tech = d.technical || {};
    const smc = d.market_structure || {};
    const smcSum = smc.summary || {};
    const sd = d.supply_demand || {};
    const demands = sd.demand || [];
    const supplies = sd.supply || [];

    const setTxt = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setTxt("ai-metric-trend", `${tech.trend?.trend_direction || "SIDEWAYS"} (${tech.trend?.trend_strength || "MODERATE"})`);
    setTxt("ai-metric-rsi", `${tech.rsi ?? 50} (${tech.rsi > 70 ? 'Overbought' : tech.rsi < 30 ? 'Oversold' : 'Neutral'})`);
    setTxt("ai-metric-adx", `${tech.adx ?? 20} (${tech.adx > 25 ? 'Strong Trend' : 'Weak/Ranging'})`);
    setTxt("ai-metric-atr", tech.atr ? fmtUsd(tech.atr) : "--");
    setTxt("ai-metric-structure", smcSum.dominant_structure || smcSum.current_structure || "NEUTRAL");
    setTxt("ai-metric-ob", `${smcSum.active_bullish_ob_count || 0} Bullish / ${smcSum.active_bearish_ob_count || 0} Bearish`);
    setTxt("ai-metric-fvg", `${smcSum.active_fvg_count || 0} Active Gaps`);
    setTxt("ai-metric-range", smc.premium_discount ? smc.premium_discount.current_regime || "EQUILIBRIUM" : "EQUILIBRIUM");
    setTxt("ai-metric-sd", `${demands.length} Demand / ${supplies.length} Supply`);
    setTxt("ai-metric-funding", `${d.funding_rate_pct ?? 0}% per 8h`);

    // 3. Exact Trade Plan (NEVER invent targets if NO TRADE)
    const plan = d.trade_plan || {};
    const dec = d.decision || "NO TRADE";
    const decBadge = document.getElementById("ai-decision-badge");
    if (decBadge) {
      decBadge.textContent = dec;
      decBadge.className = dec === "LONG" ? "badge badge-emerald font-bold" : dec === "SHORT" ? "badge badge-rose font-bold" : "badge badge-neutral";
    }

    const techScore = tech.technical_confluence_score ?? "--";
    const confidence = tech.model_confidence || "--";
    setTxt("ai-plan-status", d.lifecycle_state || "SCANNING");
    setTxt("ai-plan-score", `${techScore}/100 (${confidence})`);
    setTxt("ai-plan-entry", plan.entry_price ? fmtUsd(plan.entry_price) : "N/A (No Active Entry)");
    setTxt("ai-plan-range", plan.entry_range ? `${fmtUsd(plan.entry_range[0])} - ${fmtUsd(plan.entry_range[1])}` : "--");
    setTxt("ai-plan-sl", plan.stop_loss ? fmtUsd(plan.stop_loss) : "--");
    setTxt("ai-plan-tp1", plan.tp1 ? fmtUsd(plan.tp1) : "--");
    setTxt("ai-plan-tp2", plan.tp2 ? fmtUsd(plan.tp2) : "--");
    setTxt("ai-plan-tp3", plan.tp3 ? fmtUsd(plan.tp3) : "--");
    setTxt("ai-plan-rr", plan.net_risk_reward_ratio ? `${plan.net_risk_reward_ratio}:1 (Gross ${plan.risk_reward_ratio}:1)` : "--");
    setTxt("ai-plan-costs", `Fees ${plan.estimated_fees_pct ?? 0.08}% • Slip ${plan.estimated_slippage_pct ?? 0.04}% • ${plan.funding_considerations || "Neutral"}`);

    // Explanations lists
    const whyEnterList = document.getElementById("ai-why-enter-list");
    if (whyEnterList) {
      whyEnterList.innerHTML = (plan.why_enter || ["Awaiting actionable technical confirmation"]).map((r) => `<li>${r}</li>`).join("");
    }
    const whyAvoidList = document.getElementById("ai-why-avoid-list");
    if (whyAvoidList) {
      whyAvoidList.innerHTML = (plan.why_avoid || ["No critical invalidations"]).map((r) => `<li>${r}</li>`).join("");
    }
    const whatNextList = document.getElementById("ai-what-next-list");
    if (whatNextList) {
      whatNextList.innerHTML = (plan.what_must_happen_next || ["Monitor higher-timeframe structure"]).map((r) => `<li>${r}</li>`).join("");
    }
    setTxt("ai-invalidation-text", plan.invalidation_condition || "--");

    // 4. Narrative
    const narrativeEl = document.getElementById("ai-workspace-narrative");
    if (narrativeEl) {
      narrativeEl.innerHTML = typeof marked !== "undefined" ? marked.parse(d.narrative || "") : d.narrative || "";
    }
  } catch (e) {
    console.error("Error loading coin analysis:", e);
  }
}

async function sendAiWorkspaceMessage(msg) {
  if (!msg || !msg.trim()) return;
  const inputEl = document.getElementById("ai-workspace-input");
  if (inputEl) inputEl.value = "";

  const logBox = document.getElementById("ai-workspace-chat-log");
  if (logBox) {
    const userMsgDiv = document.createElement("div");
    userMsgDiv.className = "message-bubble user";
    userMsgDiv.textContent = msg;
    logBox.appendChild(userMsgDiv);

    const thinkingDiv = document.createElement("div");
    thinkingDiv.className = "message-bubble ai";
    thinkingDiv.textContent = "Analyzing institutional structure...";
    logBox.appendChild(thinkingDiv);
    logBox.scrollTop = logBox.scrollHeight;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          pair: window.appState.currentPair,
          interval: window.appState.currentInterval,
        }),
      });
      if (res.ok) {
        const d = await res.json();
        thinkingDiv.innerHTML = typeof marked !== "undefined" ? marked.parse(d.response || d.text || "Analysis complete.") : (d.response || "");
      } else {
        thinkingDiv.innerHTML = marked.parse(`**${window.appState.currentPair} Analysis**: Structural bias is evaluated deterministically from closed candles.`);
      }
    } catch (e) {
      thinkingDiv.innerHTML = `Unable to reach AI endpoint. Local quantitative plan remains active.`;
    }
    logBox.scrollTop = logBox.scrollHeight;
  }
}

// ============================================================================
// 10. Section 6: Trading Signals Command Center
// ============================================================================

let signalsCachedList = [];
let signalsHistoryCache = [];
let signalsActiveFilter = "all";
let signalsLoadRequestId = 0;
const lastSignalEventVersions = new Map();
let signalEventRefreshTimer = null;

function handleLiveSignalEvent(message) {
  const event = message && message.event;
  if (!event || !event.symbol || !event.timeframe) return;
  const key = `${event.symbol}:${event.timeframe}`;
  const version = Number(event.version) || 0;
  if (!version || version <= (lastSignalEventVersions.get(key) || 0)) return;
  lastSignalEventVersions.set(key, version);

  const telemetry = document.getElementById("telemetry-signals");
  if (telemetry) {
    telemetry.textContent = `${event.transition || event.state} · ${event.symbol} ${event.timeframe} · v${version}`;
  }
  if (signalEventRefreshTimer) clearTimeout(signalEventRefreshTimer);
  signalEventRefreshTimer = setTimeout(() => {
    signalEventRefreshTimer = null;
    loadSignalsData().catch((error) =>
      console.error("Signal push refresh failed:", error)
    );
    const overview = document.getElementById("view-overview");
    if (overview && overview.classList.contains("active")) {
      refreshOverviewTelemetry().catch((error) =>
        console.error("Overview push refresh failed:", error)
      );
    }
  }, 150);
}

function setupSignalsControls() {
  const btnRefresh = document.getElementById("btn-refresh-signals");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => loadSignalsData());
  }

  document.querySelectorAll("#signals-filter-pills .filter-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#signals-filter-pills .filter-pill").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      signalsActiveFilter = btn.getAttribute("data-sfilter") || "all";
      if (signalsActiveFilter === "history") {
        renderSignalsCards(signalsHistoryCache);
      } else {
        renderSignalsCards(filterSignals(signalsCachedList, signalsActiveFilter));
      }
    });
  });
}

async function loadSignalsData() {
  const container = document.getElementById("signals-cards-container");
  if (!container) return;
  const requestId = ++signalsLoadRequestId;

  try {
    const [actRes, histRes] = await Promise.all([
      fetch("/api/signals/active"),
      fetch("/api/signals/history"),
    ]);
    if (!actRes.ok || !histRes.ok) {
      const failed = !actRes.ok ? `active signals (${actRes.status})` : `signal history (${histRes.status})`;
      throw new Error(`Could not load ${failed}.`);
    }

    const [active, history] = await Promise.all([actRes.json(), histRes.json()]);
    if (requestId !== signalsLoadRequestId) return;
    signalsCachedList = [
      ...(active.signals || []),
      ...(active.setups || []).map((setup) => ({
        ...setup,
        direction: setup.direction === "BULLISH" ? "LONG" : setup.direction === "BEARISH" ? "SHORT" : setup.direction,
        isSetup: true,
      })),
    ];
    signalsHistoryCache = history.history || [];

    if (signalsActiveFilter === "history") renderSignalsCards(signalsHistoryCache);
    else renderSignalsCards(filterSignals(signalsCachedList, signalsActiveFilter));
  } catch (e) {
    if (requestId !== signalsLoadRequestId) return;
    container.innerHTML = `<div class="text-center text-red py-24" style="grid-column: 1 / -1;">Error loading signals: ${escapeSignalHtml(e.message)}</div>`;
  }
}

function filterSignals(signals, filter) {
  if (filter === "all") return signals;
  if (filter === "confirmed_long") return signals.filter((s) => isActionableConfirmedSignal(s) && s.direction === "LONG");
  if (filter === "confirmed_short") return signals.filter((s) => isActionableConfirmedSignal(s) && s.direction === "SHORT");
  if (filter === "potential") return signals.filter((s) => (s.state || "").includes("POTENTIAL"));
  if (filter === "waiting") return signals.filter((s) => (s.state || "").includes("WAITING"));
  if (filter === "history") return signalsHistoryCache;
  return signals;
}

function escapeSignalHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function formatSignalPrice(value, precision) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "--";
  if (Number.isInteger(precision) && precision >= 0 && precision <= 12) {
    return `$${Number(value).toLocaleString(undefined, {
      minimumFractionDigits: precision,
      maximumFractionDigits: precision,
    })}`;
  }
  return fmtUsd(value);
}

function formatSignalTime(value) {
  const timestamp = Number(value);
  return Number.isFinite(timestamp) && timestamp > 0
    ? new Date(timestamp * 1000).toLocaleString()
    : "Unavailable";
}

function hasValidatedSignalPlan(signal) {
  const levels = [signal.entry_price, signal.stop_loss, signal.tp1, signal.tp2, signal.tp3].map(Number);
  if (!levels.every(Number.isFinite) || levels.some((level) => level <= 0)) return false;
  const [entry, stop, tp1, tp2, tp3] = levels;
  const risk = Math.abs(entry - stop);
  const reward = Math.abs(tp2 - entry);
  if (risk <= 0 || reward / risk < 1.8) return false;
  return signal.direction === "LONG"
    ? stop < entry && entry < tp1 && tp1 < tp2 && tp2 < tp3
    : signal.direction === "SHORT"
      ? tp3 < tp2 && tp2 < tp1 && tp1 < entry && entry < stop
      : false;
}

function isActionableConfirmedSignal(signal) {
  const priceTimestamp = Number(signal.current_price_timestamp || 0);
  const priceAgeSeconds = Math.floor(Date.now() / 1000) - priceTimestamp;
  return signal.state === "CONFIRMED"
    && signal.filled === true
    && signal.market_data_fresh === true
    && signal.mtf_is_fresh === true
    && priceTimestamp > 0
    && priceAgeSeconds >= 0
    && priceAgeSeconds <= 15
    && hasValidatedSignalPlan(signal);
}

function renderSignalsCards(signals) {
  const container = document.getElementById("signals-cards-container");
  if (!container) return;

  container.innerHTML = "";
  if (signals.length === 0) {
    container.innerHTML = `<div class="text-center text-dim py-32" style="grid-column: 1 / -1;">No signals currently match the selected filter. Conservative thresholding prevents overtrading.</div>`;
    return;
  }

  signals.forEach((sig) => {
    const card = document.createElement("div");
    card.className = "signal-card";
    const direction = sig.direction === "LONG" || sig.direction === "SHORT" ? sig.direction : "NEUTRAL";
    const isLong = direction === "LONG";
    const isShort = direction === "SHORT";
    const badgeClass = isLong ? "badge-emerald" : isShort ? "badge-rose" : "badge-neutral";
    const dirText = isLong ? "LONG" : isShort ? "SHORT" : "NO TRADE";
    const precision = Number.isInteger(sig.price_precision) ? sig.price_precision : null;
    const currentPrice = formatSignalPrice(sig.current_price ?? sig.price, precision);
    const entry = formatSignalPrice(sig.entry_price, precision);
    const entryRange = Array.isArray(sig.entry_range) && sig.entry_range.length === 2
      ? `${formatSignalPrice(sig.entry_range[0], precision)} – ${formatSignalPrice(sig.entry_range[1], precision)}`
      : "Unavailable — full validated analysis required";
    const isFresh = sig.market_data_fresh === true
      && Number.isFinite(Number(sig.current_price_timestamp))
      && Date.now() / 1000 - Number(sig.current_price_timestamp) <= 15
      && Date.now() / 1000 - Number(sig.current_price_timestamp) >= 0;
    const state = escapeSignalHtml(sig.state || "SCANNING");
    const symbol = escapeSignalHtml(sig.symbol || sig.pair || "Unknown");
    const timeframe = escapeSignalHtml(sig.timeframe || "Unavailable");
    const reasonsFor = Array.isArray(sig.reasons_for) ? sig.reasons_for : [];
    const reasonsAgainst = Array.isArray(sig.reasons_against) ? sig.reasons_against : [];
    const fullPlanValid = hasValidatedSignalPlan(sig);
    const actionable = isActionableConfirmedSignal(sig);
    const reason = escapeSignalHtml(sig.reason || sig.validation_reason || (
      sig.isSetup
        ? "Setup is awaiting full technical, liquidity, and risk validation."
        : sig.market_data_fresh !== true || !isFresh
          ? "Fresh market data is unavailable; this signal is not actionable."
          : sig.mtf_is_fresh !== true
            ? sig.mtf_reason || "Required multi-timeframe data is stale or unavailable."
            : sig.state !== "CONFIRMED"
              ? `Lifecycle state is ${sig.state || "unknown"}; confirmation has not completed.`
              : sig.filled !== true
                ? "Price has not filled the entry range."
                : !fullPlanValid
                  ? "The complete directional trade plan failed validation."
                  : "No additional lifecycle reason recorded."
    ));
    const buttonLabel = sig.isSetup ? "Review Analysis →" : "Full Plan";
    const createdAt = formatSignalTime(sig.created_at || sig.timestamp);
    const updatedAt = formatSignalTime(sig.current_price_timestamp || sig.updated_at);

    card.innerHTML = `
      <div class="signal-card-header">
        <div>
          <span class="font-bold fs-md">${symbol}</span>
          <span class="fs-xs text-dim ml-4">${timeframe}</span>
        </div>
        <span class="badge ${badgeClass} font-bold">${dirText}</span>
      </div>
      <div class="signal-card-body">
        <div class="signal-metric"><span>Lifecycle state</span><strong class="text-bright">${state}</strong></div>
        <div class="signal-metric"><span>Current price</span><strong class="font-mono">${currentPrice}</strong></div>
        <div class="signal-metric"><span>Market data</span><strong class="font-mono">${isFresh ? "FRESH" : "STALE / UNAVAILABLE"}</strong></div>
        <div class="signal-metric"><span>Entry / range</span><strong class="font-mono">${entry} · ${entryRange}</strong></div>
        <div class="signal-metric"><span>Stop Loss</span><strong class="font-mono text-red">${formatSignalPrice(sig.stop_loss, precision)}</strong></div>
        <div class="signal-metric"><span>Take Profit 1</span><strong class="font-mono text-green">${formatSignalPrice(sig.tp1, precision)}</strong></div>
        <div class="signal-metric"><span>Plan readiness</span><strong class="font-mono">${actionable ? "VALID · ACTIONABLE" : fullPlanValid ? "COMPLETE · NOT ACTIONABLE" : "INCOMPLETE"}</strong></div>
        ${sig.close_reason ? `<div class="signal-metric"><span>Outcome</span><strong class="font-mono">${escapeSignalHtml(sig.close_reason)} (${escapeSignalHtml(sig.r_multiple)}R)</strong></div>` : ""}
        <details class="signal-plan-details">
          <summary>${buttonLabel}</summary>
          <div class="signal-plan-details-body">
            <div class="signal-metric"><span>Entry</span><strong>${entry}</strong></div>
            <div class="signal-metric"><span>Entry range</span><strong>${entryRange}</strong></div>
            <div class="signal-metric"><span>Stop Loss / Invalidation</span><strong>${formatSignalPrice(sig.stop_loss, precision)}</strong></div>
            <div class="signal-metric"><span>TP1 / TP2 / TP3</span><strong>${formatSignalPrice(sig.tp1, precision)} / ${formatSignalPrice(sig.tp2, precision)} / ${formatSignalPrice(sig.tp3, precision)}</strong></div>
            <div class="signal-metric"><span>Risk / Reward</span><strong>${Number.isFinite(Number(sig.risk_reward_ratio)) ? `${Number(sig.risk_reward_ratio).toFixed(2)}R` : "Unavailable"}</strong></div>
            <div class="signal-metric"><span>MTF alignment</span><strong>${escapeSignalHtml(sig.htf_bias || "Unavailable")} · ${sig.mtf_is_fresh === true ? "FRESH" : "STALE / UNAVAILABLE"}</strong></div>
            <div class="signal-plan-reason">${escapeSignalHtml(sig.mtf_reason || "MTF alignment details unavailable.")}</div>
            <div class="signal-metric"><span>Setup score</span><strong>${Number.isFinite(Number(sig.setup_score)) ? `${Number(sig.setup_score)}/100` : "Unavailable"}</strong></div>
            <div class="signal-metric"><span>Created</span><strong>${escapeSignalHtml(createdAt)}</strong></div>
            <div class="signal-metric"><span>Last price update</span><strong>${escapeSignalHtml(updatedAt)}</strong></div>
            <div class="signal-metric"><span>Model</span><strong>${escapeSignalHtml(sig.strategy_type || sig.primary_model || "Unavailable")}</strong></div>
            <div class="signal-metric"><span>Invalidation condition</span><strong>${escapeSignalHtml(sig.invalidation_condition || "Unavailable")}</strong></div>
            ${reasonsFor.length ? `<div class="signal-plan-reason"><strong>Supporting evidence:</strong><ul>${reasonsFor.map((item) => `<li>${escapeSignalHtml(item)}</li>`).join("")}</ul></div>` : ""}
            ${reasonsAgainst.length ? `<div class="signal-plan-reason"><strong>Conflicting evidence:</strong><ul>${reasonsAgainst.map((item) => `<li>${escapeSignalHtml(item)}</li>`).join("")}</ul></div>` : ""}
            <div class="signal-plan-reason"><strong>Why it is waiting / rejected:</strong> ${reason}</div>
          </div>
        </details>
      </div>
      <div class="signal-card-footer">
        <button type="button" class="btn-terminal-primary btn-sig-chart" style="flex:1;">Open Chart</button>
        ${sig.isSetup ? `<button type="button" class="btn-terminal-sm btn-sig-plan" style="flex:1;">Review Analysis →</button>` : ""}
      </div>
    `;

    card.querySelector(".btn-sig-chart").addEventListener("click", () => {
      const nextTimeframe = sig.timeframe || "15m";
      selectPair(sig.symbol || sig.pair, "view-chart", nextTimeframe);
    });
    const analysisButton = card.querySelector(".btn-sig-plan");
    if (analysisButton) {
      analysisButton.addEventListener("click", () => {
        const nextTimeframe = sig.timeframe || "15m";
        selectPair(sig.symbol || sig.pair, "view-ai-analysis", nextTimeframe);
      });
    }

    container.appendChild(card);
  });
}

// ============================================================================
// 11. Section 7: Watchlist & User Control
// ============================================================================

let watchlistCategories = {};
let activeWatchlistCategory = "Core 10";

function setupWatchlistControls() {
  const btnAdd = document.getElementById("btn-add-watchlist-pair");
  const inputAdd = document.getElementById("watchlist-add-input");
  if (btnAdd && inputAdd) {
    const submitAdd = () => {
      let val = inputAdd.value.trim().toUpperCase();
      if (!val) return;
      if (!val.includes("/")) val = `${val}/USDT`;
      addWatchlistPair(val);
      inputAdd.value = "";
    };
    btnAdd.addEventListener("click", submitAdd);
    inputAdd.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitAdd();
    });
  }

  const btnNewCat = document.getElementById("btn-new-watchlist-cat");
  if (btnNewCat) {
    btnNewCat.addEventListener("click", async () => {
      const cat = prompt("Enter custom watchlist category name:", "Breakouts");
      if (!cat || !cat.trim()) return;
      const cleanCat = cat.trim();
      if (!watchlistCategories[cleanCat]) {
        watchlistCategories[cleanCat] = [];
        try {
          await fetch("/api/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ category: cleanCat, symbols: [] }),
          });
        } catch (e) {}
      }
      activeWatchlistCategory = cleanCat;
      renderWatchlistCategoryPills();
      renderWatchlistTable();
    });
  }
}

function renderWatchlistCategoryPills() {
  const wrap = document.getElementById("watchlist-categories");
  if (!wrap) return;
  const cats = Object.keys(watchlistCategories);
  if (cats.length === 0) return;

  wrap.innerHTML = "";
  cats.forEach((cat) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `filter-pill ${cat === activeWatchlistCategory ? "active" : ""}`;
    btn.setAttribute("data-wcat", cat);
    btn.textContent = cat;
    btn.addEventListener("click", () => {
      activeWatchlistCategory = cat;
      renderWatchlistCategoryPills();
      renderWatchlistTable();
    });
    wrap.appendChild(btn);
  });
}

async function loadWatchlistData() {
  try {
    const res = await fetch("/api/watchlist");
    if (res.ok) {
      const d = await res.json();
      watchlistCategories = d.watchlists || {};
      if (!watchlistCategories[activeWatchlistCategory]) {
        activeWatchlistCategory = Object.keys(watchlistCategories)[0] || "Core 10";
      }
      renderWatchlistCategoryPills();
    }
  } catch (e) {}
  renderWatchlistTable();
}

function renderWatchlistTable() {
  const tbody = document.getElementById("tbody-full-watchlist");
  if (!tbody) return;

  tbody.innerHTML = "";
  const pairs = watchlistCategories[activeWatchlistCategory] || window.appState.watchlist;

  if (pairs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center text-dim py-20">Watchlist category is empty. Add a pair above.</td></tr>`;
    return;
  }

  const scanMap = new Map(scannerCachedPairs.map((s) => [s.pair_name || s.symbol, s]));

  pairs.forEach((pair) => {
    const scanItem = scanMap.get(pair);
    const sigTxt = scanItem ? scanItem.signal : "TRACKED";
    const sigCls = sigTxt === "LONG" ? "badge-emerald" : sigTxt === "SHORT" ? "badge-rose" : "badge-neutral";

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${pair}</strong></td>
      <td style="text-align:right;" class="font-mono">--</td>
      <td style="text-align:right;" class="font-mono">--</td>
      <td style="text-align:right;" class="font-mono">--</td>
      <td style="text-align:right;" class="font-mono">--</td>
      <td style="text-align:right;" class="font-mono text-dim">--</td>
      <td><span class="badge ${sigCls}">${sigTxt}</span></td>
      <td>
        <button type="button" class="btn-terminal-sm btn-wl-chart">Chart</button>
        <button type="button" class="btn-terminal-sm btn-wl-plan">Plan</button>
        <button type="button" class="btn-terminal-sm btn-wl-del" style="color:var(--red);">✕</button>
      </td>
    `;

    tr.querySelector(".btn-wl-chart").addEventListener("click", () => selectPair(pair, "view-chart"));
    tr.querySelector(".btn-wl-plan").addEventListener("click", () => selectPair(pair, "view-ai-analysis"));
    tr.querySelector(".btn-wl-del").addEventListener("click", () => removeWatchlistPair(pair));

    tbody.appendChild(tr);

    fetch(`/api/ticker?pair=${encodeURIComponent(pair)}`).then((r) => r.json()).then((t) => {
      if (t && t.last_price) {
        const tds = tr.querySelectorAll("td");
        const chg = parseFloat(t.price_change_percent || 0);
        tds[1].textContent = fmtUsd(t.last_price);
        tds[2].textContent = `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
        tds[2].className = `font-mono font-bold ${chg >= 0 ? "text-green" : "text-red"}`;
        tds[3].textContent = fmtUsd(t.high_24h);
        tds[4].textContent = fmtUsd(t.low_24h);
        tds[5].textContent = `$${((parseFloat(t.quote_volume_24h) || 0) / 1e6).toFixed(1)}M`;
      }
    }).catch(() => {});
  });
}

async function addWatchlistPair(pair) {
  if (!watchlistCategories[activeWatchlistCategory]) {
    watchlistCategories[activeWatchlistCategory] = [];
  }
  if (!watchlistCategories[activeWatchlistCategory].includes(pair)) {
    watchlistCategories[activeWatchlistCategory].push(pair);
  }
  renderWatchlistTable();
  try {
    await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: activeWatchlistCategory, symbols: watchlistCategories[activeWatchlistCategory] }),
    });
  } catch (e) {}
}

async function removeWatchlistPair(pair) {
  if (watchlistCategories[activeWatchlistCategory]) {
    watchlistCategories[activeWatchlistCategory] = watchlistCategories[activeWatchlistCategory].filter((p) => p !== pair);
  }
  renderWatchlistTable();
  try {
    await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: activeWatchlistCategory, symbols: watchlistCategories[activeWatchlistCategory] || [] }),
    });
  } catch (e) {}
}

// ============================================================================
// 12. Section 8: Paper Trading
// ============================================================================

let paperSelectedSide = "BUY";

function setupPaperTradingControls() {
  const btnBuy = document.getElementById("btn-paper-buy");
  const btnSell = document.getElementById("btn-paper-sell");
  const btnExecute = document.getElementById("btn-paper-execute");
  const btnRefresh = document.getElementById("btn-refresh-paper");

  if (btnBuy && btnSell) {
    btnBuy.style.opacity = "1.0";
    btnSell.style.opacity = "0.55";
    btnBuy.addEventListener("click", () => {
      paperSelectedSide = "BUY";
      btnBuy.style.opacity = "1.0";
      btnSell.style.opacity = "0.55";
    });
    btnSell.addEventListener("click", () => {
      paperSelectedSide = "SELL";
      btnSell.style.opacity = "1.0";
      btnBuy.style.opacity = "0.55";
    });
  }

  if (btnExecute) {
    btnExecute.addEventListener("click", () => {
      const amount = Number(document.getElementById("input-paper-amount")?.value);
      if (!Number.isFinite(amount) || amount <= 0) {
        alert("Enter a valid positive position size.");
        return;
      }
      executePaperTrade(window.appState.currentPair, paperSelectedSide, amount);
    });
  }

  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => loadPaperTradingData());
  }
}

async function executePaperTrade(pair, side, amountUsdt) {
  let price = window.appState.currentPrice;
  if (!price) {
    try {
      const tRes = await fetch(`/api/ticker?pair=${encodeURIComponent(pair)}`);
      if (tRes.ok) {
        const td = await tRes.json();
        price = parseFloat(td.last_price) || 0;
      }
    } catch (e) {}
  }
  if (!price || price <= 0) {
    alert("Cannot place paper trade without valid market price.");
    return;
  }

  try {
    const res = await fetch("/api/paper/trade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pair,
        side,
        amount_usdt: amountUsdt,
        price,
      }),
    });
    if (res.ok) {
      alert(`Paper ${side} trade executed for ${pair} ($${amountUsdt} @ ${fmtUsd(price)})`);
      loadPaperTradingData();
      refreshDockQuickPositions();
    } else {
      const err = await res.json();
      await loadPaperTradingData();
      await refreshDockQuickPositions();
      alert(`Paper trade error: ${err.detail || "Risk check failed"}`);
    }
  } catch (e) {
    alert(`Error submitting paper order: ${e.message}`);
  }
}

let paperPortfolioRequestId = 0;

async function loadPaperTradingData() {
  const balEl = document.getElementById("paper-stat-balance");
  const eqEl = document.getElementById("paper-stat-equity");
  const unrlEl = document.getElementById("paper-stat-unrealized");
  const rlEl = document.getElementById("paper-stat-realized");
  const tbody = document.getElementById("tbody-paper-positions");
  const limitStatusEl = document.getElementById("paper-position-limit-status");
  const closeableEl = document.getElementById("paper-closeable-positions");
  const portfolioStatusEl = document.getElementById("paper-portfolio-status");
  const requestId = ++paperPortfolioRequestId;
  if (portfolioStatusEl) portfolioStatusEl.textContent = "Refreshing paper portfolio...";

  try {
    const res = await fetch("/api/paper/summary");
    if (!res.ok) throw new Error(`Portfolio refresh failed (${res.status}).`);
    const d = await res.json();
    if (requestId !== paperPortfolioRequestId) return;

    if (balEl && d.available_margin !== undefined) balEl.textContent = fmtUsd(d.available_margin);
    if (eqEl && d.equity !== undefined) eqEl.textContent = fmtUsd(d.equity);
    if (unrlEl) {
      const u = Number(d.unrealized_pnl || 0);
      unrlEl.textContent = `${u >= 0 ? "+" : ""}$${u.toFixed(2)}`;
      unrlEl.className = `stat-value font-mono ${u >= 0 ? "text-green" : "text-red"}`;
    }
    if (rlEl) {
      const r = Number(d.realized_pnl || 0);
      rlEl.textContent = `${r >= 0 ? "+" : ""}$${r.toFixed(2)}`;
      rlEl.className = `stat-value font-mono ${r >= 0 ? "text-green" : "text-red"}`;
    }

    const positions = d.open_positions || d.positions || [];
    if (limitStatusEl) {
      const current = Number(d.current_open_positions ?? positions.length);
      const maximum = Number(d.max_open_positions ?? current);
      const slots = Number(d.available_position_slots ?? Math.max(0, maximum - current));
      limitStatusEl.textContent =
        `Current open positions: ${current}. Maximum allowed positions: ${maximum}. Available position slots: ${slots}.`;
    }
    if (closeableEl) {
      const closeable = d.closeable_positions || positions;
      closeableEl.textContent = closeable.length
        ? `Positions that can be closed: ${closeable.map((pos) => `${pos.pair} ${pos.side} ($${Number(pos.amount_usdt || 0).toFixed(2)}, ${pos.id})`).join(", ")}. Use each row's Close button below.`
        : "Positions that can be closed: none.";
    }

    if (tbody) {
      tbody.innerHTML = "";
      if (positions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center text-dim py-20">No active paper positions.</td></tr>`;
      } else {
        positions.forEach((pos) => {
          const tr = document.createElement("tr");
          const markP = Number(pos.mark_price || pos.entry_price);
          const isLong = pos.side === "BUY" || pos.side === "LONG";
          const pnl = Number(pos.unrealized_pnl ?? (isLong ? (markP - pos.entry_price) * pos.quantity : (pos.entry_price - markP) * pos.quantity));
          const pnlPct = Number(pos.unrealized_pnl_pct ?? ((pnl / (pos.amount_usdt || 100)) * 100));

          tr.innerHTML = `
            <td><strong>${pos.pair}</strong></td>
            <td><span class="${isLong ? "badge-emerald" : "badge-rose"} font-bold">${pos.side}</span></td>
            <td style="text-align:right;" class="font-mono">$${Number(pos.amount_usdt || 100).toLocaleString()}</td>
            <td style="text-align:right;" class="font-mono">${fmtUsd(pos.entry_price)}</td>
            <td style="text-align:right;" class="font-mono">${fmtUsd(markP)}</td>
            <td style="text-align:right;" class="font-mono font-bold ${pnl >= 0 ? "text-green" : "text-red"}">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}</td>
            <td style="text-align:right;" class="font-mono font-bold ${pnl >= 0 ? "text-green" : "text-red"}">${pnl >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%</td>
            <td><button type="button" class="btn-terminal-sm btn-close-paper-pos" style="color:var(--red);">Close</button></td>
          `;

          tr.querySelector(".btn-close-paper-pos").addEventListener("click", async () => {
            await fetch("/api/paper/close", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ position_id: pos.id, price: markP }),
            });
            loadPaperTradingData();
            refreshDockQuickPositions();
          });

          tbody.appendChild(tr);
        });
      }
    }
    if (portfolioStatusEl) {
      portfolioStatusEl.classList.remove("text-red");
      if (d.market_data_refresh_error) {
        portfolioStatusEl.textContent =
          `Portfolio refreshed at ${new Date().toLocaleTimeString()}. ${d.market_data_refresh_error}`;
        portfolioStatusEl.classList.add("text-red");
      } else {
        portfolioStatusEl.textContent = `Portfolio refreshed at ${new Date().toLocaleTimeString()}.`;
      }
    }
  } catch (e) {
    if (requestId === paperPortfolioRequestId && portfolioStatusEl) {
      portfolioStatusEl.textContent = `Unable to refresh paper portfolio: ${e.message}`;
      portfolioStatusEl.classList.add("text-red");
    }
  }
}

// ============================================================================
// 13. Section 9: Historical Backtesting
// ============================================================================

function setupBacktestingControls() {
  const btnRun = document.getElementById("btn-run-backtest");
  if (!btnRun) return;

  btnRun.addEventListener("click", async () => {
    const pair = document.getElementById("bt-pair-input")?.value || "BTC/USDT";
    const tf = document.getElementById("bt-tf-select")?.value || "15m";
    const limit = parseInt(document.getElementById("bt-limit-select")?.value) || 500;
    const capital = parseFloat(document.getElementById("bt-capital-input")?.value) || 1000;
    const risk = parseFloat(document.getElementById("bt-risk-input")?.value) || 1.5;

    btnRun.disabled = true;
    btnRun.textContent = "⏳ Simulating...";

    try {
      const res = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pair,
          timeframe: tf,
          candle_limit: limit,
          starting_capital: capital,
          risk_pct: risk,
        }),
      });

      if (res.ok) {
        const d = await res.json();
        renderBacktestResults(d, capital);
      }
    } catch (e) {
      console.error("Backtest error:", e);
    } finally {
      btnRun.disabled = false;
      btnRun.textContent = "Run Backtest";
    }
  });
}

function renderBacktestResults(res, capital) {
  const pnlEl = document.getElementById("bt-stat-net-profit");
  const roiEl = document.getElementById("bt-stat-roi");
  const wrEl = document.getElementById("bt-stat-winrate");
  const countEl = document.getElementById("bt-stat-trades-count");
  const pfEl = document.getElementById("bt-stat-pf");
  const ddEl = document.getElementById("bt-stat-maxdd");
  const wfEl = document.getElementById("bt-wf-values");
  const tbody = document.getElementById("tbody-bt-trades");

  const netPnl = Number(res.net_pnl ?? res.net_profit ?? 0);
  const roi = capital > 0 ? (netPnl / capital) * 100 : 0;

  if (pnlEl) {
    pnlEl.textContent = `${netPnl >= 0 ? "+" : ""}$${netPnl.toFixed(2)}`;
    pnlEl.className = `stat-value font-mono ${netPnl >= 0 ? "text-green" : "text-red"}`;
  }
  if (roiEl) roiEl.textContent = `${roi >= 0 ? "+" : ""}${roi.toFixed(2)}% ROI on initial capital`;
  if (wrEl) wrEl.textContent = `${Number(res.win_rate ?? 0).toFixed(1)}%`;
  if (countEl) countEl.textContent = `${res.total_trades ?? 0} Executed Trades${res.avg_r !== undefined ? ` (Avg ${Number(res.avg_r).toFixed(2)}R)` : ""}`;
  if (pfEl) pfEl.textContent = res.profit_factor !== null && res.profit_factor !== undefined ? Number(res.profit_factor).toFixed(2) : "N/A";
  if (ddEl) ddEl.textContent = `${Number(res.max_drawdown_pct ?? 0).toFixed(1)}%`;

  if (wfEl && res.walk_forward) {
    const wf = res.walk_forward;
    const cs = res.costs_summary ? ` | Costs: $${Number(res.costs_summary.total_costs_usd ?? 0).toFixed(2)}` : "";
    wfEl.textContent = `In-Sample (70%): ${wf.in_sample_trades} trades, $${wf.in_sample_pnl} (${wf.in_sample_win_rate ?? 0}% WR, Avg ${wf.in_sample_avg_r ?? 0}R) | Out-of-Sample (30%): ${wf.out_of_sample_trades} trades, $${wf.out_of_sample_pnl} (${wf.out_of_sample_win_rate ?? 0}% WR, Avg ${wf.out_of_sample_avg_r ?? 0}R)${cs}`;
  }

  if (tbody) {
    tbody.innerHTML = "";
    const trades = res.trades || [];
    if (trades.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" class="text-center text-dim py-20">No closed trades triggered in this lookback window under current strict strategy filters.</td></tr>`;
      return;
    }
    trades.slice(-30).forEach((t, i) => {
      const tr = document.createElement("tr");
      const isWin = (t.pnl_net || 0) >= 0;
      tr.innerHTML = `
        <td class="font-mono fs-xs">${i + 1}</td>
        <td><span class="${t.direction === "LONG" ? "badge-emerald" : "badge-rose"} font-bold">${t.direction}</span></td>
        <td class="text-dim fs-xs">${t.model_name || "MODEL_B"}</td>
        <td style="text-align:right;" class="font-mono">${fmtUsd(t.entry_price)}</td>
        <td style="text-align:right;" class="font-mono">${fmtUsd(t.exit_price)}</td>
        <td style="text-align:right;" class="font-mono font-bold ${isWin ? "text-green" : "text-red"}">${isWin ? "+" : ""}$${Number(t.pnl_net).toFixed(2)}</td>
        <td style="text-align:right;" class="font-mono font-bold ${isWin ? "text-green" : "text-red"}">${isWin ? "+" : ""}${Number(t.pnl_pct).toFixed(2)}%</td>
        <td style="text-align:right;" class="font-mono text-dim">${Number(t.mae_pct || 0).toFixed(2)}%</td>
        <td style="text-align:right;" class="font-mono text-green">${Number(t.mfe_pct || 0).toFixed(2)}%</td>
        <td><span class="${t.exit_reason === "TAKE_PROFIT" ? "text-green font-bold" : "text-red"}">${t.exit_reason}</span></td>
      `;
      tbody.appendChild(tr);
    });
  }
}

// ============================================================================
// 14. Section 10: Performance Analytics
// ============================================================================

async function loadPerformanceData() {
  const wrEl = document.getElementById("perf-winrate");
  const totalEl = document.getElementById("perf-total-signals");
  const avgREl = document.getElementById("perf-avg-r");
  const pfEl = document.getElementById("perf-pf");
  const streakEl = document.getElementById("perf-losing-streak");
  const tbodyCoin = document.getElementById("tbody-perf-by-coin");
  const tbodyTf = document.getElementById("tbody-perf-by-tf");

  try {
    const res = await fetch("/api/signals/performance");
    if (!res.ok) return;
    const d = await res.json();

    const completed = d.completed_trades ?? d.closed_signals ?? 0;
    const tracked = d.tracked_signals_total ?? d.total_signals ?? 0;
    if (wrEl) wrEl.textContent = completed > 0 ? `${Number(d.win_rate_pct ?? d.win_rate ?? 0).toFixed(1)}%` : "--%";
    if (totalEl) totalEl.textContent = `${completed} Completed Trades (${tracked} Tracked Signals)`;
    if (avgREl) avgREl.textContent = `${Number(d.avg_r_multiple ?? 0).toFixed(2)}R`;
    if (pfEl) pfEl.textContent = d.profit_factor !== null && d.profit_factor !== undefined ? Number(d.profit_factor).toFixed(2) : "N/A";
    if (streakEl) streakEl.textContent = `${d.longest_losing_streak ?? 0} Trades`;

    if (tbodyCoin) {
      tbodyCoin.innerHTML = "";
      const entries = Object.entries(d.by_symbol || {});
      if (entries.length === 0) {
        tbodyCoin.innerHTML = `<tr><td colspan="5" class="text-center text-dim py-16">No closed signal history yet. Statistical tracking updates automatically as signals close.</td></tr>`;
      } else {
        entries.forEach(([sym, data]) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td><strong>${sym}</strong></td>
            <td style="text-align:right;" class="font-mono text-green">${data.wins}</td>
            <td style="text-align:right;" class="font-mono text-red">${data.losses}</td>
            <td style="text-align:right;" class="font-mono font-bold">${data.win_rate}%</td>
            <td style="text-align:right;" class="font-mono font-bold ${data.total_pnl_pct >= 0 ? "text-green" : "text-red"}">${data.total_pnl_pct >= 0 ? "+" : ""}${data.total_pnl_pct}%</td>
          `;
          tbodyCoin.appendChild(tr);
        });
      }
    }

    if (tbodyTf) {
      tbodyTf.innerHTML = "";
      const entries = Object.entries(d.by_timeframe || {});
      if (entries.length === 0) {
        tbodyTf.innerHTML = `<tr><td colspan="5" class="text-center text-dim py-16">No closed timeframe outcomes yet.</td></tr>`;
      } else {
        entries.forEach(([tf, data]) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td><strong>${tf}</strong></td>
            <td style="text-align:right;" class="font-mono text-green">${data.wins}</td>
            <td style="text-align:right;" class="font-mono text-red">${data.losses}</td>
            <td style="text-align:right;" class="font-mono font-bold">${data.win_rate}%</td>
            <td style="text-align:right;" class="font-mono font-bold ${data.total_pnl_pct >= 0 ? "text-green" : "text-red"}">${data.total_pnl_pct >= 0 ? "+" : ""}${data.total_pnl_pct}%</td>
          `;
          tbodyTf.appendChild(tr);
        });
      }
    }
  } catch (e) {}
}

// ============================================================================
// 15. Section 11: Settings
// ============================================================================

function setupSettingsControls() {
  const btnSave = document.getElementById("btn-save-settings");
  if (!btnSave) return;

  btnSave.addEventListener("click", async () => {
    const key = document.getElementById("setting-gemini-key")?.value || "";
    const model = document.getElementById("setting-ai-model")?.value || "gemini-flash-latest";

    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gemini_api_key: key,
          ai_model: model,
        }),
      });
      if (res.ok) {
        alert("Terminal configuration saved successfully.");
      }
    } catch (e) {
      alert("Settings saved locally.");
    }
  });
}

async function loadSettingsData() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return;
    const d = await res.json();
    const keyEl = document.getElementById("setting-gemini-key");
    const modelEl = document.getElementById("setting-ai-model");
    if (keyEl && d.gemini_api_key) keyEl.value = d.gemini_api_key;
    if (modelEl && d.ai_model) modelEl.value = d.ai_model;
  } catch (e) {}
}
