/* 阀体设计模式：草图 → 标注 → 关键参数识别
 * 纯几何/参数识别部分不依赖 DOM，可在 Node 下测试（见 desktop/test-design.js）。
 * 浏览器中由 app.js 调用 ValveDesign.open({ piston, onApply, onClose })。
 */
(function (global) {
  'use strict';

  const ROLES = [
    { id: 'piston_outer', label: '活塞外圆', kind: 'circle', required: true,
      hint: '画一个以活塞中心为圆心的圆，标注为活塞外圆（活塞直径）' },
    { id: 'rod', label: '杆孔', kind: 'circle', required: true,
      hint: '画中心杆孔圆（杆直径）' },
    { id: 'shim_outer', label: '阀片最大外圆', kind: 'circle', required: true,
      hint: '画最大阀片外缘圆（最大阀片直径）' },
    { id: 'window', label: '窗口（单个）', kind: 'any', required: true,
      hint: '画 1 个窗口轮廓（圆/闭合多段线），放在窗口中心半径处；其余窗口按 N 均布' },
    { id: 'clamp', label: '夹持圆（可选）', kind: 'circle', required: false,
      hint: '画夹持（压紧）直径圆；若与杆孔同径可跳过，默认取杆孔直径' },
    { id: 'bypass', label: '常通孔（可选）', kind: 'circle', required: false,
      hint: '画一个常通孔圆，用于识别常通孔直径' },
  ];
  const ROLE_MAP = Object.fromEntries(ROLES.map((r) => [r.id, r]));
  const ROLE_COLOR = {
    piston_outer: '#7dd3fc', rod: '#fbbf24', shim_outer: '#a78bfa',
    window: '#34d399', clamp: '#f472b6', bypass: '#fb923c',
  };

  // ---------------- 纯几何 ----------------
  function snap(v, step) {
    const s = step || 0.5;
    return Math.round(v / s) * s;
  }

  function circleGeom(shape) {
    return { cx: shape.cx, cy: shape.cy, r: shape.r,
             radius: Math.hypot(shape.cx, shape.cy) };
  }

  function polygonAreaCentroid(pts) {
    let a = 0, cx = 0, cy = 0;
    for (let i = 0; i < pts.length; i++) {
      const [x0, y0] = pts[i];
      const [x1, y1] = pts[(i + 1) % pts.length];
      const cross = x0 * y1 - x1 * y0;
      a += cross;
      cx += (x0 + x1) * cross;
      cy += (y0 + y1) * cross;
    }
    a *= 0.5;
    if (Math.abs(a) < 1e-12) {
      const n = pts.length;
      return { x: pts.reduce((s, p) => s + p[0], 0) / n,
               y: pts.reduce((s, p) => s + p[1], 0) / n, area: 0 };
    }
    return { x: cx / (6 * a), y: cy / (6 * a), area: Math.abs(a) };
  }

  function shapeCentroid(shape) {
    if (shape.kind === 'circle') return { x: shape.cx, y: shape.cy };
    const c = polygonAreaCentroid(shape.pts);
    return { x: c.x, y: c.y, area: c.area };
  }

  function shapeRadialRange(shape) {
    if (shape.kind === 'circle') {
      const R = Math.hypot(shape.cx, shape.cy);
      return { rmin: Math.max(0, R - shape.r), rmax: R + shape.r };
    }
    let rmin = Infinity, rmax = -Infinity;
    for (const [x, y] of shape.pts) {
      const r = Math.hypot(x, y);
      rmin = Math.min(rmin, r);
      rmax = Math.max(rmax, r);
    }
    return { rmin, rmax };
  }

  function shapeAngularSpan(shape) {
    if (shape.kind === 'circle') {
      const R = Math.hypot(shape.cx, shape.cy);
      if (R <= shape.r) return Math.PI * 2;
      return 2 * Math.asin(Math.min(1, shape.r / R));
    }
    let angles = shape.pts.map(([x, y]) => Math.atan2(y, x));
    angles = angles.map((a) => (a < 0 ? a + Math.PI * 2 : a)).sort((p, q) => p - q);
    let span = angles[angles.length - 1] - angles[0];
    // 跨 0° 的窗口：角度分布在 0 两侧时，尝试绕回
    if (span > Math.PI) {
      const shifted = angles.map((a) => (a < Math.PI ? a + Math.PI * 2 : a));
      shifted.sort((p, q) => p - q);
      const span2 = shifted[shifted.length - 1] - shifted[0];
      span = Math.min(span, span2);
    }
    return span;
  }

  function segmentCircleHits(p0, p1, R) {
    // |p0 + t(p1-p0)|² = R²，返回 t∈[0,1]
    const dx = p1[0] - p0[0], dy = p1[1] - p0[1];
    const a = dx * dx + dy * dy;
    if (a < 1e-18) return [];
    const b = 2 * (p0[0] * dx + p0[1] * dy);
    const c = p0[0] * p0[0] + p0[1] * p0[1] - R * R;
    const disc = b * b - 4 * a * c;
    if (disc < 0) return [];
    const sq = Math.sqrt(disc);
    const out = [];
    for (const t of [(-b - sq) / (2 * a), (-b + sq) / (2 * a)]) {
      if (t >= -1e-9 && t <= 1 + 1e-9) out.push(Math.min(1, Math.max(0, t)));
    }
    return out;
  }

  /** 多边形在半径 R 圆周上的角向张角（精确求边与圆的交点） */
  function polygonAngularSpan(pts, R, centerAng) {
    const angles = [];
    for (let i = 0; i < pts.length; i++) {
      const p0 = pts[i], p1 = pts[(i + 1) % pts.length];
      for (const t of segmentCircleHits(p0, p1, R)) {
        const x = p0[0] + t * (p1[0] - p0[0]);
        const y = p0[1] + t * (p1[1] - p0[1]);
        angles.push(Math.atan2(y, x));
      }
    }
    if (angles.length < 2) {
      angles.length = 0;
      for (const [x, y] of pts) angles.push(Math.atan2(y, x));
    }
    const ref = centerAng == null ? angles[0] : centerAng;
    const rel = angles.map((a) => {
      let d = a - ref;
      while (d > Math.PI) d -= Math.PI * 2;
      while (d < -Math.PI) d += Math.PI * 2;
      return d;
    }).sort((p, q) => p - q);
    return rel[rel.length - 1] - rel[0];
  }

  /** 多边形沿径向线（角度 ang）的径向范围 */
  function polygonRadialRange(pts, ang) {
    const dx = Math.cos(ang), dy = Math.sin(ang);
    const ts = [];
    for (let i = 0; i < pts.length; i++) {
      const [x0, y0] = pts[i], [x1, y1] = pts[(i + 1) % pts.length];
      const ex = x1 - x0, ey = y1 - y0;
      const den = dx * ey - dy * ex;
      if (Math.abs(den) < 1e-12) continue;
      const t = (x0 * ey - y0 * ex) / den;          // 沿射线参数
      const s = (x0 * dy - y0 * dx) / den;          // 沿边参数
      if (t >= -1e-9 && s >= -1e-9 && s <= 1 + 1e-9) ts.push(t);
    }
    if (ts.length >= 2) {
      return { rmin: Math.max(0, Math.min(...ts)), rmax: Math.max(...ts) };
    }
    return null;
  }

  /** 窗口几何 → 模型参数：center radius / 周向弧长 / 径向宽度 */
  function windowGeometry(shape) {
    const c = shapeCentroid(shape);
    const rPort = Math.hypot(c.x, c.y);
    const centerAng = Math.atan2(c.y, c.x);
    if (shape.kind === 'circle') {
      const R = Math.hypot(shape.cx, shape.cy);
      const span = R > shape.r ? 2 * Math.asin(Math.min(1, shape.r / R)) : Math.PI * 2;
      return { center: c, r_port: rPort, width: rPort * span,
               radial: 2 * shape.r, rmin: R - shape.r, rmax: R + shape.r, span };
    }
    const span = polygonAngularSpan(shape.pts, rPort, centerAng);
    const rad = polygonRadialRange(shape.pts, centerAng) || shapeRadialRange(shape);
    return { center: c, r_port: rPort, width: rPort * span,
             radial: rad.rmax - rad.rmin, rmin: rad.rmin, rmax: rad.rmax, span };
  }

  // ---------------- 识别 ----------------
  /** 返回 { params, guide, issues, windows } */
  function extractParams(shapes, options) {
    const opt = options || {};
    const nPorts = Math.max(1, Math.round(opt.n_ports || 6));
    const hDeck = Number(opt.h_deck != null ? opt.h_deck : 0.10);
    const byRole = {};
    for (const s of shapes || []) {
      if (!s.role) continue;
      (byRole[s.role] = byRole[s.role] || []).push(s);
    }
    const issues = [];
    const params = {};
    const guide = [];

    const circleOf = (role) => {
      const arr = byRole[role] || [];
      if (arr.length === 0) return null;
      if (arr.length > 1) issues.push(`「${ROLE_MAP[role].label}」标注了多个图元`);
      const s = arr[0];
      if (s.kind !== 'circle') {
        issues.push(`「${ROLE_MAP[role].label}」需要圆，实为多段线`);
        return null;
      }
      return circleGeom(s);
    };

    const pushGuide = (roleId, ok, valueText) => {
      const role = ROLE_MAP[roleId];
      guide.push({ id: roleId, label: role.label, ok,
                   value: valueText || '', hint: role.hint });
    };

    // 1) 活塞外圆
    const piston = circleOf('piston_outer');
    if (piston) {
      const D = 2 * piston.r;
      params.d_piston_mm = D;
      pushGuide('piston_outer', true, `Ø${D.toFixed(2)} mm`);
      if (piston.radius > 0.5) issues.push('活塞外圆未以原点为圆心');
    } else {
      pushGuide('piston_outer', false);
    }

    // 2) 杆孔
    const rod = circleOf('rod');
    if (rod) {
      const d = 2 * rod.r;
      params.d_rod_mm = d;
      pushGuide('rod', true, `Ø${d.toFixed(2)} mm`);
      if (rod.radius > 0.5) issues.push('杆孔未以原点为圆心');
    } else {
      pushGuide('rod', false);
    }

    // 3) 夹持圆（可选；默认取杆孔）
    let clamp = circleOf('clamp');
    if (!clamp && rod) {
      clamp = { cx: 0, cy: 0, r: rod.r, radius: 0 };
      pushGuide('clamp', true, `Ø${(2 * rod.r).toFixed(2)} mm（默认=杆孔）`);
    } else {
      pushGuide('clamp', Boolean(clamp), clamp ? `Ø${(2 * clamp.r).toFixed(2)} mm` : '');
      if (clamp && clamp.radius > 0.5) issues.push('夹持圆未以原点为圆心');
    }
    if (clamp) params.clamp_diameter_mm = 2 * clamp.r;

    // 4) 阀片最大外圆
    const shim = circleOf('shim_outer');
    if (shim) {
      params.shim_outer_diameter_mm = 2 * shim.r;
      pushGuide('shim_outer', true, `Ø${(2 * shim.r).toFixed(2)} mm`);
      if (shim.radius > 0.5) issues.push('阀片最大外圆未以原点为圆心');
    } else {
      pushGuide('shim_outer', false);
    }

    // 5) 窗口（一个轮廓，N 均布）
    const wins = byRole.window || [];
    let win = null;
    if (wins.length === 0) {
      pushGuide('window', false);
    } else {
      win = windowGeometry(wins[0]);
      if (wins.length > 1) {
        issues.push('窗口标注了多个图元；当前只识别第一个（其余窗口按 N 均布）');
      }
      params.port_radius_mm = win.r_port;
      params.port_width_mm = win.width;
      params.port_radial_mm = win.radial;
      pushGuide('window', true,
                `r=${win.r_port.toFixed(2)} · 周向 w=${win.width.toFixed(2)} · ` +
                `径向 dr=${win.radial.toFixed(2)} mm`);
    }
    params.n_ports = nPorts;

    // 6) 常通孔（可选）
    const bypass = circleOf('bypass');
    if (bypass) {
      params.bypass_d_mm = 2 * bypass.r;
      params.bypass_n = 1;
      pushGuide('bypass', true, `Ø${(2 * bypass.r).toFixed(2)} mm（个数请在求解页确认）`);
    } else {
      pushGuide('bypass', true, '未绘制（0 = 无）');
    }

    // 7) 非几何参数
    params.deck_height_mm = hDeck;
    guide.push({ id: 'n_ports', label: '窗口个数 N', ok: true,
                 value: `${nPorts} 个（均布）`,
                 hint: '在右侧输入框确认；窗口按 N 等角均布' });
    guide.push({ id: 'deck', label: '初始间隙 h_deck', ok: true,
                 value: `${hDeck.toFixed(3)} mm`,
                 hint: '不是几何量，需手动输入（阀片底面与活塞面冷态间隙）' });

    // 8) 关系校验
    if (params.d_piston_mm != null && params.d_rod_mm != null
        && !(params.d_rod_mm > 0 && params.d_rod_mm < params.d_piston_mm)) {
      issues.push('杆直径必须大于 0 且小于活塞直径');
    }
    const clampR = params.clamp_diameter_mm != null ? params.clamp_diameter_mm / 2 : null;
    const shimR = params.shim_outer_diameter_mm != null ? params.shim_outer_diameter_mm / 2 : null;
    if (win && clampR != null && shimR != null
        && !(clampR < win.r_port && win.r_port < shimR)) {
      issues.push('窗口中心半径必须位于夹持半径与最大阀片半径之间');
    }
    if (win && !(win.radial > 0 && win.width > 0)) {
      issues.push('窗口轮廓的径向/周向尺寸必须大于 0');
    }
    if (win && win.rmin < (clampR || 0) - 1e-6) {
      issues.push('窗口轮廓延伸到夹持圆以内');
    }

    const missing = ROLES.filter((r) => r.required && !byRole[r.id])
      .map((r) => r.label);
    return { params, guide, issues, missing, windows: wins.length, window: win };
  }

  function demoShapes() {
    return [
      { id: 1, kind: 'circle', cx: 0, cy: 0, r: 23, role: 'piston_outer' },
      { id: 2, kind: 'circle', cx: 0, cy: 0, r: 21, role: 'shim_outer' },
      { id: 3, kind: 'circle', cx: 0, cy: 0, r: 9, role: 'rod' },
      { id: 4, kind: 'polygon', role: 'window',
        pts: [[14, -4], [18, -4], [18, 4], [14, 4]] },
    ];
  }

  // ---------------- DOM / 交互 ----------------
  let d = null;                 // DOM 缓存
  let ctx = null;
  let dpr = 1;
  let shapes = [];
  let nextId = 1;
  let selectedId = null;
  let tool = 'select';
  let mode = 'sketch';          // 'sketch' | 'drawing'
  let undoStack = [];
  let redoStack = [];
  let baseline = null;          // { shapeSnap, res }：从当前参数生成、未改动时精确回写
  let draft = null;             // circle: {cx,cy,r}; polygon: {pts:[]}; rect: {x0,y0}
  let panning = null;
  let view = { scale: 6, panX: 0, panY: 0 };
  let options = {};
  let active = false;
  let bound = false;

  function $(id) { return document.getElementById(id); }

  function ensureDom() {
    if (d) return;
    d = {
      overlay: $('designOverlay'),
      canvas: $('designCanvas'),
      toolbar: $('designToolbar'),
      status: $('designStatus'),
      selected: $('designSelected'),
      params: $('designParams'),
      guide: $('designGuide'),
      apply: $('designApply'),
      n: $('designN'),
      deck: $('designDeck'),
      sub: $('designSub'),
      close: $('designClose'),
      clear: $('designClear'),
      demo: $('designDemo'),
      fit: $('designFit'),
      tabSketch: $('designTabSketch'),
      tabDrawing: $('designTabDrawing'),
      fromCurrent: $('designFromCurrent'),
      undo: $('designUndo'),
      redo: $('designRedo'),
      zoomIn: $('designZoomIn'),
      zoomOut: $('designZoomOut'),
      drawingPanel: $('designDrawingPanel'),
      tp: $('designTP'),
      bypassR: $('designBypassR'),
      exportSvg: $('designExportSvg'),
      exportPng: $('designExportPng'),
      side: document.querySelector('.design-side'),
      annotatePanel: $('designAnnotatePanel'),
      paramsPanel: $('designParamsPanel'),
      guidePanel: $('designGuidePanel'),
    };
    ctx = d.canvas.getContext('2d');
  }

  function sizeCanvas() {
    const wrap = d.canvas.parentElement;
    const w = Math.max(320, wrap.clientWidth);
    const h = Math.max(320, wrap.clientHeight);
    dpr = global.devicePixelRatio || 1;
    d.canvas.width = Math.round(w * dpr);
    d.canvas.height = Math.round(h * dpr);
    d.canvas.style.width = w + 'px';
    d.canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function worldToScreen(x, y) {
    const w = d.canvas.clientWidth;
    const h = d.canvas.clientHeight;
    return [w / 2 + view.panX + x * view.scale,
            h / 2 + view.panY - y * view.scale];
  }

  function screenToWorld(sx, sy) {
    const w = d.canvas.clientWidth;
    const h = d.canvas.clientHeight;
    return [(sx - w / 2 - view.panX) / view.scale,
            -(sy - h / 2 - view.panY) / view.scale];
  }

  function fitView() {
    if (mode === 'drawing') {
      const dwg = buildCurrentDrawing();
      const b = dwg.bbox;
      const w = d.canvas.clientWidth, h = d.canvas.clientHeight;
      view.scale = Math.max(0.8, Math.min(
        w / Math.max(1, b.x1 - b.x0),
        (h - 42) / Math.max(1, b.z1 - b.z0)) * 0.92);
      view.panX = -((b.x0 + b.x1) / 2) * view.scale;
      // 底部状态条会遮住画布下沿，整体上移一点
      view.panY = ((b.z0 + b.z1) / 2) * view.scale - 22;
      return;
    }
    let maxR = 30;
    for (const s of shapes) {
      const c = shapeCentroid(s);
      const rr = Math.hypot(c.x, c.y) + (s.kind === 'circle' ? s.r : 0);
      const { rmax } = shapeRadialRange(s);
      maxR = Math.max(maxR, s.kind === 'circle' ? rr : rmax);
    }
    if (shapes.length === 0) maxR = 30;
    const w = d.canvas.clientWidth;
    const h = d.canvas.clientHeight;
    view.scale = Math.max(1, Math.min(w, h) / (2 * maxR * 1.18));
    view.panX = 0;
    view.panY = 0;
  }

  function draw() {
    if (!ctx) return;
    if (mode === 'drawing') { drawEngineering(); return; }
    const w = d.canvas.clientWidth;
    const h = d.canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#0b1220';
    ctx.fillRect(0, 0, w, h);

    const step = view.scale >= 5 ? 1 : view.scale >= 2 ? 5 : 10;
    ctx.lineWidth = 1;
    // 网格
    for (let x = Math.ceil(screenToWorld(0, 0)[0] / step) * step;
         x <= screenToWorld(w, 0)[0]; x += step) {
      const [sx] = worldToScreen(x, 0);
      ctx.strokeStyle = Math.abs(x) < 1e-9 ? '#334155'
        : (step === 1 ? '#16233a' : '#1e2d47');
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, h); ctx.stroke();
    }
    for (let y = Math.ceil(screenToWorld(0, h)[1] / step) * step;
         y <= screenToWorld(0, 0)[1]; y += step) {
      const [, sy] = worldToScreen(0, y);
      ctx.strokeStyle = Math.abs(y) < 1e-9 ? '#334155'
        : (step === 1 ? '#16233a' : '#1e2d47');
      ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(w, sy); ctx.stroke();
    }

    // 图元
    for (const s of shapes) drawShape(s, s.id === selectedId);
    if (draft) drawDraft();

    // 比例/数量放在底部状态条，画布内不叠文字（避免被工具条遮挡）
  }

  function drawShape(s, selected) {
    ctx.save();
    ctx.lineWidth = selected ? 2.6 : 1.6;
    ctx.strokeStyle = ROLE_COLOR[s.role] || '#64748b';
    if (!s.role) ctx.setLineDash([5, 4]);
    ctx.beginPath();
    if (s.kind === 'circle') {
      const [cx, cy] = worldToScreen(s.cx, s.cy);
      ctx.arc(cx, cy, s.r * view.scale, 0, Math.PI * 2);
    } else {
      s.pts.forEach(([x, y], i) => {
        const [sx, sy] = worldToScreen(x, y);
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
      });
      ctx.closePath();
    }
    ctx.stroke();
    if (selected) {
      ctx.fillStyle = (ROLE_COLOR[s.role] || '#64748b') + '22';
      ctx.fill();
    }
    // 标注：同心圆的标签放在左下角圆上，避免中心重叠
    let lx, ly;
    if (s.kind === 'circle') {
      const a = -Math.PI * 0.75;
      [lx, ly] = worldToScreen(s.cx + s.r * Math.cos(a),
                               s.cy + s.r * Math.sin(a));
      ly += 4;
    } else {
      const c = shapeCentroid(s);
      [lx, ly] = worldToScreen(c.x, c.y);
    }
    const label = s.role ? ROLE_MAP[s.role].label : '未标注';
    ctx.font = '600 11px system-ui, sans-serif';
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = 'rgba(2,6,23,0.82)';
    ctx.fillRect(lx + 4, ly - 16, tw + 8, 16);
    ctx.fillStyle = ROLE_COLOR[s.role] || '#94a3b8';
    ctx.fillText(label, lx + 8, ly - 4);
    ctx.restore();
  }

  function drawDraft() {
    ctx.save();
    ctx.strokeStyle = '#e2e8f0';
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    if (draft.mode === 'circle') {
      const [cx, cy] = worldToScreen(draft.cx, draft.cy);
      ctx.arc(cx, cy, Math.max(draft.r, 0) * view.scale, 0, Math.PI * 2);
    } else if (draft.mode === 'polygon') {
      draft.pts.forEach(([x, y], i) => {
        const [sx, sy] = worldToScreen(x, y);
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
      });
    } else if (draft.mode === 'rect') {
      const [sx0, sy0] = worldToScreen(draft.x0, draft.y0);
      const [sx1, sy1] = worldToScreen(draft.x1, draft.y1);
      ctx.rect(sx0, sy0, sx1 - sx0, sy1 - sy0);
    }
    ctx.stroke();
    ctx.restore();
  }

  function hitTest(wx, wy) {
    for (let i = shapes.length - 1; i >= 0; i--) {
      const s = shapes[i];
      if (s.kind === 'circle') {
        const dd = Math.abs(Math.hypot(wx - s.cx, wy - s.cy) - s.r);
        if (dd <= Math.max(0.6, 4 / view.scale)) return s;
      } else if (pointInPolygon(wx, wy, s.pts)) {
        return s;
      }
    }
    return null;
  }

  function pointInPolygon(x, y, pts) {
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i];
      const [xj, yj] = pts[j];
      if ((yi > y) !== (yj > y)
          && x < ((xj - xi) * (y - yi)) / ((yj - yi) || 1e-12) + xi) {
        inside = !inside;
      }
    }
    return inside;
  }

  function pointer(e) {
    const rect = d.canvas.getBoundingClientRect();
    return screenToWorld(e.clientX - rect.left, e.clientY - rect.top);
  }

  function refresh() {
    renderSide();
    draw();
    renderStatus();
  }

  function snapshot() {
    return JSON.stringify({ shapes, nextId, selectedId });
  }
  function pushUndo() {
    undoStack.push(snapshot());
    if (undoStack.length > 60) undoStack.shift();
    redoStack.length = 0;
  }
  function restoreSnapshot(str) {
    const o = JSON.parse(str);
    shapes = o.shapes; nextId = o.nextId; selectedId = o.selectedId;
    draft = null; baseline = null;
    refresh();
  }
  function undo() {
    if (!undoStack.length) return;
    redoStack.push(snapshot());
    restoreSnapshot(undoStack.pop());
  }
  function redo() {
    if (!redoStack.length) return;
    undoStack.push(snapshot());
    restoreSnapshot(redoStack.pop());
  }

  function addShape(shape) {
    shape.id = nextId++;
    shapes.push(shape);
    selectedId = shape.id;
    return shape;
  }

  function selectedShape() {
    return shapes.find((s) => s.id === selectedId) || null;
  }

  function onPointerDown(e) {
    if (!active) return;
    d.canvas.setPointerCapture && d.canvas.setPointerCapture(e.pointerId);
    const [wx, wy] = pointer(e);
    if (mode === 'drawing') {
      panning = { x: e.clientX, y: e.clientY, panX: view.panX, panY: view.panY };
      return;
    }
    if (e.button === 2 || e.button === 1 || e.shiftKey) {
      panning = { x: e.clientX, y: e.clientY, panX: view.panX, panY: view.panY };
      return;
    }
    if (tool === 'select') {
      const hit = hitTest(wx, wy);
      selectedId = hit ? hit.id : null;
      refresh();
      return;
    }
    if (tool === 'delete') {
      const hit = hitTest(wx, wy);
      if (hit) {
        pushUndo();
        shapes = shapes.filter((s) => s.id !== hit.id);
        if (selectedId === hit.id) selectedId = null;
      }
      refresh();
      return;
    }
    if (tool === 'circle') {
      draft = { mode: 'circle', cx: snap(wx), cy: snap(wy), r: 0 };
      refresh();
    } else if (tool === 'rect') {
      draft = { mode: 'rect', x0: snap(wx), y0: snap(wy), x1: wx, y1: wy };
      refresh();
    } else if (tool === 'polygon') {
      if (!draft || draft.mode !== 'polygon') draft = { mode: 'polygon', pts: [] };
      draft.pts.push([snap(wx), snap(wy)]);
      refresh();
    }
  }

  function onPointerMove(e) {
    if (!active) return;
    const [wx, wy] = pointer(e);
    if (panning) {
      view.panX = panning.panX + (e.clientX - panning.x);
      view.panY = panning.panY + (e.clientY - panning.y);
      draw();
      return;
    }
    if (draft && draft.mode === 'circle') {
      draft.r = Math.max(0.5, Math.hypot(snap(wx) - draft.cx, snap(wy) - draft.cy));
      refresh();
    } else if (draft && draft.mode === 'rect') {
      draft.x1 = snap(wx); draft.y1 = snap(wy);
      refresh();
    } else {
      renderStatus(wx, wy);
    }
  }

  function onPointerUp() {
    if (!active) return;
    if (mode === 'drawing') { panning = null; return; }
    if (panning) { panning = null; return; }
    if (!draft) return;
    if (draft.mode === 'circle') {
      if (draft.r >= 0.5) {
        pushUndo();
        addShape({ kind: 'circle', cx: draft.cx, cy: draft.cy, r: draft.r, role: null });
      }
    } else if (draft.mode === 'rect') {
      const x0 = Math.min(draft.x0, draft.x1), x1 = Math.max(draft.x0, draft.x1);
      const y0 = Math.min(draft.y0, draft.y1), y1 = Math.max(draft.y0, draft.y1);
      if (x1 - x0 >= 0.5 && y1 - y0 >= 0.5) {
        pushUndo();
        addShape({ kind: 'polygon', pts: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], role: null });
      }
    } else if (draft.mode === 'polygon') {
      // 继续点；闭合由双击/回车处理
      draw();
      return;
    }
    draft = null;
    refresh();
  }

  function closePolygon() {
    if (draft && draft.mode === 'polygon' && draft.pts.length >= 3) {
      pushUndo();
      addShape({ kind: 'polygon', pts: draft.pts.slice(), role: null });
      draft = null;
      refresh();
    }
  }

  function onDblClick() { closePolygon(); }

  function onWheel(e) {
    if (!active) return;
    e.preventDefault();
    const [mx, my] = screenToWorld(
      e.clientX - d.canvas.getBoundingClientRect().left,
      e.clientY - d.canvas.getBoundingClientRect().top);
    const before = view.scale;
    view.scale = Math.max(0.6, Math.min(80, view.scale * (e.deltaY < 0 ? 1.15 : 0.87)));
    const [sx, sy] = worldToScreen(mx, my);
    const [sx2, sy2] = worldToScreen(mx, my);
    view.panX += sx2 - sx; view.panY += sy2 - sy;
    // 保持鼠标下世界点不动
    const w = d.canvas.clientWidth, h = d.canvas.clientHeight;
    view.panX = e.clientX - d.canvas.getBoundingClientRect().left - w / 2 - mx * view.scale;
    view.panY = e.clientY - d.canvas.getBoundingClientRect().top - h / 2 + my * view.scale;
    if (before !== view.scale) draw();
  }

  function zoomBy(factor) {
    const w = d.canvas.clientWidth, h = d.canvas.clientHeight;
    const [cx, cy] = screenToWorld(w / 2, h / 2);
    view.scale = Math.max(0.6, Math.min(80, view.scale * factor));
    view.panX = w / 2 - cx * view.scale;
    view.panY = h - h / 2 + cy * view.scale - h;
    // 保持画布中心世界点不动：sy = h/2 + panY − y·scale = h/2
    view.panX = w / 2 - cx * view.scale;
    view.panY = cy * view.scale;
    draw();
  }

  function onKey(e) {
    if (!active) return;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
      e.preventDefault(); if (e.shiftKey) redo(); else undo(); return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || e.key === 'Y')) {
      e.preventDefault(); redo(); return;
    }
    if (e.key === 'f' || e.key === 'F') {
      fitView(); draw(); return;
    }
    if (e.key === '+' || e.key === '=') { zoomBy(1.2); return; }
    if (e.key === '-' || e.key === '_') { zoomBy(1 / 1.2); return; }
    if (mode === 'drawing') return;
    if (e.key === 'Escape') {
      if (draft) draft = null; else selectedId = null;
      refresh();
    } else if (e.key === 'Enter') {
      closePolygon();
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      if (selectedId != null) {
        shapes = shapes.filter((s) => s.id !== selectedId);
        selectedId = null;
        refresh();
      }
    }
  }

  function setDesignMode(m) {
    mode = m === 'drawing' ? 'drawing' : 'sketch';
    draft = null; panning = null;
    if (d.tabSketch) d.tabSketch.classList.toggle('active', mode === 'sketch');
    if (d.tabDrawing) d.tabDrawing.classList.toggle('active', mode === 'drawing');
    if (d.canvas) d.canvas.classList.toggle('drawing', mode === 'drawing');
    if (d.drawingPanel) d.drawingPanel.hidden = mode !== 'drawing';
    if (d.toolbar) d.toolbar.style.display = mode === 'drawing' ? 'none' : '';
    // 不重置 baseline：切换 tab 不应丢失"从当前参数生成"的精确回写
    requestAnimationFrame(() => { sizeCanvas(); fitView(); refresh(); });
  }

  function pistonOf() {
    return options.piston || {};
  }

  function recognitionOpts() {
    return {
      n_ports: d.n ? Number(d.n.value) : 6,
      h_deck: d.deck ? Number(d.deck.value) : 0.10,
    };
  }

  function currentRecognized() {
    if (baseline && JSON.stringify(shapes) === baseline.shapeSnap) {
      return baseline.res;
    }
    return extractParams(shapes, recognitionOpts());
  }

  function exactPistonParams(p) {
    return {
      d_piston_mm: Number(p.d_piston_mm),
      d_rod_mm: Number(p.d_rod_mm),
      n_ports: Math.max(1, Math.round(Number(p.n_ports) || 6)),
      port_width_mm: Number(p.port_width_mm),
      port_radial_mm: Number(p.port_radial_mm),
      port_radius_mm: Number(p.port_radius_mm),
      clamp_diameter_mm: Number(p.clamp_diameter_mm),
      shim_outer_diameter_mm: Number(p.shim_outer_diameter_mm),
      deck_height_mm: Number(p.deck_height_mm),
      bypass_d_mm: Number(p.bypass_d_mm) || 0,
      bypass_n: Number(p.bypass_n) || 0,
    };
  }

  function shapesFromPiston(p) {
    const out = [];
    let id = 1;
    const circ = (r, role) => {
      if (r > 0) out.push({ id: id++, kind: 'circle', cx: 0, cy: 0, r, role });
    };
    circ(Number(p.d_piston_mm) / 2, 'piston_outer');
    circ(Number(p.d_rod_mm) / 2, 'rod');
    circ(Number(p.clamp_diameter_mm) / 2, 'clamp');
    circ(Number(p.shim_outer_diameter_mm) / 2, 'shim_outer');
    const rp = Number(p.port_radius_mm), w = Number(p.port_width_mm);
    const dr = Number(p.port_radial_mm);
    if (rp > 0 && w > 0 && dr > 0) {
      out.push({ id: id++, kind: 'polygon', role: 'window',
        pts: [[rp - dr / 2, -w / 2], [rp + dr / 2, -w / 2],
              [rp + dr / 2, w / 2], [rp - dr / 2, w / 2]] });
    }
    const bd = Number(p.bypass_d_mm) || 0;
    if (bd > 0) {
      const rb = ((Number(p.clamp_diameter_mm) || 18) / 2 + rp) / 2;
      out.push({ id: id++, kind: 'circle', cx: rb, cy: 0, r: bd / 2, role: 'bypass' });
    }
    nextId = id;
    return out;
  }

  function shapesFromCurrent() {
    const p = pistonOf();
    const base = exactPistonParams(p);
    const generated = shapesFromPiston(base);
    const res = extractParams(generated, recognitionOpts());
    res.params = Object.assign({}, res.params, base);
    res.missing = [];
    res.issues = [];
    pushUndo();
    shapes = generated;
    selectedId = null;
    draft = null;
    baseline = { shapeSnap: JSON.stringify(shapes), res };
    fitView();
    refresh();
  }

  function buildCurrentDrawing() {
    const base = Object.assign({}, pistonOf());
    const res = currentRecognized();
    if (res && res.params) {
      for (const [k, v] of Object.entries(res.params)) {
        if (v !== undefined && v !== null) base[k] = v;
      }
    }
    base.t_piston_mm = d.tp ? Number(d.tp.value) : 12;
    if (d.bypassR) base.bypass_r_mm = Number(d.bypassR.value);
    return global.ValveDrawing.build(Object.assign(base, {
      layers: options.layers || [],
    }));
  }

  function drawEngineering() {
    const w = d.canvas.clientWidth, h = d.canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    try {
      const dwg = buildCurrentDrawing();
      global.ValveDrawing.paint(ctx, dwg, worldToScreen, view.scale);
    } catch (err) {
      ctx.fillStyle = '#b91c1c';
      ctx.font = '13px system-ui, sans-serif';
      ctx.fillText('工程图生成失败: ' + (err.message || err), 18, 28);
    }
  }

  function downloadFile(name, content, type) {
    const blob = new Blob([content], { type });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function exportSVG() {
    const dwg = buildCurrentDrawing();
    downloadFile('valve-body.svg', global.ValveDrawing.toSVG(dwg),
                 'image/svg+xml;charset=utf-8');
  }

  function exportPNG() {
    const dwg = buildCurrentDrawing();
    const dataUrl = global.ValveDrawing.toPNG(dwg, 12);
    const a = document.createElement('a');
    a.href = dataUrl;
    a.download = 'valve-body.png';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function setTool(t) {
    tool = t;
    draft = null;
    if (t !== 'select' && t !== 'delete') selectedId = null;
    d.toolbar.querySelectorAll('[data-tool]').forEach((b) => {
      b.classList.toggle('active', b.dataset.tool === t);
    });
    renderStatus();
    draw();
  }

  function renderStatus(wx, wy) {
    if (!d.status) return;
    if (mode === 'drawing') {
      d.status.textContent = '工程图：滚轮缩放、拖动平移；尺寸/标题栏随参数实时更新 · ' +
        `比例 ${view.scale.toFixed(1)} px/mm`;
      return;
    }
    const tips = {
      select: '选择：点击图元选中并标注（同心圆建议以原点为圆心）；拖动空白平移，滚轮缩放',
      circle: '画圆：从圆心按下拖到半径处松开；标注为同心圆角色时建议圆心在原点',
      polygon: '多段线：依次点击顶点；双击 / 回车闭合（≥3 点）',
      rect: '矩形窗口：对角拖动生成闭合轮廓（用于腰形/矩形过油窗）',
      delete: '删除：点击图元删除',
    };
    let txt = tips[tool] || '';
    if (wx != null) txt += ` · 光标 (${wx.toFixed(2)}, ${wy.toFixed(2)}) mm`;
    txt += ` · 比例 ${view.scale.toFixed(1)} px/mm · 共 ${shapes.length} 个图元`;
    d.status.textContent = txt;
  }

  function roleOptions(selectedRole) {
    return ['<option value="">未标注</option>'].concat(
      ROLES.map((r) => `<option value="${r.id}" ${selectedRole === r.id ? 'selected' : ''}>${r.label}</option>`)
    ).join('');
  }

  function renderSide() {
    const s = selectedShape();
    if (d.drawingPanel) d.drawingPanel.hidden = mode !== 'drawing';
    if (d.annotatePanel) d.annotatePanel.hidden = mode === 'drawing';
    if (d.guidePanel) d.guidePanel.hidden = mode === 'drawing';
    if (d.paramsPanel) d.paramsPanel.hidden = false;
    if (mode === 'drawing') {
      const resD = currentRecognized();
      if (d.sub) {
        d.sub.textContent = '工程图：半剖视图 · 参数取自草图识别（或当前求解输入），可导出 SVG/PNG';
      }
      if (d.apply) {
        const blocked = resD.missing.length > 0 || resD.issues.length > 0;
        d.apply.disabled = blocked;
        d.apply.title = blocked ? '草图参数不完整，无法写回；可直接返回求解' : '';
      }
      if (d.params) {
        const p = resD.params || {};
        const rowsD = [
          ['活塞直径', p.d_piston_mm, 'mm'],
          ['杆直径', p.d_rod_mm, 'mm'],
          ['夹持直径', p.clamp_diameter_mm, 'mm'],
          ['最大阀片直径', p.shim_outer_diameter_mm, 'mm'],
          ['窗口中心半径', p.port_radius_mm, 'mm'],
          ['窗口个数 N', p.n_ports, '个'],
        ];
        d.params.innerHTML = rowsD.map(([k, v, u]) =>
          `<tr><td>${k}</td><td>${v == null ? '—' : Number(v).toFixed(2)}</td><td>${u}</td></tr>`
        ).join('');
      }
      return;
    }
    if (!d.selected) return;
    if (!s) {
      d.selected.innerHTML = '<p class="design-empty">未选中图元。用左侧工具绘制，或切到选择模式点击图元。</p>';
    } else {
      const c = shapeCentroid(s);
      const geo = s.kind === 'circle'
        ? `圆：圆心 (${s.cx.toFixed(2)}, ${s.cy.toFixed(2)})，R=${s.r.toFixed(2)} mm`
        : `闭合轮廓：${s.pts.length} 点，质心 (${c.x.toFixed(2)}, ${c.y.toFixed(2)}) mm`;
      d.selected.innerHTML = `
        <p class="design-shape-info">${geo}</p>
        <label class="field"><span>标注为</span>
          <select id="designRole">${roleOptions(s.role)}</select></label>
        <button class="btn ghost tiny" id="designDeleteShape" type="button">删除该图元</button>`;
      $('designRole').addEventListener('change', (ev) => {
        pushUndo();
        baseline = null;
        const role = ev.target.value || null;
        if (role) {
          const other = shapes.find((x) => x.id !== s.id && x.role === role);
          if (other) other.role = null;
        }
        s.role = role;
        refresh();
      });
      $('designDeleteShape').addEventListener('click', () => {
        pushUndo();
        shapes = shapes.filter((x) => x.id !== s.id);
        selectedId = null;
        refresh();
      });
    }

    // 参数表 + 引导
    const res = currentRecognized();
    const rows = [
      ['活塞直径', res.params.d_piston_mm, 'mm'],
      ['杆直径', res.params.d_rod_mm, 'mm'],
      ['夹持直径', res.params.clamp_diameter_mm, 'mm'],
      ['最大阀片直径', res.params.shim_outer_diameter_mm, 'mm'],
      ['窗口中心半径', res.params.port_radius_mm, 'mm'],
      ['窗口周向弧长', res.params.port_width_mm, 'mm'],
      ['窗口径向宽度', res.params.port_radial_mm, 'mm'],
      ['窗口个数 N', res.params.n_ports, '个'],
      ['初始间隙 h_deck', res.params.deck_height_mm, 'mm'],
    ];
    if (d.params) {
      d.params.innerHTML = rows.map(([k, v, u]) =>
        `<tr><td>${k}</td><td>${v == null ? '—' : Number(v).toFixed(2)}</td><td>${u}</td></tr>`
      ).join('') + (res.issues.length
        ? `<tr class="design-issue"><td colspan="3">${res.issues.join('；')}</td></tr>` : '');
    }
    if (d.guide) {
      d.guide.innerHTML = res.guide.map((g) => `
        <li class="${g.ok ? 'ok' : 'todo'}">
          <span class="guide-mark">${g.ok ? '✓' : '○'}</span>
          <div><b>${g.label}</b>${g.value ? `<em>${g.value}</em>` : ''}
          ${g.ok ? '' : `<small>${g.hint}</small>`}</div>
        </li>`).join('');
    }
    if (d.apply) {
      const blocked = res.missing.length > 0 || res.issues.length > 0;
      d.apply.disabled = blocked;
      d.apply.title = res.missing.length
        ? `还缺：${res.missing.join('、')}`
        : (res.issues.length ? `请先修正：${res.issues.join('；')}` : '');
    }
    if (d.sub) {
      d.sub.textContent = res.missing.length
        ? `还需标注：${res.missing.join('、')}`
        : '关键参数已识别，可应用并返回求解';
    }
    d._last = res;
  }

  function open(opt) {
    ensureDom();
    options = opt || {};
    active = true;
    shapes = [];
    nextId = 1;
    selectedId = null;
    tool = 'select';
    draft = null;
    baseline = null;
    undoStack = [];
    redoStack = [];
    mode = 'sketch';
    d.overlay.classList.remove('hidden');
    d.overlay.setAttribute('aria-hidden', 'false');
    if (d.n && options.piston) {
      d.n.value = Math.round(options.piston.n_ports || 6);
    }
    if (d.deck && options.piston && options.piston.deck_height_mm != null) {
      d.deck.value = options.piston.deck_height_mm;
    }
    d.toolbar.querySelectorAll('[data-tool]').forEach((b) => {
      b.classList.toggle('active', b.dataset.tool === 'select');
    });
    if (d.tp) d.tp.value = options.tPistonMm != null ? options.tPistonMm : 12;
    if (d.bypassR && options.piston) {
      const p = options.piston;
      const mid = ((Number(p.clamp_diameter_mm) || 18) / 2 +
                   (Number(p.port_radius_mm) || 16)) / 2;
      d.bypassR.value = Number(mid.toFixed(2));
    }
    setDesignMode('sketch');
    attach();
    requestAnimationFrame(() => {
      sizeCanvas();
      fitView();
      refresh();
    });
  }

  function close() {
    active = false;
    if (d) {
      d.overlay.classList.add('hidden');
      d.overlay.setAttribute('aria-hidden', 'true');
    }
    if (options.onClose) options.onClose();
  }

  function attach() {
    if (bound) return;
    bound = true;
    d.toolbar.addEventListener('click', (e) => {
      const b = e.target.closest('[data-tool]');
      if (b) setTool(b.dataset.tool);
    });
    d.canvas.addEventListener('pointerdown', onPointerDown);
    d.canvas.addEventListener('pointermove', onPointerMove);
    d.canvas.addEventListener('pointerup', onPointerUp);
    d.canvas.addEventListener('pointercancel', onPointerUp);
    d.canvas.addEventListener('dblclick', onDblClick);
    d.canvas.addEventListener('wheel', onWheel, { passive: false });
    d.canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', () => { if (active) { sizeCanvas(); fitView(); draw(); } });
    if (d.close) d.close.addEventListener('click', close);
    if (d.fit) d.fit.addEventListener('click', () => { fitView(); draw(); });
    if (d.clear) d.clear.addEventListener('click', () => {
      pushUndo();
      shapes = []; selectedId = null; draft = null; baseline = null;
      refresh();
    });
    if (d.demo) d.demo.addEventListener('click', () => {
      pushUndo();
      shapes = demoShapes(); selectedId = null; draft = null; baseline = null;
      fitView(); refresh();
    });
    if (d.fromCurrent) d.fromCurrent.addEventListener('click', shapesFromCurrent);
    if (d.tabSketch) d.tabSketch.addEventListener('click', () => setDesignMode('sketch'));
    if (d.tabDrawing) d.tabDrawing.addEventListener('click', () => setDesignMode('drawing'));
    if (d.undo) d.undo.addEventListener('click', undo);
    if (d.redo) d.redo.addEventListener('click', redo);
    if (d.zoomIn) d.zoomIn.addEventListener('click', () => zoomBy(1.2));
    if (d.zoomOut) d.zoomOut.addEventListener('click', () => zoomBy(1 / 1.2));
    if (d.exportSvg) d.exportSvg.addEventListener('click', exportSVG);
    if (d.exportPng) d.exportPng.addEventListener('click', exportPNG);
    if (d.tp) d.tp.addEventListener('input', () => { if (mode === 'drawing') draw(); });
    if (d.bypassR) d.bypassR.addEventListener('input', () => { if (mode === 'drawing') draw(); });
    if (d.n) d.n.addEventListener('input', refresh);
    if (d.deck) d.deck.addEventListener('input', refresh);
    if (d.apply) {
      d.apply.addEventListener('click', () => {
        const res = currentRecognized();
        if (res.missing.length || res.issues.length) return;
        const params = res.params;
        if (options.onApply) options.onApply(params);
        close();
      });
    }
  }

  const api = { ROLES, ROLE_MAP, extractParams, demoShapes, windowGeometry,
                shapeCentroid, shapeRadialRange, shapeAngularSpan, open, close, snap,
                shapesFromPiston, setDesignMode, buildCurrentDrawing };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.ValveDesign = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
