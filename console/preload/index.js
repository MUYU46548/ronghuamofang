// 绒花墨坊控制台 — preload 桥（contextIsolation 安全暴露）
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mofangAPI", {
  readPreview: (relPath) => ipcRenderer.invoke("read-preview", relPath),
  openArtifact: (relPath) => ipcRenderer.invoke("open-artifact", relPath),
  openFileDialog: (options) => ipcRenderer.invoke("open-file-dialog", options),
  // 提示词模板编辑器（prompts/stage[1-7]_*.md）
  promptsList: () => ipcRenderer.invoke("prompts:list"),
  promptsGet: (name) => ipcRenderer.invoke("prompts:get", name),
  promptsSave: (name, content) => ipcRenderer.invoke("prompts:save", name, content),
  // 用户风格笔记（config/project.yaml 的 book.style_notes）
  styleNotesGet: () => ipcRenderer.invoke("style-notes:get"),
  styleNotesSave: (content) => ipcRenderer.invoke("style-notes:save", content),
  // 关于弹窗：外链与应用环境
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  appAbout: () => ipcRenderer.invoke("app:about"),
  // 自动更新
  updaterCheck: () => ipcRenderer.invoke("updater:check"),
  updaterStatus: () => ipcRenderer.invoke("updater:status"),
  updaterQuitAndInstall: () => ipcRenderer.invoke("updater:quitAndInstall"),
  onUpdater: (callback) => {
    const handler = (e, data) => callback(data);
    ipcRenderer.on("updater", handler);
    return () => ipcRenderer.removeListener("updater", handler);
  },
});
