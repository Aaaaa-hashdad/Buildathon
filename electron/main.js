// Electron shell for Job Alert. The bot itself runs in Python (backend.py);
// this file starts it, passes messages back and forth, and manages the windows.
const { app, BrowserWindow, ipcMain, shell, screen } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

const BOT_DIR = path.join(__dirname, '..');
let win = null;
let badge = null;
let backend = null;
let saved = 0;

function startBackend() {
  backend = spawn('python', ['-u', 'backend.py'], { cwd: BOT_DIR, windowsHide: true });
  let buf = '';
  backend.stdout.on('data', (chunk) => {
    buf += chunk.toString('utf8');
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      try { handleEvent(JSON.parse(line)); } catch { /* not JSON, ignore */ }
    }
  });
  backend.stderr.on('data', (d) => send({ type: 'log', text: d.toString('utf8').trim() }));
  backend.on('exit', (code) => send({ type: 'log', text: `Bot process stopped (code ${code}).` }));
}

function toBackend(msg) {
  if (backend && backend.stdin.writable) backend.stdin.write(JSON.stringify(msg) + '\n');
}

function send(ev) {
  if (win && !win.isDestroyed()) win.webContents.send('event', ev);
  // The badge only shows the status line and a count, so it gets nothing else.
  if (badge && !badge.isDestroyed() && (ev.type === 'status' || ev.type === 'row')) {
    badge.webContents.send('event', ev.type === 'row' ? { type: 'row' } : ev);
  }
}

function handleEvent(ev) {
  if (ev.type === 'row') saved += 1;
  if (ev.type === 'done') closeBadge();
  send(ev);
}

function openBadge() {
  closeBadge();
  saved = 0;
  const { workArea } = screen.getPrimaryDisplay();
  const w = 340, h = 110;
  badge = new BrowserWindow({
    width: w, height: h, x: workArea.x + workArea.width - w - 16, y: workArea.y + workArea.height - h - 16,
    frame: false, resizable: false, alwaysOnTop: true, skipTaskbar: true, show: false,
    backgroundColor: '#1d2226',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  });
  badge.setAlwaysOnTop(true, 'screen-saver');
  badge.loadFile('badge.html');
  badge.once('ready-to-show', () => badge.showInactive());   // don't steal focus from Chrome
}

function closeBadge() {
  if (badge && !badge.isDestroyed()) badge.close();
  badge = null;
}

app.whenReady().then(() => {
  startBackend();
  win = new BrowserWindow({
    width: 820, height: 940, minWidth: 600, minHeight: 640,
    title: 'Job Alert', autoHideMenuBar: true, backgroundColor: '#f5f6f8', show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  });
  win.loadFile('index.html');
  win.once('ready-to-show', () => win.show());     // no blank white flash while the page loads
  win.on('closed', () => { win = null; app.quit(); });
  // The Chrome light is polled by the page, only while LinkedIn is the chosen source.

  // Dev check: BOT_SCREENSHOT=<file.png> saves the window (and a demo badge) to an image, then quits.
  // BOT_SCREENSHOT_KIND=boards (career sites) or people/companies/jobs (LinkedIn) picks that first.
  if (process.env.BOT_SCREENSHOT) {
    win.setAlwaysOnTop(true);     // a covered window captures as blank
    const kind = process.env.BOT_SCREENSHOT_KIND;
    if (kind) {
      const source = kind === 'boards' ? 'boards' : 'linkedin';
      win.webContents.once('did-finish-load', () => win.webContents.executeJavaScript(
        `document.querySelector('input[name=source][value=${source}]').checked = true;
         if (${JSON.stringify(kind)} !== 'boards') document.getElementById('kind').value = ${JSON.stringify(kind)};
         document.getElementById('kind').dispatchEvent(new Event('change'));`));
    }
    // BOT_SCREENSHOT_SCROLL=bottom captures the end of the page instead of the top.
    if (process.env.BOT_SCREENSHOT_SCROLL === 'bottom') {
      win.webContents.once('did-finish-load', () => setTimeout(() => win.webContents.executeJavaScript(
        'window.scrollTo(0, document.body.scrollHeight)'), 3000));
    }
    win.webContents.once('did-finish-load', () => setTimeout(async () => {
      const fs = require('fs');
      fs.writeFileSync(process.env.BOT_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
      openBadge();
      badge.webContents.once('did-finish-load', () => setTimeout(async () => {
        fs.writeFileSync(process.env.BOT_SCREENSHOT.replace('.png', '_badge.png'),
          (await badge.webContents.capturePage()).toPNG());
        app.quit();
      }, 800));
    }, 4000));
  }
});

ipcMain.on('cmd', (_e, msg) => {
  if (msg.cmd === 'start') openBadge();
  toBackend(msg);
});

ipcMain.on('open-path', (_e, p) => { if (p) shell.openPath(p); });
ipcMain.on('open-url', (_e, url) => {
  if (/^https:\/\//.test(url)) shell.openExternal(url);
});

app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { if (backend) backend.kill(); });
