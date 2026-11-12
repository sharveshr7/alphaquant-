/**
 * AlphaQuant Pro - Performance Analytics Module
 *
 * Capabilities:
 * - Fetches historical model metrics from /api/signal/performance
 * - Renders summary statistics (Win Rate, Profit Factor, Max Drawdown, Sharpe)
 * - Renders per-coin breakdown table with chart navigation
 * - Renders per-timeframe expectancy breakdown
 */

class PerformanceModule {
  constructor() {
    this.winrateEl = document.getElementById("perf-winrate");
    this.pfEl = document.getElementById("perf-profit-factor");
    this.ddEl = document.getElementById("perf-max-dd");
    this.sharpeEl = document.getElementById("perf-sharpe");

    this.coinTbody = document.getElementById("perf-coin-tbody");
    this.tfTbody = document.getElementById("perf-tf-tbody");

    this.init();
  }

  init() {
    this.fetchPerformance();
  }

  async fetchPerformance() {
    try {
      let data = null;
      const res = await fetch("/api/signal/performance");
      if (res.ok) {
        data = await res.json();
      }

      if (!data) {
        data = this.getDefaultPerformance();
      }

      this.render(data);
    } catch (e) {
      console.warn("Performance fetch error, using defaults:", e);
      this.render(this.getDefaultPerformance());
    }
  }

  getDefaultPerformance() {
    return {
      overall: {
        winrate: "0.0%",
        profit_factor: "N/A",
        max_drawdown: "0.0%",
        sharpe_ratio: "N/A",
      },
      by_coin: [],
      by_timeframe: [],
    };
  }

  render(d) {
    if (!d) return;

    if (d.overall) {
      if (this.winrateEl) this.winrateEl.textContent = d.overall.winrate || "--%";
      if (this.pfEl) this.pfEl.textContent = d.overall.profit_factor || "--";
      if (this.ddEl) this.ddEl.textContent = d.overall.max_drawdown || "--%";
      if (this.sharpeEl) this.sharpeEl.textContent = d.overall.sharpe_ratio || "--";
    }

    if (this.coinTbody && d.by_coin) {
      this.coinTbody.innerHTML = "";
      d.by_coin.forEach((c) => {
        const tr = document.createElement("tr");
        tr.style.cursor = "pointer";
        tr.innerHTML = `
          <td><strong class="text-bright">${c.symbol}</strong></td>
          <td class="font-mono">${c.trades}</td>
          <td class="font-mono up-color font-bold">${c.winrate}</td>
          <td class="font-mono text-blue">${c.pf}</td>
          <td class="font-mono">${c.rr}</td>
          <td class="font-mono up-color font-bold">${c.net_pnl}</td>
        `;

        tr.addEventListener("click", () => {
          if (window.switchPair) window.switchPair(c.symbol);
          if (window.navigateTo) window.navigateTo("section-chart");
        });

        this.coinTbody.appendChild(tr);
      });
    }

    if (this.tfTbody && d.by_timeframe) {
      this.tfTbody.innerHTML = "";
      d.by_timeframe.forEach((tf) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong class="text-bright">${tf.timeframe}</strong></td>
          <td class="text-dim fs-xs">${tf.frequency}</td>
          <td class="font-mono up-color font-bold">${tf.winrate}</td>
          <td class="font-mono text-blue font-bold">${tf.expectancy}</td>
        `;
        this.tfTbody.appendChild(tr);
      });
    }
  }
}

window.performanceModule = null;
