'use strict';

const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');          // FlowSolver/
const IS_WIN = process.platform === 'win32';
const SMOKE = process.argv.includes('--smoke-test');
const SMOKE_ITERATE = process.argv.includes('--smoke-iterate');
const SMOKE_CURVE = process.argv.includes('--smoke-curve');
const SMOKE_ROD = process.argv.includes('--smoke-rod');

let win = null;
let backend = null;                                  // { child, url, port, python }
let quitting = false;

const state = {
  status: 'starting',
  message: '正在启动数值后端…',
  url: null,
  python: null,
  log: '',
};

function snapshot() {
  return { ...state };
}

function broadcast() {
  if (win && !win.isDestroyed()) {
    win.webContents.send('backend:status', snapshot());
  }
}

function setState(patch) {
  Object.assign(state, patch);
  broadcast();
}

function logChunk(chunk) {
  state.log = (state.log + String(chunk)).slice(-8000);
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function pythonCandidates() {
  const list = [];
  if (process.env.SHIMTUNER_PYTHON) {
    list.push({ command: process.env.SHIMTUNER_PYTHON, args: [], source: 'SHIMTUNER_PYTHON' });
  }
  const venvs = [
    { dir: path.join(ROOT, '.venv'), source: 'FlowSolver/.venv' },
    { dir: path.join(ROOT, '..', '.venv'), source: '仓库根 .venv' },
  ];
  for (const { dir, source } of venvs) {
    const exe = path.join(dir, IS_WIN ? 'Scripts\\python.exe' : 'bin/python');
    if (fs.existsSync(exe)) list.push({ command: exe, args: [], source });
  }
  if (IS_WIN) {
    list.push({ command: 'py', args: ['-3'], source: 'PATH (py -3)' });
  }
  list.push({ command: IS_WIN ? 'python' : 'python3', args: [], source: 'PATH' });
  return list;
}

const PREFLIGHT = 'import numpy, scipy, fastapi, uvicorn';

function probePython(candidate) {
  const res = spawnSync(candidate.command,
    [...candidate.args, '-c', PREFLIGHT],
    { encoding: 'utf8', timeout: 20000, windowsHide: true });
  if (res.error) return { ok: false, error: res.error.message };
  if (res.status !== 0) {
    return { ok: false, error: (res.stderr || res.stdout || `exit ${res.status}`).trim() };
  }
  return { ok: true };
}

function resolvePython() {
  const errors = [];
  for (const candidate of pythonCandidates()) {
    if (path.isAbsolute(candidate.command) && !fs.existsSync(candidate.command)) {
      errors.push(`${candidate.source}: 不存在 (${candidate.command})`);
      continue;
    }
    const probe = probePython(candidate);
    if (probe.ok) return { candidate, errors };
    errors.push(`${candidate.source}: ${probe.error.split('\n').slice(-1)[0]}`);
  }
  const err = new Error(
    '未找到可用的 Python 环境 (需要 numpy / scipy / fastapi / uvicorn)。\n' +
    '请在项目目录创建 .venv 并安装 FlowSolver/requirements-ui.txt, ' +
    '或设置环境变量 SHIMTUNER_PYTHON 指向解释器。\n\n' + errors.join('\n'));
  err.details = errors;
  throw err;
}

async function waitForHealth(url, timeoutMs, child) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode) {
      throw new Error(`后端进程已退出 (code ${child.exitCode})\n${state.log.slice(-2500)}`);
    }
    try {
      const res = await fetch(`${url}/api/health`, {
        signal: AbortSignal.timeout(1500),
      });
      if (res.ok) return await res.json();
    } catch {
      /* 还没就绪 */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`后端启动超时 (${Math.round(timeoutMs / 1000)} s)\n${state.log.slice(-2500)}`);
}

function stopBackend() {
  const child = backend?.child;
  backend = null;
  if (child && child.exitCode === null) {
    try { child.kill(); } catch { /* 进程已退出 */ }
  }
}

async function startBackend() {
  stopBackend();
  setState({ status: 'starting', message: '正在启动数值后端…', url: null, log: '' });

  let resolved;
  try {
    resolved = resolvePython();
  } catch (err) {
    setState({ status: 'error', message: err.message, log: err.message });
    throw err;
  }

  const port = await getFreePort();
  const url = `http://127.0.0.1:${port}`;
  const { command, args, source } = resolved.candidate;

  const child = spawn(command,
    [...args, '-m', 'backend', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUNBUFFERED: '1',
        PYTHONPATH: ROOT,
      },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

  backend = { child, url, port, python: `${source}: ${command}` };
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', logChunk);
  child.stderr.on('data', logChunk);
  child.on('exit', (code) => {
    if (quitting || backend?.child !== child) return;
    setState({
      status: 'error',
      message: `数值后端意外退出 (code ${code})`,
      url: null,
    });
  });

  try {
    const health = await waitForHealth(url, 60000, child);
    setState({
      status: 'running',
      message: `数值后端就绪 · v${health.version}`,
      url,
      python: `${source}: ${command}`,
    });
    return { ...snapshot(), health };
  } catch (err) {
    stopBackend();
    setState({ status: 'error', message: err.message, url: null });
    throw err;
  }
}

function backendInfo() {
  return {
    ...snapshot(),
    app: {
      version: app.getVersion(),
      electron: process.versions.electron,
      node: process.versions.node,
      chrome: process.versions.chrome,
      platform: process.platform,
      root: ROOT,
    },
  };
}

function createWindow(onLoaded) {
  win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    show: false,
    backgroundColor: '#070b14',
    title: 'ShimTuner · Flow Studio（流道-压力场）',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  if (!SMOKE) win.once('ready-to-show', () => win.show());
  win.on('closed', () => { win = null; });
  if (onLoaded) win.webContents.once('did-finish-load', onLoaded);
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

async function waitForRendererReady(timeoutMs, predicate) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const ready = await win.webContents.executeJavaScript(
        'window.__shimtunerState ? window.__shimtunerState() : null');
      if (ready && ready.error) return ready;
      if (ready && ready.ready && (!predicate || predicate(ready))) return ready;
    } catch {
      /* 页面尚在加载 */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return null;
}

ipcMain.handle('backend:info', () => backendInfo());
ipcMain.handle('backend:restart', async () => {
  try {
    await startBackend();
  } catch {
    /* 状态已通过事件广播 */
  }
  return backendInfo();
});

ipcMain.handle('app:save-text', async (_event, { name, text }) => {
  const result = await dialog.showSaveDialog(win, {
    title: '导出 FlowSolver 工况',
    defaultPath: name || 'shim-stack.json',
    filters: [{ name: 'JSON', extensions: ['json'] }],
  });
  if (result.canceled || !result.filePath) return { ok: false };
  fs.writeFileSync(result.filePath, text, 'utf8');
  return { ok: true, path: result.filePath };
});

ipcMain.handle('app:open-text', async () => {
  const result = await dialog.showOpenDialog(win, {
    title: '导入 FlowSolver 工况',
    properties: ['openFile'],
    filters: [{ name: 'JSON', extensions: ['json'] }],
  });
  if (result.canceled || result.filePaths.length === 0) return { ok: false };
  const content = fs.readFileSync(result.filePaths[0], 'utf8');
  return { ok: true, path: result.filePaths[0], content };
});

app.whenReady().then(async () => {
  try {
    await startBackend();
  } catch {
    if (SMOKE) {
      console.log(JSON.stringify({ ok: false, state: snapshot() }, null, 2));
      app.exit(1);
      return;
    }
  }

  if (SMOKE) {
    if (state.status !== 'running') {
      console.log(JSON.stringify({ ok: false, state: snapshot() }, null, 2));
      app.exit(1);
      return;
    }
    createWindow(async () => {
      let result = await waitForRendererReady(90000);
      const smokeMode = SMOKE_ROD ? 'rod'
        : SMOKE_CURVE ? 'curve' : SMOKE_ITERATE ? 'iterate' : null;
      if (smokeMode && result && result.ready && !result.error) {
        try {
          await win.webContents.executeJavaScript(
            `document.querySelector('.solver-btn[data-mode="${smokeMode}"]').click(); true`);
          result = await waitForRendererReady(240000,
            (s) => s.solver === smokeMode);
        } catch (err) {
          result = { ready: false, error: String(err) };
        }
      }
      const ok = Boolean(result && result.ready && !result.error
        && (!smokeMode || result.solver === smokeMode));
      console.log(JSON.stringify({
        ok,
        mode: smokeMode || 'single',
        backend: state.url,
        python: state.python,
        renderer: result,
      }, null, 2));
      stopBackend();
      app.exit(ok ? 0 : 1);
    });
    return;
  }

  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', () => { quitting = true; });
app.on('will-quit', stopBackend);
