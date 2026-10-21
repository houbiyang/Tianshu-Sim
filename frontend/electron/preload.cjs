const { contextBridge, ipcRenderer } = require('electron');
const port = String(process.env.GENTACTIX_PORT || '8700');
contextBridge.exposeInMainWorld('gentactix', {
  backendUrl: `http://127.0.0.1:${/^\d{2,5}$/.test(port) ? port : '8700'}`,
  onMenuAction(callback) {
    if (typeof callback !== 'function') throw new TypeError('A menu callback is required');
    const handler = (_event, action) => { if (['open-llm-settings', 'new-scenario'].includes(action)) callback(action); };
    ipcRenderer.on('gentactix:menu', handler);
    return () => ipcRenderer.removeListener('gentactix:menu', handler);
  },
});
