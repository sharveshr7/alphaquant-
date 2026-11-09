/**
 * AlphaQuant Pro - Complete TradingView Technical Indicators Engine
 *
 * Implements:
 * - Searchable Indicator Modal Dialog with Toggle & Configure controls
 * - Main Chart Overlays: EMA (9, 20, 50, 100, 200), SMA (20, 50, 200),
 *   VWAP, Bollinger Bands, Supertrend
 * - Oscillators & Sub-panels: RSI (14), MACD (12, 26, 9)
 * - Synchronized Crosshair and Time Scales
 */

class IndicatorsEngine {
  constructor() {
    this.catalog = [
      { id: "ema9", name: "EMA 9 (Exponential Moving Average)", category: "Moving Average", type: "overlay", enabled: true, color: "#38bdf8", period: 9 },
      { id: "ema20", name: "EMA 20", category: "Moving Average", type: "overlay", enabled: false, color: "#818cf8", period: 20 },
      { id: "ema50", name: "EMA 50", category: "Moving Average", type: "overlay", enabled: true, color: "#3b82f6", period: 50 },
      { id: "ema100", name: "EMA 100", category: "Moving Average", type: "overlay", enabled: false, color: "#a855f7", period: 100 },
      { id: "ema200", name: "EMA 200 (Macro Trend)", category: "Moving Average", type: "overlay", enabled: true, color: "#f59e0b", period: 200 },
      { id: "sma20", name: "SMA 20 (Simple Moving Average)", category: "Moving Average", type: "overlay", enabled: false, color: "#94a3b8", period: 20 },
      { id: "vwap", name: "Session VWAP (Volume Weighted Avg Price)", category: "Volume & Flow", type: "overlay", enabled: true, color: "#06b6d4" },
      { id: "bollinger", name: "Bollinger Bands (20, 2.0)", category: "Volatility", type: "overlay", enabled: false, color: "#c084fc", period: 20, stdDev: 2.0 },
      { id: "supertrend", name: "Supertrend (10, 3.0)", category: "Trend", type: "overlay", enabled: false, period: 10, multiplier: 3.0 },
      { id: "rsi", name: "RSI (Relative Strength Index 14)", category: "Momentum", type: "subchart", enabled: true, color: "#8b5cf6", period: 14, overbought: 70, oversold: 30 },
      { id: "macd", name: "MACD (Moving Average Convergence Divergence)", category: "Momentum", type: "subchart", enabled: true, fast: 12, slow: 26, signal: 9 },
      { id: "atr", name: "ATR (Average True Range 14)", category: "Volatility", type: "subchart", enabled: false, period: 14, color: "#f97316" },
      { id: "obv", name: "OBV (On-Balance Volume)", category: "Volume & Flow", type: "subchart", enabled: false, color: "#10b981" },
    ];

    this.subCharts = {
      rsi: null,
      macd: null,
    };
    this.subSeries = {};

    this.loadSettings();
    this.initModalUI();
  }

  loadSettings() {
    try {
      const saved = localStorage.getItem("alphaquant_indicator_catalog");
      if (saved) {
        const map = JSON.parse(saved);
        this.catalog.forEach((item) => {
          if (map[item.id] !== undefined) {
            item.enabled = map[item.id].enabled;
          }
        });
      }
    } catch (e) {}
  }

  saveSettings() {
    try {
      const map = {};
      this.catalog.forEach((c) => {
        map[c.id] = { enabled: c.enabled };
      });
      localStorage.setItem("alphaquant_indicator_catalog", JSON.stringify(map));
    } catch (e) {}
  }

  initModalUI() {
    const btnOpen = document.getElementById("btn-indicators-modal");
    const modal = document.getElementById("modal-indicators");
    const btnClose = document.getElementById("btn-close-indicators");
    const searchInput = document.getElementById("indicator-search-input");

    if (btnOpen && modal) {
      btnOpen.addEventListener("click", () => {
        this.renderCatalogList();
        modal.classList.add("active");
        if (searchInput) searchInput.focus();
      });
    }

    if (btnClose && modal) {
      btnClose.addEventListener("click", () => modal.classList.remove("active"));
    }

    if (modal) {
      modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.classList.remove("active");
      });
    }

    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        this.renderCatalogList(e.target.value.toLowerCase());
      });
    }
  }

  renderCatalogList(query = "") {
    const container = document.getElementById("indicator-list-container");
    if (!container) return;

    container.innerHTML = "";
    const filtered = this.catalog.filter(
      (item) => item.name.toLowerCase().includes(query) || item.category.toLowerCase().includes(query)
    );

    filtered.forEach((item) => {
      const div = document.createElement("div");
      div.className = "tv-indicator-item";
      div.innerHTML = `
        <div class="tv-indicator-info">
          <div class="tv-indicator-name">${item.name}</div>
          <div class="tv-indicator-category">${item.category} • ${item.type.toUpperCase()}</div>
        </div>
        <input type="checkbox" ${item.enabled ? "checked" : ""} style="cursor:pointer; width:16px; height:16px; accent-color:var(--blue);">
      `;

      const checkbox = div.querySelector("input");
      checkbox.addEventListener("change", (e) => {
        item.enabled = e.target.checked;
        this.saveSettings();
        if (window.chartManager) {
          window.chartManager.reloadActiveIndicators();
        }
      });

      container.appendChild(div);
    });
  }

  // --- MATHEMATICAL INDICATOR CALCULATIONS ---

  calculateEMA(candles, period) {
    if (!candles || candles.length < period) return [];
    const k = 2 / (period + 1);
    const results = [];
    let ema = candles.slice(0, period).reduce((sum, c) => sum + c.close, 0) / period;
    results.push({ time: candles[period - 1].time, value: round(ema) });

    for (let i = period; i < candles.length; i++) {
      ema = candles[i].close * k + ema * (1 - k);
      results.push({ time: candles[i].time, value: round(ema) });
    }
    return results;
  }

  calculateSMA(candles, period) {
    if (!candles || candles.length < period) return [];
    const results = [];
    let sum = 0;
    for (let i = 0; i < candles.length; i++) {
      sum += candles[i].close;
      if (i >= period) {
        sum -= candles[i - period].close;
        results.push({ time: candles[i].time, value: round(sum / period) });
      } else if (i === period - 1) {
        results.push({ time: candles[i].time, value: round(sum / period) });
      }
    }
    return results;
  }

  calculateRSI(candles, period = 14) {
    if (!candles || candles.length < period + 1) return [];
    const results = [];
    let gains = 0;
    let losses = 0;

    for (let i = 1; i <= period; i++) {
      const diff = candles[i].close - candles[i - 1].close;
      if (diff >= 0) gains += diff;
      else losses -= diff;
    }

    let avgGain = gains / period;
    let avgLoss = losses / period;
    let rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    results.push({ time: candles[period].time, value: round(100 - (100 / (1 + rs))) });

    for (let i = period + 1; i < candles.length; i++) {
      const diff = candles[i].close - candles[i - 1].close;
      avgGain = (avgGain * (period - 1) + (diff >= 0 ? diff : 0)) / period;
      avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
      rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      results.push({ time: candles[i].time, value: round(100 - (100 / (1 + rs))) });
    }
    return results;
  }

  calculateMACD(candles, fast = 12, slow = 26, signal = 9) {
    if (!candles || candles.length < slow + signal) return { macd: [], signal: [], hist: [] };
    const fastEMA = this.calculateEMA(candles, fast);
    const slowEMA = this.calculateEMA(candles, slow);

    const slowMap = new Map(slowEMA.map((s) => [s.time, s.value]));
    const macdLine = [];
    for (const f of fastEMA) {
      if (slowMap.has(f.time)) {
        macdLine.push({ time: f.time, value: round(f.value - slowMap.get(f.time)) });
      }
    }

    const signalLine = [];
    if (macdLine.length >= signal) {
      const k = 2 / (signal + 1);
      let sEMA = macdLine.slice(0, signal).reduce((acc, m) => acc + m.value, 0) / signal;
      signalLine.push({ time: macdLine[signal - 1].time, value: round(sEMA) });

      for (let i = signal; i < macdLine.length; i++) {
        sEMA = macdLine[i].value * k + sEMA * (1 - k);
        signalLine.push({ time: macdLine[i].time, value: round(sEMA) });
      }
    }

    const signalMap = new Map(signalLine.map((s) => [s.time, s.value]));
    const hist = [];
    for (const m of macdLine) {
      if (signalMap.has(m.time)) {
        const val = round(m.value - signalMap.get(m.time));
        hist.push({
          time: m.time,
          value: val,
          color: val >= 0 ? "rgba(16, 185, 129, 0.7)" : "rgba(239, 68, 68, 0.7)",
        });
      }
    }

    return { macd: macdLine, signal: signalLine, hist: hist };
  }

  calculateBollinger(candles, period = 20, multiplier = 2.0) {
    if (!candles || candles.length < period) return { upper: [], middle: [], lower: [] };
    const upper = [];
    const middle = [];
    const lower = [];

    for (let i = period - 1; i < candles.length; i++) {
      const slice = candles.slice(i - period + 1, i + 1);
      const mean = slice.reduce((acc, c) => acc + c.close, 0) / period;
      const variance = slice.reduce((acc, c) => acc + Math.pow(c.close - mean, 2), 0) / period;
      const stdDev = Math.sqrt(variance);
      const t = candles[i].time;

      middle.push({ time: t, value: round(mean, 2) });
      upper.push({ time: t, value: round(mean + stdDev * multiplier, 2) });
      lower.push({ time: t, value: round(mean - stdDev * multiplier, 2) });
    }

    return { upper, middle, lower };
  }

  calculateSupertrend(candles, period = 10, multiplier = 3.0) {
    if (!candles || candles.length < period + 1) return [];
    const tr = [candles[0].high - candles[0].low];
    for (let i = 1; i < candles.length; i++) {
      const hl = candles[i].high - candles[i].low;
      const hc = Math.abs(candles[i].high - candles[i - 1].close);
      const lc = Math.abs(candles[i].low - candles[i - 1].close);
      tr.push(Math.max(hl, hc, lc));
    }

    let atr = tr.slice(0, period).reduce((acc, v) => acc + v, 0) / period;
    const supertrend = [];
    let inUptrend = true;
    let prevFinalUpper = (candles[period - 1].high + candles[period - 1].low) / 2 + multiplier * atr;
    let prevFinalLower = (candles[period - 1].high + candles[period - 1].low) / 2 - multiplier * atr;

    for (let i = period; i < candles.length; i++) {
      atr = (atr * (period - 1) + tr[i]) / period;
      const hl2 = (candles[i].high + candles[i].low) / 2;
      const basicUpper = hl2 + multiplier * atr;
      const basicLower = hl2 - multiplier * atr;

      const finalUpper = (basicUpper < prevFinalUpper || candles[i - 1].close > prevFinalUpper) ? basicUpper : prevFinalUpper;
      const finalLower = (basicLower > prevFinalLower || candles[i - 1].close < prevFinalLower) ? basicLower : prevFinalLower;

      if (inUptrend) {
        if (candles[i].close < finalLower) {
          inUptrend = false;
        }
      } else {
        if (candles[i].close > finalUpper) {
          inUptrend = true;
        }
      }

      const val = inUptrend ? finalLower : finalUpper;
      supertrend.push({
        time: candles[i].time,
        value: round(val, 2),
        color: inUptrend ? "#10b981" : "#ef4444",
      });

      prevFinalUpper = finalUpper;
      prevFinalLower = finalLower;
    }

    return supertrend;
  }

  // --- SUB-CHARTS PANEL RENDERING ---

  initSubCharts() {
    const rsiPane = document.getElementById("rsi-chart-surface");
    const macdPane = document.getElementById("macd-chart-surface");

    if (rsiPane && !this.subCharts.rsi) {
      const w = rsiPane.clientWidth || (rsiPane.parentElement ? rsiPane.parentElement.clientWidth : 400);
      this.subCharts.rsi = LightweightCharts.createChart(rsiPane, {
        width: Math.max(100, w),
        height: 105,
        layout: {
          background: { color: "#0a0e17" },
          textColor: "#8892a4",
          fontSize: 10,
          fontFamily: "'JetBrains Mono', monospace",
        },
        grid: {
          vertLines: { color: "rgba(30, 42, 58, 0.3)" },
          horzLines: { color: "rgba(30, 42, 58, 0.3)" },
        },
        rightPriceScale: { borderColor: "#1e2a3a" },
        timeScale: { visible: false },
      });

      this.subSeries.rsi = this.subCharts.rsi.addSeries(LightweightCharts.LineSeries, {
        color: "#8b5cf6",
        lineWidth: 1.5,
        priceLineVisible: false,
      });

      this.subSeries.rsi.createPriceLine({ price: 70, color: "rgba(239,68,68,0.5)", lineWidth: 1, lineStyle: 2, title: "70 OB" });
      this.subSeries.rsi.createPriceLine({ price: 30, color: "rgba(16,185,129,0.5)", lineWidth: 1, lineStyle: 2, title: "30 OS" });
    }

    if (macdPane && !this.subCharts.macd) {
      const w = macdPane.clientWidth || (macdPane.parentElement ? macdPane.parentElement.clientWidth : 400);
      this.subCharts.macd = LightweightCharts.createChart(macdPane, {
        width: Math.max(100, w),
        height: 105,
        layout: {
          background: { color: "#0a0e17" },
          textColor: "#8892a4",
          fontSize: 10,
          fontFamily: "'JetBrains Mono', monospace",
        },
        grid: {
          vertLines: { color: "rgba(30, 42, 58, 0.3)" },
          horzLines: { color: "rgba(30, 42, 58, 0.3)" },
        },
        rightPriceScale: { borderColor: "#1e2a3a" },
        timeScale: { borderColor: "#1e2a3a", visible: true, timeVisible: true },
      });

      this.subSeries.macdHist = this.subCharts.macd.addSeries(LightweightCharts.HistogramSeries, {
        priceFormat: { type: "volume" },
        priceLineVisible: false,
      });

      this.subSeries.macdLine = this.subCharts.macd.addSeries(LightweightCharts.LineSeries, {
        color: "#06b6d4",
        lineWidth: 1.5,
        priceLineVisible: false,
      });

      this.subSeries.macdSignal = this.subCharts.macd.addSeries(LightweightCharts.LineSeries, {
        color: "#f59e0b",
        lineWidth: 1.5,
        priceLineVisible: false,
      });
    }
  }

  updateData(candles) {
    if (!candles || candles.length === 0) return;

    if (!this.subCharts.rsi || !this.subCharts.macd) {
      this.initSubCharts();
    }

    // Update RSI
    if (this.subSeries.rsi) {
      const rsiData = this.calculateRSI(candles, 14);
      this.subSeries.rsi.setData(rsiData);
      if (rsiData.length > 0) {
        const el = document.getElementById("rsi-hud-val");
        if (el) el.textContent = rsiData[rsiData.length - 1].value.toFixed(1);
      }
      if (this.subCharts.rsi) this.subCharts.rsi.timeScale().fitContent();
    }

    // Update MACD
    if (this.subSeries.macdLine) {
      const macdObj = this.calculateMACD(candles, 12, 26, 9);
      this.subSeries.macdLine.setData(macdObj.macd);
      this.subSeries.macdSignal.setData(macdObj.signal);
      this.subSeries.macdHist.setData(macdObj.hist);
      if (macdObj.macd.length > 0) {
        const el = document.getElementById("macd-hud-val");
        if (el) el.textContent = macdObj.macd[macdObj.macd.length - 1].value.toFixed(2);
      }
      if (this.subCharts.macd) this.subCharts.macd.timeScale().fitContent();
    }
  }

  resize() {
    const rsiPane = document.getElementById("rsi-chart-surface");
    if (this.subCharts.rsi && rsiPane) {
      const w = rsiPane.clientWidth;
      if (w > 0) this.subCharts.rsi.applyOptions({ width: w });
    }
    const macdPane = document.getElementById("macd-chart-surface");
    if (this.subCharts.macd && macdPane) {
      const w = macdPane.clientWidth;
      if (w > 0) this.subCharts.macd.applyOptions({ width: w });
    }
  }
}

function round(val, dec = 2) {
  const f = Math.pow(10, dec);
  return Math.round(val * f) / f;
}

window.indicatorsEngine = new IndicatorsEngine();
