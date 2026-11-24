'use strict';

/**
 * FlowSolver 桌面端渲染逻辑：单点求解（挠度场 → 压力场）与耦合迭代（Q→w→P→w′）。
 * 所有数值来自后端同一次求解，前端只做展示。
 */

const $ = (id) => document.getElementById(id);

const state = {
  baseUrl: null,
  appInfo: null,
  mode: 'single',
  layers: [
    { din: 18, dout: 42, t: 0.25 },
    { din: 18, dout: 36, t: 0.20 },
    { din: 18, dout: 30, t: 0.20 },
  ],
  last: null,
  ready: false,
  error: null,
  charts: {},
  hidden: { c1: new Set(), c2: new Set() },
  debounce: null,
  running: false,
  queued: false,
  autoRun: true,
  timer: null,
  started: 0,
  project: {
    path: null,
    createdAt: null,
    reserved: {},
    extra: {},
    schemes: { shim_stacks: [], restrictors: [] },
    active: { shim_stack: null, restrictor: null },
  },
};

window.__shimtunerState = () => ({
  ready: state.ready,
  error: state.error,
  solver: state.mode,
  layerCount: state.layers.length,
  backend: state.baseUrl,
  headline: state.last ? state.last.headline : null,
  elapsedMs: state.last ? state.last.elapsed_ms : null,
  kpis: state.last && state.last.report
    ? state.last.report.kpis.map((k) => k.value) : [],
});

/* ------------------------------------------------------------------ */
/* 初始化                                                              */
/* ------------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', init);

async function init() {
  state.charts = {
    c1: new LineChart($('c1Canvas'), $('c1Tip')),
    c2: new LineChart($('c2Canvas'), $('c2Tip')),
  };
  wireUi();
  renderLayers();
  setMode('single', false);
  setBackendStatus({ status: 'starting', message: '正在连接数值后端…' });

  try {
    await resolveBackend();
    await loadConfig();
    applyDefaults();
    ensureSchemes();
    updateProjectBadge(null, '');
    refreshProjectList();
    setBackendStatus({ status: 'running', message: state.appInfo?.message || '数值后端就绪' });
    await runAnalysis(true);
  } catch (err) {
    state.error = err.message || String(err);
    showError(state.error);
    setBackendStatus({ status: 'error', message: state.error });
  }
}

async function resolveBackend() {
  const bridge = window.shimtuner;
  if (bridge) {
    state.appInfo = await bridge.getBackendInfo();
    if (state.appInfo.app?.version) {
      $('versionBadge').textContent = `v${state.appInfo.app.version}`;
    }
    bridge.onBackendStatus((payload) => {
      state.appInfo = { ...state.appInfo, ...payload };
      setBackendStatus(payload);
      if (payload.status === 'running' && payload.url) state.baseUrl = payload.url;
    });
    let info = state.appInfo;
    if (info.status !== 'running' || !info.url) info = await waitForBackendRunning(bridge);
    state.baseUrl = info.url;
    setBackendStatus(info);
    return;
  }
  const params = new URLSearchParams(location.search);
  state.baseUrl = (params.get('backend') || 'http://127.0.0.1:8761').replace(/\/$/, '');
  setBackendStatus({ status: 'running', message: `浏览器预览 · ${state.baseUrl}` });
}

function waitForBackendRunning(bridge) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = setInterval(async () => {
      try {
        const info = await bridge.getBackendInfo();
        if (info.status === 'running' && info.url) {
          clearInterval(timer);
          resolve(info);
        } else if (Date.now() - started > 60000) {
          clearInterval(timer);
          reject(new Error(info.message || '后端启动超时'));
        }
      } catch (err) {
        clearInterval(timer);
        reject(err);
      }
    }, 400);
  });
}

async function loadConfig() {
  const res = await fetch(`${state.baseUrl}/api/config`);
  if (!res.ok) throw new Error(`配置读取失败 (${res.status})`);
  state.config = await res.json();
}

function applyDefaults() {
  const cfg = state.config?.preset || {};
  const p = cfg.piston || {};
  $('pD').value = p.d_piston_mm ?? 46;
  $('pRod').value = p.d_rod_mm ?? 18;
  $('pN').value = p.n_ports ?? 6;
  $('pW').value = p.port_width_mm ?? 8;
  $('pRad').value = p.port_radial_mm ?? 4;
  $('pR').value = p.port_radius_mm ?? 16;
  $('pSeat').value = p.clamp_diameter_mm ?? (2 * (p.seat_radius_mm ?? 9));
  $('pDeck').value = p.deck_height_mm ?? 0.10;
  $('pOuter').value = p.shim_outer_diameter_mm ?? (2 * (p.shim_outer_mm ?? 21));
  $('pBypD').value = p.bypass_d_mm ?? 0;
  $('pBypN').value = p.bypass_n ?? 0;

  const f = cfg.fluid || {};
  $('fRho').value = f.rho ?? 850;
  $('fCst').value = f.cSt ?? 15;
  $('fBack').value = f.p_back_MPa ?? 0;
  $('fVap').value = f.p_vapor_kPa ?? 0;
  $('fT').value = f.T_C ?? 40;
  $('fKv40').value = f.KV40 ?? 0;
  $('fKv100').value = f.KV100 ?? 0;
  $('fVI').value = f.VI ?? 0;

  const c = cfg.coefficients || {};
  $('cCd').value = c.C_d ?? 0.70;
  $('cCf').value = c.C_f ?? 0.30;
  $('cBypCd').value = p.bypass_Cd ?? 0.62;

  const g = cfg.gap || {};
  $('gLift').value = g.lift_mm ?? 0.15;
  $('gPower').value = g.power ?? 2.0;

  const o = cfg.operating || {};
  $('oVmax').value = o.v_max_mps ?? 0.5;
  $('oN').value = o.n_points ?? 21;
  $('oV').value = (o.v_max_mps ?? 0.5) * 0.5;

  const it = cfg.iterate || {};
  $('itV').value = it.v_mps ?? 0.15;
  $('itAlpha').value = it.alpha ?? 0.5;
  $('itTol').value = it.tol_um ?? 2.0;
  $('itMax').value = it.max_iter ?? 25;
  $('itNr').value = it.n_radial ?? 16;
  $('itNt').value = it.n_theta ?? 12;
  $('itNl').checked = Boolean(it.nl);
  $('secLoad').checked = Boolean(it.window_load || it.sector_load);

  const cv = cfg.curve || {};
  $('cvMin').value = cv.v_min_mps ?? 0.02;
  $('cvMax').value = cv.v_max_mps ?? 0.5;
  $('cvPts').value = cv.n_points ?? 6;
  document.querySelectorAll('input[name="rodValve"]').forEach((el) => {
    el.checked = el.value === 'mid';
  });
  $('rdMin').value = cv.v_min_mps ?? 0.02;
  $('rdMax').value = cv.v_max_mps ?? 0.5;
  $('rdPts').value = cv.n_points ?? 6;

  if (Array.isArray(cfg.layers) && cfg.layers.length) {
    state.layers = cfg.layers.map((l) => ({
      din: l.d_in_mm, dout: l.d_out_mm, t: l.t_mm,
    }));
    renderLayers();
  }
}

/* ------------------------------------------------------------------ */
/* 交互                                                                */
/* ------------------------------------------------------------------ */
function wireUi() {
  document.querySelectorAll('.solver-btn[data-mode]').forEach((btn) => {
    btn.addEventListener('click', () => setMode(btn.dataset.mode));
  });
  $('addLayerBtn').addEventListener('click', () => {
    // 不添加预设阀片：沿用已填写的内径（取最后一片；空表则取第一片；再兜底 18）
    const last = state.layers[state.layers.length - 1] || {};
    const first = state.layers[0] || {};
    const din = Number(last.din ?? first.din);
    const dout = Number(last.dout ?? first.dout);
    state.layers.push({
      din: Number.isFinite(din) && din > 0 ? din : 18,
      dout: Number.isFinite(dout) && dout > 0 ? dout : 30,
      t: Number.isFinite(Number(last.t)) && Number(last.t) > 0
        ? Number(last.t) : 0.2,
    });
    renderLayers();
    scheduleRun();
  });
  document.querySelectorAll('input, select').forEach((el) => {
    el.addEventListener('input', () => {
      if (el.id === 'autoRun') {
        state.autoRun = el.checked;
        return;
      }
      if (el.id === 'importFile') return;
      if (el.id && el.id.startsWith('proj')) return;
      if (state.autoRun) scheduleRun();
    });
  });
  $('runBtn').addEventListener('click', () => runAnalysis(true));
  $('restartBackendBtn').addEventListener('click', restartBackend);
  $('copyBtn').addEventListener('click', copyReport);
  $('importBtn').addEventListener('click', () => $('importFile').click());
  $('exportBtn').addEventListener('click', exportCase);
  $('importFile').addEventListener('change', importCaseFile);
  // 阀体设计模式：草图 → 标注 → 识别参数 → 写回输入框
  $('designBtn').addEventListener('click', () => {
    if (!window.ValveDesign) {
      hideError();
      showError(new Error('design.js 未加载（检查文件是否存在）'));
      return;
    }
    window.ValveDesign.open({
      piston: pistonInput(),
      layers: state.layers.map((l) => ({
        d_in_mm: Number(l.din), d_out_mm: Number(l.dout), t_mm: Number(l.t),
      })),
      onApply: (params) => {
        applyPistonParams(params);
        if (state.autoRun) scheduleRun();
      },
    });
  });
  wireProjectUi();
  $('errorClose').addEventListener('click', () => hideError());
  $('retryBtn').addEventListener('click', () => { hideError(); runAnalysis(true); });
  document.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
      ev.preventDefault();
      runAnalysis(true);
    }
  });
}

function setMode(mode, doRun = true) {
  state.mode = mode;
  document.querySelectorAll('.solver-btn[data-mode]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });
  const single = mode === 'single';
  const curve = mode === 'curve';
  const rod = mode === 'rod';
  $('gapPanel').classList.toggle('hidden', !single);
  $('opPanel').classList.toggle('hidden', !single);
  $('layerPanel').classList.toggle('hidden', single);
  $('iterPanel').classList.toggle('hidden', single);
  $('curvePanel').classList.toggle('hidden', !curve);
  $('rodPanel').classList.toggle('hidden', !rod);
  // 杆速模式下 Q(v) 为纯线性换算，不单独成图：F(v) 单图占满
  $('c2Card').classList.toggle('hidden', rod);
  $('chartsGrid').classList.toggle('single', rod);
  $('modeDesc').textContent = single
    ? '输入挠度场与活塞速度，求流道压力场与阻尼力。'
    : curve
      ? '沿速度序列逐点执行 Q→w→P→w′ 闭环迭代，输出 Q–F 曲线。'
      : rod
        ? 'Q 由杆速决定：Mid-valve Q=(A_p−A_rod)·v；Base-valve Q=A_rod·v（杆侵入体积）。'
        : '给定阀片堆叠与流量，迭代 Q→w→P→w′ 直到残差收敛。';
  if (doRun) runAnalysis(true);
}

/* ------------------------------------------------------------------ */
/* 请求                                                                */
/* ------------------------------------------------------------------ */
function num(id) { return Number($(id).value); }

function pistonInput() {
  return {
    d_piston_mm: num('pD'),
    d_rod_mm: num('pRod'),
    n_ports: Math.round(num('pN')),
    port_width_mm: num('pW'),
    port_radial_mm: num('pRad'),
    port_radius_mm: num('pR'),
    clamp_diameter_mm: num('pSeat'),
    deck_height_mm: num('pDeck'),
    shim_outer_diameter_mm: num('pOuter'),
    bypass_d_mm: num('pBypD'),
    bypass_n: Math.round(num('pBypN')),
    bypass_Cd: num('cBypCd'),
  };
}

/** 把「阀体设计」识别出的参数写回活塞/窗口输入框 */
function applyPistonParams(p) {
  const map = {
    pD: p.d_piston_mm, pRod: p.d_rod_mm, pN: p.n_ports,
    pW: p.port_width_mm, pRad: p.port_radial_mm, pR: p.port_radius_mm,
    pSeat: p.clamp_diameter_mm, pOuter: p.shim_outer_diameter_mm,
    pDeck: p.deck_height_mm,
  };
  for (const [id, val] of Object.entries(map)) {
    if (val !== undefined && val !== null && $(id)) $(id).value = val;
  }
  if (p.bypass_d_mm !== undefined && p.bypass_d_mm !== null && $('pBypD')) {
    $('pBypD').value = p.bypass_d_mm;
  }
  if (p.bypass_n !== undefined && p.bypass_n !== null && $('pBypN')) {
    $('pBypN').value = p.bypass_n;
  }
}

function fluidInput() {
  return {
    rho: num('fRho'), cSt: num('fCst'),
    p_back_MPa: num('fBack'), p_vapor_kPa: num('fVap'),
    T_C: num('fT'), KV40: num('fKv40'), KV100: num('fKv100'), VI: num('fVI'),
  };
}

function coefInput() { return { C_d: num('cCd'), C_f: num('cCf') }; }

function iterationInput() {
  return {
    alpha: num('itAlpha'),
    tol_um: num('itTol'),
    max_iter: Math.round(num('itMax')),
    relax_step_um: 150,
    nl: $('itNl').checked,
    n_radial: Math.round(num('itNr')),
    n_theta: Math.round(num('itNt')),
    window_load: $('secLoad').checked,
  };
}

function layersInput() {
  return state.layers.map((l) => ({
    d_in_mm: Number(l.din), d_out_mm: Number(l.dout), t_mm: Number(l.t),
  }));
}

function collect() {
  const base = {
    piston: pistonInput(), fluid: fluidInput(), coefficients: coefInput(),
  };
  if (state.mode === 'single') {
    return {
      ...base,
      gap: { mode: 'power_law', lift_mm: num('gLift'), power: num('gPower') },
      operating: {
        v_max_mps: num('oVmax'),
        n_points: Math.round(num('oN')),
        v_point_mps: num('oV'),
      },
      grid_points: 300,
    };
  }
  if (state.mode === 'curve') {
    return {
      ...base,
      layers: layersInput(),
      curve: {
        ...iterationInput(),
        v_min_mps: num('cvMin'),
        v_max_mps: num('cvMax'),
        n_points: Math.round(num('cvPts')),
      },
    };
  }
  if (state.mode === 'rod') {
    const layers = layersInput();
    const valveEl = document.querySelector('input[name="rodValve"]:checked');
    const body = {
      ...base,
      layers,
      valve_type: valveEl ? valveEl.value : 'mid',
      curve: {
        ...iterationInput(),
        v_min_mps: num('rdMin'),
        v_max_mps: num('rdMax'),
        n_points: Math.round(num('rdPts')),
      },
    };
    if ($('rdRebound').checked) body.layers_rebound = layers;
    return body;
  }
  return {
    ...base,
    layers: layersInput(),
    iterate: { v_mps: num('itV'), ...iterationInput() },
  };
}

function scheduleRun(delay = 380) {
  if (!state.autoRun) return;
  clearTimeout(state.debounce);
  state.debounce = setTimeout(() => runAnalysis(false), delay);
}

async function runAnalysis(force) {
  if (!state.baseUrl) return;
  if (state.running) { state.queued = true; return; }
  if (!force && !state.autoRun) return;
  state.running = true;
  state.queued = false;
  state.ready = false;
  state.started = Date.now();
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    $('elapsedBadge').textContent = `计算中 ${((Date.now() - state.started) / 1000).toFixed(1)} s`;
  }, 200);
  $('runBtn').disabled = true;

  const path = state.mode === 'single' ? '/api/analyze'
    : state.mode === 'curve' ? '/api/fv-curve'
      : state.mode === 'rod' ? '/api/rod-curve' : '/api/iterate';
  try {
    const res = await fetch(`${state.baseUrl}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collect()),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.detail || `HTTP ${res.status}`);
    state.error = null;
    hideError();
    render(payload);
    state.ready = true;
  } catch (err) {
    state.error = err.message || String(err);
    showError(state.error);
  } finally {
    state.running = false;
    $('runBtn').disabled = false;
    clearInterval(state.timer);
    if (state.queued) { state.queued = false; runAnalysis(true); }
  }
}

/* ------------------------------------------------------------------ */
/* 渲染                                                                */
/* ------------------------------------------------------------------ */
function render(res) {
  state.last = res;
  $('headline').textContent = res.headline;
  $('subline').textContent = `${res.solver_label} · 后端 ${state.baseUrl}`;
  $('elapsedBadge').textContent = `${res.elapsed_ms.toFixed(0)} ms`;
  $('reportBadge').textContent = res.solver_label;
  $('reportTime').textContent = new Date().toLocaleTimeString();
  renderKpis(res.report.kpis);
  renderWarnings(res.report.warnings);
  $('reportLines').textContent = (res.report.lines || []).join('\n');
  renderNotes(res.report.notes || []);

  let chartC1;
  let chartC2;
  let badge1;
  let badge2;
  if (state.mode === 'single') {
    chartC1 = res.charts.pressure; chartC2 = res.charts.force;
    badge1 = 'p(r)'; badge2 = 'F(v)';
  } else if (state.mode === 'curve') {
    chartC1 = res.charts.curve; chartC2 = res.charts.convergence;
    badge1 = 'F(Q)'; badge2 = '逐点收敛';
  } else if (state.mode === 'rod') {
    // Q(v) 是纯线性换算，不再单独画图；F(v) 单图占满高度
    chartC1 = res.charts.force;
    badge1 = 'F(v)';
  } else {
    chartC1 = res.charts.convergence; chartC2 = res.charts.deflection;
    badge1 = '残差'; badge2 = 'w(r)';
  }
  renderChart('c1', chartC1, $('c1Title'));
  if (chartC2) renderChart('c2', chartC2, $('c2Title'));
  $('c1Badge').textContent = badge1;
  $('c2Badge').textContent = badge2 || '';
}

function renderKpis(kpis) {
  const wrap = $('kpis');
  wrap.innerHTML = '';
  for (const kpi of kpis) {
    const div = document.createElement('div');
    div.className = 'kpi';
    div.innerHTML = `<div class="k-label">${escapeHtml(kpi.label)}</div>` +
      `<div class="k-value">${escapeHtml(kpi.value)}</div>` +
      `<div class="k-hint">${escapeHtml(kpi.hint || '')}</div>`;
    wrap.appendChild(div);
  }
}

function renderWarnings(warnings) {
  const wrap = $('warnings');
  wrap.innerHTML = '';
  if (!warnings || warnings.length === 0) {
    wrap.classList.add('hidden');
    return;
  }
  wrap.classList.remove('hidden');
  for (const text of warnings) {
    const chip = document.createElement('span');
    chip.className = 'warn-chip';
    chip.textContent = text;
    wrap.appendChild(chip);
  }
}

function renderNotes(notes) {
  const ul = $('reportNotes');
  ul.innerHTML = '';
  for (const note of notes) {
    const li = document.createElement('li');
    li.textContent = note;
    ul.appendChild(li);
  }
}

function renderChart(key, chartData, titleEl) {
  if (!chartData) return;
  titleEl.textContent = chartData.title;
  const chart = state.charts[key];
  chart.setData(chartData);
  const legend = $(`${key}Legend`);
  legend.innerHTML = '';
  const hidden = state.hidden[key];
  const valid = new Set(chartData.series.map((s) => s.id));
  for (const id of [...hidden]) if (!valid.has(id)) hidden.delete(id);
  for (const series of chartData.series) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'legend-item' + (hidden.has(series.id) ? ' off' : '');
    const dash = ['dash', 'dot', 'dashdot'].includes(series.dash) ? 'dashed' : 'solid';
    btn.innerHTML =
      `<span class="legend-swatch" style="color:${series.color};` +
      `border-top-style:${dash}"></span><span>${escapeHtml(series.label)}</span>`;
    btn.addEventListener('click', () => {
      if (hidden.has(series.id)) hidden.delete(series.id);
      else hidden.add(series.id);
      btn.classList.toggle('off', hidden.has(series.id));
      chart.setHidden(new Set(hidden));
    });
    legend.appendChild(btn);
  }
  chart.setHidden(new Set(hidden));
}

/* ------------------------------------------------------------------ */
/* 阀片堆叠                                                            */
/* ------------------------------------------------------------------ */
function renderLayers() {
  const wrap = $('layerRows');
  wrap.innerHTML = '';
  state.layers.forEach((layer, index) => {
    const row = document.createElement('div');
    row.className = 'layer-row';
    row.innerHTML =
      `<span class="idx">${index + 1}</span>` +
      `<input type="number" step="1" min="1" value="${layer.din}">` +
      `<input type="number" step="1" min="1" value="${layer.dout}">` +
      `<input type="number" step="1" min="0" value="${layer.t}">` +
      `<button class="btn tiny ghost" type="button" title="删除">✕</button>`;
    const [din, dout, t] = row.querySelectorAll('input');
    const sync = () => {
      layer.din = Number(din.value);
      layer.dout = Number(dout.value);
      layer.t = Number(t.value);
      $('layerSummary').textContent =
        `${state.layers.length} 片 · 总厚 ${state.layers.reduce((a, l) => a + Number(l.t), 0).toFixed(2)} mm`;
      scheduleRun();
    };
    din.addEventListener('input', sync);
    dout.addEventListener('input', sync);
    t.addEventListener('input', sync);
    row.querySelector('button').addEventListener('click', () => {
      if (state.layers.length <= 1) return;
      state.layers.splice(index, 1);
      renderLayers();
      scheduleRun();
    });
    wrap.appendChild(row);
  });
  $('layerSummary').textContent =
    `${state.layers.length} 片 · 总厚 ${state.layers.reduce((a, l) => a + Number(l.t), 0).toFixed(2)} mm`;
}

/* ------------------------------------------------------------------ */
/* 导入 / 导出                                                         */
/* ------------------------------------------------------------------ */
function exportCase() {
  const payload = {
    schema: 'shimtuner-flow-case/1',
    mode: state.mode,
    input: collect(),
    summary: state.last ? {
      headline: state.last.headline,
      kpis: state.last.report.kpis,
      lines: state.last.report.lines,
    } : null,
  };
  const text = JSON.stringify(payload, null, 2);
  if (window.shimtuner?.saveText) {
    window.shimtuner.saveText('flow-case.json', text);
  } else {
    const blob = new Blob([text], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'flow-case.json';
    a.click();
  }
}

function importCaseFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      applyCase(data.input || data);
      hideError();
      runAnalysis(true);
    } catch (err) {
      showError(`导入失败: ${err.message}`);
    }
  };
  reader.readAsText(file, 'utf-8');
  event.target.value = '';
}

function applyCase(input) {
  const p = input.piston || {};
  const map = { pD: p.d_piston_mm, pRod: p.d_rod_mm, pN: p.n_ports, pW: p.port_width_mm,
    pRad: p.port_radial_mm, pR: p.port_radius_mm,
    pSeat: p.clamp_diameter_mm ?? (p.seat_radius_mm != null ? 2 * p.seat_radius_mm : undefined),
    pDeck: p.deck_height_mm,
    pOuter: p.shim_outer_diameter_mm ?? (p.shim_outer_mm != null ? 2 * p.shim_outer_mm : undefined),
    pBypD: p.bypass_d_mm, pBypN: p.bypass_n,
    cBypCd: p.bypass_Cd };
  const f = input.fluid || {};
  Object.assign(map, { fRho: f.rho, fCst: f.cSt, fBack: f.p_back_MPa,
    fVap: f.p_vapor_kPa, fT: f.T_C, fKv40: f.KV40, fKv100: f.KV100, fVI: f.VI });
  const c = input.coefficients || {};
  Object.assign(map, { cCd: c.C_d, cCf: c.C_f });
  for (const [id, val] of Object.entries(map)) {
    if (val !== undefined && val !== null && $(id)) $(id).value = val;
  }
  if (Array.isArray(input.layers) && input.layers.length) {
    state.layers = input.layers.map((l) => ({
      din: l.d_in_mm, dout: l.d_out_mm, t: l.t_mm,
    }));
    renderLayers();
  }
  if (input.gap) {
    if (input.gap.lift_mm !== undefined) $('gLift').value = input.gap.lift_mm;
    if (input.gap.power !== undefined) $('gPower').value = input.gap.power;
  }
  if (input.operating) {
    if (input.operating.v_max_mps !== undefined) $('oVmax').value = input.operating.v_max_mps;
    if (input.operating.n_points !== undefined) $('oN').value = input.operating.n_points;
    if (input.operating.v_point_mps !== undefined) $('oV').value = input.operating.v_point_mps;
  }
  if (input.iterate) {
    const it = input.iterate;
    const map2 = { itV: it.v_mps, itAlpha: it.alpha, itTol: it.tol_um,
      itMax: it.max_iter, itNr: it.n_radial, itNt: it.n_theta };
    for (const [id, val] of Object.entries(map2)) {
      if (val !== undefined && val !== null) $(id).value = val;
    }
    if (it.nl !== undefined) $('itNl').checked = Boolean(it.nl);
    if (it.window_load !== undefined || it.sector_load !== undefined)
      $('secLoad').checked = Boolean(it.window_load || it.sector_load);
  }
  if (input.curve) {
    const cv = input.curve;
    const map3 = { cvMin: cv.v_min_mps, cvMax: cv.v_max_mps,
      cvPts: cv.n_points, rdMin: cv.v_min_mps, rdMax: cv.v_max_mps,
      rdPts: cv.n_points, itAlpha: cv.alpha, itTol: cv.tol_um,
      itMax: cv.max_iter, itNr: cv.n_radial, itNt: cv.n_theta };
    for (const [id, val] of Object.entries(map3)) {
      if (val !== undefined && val !== null) $(id).value = val;
    }
    if (cv.nl !== undefined) $('itNl').checked = Boolean(cv.nl);
    if (cv.window_load !== undefined || cv.sector_load !== undefined)
      $('secLoad').checked = Boolean(cv.window_load || cv.sector_load);
  }
  const vt = input.valve_type || (input.curve && input.curve.valve_type);
  if (vt) setRodValve(vt);
}


/* ------------------------------------------------------------------ */
/* 避震项目 + 方案管理                                                 */
/* ------------------------------------------------------------------ */
const PROJECT_SCHEMA = 'shimtuner-shock-project/1';
const CASE_SCHEMA = 'shimtuner-flow-case/1';
const PROJECT_KNOWN = new Set([
  'name', 'vehicle', 'position', 'valve_type', 'notes',
  'created_at', 'updated_at', 'reserved',
]);

const clone = (v) => JSON.parse(JSON.stringify(v));
const basename = (p) => String(p || '').split(/[\\/]/).pop();
const ACTIVE_KEY = { shim_stacks: 'shim_stack', restrictors: 'restrictor' };

function setRodValve(vt) {
  const v = String(vt || 'mid').toLowerCase();
  const use = v === 'base' ? 'base' : 'mid';
  document.querySelectorAll('input[name="rodValve"]').forEach((el) => {
    el.checked = el.value === use;
  });
  if ($('projValve') && ['mid', 'base', 'both'].includes(v)) {
    $('projValve').value = v;
  }
}

function rodValveValue() {
  const el = document.querySelector('input[name="rodValve"]:checked');
  return el ? el.value : 'mid';
}

function projectMeta() {
  return {
    name: ($('projName')?.value || '').trim(),
    vehicle: ($('projVehicle')?.value || '').trim(),
    position: $('projPos')?.value || 'front',
    valve_type: $('projValve')?.value || 'mid',
    notes: ($('projNotes')?.value || '').trim(),
  };
}

function schemeId(prefix) {
  return prefix + '-' + Date.now().toString(36)
    + Math.random().toString(36).slice(2, 5);
}

function restrictorFromInputs() {
  const p = pistonInput();
  return {
    n_ports: p.n_ports,
    port_width_mm: p.port_width_mm,
    port_radial_mm: p.port_radial_mm,
    port_radius_mm: p.port_radius_mm,
    bypass_d_mm: p.bypass_d_mm,
    bypass_n: p.bypass_n,
    bypass_Cd: p.bypass_Cd,
  };
}

function schemes() { return state.project.schemes; }

function findScheme(kind, id) {
  return (schemes()[kind] || []).find((s) => s.id === id) || null;
}

function ensureSchemes() {
  const s = schemes();
  if (!s.shim_stacks.length) {
    s.shim_stacks.push({
      id: schemeId('stack'), name: '默认阀片组', layers: layersInput(),
    });
  }
  if (!s.restrictors.length) {
    s.restrictors.push({
      id: schemeId('restr'), name: '默认限流孔', piston: restrictorFromInputs(),
    });
  }
  if (!state.project.active.shim_stack) {
    state.project.active.shim_stack = s.shim_stacks[0].id;
  }
  if (!state.project.active.restrictor) {
    state.project.active.restrictor = s.restrictors[0].id;
  }
  renderSchemeLists();
}

function syncActiveScheme(kind) {
  const id = state.project.active[ACTIVE_KEY[kind]];
  const scheme = findScheme(kind, id);
  if (!scheme) return;
  if (kind === 'shim_stacks') scheme.layers = layersInput();
  else scheme.piston = restrictorFromInputs();
}

function applyShimScheme(scheme) {
  if (!scheme || !Array.isArray(scheme.layers)) return;
  state.layers = scheme.layers.map((l) => ({
    din: Number(l.d_in_mm), dout: Number(l.d_out_mm), t: Number(l.t_mm),
  }));
  renderLayers();
}

function applyRestrictorScheme(scheme) {
  if (!scheme || !scheme.piston) return;
  const p = scheme.piston;
  const map = {
    pN: p.n_ports, pW: p.port_width_mm, pRad: p.port_radial_mm,
    pR: p.port_radius_mm, pBypD: p.bypass_d_mm, pBypN: p.bypass_n,
    cBypCd: p.bypass_Cd,
  };
  for (const [id, val] of Object.entries(map)) {
    if (val !== undefined && val !== null && $(id)) $(id).value = val;
  }
}

function applyActiveSchemes() {
  applyShimScheme(findScheme('shim_stacks', state.project.active.shim_stack));
  applyRestrictorScheme(findScheme('restrictors', state.project.active.restrictor));
}

function renderSchemeLists() {
  const fill = (el, list, activeId) => {
    if (!el) return;
    el.innerHTML = '';
    for (const s of list) {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name || s.id;
      el.appendChild(opt);
    }
    if (activeId) el.value = activeId;
  };
  fill($('projStackList'), schemes().shim_stacks,
       state.project.active.shim_stack);
  fill($('projRestrictList'), schemes().restrictors,
       state.project.active.restrictor);
}

function selectScheme(kind, id, doRun = true) {
  if (!findScheme(kind, id)) return;
  syncActiveScheme(kind);
  state.project.active[ACTIVE_KEY[kind]] = id;
  applyActiveSchemes();
  renderSchemeLists();
  if (doRun && state.autoRun) scheduleRun();
}

function addOrUpdateScheme(kind) {
  const isStack = kind === 'shim_stacks';
  const nameEl = isStack ? $('projStackName') : $('projRestrictName');
  const name = (nameEl.value || '').trim();
  const list = schemes()[kind];
  let scheme = findScheme(kind, state.project.active[ACTIVE_KEY[kind]]);
  if (name) {
    scheme = isStack
      ? { id: schemeId('stack'), name, layers: layersInput() }
      : { id: schemeId('restr'), name, piston: restrictorFromInputs() };
    list.push(scheme);
  } else if (scheme) {
    if (isStack) scheme.layers = layersInput();
    else scheme.piston = restrictorFromInputs();
  } else {
    scheme = isStack
      ? { id: schemeId('stack'), name: '阀片组方案', layers: layersInput() }
      : { id: schemeId('restr'), name: '限流孔方案',
          piston: restrictorFromInputs() };
    list.push(scheme);
  }
  state.project.active[ACTIVE_KEY[kind]] = scheme.id;
  nameEl.value = '';
  renderSchemeLists();
}

function deleteScheme(kind) {
  const list = schemes()[kind];
  if (list.length <= 1) return;
  const id = state.project.active[ACTIVE_KEY[kind]];
  const idx = list.findIndex((s) => s.id === id);
  if (idx < 0) return;
  list.splice(idx, 1);
  state.project.active[ACTIVE_KEY[kind]] = list[0].id;
  applyActiveSchemes();
  renderSchemeLists();
  if (state.autoRun) scheduleRun();
}

function updateProjectBadge(path, name) {
  const badge = $('projectBadge');
  if (!badge) return;
  const rawName = name || ($('projName')?.value || '').trim();
  if (path) {
    badge.textContent = basename(path);
    badge.classList.add('ok');
  } else {
    badge.textContent = rawName ? '未保存' : '未命名项目';
    badge.classList.remove('ok');
  }
  const reservedN = Object.keys(state.project.reserved || {}).length;
  badge.title = [
    path ? `文件：${path}` : '尚未保存到项目文件',
    `预留字段：${reservedN} 项`,
    '打开项目时读取项目信息与预留字段',
  ].join('\n');
  document.title = rawName
    ? `${rawName} · ShimTuner Flow Studio`
    : 'ShimTuner Flow Studio';
}

function projectPayload() {
  ensureSchemes();
  syncActiveScheme('shim_stacks');
  syncActiveScheme('restrictors');
  const meta = projectMeta();
  const now = new Date().toISOString();
  const project = {
    ...(state.project.extra || {}),
    ...meta,
    created_at: state.project.createdAt || now,
    updated_at: now,
    reserved: clone(state.project.reserved || {}),
  };
  for (const k of Object.keys(project)) {
    if (!PROJECT_KNOWN.has(k) && k !== 'created_at' && k !== 'updated_at') {
      state.project.extra[k] = clone(project[k]);
    }
  }
  return {
    schema: PROJECT_SCHEMA,
    project,
    schemes: {
      shim_stacks: clone(schemes().shim_stacks),
      restrictors: clone(schemes().restrictors),
    },
    active_schemes: clone(state.project.active),
    case: {
      schema: CASE_SCHEMA,
      mode: state.mode,
      valve_type: rodValveValue(),
      input: collect(),
      summary: state.last ? {
        headline: state.last.headline,
        kpis: state.last.report.kpis,
      } : null,
    },
  };
}

function applyProject(data, filePath) {
  if (!data || typeof data !== 'object') {
    throw new Error('项目文件内容不是 JSON 对象');
  }
  const caseData = data.case || {};
  const input = caseData.input || data.input || {};
  if (data.schema === CASE_SCHEMA || (!data.project && data.input)) {
    applyCase(input);
    if (data.mode) setMode(data.mode, false);
    if (data.valve_type) setRodValve(data.valve_type);
    state.project.path = filePath || state.project.path;
    updateProjectBadge(filePath, '');
    if (state.autoRun) scheduleRun();
    return;
  }
  const proj = data.project || {};
  $('projName').value = proj.name ?? '';
  $('projVehicle').value = proj.vehicle ?? '';
  $('projPos').value = ['front', 'rear', 'other'].includes(proj.position)
    ? proj.position : 'front';
  $('projValve').value = ['mid', 'base', 'both'].includes(proj.valve_type)
    ? proj.valve_type : 'mid';
  $('projNotes').value = proj.notes ?? '';
  state.project.path = filePath || null;
  state.project.createdAt = proj.created_at || null;
  state.project.reserved = (proj.reserved && typeof proj.reserved === 'object')
    ? clone(proj.reserved) : {};
  state.project.extra = {};
  for (const k of Object.keys(proj)) {
    if (!PROJECT_KNOWN.has(k) && k !== 'created_at' && k !== 'updated_at') {
      state.project.extra[k] = clone(proj[k]);
    }
  }

  // 方案：优先读 schemes；旧项目从当前 case 生成一套默认方案
  const sc = data.schemes || {};
  state.project.schemes.shim_stacks = Array.isArray(sc.shim_stacks)
    ? clone(sc.shim_stacks) : [];
  state.project.schemes.restrictors = Array.isArray(sc.restrictors)
    ? clone(sc.restrictors) : [];
  state.project.active = {
    shim_stack: data.active_schemes?.shim_stack || null,
    restrictor: data.active_schemes?.restrictor || null,
  };
  if (!state.project.schemes.shim_stacks.length) {
    state.project.schemes.shim_stacks.push({
      id: schemeId('stack'),
      name: '默认阀片组',
      layers: Array.isArray(input.layers) && input.layers.length
        ? clone(input.layers) : layersInput(),
    });
  }
  if (!state.project.schemes.restrictors.length) {
    state.project.schemes.restrictors.push({
      id: schemeId('restr'),
      name: '默认限流孔',
      piston: input.piston ? clone(input.piston) : restrictorFromInputs(),
    });
  }
  if (!findScheme('shim_stacks', state.project.active.shim_stack)) {
    state.project.active.shim_stack = state.project.schemes.shim_stacks[0].id;
  }
  if (!findScheme('restrictors', state.project.active.restrictor)) {
    state.project.active.restrictor = state.project.schemes.restrictors[0].id;
  }

  if (caseData.mode) setMode(caseData.mode, false);
  setRodValve(proj.valve_type || caseData.valve_type
    || (input.curve && input.curve.valve_type) || rodValveValue());
  applyCase(input);
  applyActiveSchemes();
  renderSchemeLists();
  updateProjectBadge(filePath, proj.name);
  if (state.autoRun) scheduleRun();
}

function newProject() {
  state.project.path = null;
  state.project.createdAt = null;
  state.project.reserved = {};
  state.project.extra = {};
  state.project.schemes = { shim_stacks: [], restrictors: [] };
  state.project.active = { shim_stack: null, restrictor: null };
  $('projName').value = '';
  $('projVehicle').value = '';
  $('projPos').value = 'front';
  $('projValve').value = 'mid';
  $('projNotes').value = '';
  setRodValve('mid');
  ensureSchemes();
  updateProjectBadge(null, '');
  refreshProjectList();
}

async function saveProject(saveAs = false) {
  ensureSchemes();
  const payload = projectPayload();
  const name = payload.project.name || 'shock-project';
  const text = JSON.stringify(payload, null, 2);
  if (!window.shimtuner?.projectSave) {
    showError(new Error('当前环境不支持项目文件保存'));
    return;
  }
  const res = await window.shimtuner.projectSave(
    name, text, saveAs ? null : state.project.path);
  if (!res.ok) return;
  state.project.path = res.path;
  updateProjectBadge(res.path, payload.project.name);
  await refreshProjectList();
  hideError();
}

async function openProjectFromDialog() {
  if (!window.shimtuner?.projectOpen) {
    showError(new Error('当前环境不支持项目文件打开'));
    return;
  }
  const res = await window.shimtuner.projectOpen();
  if (!res.ok) return;
  try {
    applyProject(JSON.parse(res.content), res.path);
    hideError();
  } catch (err) {
    showError(err);
  }
}

async function openProjectPath(filePath) {
  if (!filePath || !window.shimtuner?.projectOpen) return;
  const res = await window.shimtuner.projectOpen(filePath);
  if (!res.ok) return;
  try {
    applyProject(JSON.parse(res.content), res.path);
    hideError();
  } catch (err) {
    showError(err);
  }
}

async function refreshProjectList() {
  const sel = $('projList');
  if (!sel) return;
  if (!window.shimtuner?.projectList) {
    sel.innerHTML = '<option value="">（仅桌面端支持项目目录）</option>';
    return;
  }
  const res = await window.shimtuner.projectList();
  sel.innerHTML = '';
  if (!res.ok || !res.items.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '（项目目录为空）';
    sel.appendChild(opt);
    return;
  }
  for (const item of res.items) {
    const opt = document.createElement('option');
    opt.value = item.path;
    opt.textContent = item.name;
    sel.appendChild(opt);
  }
}

function wireProjectUi() {
  $('projNew')?.addEventListener('click', newProject);
  $('projOpen')?.addEventListener('click', openProjectFromDialog);
  $('projSave')?.addEventListener('click', saveProject);
  $('projSaveAs')?.addEventListener('click', () => saveProject(true));
  $('projRefresh')?.addEventListener('click', refreshProjectList);
  $('projOpenList')?.addEventListener('click', () => {
    const p = $('projList')?.value;
    if (p) openProjectPath(p);
  });
  $('projList')?.addEventListener('change', () => {});
  $('projStackList')?.addEventListener('change', (ev) => {
    selectScheme('shim_stacks', ev.target.value);
  });
  $('projRestrictList')?.addEventListener('change', (ev) => {
    selectScheme('restrictors', ev.target.value);
  });
  $('projStackAdd')?.addEventListener('click', () => addOrUpdateScheme('shim_stacks'));
  $('projRestrictAdd')?.addEventListener('click', () => addOrUpdateScheme('restrictors'));
  $('projStackDel')?.addEventListener('click', () => deleteScheme('shim_stacks'));
  $('projRestrictDel')?.addEventListener('click', () => deleteScheme('restrictors'));
  ['projName', 'projVehicle', 'projPos', 'projValve', 'projNotes'].forEach((id) => {
    $(id)?.addEventListener('change', () => updateProjectBadge(
      state.project.path, $('projName').value));
  });
}

window.__shimtunerProject = {
  payload: projectPayload,
  apply: applyProject,
  meta: projectMeta,
  schemes: () => clone(schemes()),
};

function copyReport() {
  if (!state.last) return;
  const res = state.last;
  const lines = [res.headline, '', ...res.report.kpis.map(
    (k) => `${k.label}: ${k.value}  (${k.hint || ''})`), ''];
  if (res.report.warnings.length) {
    lines.push('警告:', ...res.report.warnings.map((w) => `  - ${w}`), '');
  }
  lines.push(...res.report.lines, '', '备注:', ...res.report.notes.map((n) => `  - ${n}`));
  const text = lines.join('\n');
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text);
  else window.shimtuner?.saveText('flow-report.txt', text);
}

/* ------------------------------------------------------------------ */
/* 状态 / 错误                                                         */
/* ------------------------------------------------------------------ */
function setBackendStatus(payload) {
  const pill = $('backendPill');
  const ok = payload.status === 'running';
  pill.classList.toggle('ok', ok);
  pill.classList.toggle('bad', !ok && payload.status === 'error');
  pill.querySelector('span').textContent = ok ? '后端就绪' : (payload.message || '连接中…');
  $('backendDetail').textContent = payload.url ? `API ${payload.url}` : '';
  $('pythonDetail').textContent = payload.python || '';
}

async function restartBackend() {
  if (!window.shimtuner) return;
  setBackendStatus({ status: 'starting', message: '重启中…' });
  const info = await window.shimtuner.restartBackend();
  state.appInfo = info;
  if (info.url) state.baseUrl = info.url;
  setBackendStatus(info);
  runAnalysis(true);
}

function showError(message) {
  $('errorBanner').classList.remove('hidden');
  $('errorText').textContent = message;
  $('elapsedBadge').textContent = '—';
}

function hideError() {
  $('errorBanner').classList.add('hidden');
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
