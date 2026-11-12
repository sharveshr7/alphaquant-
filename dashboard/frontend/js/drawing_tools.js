/**
 * AlphaQuant AI Pro - Complete Vector Drawing Tools & Anchor Engine
 *
 * All drawings anchor to exact (time, price) coordinates so they track
 * candlesticks cleanly during zoom, pan, resize, and reload.
 * Supports: Trendline, Horizontal Line, Vertical Line, Ray, Rectangle, Fibonacci Retracement,
 * Long Position R:R, Short Position R:R, Text Annotation, Manual Demand Zone, Manual Supply Zone.
 * Allows selecting, dragging/moving, editing text, deleting individual drawings, and persistence.
 */

class DrawingToolsManager {
  constructor() {
    this.svg = document.getElementById("drawing-canvas-overlay");
    this.activeTool = "cursor";
    this.drawings = [];
    this.startPoint = null;
    this.isDrawing = false;
    this.selectedDrawingId = null;
    this.dragging = null;
    this._loadSeq = 0;
    this._abortController = null;

    this.init();
  }

  init() {
    if (!this.svg) return;

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

    this.svg.addEventListener("mousedown", (e) => this.onMouseDown(e));
    window.addEventListener("mousemove", (e) => this.onMouseMove(e));
    window.addEventListener("mouseup", (e) => this.onMouseUp(e));

    // Delete selected drawing via keyboard Delete / Backspace (unless typing in an input)
    window.addEventListener("keydown", (e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && this.selectedDrawingId) {
        const tag = (document.activeElement && document.activeElement.tagName) || "";
        if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
          this.deleteDrawing(this.selectedDrawingId);
        }
      } else if (e.key === "Escape") {
        this.setTool("cursor");
        this.deselectAll();
      }
    });

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
      this.deselectAll();
    }
  }

  getSvgCoords(e) {
    const rect = this.svg.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(rect.width, e.clientX - rect.left)),
      y: Math.max(0, Math.min(rect.height, e.clientY - rect.top)),
    };
  }

  coordToAnchor(x, y) {
    let t = null;
    let price = null;
    if (window.chartManager && window.chartManager.charts.length > 0) {
      const c = window.chartManager.charts[0];
      if (c && c.chart && c.candleSeries) {
        try {
          const ts = c.chart.timeScale();
          t = ts.coordinateToTime(x);
          // Extrapolate time if clicked beyond right/left candles or between candles
          if ((t === null || t === undefined) && c.allCandles && c.allCandles.length > 0) {
            const logical = ts.coordinateToLogical(x);
            const lastIdx = c.allCandles.length - 1;
            const step = c.allCandles.length >= 2 ? c.allCandles[lastIdx].time - c.allCandles[lastIdx - 1].time : 60;
            if (logical !== null && Number.isFinite(logical)) {
              if (logical >= 0 && logical <= lastIdx) {
                t = c.allCandles[Math.round(logical)].time;
              } else if (logical > lastIdx) {
                t = Math.round(c.allCandles[lastIdx].time + (logical - lastIdx) * (step > 0 ? step : 60));
              } else {
                t = Math.round(c.allCandles[0].time + logical * (step > 0 ? step : 60));
              }
            } else {
              t = c.allCandles[lastIdx].time;
            }
          }
          price = c.candleSeries.coordinateToPrice(y);
        } catch (err) {}
      }
    }
    if (t === null || !Number.isFinite(Number(t))) {
      t = Math.floor(Date.now() / 1000);
    }
    return { t, price };
  }

  getViewportBounds() {
    const w = (this.svg && this.svg.clientWidth) || 1200;
    const h = (this.svg && this.svg.clientHeight) || 600;
    return { w, h };
  }

  clampCoord(v, min, max, fallback = 0) {
    const n = Number(v);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(min, Math.min(max, n));
  }

  isDrawingValidForActiveChart(d) {
    if (!d) return false;
    if (!(window.chartManager && window.chartManager.charts.length > 0)) return true;
    const c = window.chartManager.charts[0];
    if (!c || !c.allCandles || c.allCandles.length === 0) return true;
    const ref = c.allCandles[c.allCandles.length - 1].close;
    if (!Number.isFinite(ref) || ref <= 0) return true;

    const prices = [d.price1, d.price2, d.price, d.priceTop, d.priceBottom].filter((p) => p !== null && p !== undefined && Number.isFinite(Number(p)));
    for (let i = 0; i < prices.length; i++) {
      const p = Number(prices[i]);
      if (p <= 0 || p > ref * 2.5 || p < ref * 0.4) return false;
    }
    return true;
  }

  anchorToCoord(t, price, fallbackX, fallbackY) {
    const { w, h } = this.getViewportBounds();
    let x = this.clampCoord(fallbackX, -w, w * 2, 0);
    let y = this.clampCoord(fallbackY, -h, h * 2, h * 0.5);
    if (window.chartManager && window.chartManager.charts.length > 0) {
      const c = window.chartManager.charts[0];
      if (c && c.chart && c.candleSeries) {
        try {
          if (t !== null && t !== undefined && Number.isFinite(Number(t))) {
            const numT = Number(t);
            let cx = c.chart.timeScale().timeToCoordinate(numT);
            if ((cx === null || !Number.isFinite(cx)) && c.allCandles && c.allCandles.length >= 2) {
              const lastIdx = c.allCandles.length - 1;
              const step = c.allCandles[lastIdx].time - c.allCandles[lastIdx - 1].time;
              if (step > 0) {
                const approxLogical = lastIdx + (numT - c.allCandles[lastIdx].time) / step;
                cx = c.chart.timeScale().logicalToCoordinate(approxLogical);
              }
            }
            if (cx !== null && Number.isFinite(cx)) {
              x = this.clampCoord(cx, -w, w * 2, x);
            }
          }
          if (price !== null && price !== undefined && Number.isFinite(Number(price))) {
            const cy = c.candleSeries.priceToCoordinate(Number(price));
            if (cy !== null && Number.isFinite(cy)) {
              y = this.clampCoord(cy, -h, h * 2, y);
            }
          }
        } catch (err) {}
      }
    }
    return { x, y };
  }

  formatPrice(val) {
    if (val === null || val === undefined || isNaN(val)) return "--";
    const aval = Math.abs(val);
    const dec = aval < 0.01 ? 6 : aval < 1 ? 5 : aval < 50 ? 4 : 2;
    return `$${val.toFixed(dec)}`;
  }

  onMouseDown(e) {
    if (this.activeTool === "cursor") {
      const group = e.target.closest("g[data-id]");
      if (group) {
        e.stopPropagation();
        const id = group.getAttribute("data-id");
        this.selectDrawing(id, e);
        const pt = this.getSvgCoords(e);
        const d = this.drawings.find((item) => item.id === id);
        if (d) {
          this.dragging = {
            id,
            startX: pt.x,
            startY: pt.y,
            origP1: d.p1 ? { ...d.p1 } : { x: d.x || 0, y: d.y || 0 },
            origP2: d.p2 ? { ...d.p2 } : null,
            moved: false,
          };
        }
      } else {
        this.deselectAll();
      }
      return;
    }

    this.isDrawing = true;
    this.startPoint = this.getSvgCoords(e);

    if (this.activeTool === "text") {
      const text = prompt("Enter chart annotation text:", "Key Level");
      if (text && text.trim()) {
        this.addTextAnnotation(this.startPoint.x, this.startPoint.y, text.trim());
      }
      this.isDrawing = false;
      this.setTool("cursor");
      return;
    }

    if (this.activeTool === "hline") {
      this.addHorizontalLine(this.startPoint.x, this.startPoint.y);
      this.isDrawing = false;
      this.setTool("cursor");
      return;
    }

    if (this.activeTool === "vline") {
      this.addVerticalLine(this.startPoint.x, this.startPoint.y);
      this.isDrawing = false;
      this.setTool("cursor");
      return;
    }
  }

  onMouseMove(e) {
    if (this.dragging) {
      const pt = this.getSvgCoords(e);
      const dx = pt.x - this.dragging.startX;
      const dy = pt.y - this.dragging.startY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) this.dragging.moved = true;
      const d = this.drawings.find((item) => item.id === this.dragging.id);
      if (d) {
        if (d.p1) {
          d.p1.x = this.dragging.origP1.x + dx;
          d.p1.y = this.dragging.origP1.y + dy;
          if (d.p2 && this.dragging.origP2) {
            d.p2.x = this.dragging.origP2.x + dx;
            d.p2.y = this.dragging.origP2.y + dy;
          }
        } else {
          d.x = this.dragging.origP1.x + dx;
          d.y = this.dragging.origP1.y + dy;
        }
        this.redrawAll();
      }
      return;
    }

    if (!this.isDrawing || !this.startPoint) return;
    const current = this.getSvgCoords(e);
    this.renderPreview(this.startPoint, current);
  }

  onMouseUp(e) {
    if (this.dragging) {
      const dragInfo = this.dragging;
      this.dragging = null;
      if (dragInfo.moved) {
        const d = this.drawings.find((item) => item.id === dragInfo.id);
        if (d) {
          if (d.p1) {
            const a1 = this.coordToAnchor(d.p1.x, d.p1.y);
            d.t1 = a1.t;
            d.price1 = a1.price;
            if (d.p2) {
              const a2 = this.coordToAnchor(d.p2.x, d.p2.y);
              d.t2 = a2.t;
              d.price2 = a2.price;
              const pr = this.convertYToPrices(d.p1.y, d.p2.y);
              d.priceTop = pr.top;
              d.priceBottom = pr.bottom;
            }
          } else {
            const a = this.coordToAnchor(d.x || 0, d.y || 0);
            d.t1 = a.t;
            d.price1 = a.price;
          }
          this.saveDrawings();
        }
      }
      return;
    }

    if (!this.isDrawing || !this.startPoint) return;
    const endPoint = this.getSvgCoords(e);
    if (Math.hypot(endPoint.x - this.startPoint.x, endPoint.y - this.startPoint.y) >= 4) {
      this.finalizeDrawing(this.startPoint, endPoint);
    }
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
      g.appendChild(this.createSvgElement("line", {
        x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, stroke: "#3b82f6", "stroke-width": 2, "stroke-dasharray": "4",
      }));
    } else if (this.activeTool === "ray") {
      const ext = this.computeRayEndpoint(p1, p2);
      g.appendChild(this.createSvgElement("line", {
        x1: p1.x, y1: p1.y, x2: ext.x, y2: ext.y, stroke: "#f59e0b", "stroke-width": 2, "stroke-dasharray": "4",
      }));
    } else if (
      this.activeTool === "rectangle" ||
      this.activeTool === "demand_zone" ||
      this.activeTool === "supply_zone"
    ) {
      const x = Math.min(p1.x, p2.x);
      const y = Math.min(p1.y, p2.y);
      const w = Math.abs(p2.x - p1.x);
      const h = Math.abs(p2.y - p1.y);
      const isDemand = this.activeTool === "demand_zone";
      const isSupply = this.activeTool === "supply_zone";
      const fill = isDemand ? "rgba(16, 185, 129, 0.2)" : isSupply ? "rgba(239, 68, 68, 0.2)" : "rgba(59, 130, 246, 0.16)";
      const stroke = isDemand ? "#10b981" : isSupply ? "#ef4444" : "#3b82f6";

      g.appendChild(this.createSvgElement("rect", { x, y, width: w, height: h, fill, stroke, "stroke-width": 1.5 }));
    } else if (this.activeTool === "fibonacci") {
      this.renderFibonacci(g, p1, p2, true);
    } else if (this.activeTool === "position_long" || this.activeTool === "position_short") {
      this.renderPositionTool(g, p1, p2, this.activeTool === "position_long", true);
    }

    this.svg.appendChild(g);
  }

  clearPreview() {
    const prev = document.getElementById("draw-preview-group");
    if (prev) prev.remove();
  }

  computeRayEndpoint(p1, p2) {
    const { w, h } = this.getViewportBounds();
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    const maxW = w || 2400;
    const maxH = h || 1200;
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return { x: p1.x, y: p1.y };
    if (Math.abs(dx) < 0.001) {
      return { x: p1.x, y: dy >= 0 ? maxH : 0 };
    }
    const targetX = dx > 0 ? maxW : 0;
    const scale = (targetX - p1.x) / dx;
    const rawY = p1.y + dy * scale;
    return { x: targetX, y: this.clampCoord(rawY, -maxH, maxH * 2, p1.y) };
  }

  finalizeDrawing(p1, p2) {
    const tool = this.activeTool;
    let zoneType = "NEUTRAL";
    if (tool === "demand_zone") zoneType = "DEMAND";
    else if (tool === "supply_zone") zoneType = "SUPPLY";

    const a1 = this.coordToAnchor(p1.x, p1.y);
    const a2 = this.coordToAnchor(p2.x, p2.y);
    const prices = this.convertYToPrices(p1.y, p2.y);

    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: tool,
      zoneType: zoneType,
      p1: { x: p1.x, y: p1.y },
      p2: { x: p2.x, y: p2.y },
      t1: a1.t,
      t2: a2.t,
      price1: a1.price,
      price2: a2.price,
      priceTop: prices.top,
      priceBottom: prices.bottom,
    };

    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();

    if (drawingObj.zoneType === "DEMAND" || drawingObj.zoneType === "SUPPLY") {
      this.selectDrawing(drawingObj.id);
    }
  }

  reprojectDrawings() {
    if (!this.svg || this.drawings.length === 0) return;

    this.drawings.forEach((d) => {
      if (!this.isDrawingValidForActiveChart(d)) {
        d._skipRender = true;
        return;
      }
      if (d.t1 === null || d.t1 === undefined || !Number.isFinite(Number(d.t1))) {
        d._skipRender = true;
        return;
      }
      if (d.p2 && (d.t2 === null || d.t2 === undefined || !Number.isFinite(Number(d.t2)))) {
        d._skipRender = true;
        return;
      }
      d._skipRender = false;
      if (d.p1) {
        const c1 = this.anchorToCoord(d.t1, d.price1, d.p1.x, d.p1.y);
        d.p1.x = c1.x;
        d.p1.y = c1.y;
        if (d.p2) {
          const c2 = this.anchorToCoord(d.t2, d.price2, d.p2.x, d.p2.y);
          d.p2.x = c2.x;
          d.p2.y = c2.y;
        }
      } else {
        const c = this.anchorToCoord(d.t1, d.price1, d.x || 0, d.y || 0);
        if (d.tool === "hline" || d.tool === "text") d.y = c.y;
        if (d.tool === "vline" || d.tool === "text") d.x = c.x;
      }
    });

    this.redrawAll();
  }

  redrawAll() {
    if (!this.svg) return;
    this.svg.querySelectorAll("g[data-id]").forEach((el) => el.remove());
    this.drawings.forEach((d) => {
      if (!d._skipRender) this.renderPermanent(d);
    });
  }

  convertYToPrices(y1, y2) {
    if (window.chartManager && window.chartManager.charts.length > 0) {
      const c = window.chartManager.charts[0];
      if (c && c.candleSeries && typeof c.candleSeries.coordinateToPrice === "function") {
        const pr1 = c.candleSeries.coordinateToPrice(y1);
        const pr2 = c.candleSeries.coordinateToPrice(y2);
        if (pr1 !== null && pr2 !== null) {
          return { top: Math.max(pr1, pr2), bottom: Math.min(pr1, pr2) };
        }
      }
    }
    const cur = window.appState ? window.appState.currentPrice || 65000 : 65000;
    return { top: cur * 1.005, bottom: cur * 0.995 };
  }

  renderPermanent(d) {
    if (!d || d._skipRender || !this.isDrawingValidForActiveChart(d)) return;
    if (d.p1 && (!Number.isFinite(d.p1.x) || !Number.isFinite(d.p1.y))) return;
    if (d.p2 && (!Number.isFinite(d.p2.x) || !Number.isFinite(d.p2.y))) return;

    const { h } = this.getViewportBounds();
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("data-id", d.id);
    if (this.selectedDrawingId === d.id) g.classList.add("drawing-selected");

    if (d.tool === "trendline") {
      if (!d.p1 || !d.p2) return;
      g.appendChild(this.createSvgElement("line", {
        x1: d.p1.x, y1: d.p1.y, x2: d.p2.x, y2: d.p2.y, stroke: "#60a5fa", "stroke-width": 2,
      }));
      g.appendChild(this.createSvgElement("circle", { cx: d.p1.x, cy: d.p1.y, r: 3.5, fill: "#60a5fa" }));
      g.appendChild(this.createSvgElement("circle", { cx: d.p2.x, cy: d.p2.y, r: 3.5, fill: "#60a5fa" }));
    } else if (d.tool === "ray") {
      if (!d.p1 || !d.p2) return;
      const ext = this.computeRayEndpoint(d.p1, d.p2);
      g.appendChild(this.createSvgElement("line", {
        x1: d.p1.x, y1: d.p1.y, x2: ext.x, y2: ext.y, stroke: "#f59e0b", "stroke-width": 2,
      }));
      g.appendChild(this.createSvgElement("circle", { cx: d.p1.x, cy: d.p1.y, r: 3.5, fill: "#f59e0b" }));
    } else if (d.tool === "hline") {
      const y = d.y !== undefined ? d.y : (d.p1 ? d.p1.y : 100);
      if (!Number.isFinite(y) || y < -h || y > h * 2) return;
      g.appendChild(this.createSvgElement("line", {
        x1: 0, y1: y, x2: "100%", y2: y, stroke: "#38bdf8", "stroke-width": 1.5, "stroke-dasharray": "5,4",
      }));
      const lbl = this.createSvgElement("text", {
        x: 10, y: y - 4, fill: "#38bdf8", "font-size": "10px", "font-family": "monospace", "font-weight": "bold",
      });
      lbl.textContent = `H-Line ${this.formatPrice(d.price1 ?? d.price)}`;
      g.appendChild(lbl);
    } else if (d.tool === "vline") {
      const x = d.x !== undefined ? d.x : (d.p1 ? d.p1.x : 100);
      if (!Number.isFinite(x)) return;
      g.appendChild(this.createSvgElement("line", {
        x1: x, y1: 0, x2: x, y2: "100%", stroke: "#a855f7", "stroke-width": 1.5, "stroke-dasharray": "5,4",
      }));
    } else if (
      d.tool === "rectangle" ||
      d.tool === "demand_zone" ||
      d.tool === "supply_zone"
    ) {
      if (!d.p1 || !d.p2) return;
      const x = Math.min(d.p1.x, d.p2.x);
      const y = Math.min(d.p1.y, d.p2.y);
      const w = Math.abs(d.p2.x - d.p1.x);
      const rectH = Math.abs(d.p2.y - d.p1.y);
      const isDemand = d.zoneType === "DEMAND";
      const isSupply = d.zoneType === "SUPPLY";
      const fill = isDemand ? "rgba(16, 185, 129, 0.2)" : isSupply ? "rgba(239, 68, 68, 0.2)" : "rgba(59, 130, 246, 0.16)";
      const stroke = isDemand ? "#10b981" : isSupply ? "#ef4444" : "#3b82f6";

      g.appendChild(this.createSvgElement("rect", {
        x, y, width: w, height: rectH, fill, stroke, "stroke-width": 1.5,
      }));

      const label = this.createSvgElement("text", {
        x: x + 6, y: y + 14, fill: stroke, "font-size": "10px", "font-family": "monospace", "font-weight": "bold",
      });
      const zLabel = isDemand ? "DEMAND" : isSupply ? "SUPPLY" : "BOX";
      let statusTag = d.classification ? ` [${d.classification}]` : "";
      if (d.mitigation_pct !== undefined && d.mitigation_pct > 0) {
        statusTag += ` (${d.mitigation_pct}% MIT)`;
      } else if (d.freshness) {
        statusTag += ` (FRESH)`;
      }
      label.textContent = `${zLabel}${statusTag} (${this.formatPrice(d.priceBottom)} - ${this.formatPrice(d.priceTop)})`;
      g.appendChild(label);

      if (d.classification && d.quality_score) {
        const subLabel = this.createSvgElement("text", {
          x: x + 6, y: y + 26, fill: stroke, "font-size": "9px", "font-family": "monospace", opacity: "0.85",
        });
        subLabel.textContent = `Score: ${d.quality_score}/100 • ${d.classification_reason ? d.classification_reason.slice(0, 38) + '...' : ''}`;
        g.appendChild(subLabel);
      }
    } else if (d.tool === "fibonacci") {
      if (!d.p1 || !d.p2) return;
      this.renderFibonacci(g, d.p1, d.p2, false, d);
    } else if (d.tool === "position_long" || d.tool === "position_short" || d.tool === "position") {
      if (!d.p1 || !d.p2) return;
      this.renderPositionTool(g, d.p1, d.p2, d.tool !== "position_short", false, d);
    } else if (d.tool === "text") {
      const x = d.x !== undefined ? d.x : (d.p1 ? d.p1.x : 50);
      const y = d.y !== undefined ? d.y : (d.p1 ? d.p1.y : 50);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const text = this.createSvgElement("text", {
        x, y, fill: "#f1f5f9", "font-size": "12px", "font-family": "monospace", "font-weight": "bold",
      });
      text.textContent = d.text;
      g.appendChild(text);
    }

    this.svg.appendChild(g);
  }

  renderFibonacci(g, p1, p2, isPreview, dObj = null) {
    const levels = [
      { r: 0.0, label: "0.0", color: "#94a3b8" },
      { r: 0.382, label: "0.382", color: "#38bdf8" },
      { r: 0.5, label: "0.5 (EQ)", color: "#f59e0b" },
      { r: 0.618, label: "0.618 (OTE)", color: "#10b981" },
      { r: 0.786, label: "0.786 (OTE)", color: "#10b981" },
      { r: 1.0, label: "1.0", color: "#94a3b8" },
    ];
    const dy = p2.y - p1.y;
    const leftX = Math.min(p1.x, p2.x);
    const rightX = Math.max(p1.x, p2.x) + 120;

    levels.forEach((lvl) => {
      const y = p1.y + dy * lvl.r;
      g.appendChild(this.createSvgElement("line", {
        x1: leftX, y1: y, x2: rightX, y2: y, stroke: lvl.color, "stroke-width": 1,
        "stroke-dasharray": isPreview ? "3,3" : "none",
      }));
      let priceText = "";
      if (dObj && dObj.price1 !== null && dObj.price2 !== null && dObj.price1 !== undefined && dObj.price2 !== undefined) {
        const pr = dObj.price1 + (dObj.price2 - dObj.price1) * lvl.r;
        priceText = ` (${this.formatPrice(pr)})`;
      }
      const text = this.createSvgElement("text", {
        x: Math.max(p1.x, p2.x) + 6, y: y - 2, fill: lvl.color, "font-size": "10px", "font-family": "monospace",
      });
      text.textContent = `${lvl.label}${priceText}`;
      g.appendChild(text);
    });
  }

  renderPositionTool(g, p1, p2, isLong, isPreview, dObj = null) {
    const { w: svgW, h: svgH } = this.getViewportBounds();
    const x = Math.max(0, Math.min(svgW - 20, Math.min(p1.x, p2.x)));
    const rawW = Math.abs(p2.x - p1.x);
    const w = Math.min(svgW, Math.max(rawW, 110));
    // p1.y is Entry level; p2.y defines Stop or Target span (symmetric 2:1 projection)
    const entryY = this.clampCoord(p1.y, -svgH, svgH * 2, svgH * 0.5);
    const halfSpan = Math.max(Math.min(Math.abs(p2.y - p1.y) * 0.4, svgH * 0.4), 14);
    const tpY = this.clampCoord(isLong ? entryY - halfSpan * 2.0 : entryY + halfSpan * 2.0, -svgH, svgH * 2, entryY);
    const slY = this.clampCoord(isLong ? entryY + halfSpan : entryY - halfSpan, -svgH, svgH * 2, entryY);

    const profitBoxY = Math.min(entryY, tpY);
    const profitBoxH = Math.min(svgH * 2, Math.abs(tpY - entryY));
    const riskBoxY = Math.min(entryY, slY);
    const riskBoxH = Math.min(svgH * 2, Math.abs(slY - entryY));

    // Profit box (Green)
    g.appendChild(this.createSvgElement("rect", {
      x, y: profitBoxY, width: w, height: profitBoxH,
      fill: "rgba(16, 185, 129, 0.22)", stroke: "#10b981", "stroke-width": 1,
    }));
    // Risk box (Red)
    g.appendChild(this.createSvgElement("rect", {
      x, y: riskBoxY, width: w, height: riskBoxH,
      fill: "rgba(239, 68, 68, 0.22)", stroke: "#ef4444", "stroke-width": 1,
    }));

    // Horizontal level lines for TP, Entry, SL
    g.appendChild(this.createSvgElement("line", {
      x1: x, y1: tpY, x2: x + w, y2: tpY, stroke: "#10b981", "stroke-width": 1.5, "stroke-dasharray": "3,3",
    }));
    g.appendChild(this.createSvgElement("line", {
      x1: x, y1: entryY, x2: x + w, y2: entryY, stroke: "#38bdf8", "stroke-width": 1.5,
    }));
    g.appendChild(this.createSvgElement("line", {
      x1: x, y1: slY, x2: x + w, y2: slY, stroke: "#ef4444", "stroke-width": 1.5, "stroke-dasharray": "3,3",
    }));

    const label = this.createSvgElement("text", {
      x: x + 6, y: entryY - 4, fill: "#f1f5f9", "font-size": "10px", "font-family": "monospace", "font-weight": "bold",
    });
    const entryPriceTxt = dObj && dObj.price1 ? this.formatPrice(dObj.price1) : "";
    label.textContent = `${isLong ? "LONG" : "SHORT"} 2.0R ${entryPriceTxt}`.trim();
    g.appendChild(label);
  }

  addHorizontalLine(x, y) {
    const anchor = this.coordToAnchor(x, y);
    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: "hline",
      x,
      y,
      t1: anchor.t,
      price1: anchor.price,
      price: anchor.price,
    };
    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();
  }

  addVerticalLine(x, y) {
    const anchor = this.coordToAnchor(x, y);
    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: "vline",
      x,
      y,
      t1: anchor.t,
      price1: anchor.price,
    };
    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();
  }

  addTextAnnotation(x, y, text) {
    const anchor = this.coordToAnchor(x, y);
    const drawingObj = {
      id: "draw_" + Date.now(),
      tool: "text",
      x,
      y,
      t1: anchor.t,
      price1: anchor.price,
      text,
    };
    this.drawings.push(drawingObj);
    this.renderPermanent(drawingObj);
    this.saveDrawings();
  }

  selectDrawing(id, e = null) {
    this.selectedDrawingId = id;
    this.redrawAll();
    const d = this.drawings.find((item) => item.id === id);
    if (!d) return;

    const coords = e ? this.getSvgCoords(e) : {
      x: d.p1 ? (d.p1.x + d.p2.x) / 2 : (d.x || 200),
      y: d.p1 ? Math.min(d.p1.y, d.p2.y) : (d.y || 120),
    };
    this.showDrawingToolbarPopup(d, coords.x, coords.y);
  }

  deselectAll() {
    this.selectedDrawingId = null;
    this.hideZoneInspector();
    this.redrawAll();
  }

  deleteDrawing(id) {
    this.drawings = this.drawings.filter((item) => item.id !== id);
    if (this.selectedDrawingId === id) this.selectedDrawingId = null;
    this.hideZoneInspector();
    this.redrawAll();
    this.saveDrawings();
  }

  showDrawingToolbarPopup(obj, x, y) {
    this.hideZoneInspector();

    const inspector = document.createElement("div");
    inspector.id = "zone-ai-inspector-badge";
    inspector.className = "zone-inspector-popup";
    inspector.style.left = `${Math.max(20, Math.min(x - 80, (this.svg?.clientWidth || 800) - 240))}px`;
    inspector.style.top = `${Math.max(20, y - 55)}px`;

    const isZone = obj.zoneType === "DEMAND" || obj.zoneType === "SUPPLY";
    const tagLabel = isZone ? `${obj.zoneType} ZONE${obj.classification ? ' • ' + obj.classification : ''}` : obj.tool.toUpperCase();
    const tagClass = obj.zoneType === "DEMAND" ? "tag-demand" : obj.zoneType === "SUPPLY" ? "tag-supply" : "";

    let priceTxt = "";
    if (isZone && obj.priceBottom && obj.priceTop) {
      priceTxt = `${this.formatPrice(obj.priceBottom)} - ${this.formatPrice(obj.priceTop)}`;
      if (obj.quality_score) priceTxt += ` | Score: ${obj.quality_score}/100`;
    } else if (obj.price1) {
      priceTxt = this.formatPrice(obj.price1);
    }

    inspector.innerHTML = `
      <div class="inspector-header">
        <span class="inspector-tag ${tagClass}">${tagLabel}</span>
        <span class="inspector-prices font-mono fs-xs">${priceTxt}</span>
      </div>
      ${obj.classification_reason ? `<div class="fs-xs text-dim p-4" style="max-width:260px; line-height:1.3;">${obj.classification_reason}</div>` : ''}
      <div style="display:flex; gap:6px;">
        ${isZone ? `<button id="btn-trigger-ai-zone" class="btn-analyze-zone-ai" style="flex:1;">⚡ Analyze Zone</button>` : ""}
        ${obj.tool === "text" ? `<button id="btn-edit-drawing-text" class="btn-terminal-sm">✎ Edit</button>` : ""}
        <button id="btn-delete-drawing-item" class="btn-terminal-sm" style="color:var(--red);">🗑 Delete</button>
      </div>
    `;

    const parent = document.querySelector(".tv-main-chart-box") || document.body;
    parent.appendChild(inspector);

    const aiBtn = document.getElementById("btn-trigger-ai-zone");
    if (aiBtn) aiBtn.addEventListener("click", () => this.executeGeminiZoneAnalysis(obj));

    const editBtn = document.getElementById("btn-edit-drawing-text");
    if (editBtn) {
      editBtn.addEventListener("click", () => {
        const next = prompt("Edit annotation text:", obj.text || "");
        if (next && next.trim()) {
          obj.text = next.trim();
          this.redrawAll();
          this.saveDrawings();
        }
      });
    }

    const delBtn = document.getElementById("btn-delete-drawing-item");
    if (delBtn) delBtn.addEventListener("click", () => this.deleteDrawing(obj.id));
  }

  hideZoneInspector() {
    const el = document.getElementById("zone-ai-inspector-badge");
    if (el) el.remove();
  }

  async executeGeminiZoneAnalysis(zoneObj) {
    const proximal = zoneObj.zoneType === "DEMAND" ? zoneObj.priceTop : zoneObj.priceBottom;
    const distal = zoneObj.zoneType === "DEMAND" ? zoneObj.priceBottom : zoneObj.priceTop;
    const pair = window.appState ? window.appState.currentPair || window.appState.pair : "BTC/USDT";
    const tf = window.appState ? window.appState.currentInterval || window.appState.interval : "15m";

    this.hideZoneInspector();

    const aiTrayBtn = document.querySelector(".tv-tray-icon[data-page='page-ai']");
    if (aiTrayBtn) aiTrayBtn.click();

    const chatContainer = document.getElementById("chat-messages-container");
    if (chatContainer) {
      const loadingDiv = document.createElement("div");
      loadingDiv.className = "message-bubble ai";
      loadingDiv.innerHTML = `<em>⚡ Evaluating manual <strong>${zoneObj.zoneType} Zone</strong> (${this.formatPrice(distal)} - ${this.formatPrice(proximal)})...</em>`;
      chatContainer.appendChild(loadingDiv);
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    try {
      const res = await fetch("/api/analyze-zone", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pair,
          timeframe: tf,
          zone_type: zoneObj.zoneType,
          proximal,
          distal,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        // Update drawing attributes with analytical results
        zoneObj.classification = data.classification;
        zoneObj.quality_score = data.quality_score;
        zoneObj.mitigation_pct = data.mitigation_pct;
        zoneObj.freshness = data.freshness;
        zoneObj.classification_reason = data.classification_reason || data.overlap_suggestion || "";
        zoneObj.criteria = data.criteria;
        this.redrawAll();
        this.saveDrawings();

        if (chatContainer) {
          const aiDiv = document.createElement("div");
          aiDiv.className = "message-bubble ai";
          const formattedText = typeof marked !== "undefined" ? marked.parse(data.ai_analysis) : data.ai_analysis;
          aiDiv.innerHTML = `
            <div style="background:rgba(41,98,255,0.08); border:1px solid rgba(41,98,255,0.3); padding:10px; border-radius:6px;">
              <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                <span class="badge badge-${data.classification.includes("STRONG") || data.classification.includes("VALID") ? "emerald" : data.classification.includes("WEAK") ? "amber" : "rose"}">
                  ${data.classification} (${data.quality_score}/100)
                </span>
                <span class="font-mono fs-xs text-dim">${data.lifecycle_state}</span>
              </div>
              <div style="font-size:11px; margin-bottom:6px; color:var(--text-muted);">${data.classification_reason || ''}</div>
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

  async validateAllZonesOnChart() {
    const pair = window.appState ? window.appState.currentPair || window.appState.pair : "BTC/USDT";
    const tf = window.appState ? window.appState.currentInterval || window.appState.interval : "15m";
    const zones = this.drawings.filter(d => d.zoneType === "DEMAND" || d.zoneType === "SUPPLY" || d.tool === "demand_zone" || d.tool === "supply_zone");
    if (!zones.length) {
      if (typeof window.showToast === "function") window.showToast("No manual zones on chart to validate", "info");
      return;
    }
    
    try {
      const payload = {
        pair,
        timeframe: tf,
        zones: zones.map(z => ({
          id: z.id,
          zone_type: z.zoneType || (z.tool === "demand_zone" ? "DEMAND" : "SUPPLY"),
          proximal: z.zoneType === "DEMAND" ? z.priceTop : z.priceBottom,
          distal: z.zoneType === "DEMAND" ? z.priceBottom : z.priceTop,
          priceTop: z.priceTop,
          priceBottom: z.priceBottom
        }))
      };
      
      const res = await fetch("/api/validate-zones", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const data = await res.json();
        if (data.zone_evaluations) {
          for (const ev of data.zone_evaluations) {
            const match = zones.find(z => z.id === ev.id || (Math.abs(z.priceTop - ev.priceTop) < 0.01 && Math.abs(z.priceBottom - ev.priceBottom) < 0.01));
            if (match) {
              match.classification = ev.classification;
              match.quality_score = ev.quality_score;
              match.mitigation_pct = ev.mitigation_pct;
              match.freshness = ev.freshness;
              match.classification_reason = ev.classification_reason;
              match.criteria = ev.criteria;
            }
          }
          this.redrawAll();
          this.saveDrawings();
          if (typeof window.showToast === "function") {
            window.showToast(`Validated ${data.total_manual_zones} zones (${data.strong_zones} Strong, ${data.valid_zones} Valid)`, "success");
          }
        }
      }
    } catch (e) {
      console.error("validateAllZonesOnChart error:", e);
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
    if (this.drawings.length === 0) return;
    if (confirm("Clear all drawings on this chart timeframe?")) {
      this.drawings = [];
      this.selectedDrawingId = null;
      this.svg.innerHTML = "";
      this.hideZoneInspector();
      this.saveDrawings();
    }
  }

  async saveDrawings() {
    const pair = window.appState ? window.appState.currentPair || window.appState.pair : "BTC/USDT";
    const tf = window.appState ? window.appState.currentInterval || window.appState.interval : "15m";

    try {
      localStorage.setItem(`drawings_${pair}_${tf}`, JSON.stringify(this.drawings));
      await fetch("/api/drawings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair, timeframe: tf, drawings: this.drawings }),
      });
    } catch (e) {
      console.debug("Failed saving drawings:", e);
    }
  }

  async loadDrawings() {
    const seq = ++this._loadSeq;
    if (this._abortController) {
      try { this._abortController.abort(); } catch (e) {}
    }
    this._abortController = typeof AbortController !== "undefined" ? new AbortController() : null;
    const signal = this._abortController ? this._abortController.signal : undefined;

    const pair = window.appState ? window.appState.currentPair || window.appState.pair : "BTC/USDT";
    const tf = window.appState ? window.appState.currentInterval || window.appState.interval : "15m";

    this.drawings = [];
    if (this.svg) this.svg.innerHTML = "";
    this.selectedDrawingId = null;
    this.hideZoneInspector();
    try {
      const res = await fetch(`/api/drawings?pair=${encodeURIComponent(pair)}&timeframe=${tf}`, { signal });
      if (seq !== this._loadSeq || (signal && signal.aborted)) return;
      if (res.ok) {
        const data = await res.json();
        if (seq !== this._loadSeq || (signal && signal.aborted)) return;
        this.drawings = Array.isArray(data.drawings) ? data.drawings : [];
        this.reprojectDrawings();
        return;
      }
    } catch (e) {
      if (e && e.name === "AbortError") return;
    }
    if (seq !== this._loadSeq || (signal && signal.aborted)) return;
    const local = localStorage.getItem(`drawings_${pair}_${tf}`);
    if (local) {
      try {
        const parsed = JSON.parse(local);
        if (seq === this._loadSeq && Array.isArray(parsed)) {
          this.drawings = parsed;
          this.reprojectDrawings();
        }
      } catch (e) {}
    }
  }
}

window.drawingTools = null;
