/**
 * AlphaQuant Pro - Interactive Vector Drawing Tools & "Analyze My Zone" AI Engine
 *
 * Tools:
 * - Trendlines & Rays
 * - Supply & Demand Zone Rectangles (Auto price conversion)
 * - Fibonacci Retracement Levels
 * - Long / Short Risk:Reward Position Tool
 * - Text Annotations
 * - "Analyze My Zone" Gemini AI integration on manual Supply/Demand zones
 */

class DrawingToolsManager {
  constructor() {
    this.svg = document.getElementById("drawing-canvas-overlay");
    this.activeTool = "cursor"; // cursor, trendline, ray, rectangle, demand_zone, supply_zone, fibonacci, position, text
    this.drawings = [];
    this.startPoint = null;
    this.currentDrawing = null;
    this.isDrawing = false;
    this.selectedDrawingId = null;

    this.init();
  }

  init() {
    if (!this.svg) return;

    // Attach button listeners
    document.querySelectorAll(".tv-tool-btn, .draw-tool-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tool = btn.getAttribute("data-tool");
        if (tool === "clear") {
          this.clearAll();
          return;
        }
        if (tool === "save") {
          this.saveDrawings();
          return;
        }
        this.setTool(tool);
      });
    });

    // Mouse events on SVG overlay
    this.svg.addEventListener("mousedown", (e) => this.onMouseDown(e));
    this.svg.addEventListener("mousemove", (e) => this.onMouseMove(e));
    this.svg.addEventListener("mouseup", (e) => this.onMouseUp(e));

    // Load saved drawings
    this.loadDrawings();
  }

  setTool(toolName) {
    this.activeTool = toolName;
    document.querySelectorAll(".tv-tool-btn, .draw-tool-btn").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-tool") === toolName);
    });

    if (toolName === "cursor") {
      this.svg.classList.remove("drawing-active");
    } else {
      this.svg.classList.add("drawing-active");
    }
  }

  getSvgCoords(e) {
    const rect = this.svg.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    };
  }

  onMouseDown(e) {
    if (this.activeTool === "cursor") {
      // Check if user clicked on an existing zone element
      const target = e.target;
      const group = target.closest("g[data-id]");
      if (group) {
        const id = group.getAttribute("data-id");
        this.selectDrawing(id, e);
      } else {
        this.hideZoneInspector();
      }
      return;
    }

    this.isDrawing = true;
    this.startPoint = this.getSvgCoords(e);

    if (this.activeTool === "text") {
      const text = prompt("Enter chart annotation text:", "SMC Key Level");
      if (text) {
        this.addTextAnnotation(this.startPoint.x, this.startPoint.y, text);
      }
      this.isDrawing = false;
      this.setTool("cursor");
      return;
    }

    if (this.activeTool === "ray" || this.activeTool === "hline") {
      this.addHorizontalRay(this.startPoint.y);
      this.isDrawing = false;
      this.setTool("cursor");
      return;
    }

    if (this.activeTool === "vline") {
      this.addVerticalLine(this.startPoint.x);
      this.isDrawing = false;
      this.setTool("cursor");
      return;
    }
  }

  onMouseMove(e) {
    if (!this.isDrawing || !this.startPoint) return;
    const current = this.getSvgCoords(e);
    this.renderPreview(this.startPoint, current);
  }

  onMouseUp(e) {
    if (!this.isDrawing || !this.startPoint) return;
    const endPoint = this.getSvgCoords(e);
    this.finalizeDrawing(this.startPoint, endPoint);
    this.isDrawing = false;
    this.startPoint = null;
    this.clearPreview();
    this.setTool("cursor");
  }

  renderPreview(p1, p2) {
    this.clearPreview();
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.id = "draw-preview-group";

    if (this.activeTool === "trendline") {
      const line = this.createSvgElement("line", {
        x1: p1.x,
        y1: p1.y,
        x2: p2.x,
        y2: p2.y,
        stroke: "#3b82f6",
        "stroke-width": 2,
        "stroke-dasharray": "4",
      });
      g.appendChild(line);
    } else if (
      this.activeTool === "rectangle" ||
      this.activeTool === "demand_zone" ||
      this.activeTool === "supply_zone"
    ) {
      const x = Math.min(p1.x, p2.x);
      const y = Math.min(p1.y, p2.y);
      const w = Math.abs(p2.x - p1.x);
      const h = Math.abs(p2.y - p1.y);
      const isDemand = this.activeTool === "demand_zone" || (this.activeTool === "rectangle" && p2.y > p1.y);

      const rect = this.createSvgElement("rect", {
        x: x,
        y: y,
        width: w,
        height: h,
        fill: isDemand ? "rgba(16, 185, 129, 0.2)" : "rgba(239, 68, 68, 0.2)",
        stroke: isDemand ? "#10b981" : "#ef4444",
        "stroke-width": 1.5,
      });
      g.appendChild(rect);
    } else if (this.activeTool === "fibonacci") {
      this.renderFibonacci(g, p1, p2, true);
    } else if (this.activeTool === "position") {
      this.renderPositionTool(g, p1, p2, true);
    }

    this.svg.appendChild(g);
  }

  clearPreview() {
    const prev = document.getElementById("draw-preview-group");
    if (prev) prev.remove();
  }

  finalizeDrawing(p1, p2) {
    let tool = this.activeTool;
    let zoneType = "NEUTRAL";

    if (tool === "demand_zone" || (tool === "rectangle" && p2.y > p1.y)) {
      zoneType = "DEMAND";
    } else if (tool === "supply_zone" || (tool === "rectangle" && p2.y <= p1.y)) {
      zoneType = "SUPPLY";
    }

    // Convert pixel Y coordinates to exact market prices
    const prices = this.convertYToPrices(p1.y, p2.y);
    let t1 = null, t2 = null, price1 = null, price2 = null;

    if (window.chartManager && window.chartManager.charts.length > 0) {
      const c = window.chartManager.charts[0];
      if (c && c.chart && c.candleSeries) {
        try {
          if (typeof c.chart.timeScale().coordinateToTime === "function") {
            t1 = c.chart.timeScale().coordinateToTime(p1.x);
            t2 = c.chart.timeScale().coordinateToTime(p2.x);
          }
          if (typeof c.candleSeries.coordinateToPrice === "function") {
            price1 = c.candleSeries.coordinateToPrice(p1.y);
            price2 = c.candleSeries.coordinateToPrice(p2.y);
          }
        } catch (err) {}
      }
    }

    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: tool,
      zoneType: zoneType,
      p1: { x: p1.x, y: p1.y },
      p2: { x: p2.x, y: p2.y },
      t1: t1,
      t2: t2,
      price1: price1,
      price2: price2,
      priceTop: prices.top,
      priceBottom: prices.bottom,
    };

    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();

    // If it's a zone, immediately offer AI analysis!
    if (drawingObj.zoneType === "DEMAND" || drawingObj.zoneType === "SUPPLY") {
      this.showZoneInspector(drawingObj, (p1.x + p2.x) / 2, Math.min(p1.y, p2.y));
    }
  }

  reprojectDrawings() {
    if (!window.chartManager || window.chartManager.charts.length === 0) return;
    const c = window.chartManager.charts[0];
    if (!c || !c.chart || !c.candleSeries) return;

    const timeScale = c.chart.timeScale();
    const candleSeries = c.candleSeries;

    this.drawings.forEach((d) => {
      try {
        if (d.t1 !== null && d.t1 !== undefined && typeof timeScale.timeToCoordinate === "function") {
          const x1 = timeScale.timeToCoordinate(d.t1);
          if (x1 !== null && !isNaN(x1)) d.p1.x = x1;
        }
        if (d.t2 !== null && d.t2 !== undefined && typeof timeScale.timeToCoordinate === "function") {
          const x2 = timeScale.timeToCoordinate(d.t2);
          if (x2 !== null && !isNaN(x2)) d.p2.x = x2;
        }
        if (d.price1 !== null && d.price1 !== undefined && typeof candleSeries.priceToCoordinate === "function") {
          const y1 = candleSeries.priceToCoordinate(d.price1);
          if (y1 !== null && !isNaN(y1)) d.p1.y = y1;
        }
        if (d.price2 !== null && d.price2 !== undefined && typeof candleSeries.priceToCoordinate === "function") {
          const y2 = candleSeries.priceToCoordinate(d.price2);
          if (y2 !== null && !isNaN(y2)) d.p2.y = y2;
        }
      } catch (err) {}
    });

    this.svg.querySelectorAll("g[data-id]").forEach((el) => el.remove());
    this.drawings.forEach((d) => this.renderPermanent(d));
  }

  convertYToPrices(y1, y2) {
    if (window.chartManager && window.chartManager.charts.length > 0) {
      const c = window.chartManager.charts[0];
      if (c && c.candleSeries && typeof c.candleSeries.coordinateToPrice === "function") {
        const pr1 = c.candleSeries.coordinateToPrice(y1);
        const pr2 = c.candleSeries.coordinateToPrice(y2);
        if (pr1 !== null && pr2 !== null) {
          return {
            top: Math.max(pr1, pr2),
            bottom: Math.min(pr1, pr2),
          };
        }
      }
    }

    // Fallback based on current price
    const cur = window.appState ? window.appState.currentPrice : 65000;
    const dy1 = (y1 - 150) * 10;
    const dy2 = (y2 - 150) * 10;
    return {
      top: Math.max(cur - dy1, cur - dy2),
      bottom: Math.min(cur - dy1, cur - dy2),
    };
  }

  renderPermanent(d) {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("data-id", d.id);
    g.style.cursor = "pointer";

    if (d.tool === "trendline") {
      const line = this.createSvgElement("line", {
        x1: d.p1.x,
        y1: d.p1.y,
        x2: d.p2.x,
        y2: d.p2.y,
        stroke: "#60a5fa",
        "stroke-width": 2,
      });
      g.appendChild(line);
    } else if (
      d.tool === "rectangle" ||
      d.tool === "demand_zone" ||
      d.tool === "supply_zone"
    ) {
      const x = Math.min(d.p1.x, d.p2.x);
      const y = Math.min(d.p1.y, d.p2.y);
      const w = Math.abs(d.p2.x - d.p1.x);
      const h = Math.abs(d.p2.y - d.p1.y);
      const isDemand = d.zoneType === "DEMAND";

      const rect = this.createSvgElement("rect", {
        x: x,
        y: y,
        width: w,
        height: h,
        fill: isDemand ? "rgba(16, 185, 129, 0.22)" : "rgba(239, 68, 68, 0.22)",
        stroke: isDemand ? "#10b981" : "#ef4444",
        "stroke-width": 1.5,
      });

      // Price Tag Label
      const label = this.createSvgElement("text", {
        x: x + 6,
        y: y + 14,
        fill: isDemand ? "#10b981" : "#ef4444",
        "font-size": "10px",
        "font-family": "monospace",
        "font-weight": "bold",
      });
      label.textContent = `${d.zoneType} ($${d.priceBottom ? d.priceBottom.toFixed(1) : ""} - $${d.priceTop ? d.priceTop.toFixed(1) : ""})`;

      g.appendChild(rect);
      g.appendChild(label);
    } else if (d.tool === "fibonacci") {
      this.renderFibonacci(g, d.p1, d.p2, false);
    } else if (d.tool === "position") {
      this.renderPositionTool(g, d.p1, d.p2, false);
    } else if (d.tool === "ray") {
      const line = this.createSvgElement("line", {
        x1: 0,
        y1: d.y,
        x2: "100%",
        y2: d.y,
        stroke: "#f59e0b",
        "stroke-width": 1.5,
        "stroke-dasharray": "5,5",
      });
      g.appendChild(line);
    } else if (d.tool === "text") {
      const text = this.createSvgElement("text", {
        x: d.x,
        y: d.y,
        fill: "#f1f5f9",
        "font-size": "12px",
        "font-family": "monospace",
        "font-weight": "bold",
      });
      text.textContent = d.text;
      g.appendChild(text);
    }

    this.svg.appendChild(g);
  }

  renderFibonacci(g, p1, p2, isPreview) {
    const levels = [
      { r: 0.0, label: "0.0", color: "#94a3b8" },
      { r: 0.382, label: "0.382", color: "#38bdf8" },
      { r: 0.5, label: "0.5 (EQ)", color: "#f59e0b" },
      { r: 0.618, label: "0.618 (OTE)", color: "#10b981" },
      { r: 0.786, label: "0.786 (OTE)", color: "#10b981" },
      { r: 1.0, label: "1.0", color: "#94a3b8" },
    ];
    const dy = p2.y - p1.y;

    levels.forEach((lvl) => {
      const y = p1.y + dy * lvl.r;
      const line = this.createSvgElement("line", {
        x1: Math.min(p1.x, p2.x),
        y1: y,
        x2: Math.max(p1.x, p2.x) + 120,
        y2: y,
        stroke: lvl.color,
        "stroke-width": 1,
        "stroke-dasharray": isPreview ? "3,3" : "none",
      });
      const text = this.createSvgElement("text", {
        x: Math.max(p1.x, p2.x) + 5,
        y: y - 2,
        fill: lvl.color,
        "font-size": "10px",
        "font-family": "monospace",
      });
      text.textContent = lvl.label;
      g.appendChild(line);
      g.appendChild(text);
    });
  }

  renderPositionTool(g, p1, p2, isPreview) {
    const midY = (p1.y + p2.y) / 2;
    const x = Math.min(p1.x, p2.x);
    const w = Math.max(Math.abs(p2.x - p1.x), 90);

    const tpBox = this.createSvgElement("rect", {
      x: x,
      y: Math.min(p1.y, midY),
      width: w,
      height: Math.abs(midY - p1.y),
      fill: "rgba(16, 185, 129, 0.25)",
      stroke: "#10b981",
      "stroke-width": 1,
    });

    const slBox = this.createSvgElement("rect", {
      x: x,
      y: Math.min(midY, p2.y),
      width: w,
      height: Math.abs(p2.y - midY),
      fill: "rgba(239, 68, 68, 0.25)",
      stroke: "#ef4444",
      "stroke-width": 1,
    });

    const label = this.createSvgElement("text", {
      x: x + 6,
      y: midY - 4,
      fill: "#f1f5f9",
      "font-size": "11px",
      "font-family": "monospace",
      "font-weight": "bold",
    });
    label.textContent = "Risk:Reward 2.0 (Target / Stop)";

    g.appendChild(tpBox);
    g.appendChild(slBox);
    g.appendChild(label);
  }

  addHorizontalRay(y) {
    const prices = this.convertYToPrices(y, y);
    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: "ray",
      y: y,
      price: prices.top,
    };
    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();
  }

  addVerticalLine(x) {
    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: "vline",
      x: x,
    };
    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();
  }

  addTextAnnotation(x, y, text) {
    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: "text",
      x: x,
      y: y,
      text: text,
    };
    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();
  }

  selectDrawing(id, e) {
    const d = this.drawings.find((item) => item.id === id);
    if (!d) return;

    if (d.zoneType === "DEMAND" || d.zoneType === "SUPPLY") {
      const coords = this.getSvgCoords(e);
      this.showZoneInspector(d, coords.x, coords.y);
    }
  }

  showZoneInspector(zoneObj, x, y) {
    this.hideZoneInspector();

    const inspector = document.createElement("div");
    inspector.id = "zone-ai-inspector-badge";
    inspector.className = "zone-inspector-popup";
    inspector.style.left = `${Math.max(20, x - 100)}px`;
    inspector.style.top = `${Math.max(20, y - 45)}px`;

    const proximal = zoneObj.zoneType === "DEMAND" ? zoneObj.priceTop : zoneObj.priceBottom;
    const distal = zoneObj.zoneType === "DEMAND" ? zoneObj.priceBottom : zoneObj.priceTop;

    inspector.innerHTML = `
      <div class="inspector-header">
        <span class="inspector-tag tag-${zoneObj.zoneType.toLowerCase()}">${zoneObj.zoneType} ZONE</span>
        <span class="inspector-prices font-mono">$${distal.toFixed(1)} - $${proximal.toFixed(1)}</span>
      </div>
      <button id="btn-trigger-ai-zone" class="btn-analyze-zone-ai">
        ⚡ Analyze Zone with Gemini AI
      </button>
    `;

    const parent = document.querySelector(".tv-main-chart-box") || document.body;
    parent.appendChild(inspector);

    document.getElementById("btn-trigger-ai-zone").addEventListener("click", () => {
      this.executeGeminiZoneAnalysis(zoneObj);
    });
  }

  hideZoneInspector() {
    const el = document.getElementById("zone-ai-inspector-badge");
    if (el) el.remove();
  }

  async executeGeminiZoneAnalysis(zoneObj) {
    const proximal = zoneObj.zoneType === "DEMAND" ? zoneObj.priceTop : zoneObj.priceBottom;
    const distal = zoneObj.zoneType === "DEMAND" ? zoneObj.priceBottom : zoneObj.priceTop;
    const pair = window.appState ? window.appState.pair : "BTC/USDT";
    const tf = window.appState ? window.appState.interval : "15m";

    this.hideZoneInspector();

    // Switch right dock to Gemini AI panel
    const aiTrayBtn = document.querySelector(".tv-tray-icon[data-page='page-ai']");
    if (aiTrayBtn) aiTrayBtn.click();

    const chatContainer = document.getElementById("chat-messages-container");
    if (chatContainer) {
      const loadingDiv = document.createElement("div");
      loadingDiv.className = "message-bubble ai";
      loadingDiv.innerHTML = `<em>🤖 Gemini AI is evaluating your manual <strong>${zoneObj.zoneType} Zone</strong> ($${distal.toFixed(2)} - $${proximal.toFixed(2)})...</em>`;
      chatContainer.appendChild(loadingDiv);
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    try {
      const res = await fetch("/api/analyze-zone", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pair: pair,
          timeframe: tf,
          zone_type: zoneObj.zoneType,
          proximal: proximal,
          distal: distal,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        if (chatContainer) {
          const aiDiv = document.createElement("div");
          aiDiv.className = "message-bubble ai";
          const formattedText = marked ? marked.parse(data.ai_analysis) : data.ai_analysis;
          aiDiv.innerHTML = `
            <div style="background:rgba(41,98,255,0.08); border:1px solid rgba(41,98,255,0.3); padding:10px; border-radius:6px;">
              <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                <span class="badge badge-${data.classification.includes('VALID') ? 'emerald' : data.classification.includes('WEAK') ? 'amber' : 'rose'}">
                  ${data.classification} (${data.quality_score}/100 Score)
                </span>
                <span class="font-mono fs-xs text-dim">${data.lifecycle_state}</span>
              </div>
              <div>${formattedText}</div>
            </div>
          `;
          chatContainer.appendChild(aiDiv);
          chatContainer.scrollTop = chatContainer.scrollHeight;
        }
      }
    } catch (err) {
      console.error("Zone analysis failed:", err);
    }
  }

  createSvgElement(type, attrs) {
    const elem = document.createElementNS("http://www.w3.org/2000/svg", type);
    for (const [k, v] of Object.entries(attrs)) {
      elem.setAttribute(k, v);
    }
    return elem;
  }

  clearAll() {
    if (confirm("Clear all drawings on chart?")) {
      this.drawings = [];
      this.svg.innerHTML = "";
      this.hideZoneInspector();
      this.saveDrawings();
    }
  }

  async saveDrawings() {
    const pair = window.appState ? window.appState.pair : "BTC/USDT";
    const tf = window.appState ? window.appState.interval : "15m";

    try {
      localStorage.setItem(`drawings_${pair}_${tf}`, JSON.stringify(this.drawings));
      await fetch("/api/drawings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair: pair, timeframe: tf, drawings: this.drawings }),
      });
    } catch (e) {
      console.debug("Failed saving drawings:", e);
    }
  }

  async loadDrawings() {
    const pair = window.appState ? window.appState.pair : "BTC/USDT";
    const tf = window.appState ? window.appState.interval : "15m";

    this.svg.innerHTML = "";
    try {
      const res = await fetch(`/api/drawings?pair=${encodeURIComponent(pair)}&timeframe=${tf}`);
      if (res.ok) {
        const data = await res.json();
        this.drawings = data.drawings || [];
        this.drawings.forEach((d) => this.renderPermanent(d));
      }
    } catch (e) {
      const local = localStorage.getItem(`drawings_${pair}_${tf}`);
      if (local) {
        this.drawings = JSON.parse(local);
        this.drawings.forEach((d) => this.renderPermanent(d));
      }
    }
  }
}

window.drawingTools = null;
