'use strict';

const $ = (id) => document.getElementById(id);
const { escapeHtml } = window.chartHelpers;

const PALETTE_FALLBACK = ['#38bdf8', '#fbbf24', '#34d399', '#f472b6',
  '#a78bfa', '#fb7185', '#22d3ee', '#eab308'];

const state = {
  config: null,
  baseUrl: null,
  backendStatus: 'starting',
  backend: null,
  appInfo: null,
  layers: [],
  solver: '1d',
  autoRun: true,
  running: false,
  queued: false,
  seq: 0,
  debounce: null,
  last: null,
  ready: false,
  error: null,
  hidden: { hp: new Set(), profile: new Set() },
  charts: {},
  busyTimer: null,
};

window.__shimtunerState = () => ({
  ready: state.ready,
  error: state.error,
  solver: state.solver,
  layerCount: state.layers.length,
  backend: state.baseUrl,
  headline: state.last ? state.last.headline : null,
  elapsedMs: state.last ? state.last.elapsed_ms : null,
  kpis: state.last ? state.last.report.kpis.map((k) => k.value) : [],
});

/* ------------------------------------------------------------------ */
/* 初始化                                                              */
/* ------------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', init);

async function init() {
  state.charts = {
    hp: new LineChart($('hpCanvas'), $('hpTip')),
    profile: new LineChart($('profileCanvas'), $('profileTip')),
  };
  wireUi();
  setBackendStatus({ status: 'starting', message: '正在连接数值后端…' });

  try {
    await resolveBackend();
    await loadConfig();
    applyConfigDefaults();
    renderPresets();
    renderSolvers();
    renderLayers();
    updateSolverUi();
    setBackendStatus({ status: 'running', message: state.backend?.message || '数值后端就绪' });
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
    state.backend = state.appInfo;
    if (state.appInfo.app?.version) {
      $('versionBadge').textContent = `v${state.appInfo.app.version}`;
    }
    bridge.onBackendStatus((payload) => {
      state.backend = { ...state.backend, ...payload };
      setBackendStatus(payload);
      if (payload.status === 'running' && payload.url) {
        state.baseUrl = payload.url;
      }
    });

    let info = state.appInfo;
    if (info.status !== 'running' || !info.url) {
      info = await waitForBackendRunning(bridge);
    }
    state.baseUrl = info.url;
    setBackendStatus(info);
    return;
  }

  // 浏览器预览模式 (npm start 之外): ?backend=http://127.0.0.1:8760
  const params = new URLSearchParams(location.search);
  state.baseUrl = (params.get('backend') || 'http://127.0.0.1:8760').replace(/\/$/, '');
  setBackendStatus({
    status: 'running',
    message: `浏览器预览模式 · ${state.baseUrl}`,
  });
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
          return;
        }
        if (info.status === 'error') {
          clearInterval(timer);
          reject(new Error(info.message || '数值后端启动失败'));
          return;
        }
        if (Date.now() - started > 90000) {
          clearInterval(timer);
          reject(new Error('数值后端启动超时'));
        }
      } catch (err) {
        clearInterval(timer);
        reject(err);
      }
    }, 350);
  });
}

async function loadConfig() {
  const res = await fetch(`${state.baseUrl}/api/config`, {
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`读取后端配置失败 (HTTP ${res.status})`);
  state.config = await res.json();
  $('versionBadge').textContent = `v${state.config.version}`;
}

function applyConfigDefaults() {
  const cfg = state.config;
  $('eInput').value = cfg.material_defaults.E_GPa;
  $('nuInput').value = cfg.material_defaults.nu;
  $('pmaxInput').value = cfg.defaults.p_max_MPa;
  $('femRadial').value = cfg.defaults.fem.n_radial;
  $('femTheta').value = cfg.defaults.fem.n_theta;
  $('femClamp').value = '';
  $('layeredNl').checked = Boolean(cfg.defaults.layered_nonlinear);

  const list = $('assumptionList');
  list.innerHTML = '';
  for (const item of cfg.assumptions || []) {
    const li = document.createElement('li');
    li.textContent = item;
    list.appendChild(li);
  }

  const preset = cfg.presets.find((p) => p.id === 'taper') || cfg.presets[0];
  state.layers = preset.layers.map((l) => ({ ...l }));
  state.solver = cfg.defaults.solver || '1d';
}

/* ------------------------------------------------------------------ */
/* 渲染: 侧栏                                                          */
/* ------------------------------------------------------------------ */
function palette() {
  return state.config?.palette || PALETTE_FALLBACK;
}

function layerColor(index) {
  const colors = palette();
  return colors[index % colors.length];
}

function renderPresets() {
  const wrap = $('presetChips');
  wrap.innerHTML = '';
  for (const preset of state.config.presets) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'preset';
    btn.textContent = preset.label;
    btn.addEventListener('click', () => {
      state.layers = preset.layers.map((l) => ({ ...l }));
      renderLayers();
      updateSolverUi();
      scheduleRun();
    });
    wrap.appendChild(btn);
  }
}

function renderSolvers() {
  const grid = $('solverGrid');
  grid.innerHTML = '';
  for (const solver of state.config.solvers) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'solver-card' + (solver.id === state.solver ? ' active' : '');
    btn.dataset.solver = solver.id;
    btn.innerHTML = `<strong>${escapeHtml(solver.label)}</strong>` +
      `<small>${escapeHtml(solver.badge)}</small>`;
    btn.addEventListener('click', () => selectSolver(solver.id));
    grid.appendChild(btn);
  }
}

function selectSolver(id) {
  if (state.solver === id) return;
  const previous = state.config.solvers.find((s) => s.id === state.solver);
  const matchesRecommended = (rec) => rec
    && Math.round(readNumber($('femRadial')) ?? -1) === rec.n_radial
    && Math.round(readNumber($('femTheta')) ?? -1) === rec.n_theta;
  // 用户手动调过网格时保留其数值, 否则载入目标求解器的建议网格
  const useRecommended = !previous?.recommended_fem
    || matchesRecommended(previous.recommended_fem);

  state.solver = id;
  document.querySelectorAll('.solver-card').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.solver === id);
  });
  const solver = state.config.solvers.find((s) => s.id === id);
  if (solver?.recommended_fem && useRecommended) {
    $('femRadial').value = solver.recommended_fem.n_radial;
    $('femTheta').value = solver.recommended_fem.n_theta;
  }
  updateSolverUi();
  scheduleRun();
}

function updateSolverUi() {
  const solver = state.config.solvers.find((s) => s.id === state.solver);
  $('solverDesc').textContent = solver ? solver.description : '';
  $('femBox').classList.toggle('hidden', state.solver === '1d');
  $('layeredNlWrap').classList.toggle('hidden', state.solver !== 'layered');
}

function renderLayers() {
  const wrap = $('layerRows');
  wrap.innerHTML = '';
  state.layers.forEach((layer, index) => {
    const row = document.createElement('div');
    row.className = 'layer-row' + (index === 0 ? ' is-top' : '');

    const idx = document.createElement('div');
    idx.className = 'idx';
    idx.innerHTML = `<i style="background:${layerColor(index)}"></i><span>${index + 1}</span>`;
    row.appendChild(idx);

    for (const key of ['d_in_mm', 'd_out_mm', 't_mm']) {
      const input = document.createElement('input');
      input.type = 'number';
      input.step = '1';   // mm 输入框步长 1 mm；更细的值由用户直接键入
      input.min = '0';
      input.value = String(layer[key]);
      input.title = key === 'd_in_mm' ? '内径' : key === 'd_out_mm' ? '外径' : '厚度';
      input.addEventListener('input', () => {
        const value = Number(input.value);
        const ok = input.value.trim() !== '' && Number.isFinite(value) && value > 0;
        input.classList.toggle('invalid', !ok);
        if (ok) {
          state.layers[index][key] = value;
          updateLayerSummary();
          scheduleRun();
        }
      });
      row.appendChild(input);
    }

    const ops = document.createElement('div');
    ops.className = 'ops';
    ops.appendChild(iconButton('↑', '上移', index === 0, () => moveLayer(index, -1)));
    ops.appendChild(iconButton('↓', '下移', index === state.layers.length - 1,
      () => moveLayer(index, 1)));
    ops.appendChild(iconButton('×', '删除', state.layers.length <= 1, () => {
      state.layers.splice(index, 1);
      renderLayers();
      updateSolverUi();
      scheduleRun();
    }, 'danger'));
    row.appendChild(ops);

    wrap.appendChild(row);
  });
  updateLayerSummary();
}

function iconButton(text, title, disabled, handler, extra = '') {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = `icon-btn ${extra}`.trim();
  btn.textContent = text;
  btn.title = title;
  btn.disabled = disabled;
  btn.addEventListener('click', handler);
  return btn;
}

function moveLayer(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.layers.length) return;
  const [item] = state.layers.splice(index, 1);
  state.layers.splice(target, 0, item);
  renderLayers();
  scheduleRun();
}

function updateLayerSummary() {
  const total = state.layers.reduce((sum, l) => sum + (Number(l.t_mm) || 0), 0);
  $('layerSummary').textContent =
    `${state.layers.length} 片 · 总厚 ${total.toFixed(2)} mm`;
}

/* ------------------------------------------------------------------ */
/* 请求                                                                */
/* ------------------------------------------------------------------ */
function scheduleRun() {
  if (!state.autoRun) return;
  clearTimeout(state.debounce);
  state.debounce = setTimeout(() => runAnalysis(false), 380);
}

function readNumber(input) {
  const raw = input.value.trim();
  if (raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : NaN;
}

function collectPayload() {
  if (state.layers.length === 0) throw new Error('请至少保留一片阀片');
  if (document.querySelector('.layer-row input.invalid')) {
    throw new Error('阀片堆叠中存在无效输入，请修正标红的字段');
  }
  const layers = state.layers.map((layer, index) => {
    const din = Number(layer.d_in_mm);
    const dout = Number(layer.d_out_mm);
    const t = Number(layer.t_mm);
    if (!(din > 0 && dout > 0 && t > 0)) {
      throw new Error(`第 ${index + 1} 片: 内径/外径/厚度必须 > 0 mm`);
    }
    if (dout <= din) {
      throw new Error(`第 ${index + 1} 片: 外径 (${dout}) 必须大于内径 (${din})`);
    }
    return { d_in_mm: din, d_out_mm: dout, t_mm: t };
  });

  const E = readNumber($('eInput'));
  const nu = readNumber($('nuInput'));
  const pMax = readNumber($('pmaxInput'));
  if (!(E > 0)) throw new Error('弹性模量 E 必须 > 0 GPa');
  if (!(nu >= 0 && nu < 0.5)) throw new Error('泊松比 ν 必须满足 0 ≤ ν < 0.5');
  if (!(pMax > 0)) throw new Error('p_max 必须 > 0 MPa');

  const nRadial = Math.round(readNumber($('femRadial')) ?? 60);
  const nTheta = Math.round(readNumber($('femTheta')) ?? 0);
  if (!(nRadial >= 4 && nRadial <= 200)) throw new Error('径向单元数应在 4…200');
  if (!(nTheta === 0 || (nTheta >= 3 && nTheta <= 256))) {
    throw new Error('周向分段应为 0 (自动) 或 3…256');
  }
  const clampRaw = $('femClamp').value.trim();
  const clamp = clampRaw === '' ? null : Number(clampRaw);
  if (clamp !== null && !(clamp > 0)) throw new Error('夹持半径必须 > 0 mm (留空自动)');

  return {
    layers,
    material: { E_GPa: E, nu },
    p_max_MPa: pMax,
    solver: state.solver,
    fem: { n_radial: nRadial, n_theta: nTheta, clamp_radius_mm: clamp },
    layered_nonlinear: $('layeredNl').checked,
    curve_points: 101,
  };
}

async function runAnalysis(manual) {
  if (!state.baseUrl) {
    showError('数值后端尚未就绪');
    return;
  }
  clearTimeout(state.debounce);
  state.ready = false;
  state.error = null;

  let payload;
  try {
    payload = collectPayload();
  } catch (err) {
    state.seq += 1;          // 作废仍在进行的旧请求结果
    showError(err.message);
    return;
  }

  if (state.running) {
    state.queued = true;
    return;
  }

  state.running = true;
  hideError();
  setBusy(true);
  const seq = ++state.seq;
  const wallStart = performance.now();

  try {
    const res = await fetch(`${state.baseUrl}/api/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(formatDetail(body.detail) || `请求失败 (HTTP ${res.status})`);
    }
    if (seq !== state.seq) return;
    state.ready = true;
    applyResult(body, performance.now() - wallStart);
  } catch (err) {
    if (seq === state.seq) {
      state.error = err.message || String(err);
      showError(state.error);
    }
  } finally {
    state.running = false;
    setBusy(false);
    if (state.queued) {
      state.queued = false;
      runAnalysis(false);
    }
  }
}

function formatDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === 'string') return item;
      const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
      return `${loc ? `${loc}: ` : ''}${item.msg || JSON.stringify(item)}`;
    }).join('\n');
  }
  return JSON.stringify(detail);
}

function setBusy(busy) {
  const btn = $('runBtn');
  btn.disabled = busy || state.backendStatus !== 'running';
  btn.classList.toggle('loading', busy);
  btn.querySelector('span:nth-child(2)').textContent = busy ? '计算中…' : '运行分析';
  document.querySelectorAll('.chart-card').forEach((card) => {
    card.classList.toggle('loading-shimmer', busy);
  });
  clearInterval(state.busyTimer);
  state.busyTimer = null;
  if (busy) {
    const started = performance.now();
    $('elapsedBadge').textContent = '计算中 0.0 s';
    state.busyTimer = setInterval(() => {
      $('elapsedBadge').textContent =
        `计算中 ${((performance.now() - started) / 1000).toFixed(1)} s`;
    }, 120);
  }
}

/* ------------------------------------------------------------------ */
/* 渲染: 结果                                                          */
/* ------------------------------------------------------------------ */
function applyResult(result, wallMs) {
  state.last = result;
  state.error = null;
  const pMax = result.p_max_MPa;
  $('headline').textContent = result.headline;
  $('subline').textContent = `${result.solver_label} · ${result.layers.length} 片 · ` +
    `p_max = ${pMax} MPa · ${result.solver_description}`;
  $('elapsedBadge').textContent = `${result.elapsed_ms.toFixed(0)} ms 后端 / ` +
    `${wallMs.toFixed(0)} ms 往返`;
  $('hpBadge').textContent = result.solver_label;
  $('profileBadge').textContent = result.solver_label;
  $('reportBadge').textContent = `${result.solver_label} · ${pMax} MPa`;
  $('reportTime').textContent =
    `${new Date().toLocaleTimeString()} · ${result.elapsed_ms.toFixed(1)} ms`;

  renderKpis(result.report.kpis);
  renderWarnings(result.report.warnings);
  renderLayerTable(result.report.layers);
  $('reportLines').textContent = result.report.lines.join('\n');
  renderNotes(result.report.notes);

  renderChart('hp', result.charts.hp, $('hpTitle'));
  renderChart('profile', result.charts.profile, $('profileTitle'));

  setBackendStatus({
    status: 'running',
    message: state.backend?.message || '数值后端就绪',
  });
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

function renderLayerTable(rows) {
  const body = $('layerTableBody');
  body.innerHTML = '';
  for (const row of rows) {
    const tr = document.createElement('tr');
    if (row.warn) tr.className = 'warn';
    const pct = Math.max(3, Math.min(100, (row.ratio / row.limit) * 100));
    tr.innerHTML =
      `<td><span class="cell-idx"><i style="background:${row.color}"></i>${row.index}</span></td>` +
      `<td class="cell-geo">Ø${row.d_in_mm} / Ø${row.d_out_mm} × ${row.t_mm}</td>` +
      `<td class="cell-h">${row.h_mm.toFixed(5)} mm</td>` +
      `<td><div class="ratio ${row.warn ? 'warn' : ''}">` +
      `<div class="ratio-bar"><div class="ratio-fill" style="width:${pct}%"></div></div>` +
      `<span>${row.ratio.toFixed(3)} ${escapeHtml(row.ratio_name)}</span></div></td>` +
      `<td class="cell-detail">${escapeHtml(row.detail || '')}</td>`;
    body.appendChild(tr);
  }
}

function renderNotes(notes) {
  const list = $('reportNotes');
  list.innerHTML = '';
  for (const note of notes || []) {
    const li = document.createElement('li');
    li.textContent = note;
    list.appendChild(li);
  }
}

function renderChart(key, chartData, titleEl) {
  titleEl.textContent = chartData.title;
  const chart = state.charts[key];
  chart.setData(chartData);
  const legend = $(key === 'hp' ? 'hpLegend' : 'profileLegend');
  legend.innerHTML = '';
  const hidden = state.hidden[key];
  const valid = new Set(chartData.series.map((s) => s.id));
  for (const id of [...hidden]) if (!valid.has(id)) hidden.delete(id);

  for (const series of chartData.series) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'legend-item' + (hidden.has(series.id) ? ' off' : '');
    const dash = series.dash === 'dash' || series.dash === 'dot'
      || series.dash === 'dashdot' ? 'dashed' : 'solid';
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
/* 状态 / 错误                                                         */
/* ------------------------------------------------------------------ */
function setBackendStatus(payload) {
  const status = payload.status || 'starting';
  state.backendStatus = status;
  if (payload.url) state.baseUrl = payload.url;
  if (payload.message) state.backend = { ...(state.backend || {}), ...payload };

  const pill = $('backendPill');
  pill.className = `pill ${status}`;
  const text = status === 'running' ? '后端就绪'
    : status === 'starting' ? '后端启动中'
      : status === 'error' ? '后端异常' : status;
  pill.querySelector('span').textContent = text;

  $('backendDetail').textContent = payload.message || '';
  $('pythonDetail').textContent = payload.python ? `Python · ${payload.python}` : '';

  const canRun = status === 'running' && !state.running;
  $('runBtn').disabled = !canRun;
  $('restartBackendBtn').disabled = !window.shimtuner;

  if (status === 'error' && payload.message && !state.running) {
    state.error = payload.message;
    showError(payload.message);
  }
}

function showError(message) {
  state.error = message;
  $('errorText').textContent = message;
  $('errorBanner').classList.remove('hidden');
}

function hideError() {
  $('errorBanner').classList.add('hidden');
}

async function retry() {
  hideError();
  if (state.backendStatus === 'error' && window.shimtuner) {
    await restartBackend();
    return;
  }
  runAnalysis(true);
}

async function restartBackend() {
  if (!window.shimtuner) {
    showError('浏览器预览模式下无法重启后端，请手动启动 python -m backend --port 8760');
    return;
  }
  hideError();
  setBackendStatus({ status: 'starting', message: '正在重启数值后端…' });
  try {
    const info = await window.shimtuner.restartBackend();
    state.backend = info;
    setBackendStatus(info);
    if (info.status === 'running' && info.url) {
      state.baseUrl = info.url;
      await runAnalysis(true);
    }
  } catch (err) {
    setBackendStatus({ status: 'error', message: err.message || String(err) });
  }
}

/* ------------------------------------------------------------------ */
/* 导入 / 导出 / 复制                                                   */
/* ------------------------------------------------------------------ */
function currentStackDocument() {
  let payload = null;
  try {
    payload = collectPayload();
  } catch {
    payload = {
      layers: state.layers,
      material: { E_GPa: readNumber($('eInput')), nu: readNumber($('nuInput')) },
      p_max_MPa: readNumber($('pmaxInput')),
      solver: state.solver,
    };
  }
  return {
    format: 'shimtuner-stack',
    version: state.config?.version,
    savedAt: new Date().toISOString(),
    ...payload,
  };
}

async function exportStack() {
  try {
    const text = JSON.stringify(currentStackDocument(), null, 2);
    if (window.shimtuner) {
      await window.shimtuner.saveText('shim-stack.json', text);
    } else {
      const blob = new Blob([text], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'shim-stack.json';
      a.click();
      URL.revokeObjectURL(url);
    }
  } catch (err) {
    showError(`导出失败: ${err.message}`);
  }
}

async function importStack() {
  try {
    let content = null;
    if (window.shimtuner) {
      const res = await window.shimtuner.openText();
      if (!res.ok) return;
      content = res.content;
    } else {
      const file = $('importFile').files?.[0];
      if (!file) return;
      content = await file.text();
    }
    const doc = JSON.parse(content);
    applyStackDocument(doc);
  } catch (err) {
    showError(`导入失败: ${err.message}`);
  } finally {
    $('importFile').value = '';
  }
}

function applyStackDocument(doc) {
  if (!doc || !Array.isArray(doc.layers) || doc.layers.length === 0) {
    throw new Error('文件中缺少 layers 数组');
  }
  state.layers = doc.layers.map((l, index) => {
    const next = {
      d_in_mm: Number(l.d_in_mm),
      d_out_mm: Number(l.d_out_mm),
      t_mm: Number(l.t_mm),
    };
    if (!(next.d_in_mm > 0 && next.d_out_mm > next.d_in_mm && next.t_mm > 0)) {
      throw new Error(`第 ${index + 1} 片几何参数无效`);
    }
    return next;
  });
  if (doc.material) {
    if (Number.isFinite(Number(doc.material.E_GPa))) $('eInput').value = doc.material.E_GPa;
    if (Number.isFinite(Number(doc.material.nu))) $('nuInput').value = doc.material.nu;
  }
  if (Number.isFinite(Number(doc.p_max_MPa))) $('pmaxInput').value = doc.p_max_MPa;
  if (doc.solver && state.config.solvers.some((s) => s.id === doc.solver)) {
    state.solver = doc.solver;
    document.querySelectorAll('.solver-card').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.solver === doc.solver);
    });
  }
  if (doc.fem) {
    if (doc.fem.n_radial != null) $('femRadial').value = doc.fem.n_radial;
    if (doc.fem.n_theta != null) $('femTheta').value = doc.fem.n_theta;
    $('femClamp').value = doc.fem.clamp_radius_mm == null ? '' : doc.fem.clamp_radius_mm;
  }
  if (doc.layered_nonlinear != null) $('layeredNl').checked = Boolean(doc.layered_nonlinear);
  renderLayers();
  updateSolverUi();
  runAnalysis(true);
}

async function copyReport() {
  if (!state.last) return;
  const res = state.last;
  const lines = [
    `ShimTuner 数值核验报告  (${new Date().toLocaleString()})`,
    res.headline,
    '',
    ...res.report.kpis.map((k) => `${k.label}: ${k.value}  (${k.hint})`),
    '',
  ];
  if (res.report.warnings.length) {
    lines.push('警告:', ...res.report.warnings.map((w) => `  - ${w}`), '');
  }
  lines.push('各层结果:');
  for (const row of res.report.layers) {
    lines.push(`  第${row.index}片 Ø${row.d_in_mm}/Ø${row.d_out_mm}×${row.t_mm}: ` +
      `h = ${row.h_mm.toFixed(5)} mm, ${row.ratio_name} = ${row.ratio.toFixed(3)}` +
      `${row.detail ? `, ${row.detail}` : ''}`);
  }
  lines.push('', '数值核验 / 状态:', ...res.report.lines, '');
  lines.push('备注:', ...res.report.notes.map((n) => `  - ${n}`));
  lines.push('假设:', ...(res.assumptions || []).map((n) => `  - ${n}`));
  const text = lines.join('\n');

  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  const btn = $('copyBtn');
  const old = btn.textContent;
  btn.textContent = '已复制 ✓';
  setTimeout(() => { btn.textContent = old; }, 1400);
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
function wireUi() {
  $('runBtn').addEventListener('click', () => runAnalysis(true));
  $('restartBackendBtn').addEventListener('click', restartBackend);
  $('autoRun').addEventListener('change', (e) => {
    state.autoRun = e.target.checked;
    if (state.autoRun) scheduleRun();
  });

  $('addLayerBtn').addEventListener('click', () => {
    const last = state.layers[state.layers.length - 1] || { d_in_mm: 6, d_out_mm: 10, t_mm: 0.1 };
    state.layers.push({
      d_in_mm: Number(last.d_in_mm) || 6,
      d_out_mm: Number(last.d_out_mm) || 10,
      t_mm: Number(last.t_mm) || 0.1,
    });
    renderLayers();
    scheduleRun();
  });

  for (const id of ['eInput', 'nuInput', 'pmaxInput', 'femRadial', 'femTheta', 'femClamp']) {
    $(id).addEventListener('input', scheduleRun);
  }
  $('layeredNl').addEventListener('change', scheduleRun);

  $('copyBtn').addEventListener('click', copyReport);
  $('exportBtn').addEventListener('click', exportStack);
  $('importBtn').addEventListener('click', () => {
    if (window.shimtuner) importStack();
    else $('importFile').click();
  });
  $('importFile').addEventListener('change', importStack);

  $('retryBtn').addEventListener('click', retry);
  $('errorClose').addEventListener('click', hideError);

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      runAnalysis(true);
    }
  });
}
