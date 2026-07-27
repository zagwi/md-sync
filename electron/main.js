// Electron shell for md-sync.
// Reuses the existing FastAPI backend (md_sync.web.app via start_server.py)
// and the existing web dashboard — no UI/backend code is duplicated here.
const { app, BrowserWindow, Menu } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..'); // md_sync project root
const HOST = '127.0.0.1';
const PORT = 8580;
const URL = `http://${HOST}:${PORT}`;

let backend = null;

async function probe() {
  try {
    const r = await fetch(URL + '/api/status', { signal: AbortSignal.timeout(800) });
    return r.ok;
  } catch {
    return false;
  }
}

function startBackend() {
  backend = spawn('python', ['start_server.py'], {
    cwd: ROOT,
    stdio: ['ignore', 'inherit', 'inherit'],
  });
  backend.on('error', (e) => console.error('[backend] 启动失败:', e.message));
  backend.on('exit', (code) => {
    if (code !== null && code !== 0) console.warn(`[backend] 进程退出, code=${code}`);
  });
}

async function waitForServer(timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await probe()) return;
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error('后端启动超时（可能 python 未安装或 start_server.py 报错）');
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1000,
    height: 1040,
    minWidth: 760,
    title: 'md-sync · Markdown 同步',
    backgroundColor: '#f5f6f8',
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  Menu.setApplicationMenu(null);
  win.loadURL(URL);
}

function shutdown() {
  if (backend) {
    try { backend.kill('SIGTERM'); } catch {}
    backend = null;
  }
}

app.whenReady().then(async () => {
  // Reuse an already-running backend if port 8580 is occupied.
  if (!(await probe())) startBackend();
  try {
    await waitForServer();
  } catch (e) {
    console.error(e.message);
  }
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  shutdown();
  if (process.platform !== 'darwin') app.quit();
});
app.on('before-quit', shutdown);
