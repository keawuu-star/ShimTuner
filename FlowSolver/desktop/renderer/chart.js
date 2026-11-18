'use strict';

/**
 * 轻量 Canvas 折线图: 轴线/网格/虚线样式/竖直标记/十字准星/悬浮读数。
 * 不依赖任何第三方图表库, 保证 Electron 离线可用。
 */
(function () {
  const DASHES = {
    solid: [],
    dash: [7, 5],
    dot: [2, 4],
    dashdot: [8, 4, 2, 4],
  };
  const COLORS = {
    grid: 'rgba(148, 163, 184, 0.11)',
    zero: 'rgba(148, 163, 184, 0.30)',
    tick: '#7d8fa7',
    label: '#9fb1c7',
    faint: '#5f7189',
    crosshair: 'rgba(226, 237, 247, 0.30)',
    plotBg: 'rgba(8, 15, 26, 0.35)',
  };
  const TICK_FONT = '10.5px "Cascadia Mono", Consolas, monospace';
  const LABEL_FONT = '10.5px "Segoe UI", system-ui, "Microsoft YaHei", sans-serif';

  function niceTicks(min, max, target) {
    const span = max - min;
    if (!(span > 0) || !Number.isFinite(span)) return [min];
    const raw = span / Math.max(1, target);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
    const start = Math.ceil(min / step - 1e-9) * step;
    const out = [];
    for (let v = start; v <= max + step * 1e-6; v += step) {
      out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    }
    if (out.length === 0) out.push(min, max);
    return out;
  }

  function tickLabel(value) {
    if (!Number.isFinite(value)) return '';
    if (value === 0) return '0';
    const a = Math.abs(value);
    if (a >= 1000) return value.toFixed(0);
    if (a >= 100) return value.toFixed(1).replace(/\.0$/, '');
    if (a >= 10) return value.toFixed(2).replace(/0$/, '').replace(/\.$/, '');
    if (a >= 1) return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
    if (a >= 0.001) return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
    return value.toExponential(1);
  }

  function valueLabel(value) {
    if (!Number.isFinite(value)) return '—';
    const a = Math.abs(value);
    if (a === 0) return '0';
    if (a >= 100) return value.toFixed(2);
    if (a >= 10) return value.toFixed(3);
    if (a >= 1) return value.toFixed(4);
    if (a >= 0.0001) return value.toFixed(5);
    return value.toExponential(2);
  }

  function interpolate(series, x) {
    const xs = series.x;
    const ys = series.y;
    let prev = null;
    for (let i = 0; i < xs.length; i += 1) {
      const xi = xs[i];
      const yi = ys[i];
      if (!Number.isFinite(xi) || !Number.isFinite(yi)) continue;
      if (Math.abs(xi - x) < 1e-12) return yi;
      if (prev && (prev.x - x) * (xi - x) <= 0) {
        const dx = xi - prev.x;
        if (Math.abs(dx) < 1e-15) return yi;
        return prev.y + ((x - prev.x) / dx) * (yi - prev.y);
      }
      prev = { x: xi, y: yi };
    }
    return null;
  }

  class LineChart {
    constructor(canvas, tip, options = {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.tip = tip;
      this.data = null;
      this.hidden = new Set();
      this.hover = null;
      this.geom = null;
      this.onToggle = options.onToggle || null;

      this._resize = this.resize.bind(this);
      this._move = this.onMove.bind(this);
      this._leave = this.onLeave.bind(this);
      if (typeof ResizeObserver !== 'undefined') {
        this.observer = new ResizeObserver(this._resize);
        this.observer.observe(canvas.parentElement);
      } else {
        window.addEventListener('resize', this._resize);
      }
      canvas.addEventListener('mousemove', this._move);
      canvas.addEventListener('mouseleave', this._leave);
      this.resize();
    }

    setData(data) {
      this.data = data;
      const ids = new Set((data?.series || []).map((s) => s.id));
      this.hidden = new Set([...this.hidden].filter((id) => ids.has(id)));
      this.hover = null;
      this.hideTip();
      this.draw();
    }

    setHidden(hidden) {
      this.hidden = hidden;
      this.draw();
    }

    resize() {
      const parent = this.canvas.parentElement;
      if (!parent) return;
      const rect = parent.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(1, Math.floor(rect.width));
      const h = Math.max(1, Math.floor(rect.height));
      this.cssW = w;
      this.cssH = h;
      this.canvas.width = Math.floor(w * dpr);
      this.canvas.height = Math.floor(h * dpr);
      this.dpr = dpr;
      this.draw();
    }

    visibleSeries() {
      if (!this.data) return [];
      return this.data.series.filter((s) => !this.hidden.has(s.id));
    }

    draw() {
      const ctx = this.ctx;
      const w = this.cssW || 0;
      const h = this.cssH || 0;
      const dpr = this.dpr || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      if (w < 40 || h < 40) return;

      if (!this.data) {
        this.drawEmpty('等待求解结果…');
        return;
      }

      const series = this.visibleSeries();
      if (series.length === 0) {
        this.drawEmpty('所有序列已隐藏 — 点击图例恢复');
        return;
      }

      const PAD = { l: 62, r: 18, t: 16, b: 36 };
      const plotW = w - PAD.l - PAD.r;
      const plotH = h - PAD.t - PAD.b;
      if (plotW < 20 || plotH < 20) return;

      let xmin = Infinity;
      let xmax = -Infinity;
      let ymin = Infinity;
      let ymax = -Infinity;
      for (const s of series) {
        for (let i = 0; i < s.x.length; i += 1) {
          const x = s.x[i];
          const y = s.y[i];
          if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
          if (x < xmin) xmin = x;
          if (x > xmax) xmax = x;
          if (y < ymin) ymin = y;
          if (y > ymax) ymax = y;
        }
      }
      if (!Number.isFinite(xmin) || !Number.isFinite(ymin)) {
        this.drawEmpty('没有可绘制的数据点');
        return;
      }
      if (!(xmax > xmin)) xmax = xmin + 1;
      if (!(ymax > ymin)) ymax = ymin + 1;
      if (ymin > 0) ymin = 0;
      const xPad = (xmax - xmin) * 0.015;
      xmax += xPad;
      const yPad = (ymax - ymin) * 0.06;
      ymax += yPad;
      if (ymin < 0) ymin -= yPad;

      const px = (x) => PAD.l + ((x - xmin) / (xmax - xmin)) * plotW;
      const py = (y) => PAD.t + plotH - ((y - ymin) / (ymax - ymin)) * plotH;

      this.geom = { PAD, plotW, plotH, xmin, xmax, ymin, ymax, px, py, series };

      ctx.save();
      ctx.beginPath();
      ctx.rect(PAD.l, PAD.t, plotW, plotH);
      ctx.fillStyle = COLORS.plotBg;
      ctx.fill();
      ctx.restore();

      // 网格与刻度
      const xTicks = niceTicks(xmin, xmax, Math.max(3, Math.min(7, Math.floor(plotW / 92))));
      const yTicks = niceTicks(ymin, ymax, Math.max(3, Math.min(6, Math.floor(plotH / 40))));
      ctx.font = TICK_FONT;
      ctx.lineWidth = 1;
      ctx.strokeStyle = COLORS.grid;
      ctx.fillStyle = COLORS.tick;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      for (const t of xTicks) {
        const x = px(t);
        if (x < PAD.l - 1 || x > PAD.l + plotW + 1) continue;
        ctx.beginPath();
        ctx.moveTo(x, PAD.t);
        ctx.lineTo(x, PAD.t + plotH);
        ctx.stroke();
        ctx.fillText(tickLabel(t), x, PAD.t + plotH + 7);
      }
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (const t of yTicks) {
        const y = py(t);
        if (y < PAD.t - 1 || y > PAD.t + plotH + 1) continue;
        ctx.beginPath();
        ctx.moveTo(PAD.l, y);
        ctx.lineTo(PAD.l + plotW, y);
        ctx.stroke();
        ctx.fillText(tickLabel(t), PAD.l - 8, y);
      }

      // 零线
      ctx.strokeStyle = COLORS.zero;
      if (xmin < 0 && xmax > 0) {
        const x0 = px(0);
        ctx.beginPath();
        ctx.moveTo(x0, PAD.t);
        ctx.lineTo(x0, PAD.t + plotH);
        ctx.stroke();
      }
      if (ymin < 0 && ymax > 0) {
        const y0 = py(0);
        ctx.beginPath();
        ctx.moveTo(PAD.l, y0);
        ctx.lineTo(PAD.l + plotW, y0);
        ctx.stroke();
      }

      // 竖直标记 (阀片外缘 / 固支半径)
      const markers = this.data.markers || [];
      ctx.save();
      ctx.beginPath();
      ctx.rect(PAD.l, PAD.t, plotW, plotH);
      ctx.clip();
      markers.forEach((m, index) => {
        if (m.axis !== 'x' || !Number.isFinite(m.value)) return;
        if (m.value < xmin || m.value > xmax) return;
        const x = px(m.value);
        ctx.save();
        ctx.strokeStyle = m.color;
        ctx.globalAlpha = 0.5;
        ctx.setLineDash(DASHES[m.style] || DASHES.dash);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, PAD.t);
        ctx.lineTo(x, PAD.t + plotH);
        ctx.stroke();
        ctx.restore();

        ctx.save();
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = m.color;
        ctx.font = '9px "Cascadia Mono", Consolas, monospace';
        ctx.textBaseline = 'top';
        const label = m.label.length > 18 ? `${m.label.slice(0, 17)}…` : m.label;
        const labelW = ctx.measureText(label).width;
        let labelX = x + 4;
        ctx.textAlign = 'left';
        if (labelX + labelW > PAD.l + plotW - 4) {
          labelX = x - 4;
          ctx.textAlign = 'right';
        }
        const ly = PAD.t + 3 + (index % 3) * 11;
        ctx.fillText(label, labelX, ly);
        ctx.restore();
      });
      ctx.restore();

      // 数据序列
      ctx.save();
      ctx.beginPath();
      ctx.rect(PAD.l, PAD.t, plotW, plotH);
      ctx.clip();
      for (const s of series) {
        if (!s.x.length) continue;
        ctx.save();
        ctx.strokeStyle = s.color;
        ctx.lineWidth = Math.max(1, s.width || 2);
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.setLineDash(DASHES[s.dash] || DASHES.solid);
        let started = false;
        ctx.beginPath();
        for (let i = 0; i < s.x.length; i += 1) {
          const x = s.x[i];
          const y = s.y[i];
          if (!Number.isFinite(x) || !Number.isFinite(y)) {
            started = false;
            continue;
          }
          if (!started) {
            ctx.moveTo(px(x), py(y));
            started = true;
          } else {
            ctx.lineTo(px(x), py(y));
          }
        }
        ctx.stroke();
        ctx.restore();

        if (s.marker === 'circle' && s.x.length <= 48) {
          ctx.save();
          ctx.fillStyle = s.color;
          ctx.strokeStyle = 'rgba(7, 12, 21, 0.9)';
          ctx.lineWidth = 1;
          for (let i = 0; i < s.x.length; i += 1) {
            const x = s.x[i];
            const y = s.y[i];
            if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
            ctx.beginPath();
            ctx.arc(px(x), py(y), 2.6, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
          }
          ctx.restore();
        }
      }
      ctx.restore();

      // 轴标题
      ctx.fillStyle = COLORS.label;
      ctx.font = LABEL_FONT;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(this.data.x_label, PAD.l + plotW / 2, h - 4);
      ctx.save();
      ctx.translate(11, PAD.t + plotH / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textBaseline = 'middle';
      ctx.fillText(this.data.y_label, 0, 0);
      ctx.restore();

      // 十字准星 + 悬浮点
      if (this.hover && this.hover.x >= xmin && this.hover.x <= xmax) {
        const hx = px(this.hover.x);
        ctx.save();
        ctx.strokeStyle = COLORS.crosshair;
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(hx, PAD.t);
        ctx.lineTo(hx, PAD.t + plotH);
        ctx.stroke();
        ctx.setLineDash([]);
        for (const s of series) {
          const vy = interpolate(s, this.hover.x);
          if (vy === null) continue;
          const vyPx = py(vy);
          if (vyPx < PAD.t || vyPx > PAD.t + plotH) continue;
          ctx.fillStyle = s.color;
          ctx.strokeStyle = 'rgba(7, 12, 21, 0.95)';
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(hx, vyPx, 3.2, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        }
        ctx.restore();
      }
    }

    drawEmpty(text) {
      this.geom = null;
      const ctx = this.ctx;
      const w = this.cssW || 0;
      const h = this.cssH || 0;
      ctx.fillStyle = COLORS.faint;
      ctx.font = '11px "Segoe UI", system-ui, "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, w / 2, h / 2);
    }

    onMove(event) {
      if (!this.geom) return;
      const rect = this.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const { PAD, plotW, plotH, xmin, xmax, series } = this.geom;
      if (mx < PAD.l || mx > PAD.l + plotW || my < PAD.t || my > PAD.t + plotH) {
        this.onLeave();
        return;
      }

      const t = Math.min(1, Math.max(0, (mx - PAD.l) / plotW));
      const x = xmin + t * (xmax - xmin);
      if (!series.some((s) => interpolate(s, x) !== null)) {
        this.onLeave();
        return;
      }

      this.hover = { x, mx, my };
      this.draw();
      this.showTip(x, mx, my);
    }

    showTip(x, mx, my) {
      if (!this.tip || !this.geom) return;
      const rows = [];
      for (const s of this.geom.series) {
        const vy = interpolate(s, x);
        if (vy === null) continue;
        rows.push(
          `<div class="tip-row"><i style="background:${s.color}"></i>` +
          `<b>${escapeHtml(s.label)}</b><span>${valueLabel(vy)}</span></div>`);
      }
      if (rows.length === 0) {
        this.hideTip();
        return;
      }
      this.tip.innerHTML =
        `<div class="tip-x">${escapeHtml(this.data.x_label)} = ${valueLabel(x)}</div>` +
        rows.join('');
      this.tip.classList.add('show');
      const tw = this.tip.offsetWidth;
      const th = this.tip.offsetHeight;
      const left = Math.min(Math.max(mx + 14, 6), this.cssW - tw - 6);
      const top = Math.min(Math.max(my - th / 2, 6), this.cssH - th - 6);
      this.tip.style.left = `${left}px`;
      this.tip.style.top = `${top}px`;
    }

    hideTip() {
      if (this.tip) this.tip.classList.remove('show');
    }

    onLeave() {
      if (!this.hover) {
        this.hideTip();
        return;
      }
      this.hover = null;
      this.hideTip();
      this.draw();
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  window.LineChart = LineChart;
  window.chartHelpers = { tickLabel, valueLabel, escapeHtml };
})();
