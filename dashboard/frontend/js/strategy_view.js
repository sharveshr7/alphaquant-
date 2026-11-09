/**
 * Strategy View Controller - SupplyDemandConfluence Signals & Zones
 */

class StrategyView {
  constructor() {
    this.checklistContainer = document.getElementById('checklist-container');
    this.zonesContainer = document.getElementById('zones-container');
    this.scoreText = document.getElementById('strategy-score-text');
    this.signalBadge = document.getElementById('strategy-signal-badge');
    this.actionText = document.getElementById('strategy-action-text');

    this.entryElem = document.getElementById('setup-entry');
    this.slElem = document.getElementById('setup-sl');
    this.tp1Elem = document.getElementById('setup-tp1');
    this.tp2Elem = document.getElementById('setup-tp2');
  }

  updateConfluence(data) {
    if (!data) return;

    // Confluence Score & Signal
    const score = data.confluence_score ?? 0;
    const maxScore = data.max_score ?? 8;
    this.scoreText.textContent = `${score} / ${maxScore}`;

    const isHigh = score >= 6;
    const isMedium = score >= 4;

    this.scoreText.className = `score-num ${isHigh ? 'price-up' : isMedium ? '' : 'price-down'}`;
    this.signalBadge.textContent = data.signal || 'NEUTRAL';
    this.signalBadge.className = `ticker-badge ${isHigh ? 'bg-up' : isMedium ? '' : 'bg-down'}`;
    this.actionText.textContent = data.action || 'Evaluating conditions...';

    // Checklist Breakdown
    if (data.checklist && this.checklistContainer) {
      this.checklistContainer.innerHTML = '';
      data.checklist.forEach(item => {
        const row = document.createElement('div');
        row.className = 'check-item';
        row.innerHTML = `
          <span class="check-icon">${item.passed ? '✅' : '❌'}</span>
          <div style="flex:1;">
            <div class="check-title" style="color: ${item.passed ? '#34d399' : '#94a3b8'};">${item.title}</div>
            <div class="check-detail">${item.detail}</div>
          </div>
        `;
        this.checklistContainer.appendChild(row);
      });
    }

    // Active Zones
    if (data.zones && this.zonesContainer) {
      this.zonesContainer.innerHTML = '';
      const demands = data.zones.demand || [];
      const supplies = data.zones.supply || [];

      if (demands.length === 0 && supplies.length === 0) {
        this.zonesContainer.innerHTML = '<p style="font-size:0.8rem; color:var(--text-dim);">No active unmitigated zones in recent candles.</p>';
      } else {
        demands.forEach(d => {
          const div = document.createElement('div');
          div.className = 'zone-item zone-demand';
          div.innerHTML = `
            <span>🟢 Demand Zone</span>
            <span>$${d.zone_bottom.toLocaleString()} - $${d.zone_top.toLocaleString()} (${d.distance_pct}%)</span>
          `;
          this.zonesContainer.appendChild(div);
        });

        supplies.forEach(s => {
          const div = document.createElement('div');
          div.className = 'zone-item zone-supply';
          div.innerHTML = `
            <span>🔴 Supply Zone</span>
            <span>$${s.zone_bottom.toLocaleString()} - $${s.zone_top.toLocaleString()} (${s.distance_pct}%)</span>
          `;
          this.zonesContainer.appendChild(div);
        });
      }
    }

    // Trade Setup
    if (data.trade_setup) {
      const s = data.trade_setup;
      if (this.entryElem) this.entryElem.textContent = `$${Number(s.entry).toLocaleString()}`;
      if (this.slElem) this.slElem.textContent = `$${Number(s.stop_loss).toLocaleString()} (-3.5%)`;
      if (this.tp1Elem) this.tp1Elem.textContent = `$${Number(s.tp1).toLocaleString()} (+5.2%)`;
      if (this.tp2Elem) this.tp2Elem.textContent = `$${Number(s.tp2).toLocaleString()} (+8.7%)`;
    }
  }
}

window.strategyView = null;
