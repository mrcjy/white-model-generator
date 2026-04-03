const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  openShp:   ()     => ipcRenderer.invoke('dialog:openShp'),
  openDir:   ()     => ipcRenderer.invoke('dialog:openDir'),
  getFields: (p)    => ipcRenderer.invoke('shp:getFields', p),
  generate:  (opts) => ipcRenderer.invoke('tiles:generate', opts),
  onLog:     (cb)   => ipcRenderer.on('tiles:log',  (_, line) => cb(line)),
  onDone:    (cb)   => ipcRenderer.on('tiles:done', (_, ok)   => cb(ok)),
});
