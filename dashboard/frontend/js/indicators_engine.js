/**
 * AlphaQuant AI Pro - Complete TradingView Technical Indicators Engine
 *
 * Implements all 16 institutional indicators:
 * Overlays: EMA (9, 20, 50, 100, 200), SMA (20, 50, 200), Session VWAP, Anchored VWAP,
 *           Bollinger Bands, Supertrend, Ichimoku Cloud, Volume SMA
 * Subcharts: RSI (14), MACD (12, 26, 9), Stochastic RSI, ATR (14), ADX (14), CCI (20), OBV, MFI (14)
 */

function getPriceDecimals(candles) {
  if (!candles || candles.length === 0) return 2;
  const p = Math.abs(candles[candles.length - 1].close || 0);
  if (p < 0.01) return 6;
  if (p < 1.0) return 5;
  if (p < 50.0) return 4;
  return 2;
}

function adaptRound(val, dec = 2) {
  if (val === null || val === undefined || isNaN(val)) return 0;
  const f = Math.pow(10, dec);
  return Math.round(val * f) / f;
}

class IndicatorsEngine {
  constructor() {
    this.catalog = [
      { id: "ema9", name: "EMA 9 (Exponential Moving Average)", category: "Moving Average", type: "overlay", enabled: false, color: "#38bdf8", period: 9 },
      { id: "ema20", name: "EMA 20", category: "Moving Average", type: "overlay", enabled: true, color: "#818cf8", period: 20 },
      { id: "ema50", name: "EMA 50", category: "Moving Average", type: "overlay", enabled: true, color: "#3b82f6", period: 50 },
      { id: "ema100", name: "EMA 100", category: "Moving Average", type: "overlay", enabled: false, color: "#a855f7", period: 100 },
      { id: "ema200", name: "EMA 200 (Macro Trend)", category: "Moving Average", type: "overlay", enabled: true, color: "#f59e0b", period: 200 },
      { id: "sma20", name: "SMA 20 (Simple Moving Average)", category: "Moving Average", type: "overlay", enabled: false, color: "#94a3b8", period: 20 },
      { id: "sma50", name: "SMA 50", category: "Moving Average", type: "overlay", enabled: false, color: "#cbd5e1", period: 50 },
      { id: "sma200", name: "SMA 200 (Long-term Baseline)", category: "Moving Average", type: "overlay", enabled: false, color: "#e2e8f0", period: 200 },
      { id: "vwap", name: "Session VWAP (Volume Weighted Avg Price)", category: "Volume & Flow", type: "overlay", enabled: true, color: "#06b6d4" },
      { id: "session_shading", name: "Market Sessions Shading (Asian / London / NY)", category: "Volume & Flow", type: "overlay", enabled: false, color: "#38bdf8" },
      { id: "anchored_vwap", name: "Anchored VWAP (Major Pivot Anchor)", category: "Volume & Flow", type: "overlay", enabled: false, color: "#0ea5e9" },
      { id: "bollinger", name: "Bollinger Bands (20, 2.0)", category: "Volatility", type: "overlay", enabled: false, color: "#c084fc", period: 20, stdDev: 2.0 },
      { id: "supertrend", name: "Supertrend (10, 3.0)", category: "Trend", type: "overlay", enabled: false, period: 10, multiplier: 3.0 },
      { id: "ichimoku", name: "Ichimoku Cloud (9, 26, 52)", category: "Trend", type: "overlay", enabled: false },
      { id: "volume_sma", name: "Volume SMA (20)", category: "Volume & Flow", type: "overlay", enabled: false, color: "#64748b", period: 20 },
      { id: "rsi", name: "RSI (Relative Strength Index 14)", category: "Momentum", type: "subchart", enabled: true, color: "#8b5cf6", period: 14, overbought: 70, oversold: 30 },
      { id: "macd", name: "MACD (Moving Average Convergence Divergence)", category: "Momentum", type: "subchart", enabled: true, fast: 12, slow: 26, signal: 9 },
      { id: "stoch_rsi", name: "Stochastic RSI (14, 14, 3, 3)", category: "Momentum", type: "subchart", enabled: false, color: "#ec4899" },
      { id: "atr", name: "ATR (Average True Range 14)", category: "Volatility", type: "subchart", enabled: false, period: 14, color: "#f97316" },
      { id: "adx", name: "ADX (Average Directional Index 14)", category: "Trend", type: "subchart", enabled: false, period: 14, color: "#eab308" },
      { id: "cci", name: "CCI (Commodity Channel Index 20)", category: "Momentum", type: "subchart", enabled: false, period: 20, color: "#14b8a6" },
      { id: "obv", name: "OBV (On-Balance Volume)", category: "Volume & Flow", type: "subchart", enabled: false, color: "#10b981" },
      { id: "mfi", name: "MFI (Money Flow Index 14)", category: "Volume & Flow", type: "subchart", enabled: false, period: 14, color: "#22c55e" },
    ];

    this.subCharts = {
      rsi: null,
      macd: null,
      extra: null,
    };
    this.subSeries = {};
    this.extraSeries = {};
    this.lastCandles = [];

    this.loadSettings();
    this.initModalUI();
  }

  isEnabled(id) {
    const found = this.catalog.find((c) => c.id === id);
    return found ? !!found.enabled : false;
  }

  toggle(id) {
    const item = this.catalog.find((c) => c.id === id);
    if (item) {
      item.enabled = !item.enabled;
      this.saveSettings();
      if (window.chartManager) {
        window.chartManager.reloadActiveIndicators();
      }
      if (this.lastCandles && this.lastCandles.length > 0) {
        this.updateData(this.lastCandles);
      }
      return item.enabled;
    }
    return false;
  }

  setEnabled(id, state) {
    const item = this.catalog.find((c) => c.id === id);
    if (item) {
      item.enabled = !!state;
      this.saveSettings();
      if (window.chartManager) {
        window.chartManager.reloadActiveIndicators();
      }
      if (this.lastCandles && this.lastCandles.length > 0) {
        this.updateData(this.lastCandles);
      }
      return item.enabled;
    }
    return false;
  }

  loadSettings() {
    try {
      const saved = localStorage.getItem("alphaquant_indicator_catalog");
      if (saved) {
        const map = JSON.parse(saved);
        this.catalog.forEach((item) => {
          if (map[item.id] !== undefined) {
            item.enabled = !!map[item.id].enabled;
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
        this.renderCatalogList(searchInput ? searchInput.value.toLowerCase() : "");
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
      (item) =>
        item.name.toLowerCase().includes(query) ||
        item.category.toLowerCase().includes(query) ||
        item.id.toLowerCase().includes(query)
    );

    if (filtered.length === 0) {
      container.innerHTML = `<div class="text-center text-dim py-16">No indicators matched "${query}"</div>`;
      return;
    }

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
        if (this.lastCandles.length > 0) {
          this.updateData(this.lastCandles);
        }
      });

      div.addEventListener("click", (e) => {
        if (e.target !== checkbox) {
          checkbox.checked = !checkbox.checked;
          checkbox.dispatchEvent(new Event("change"));
        }
      });

      container.appendChild(div);
    });
  }

  // --- MATHEMATICAL INDICATOR CALCULATIONS (ADAPTIVE PRECISION) ---

  calculateEMA(candles, period) {
    if (!candles || candles.length < period) return [];
    const dec = getPriceDecimals(candles);
    const k = 2 / (period + 1);
    const results = [];
    let ema = candles.slice(0, period).reduce((sum, c) => sum + c.close, 0) / period;
    results.push({ time: candles[period - 1].time, value: adaptRound(ema, dec) });

    for (let i = period; i < candles.length; i++) {
      ema = candles[i].close * k + ema * (1 - k);
      results.push({ time: candles[i].time, value: adaptRound(ema, dec) });
    }
    return results;
  }

  calculateSMA(candles, period) {
    if (!candles || candles.length < period) return [];
    const dec = getPriceDecimals(candles);
    const results = [];
    let sum = 0;
    for (let i = 0; i < candles.length; i++) {
      sum += candles[i].close;
      if (i >= period) {
        sum -= candles[i - period].close;
        results.push({ time: candles[i].time, value: adaptRound(sum / period, dec) });
      } else if (i === period - 1) {
        results.push({ time: candles[i].time, value: adaptRound(sum / period, dec) });
      }
    }
    return results;
  }

  calculateAnchoredVWAP(candles, anchorIdx = null) {
    if (!candles || candles.length < 10) return [];
    const dec = getPriceDecimals(candles);
    // Default anchor: lowest low pivot in recent 100 bars
    let start = anchorIdx;
    if (start === null) {
      const lookbackStart = Math.max(0, candles.length - 100);
      let minLow = Infinity;
      start = lookbackStart;
      for (let i = lookbackStart; i < candles.length - 2; i++) {
        if (candles[i].low < minLow) {
          minLow = candles[i].low;
          start = i;
        }
      }
    }
    let cumPV = 0;
    let cumVol = 0;
    const out = [];
    for (let i = start; i < candles.length; i++) {
      const c = candles[i];
      const tp = (c.high + c.low + c.close) / 3;
      const v = c.volume || 1;
      cumPV += tp * v;
      cumVol += v;
      out.push({ time: c.time, value: adaptRound(cumPV / Math.max(cumVol, 1e-8), dec) });
    }
    return out;
  }

  calculateVolumeSMA(candles, period = 20) {
    if (!candles || candles.length < period) return [];
    const out = [];
    let sum = 0;
    for (let i = 0; i < candles.length; i++) {
      sum += candles[i].volume || 0;
      if (i >= period) {
        sum -= candles[i - period].volume || 0;
        out.push({ time: candles[i].time, value: adaptRound(sum / period, 2) });
      } else if (i === period - 1) {
        out.push({ time: candles[i].time, value: adaptRound(sum / period, 2) });
      }
    }
    return out;
  }

  calculateIchimoku(candles) {
    if (!candles || candles.length < 52) return { tenkan: [], kijun: [], senkouA: [], senkouB: [] };
    const dec = getPriceDecimals(candles);
    const midHL = (idx, len) => {
      const start = Math.max(0, idx - len + 1);
      let hi = -Infinity;
      let lo = Infinity;
      for (let j = start; j <= idx; j++) {
        if (candles[j].high > hi) hi = candles[j].high;
        if (candles[j].low < lo) lo = candles[j].low;
      }
      return (hi + lo) / 2;
    };

    const tenkan = [];
    const kijun = [];
    const senkouA = [];
    const senkouB = [];

    for (let i = 25; i < candles.length; i++) {
      const t = candles[i].time;
      const tk = midHL(i, 9);
      const kj = midHL(i, 26);
      tenkan.push({ time: t, value: adaptRound(tk, dec) });
      kijun.push({ time: t, value: adaptRound(kj, dec) });
      if (i >= 51) {
        senkouA.push({ time: t, value: adaptRound((tk + kj) / 2, dec) });
        senkouB.push({ time: t, value: adaptRound(midHL(i, 52), dec) });
      }
    }
    return { tenkan, kijun, senkouA, senkouB };
  }

  calculateBollinger(candles, period = 20, multiplier = 2.0) {
    if (!candles || candles.length < period) return { upper: [], middle: [], lower: [] };
    const dec = getPriceDecimals(candles);
    const upper = [];
    const middle = [];
    const lower = [];

    for (let i = period - 1; i < candles.length; i++) {
      const slice = candles.slice(i - period + 1, i + 1);
      const mean = slice.reduce((acc, c) => acc + c.close, 0) / period;
      const variance = slice.reduce((acc, c) => acc + Math.pow(c.close - mean, 2), 0) / period;
      const stdDev = Math.sqrt(variance);
      const t = candles[i].time;

      middle.push({ time: t, value: adaptRound(mean, dec) });
      upper.push({ time: t, value: adaptRound(mean + stdDev * multiplier, dec) });
      lower.push({ time: t, value: adaptRound(mean - stdDev * multiplier, dec) });
    }

    return { upper, middle, lower };
  }

  calculateSupertrend(candles, period = 10, multiplier = 3.0) {
    if (!candles || candles.length < period + 1) return [];
    const dec = getPriceDecimals(candles);
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

      const finalUpper = basicUpper < prevFinalUpper || candles[i - 1].close > prevFinalUpper ? basicUpper : prevFinalUpper;
      const finalLower = basicLower > prevFinalLower || candles[i - 1].close < prevFinalLower ? basicLower : prevFinalLower;

      if (inUptrend) {
        if (candles[i].close < finalLower) inUptrend = false;
      } else {
        if (candles[i].close > finalUpper) inUptrend = true;
      }

      const val = inUptrend ? finalLower : finalUpper;
      supertrend.push({
        time: candles[i].time,
        value: adaptRound(val, dec),
        color: inUptrend ? "#10b981" : "#ef4444",
      });

      prevFinalUpper = finalUpper;
      prevFinalLower = finalLower;
    }

    return supertrend;
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
    results.push({ time: candles[period].time, value: adaptRound(100 - 100 / (1 + rs), 2) });

    for (let i = period + 1; i < candles.length; i++) {
      const diff = candles[i].close - candles[i - 1].close;
      avgGain = (avgGain * (period - 1) + (diff >= 0 ? diff : 0)) / period;
      avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
      rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      results.push({ time: candles[i].time, value: adaptRound(100 - 100 / (1 + rs), 2) });
    }
    return results;
  }

  calculateStochRSI(candles, rsiPeriod = 14, stochPeriod = 14, kSmooth = 3) {
    const rsi = this.calculateRSI(candles, rsiPeriod);
    if (rsi.length < stochPeriod) return [];
    const rawK = [];
    for (let i = stochPeriod - 1; i < rsi.length; i++) {
      const win = rsi.slice(i - stochPeriod + 1, i + 1).map((r) => r.value);
      const minR = Math.min(...win);
      const maxR = Math.max(...win);
      const val = maxR === minR ? 50 : ((rsi[i].value - minR) / (maxR - minR)) * 100;
      rawK.push({ time: rsi[i].time, value: val });
    }
    const out = [];
    for (let i = kSmooth - 1; i < rawK.length; i++) {
      const avg = rawK.slice(i - kSmooth + 1, i + 1).reduce((s, x) => s + x.value, 0) / kSmooth;
      out.push({ time: rawK[i].time, value: adaptRound(avg, 2) });
    }
    return out;
  }

  calculateMACD(candles, fast = 12, slow = 26, signal = 9) {
    if (!candles || candles.length < slow + signal) return { macd: [], signal: [], hist: [] };
    const dec = getPriceDecimals(candles) + 1;
    const fastEMA = this.calculateEMA(candles, fast);
    const slowEMA = this.calculateEMA(candles, slow);

    const slowMap = new Map(slowEMA.map((s) => [s.time, s.value]));
    const macdLine = [];
    for (const f of fastEMA) {
      if (slowMap.has(f.time)) {
        macdLine.push({ time: f.time, value: adaptRound(f.value - slowMap.get(f.time), dec) });
      }
    }

    const signalLine = [];
    if (macdLine.length >= signal) {
      const k = 2 / (signal + 1);
      let sEMA = macdLine.slice(0, signal).reduce((acc, m) => acc + m.value, 0) / signal;
      signalLine.push({ time: macdLine[signal - 1].time, value: adaptRound(sEMA, dec) });

      for (let i = signal; i < macdLine.length; i++) {
        sEMA = macdLine[i].value * k + sEMA * (1 - k);
        signalLine.push({ time: macdLine[i].time, value: adaptRound(sEMA, dec) });
      }
    }

    const signalMap = new Map(signalLine.map((s) => [s.time, s.value]));
    const hist = [];
    for (const m of macdLine) {
      if (signalMap.has(m.time)) {
        const val = adaptRound(m.value - signalMap.get(m.time), dec);
        hist.push({
          time: m.time,
          value: val,
          color: val >= 0 ? "rgba(16, 185, 129, 0.7)" : "rgba(239, 68, 68, 0.7)",
        });
      }
    }

    return { macd: macdLine, signal: signalLine, hist: hist };
  }

  calculateATR(candles, period = 14) {
    if (!candles || candles.length < period + 1) return [];
    const dec = getPriceDecimals(candles);
    const tr = [candles[0].high - candles[0].low];
    for (let i = 1; i < candles.length; i++) {
      tr.push(Math.max(
        candles[i].high - candles[i].low,
        Math.abs(candles[i].high - candles[i - 1].close),
        Math.abs(candles[i].low - candles[i - 1].close)
      ));
    }
    let atr = tr.slice(0, period).reduce((a, b) => a + b, 0) / period;
    const out = [{ time: candles[period - 1].time, value: adaptRound(atr, dec) }];
    for (let i = period; i < candles.length; i++) {
      atr = (atr * (period - 1) + tr[i]) / period;
      out.push({ time: candles[i].time, value: adaptRound(atr, dec) });
    }
    return out;
  }

  calculateADX(candles, period = 14) {
    if (!candles || candles.length < period * 2 + 1) return [];
    const plusDM = [0];
    const minusDM = [0];
    const tr = [candles[0].high - candles[0].low];
    for (let i = 1; i < candles.length; i++) {
      const upMove = candles[i].high - candles[i - 1].high;
      const downMove = candles[i - 1].low - candles[i].low;
      plusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
      minusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);
      tr.push(Math.max(
        candles[i].high - candles[i].low,
        Math.abs(candles[i].high - candles[i - 1].close),
        Math.abs(candles[i].low - candles[i - 1].close)
      ));
    }

    let tr14 = tr.slice(1, period + 1).reduce((a, b) => a + b, 0);
    let pDM14 = plusDM.slice(1, period + 1).reduce((a, b) => a + b, 0);
    let mDM14 = minusDM.slice(1, period + 1).reduce((a, b) => a + b, 0);
    const dxList = [];

    for (let i = period + 1; i < candles.length; i++) {
      tr14 = tr14 - tr14 / period + tr[i];
      pDM14 = pDM14 - pDM14 / period + plusDM[i];
      mDM14 = mDM14 - mDM14 / period + minusDM[i];
      const pDI = tr14 === 0 ? 0 : (pDM14 / tr14) * 100;
      const mDI = tr14 === 0 ? 0 : (mDM14 / tr14) * 100;
      const denom = pDI + mDI;
      const dx = denom === 0 ? 0 : (Math.abs(pDI - mDI) / denom) * 100;
      dxList.push({ time: candles[i].time, dx });
    }

    if (dxList.length < period) return [];
    let adx = dxList.slice(0, period).reduce((s, d) => s + d.dx, 0) / period;
    const out = [{ time: dxList[period - 1].time, value: adaptRound(adx, 2) }];
    for (let i = period; i < dxList.length; i++) {
      adx = (adx * (period - 1) + dxList[i].dx) / period;
      out.push({ time: dxList[i].time, value: adaptRound(adx, 2) });
    }
    return out;
  }

  calculateCCI(candles, period = 20) {
    if (!candles || candles.length < period) return [];
    const out = [];
    for (let i = period - 1; i < candles.length; i++) {
      const tps = [];
      for (let j = i - period + 1; j <= i; j++) {
        tps.push((candles[j].high + candles[j].low + candles[j].close) / 3);
      }
      const sma = tps.reduce((a, b) => a + b, 0) / period;
      const mad = tps.reduce((a, b) => a + Math.abs(b - sma), 0) / period;
      const cci = mad === 0 ? 0 : (tps[tps.length - 1] - sma) / (0.015 * mad);
      out.push({ time: candles[i].time, value: adaptRound(cci, 2) });
    }
    return out;
  }

  calculateOBV(candles) {
    if (!candles || candles.length === 0) return [];
    let obv = 0;
    const out = [{ time: candles[0].time, value: 0 }];
    for (let i = 1; i < candles.length; i++) {
      if (candles[i].close > candles[i - 1].close) obv += candles[i].volume || 0;
      else if (candles[i].close < candles[i - 1].close) obv -= candles[i].volume || 0;
      out.push({ time: candles[i].time, value: adaptRound(obv, 2) });
    }
    return out;
  }

  calculateMFI(candles, period = 14) {
    if (!candles || candles.length < period + 1) return [];
    const posMF = [];
    const negMF = [];
    for (let i = 1; i < candles.length; i++) {
      const tpPrev = (candles[i - 1].high + candles[i - 1].low + candles[i - 1].close) / 3;
      const tpCurr = (candles[i].high + candles[i].low + candles[i].close) / 3;
      const mf = tpCurr * (candles[i].volume || 0);
      posMF.push(tpCurr > tpPrev ? mf : 0);
      negMF.push(tpCurr < tpPrev ? mf : 0);
    }
    const out = [];
    for (let i = period - 1; i < posMF.length; i++) {
      const posSum = posMF.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
      const negSum = negMF.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
      const mfi = negSum === 0 ? 100 : 100 - 100 / (1 + posSum / negSum);
      out.push({ time: candles[i + 1].time, value: adaptRound(mfi, 2) });
    }
    return out;
  }

  // --- SUB-CHARTS PANEL RENDERING & TIME-AXIS SYNC ---

  initSubCharts() {
    const rsiPane = document.getElementById("rsi-chart-surface");
    const macdPane = document.getElementById("macd-chart-surface");

    if (rsiPane && !this.subCharts.rsi) {
      const w = rsiPane.clientWidth || (rsiPane.parentElement ? rsiPane.parentElement.clientWidth : 400);
      this.subCharts.rsi = LightweightCharts.createChart(rsiPane, {
        width: Math.max(100, w),
        height: 80,
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
        rightPriceScale: { borderColor: "#1e2a3a", minimumWidth: 68 },
        timeScale: { visible: false },
      });

      this.subSeries.rsi = this.subCharts.rsi.addSeries(LightweightCharts.LineSeries, {
        color: "#8b5cf6",
        lineWidth: 1.5,
        priceLineVisible: false,
      });

      this.subSeries.rsi.createPriceLine({ price: 70, color: "rgba(239,68,68,0.45)", lineWidth: 1, lineStyle: 2, title: "70" });
      this.subSeries.rsi.createPriceLine({ price: 30, color: "rgba(16,185,129,0.45)", lineWidth: 1, lineStyle: 2, title: "30" });
    }

    if (macdPane && !this.subCharts.macd) {
      const w = macdPane.clientWidth || (macdPane.parentElement ? macdPane.parentElement.clientWidth : 400);
      this.subCharts.macd = LightweightCharts.createChart(macdPane, {
        width: Math.max(100, w),
        height: 85,
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
        rightPriceScale: { borderColor: "#1e2a3a", minimumWidth: 68 },
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

    // Dynamic extra oscillator pane if user enables Stoch RSI, ATR, ADX, CCI, OBV, or MFI
    let extraPaneEl = document.getElementById("extra-subchart-pane");
    const tray = document.getElementById("tv-subcharts-container");
    if (!extraPaneEl && tray) {
      extraPaneEl = document.createElement("div");
      extraPaneEl.id = "extra-subchart-pane";
      extraPaneEl.className = "tv-subchart-pane";
      extraPaneEl.style.display = "none";
      extraPaneEl.innerHTML = `
        <div class="tv-subchart-header">
          <span class="tv-subchart-title" id="extra-subchart-title">Oscillator</span>
          <span id="extra-hud-val" class="tv-subchart-val">--</span>
        </div>
        <div id="extra-chart-surface" class="tv-subchart-render"></div>
      `;
      tray.appendChild(extraPaneEl);
    }

    const extraSurface = document.getElementById("extra-chart-surface");
    if (extraSurface && !this.subCharts.extra) {
      const w = tray ? tray.clientWidth : 400;
      this.subCharts.extra = LightweightCharts.createChart(extraSurface, {
        width: Math.max(100, w),
        height: 75,
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
        rightPriceScale: { borderColor: "#1e2a3a", minimumWidth: 68 },
        timeScale: { visible: false },
      });
    }
  }

  syncTimeScale(logicalRange) {
    if (!logicalRange || !Number.isFinite(logicalRange.from) || !Number.isFinite(logicalRange.to)) return;
    try {
      if (this.subCharts.rsi) this.subCharts.rsi.timeScale().setVisibleLogicalRange(logicalRange);
    } catch (e) {}
    try {
      if (this.subCharts.macd) this.subCharts.macd.timeScale().setVisibleLogicalRange(logicalRange);
    } catch (e) {}
    try {
      if (this.subCharts.extra) this.subCharts.extra.timeScale().setVisibleLogicalRange(logicalRange);
    } catch (e) {}
  }

  padWhitespace(candles, points) {
    if (!Array.isArray(candles) || candles.length === 0) return [];
    if (!Array.isArray(points) || points.length === 0) {
      return candles.map((c) => ({ time: c.time }));
    }
    const map = new Map();
    for (let i = 0; i < points.length; i++) {
      const p = points[i];
      if (p && Number.isFinite(p.time) && Number.isFinite(p.value)) {
        map.set(p.time, p);
      }
    }
    return candles.map((c) => map.get(c.time) || { time: c.time });
  }

  updateData(candles) {
    if (!Array.isArray(candles) || candles.length === 0) return;
    this.lastCandles = candles;

    if (!this.subCharts.rsi || !this.subCharts.macd) {
      this.initSubCharts();
    }

    const rsiPaneWrap = document.getElementById("rsi-subchart-pane");
    const macdPaneWrap = document.getElementById("macd-subchart-pane");
    const extraPaneWrap = document.getElementById("extra-subchart-pane");

    // 1. RSI Pane (padded to 1:1 candle time indices so zoom/pan aligns pixel-for-pixel)
    const rsiOn = this.isEnabled("rsi");
    if (rsiPaneWrap) rsiPaneWrap.style.display = rsiOn ? "flex" : "none";
    if (rsiOn && this.subSeries.rsi) {
      const rsiData = this.calculateRSI(candles, 14);
      this.subSeries.rsi.setData(this.padWhitespace(candles, rsiData));
      if (rsiData.length > 0) {
        const el = document.getElementById("rsi-hud-val");
        if (el) el.textContent = rsiData[rsiData.length - 1].value.toFixed(1);
      }
    }

    // 2. MACD Pane
    const macdOn = this.isEnabled("macd");
    if (macdPaneWrap) macdPaneWrap.style.display = macdOn ? "flex" : "none";
    if (macdOn && this.subSeries.macdLine) {
      const macdObj = this.calculateMACD(candles, 12, 26, 9);
      this.subSeries.macdLine.setData(this.padWhitespace(candles, macdObj.macd));
      this.subSeries.macdSignal.setData(this.padWhitespace(candles, macdObj.signal));
      this.subSeries.macdHist.setData(this.padWhitespace(candles, macdObj.hist));
      if (macdObj.macd.length > 0) {
        const el = document.getElementById("macd-hud-val");
        if (el) el.textContent = macdObj.macd[macdObj.macd.length - 1].value.toFixed(2);
      }
    }

    // 3. Extra Subcharts (Stoch RSI, ATR, ADX, CCI, OBV, MFI)
    const extraIds = ["stoch_rsi", "atr", "adx", "cci", "obv", "mfi"];
    const activeExtras = extraIds.filter((id) => this.isEnabled(id));
    if (extraPaneWrap) {
      extraPaneWrap.style.display = activeExtras.length > 0 ? "flex" : "none";
    }

    if (this.subCharts.extra) {
      // Remove inactive series
      extraIds.forEach((id) => {
        if (!activeExtras.includes(id) && this.extraSeries[id]) {
          try { this.subCharts.extra.removeSeries(this.extraSeries[id]); } catch (e) {}
          delete this.extraSeries[id];
        }
      });

      const hudSummaries = [];
      activeExtras.forEach((id) => {
        const item = this.catalog.find((c) => c.id === id);
        if (!this.extraSeries[id]) {
          this.extraSeries[id] = this.subCharts.extra.addSeries(LightweightCharts.LineSeries, {
            color: (item && item.color) || "#38bdf8",
            lineWidth: 1.5,
            title: id.toUpperCase(),
            priceLineVisible: false,
          });
        }
        let seriesData = [];
        if (id === "stoch_rsi") seriesData = this.calculateStochRSI(candles);
        else if (id === "atr") seriesData = this.calculateATR(candles, 14);
        else if (id === "adx") seriesData = this.calculateADX(candles, 14);
        else if (id === "cci") seriesData = this.calculateCCI(candles, 20);
        else if (id === "obv") seriesData = this.calculateOBV(candles);
        else if (id === "mfi") seriesData = this.calculateMFI(candles, 14);

        this.extraSeries[id].setData(this.padWhitespace(candles, seriesData));
        if (seriesData.length > 0) {
          hudSummaries.push(`${id.toUpperCase()}: ${seriesData[seriesData.length - 1].value}`);
        }
      });

      const extraTitle = document.getElementById("extra-subchart-title");
      const extraHud = document.getElementById("extra-hud-val");
      if (extraTitle) extraTitle.textContent = activeExtras.map((s) => s.toUpperCase()).join(" + ") || "Oscillator";
      if (extraHud) extraHud.textContent = hudSummaries.join(" | ") || "--";
    }

    // Re-sync height based on active panes
    this.resize();
    if (window.chartManager && window.chartManager.charts[0]) {
      const range = window.chartManager.charts[0].chart.timeScale().getVisibleLogicalRange();
      this.syncTimeScale(range);
    }
  }

  resize() {
    const tray = document.getElementById("tv-subcharts-container");
    const activeCount = [this.isEnabled("rsi"), this.isEnabled("macd"), ["stoch_rsi", "atr", "adx", "cci", "obv", "mfi"].some((id) => this.isEnabled(id))].filter(Boolean).length;
    if (tray) {
      if (activeCount === 0) {
        tray.style.height = "0px";
        tray.style.minHeight = "0px";
      } else {
        const h = activeCount === 1 ? 110 : activeCount === 2 ? 200 : 275;
        tray.style.height = `${h}px`;
        tray.style.minHeight = `${h}px`;
        tray.style.maxHeight = `${h}px`;
      }
    }

    const rsiPane = document.getElementById("rsi-chart-surface");
    if (this.subCharts.rsi && rsiPane && rsiPane.clientWidth > 0) {
      this.subCharts.rsi.applyOptions({ width: rsiPane.clientWidth, height: Math.max(60, rsiPane.clientHeight || 75) });
    }
    const macdPane = document.getElementById("macd-chart-surface");
    if (this.subCharts.macd && macdPane && macdPane.clientWidth > 0) {
      this.subCharts.macd.applyOptions({ width: macdPane.clientWidth, height: Math.max(60, macdPane.clientHeight || 80) });
    }
    const extraSurface = document.getElementById("extra-chart-surface");
    if (this.subCharts.extra && extraSurface && extraSurface.clientWidth > 0) {
      this.subCharts.extra.applyOptions({ width: extraSurface.clientWidth, height: Math.max(60, extraSurface.clientHeight || 70) });
    }
  }
}

window.indicatorsEngine = new IndicatorsEngine();
