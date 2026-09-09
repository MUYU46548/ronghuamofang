// 绒花墨坊控制台 — preload 桥（contextIsolation 安全暴露）
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mofangAPI", {
  readPreview: (relPath) => ipcRenderer.invoke("read-preview", relPath),
  openArtifact: (relPath) => ipcRenderer.invoke("open-artifact", relPath),
});
