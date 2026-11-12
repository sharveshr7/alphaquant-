/**
 * AlphaQuant Pro - IndexedDB Local Candle Caching Engine
 *
 * Persists up to 365+ days of historical OHLCV candlestick data locally in the browser.
 * Reduces redundant network requests and enables instantaneous chart loading.
 */

class CandleCacheDB {
  constructor() {
    this.dbName = "AlphaQuantChartCacheDB";
    this.version = 1;
    this.storeName = "candles";
    this.db = null;
    this.readyPromise = this.init();
  }

  init() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.dbName, this.version);

      request.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(this.storeName)) {
          // Key format: `${symbol}_${interval}`
          db.createObjectStore(this.storeName, { keyPath: "key" });
        }
      };

      request.onsuccess = (e) => {
        this.db = e.target.result;
        resolve(this.db);
      };

      request.onerror = (e) => {
        console.warn("IndexedDB initialization failed, falling back to memory:", e);
        resolve(null);
      };
    });
  }

  sanitize(candles) {
    if (!Array.isArray(candles)) return [];
    const byTime = new Map();
    for (const raw of candles) {
      if (!raw || typeof raw !== "object") continue;
      const t = Number(raw.time);
      const o = Number(raw.open);
      const h0 = Number(raw.high);
      const l0 = Number(raw.low);
      const c = Number(raw.close);
      const v = raw.volume === undefined ? 0 : Number(raw.volume);
      if (
        !Number.isFinite(t) || t <= 0 ||
        !Number.isFinite(o) || !Number.isFinite(h0) || !Number.isFinite(l0) || !Number.isFinite(c) ||
        !Number.isFinite(v) || v < 0 ||
        o <= 0 || h0 <= 0 || l0 <= 0 || c <= 0 || h0 < l0
      ) {
        continue;
      }
      byTime.set(Math.floor(t), {
        time: Math.floor(t),
        open: o,
        high: Math.max(h0, o, c),
        low: Math.min(l0, o, c),
        close: c,
        volume: v,
      });
    }
    return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  }

  getKey(pair, interval) {
    const cleanPair = (pair || "BTC/USDT").replace("/", "").toUpperCase();
    return `${cleanPair}_${interval}`;
  }

  async getCandles(pair, interval) {
    await this.readyPromise;
    if (!this.db) return null;

    const key = this.getKey(pair, interval);
    return new Promise((resolve) => {
      try {
        const tx = this.db.transaction([this.storeName], "readonly");
        const store = tx.objectStore(this.storeName);
        const req = store.get(key);

        req.onsuccess = () => {
          if (req.result && Array.isArray(req.result.candles) && req.result.candles.length > 0) {
            const clean = this.sanitize(req.result.candles);
            resolve(clean.length > 0 ? clean : null);
          } else {
            resolve(null);
          }
        };

        req.onerror = () => resolve(null);
      } catch (err) {
        console.warn("Error reading from IndexedDB:", err);
        resolve(null);
      }
    });
  }

  async saveCandles(pair, interval, candles) {
    await this.readyPromise;
    const cleanIncoming = this.sanitize(candles);
    if (!this.db || cleanIncoming.length === 0) return cleanIncoming;

    const key = this.getKey(pair, interval);
    return new Promise((resolve) => {
      try {
        const tx = this.db.transaction([this.storeName], "readwrite");
        const store = tx.objectStore(this.storeName);

        // Fetch existing to merge seamlessly
        const getReq = store.get(key);
        getReq.onsuccess = () => {
          let merged = cleanIncoming;
          if (getReq.result && Array.isArray(getReq.result.candles)) {
            merged = this.sanitize([...getReq.result.candles, ...cleanIncoming]);
          }

          store.put({
            key: key,
            updatedAt: Date.now(),
            candles: merged,
          });
          resolve(merged);
        };

        getReq.onerror = () => {
          store.put({
            key: key,
            updatedAt: Date.now(),
            candles: cleanIncoming,
          });
          resolve(cleanIncoming);
        };
      } catch (err) {
        console.warn("Error writing to IndexedDB:", err);
        resolve(cleanIncoming);
      }
    });
  }

  async clearCache() {
    await this.readyPromise;
    if (!this.db) return;
    try {
      const tx = this.db.transaction([this.storeName], "readwrite");
      tx.objectStore(this.storeName).clear();
    } catch (e) {
      console.warn("Failed to clear candle cache:", e);
    }
  }
}

window.candleCacheDB = new CandleCacheDB();
