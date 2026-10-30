const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('bot', {
  send: (msg) => ipcRenderer.send('cmd', msg),
  onEvent: (cb) => ipcRenderer.on('event', (_e, ev) => cb(ev)),
  openPath: (p) => ipcRenderer.send('open-path', p),
  openUrl: (url) => ipcRenderer.send('open-url', url),
});
