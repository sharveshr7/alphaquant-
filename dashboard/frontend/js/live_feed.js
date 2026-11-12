/**
 * AlphaQuant Pro - Direct Binance Public WebSocket & Real-Time Feed Engine
 *
 * Connects directly to Binance USDⓈ-M Futures Public WebSocket multi-stream:
 * wss://fstream.binance.com/stream?streams=<symbol>@kline_<interval>/<symbol>@ticker
 *
 * Features:
 * - Sub-second live price tick execution
 * - Non-repainting live candle updates (open, high, low, close, volume)
 * - Automatic timeframe and symbol subscription switching
 * - High-precision candle countdown timer
 * - Nav sidebar connection status indicator & header status pill
 * - Auto-reconnection with exponential backoff & backend proxy fallback
 */

class LiveFeed {
  constructor() {
    this.ws = null;
    this.fallbackWs = null;
    this.reconnectTimer = null;
    this.fallbackReconnectTimer = null;
    this.directFrameTimer = null;
    this.directFreshnessTimer = null;
    this.currentPair = "BTC/USDT";
    this.currentInterval = "15m";
    this.onTickCallbacks = new Set();
    this.onSignalEventCallbacks = new Set();
    this.candleCloseTime = 0;
    this.countdownTimer = null;
    this.state = "DISCONNECTED"; // LIVE, CONNECTING, DISCONNECTED, ERROR
    this._connSeq = 0;
    this._reconnectAttempts = 0;
    this._fallbackReconnectAttempts = 0;
    this._fallbackSignalEventsOnly = true;
    this._hasDirectData = false;

    this.initCountdownLoop();
  }

  getCleanSymbol(pair) {
    return (pair || this.currentPair).replace(/[\/\-_]/g, "").toLowerCase();
  }

  closeSockets() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.fallbackReconnectTimer) {
      clearTimeout(this.fallbackReconnectTimer);
      this.fallbackReconnectTimer = null;
    }
    if (this.directFrameTimer) {
      clearTimeout(this.directFrameTimer);
      this.directFrameTimer = null;
    }
    if (this.directFreshnessTimer) {
      clearInterval(this.directFreshnessTimer);
      this.directFreshnessTimer = null;
    }
    if (this.ws) {
      const oldWs = this.ws;
      this.ws = null;
      oldWs.onopen = null;
      oldWs.onmessage = null;
      oldWs.onclose = null;
      oldWs.onerror = null;
      try {
        if (oldWs.readyState === WebSocket.OPEN || oldWs.readyState === WebSocket.CONNECTING) {
          oldWs.close();
        }
      } catch (e) {}
    }
    if (this.fallbackWs) {
      const oldFb = this.fallbackWs;
      this.fallbackWs = null;
      oldFb.onopen = null;
      oldFb.onmessage = null;
      oldFb.onclose = null;
      oldFb.onerror = null;
      try {
        if (oldFb.readyState === WebSocket.OPEN || oldFb.readyState === WebSocket.CONNECTING) {
          oldFb.close();
        }
      } catch (e) {}
    }
  }

  connect() {
    this.closeSockets();
    const mySeq = ++this._connSeq;
    const expectedPair = this.currentPair;
    const expectedInterval = this.currentInterval;
    this._hasDirectData = false;
    const symbol = this.getCleanSymbol(expectedPair);
    const interval = expectedInterval;

    this.setState("CONNECTING");
    this.directFreshnessTimer = setInterval(() => {
      if (
        mySeq === this._connSeq &&
        this._hasDirectData &&
        Date.now() - this._lastDirectFrameAt > 15000
      ) {
        this._hasDirectData = false;
        this.setState("DELAYED");
        this.connectFallback(mySeq, expectedPair, expectedInterval, false);
        this.scheduleReconnect(mySeq);
      }
    }, 1000);

    // Binance USDⓈ-M Futures Public WebSocket combined market stream
    const binanceWsUrl = `wss://fstream.binance.com/stream?streams=${symbol}@kline_${interval}/${symbol}@ticker`;

    try {
      const ws = new WebSocket(binanceWsUrl);
      this.ws = ws;

      ws.onopen = () => {
        if (mySeq !== this._connSeq) {
          try { ws.close(); } catch (e) {}
          return;
        }
        console.log(`[Binance WebSocket] Socket opened for ${symbol}@kline_${interval}; awaiting a market frame`);
        this.connectFallback(mySeq, expectedPair, expectedInterval, true);
        this.directFrameTimer = setTimeout(() => {
          if (mySeq !== this._connSeq || this._hasDirectData) return;
          this.setState("DELAYED");
          this.connectFallback(mySeq, expectedPair, expectedInterval, false);
          try { ws.close(); } catch (e) {}
        }, 5000);
      };

      ws.onmessage = (event) => {
        if (mySeq !== this._connSeq) return;
        try {
          const payload = JSON.parse(event.data);
          if (this.handleBinanceMessage(payload, expectedPair, expectedInterval)) {
            this._hasDirectData = true;
            this._lastDirectFrameAt = Date.now();
            this._reconnectAttempts = 0;
            if (this.directFrameTimer) {
              clearTimeout(this.directFrameTimer);
              this.directFrameTimer = null;
            }
            this.setState("LIVE");
            this.connectFallback(mySeq, expectedPair, expectedInterval, true);
          }
        } catch (err) {
          console.warn("Binance WS parse error:", err);
        }
      };

      ws.onclose = (event) => {
        if (mySeq !== this._connSeq) return;
        if (this.directFrameTimer) {
          clearTimeout(this.directFrameTimer);
          this.directFrameTimer = null;
        }
        console.warn(`[Binance WebSocket] Closed (code: ${event.code}), scheduling reconnect...`);
        this._hasDirectData = false;
        this.setState("DELAYED");
        this.connectFallback(mySeq, expectedPair, expectedInterval, false);
        this.scheduleReconnect(mySeq);
      };

      ws.onerror = (err) => {
        if (mySeq !== this._connSeq) return;
        console.warn("[Binance WebSocket] Error encountered, falling back to local stream...", err);
        this.setState("DELAYED");
        this.connectFallback(mySeq, expectedPair, expectedInterval, false);
      };
    } catch (e) {
      if (mySeq !== this._connSeq) return;
      console.warn("Could not initiate Binance WebSocket, connecting fallback:", e);
      this.setState("DELAYED");
      this.connectFallback(mySeq, expectedPair, expectedInterval, false);
    }
  }

  connectFallback(
    expectedSeq = this._connSeq,
    expectedPair = this.currentPair,
    expectedInterval = this.currentInterval,
    signalEventsOnly = true
  ) {
    if (expectedSeq !== this._connSeq) return;
    this._fallbackSignalEventsOnly = signalEventsOnly;
    if (this.fallbackWs && (this.fallbackWs.readyState === WebSocket.OPEN || this.fallbackWs.readyState === WebSocket.CONNECTING)) {
      if (this.fallbackWs.readyState === WebSocket.OPEN) {
        this.fallbackWs.send(JSON.stringify({
          pair: expectedPair,
          interval: expectedInterval,
          signal_events_only: signalEventsOnly,
        }));
      }
      return;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host || "127.0.0.1:8000";
    const fallbackUrl = `${protocol}//${host}/ws/live`;

    try {
      const fb = new WebSocket(fallbackUrl);
      this.fallbackWs = fb;
      fb.onopen = () => {
        if (expectedSeq !== this._connSeq) {
          try { fb.close(); } catch (e) {}
          return;
        }
        this._fallbackReconnectAttempts = 0;
        fb.send(JSON.stringify({
          pair: expectedPair,
          interval: expectedInterval,
          signal_events_only: this._fallbackSignalEventsOnly,
        }));
      };
      fb.onmessage = (event) => {
        if (expectedSeq !== this._connSeq) return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === "signal_event") {
            this.broadcastSignalEvent(data);
            return;
          }
          if (data.type === "tick") {
            if (data.pair && data.pair !== this.currentPair) return;
            if (data.interval && data.interval !== this.currentInterval) return;
            if (!this._hasDirectData) this.setState("LIVE (Proxy)");
            if (this._hasDirectData) return;
            this.handleTick(data, expectedPair, expectedInterval);
          }
        } catch (e) {}
      };
      fb.onclose = () => {
        if (expectedSeq !== this._connSeq) return;
        this.fallbackWs = null;
        this.scheduleFallbackReconnect(expectedSeq, expectedPair, expectedInterval);
      };
    } catch (e) {
      this.scheduleFallbackReconnect(expectedSeq, expectedPair, expectedInterval);
    }
  }

  scheduleFallbackReconnect(
    expectedSeq = this._connSeq,
    expectedPair = this.currentPair,
    expectedInterval = this.currentInterval
  ) {
    if (expectedSeq !== this._connSeq || this.fallbackReconnectTimer) return;
    const delay = Math.min(1000 * (2 ** this._fallbackReconnectAttempts), 30000);
    this._fallbackReconnectAttempts += 1;
    this.fallbackReconnectTimer = setTimeout(() => {
      this.fallbackReconnectTimer = null;
      this.connectFallback(
        expectedSeq,
        expectedPair,
        expectedInterval,
        this._fallbackSignalEventsOnly
      );
    }, delay);
  }

  scheduleReconnect(expectedSeq = this._connSeq) {
    if (expectedSeq !== this._connSeq) return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    const delay = Math.min(1000 * (2 ** this._reconnectAttempts), 30000);
    this._reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      if (expectedSeq !== this._connSeq) return;
      this.connect();
    }, delay);
  }

  subscribe(pair, interval) {
    const symbolChanged = this.currentPair !== pair;
    const intervalChanged = this.currentInterval !== interval;

    this.currentPair = pair;
    this.currentInterval = interval;

    if (symbolChanged || intervalChanged || !this.ws || this.ws.readyState > WebSocket.OPEN) {
      this.connect();
    }
  }

  handleBinanceMessage(payload, expectedPair = this.currentPair, expectedInterval = this.currentInterval) {
    if (!payload || !payload.stream || !payload.data) return false;
    if (expectedPair !== this.currentPair || expectedInterval !== this.currentInterval) return false;

    const stream = payload.stream;
    const expectedSym = this.getCleanSymbol(expectedPair);
    if (!stream.toLowerCase().startsWith(expectedSym + "@")) return false;

    const data = payload.data;
    let processed = false;

    // 1. Kline stream update
    if (stream.includes("@kline")) {
      const k = data.k;
      if (!k || (k.i && k.i !== expectedInterval)) return false;

      const tSec = Math.floor(Number(k.t) / 1000);
      const o = parseFloat(k.o);
      const h = parseFloat(k.h);
      const l = parseFloat(k.l);
      const c = parseFloat(k.c);
      const v = parseFloat(k.v);
      if (
        !Number.isFinite(tSec) || tSec <= 0 ||
        !Number.isFinite(o) || !Number.isFinite(h) || !Number.isFinite(l) || !Number.isFinite(c) ||
        o <= 0 || h <= 0 || l <= 0 || c <= 0 || h < l
      ) {
        return false;
      }
      processed = true;

      this.candleCloseTime = k.T; // Close time in epoch ms

      const candle = {
        time: tSec, // Epoch seconds for Lightweight Charts
        open: o,
        high: Math.max(h, o, c),
        low: Math.min(l, o, c),
        close: c,
        volume: Number.isFinite(v) && v >= 0 ? v : 0,
        isClosed: k.x,
      };

      // Push to chart with active pair/interval verification
      if (window.chartManager) {
        window.chartManager.updateLiveCandle(candle, { pair: expectedPair, interval: expectedInterval });
      }

      // Synchronize price across Header, Watchlist, AppState
      if (typeof window.syncLivePrice === "function") {
        window.syncLivePrice(candle.close, expectedPair);
      } else if (window.appState) {
        window.appState.currentPrice = candle.close;
        window.appState.lastTickTimestamp = Date.now();
      }

      this.broadcastTick({ candle: candle, pair: expectedPair, interval: expectedInterval });
    }

    // 2. Ticker 24h stream update
    if (stream.includes("@ticker")) {
      const lp = parseFloat(data.c);
      if (!Number.isFinite(lp) || lp <= 0) return false;
      processed = true;
      const ticker = {
        last_price: lp,
        price_change: parseFloat(data.p),
        price_change_percent: parseFloat(data.P),
        high_24h: parseFloat(data.h),
        low_24h: parseFloat(data.l),
        volume_24h: parseFloat(data.v),
        quote_volume_24h: parseFloat(data.q),
      };

      this.updateHeaderHUD(ticker);
      if (typeof window.syncLivePrice === "function") {
        window.syncLivePrice(lp, expectedPair, ticker);
      }
      this.broadcastTick({ ticker: ticker, pair: expectedPair, interval: expectedInterval });
    }
    return processed;
  }

  handleTick(data, expectedPair = this.currentPair, expectedInterval = this.currentInterval) {
    if (expectedPair !== this.currentPair || expectedInterval !== this.currentInterval) return;
    const { ticker, candle } = data;
    if (ticker) this.updateHeaderHUD(ticker);
    if (candle && window.chartManager) {
      window.chartManager.updateLiveCandle(candle, { pair: expectedPair, interval: expectedInterval });
    }
    this.broadcastTick(data);
  }

  updateHeaderHUD(ticker) {
    if (!ticker) return;
    if (window.appState) {
      window.appState.lastTickTimestamp = Date.now();
    }

    const priceElem = document.getElementById("ticker-price");
    const changeElem = document.getElementById("ticker-change");
    const qaPrice = document.getElementById("qa-price");
    const qaChange = document.getElementById("qa-change");

    const p = Number(ticker.last_price || 0);
    const dec = p < 0.01 ? 6 : p < 1 ? 5 : p < 50 ? 4 : 2;
    const isUp = (ticker.price_change || 0) >= 0;
    const formattedPrice = p.toLocaleString("en-US", {
      minimumFractionDigits: dec,
      maximumFractionDigits: dec,
    });

    if (priceElem) {
      priceElem.textContent = `$${formattedPrice}`;
      priceElem.className = `live-price-val ${isUp ? "up-color" : "down-color"}`;
    }

    if (changeElem && ticker.price_change_percent !== undefined) {
      changeElem.textContent = `${isUp ? "+" : ""}${Number(ticker.price_change_percent).toFixed(2)}%`;
      changeElem.className = `live-change-badge ${isUp ? "bg-up" : "bg-down"}`;
    }

    if (qaPrice) {
      qaPrice.textContent = `$${formattedPrice}`;
      qaPrice.className = `font-mono fs-lg font-bold ${isUp ? "up-color" : "down-color"}`;
    }
    if (qaChange && ticker.price_change_percent !== undefined) {
      qaChange.textContent = `${isUp ? "+" : ""}${Number(ticker.price_change_percent).toFixed(2)}%`;
      qaChange.className = `fs-xs font-bold ${isUp ? "up-color" : "down-color"}`;
    }
  }

  initCountdownLoop() {
    if (this.countdownTimer) clearInterval(this.countdownTimer);
    this.countdownTimer = setInterval(() => {
      const countdownEl = document.getElementById("candle-countdown-val");
      if (!countdownEl) return;

      let diffMs = this.candleCloseTime ? this.candleCloseTime - Date.now() : 0;
      if (!this.candleCloseTime || diffMs < 0) {
        // Compute from standard interval boundary if WS kline hasn't arrived yet
        const tf = window.appState ? window.appState.currentInterval || "15m" : "15m";
        const now = new Date();
        if (tf === "1w") {
          const daysUntilMonday = (8 - now.getUTCDay()) % 7 || 7;
          diffMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + daysUntilMonday) - Date.now();
        } else if (tf === "1M") {
          diffMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1) - Date.now();
        } else {
          const tfMap = { "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400 };
          const step = tfMap[tf] || 900;
          const nowSec = Math.floor(Date.now() / 1000);
          diffMs = (step - (nowSec % step)) * 1000;
        }
      }

      const totalSec = Math.max(0, Math.floor(diffMs / 1000));
      const hours = Math.floor(totalSec / 3600);
      const minutes = Math.floor((totalSec % 3600) / 60);
      const seconds = totalSec % 60;
      countdownEl.textContent = hours > 0
        ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
        : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }, 1000);
  }

  setState(newState) {
    this.state = newState;
    const mapped = newState === "LIVE" || newState === "LIVE (Proxy)"
      ? "LIVE"
      : newState === "CONNECTING" || newState === "DELAYED"
      ? "DELAYED"
      : "DISCONNECTED";

    if (typeof window.setConnectionStatus === "function") {
      window.setConnectionStatus(mapped);
    }
  }

  onTick(callback) {
    if (typeof callback === "function") {
      this.onTickCallbacks.add(callback);
    }
  }

  onSignalEvent(callback) {
    if (typeof callback === "function") {
      this.onSignalEventCallbacks.add(callback);
    }
  }

  broadcastTick(data) {
    this.onTickCallbacks.forEach((cb) => {
      try { cb(data); } catch (e) {}
    });
  }

  broadcastSignalEvent(data) {
    this.onSignalEventCallbacks.forEach((cb) => {
      try { cb(data); } catch (e) {}
    });
  }
}

window.liveFeed = null;
