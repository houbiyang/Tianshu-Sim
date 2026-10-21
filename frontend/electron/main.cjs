// Privileged desktop boundary: local renderer, allowlisted menus, bundled backend.
const { app, BrowserWindow, Menu, shell, dialog, session } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
if (process.env.GENTACTIX_DESKTOP_DATA) {
  const data = path.resolve(process.env.GENTACTIX_DESKTOP_DATA);
  fs.mkdirSync(data, { recursive: true }); app.setPath('userData', data);
}
const ROOT = path.resolve(__dirname, '..', '..');
function projectRoot() {
  if (process.env.GENTACTIX_PROJECT_ROOT) return path.resolve(process.env.GENTACTIX_PROJECT_ROOT);
  if (!app.isPackaged) return ROOT;
  const directory = path.dirname(process.env.APPIMAGE || app.getPath('exe'));
  let candidate = directory;
  for (let i = 0; i < 5; i++) {
    if (fs.existsSync(path.join(candidate, 'AGENTS.md')) && fs.existsSync(path.join(candidate, 'gentactix'))) return candidate;
    candidate = path.dirname(candidate);
  }
  return directory;
}
const PORT = String(process.env.GENTACTIX_PORT || '8700');
if (!/^\d{2,5}$/.test(PORT) || +PORT > 65535) throw new Error('Invalid backend port');
const BACKEND_URL = `http://127.0.0.1:${PORT}`;
const CONDA_ENV = process.env.GENTACTIX_CONDA_ENV || 'wargame';
let backend = null;
let mainWindow = null;
const externalHosts = new Set(['github.com', 'api-docs.deepseek.com', 'www.openstreetmap.org', 'www.naturalearthdata.com', 'leafletjs.com', 'www.esri.com', 'opentopomap.org', 'www.opentopomap.org']);
function openExternal(url) {
  try { const parsed = new URL(url); if (parsed.protocol === 'https:' && externalHosts.has(parsed.hostname)) void shell.openExternal(parsed.href); } catch { /* Reject malformed links. */ }
}
function sendAction(action) {
  if (mainWindow && ['new-scenario', 'open-llm-settings'].includes(action)) mainWindow.webContents.send('gentactix:menu', action);
}
function installMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: '想定', submenu: [
      { label: '选择 / 创建战役', accelerator: 'CmdOrCtrl+N', click: () => sendAction('new-scenario') },
      { type: 'separator' }, { role: 'quit', label: '退出' },
    ] },
    { label: '配置', submenu: [{ label: 'DSH · LLM配置', accelerator: 'CmdOrCtrl+,', click: () => sendAction('open-llm-settings') }] },
    { label: '视图', submenu: [{ role: 'resetZoom', label: '实际大小' }, { role: 'zoomIn', label: '放大' }, { role: 'zoomOut', label: '缩小' }, { role: 'togglefullscreen', label: '全屏' }] },
    { label: '帮助', submenu: [
      { label: '项目主页', click: () => openExternal('https://github.com/snow-wind-001/Gentactix-DSH') },
      { label: '上游项目与致谢', click: () => openExternal('https://github.com/pystrategylab/Gentactix') },
      { label: '关于', click: () => dialog.showMessageBox(mainWindow, { type: 'info', title: '关于 GenTactix-DSH', message: 'GenTactix-DSH', detail: '沈阳理工大学装备工程学院深度学习实验室\n\n基于 pystrategylab/Gentactix 扩展，感谢原作者开源。保留 MIT 版权与许可证。\n\n历史教学与推演研究工具。', buttons: ['确定'] }) },
    ] },
  ]));
}
async function startBackend() {
  try { const res = await fetch(`${BACKEND_URL}/api/health`); if (res.ok) return; } catch { /* Start local backend. */ }
  const args = ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', PORT];
  let executable, commandArgs, cwd = ROOT, env = { ...process.env, GENTACTIX_PORT: PORT, GENTACTIX_PROJECT_ROOT: projectRoot() };
  if (app.isPackaged) {
    executable = path.join(process.resourcesPath, 'backend', 'gentactix-backend'); commandArgs = [];
    const data = app.getPath('userData');
    env = { ...env, GENTACTIX_SESSION_DIR: path.join(data, 'sessions'), GENTACTIX_TRACE_DIR: path.join(data, 'trajectories'),
      GENTACTIX_LLM_CONFIG: process.env.GENTACTIX_LLM_CONFIG || path.join(projectRoot(), '.gentactix-llm.enc'), GENTACTIX_DSH_HOME: path.join(data, 'dsh-home'),
      GENTACTIX_DSH_WORKSPACE: path.join(data, 'dsh-workspace'), GENTACTIX_ROLE_INDEX: path.join(data, 'roles.json') };
    fs.mkdirSync(data, { recursive: true }); cwd = data;
  } else {
    const probe = spawnSync('conda', ['run', '-n', CONDA_ENV, 'python', '-V'], { encoding: 'utf8' });
    if (probe.status === 0) { executable = 'conda'; commandArgs = ['run', '--no-capture-output', '-n', CONDA_ENV, 'python', ...args]; }
    else { executable = process.env.GENTACTIX_PYTHON || path.join(process.env.HOME || '', 'anaconda3', 'envs', CONDA_ENV, 'bin', 'python'); commandArgs = args; }
  }
  const logDirectory = path.join(app.getPath('userData'), 'logs'); fs.mkdirSync(logDirectory, { recursive: true });
  const log = fs.openSync(path.join(logDirectory, 'backend.log'), 'a');
  backend = spawn(executable, commandArgs, { cwd, env, stdio: ['ignore', log, log] }); fs.closeSync(log);
  backend.on('error', () => console.error('Backend launch failed; see the local log.'));
}
async function waitForBackend() {
  for (let attempt = 0; attempt < 120; attempt++) {
    try { if ((await fetch(`${BACKEND_URL}/api/health`)).ok) return true; } catch { /* Wait for local startup. */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  return false;
}
function createWindow() {
  const win = new BrowserWindow({ width: 1530, height: 980, minWidth: 900, minHeight: 650,
    title: 'GenTactix-DSH · 历史战役推演工作台', backgroundColor: '#071522',
    icon: path.join(__dirname, '..', 'public', 'icons', 'gentactix.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  mainWindow = win;
  const dev = process.env.VITE_DEV_SERVER_URL;
  let localUrl = null;
  if (dev && !app.isPackaged) {
    const parsed = new URL(dev);
    if (parsed.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(parsed.hostname)) localUrl = parsed.href;
  }
  win.webContents.setWindowOpenHandler(({ url }) => { openExternal(url); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (event, url) => {
    if (url !== win.webContents.getURL()) { event.preventDefault(); openExternal(url); }
  });
  if (localUrl) void win.loadURL(localUrl);
  else void win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  win.on('closed', () => { mainWindow = null; });
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); } });
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    installMenu(); await startBackend();
    if (!await waitForBackend()) console.error('Local backend is unavailable; the interface will show connection guidance.');
    createWindow();
  });
}
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { if (backend) backend.kill('SIGTERM'); });
