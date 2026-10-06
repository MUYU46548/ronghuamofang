// 绒花墨坊控制台 — preload 桥（contextIsolation 安全暴露）
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mofangAPI", {
  readPreview: (relPath) => ipcRenderer.invoke("read-preview", relPath),
  openArtifact: (relPath) => ipcRenderer.invoke("open-artifact", relPath),
  // 直接用系统默认程序打开文件（用户显式点击「打开」时调用）
  openFile: (relPath) => ipcRenderer.invoke("open-file", relPath),
  // 在文件夹中定位（不打开内容）——用于 .env 等含密钥的敏感文件
  revealInFolder: (relPath) => ipcRenderer.invoke("reveal-in-folder", relPath),
  // 接入其他 Agent：MCP 配置段生成（真实路径）+ 打开目标配置文件
  agentConnectInfo: () => ipcRenderer.invoke("agent-connect:info"),
  agentOpenConfig: (target) => ipcRenderer.invoke("agent-connect:open-config", target),
  // 诊断与调试入口（设置 → 诊断与调试）
  debugInfo: () => ipcRenderer.invoke("debug:info"),
  debugOpenLog: () => ipcRenderer.invoke("debug:open-log"),
  debugToggleDevtools: () => ipcRenderer.invoke("debug:devtools"),
  debugRestartApi: () => ipcRenderer.invoke("debug:restart-api"),
  // 审批门系统通知（主进程 OS 级通知，无需授权 —— 修「通知权限是死按钮」）
  notifyGate: (opts) => ipcRenderer.invoke("notify:gate", opts),
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
  // ---- 退出守卫（2026-10-03）----
  // 渲染进程只负责：① 上报"我这儿有没有在忙"；② 主进程问过来时弹**应用内**确认框。
  // 关窗拦不拦、超时怎么办，都在主进程里判（见 main/quitGuard.js 的说明）。
  setQuitBusy: (busy) => ipcRenderer.send("app:busy", !!busy),
  onCloseRequested: (callback) => {
    const handler = () => callback();
    ipcRenderer.on("app:close-requested", handler);
    return () => ipcRenderer.removeListener("app:close-requested", handler);
  },
  confirmQuit: () => ipcRenderer.invoke("app:quit-confirmed"),
  cancelQuit: () => ipcRenderer.invoke("app:quit-canceled"),
  // 退出必确认设置（默认开；设置页可关）
  getConfirmOnExit: () => ipcRenderer.invoke("app:get-confirm-on-exit"),
  setConfirmOnExit: (v) => ipcRenderer.invoke("app:set-confirm-on-exit", !!v),
});
