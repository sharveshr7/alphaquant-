/**
 * AlphaQuant Pro - Watchlist Module
 *
 * Capabilities:
 * - LocalStorage persistence for user favorite trading pairs
 * - Sync with backend /api/watchlist
 * - Live price, 24h change, high/low, and volume tracking
 * - Add/remove pair controls
 * - Quick-open chart navigation
 */

class WatchlistModule {
  constructor() {
    this.tableBody = document.getElementById("watchlist-table-body");
    this.addInput = document.getElementById("input-add-watchlist");
    this.addBtn = document.getElementById("btn-add-watchlist");

    this.watchlist = [];
    this.tickersMap = {};
    this.refreshTimer = null;

    this.init();
  }

  init() {
    this.loadWatchlist();

    if (this.addBtn && this.addInput) {
      this.addBtn.addEventListener("click", () => this.handleAddPair());
      this.addInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") this.handleAddPair();
      });
    }

    // Refresh every 10 seconds
    this.fetchTickers();
    this.refreshTimer = setInterval(() => this.fetchTickers(), 10000);
  }

  loadWatchlist() {
    try {
      const saved = localStorage.getItem("aq_watchlist");
      if (saved) {
        this.watchlist = JSON.parse(saved);
      } else if (window.appState && window.appState.watchlist) {
        this.watchlist = [...window.appState.watchlist];
      } else {
        this.watchlist = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "AVAX/USDT"];
      }
    } catch (e) {
      this.watchlist = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"];
    }

    if (window.appState) {
      window.appState.watchlist = this.watchlist;
    }

    // Try backend sync
    fetch("/api/watchlist")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && Array.isArray(data.watchlist) && data.watchlist.length > 0) {
          this.watchlist = Array.from(new Set([...this.watchlist, ...data.watchlist]));
          this.saveWatchlist();
          this.render();
        }
      })
      .catch(() => {});

    this.render();
  }

  saveWatchlist() {
    try {
      localStorage.setItem("aq_watchlist", JSON.stringify(this.watchlist));
      if (window.appState) {
        window.appState.watchlist = this.watchlist;
      }
      fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ watchlist: this.watchlist }),
      }).catch(() => {});
    } catch (e) {}
  }

  handleAddPair() {
    if (!this.addInput) return;
    let sym = this.addInput.value.trim().toUpperCase();
    if (!sym) return;

    if (!sym.includes("/")) {
      if (sym.endsWith("USDT")) {
        sym = sym.replace("USDT", "/USDT");
      } else {
        sym = `${sym}/USDT`;
      }
    }

    if (!this.watchlist.includes(sym)) {
      this.watchlist.push(sym);
      this.saveWatchlist();
      this.addInput.value = "";
      this.fetchTickers();
    }
  }

  removePair(sym) {
    this.watchlist = this.watchlist.filter((s) => s !== sym);
    this.saveWatchlist();
    this.render();
  }

  async fetchTickers() {
    try {
      const res = await fetch("/api/ticker");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          data.forEach((t) => {
            const pair = t.pair || t.symbol;
            if (pair) this.tickersMap[pair] = t;
          });
        }
      }
      this.render();
    } catch (e) {}
  }

  render() {
    if (!this.tableBody) return;
    this.tableBody.innerHTML = "";

    if (this.watchlist.length === 0) {
      this.tableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:24px;" class="text-dim">Your watchlist is empty. Add symbols above.</td></tr>`;
      return;
    }

    this.watchlist.forEach((sym) => {
      const t = this.tickersMap[sym] || {};
      const price = parseFloat(t.last_price || 0);
      const change = parseFloat(t.price_change_percent || 0);
      const high = parseFloat(t.high_24h || 0);
      const low = parseFloat(t.low_24h || 0);
      const vol = parseFloat(t.quote_volume_24h || t.volume_24h || 0);

      const isUp = change >= 0;

      const formatP = (val) =>
        val ? `$${Number(val).toLocaleString(undefined, { minimumFractionDigits: val < 1 ? 4 : 2 })}` : "--";

      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";

      tr.innerHTML = `
        <td><strong class="text-bright">${sym}</strong></td>
        <td style="text-align:right;" class="font-mono font-bold">${formatP(price)}</td>
        <td style="text-align:right;" class="font-mono font-bold ${isUp ? "up-color" : "down-color"}">${isUp ? "+" : ""}${change.toFixed(2)}%</td>
        <td style="text-align:right;" class="font-mono text-dim">${formatP(high)}</td>
        <td style="text-align:right;" class="font-mono text-dim">${formatP(low)}</td>
        <td style="text-align:right;" class="font-mono text-dim">$${Number(vol).toLocaleString(undefined, { notation: "compact" })}</td>
        <td style="text-align:center;">
          <button type="button" class="btn-primary btn-wl-chart" style="padding:3px 8px; font-size:11px; margin-right:4px;">Chart</button>
          <button type="button" class="btn-secondary btn-wl-del" style="padding:3px 8px; font-size:11px; color:var(--red);">✕</button>
        </td>
      `;

      tr.querySelector(".btn-wl-chart").addEventListener("click", (e) => {
        e.stopPropagation();
        if (window.switchPair) window.switchPair(sym);
        if (window.navigateTo) window.navigateTo("section-chart");
      });

      tr.querySelector(".btn-wl-del").addEventListener("click", (e) => {
        e.stopPropagation();
        this.removePair(sym);
      });

      tr.addEventListener("click", () => {
        if (window.switchPair) window.switchPair(sym);
        if (window.navigateTo) window.navigateTo("section-chart");
      });

      this.tableBody.appendChild(tr);
    });
  }
}

window.watchlistModule = null;
