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
            resolve(req.result.candles);
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
    if (!this.db || !candles || candles.length === 0) return;

    const key = this.getKey(pair, interval);
    return new Promise((resolve) => {
      try {
        const tx = this.db.transaction([this.storeName], "readwrite");
        const store = tx.objectStore(this.storeName);

        // Fetch existing to merge seamlessly
        const getReq = store.get(key);
        getReq.onsuccess = () => {
          let merged = candles;
          if (getReq.result && Array.isArray(getReq.result.candles)) {
            const map = new Map();
            getReq.result.candles.forEach((c) => map.set(c.time, c));
            candles.forEach((c) => map.set(c.time, c));
            merged = Array.from(map.values()).sort((a, b) => a.time - b.time);
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
            candles: candles,
          });
          resolve(candles);
        };
      } catch (err) {
        console.warn("Error writing to IndexedDB:", err);
        resolve(candles);
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
