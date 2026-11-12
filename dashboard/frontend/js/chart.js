/**
 * AlphaQuant Pro - TradingView Chart Workspace Engine (Lightweight Charts v5)
 *
 * Capabilities:
 * - Dominant TradingView layout (~80% workspace width)
 * - Lightweight Charts v5 Candlestick, Histogram, and Line Series
 * - Backward historical scroll pagination (auto-fetching older chunks via API)
 * - 365+ Days IndexedDB browser caching
 * - Sub-second live WebSocket candle tick rendering
 * - Dynamic Top-Left OHLC Tooltip HUD on crosshair hover
 * - Session VWAP, PDH/PDL, PWH/PWL, and Institutional Supply/Demand levels
 * - Custom "AlphaQuant AI Pro" watermark branding
 * - Re-center to live bar button & multi-layout splitting
 */

function validateCandle(c) {
  if (!c || typeof c !== "object") return null;
  let time = Number(c.time);
  const open = Number(c.open);
  const high = Number(c.high);
  const low = Number(c.low);
  const close = Number(c.close);
  const volumeRaw = c.volume !== undefined && c.volume !== null ? Number(c.volume) : 0;
  const volume = Number.isFinite(volumeRaw) && volumeRaw >= 0 ? volumeRaw : 0;

  if (time > 10_000_000_000) {
    time = Math.floor(time / 1000);
  } else {
    time = Math.floor(time);
  }

  if (
    !Number.isFinite(time) ||
    time <= 0 ||
    !Number.isFinite(open) ||
    !Number.isFinite(high) ||
    !Number.isFinite(low) ||
    !Number.isFinite(close) ||
    open <= 0 ||
    high <= 0 ||
    low <= 0 ||
    close <= 0 ||
    high < low
  ) {
    return null;
  }

  const saneHigh = Math.max(high, open, close);
  const saneLow = Math.min(low, open, close);
  if (saneHigh < saneLow) return null;

  return {
    time,
    open,
    high: saneHigh,
    low: saneLow,
    close,
    volume,
  };
}

function sanitizeCandles(candles) {
  if (!Array.isArray(candles) || candles.length === 0) return [];
  const map = new Map();
  for (let i = 0; i < candles.length; i++) {
    const clean = validateCandle(candles[i]);
    if (clean) map.set(clean.time, clean);
  }
  return Array.from(map.values()).sort((a, b) => a.time - b.time);
}

class ChartInstance {
  constructor(containerId, index, defaultTf = "15m") {
    this.containerId = containerId;
    this.index = index;
    this.timeframe = defaultTf;

    this.chart = null;
    this.candleSeries = null;
    this.volumeSeries = null;
    this.vwapSeries = null;
    this.markersPlugin = null;
    this.resizeObserver = null;
    this._resizeRaf = null;
    this._lastWidth = 0;
    this._lastHeight = 0;

    // Overlay Series Map
    this.overlaySeries = {};

    // Price lines
    this.pdhLine = null;
    this.pdlLine = null;
    this.pwhLine = null;
    this.pwlLine = null;
    this.zonePriceLines = [];

    // State & Pagination
    this.allCandles = [];
    this.isLoadingOlder = false;
    this.hasMoreHistorical = true;
    this.isReplayActive = false;
    this._paginationReady = false;
    this._hasInitialViewport = false;
    this._awaitingHistory = false;
    this._pendingLiveCandle = null;
    this._pendingLiveMeta = null;
    this._contextEpoch = 0;
    this.activePair = null;
    this.activeInterval = defaultTf;
    this._anomalyWarnedAt = 0;
    this.showAiOverlays = true;
    this.showManualDrawings = true;
    this.lastSmcData = null;

    this.initChart();
  }

  destroy() {
    if (this._resizeRaf) {
      cancelAnimationFrame(this._resizeRaf);
      this._resizeRaf = null;
    }
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
      this.resizeObserver = null;
    }
    this.clearOverlaysAndLevels();
    if (this.markersPlugin && typeof this.markersPlugin.detach === "function") {
      try {
        this.markersPlugin.detach();
      } catch (e) {}
    }
    this.markersPlugin = null;
    if (this.chart) {
      try {
        this.chart.remove();
      } catch (e) {}
      this.chart = null;
    }
    this.candleSeries = null;
    this.volumeSeries = null;
    this.vwapSeries = null;
  }

  initChart() {
    const container = document.getElementById(this.containerId);
    if (!container) return;
    if (this.chart || this.resizeObserver) {
      this.destroy();
    }
    container.innerHTML = "";

    const w = container.clientWidth || (container.parentElement ? container.parentElement.clientWidth : 800);
    const h = container.clientHeight || (container.parentElement ? container.parentElement.clientHeight : 500);
    this._lastWidth = Math.max(100, w);
    this._lastHeight = Math.max(100, h);

    // TradingView v5 Official Chart Options with AlphaQuant AI Pro watermark
    this.chart = LightweightCharts.createChart(container, {
      width: Math.max(100, w),
      height: Math.max(100, h),
      layout: {
        background: { color: "transparent" },
        textColor: "#8892a4",
        fontSize: 11,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      },
      watermark: {
        visible: true,
        fontSize: 16,
        horzAlign: "left",
        vertAlign: "bottom",
        color: "rgba(255, 255, 255, 0.07)",
        text: "AlphaQuant AI Pro",
      },
      grid: {
        vertLines: { color: "rgba(30, 42, 58, 0.45)" },
        horzLines: { color: "rgba(30, 42, 58, 0.45)" },
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: "#8892a4", width: 1, style: 2 },
        horzLine: { color: "#8892a4", width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: "#1e2a3a",
        autoScale: true,
        minimumWidth: 68,
        scaleMargins: { top: 0.1, bottom: 0.18 },
      },
      timeScale: {
        borderColor: "#1e2a3a",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
    });

    // 1. Candlestick Series (#10b981 / #ef4444)
    this.candleSeries = this.chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    // 2. Volume Histogram Series (overlay price scale locked to bottom 18%, isolated scale)
    this.volumeSeries = this.chart.addSeries(LightweightCharts.HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    try {
      this.chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
    } catch (e) {}

    // 3. Session VWAP (isolated autoscale)
    this.vwapSeries = this.chart.addSeries(LightweightCharts.LineSeries, {
      color: "#06b6d4",
      lineWidth: 1.5,
      title: "",
      priceLineVisible: false,
      lastValueVisible: false,
      autoscaleInfoProvider: () => null,
    });
    this.lastLevels = null;

    // 4. Single reusable markers plugin handle (v5 createSeriesMarkers appends plugins per call)
    this.markersPlugin =
      LightweightCharts && typeof LightweightCharts.createSeriesMarkers === "function"
        ? LightweightCharts.createSeriesMarkers(this.candleSeries, [])
        : null;

    // Container ResizeObserver (rAF-throttled and guarded against duplicate/zero sizes)
    if (typeof ResizeObserver !== "undefined") {
      this.resizeObserver = new ResizeObserver((entries) => {
        const entry = entries && entries[0];
        if (!entry || !this.chart) return;
        const rect = entry.contentRect;
        const nextW = Math.round(rect ? rect.width : container.clientWidth);
        const nextH = Math.round(rect ? rect.height : container.clientHeight);
        if (nextW < 50 || nextH < 50) return;
        if (nextW === this._lastWidth && nextH === this._lastHeight) return;
        if (this._resizeRaf) cancelAnimationFrame(this._resizeRaf);
        this._resizeRaf = requestAnimationFrame(() => {
          this._resizeRaf = null;
          this.resize();
        });
      });
      this.resizeObserver.observe(container);
    }

    // Setup Historical Scroll Pagination & Subchart Range Sync
    this.setupHistoricalPaginationHook();

    // Setup Crosshair HUD
    this.setupCrosshairHUD();
  }

  clearOverlaysAndLevels() {
    if (this.candleSeries) {
      if (this.pdhLine) { try { this.candleSeries.removePriceLine(this.pdhLine); } catch (e) {} this.pdhLine = null; }
      if (this.pdlLine) { try { this.candleSeries.removePriceLine(this.pdlLine); } catch (e) {} this.pdlLine = null; }
      if (this.pwhLine) { try { this.candleSeries.removePriceLine(this.pwhLine); } catch (e) {} this.pwhLine = null; }
      if (this.pwlLine) { try { this.candleSeries.removePriceLine(this.pwlLine); } catch (e) {} this.pwlLine = null; }
      if (Array.isArray(this.zonePriceLines) && this.zonePriceLines.length > 0) {
        this.zonePriceLines.forEach((l) => {
          try { this.candleSeries.removePriceLine(l); } catch (e) {}
        });
      }
    }
    this.zonePriceLines = [];
    this.lastLevels = null;
    this.lastZonesData = null;

    const autoZonesG = document.getElementById("chart-auto-zones-group");
    if (autoZonesG) autoZonesG.innerHTML = "";
    const sessionsSvg = document.getElementById("chart-sessions-overlay");
    if (sessionsSvg) sessionsSvg.innerHTML = "";

    if (this.vwapSeries) {
      try { this.vwapSeries.setData([]); } catch (e) {}
    }

    if (this.markersPlugin && typeof this.markersPlugin.setMarkers === "function") {
      try { this.markersPlugin.setMarkers([]); } catch (e) {}
    }

    Object.keys(this.overlaySeries || {}).forEach((k) => {
      try { this.overlaySeries[k].setData([]); } catch (e) {}
    });
  }

  prepareContext(pair, interval) {
    const normalizedPair = pair || this.activePair || "BTC/USDT";
    const normalizedTf = interval || this.timeframe || "15m";
    const changed = this.activePair !== normalizedPair || this.activeInterval !== normalizedTf;
    this.timeframe = normalizedTf;
    if (changed) {
      this._contextEpoch += 1;
      this.activePair = normalizedPair;
      this.activeInterval = normalizedTf;
      this.isLoadingOlder = false;
      this.hasMoreHistorical = true;
      this._paginationReady = false;
      this._hasInitialViewport = false;
      this._awaitingHistory = true;
      this._pendingLiveCandle = null;
      this._pendingLiveMeta = null;
      this.allCandles = [];
      this.clearOverlaysAndLevels();
      if (this.candleSeries) {
        try { this.candleSeries.setData([]); } catch (e) {}
      }
      if (this.volumeSeries) {
        try { this.volumeSeries.setData([]); } catch (e) {}
      }
      if (this.chart) {
        try {
          this.chart.priceScale("right").applyOptions({ autoScale: true });
        } catch (e) {}
      }
    }
    return this._contextEpoch;
  }

  isPlausiblePrice(price) {
    const val = Number(price);
    if (!Number.isFinite(val) || val <= 0) return false;
    let ref = null;
    if (this.allCandles && this.allCandles.length > 0) {
      ref = this.allCandles[this.allCandles.length - 1].close;
    } else if (window.appState && Number.isFinite(Number(window.appState.currentPrice)) && Number(window.appState.currentPrice) > 0) {
      ref = Number(window.appState.currentPrice);
    }
    if (ref !== null && Number.isFinite(ref) && ref > 0) {
      if (val > ref * 1.8 || val < ref * 0.55) return false;
    }
    return true;
  }

  checkRenderAnomaly(reason = "update", extraInfo = {}) {
    if (
      !this.chart ||
      !this.candleSeries ||
      this._awaitingHistory ||
      !this.allCandles ||
      this.allCandles.length < 10
    ) {
      return;
    }
    const now = Date.now();
    if (now - this._anomalyWarnedAt < 3000) return;

    const container = document.getElementById(this.containerId);
    const w = container ? container.clientWidth : this._lastWidth;
    const h = container ? container.clientHeight : this._lastHeight;
    if (!h || h <= 0) return;

    const topP = this.candleSeries.coordinateToPrice(2);
    const botP = this.candleSeries.coordinateToPrice(h - 2);
    if (!Number.isFinite(topP) || !Number.isFinite(botP) || topP <= botP) return;

    let visCandles = this.allCandles;
    let visibleTimeRange = null;
    try {
      const tr = this.chart.timeScale().getVisibleRange();
      if (tr) visibleTimeRange = { from: tr.from, to: tr.to };
      const lr = this.chart.timeScale().getVisibleLogicalRange();
      if (lr && Number.isFinite(lr.from) && Number.isFinite(lr.to)) {
        const start = Math.max(0, Math.floor(lr.from));
        const end = Math.min(this.allCandles.length - 1, Math.ceil(lr.to));
        if (end >= start) visCandles = this.allCandles.slice(start, end + 1);
      }
    } catch (e) {}

    let visHigh = -Infinity;
    let visLow = Infinity;
    for (let i = 0; i < visCandles.length; i++) {
      if (visCandles[i].high > visHigh) visHigh = visCandles[i].high;
      if (visCandles[i].low < visLow) visLow = visCandles[i].low;
    }
    if (!Number.isFinite(visHigh) || !Number.isFinite(visLow) || visHigh <= visLow) return;

    const scaleSpan = topP - botP;
    const candleSpan = visHigh - visLow;
    if (botP <= 0 || (visCandles.length >= 5 && scaleSpan > candleSpan * 8)) {
      this._anomalyWarnedAt = now;
      const latestCandle = this.allCandles[this.allCandles.length - 1];
      const activeOverlaysCount = Object.keys(this.overlaySeries || {}).length +
        (window.drawingTools && window.drawingTools.drawings ? window.drawingTools.drawings.length : 0);
      const activeSeriesCount = 2 + (this.vwapSeries ? 1 : 0) + Object.keys(this.overlaySeries || {}).length;

      console.warn(
        "[AlphaQuant ChartAnomaly] " +
          JSON.stringify({
            symbol: this.activePair,
            timeframe: this.timeframe,
            candleCount: this.allCandles.length,
            firstCandleTimestamp: this.allCandles[0] ? this.allCandles[0].time : null,
            lastCandleTimestamp: latestCandle ? latestCandle.time : null,
            latestCandleOHLC: latestCandle ? { open: latestCandle.open, high: latestCandle.high, low: latestCandle.low, close: latestCandle.close } : null,
            visibleTimeRange: visibleTimeRange,
            visiblePriceRange: { top: Number(topP.toPrecision(6)), bottom: Number(botP.toPrecision(6)) },
            containerWidth: w,
            containerHeight: h,
            activeSeriesCount: activeSeriesCount,
            activeOverlaysCount: activeOverlaysCount,
            fitContentOrSetVisibleRangeCalled: extraInfo.fitContentOrSetVisibleRange || false,
            wsUpdateTimestamp: (window.appState && window.appState.lastTickTimestamp) || null,
            candleTimestamp: latestCandle ? latestCandle.time : null,
            reason: reason,
            candleSpan: Number(candleSpan.toPrecision(5)),
            scaleSpan: Number(scaleSpan.toPrecision(5)),
          })
      );
    }
  }

  setupCrosshairHUD() {
    this.chart.subscribeCrosshairMove((param) => {
      const oEl = document.getElementById("hud-o");
      const hEl = document.getElementById("hud-h");
      const lEl = document.getElementById("hud-l");
      const cEl = document.getElementById("hud-c");
      const vEl = document.getElementById("hud-vol");
      const badge = document.getElementById("hud-history-badge");

      if (!param || !param.time || !param.seriesData || !param.point) {
        // Mouse left chart viewport: restore HUD immediately to latest live bar
        if (this.allCandles && this.allCandles.length > 0) {
          const lastC = this.allCandles[this.allCandles.length - 1];
          const dec = lastC.close < 0.01 ? 6 : lastC.close < 1 ? 5 : lastC.close < 50 ? 4 : 2;
          if (oEl) oEl.textContent = lastC.open.toFixed(dec);
          if (hEl) hEl.textContent = lastC.high.toFixed(dec);
          if (lEl) lEl.textContent = lastC.low.toFixed(dec);
          if (cEl) {
            cEl.textContent = lastC.close.toFixed(dec);
            cEl.className = lastC.close >= lastC.open ? "hud-val up-color" : "hud-val down-color";
          }
          if (vEl) vEl.textContent = Number(lastC.volume || 0).toLocaleString(undefined, { maximumFractionDigits: 1 });
          if (badge) {
            badge.textContent = `Live (${this.allCandles.length} bars)`;
            badge.className = "badge badge-blue";
          }
        }
        return;
      }

      const bar = param.seriesData.get(this.candleSeries);
      if (!bar) return;

      const dec = bar.close < 0.01 ? 6 : bar.close < 1 ? 5 : bar.close < 50 ? 4 : 2;
      if (oEl && bar.open !== undefined) oEl.textContent = bar.open.toFixed(dec);
      if (hEl && bar.high !== undefined) hEl.textContent = bar.high.toFixed(dec);
      if (lEl && bar.low !== undefined) lEl.textContent = bar.low.toFixed(dec);
      if (cEl && bar.close !== undefined) {
        cEl.textContent = bar.close.toFixed(dec);
        cEl.className = bar.close >= bar.open ? "hud-val up-color" : "hud-val down-color";
      }
      if (vEl && this.volumeSeries) {
        const volBar = param.seriesData.get(this.volumeSeries);
        if (volBar && volBar.value !== undefined) {
          vEl.textContent = Number(volBar.value).toLocaleString(undefined, { maximumFractionDigits: 1 });
        }
      }

      if (badge && this.allCandles && this.allCandles.length > 0) {
        const lastBar = this.allCandles[this.allCandles.length - 1];
        if (bar.time === lastBar.time) {
          badge.textContent = "LIVE BAR";
          badge.className = "badge badge-emerald";
        } else {
          badge.textContent = "HISTORICAL";
          badge.className = "badge badge-neutral";
        }
      }
    });
  }

  setupHistoricalPaginationHook() {
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(async (newRange) => {
      if (window.indicatorsEngine && newRange) {
        window.indicatorsEngine.syncTimeScale(newRange);
      }
      if (window.drawingTools && typeof window.drawingTools.reprojectDrawings === "function") {
        window.drawingTools.reprojectDrawings();
      }
      this.renderAutoZones();
      this.renderSessionShading();
      if (
        !newRange ||
        !this._paginationReady ||
        this.isLoadingOlder ||
        !this.hasMoreHistorical ||
        this.isReplayActive
      ) {
        return;
      }
      // If user scrolls backwards within 20 bars of the left edge
      if (newRange.from < 20 && this.allCandles.length >= 50) {
        await this.fetchOlderHistoricalChunk();
      }
    });
  }

  async fetchOlderHistoricalChunk() {
    if (this.isLoadingOlder || !this.hasMoreHistorical || this.allCandles.length === 0) return;
    this.isLoadingOlder = true;

    const reqEpoch = this._contextEpoch;
    const reqPair =
      this.activePair ||
      (window.appState ? (window.appState.currentPair || window.appState.pair || "BTC/USDT") : "BTC/USDT");
    const reqTf = this.timeframe;
    const prevCount = this.allCandles.length;
    const prevRange = this.chart ? this.chart.timeScale().getVisibleLogicalRange() : null;
    const oldestTime = this.allCandles[0].time;

    const badge = document.getElementById("hud-history-badge");
    if (badge) badge.textContent = "Loading older history...";

    try {
      const res = await fetch(
        `/api/candles?pair=${encodeURIComponent(reqPair)}&interval=${reqTf}&limit=1000&endTime=${oldestTime - 1}`
      );
      if (this._contextEpoch !== reqEpoch || this.activePair !== reqPair || this.timeframe !== reqTf) {
        return;
      }
      if (res.ok) {
        const data = await res.json();
        if (this._contextEpoch !== reqEpoch || this.activePair !== reqPair || this.timeframe !== reqTf) {
          return;
        }
        const olderCandles = sanitizeCandles(data.candles || []);

        if (olderCandles.length === 0) {
          this.hasMoreHistorical = false;
          if (badge) badge.textContent = "All 1Y+ History Loaded";
        } else {
          const map = new Map();
          olderCandles.forEach((c) => map.set(c.time, c));
          this.allCandles.forEach((c) => map.set(c.time, c));
          this.allCandles = Array.from(map.values()).sort((a, b) => a.time - b.time);

          const added = this.allCandles.length - prevCount;
          if (added <= 0) {
            this.hasMoreHistorical = false;
            return;
          }

          this.candleSeries.setData(this.allCandles);
          const vols = this.allCandles.map((c) => ({
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? "rgba(16, 185, 129, 0.35)" : "rgba(239, 68, 68, 0.35)",
          }));
          this.volumeSeries.setData(vols);

          if (prevRange && Number.isFinite(prevRange.from) && Number.isFinite(prevRange.to)) {
            this.chart.timeScale().setVisibleLogicalRange({
              from: prevRange.from + added,
              to: prevRange.to + added,
            });
          }

          this.reloadActiveIndicators();
          if (this.index === 0 && window.indicatorsEngine) {
            window.indicatorsEngine.updateData(this.allCandles);
          }

          // Update cache
          if (window.candleCacheDB) {
            window.candleCacheDB.saveCandles(reqPair, reqTf, this.allCandles);
          }

          if (badge) badge.textContent = `1Y+ (${this.allCandles.length} bars)`;
        }
      }
    } catch (e) {
      console.warn("Failed fetching older chunk:", e);
    } finally {
      this.isLoadingOlder = false;
    }
  }

  setCandles(candles, options = {}) {
    const clean = sanitizeCandles(candles);
    if (!clean || clean.length === 0 || !this.candleSeries) return;

    const {
      preserveRange = false,
      pair = null,
      interval = null,
      reason = "setCandles",
    } = options || {};

    const resolvedPair =
      pair ||
      this.activePair ||
      (window.appState ? (window.appState.currentPair || window.appState.pair || "BTC/USDT") : "BTC/USDT");
    const resolvedTf = interval || this.timeframe || "15m";
    const isContextSwitch =
      (this.activePair && resolvedPair !== this.activePair) ||
      (this.activeInterval && resolvedTf !== this.activeInterval);

    if (isContextSwitch) {
      this.clearOverlaysAndLevels();
      this._hasInitialViewport = false;
      this._contextEpoch += 1;
    }

    this.activePair = resolvedPair;
    this.activeInterval = resolvedTf;
    this.timeframe = resolvedTf;
    this._awaitingHistory = false;

    if (
      this._pendingLiveCandle &&
      (!this._pendingLiveMeta ||
        ((!this._pendingLiveMeta.pair || this._pendingLiveMeta.pair === resolvedPair) &&
          (!this._pendingLiveMeta.interval || this._pendingLiveMeta.interval === resolvedTf)))
    ) {
      const pending = this._pendingLiveCandle;
      const tail = clean[clean.length - 1];
      if (tail && tail.close > 0 && pending.close <= tail.close * 3 && pending.close >= tail.close * 0.33) {
        if (pending.time === tail.time) {
          clean[clean.length - 1] = pending;
        } else if (pending.time > tail.time) {
          clean.push(pending);
        }
      }
    }
    this._pendingLiveCandle = null;
    this._pendingLiveMeta = null;

    const prevRange =
      preserveRange && !isContextSwitch && this._hasInitialViewport && this.chart
        ? this.chart.timeScale().getVisibleLogicalRange()
        : null;

    this.allCandles = clean;
    this.hasMoreHistorical = true;
    this._paginationReady = false;

    const lastC = clean[clean.length - 1];
    const dec = lastC.close < 0.01 ? 6 : lastC.close < 1 ? 5 : lastC.close < 50 ? 4 : 2;
    this.candleSeries.applyOptions({
      priceFormat: { type: "price", precision: dec, minMove: 1 / Math.pow(10, dec) },
    });

    if (isContextSwitch || !this._hasInitialViewport) {
      try {
        this.chart.priceScale("right").applyOptions({ autoScale: true });
      } catch (e) {}
    }

    this.candleSeries.setData(clean);
    const vols = clean.map((c) => ({
      time: c.time,
      value: c.volume,
      color: c.close >= c.open ? "rgba(16, 185, 129, 0.35)" : "rgba(239, 68, 68, 0.35)",
    }));
    this.volumeSeries.setData(vols);

    // Update top-left OHLC HUD to latest candle
    const oEl = document.getElementById("hud-o");
    const hEl = document.getElementById("hud-h");
    const lEl = document.getElementById("hud-l");
    const cEl = document.getElementById("hud-c");
    const vEl = document.getElementById("hud-vol");
    if (oEl) oEl.textContent = lastC.open.toFixed(dec);
    if (hEl) hEl.textContent = lastC.high.toFixed(dec);
    if (lEl) lEl.textContent = lastC.low.toFixed(dec);
    if (cEl) {
      cEl.textContent = lastC.close.toFixed(dec);
      cEl.className = lastC.close >= lastC.open ? "hud-val up-color" : "hud-val down-color";
    }
    if (vEl) vEl.textContent = Number(lastC.volume || 0).toLocaleString(undefined, { maximumFractionDigits: 1 });

    // Preserve user zoom/pan on background refreshes; otherwise initialize 140-bar viewport
    if (prevRange && Number.isFinite(prevRange.from) && Number.isFinite(prevRange.to)) {
      this.chart.timeScale().setVisibleLogicalRange(prevRange);
    } else if (!preserveRange || !this._hasInitialViewport) {
      if (clean.length > 140) {
        this.chart.timeScale().setVisibleLogicalRange({
          from: clean.length - 140,
          to: clean.length + 5,
        });
      } else {
        this.chart.timeScale().fitContent();
      }
    }

    this._hasInitialViewport = true;
    this._paginationReady = clean.length >= 50;

    // Apply active overlay indicators
    this.reloadActiveIndicators();

    // Update subcharts (RSI, MACD & extra oscillators)
    if (this.index === 0 && window.indicatorsEngine) {
      window.indicatorsEngine.updateData(clean);
    }

    if (window.drawingTools && typeof window.drawingTools.reprojectDrawings === "function") {
      setTimeout(() => window.drawingTools.reprojectDrawings(), 30);
    }
    this.renderAutoZones();
    this.renderSessionShading();

    if (typeof window.syncLivePrice === "function" && lastC && lastC.close) {
      window.syncLivePrice(lastC.close, resolvedPair);
    }

    const badge = document.getElementById("hud-history-badge");
    if (badge) badge.textContent = `History: ${clean.length} bars`;

    requestAnimationFrame(() => this.checkRenderAnomaly(reason));
  }

  updateLiveCandle(candle, meta = {}) {
    if (!candle || !this.candleSeries || this.isReplayActive) return;

    if (meta && meta.pair && this.activePair && meta.pair !== this.activePair) {
      console.warn("[AlphaQuant ChartGuard] Ignored stale cross-pair live tick:", meta.pair, "expected", this.activePair);
      return;
    }
    if (meta && meta.interval && this.activeInterval && meta.interval !== this.activeInterval) {
      return;
    }

    const clean = validateCandle(candle);
    if (!clean) {
      console.warn("[AlphaQuant ChartGuard] Rejected malformed live candle:", candle);
      return;
    }

    if (this._awaitingHistory || this.allCandles.length === 0) {
      this._pendingLiveCandle = clean;
      this._pendingLiveMeta = {
        pair: meta && meta.pair ? meta.pair : this.activePair,
        interval: meta && meta.interval ? meta.interval : this.activeInterval,
      };
      return;
    }

    if (this.allCandles.length > 0) {
      const last = this.allCandles[this.allCandles.length - 1];
      if (clean.time < last.time) {
        return;
      }
      if (last.close > 0 && (clean.close > last.close * 3 || clean.close < last.close * 0.33)) {
        console.warn("[AlphaQuant ChartGuard] Rejected outlier live candle price:", {
          pair: this.activePair,
          lastClose: last.close,
          incomingClose: clean.close,
          meta,
        });
        return;
      }
      if (last.time === clean.time) {
        this.allCandles[this.allCandles.length - 1] = clean;
      } else {
        this.allCandles.push(clean);
      }
    }

    try {
      this.candleSeries.update(clean);
      if (this.volumeSeries) {
        this.volumeSeries.update({
          time: clean.time,
          value: clean.volume,
          color: clean.close >= clean.open ? "rgba(16, 185, 129, 0.35)" : "rgba(239, 68, 68, 0.35)",
        });
      }
      if (typeof window.syncLivePrice === "function") {
        window.syncLivePrice(clean.close, meta && meta.pair ? meta.pair : this.activePair);
      }
    } catch (e) {
      console.warn("[AlphaQuant ChartGuard] Series live update skipped:", e && e.message ? e.message : e);
      return;
    }

    this.checkRenderAnomaly("live_tick");
  }

  reloadActiveIndicators() {
    if (!window.indicatorsEngine || this.allCandles.length < 10) return;

    this.renderSessionShading();

    const catalog = window.indicatorsEngine.catalog;

    // Handle Session VWAP toggle cleanly with price validation
    const vwapEnabled = window.indicatorsEngine.isEnabled("vwap");
    if (this.vwapSeries) {
      if (vwapEnabled && this.lastLevels && Array.isArray(this.lastLevels.vwap_series)) {
        const cleanVwap = this.lastLevels.vwap_series
          .filter((pt) => pt && Number.isFinite(Number(pt.time)) && this.isPlausiblePrice(pt.value))
          .map((pt) => ({ time: Number(pt.time), value: Number(pt.value) }));
        this.vwapSeries.setData(cleanVwap);
      } else if (!vwapEnabled) {
        this.vwapSeries.setData([]);
      }
    }

    catalog.forEach((item) => {
      if (item.type !== "overlay" || item.id === "vwap") return;

      if (item.id === "bollinger") {
        const keys = ["bollinger_upper", "bollinger_middle", "bollinger_lower"];
        if (item.enabled) {
          if (!this.overlaySeries["bollinger_upper"]) {
            this.overlaySeries["bollinger_upper"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "rgba(192, 132, 252, 0.7)", lineWidth: 1, lineStyle: 2, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
            this.overlaySeries["bollinger_middle"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "rgba(192, 132, 252, 0.9)", lineWidth: 1, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
            this.overlaySeries["bollinger_lower"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "rgba(192, 132, 252, 0.7)", lineWidth: 1, lineStyle: 2, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
          }
          const bb = window.indicatorsEngine.calculateBollinger(this.allCandles, item.period || 20, item.stdDev || 2.0);
          if (bb.upper.length > 0) {
            this.overlaySeries["bollinger_upper"].setData(bb.upper);
            this.overlaySeries["bollinger_middle"].setData(bb.middle);
            this.overlaySeries["bollinger_lower"].setData(bb.lower);
          }
        } else {
          keys.forEach((k) => {
            if (this.overlaySeries[k]) {
              this.chart.removeSeries(this.overlaySeries[k]);
              delete this.overlaySeries[k];
            }
          });
        }
      } else if (item.id === "ichimoku") {
        const keys = ["ichimoku_tenkan", "ichimoku_kijun", "ichimoku_senkouA", "ichimoku_senkouB"];
        if (item.enabled) {
          if (!this.overlaySeries["ichimoku_tenkan"]) {
            this.overlaySeries["ichimoku_tenkan"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "#38bdf8", lineWidth: 1, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
            this.overlaySeries["ichimoku_kijun"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "#f43f5e", lineWidth: 1, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
            this.overlaySeries["ichimoku_senkouA"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "rgba(16, 185, 129, 0.55)", lineWidth: 1, lineStyle: 2, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
            this.overlaySeries["ichimoku_senkouB"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "rgba(239, 68, 68, 0.55)", lineWidth: 1, lineStyle: 2, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
          }
          const ich = window.indicatorsEngine.calculateIchimoku(this.allCandles);
          if (ich.tenkan.length > 0) {
            this.overlaySeries["ichimoku_tenkan"].setData(ich.tenkan);
            this.overlaySeries["ichimoku_kijun"].setData(ich.kijun);
            this.overlaySeries["ichimoku_senkouA"].setData(ich.senkouA);
            this.overlaySeries["ichimoku_senkouB"].setData(ich.senkouB);
          }
        } else {
          keys.forEach((k) => {
            if (this.overlaySeries[k]) {
              this.chart.removeSeries(this.overlaySeries[k]);
              delete this.overlaySeries[k];
            }
          });
        }
      } else if (item.id === "volume_sma") {
        if (item.enabled) {
          if (!this.overlaySeries["volume_sma"]) {
            this.overlaySeries["volume_sma"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: item.color || "#64748b",
              lineWidth: 1.5,
              title: "",
              priceScaleId: "volume",
              priceLineVisible: false,
              lastValueVisible: false,
              autoscaleInfoProvider: () => null,
            });
          }
          const vsma = window.indicatorsEngine.calculateVolumeSMA(this.allCandles, item.period || 20);
          if (vsma.length > 0) this.overlaySeries["volume_sma"].setData(vsma);
        } else if (this.overlaySeries["volume_sma"]) {
          this.chart.removeSeries(this.overlaySeries["volume_sma"]);
          delete this.overlaySeries["volume_sma"];
        }
      } else if (item.id === "supertrend") {
        if (item.enabled) {
          if (!this.overlaySeries["supertrend"]) {
            this.overlaySeries["supertrend"] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: "#10b981", lineWidth: 2, title: "", priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null,
            });
          }
          const st = window.indicatorsEngine.calculateSupertrend(this.allCandles, item.period || 10, item.multiplier || 3.0);
          if (st.length > 0) this.overlaySeries["supertrend"].setData(st);
        } else if (this.overlaySeries["supertrend"]) {
          this.chart.removeSeries(this.overlaySeries["supertrend"]);
          delete this.overlaySeries["supertrend"];
        }
      } else {
        if (item.enabled) {
          if (!this.overlaySeries[item.id]) {
            this.overlaySeries[item.id] = this.chart.addSeries(LightweightCharts.LineSeries, {
              color: item.color || "#3b82f6",
              lineWidth: 1.5,
              title: "",
              priceLineVisible: false,
              lastValueVisible: false,
              autoscaleInfoProvider: () => null,
            });
          }
          let data = [];
          if (item.id.startsWith("ema")) {
            data = window.indicatorsEngine.calculateEMA(this.allCandles, item.period);
          } else if (item.id.startsWith("sma")) {
            data = window.indicatorsEngine.calculateSMA(this.allCandles, item.period);
          } else if (item.id === "anchored_vwap") {
            data = window.indicatorsEngine.calculateAnchoredVWAP(this.allCandles);
          }
          if (data.length > 0) this.overlaySeries[item.id].setData(data);
        } else if (this.overlaySeries[item.id]) {
          this.chart.removeSeries(this.overlaySeries[item.id]);
          delete this.overlaySeries[item.id];
        }
      }
    });
  }

  scrollToLive() {
    if (this.chart) {
      this.chart.timeScale().scrollToRealTime();
    }
  }

  setMarketLevels(levels, expectedPair = null, expectedInterval = null) {
    if (!levels || !this.candleSeries || this._awaitingHistory || !this.allCandles || this.allCandles.length < 5) return;
    if (expectedPair && this.activePair && expectedPair !== this.activePair) return;
    if (expectedInterval && this.activeInterval && expectedInterval !== this.activeInterval) return;

    this.lastLevels = levels;

    if (this.pdhLine) { try { this.candleSeries.removePriceLine(this.pdhLine); } catch (e) {} this.pdhLine = null; }
    if (this.pdlLine) { try { this.candleSeries.removePriceLine(this.pdlLine); } catch (e) {} this.pdlLine = null; }
    if (this.pwhLine) { try { this.candleSeries.removePriceLine(this.pwhLine); } catch (e) {} this.pwhLine = null; }
    if (this.pwlLine) { try { this.candleSeries.removePriceLine(this.pwlLine); } catch (e) {} this.pwlLine = null; }

    if (this.isPlausiblePrice(levels.pdh)) {
      this.pdhLine = this.candleSeries.createPriceLine({
        price: Number(levels.pdh), color: "rgba(245, 158, 11, 0.7)", lineWidth: 1, lineStyle: 2, title: "PDH", axisLabelVisible: true,
      });
    }
    if (this.isPlausiblePrice(levels.pdl)) {
      this.pdlLine = this.candleSeries.createPriceLine({
        price: Number(levels.pdl), color: "rgba(245, 158, 11, 0.7)", lineWidth: 1, lineStyle: 2, title: "PDL", axisLabelVisible: true,
      });
    }
    if (this.isPlausiblePrice(levels.pwh)) {
      this.pwhLine = this.candleSeries.createPriceLine({
        price: Number(levels.pwh), color: "rgba(139, 92, 246, 0.5)", lineWidth: 1, lineStyle: 3, title: "PWH", axisLabelVisible: false,
      });
    }
    if (this.isPlausiblePrice(levels.pwl)) {
      this.pwlLine = this.candleSeries.createPriceLine({
        price: Number(levels.pwl), color: "rgba(139, 92, 246, 0.5)", lineWidth: 1, lineStyle: 3, title: "PWL", axisLabelVisible: false,
      });
    }

    if (Array.isArray(levels.vwap_series) && this.vwapSeries) {
      const showVwap = window.indicatorsEngine ? window.indicatorsEngine.isEnabled("vwap") : true;
      if (showVwap) {
        const vwapMap = new Map();
        for (let i = 0; i < levels.vwap_series.length; i++) {
          const pt = levels.vwap_series[i];
          const t = pt ? Number(pt.time) : NaN;
          if (Number.isFinite(t) && t > 0 && this.isPlausiblePrice(pt.value)) {
            vwapMap.set(t, { time: t, value: Number(pt.value) });
          }
        }
        const cleanVwap = Array.from(vwapMap.values()).sort((a, b) => a.time - b.time);
        this.vwapSeries.setData(cleanVwap);
      } else {
        this.vwapSeries.setData([]);
      }
    }
    this.checkRenderAnomaly("market_levels");
  }

  setZones(sdData, expectedPair = null, expectedInterval = null) {
    if (this._awaitingHistory || !this.allCandles || this.allCandles.length < 5) return;
    if (expectedPair && this.activePair && expectedPair !== this.activePair) return;
    if (expectedInterval && this.activeInterval && expectedInterval !== this.activeInterval) return;

    this.zonePriceLines.forEach((l) => {
      try { this.candleSeries.removePriceLine(l); } catch (e) {}
    });
    this.zonePriceLines = [];

    this.lastZonesData = sdData;
    this.renderAutoZones();
    this.checkRenderAnomaly("zones");
  }

  renderAutoZones() {
    const svg = document.getElementById("drawing-canvas-overlay");
    if (!svg || !this.chart || !this.candleSeries || !this.lastZonesData) return;

    let group = document.getElementById("chart-auto-zones-group");
    if (!group) {
      group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.id = "chart-auto-zones-group";
      svg.insertBefore(group, svg.firstChild);
    }
    group.innerHTML = "";

    if (this.showAiOverlays === false) {
      return;
    }

    const sd = this.lastZonesData;
    const refCandle = this.allCandles.length > 0 ? this.allCandles[this.allCandles.length - 1] : null;
    if (!refCandle) return;
    const dec = refCandle.close < 0.01 ? 6 : refCandle.close < 1 ? 5 : refCandle.close < 50 ? 4 : 2;
    const container = document.getElementById(this.containerId);
    const svgW = (container && container.clientWidth) || 1200;
    const svgH = (container && container.clientHeight) || 500;
    const xRight = Math.max(50, svgW - 70);
    const startIdx = Math.max(0, this.allCandles.length - 45);
    let xLeft = this.chart.timeScale().logicalToCoordinate(startIdx);
    if (xLeft === null || !Number.isFinite(xLeft) || xLeft < 0) xLeft = 0;
    const zoneW = Math.max(30, xRight - xLeft);

    const drawBox = (topVal, botVal, isDemand, labelText) => {
      if (!this.isPlausiblePrice(topVal) || !this.isPlausiblePrice(botVal)) return;
      const topP = Math.max(topVal, botVal);
      const botP = Math.min(topVal, botVal);
      const y1 = this.candleSeries.priceToCoordinate(topP);
      const y2 = this.candleSeries.priceToCoordinate(botP);
      if (y1 === null || y2 === null || !Number.isFinite(y1) || !Number.isFinite(y2)) return;

      const topY = Math.min(y1, y2);
      const height = Math.max(3, Math.abs(y2 - y1));
      if (topY + height < -20 || topY > svgH + 20) return;

      const fill = isDemand ? "rgba(16, 185, 129, 0.08)" : "rgba(239, 68, 68, 0.08)";
      const stroke = isDemand ? "rgba(16, 185, 129, 0.5)" : "rgba(239, 68, 68, 0.5)";

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", xLeft);
      rect.setAttribute("y", topY);
      rect.setAttribute("width", zoneW);
      rect.setAttribute("height", height);
      rect.setAttribute("fill", fill);
      rect.setAttribute("stroke", stroke);
      rect.setAttribute("stroke-width", "1");
      rect.setAttribute("stroke-dasharray", "3,3");
      group.appendChild(rect);

      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", xLeft + 6);
      text.setAttribute("y", Math.min(topY + 11, topY + height - 2));
      text.setAttribute("fill", stroke);
      text.setAttribute("font-size", "9px");
      text.setAttribute("font-family", "monospace");
      text.setAttribute("font-weight", "600");
      text.textContent = `${labelText} (${botP.toFixed(dec)} - ${topP.toFixed(dec)})`;
      group.appendChild(text);
    };

    if (Array.isArray(sd.demand)) {
      sd.demand.slice(0, 2).forEach((z, i) => {
        drawBox(Number(z.proximal), Number(z.distal), true, `DEMAND #${i + 1}`);
      });
    }
    if (Array.isArray(sd.supply)) {
      sd.supply.slice(0, 2).forEach((z, i) => {
        drawBox(Number(z.distal), Number(z.proximal), false, `SUPPLY #${i + 1}`);
      });
    }
  }

  renderSessionShading() {
    const svg = document.getElementById("chart-sessions-overlay");
    if (!svg || !this.chart || !this.allCandles || this.allCandles.length < 5) {
      if (svg) svg.innerHTML = "";
      return;
    }

    const isEnabled = window.indicatorsEngine && window.indicatorsEngine.isEnabled("session_shading");
    if (!isEnabled) {
      svg.innerHTML = "";
      return;
    }

    svg.innerHTML = "";
    const container = document.getElementById(this.containerId);
    const svgH = container ? container.clientHeight : 500;
    const svgW = container ? container.clientWidth : 1200;
    const maxW = Math.max(50, svgW - 70);

    const firstTime = this.allCandles[0].time;
    const lastTime = this.allCandles[this.allCandles.length - 1].time;
    const startDay = Math.floor(firstTime / 86400) * 86400;
    const endDay = Math.floor(lastTime / 86400) * 86400;

    const timeScale = this.chart.timeScale();

    const drawSession = (startSec, endSec, fill, stroke, label) => {
      if (endSec < firstTime || startSec > lastTime + 86400) return;

      let x1 = timeScale.timeToCoordinate(startSec);
      let x2 = timeScale.timeToCoordinate(endSec);

      if (x1 === null && this.allCandles.length >= 2) {
        const lastIdx = this.allCandles.length - 1;
        const step = Math.max(60, this.allCandles[lastIdx].time - this.allCandles[lastIdx - 1].time);
        const log = lastIdx + (startSec - this.allCandles[lastIdx].time) / step;
        x1 = timeScale.logicalToCoordinate(log);
      }
      if (x2 === null && this.allCandles.length >= 2) {
        const lastIdx = this.allCandles.length - 1;
        const step = Math.max(60, this.allCandles[lastIdx].time - this.allCandles[lastIdx - 1].time);
        const log = lastIdx + (endSec - this.allCandles[lastIdx].time) / step;
        x2 = timeScale.logicalToCoordinate(log);
      }

      if (x1 === null || x2 === null || !Number.isFinite(x1) || !Number.isFinite(x2)) return;

      const left = Math.min(x1, x2);
      const right = Math.max(x1, x2);
      if (right <= 0 || left >= maxW) return;

      const cl = Math.max(0, left);
      const cr = Math.min(maxW, right);
      const cw = cr - cl;
      if (cw < 4) return;

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", cl);
      rect.setAttribute("y", 0);
      rect.setAttribute("width", cw);
      rect.setAttribute("height", svgH);
      rect.setAttribute("fill", fill);
      rect.setAttribute("stroke", stroke);
      rect.setAttribute("stroke-width", "0.75");
      rect.setAttribute("stroke-dasharray", "2,2");
      svg.appendChild(rect);

      if (cw >= 36) {
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", cl + 4);
        text.setAttribute("y", 18);
        text.setAttribute("fill", stroke);
        text.setAttribute("font-size", "9px");
        text.setAttribute("font-family", "monospace");
        text.setAttribute("font-weight", "600");
        text.setAttribute("opacity", "0.75");
        text.textContent = label;
        svg.appendChild(text);
      }
    };

    for (let day = startDay; day <= endDay + 86400; day += 86400) {
      // Asian Session (00:00 - 08:00 UTC)
      drawSession(day + 0, day + 8 * 3600, "rgba(56, 189, 248, 0.035)", "rgba(56, 189, 248, 0.2)", "ASIAN");
      // London Session (07:00 - 15:30 UTC)
      drawSession(day + 7 * 3600, day + 15.5 * 3600, "rgba(99, 102, 241, 0.035)", "rgba(99, 102, 241, 0.2)", "LONDON");
      // New York Session (12:30 - 21:00 UTC)
      drawSession(day + 12.5 * 3600, day + 21 * 3600, "rgba(16, 185, 129, 0.035)", "rgba(16, 185, 129, 0.2)", "NY");
    }
  }

  setSMCMarkers(smcData, expectedPair = null, expectedInterval = null) {
    if (expectedPair && this.activePair && expectedPair !== this.activePair) return;
    if (expectedInterval && this.activeInterval && expectedInterval !== this.activeInterval) return;
    if (!smcData || !this.candleSeries) return;
    this.lastSmcData = smcData;

    if (this.showAiOverlays === false) {
      try {
        if (this.markersPlugin && typeof this.markersPlugin.setMarkers === "function") {
          this.markersPlugin.setMarkers([]);
        } else if (this.candleSeries.setMarkers) {
          this.candleSeries.setMarkers([]);
        }
      } catch (e) {}
      return;
    }

    const rawStruct = smcData.bos_choch || smcData.market_structure || [];
    const minTime = this.allCandles.length > 0 ? this.allCandles[0].time : 0;
    const maxTime = this.allCandles.length > 0 ? this.allCandles[this.allCandles.length - 1].time : Infinity;

    // Filter valid, deduplicate by time, keep latest 8 events to prevent clutter
    const timeMap = new Map();
    rawStruct.forEach((st) => {
      const t = Number(st.time);
      if (!Number.isFinite(t) || t < minTime || t > maxTime) return;
      timeMap.set(t, st);
    });

    const deduped = Array.from(timeMap.values()).sort((a, b) => Number(a.time) - Number(b.time));
    const recent = deduped.slice(-8);

    const markers = recent.map((st) => {
      const isBull = st.direction === "BULLISH";
      const type = (st.event || st.type || "BOS").toUpperCase();
      const shortText = type.includes("CHOCH") ? "CHoCH" : "BOS";
      return {
        time: Number(st.time),
        position: isBull ? "belowBar" : "aboveBar",
        color: isBull ? "#10b981" : "#ef4444",
        shape: isBull ? "arrowUp" : "arrowDown",
        text: shortText,
      };
    });

    markers.sort((a, b) => a.time - b.time);
    try {
      if (this.markersPlugin && typeof this.markersPlugin.setMarkers === "function") {
        this.markersPlugin.setMarkers(markers);
      } else if (LightweightCharts.createSeriesMarkers) {
        this.markersPlugin = LightweightCharts.createSeriesMarkers(this.candleSeries, markers);
      } else if (this.candleSeries.setMarkers) {
        this.candleSeries.setMarkers(markers);
      }
    } catch (e) {}
  }

  toggleAiOverlays(enable = null) {
    this.showAiOverlays = enable !== null ? enable : !this.showAiOverlays;
    this.renderAutoZones();
    if (this.lastSmcData) {
      this.setSMCMarkers(this.lastSmcData);
    }
    this.renderSessionShading();
    return this.showAiOverlays;
  }

  toggleManualDrawings(enable = null) {
    this.showManualDrawings = enable !== null ? enable : !this.showManualDrawings;
    const svg = document.getElementById("drawing-canvas-overlay");
    if (svg) {
      const drawingsGroup = svg.querySelectorAll("g:not(#chart-auto-zones-group)");
      drawingsGroup.forEach((el) => {
        el.style.display = this.showManualDrawings ? "" : "none";
      });
    }
    return this.showManualDrawings;
  }

  resize() {
    const container = document.getElementById(this.containerId);
    if (this.chart && container) {
      const w = container.clientWidth || (container.parentElement ? container.parentElement.clientWidth : 800);
      const h = container.clientHeight || (container.parentElement ? container.parentElement.clientHeight : 500);
      if (w >= 50 && h >= 50 && (w !== this._lastWidth || h !== this._lastHeight)) {
        this._lastWidth = w;
        this._lastHeight = h;
        this.chart.applyOptions({
          width: w,
          height: h,
        });
        this.renderAutoZones();
        this.renderSessionShading();
      }
    }
  }
}

class WorkspaceChartManager {
  constructor() {
    this.layout = "layout-1";
    this.charts = [];
    this._loadSeq = 0;
    this._abortController = null;
    this._boundResize = () => this.resize();

    this.initWorkspace();
    this.setupResizeListeners();
  }

  initWorkspace() {
    this.charts.forEach((c) => {
      if (c && typeof c.destroy === "function") c.destroy();
    });
    this.charts = [];
    const containerViews = document.getElementById("tv-chart-views");
    if (containerViews) containerViews.className = "tv-main-chart-box layout-1";
    const tf = window.appState ? (window.appState.currentInterval || window.appState.interval || "15m") : "15m";
    this.charts.push(new ChartInstance("chart-viewport-0", 0, tf));
  }

  getCurrentChart() {
    return this.charts && this.charts.length > 0 ? this.charts[0] : null;
  }

  setLayout(layoutId) {
    this.layout = layoutId;
    const containerViews = document.getElementById("tv-chart-views");
    if (containerViews) {
      containerViews.className = `tv-main-chart-box ${layoutId}`;
    }

    const tfs = ["15m", "1h", "4h", "1d"];
    const neededCount = layoutId === "layout-4" ? 4 : layoutId === "layout-2" ? 2 : 1;

    // Show or hide viewports based on layout
    for (let i = 0; i < 4; i++) {
      const el = document.getElementById(`chart-viewport-${i}`);
      if (el) {
        if (i < neededCount) el.classList.remove("d-none");
        else el.classList.add("d-none");
      }
    }

    while (this.charts.length < neededCount) {
      const idx = this.charts.length;
      const vId = `chart-viewport-${idx}`;
      this.charts.push(new ChartInstance(vId, idx, tfs[idx] || "1h"));
    }

    setTimeout(() => {
      this.resize();
      this.reloadAllCharts();
    }, 100);
  }

  setupResizeListeners() {
    window.removeEventListener("resize", this._boundResize);
    window.addEventListener("resize", this._boundResize);
  }

  resize() {
    this.charts.forEach((c) => {
      if (c) c.resize();
    });
    if (window.indicatorsEngine) {
      window.indicatorsEngine.resize();
    }
  }

  scrollToLive() {
    if (this.charts.length > 0 && this.charts[0]) {
      this.charts[0].scrollToLive();
    }
  }

  reloadActiveIndicators() {
    this.charts.forEach((c) => {
      if (c) c.reloadActiveIndicators();
    });
  }

  async reloadAllCharts() {
    const loadSeq = ++this._loadSeq;
    if (this._abortController) {
      try { this._abortController.abort(); } catch (e) {}
    }
    this._abortController = typeof AbortController !== "undefined" ? new AbortController() : null;
    const signal = this._abortController ? this._abortController.signal : undefined;

    const pair = window.appState ? (window.appState.currentPair || window.appState.pair || "BTC/USDT") : "BTC/USDT";
    const interval = window.appState ? (window.appState.currentInterval || window.appState.interval || "15m") : "15m";

    if (this.charts.length > 0 && this.charts[0]) {
      this.charts[0].timeframe = interval;
    }

    const isStale = () => loadSeq !== this._loadSeq || (signal && signal.aborted);

    for (const c of this.charts) {
      if (!c || isStale()) return;
      const chartTf = c.timeframe || interval;
      c.prepareContext(pair, chartTf);

      try {
        let cached = null;
        if (window.candleCacheDB) {
          cached = await window.candleCacheDB.getCandles(pair, chartTf);
          if (isStale()) return;
        }

        if (cached && cached.length >= 100) {
          c.setCandles(cached, { preserveRange: false, pair, interval: chartTf, reason: "cache_hit" });
          fetch(`/api/candles?pair=${encodeURIComponent(pair)}&interval=${chartTf}&limit=100`, { signal })
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
              if (!d || !Array.isArray(d.candles) || isStale() || !window.candleCacheDB) return;
              return window.candleCacheDB.saveCandles(pair, chartTf, d.candles).then((merged) => {
                if (isStale() || !merged || merged.length === 0) return;
                const prevLast = c.allCandles.length > 0 ? c.allCandles[c.allCandles.length - 1] : null;
                const nextLast = merged[merged.length - 1];
                const changed =
                  !prevLast ||
                  merged.length !== c.allCandles.length ||
                  nextLast.time !== prevLast.time ||
                  nextLast.close !== prevLast.close ||
                  nextLast.high !== prevLast.high ||
                  nextLast.low !== prevLast.low;
                if (changed) {
                  c.setCandles(merged, { preserveRange: true, pair, interval: chartTf, reason: "bg_refresh" });
                }
              });
            })
            .catch(() => {});
        } else {
          const res = await fetch(`/api/candles?pair=${encodeURIComponent(pair)}&interval=${chartTf}&limit=1000`, {
            signal,
          });
          if (isStale()) return;
          if (res.ok) {
            const d = await res.json();
            if (isStale()) return;
            const candles = d.candles || [];
            c.setCandles(candles, { preserveRange: false, pair, interval: chartTf, reason: "api_full" });
            if (window.candleCacheDB) {
              window.candleCacheDB.saveCandles(pair, chartTf, candles);
            }
          }
        }

        if (isStale()) return;

        // Fetch levels, zones, and SMC markers concurrently without allowing stale responses
        const [lvlRes, sdRes, smcRes] = await Promise.all([
          fetch(`/api/market-levels?pair=${encodeURIComponent(pair)}&interval=${chartTf}`, { signal }),
          fetch(`/api/supply-demand?pair=${encodeURIComponent(pair)}&interval=${chartTf}`, { signal }),
          fetch(`/api/smc?pair=${encodeURIComponent(pair)}&interval=${chartTf}`, { signal }),
        ]);

        if (isStale()) return;

        if (lvlRes && lvlRes.ok) {
          const lvlData = await lvlRes.json();
          if (!isStale()) c.setMarketLevels(lvlData, pair, chartTf);
        }
        if (sdRes && sdRes.ok) {
          const sdData = await sdRes.json();
          if (!isStale()) c.setZones(sdData, pair, chartTf);
        }
        if (smcRes && smcRes.ok) {
          const smcData = await smcRes.json();
          if (!isStale()) c.setSMCMarkers(smcData, pair, chartTf);
        }
      } catch (err) {
        if (err && err.name === "AbortError") return;
        console.error("Error reloading chart:", err);
      }
    }
  }

  updateLiveCandle(candle, meta = {}) {
    if (this.charts.length > 0 && this.charts[0]) {
      this.charts[0].updateLiveCandle(candle, meta);
    }
  }
}

window.chartManager = null;
