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

class ChartInstance {
  constructor(containerId, index, defaultTf = "15m") {
    this.containerId = containerId;
    this.index = index;
    this.timeframe = defaultTf;

    this.chart = null;
    this.candleSeries = null;
    this.volumeSeries = null;
    this.vwapSeries = null;

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

    this.initChart();
  }

  initChart() {
    const container = document.getElementById(this.containerId);
    if (!container) return;
    container.innerHTML = "";

    const w = container.clientWidth || (container.parentElement ? container.parentElement.clientWidth : 800);
    const h = container.clientHeight || (container.parentElement ? container.parentElement.clientHeight : 500);

    // TradingView v5 Official Chart Options with AlphaQuant AI Pro watermark
    this.chart = LightweightCharts.createChart(container, {
      width: Math.max(100, w),
      height: Math.max(100, h),
      layout: {
        background: { color: "#0a0e17" },
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
        scaleMargins: { top: 0.1, bottom: 0.18 },
      },
      timeScale: {
        borderColor: "#1e2a3a",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
    });

    // 1. Candlestick Series (TradingView Standard #10b981 / #ef4444)
    this.candleSeries = this.chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    // 2. Volume Histogram Series
    this.volumeSeries = this.chart.addSeries(LightweightCharts.HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    // 3. Session VWAP
    this.vwapSeries = this.chart.addSeries(LightweightCharts.LineSeries, {
      color: "#06b6d4",
      lineWidth: 1.5,
      title: "VWAP",
      priceLineVisible: false,
    });

    // Setup Historical Scroll Pagination Hook
    this.setupHistoricalPaginationHook();

    // Setup Crosshair HUD
    this.setupCrosshairHUD();
  }

  setupCrosshairHUD() {
    this.chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData) return;
      const bar = param.seriesData.get(this.candleSeries);
      if (!bar) return;

      const oEl = document.getElementById("hud-o");
      const hEl = document.getElementById("hud-h");
      const lEl = document.getElementById("hud-l");
      const cEl = document.getElementById("hud-c");

      if (oEl && bar.open !== undefined) oEl.textContent = bar.open.toFixed(2);
      if (hEl && bar.high !== undefined) hEl.textContent = bar.high.toFixed(2);
      if (lEl && bar.low !== undefined) lEl.textContent = bar.low.toFixed(2);
      if (cEl && bar.close !== undefined) {
        cEl.textContent = bar.close.toFixed(2);
        cEl.className = bar.close >= bar.open ? "hud-val up-color" : "hud-val down-color";
      }
    });
  }

  setupHistoricalPaginationHook() {
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(async (newRange) => {
      if (window.drawingTools && typeof window.drawingTools.reprojectDrawings === "function") {
        window.drawingTools.reprojectDrawings();
      }
      if (!newRange || this.isLoadingOlder || !this.hasMoreHistorical || this.isReplayActive) {
        return;
      }
      // If user scrolls backwards within 20 bars of the left edge
      if (newRange.from < 20 && this.allCandles.length > 0) {
        await this.fetchOlderHistoricalChunk();
      }
    });
  }

  async fetchOlderHistoricalChunk() {
    if (this.isLoadingOlder || !this.hasMoreHistorical || this.allCandles.length === 0) return;
    this.isLoadingOlder = true;

    const oldestTime = this.allCandles[0].time;
    const pair = window.appState ? (window.appState.currentPair || window.appState.pair || "BTC/USDT") : "BTC/USDT";

    const badge = document.getElementById("hud-history-badge");
    if (badge) badge.textContent = "Loading older history...";

    try {
      const res = await fetch(
        `/api/candles?pair=${encodeURIComponent(pair)}&interval=${this.timeframe}&limit=1000&endTime=${oldestTime - 1}`
      );
      if (res.ok) {
        const data = await res.json();
        const olderCandles = data.candles || [];

        if (olderCandles.length === 0) {
          this.hasMoreHistorical = false;
          if (badge) badge.textContent = "All 1Y+ History Loaded";
        } else {
          // Prepend older data without altering scroll viewport
          const map = new Map();
          olderCandles.forEach((c) => map.set(c.time, c));
          this.allCandles.forEach((c) => map.set(c.time, c));
          this.allCandles = Array.from(map.values()).sort((a, b) => a.time - b.time);

          this.candleSeries.setData(this.allCandles);
          const vols = this.allCandles.map((c) => ({
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? "rgba(16, 185, 129, 0.35)" : "rgba(239, 68, 68, 0.35)",
          }));
          this.volumeSeries.setData(vols);

          // Update cache
          if (window.candleCacheDB) {
            window.candleCacheDB.saveCandles(pair, this.timeframe, this.allCandles);
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

  setCandles(candles) {
    if (!candles || candles.length === 0) return;
    this.allCandles = candles;
    this.hasMoreHistorical = true;

    this.candleSeries.setData(candles);
    const vols = candles.map((c) => ({
      time: c.time,
      value: c.volume,
      color: c.close >= c.open ? "rgba(16, 185, 129, 0.35)" : "rgba(239, 68, 68, 0.35)",
    }));
    this.volumeSeries.setData(vols);

    this.chart.timeScale().fitContent();

    // Apply active overlay indicators
    this.reloadActiveIndicators();

    // Update subcharts (RSI & MACD)
    if (this.index === 0 && window.indicatorsEngine) {
      window.indicatorsEngine.updateData(candles);
    }

    const badge = document.getElementById("hud-history-badge");
    if (badge) badge.textContent = `History: ${candles.length} bars`;
  }

  updateLiveCandle(candle) {
    if (!candle || !this.candleSeries || this.isReplayActive) return;

    this.candleSeries.update(candle);
    if (this.volumeSeries && candle.volume !== undefined) {
      this.volumeSeries.update({
        time: candle.time,
        value: candle.volume,
        color: candle.close >= candle.open ? "rgba(16, 185, 129, 0.35)" : "rgba(239, 68, 68, 0.35)",
      });
    }

    // Maintain in memory
    if (this.allCandles.length > 0) {
      const last = this.allCandles[this.allCandles.length - 1];
      if (last.time === candle.time) {
        this.allCandles[this.allCandles.length - 1] = candle;
      } else if (candle.time > last.time) {
        this.allCandles.push(candle);
      }
    }
  }

  reloadActiveIndicators() {
    if (!window.indicatorsEngine || this.allCandles.length < 10) return;

    const catalog = window.indicatorsEngine.catalog;
    catalog.forEach((item) => {
      if (item.type === "overlay") {
        if (item.id === "bollinger") {
          const keys = ["bollinger_upper", "bollinger_middle", "bollinger_lower"];
          if (item.enabled) {
            if (!this.overlaySeries["bollinger_upper"]) {
              this.overlaySeries["bollinger_upper"] = this.chart.addSeries(LightweightCharts.LineSeries, {
                color: "rgba(192, 132, 252, 0.7)",
                lineWidth: 1,
                lineStyle: 2,
                title: "BB Upper",
                priceLineVisible: false,
              });
              this.overlaySeries["bollinger_middle"] = this.chart.addSeries(LightweightCharts.LineSeries, {
                color: "rgba(192, 132, 252, 0.9)",
                lineWidth: 1,
                title: "BB Mid",
                priceLineVisible: false,
              });
              this.overlaySeries["bollinger_lower"] = this.chart.addSeries(LightweightCharts.LineSeries, {
                color: "rgba(192, 132, 252, 0.7)",
                lineWidth: 1,
                lineStyle: 2,
                title: "BB Lower",
                priceLineVisible: false,
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
        } else if (item.id === "supertrend") {
          if (item.enabled) {
            if (!this.overlaySeries["supertrend"]) {
              this.overlaySeries["supertrend"] = this.chart.addSeries(LightweightCharts.LineSeries, {
                color: "#10b981",
                lineWidth: 2,
                title: "Supertrend",
                priceLineVisible: false,
              });
            }
            const st = window.indicatorsEngine.calculateSupertrend(this.allCandles, item.period || 10, item.multiplier || 3.0);
            if (st.length > 0) {
              this.overlaySeries["supertrend"].setData(st);
            }
          } else {
            if (this.overlaySeries["supertrend"]) {
              this.chart.removeSeries(this.overlaySeries["supertrend"]);
              delete this.overlaySeries["supertrend"];
            }
          }
        } else {
          if (item.enabled) {
            if (!this.overlaySeries[item.id]) {
              this.overlaySeries[item.id] = this.chart.addSeries(LightweightCharts.LineSeries, {
                color: item.color || "#3b82f6",
                lineWidth: 1,
                title: item.name.split(" ")[0],
                priceLineVisible: false,
              });
            }
            let data = [];
            if (item.id.startsWith("ema")) {
              data = window.indicatorsEngine.calculateEMA(this.allCandles, item.period);
            } else if (item.id.startsWith("sma")) {
              data = window.indicatorsEngine.calculateSMA(this.allCandles, item.period);
            }
            if (data.length > 0) {
              this.overlaySeries[item.id].setData(data);
            }
          } else {
            if (this.overlaySeries[item.id]) {
              this.chart.removeSeries(this.overlaySeries[item.id]);
              delete this.overlaySeries[item.id];
            }
          }
        }
      }
    });
  }

  scrollToLive() {
    if (this.chart) {
      this.chart.timeScale().scrollToRealTime();
    }
  }

  setMarketLevels(levels) {
    if (!levels || !this.candleSeries) return;

    if (this.pdhLine) { this.candleSeries.removePriceLine(this.pdhLine); this.pdhLine = null; }
    if (this.pdlLine) { this.candleSeries.removePriceLine(this.pdlLine); this.pdlLine = null; }
    if (this.pwhLine) { this.candleSeries.removePriceLine(this.pwhLine); this.pwhLine = null; }
    if (this.pwlLine) { this.candleSeries.removePriceLine(this.pwlLine); this.pwlLine = null; }

    if (levels.pdh) {
      this.pdhLine = this.candleSeries.createPriceLine({
        price: levels.pdh,
        color: "#f59e0b",
        lineWidth: 1,
        lineStyle: 2,
        title: "PDH",
      });
    }
    if (levels.pdl) {
      this.pdlLine = this.candleSeries.createPriceLine({
        price: levels.pdl,
        color: "#f59e0b",
        lineWidth: 1,
        lineStyle: 2,
        title: "PDL",
      });
    }
    if (levels.pwh) {
      this.pwhLine = this.candleSeries.createPriceLine({
        price: levels.pwh,
        color: "#8b5cf6",
        lineWidth: 1,
        lineStyle: 3,
        title: "PWH",
      });
    }
    if (levels.pwl) {
      this.pwlLine = this.candleSeries.createPriceLine({
        price: levels.pwl,
        color: "#8b5cf6",
        lineWidth: 1,
        lineStyle: 3,
        title: "PWL",
      });
    }

    if (levels.vwap_series && this.vwapSeries) {
      this.vwapSeries.setData(levels.vwap_series);
    }
  }

  setZones(sdData) {
    this.zonePriceLines.forEach((l) => {
      try { this.candleSeries.removePriceLine(l); } catch (e) {}
    });
    this.zonePriceLines = [];

    if (!sdData || !this.candleSeries) return;

    if (sdData.demand) {
      sdData.demand.slice(0, 2).forEach((z, i) => {
        const top = z.proximal;
        const bot = z.distal;
        const l1 = this.candleSeries.createPriceLine({
          price: top,
          color: "#10b981",
          lineWidth: 1,
          lineStyle: 0,
          title: `DEMAND #${i + 1} (${z.lifecycle_state || "ACTIVE"})`,
        });
        const l2 = this.candleSeries.createPriceLine({
          price: bot,
          color: "rgba(16, 185, 129, 0.4)",
          lineWidth: 1,
          lineStyle: 2,
          title: "DEMAND DIST",
        });
        this.zonePriceLines.push(l1, l2);
      });
    }

    if (sdData.supply) {
      sdData.supply.slice(0, 2).forEach((z, i) => {
        const bot = z.proximal;
        const top = z.distal;
        const l1 = this.candleSeries.createPriceLine({
          price: bot,
          color: "#ef4444",
          lineWidth: 1,
          lineStyle: 0,
          title: `SUPPLY #${i + 1} (${z.lifecycle_state || "ACTIVE"})`,
        });
        const l2 = this.candleSeries.createPriceLine({
          price: top,
          color: "rgba(239, 68, 68, 0.4)",
          lineWidth: 1,
          lineStyle: 2,
          title: "SUPPLY DIST",
        });
        this.zonePriceLines.push(l1, l2);
      });
    }
  }

  setSMCMarkers(smcData) {
    if (!smcData || !this.candleSeries) return;
    const markers = [];
    const struct = smcData.bos_choch || smcData.market_structure || [];

    struct.slice(-12).forEach((st) => {
      markers.push({
        time: st.time,
        position: st.direction === "BULLISH" ? "belowBar" : "aboveBar",
        color: st.direction === "BULLISH" ? "#10b981" : "#ef4444",
        shape: st.direction === "BULLISH" ? "arrowUp" : "arrowDown",
        text: `${st.event || st.type} (${st.direction === "BULLISH" ? "Bull" : "Bear"})`,
      });
    });

    markers.sort((a, b) => a.time - b.time);
    try {
      if (LightweightCharts.createSeriesMarkers) {
        LightweightCharts.createSeriesMarkers(this.candleSeries, markers);
      } else if (this.candleSeries.setMarkers) {
        this.candleSeries.setMarkers(markers);
      }
    } catch (e) {}
  }

  resize() {
    const container = document.getElementById(this.containerId);
    if (this.chart && container) {
      const w = container.clientWidth || (container.parentElement ? container.parentElement.clientWidth : 800);
      const h = container.clientHeight || (container.parentElement ? container.parentElement.clientHeight : 500);
      if (w > 0 && h > 0) {
        this.chart.applyOptions({
          width: w,
          height: h,
        });
      }
    }
  }
}

class WorkspaceChartManager {
  constructor() {
    this.layout = "layout-1";
    this.charts = [];

    this.initWorkspace();
    this.setupResizeListeners();
  }

  initWorkspace() {
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
    window.addEventListener("resize", () => this.resize());
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
    const pair = window.appState ? (window.appState.currentPair || window.appState.pair || "BTC/USDT") : "BTC/USDT";
    const interval = window.appState ? (window.appState.currentInterval || window.appState.interval || "15m") : "15m";

    if (this.charts.length > 0 && this.charts[0]) {
      this.charts[0].timeframe = interval;
    }

    for (const c of this.charts) {
      try {
        let cached = null;
        if (window.candleCacheDB) {
          cached = await window.candleCacheDB.getCandles(pair, c.timeframe);
        }

        if (cached && cached.length >= 100) {
          c.setCandles(cached);
          fetch(`/api/candles?pair=${encodeURIComponent(pair)}&interval=${c.timeframe}&limit=100`)
            .then((r) => r.json())
            .then((d) => {
              if (d && d.candles && window.candleCacheDB) {
                window.candleCacheDB.saveCandles(pair, c.timeframe, d.candles).then((merged) => {
                  c.setCandles(merged);
                });
              }
            })
            .catch(() => {});
        } else {
          const res = await fetch(`/api/candles?pair=${encodeURIComponent(pair)}&interval=${c.timeframe}&limit=1000`);
          if (res.ok) {
            const d = await res.json();
            const candles = d.candles || [];
            c.setCandles(candles);
            if (window.candleCacheDB) {
              window.candleCacheDB.saveCandles(pair, c.timeframe, candles);
            }
          }
        }

        // Market levels & S/D
        const lvlRes = await fetch(`/api/market-levels?pair=${encodeURIComponent(pair)}&interval=${c.timeframe}`);
        if (lvlRes.ok) c.setMarketLevels(await lvlRes.json());

        const sdRes = await fetch(`/api/supply-demand?pair=${encodeURIComponent(pair)}&interval=${c.timeframe}`);
        if (sdRes.ok) c.setZones(await sdRes.json());

        const smcRes = await fetch(`/api/smc?pair=${encodeURIComponent(pair)}&interval=${c.timeframe}`);
        if (smcRes.ok) c.setSMCMarkers(await smcRes.json());
      } catch (err) {
        console.error("Error reloading chart:", err);
      }
    }
  }

  updateLiveCandle(candle) {
    if (this.charts.length > 0 && this.charts[0]) {
      this.charts[0].updateLiveCandle(candle);
    }
  }
}

window.chartManager = null;
