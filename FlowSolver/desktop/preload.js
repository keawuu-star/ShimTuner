'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('shimtuner', {
  getBackendInfo: () => ipcRenderer.invoke('backend:info'),
  restartBackend: () => ipcRenderer.invoke('backend:restart'),
  saveText: (name, text) => ipcRenderer.invoke('app:save-text', { name, text }),
  openText: () => ipcRenderer.invoke('app:open-text'),
  projectList: () => ipcRenderer.invoke('project:list'),
  projectOpen: (path) => ipcRenderer.invoke('project:open', { path }),
  projectSave: (name, text, path) =>
    ipcRenderer.invoke('project:save', { name, text, path }),
  onBackendStatus: (callback) => {
    const handler = (_event, payload) => callback(payload);
    ipcRenderer.on('backend:status', handler);
    return () => ipcRenderer.removeListener('backend:status', handler);
  },
});
