// 绒花墨坊桌面控制台 — Electron 主进程
const { app, BrowserWindow, ipcMain, shell, Menu, dialog } = require("electron");
app.disableHardwareAcceleration();
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

// 自动更新（生产模式 + 非 portable 模式）
let autoUpdater = null;
let updateAvailable = false;
let updateDownloaded = false;

function setupAutoUpdater() {
  if (process.env.NODE_ENV === "development") return;
  // 未打包（源码直跑 electron .）时自动更新没有意义，且 electron-updater 会打印
  // "Skip checkForUpdates because application is not packed" 这类干扰日志
  if (!app.isPackaged) return;
  if (process.windowsStore || process.env.PORTABLE_EXEC_DIR) return;
  try {
    const { autoUpdater: au } = require("electron-updater");
    autoUpdater = au;
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.allowDowngrade = false;
    autoUpdater.logger = console;

    autoUpdater.on("checking-for-update", () => {
      console.log("[updater] checking for updates...");
    });
    autoUpdater.on("update-available", (info) => {
      console.log("[updater] update available:", info.version);
      updateAvailable = true;
      if (mainWindow) {
        mainWindow.webContents.send("updater", {
          type: "update-available",
          version: info.version,
          releaseNotes: info.releaseNotes,
        });
      }
    });
    autoUpdater.on("update-not-available", () => {
      console.log("[updater] already up to date");
    });
    autoUpdater.on("download-progress", (progress) => {
      console.log(`[updater] downloading ${progress.percent.toFixed(1)}%`);
      if (mainWindow) {
        mainWindow.webContents.send("updater", {
          type: "download-progress",
          percent: progress.percent,
          bytesPerSecond: progress.bytesPerSecond,
        });
      }
    });
    autoUpdater.on("update-downloaded", (info) => {
      console.log("[updater] update downloaded, installs on quit");
      updateDownloaded = true;
      if (mainWindow) {
        mainWindow.webContents.send("updater", {
          type: "update-downloaded",
          version: info.version,
        });
      }
    });
    autoUpdater.on("error", (err) => {
      console.error("[updater] error:", err.message);
      if (mainWindow) {
        mainWindow.webContents.send("updater", {
          type: "error",
          message: err.message,
        });
      }
    });
  } catch (e) {
    console.error("[updater] init failed:", e.message);
  }
}

// 双轨路径：打包态 vs 源码态
const isPackaged = app.isPackaged;
const ROOT = isPackaged
  ? path.join(process.resourcesPath, "payload")
  : path.resolve(__dirname, "..", "..");
const PY = isPackaged
  ? path.join(process.resourcesPath, "runtime", "python", "python.exe")
  : path.join(ROOT, ".venv", "Scripts", "pythonw.exe");
const API_PORT = 8765;

// 用户数据目录（可写）：统一放 %APPDATA%\绒花墨坊\workspace
// 便携版不再放 temp 目录（每次解压路径不同 + 旧进程锁目录 = 端口冲突）
function getWorkspaceDir() {
  return path.join(app.getPath("appData"), "绒花墨坊", "workspace");
}

// 首启：种子 payload 进 workspace + 建 data/ 目录结构
// 打包态：从 process.resourcesPath/payload 复制；源码态：从项目根目录 ROOT 复制
function seedWorkspace() {
  const ws = getWorkspaceDir();
  if (fs.existsSync(path.join(ws, ".seeded"))) return;
  const payloadRoot = isPackaged
    ? path.join(process.resourcesPath, "payload")
    : ROOT;
  const seedDirs = ["scripts", "prompts", "config", "templates"];
  for (const d of seedDirs) {
    const src = path.join(payloadRoot, d);
    const dst = path.join(ws, d);
    if (fs.existsSync(src) && !fs.existsSync(dst)) {
      fs.cpSync(src, dst, { recursive: true });
    }
  }
  // 预建 data/ 目录结构
  ["data/state", "data/outline/chapters", "data/setting", "data/chapters/raw", "data/chapters/checked", "data/chapters/refined", "data/books"].forEach(d => {
    fs.mkdirSync(path.join(ws, d), { recursive: true });
  });
  fs.writeFileSync(path.join(ws, ".seeded"), "1");
}
const BASE = "http://127.0.0.1:" + API_PORT;

let apiProc = null;
let mainWindow = null;

function startApi() {
  if (!fs.existsSync(PY)) {
    console.error("[console] python not found:", PY);
    return;
  }
  const net = require("net");
  const tester = net.createServer();
  tester.once("error", (e) => {
    if (e.code === "EADDRINUSE") {
      console.warn(`[console] port ${API_PORT} in use, killing old process...`);
      // Kill whatever is using our port, then retry
      try {
        require("child_process").execSync(`netstat -ano | findstr :${API_PORT} | findstr LISTENING`, { stdio: "pipe" })
          .toString().split("\n").forEach(line => {
            const pid = line.trim().split(/\s+/).pop();
            if (pid && /^\d+$/.test(pid)) {
              console.log(`[console] killing PID ${pid}`);
              try { process.kill(parseInt(pid)); } catch (e) {}
            }
          });
      } catch (e) {}
      setTimeout(() => {
        tester.close();
        startApi();
      }, 1000);
      return;
    }
    console.error("[console] port probe error:", e);
  });
  tester.once("listening", () => {
    tester.close();
    const ws = getWorkspaceDir();
    fs.mkdirSync(ws, { recursive: true });
    const apiScript = path.join(ws, "scripts", "nf_api.py");
    const logPath = path.join(process.env.LOCALAPPDATA || ROOT, "Temp", "nf_api_child.log");
    const out = fs.openSync(logPath, "a");
    const cmd = PY;
    const args = [apiScript, "--port", String(API_PORT), "--root", ws];
    console.log("[console] spawn nf_api:", cmd, args.join(" "));
    apiProc = spawn(cmd, args, {
      cwd: ws,
      stdio: ["ignore", out, out],
      windowsHide: true,
    });
    console.log("[console] nf_api child started pid=", apiProc.pid, "log ->", logPath);
    apiProc.on("error", (e) => console.error("[console] nf_api spawn failed:", e));
    apiProc.on("exit", (code) => {
      console.log("[nf_api] exited code=", code);
      apiProc = null;
    });
  });
  tester.listen(API_PORT);
}

function stopApi() {
  if (apiProc) {
    try { apiProc.kill(); } catch (e) { /* ignore */ }
    apiProc = null;
  }
}

async function waitForApi(timeoutMs = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(BASE + "/health");
      if (r.ok) return true;
    } catch (e) { /* not ready */ }
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function pathAllowed(p) {
  const norm = path.resolve(p).replace(/\\/g, "/").toLowerCase();
  const root = ROOT.replace(/\\/g, "/").toLowerCase() + "/";
  const ws = getWorkspaceDir().replace(/\\/g, "/").toLowerCase() + "/";
  if (!norm.startsWith(root) && !norm.startsWith(ws)) return false;
  const rel = norm.slice(root.length);
  // 允许直接打开这几个白名单目录本身（关于弹窗的「打开目录」按钮）
  if (["data", "output", "logs", "materials", "config"].includes(rel)) return true;
  if (rel.startsWith("data/") || rel.startsWith("output/")) return true;
  if ((rel.startsWith("logs/") || rel.startsWith("materials/")) && /\.(md|log|json)$/.test(rel)) return true;
  if (rel.startsWith("config/") && /\.(yaml|yml)$/.test(rel)) return true;
  if (rel === ".env") return true;  // Allow opening .env in external editor
  return false;
}

ipcMain.handle("read-preview", async (e, relPath) => {
  try {
    const abs = path.resolve(ROOT, relPath);
    if (!pathAllowed(abs)) return { ok: false, error: "路径不在白名单: " + relPath };
    if (!fs.existsSync(abs)) return { ok: false, error: "文件不存在: " + relPath };
    const stat = fs.statSync(abs);
    if (stat.size > 2 * 1024 * 1024) return { ok: false, error: "文件过大（>2MB）" };
    return { ok: true, content: fs.readFileSync(abs, "utf-8"), size: stat.size };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle("open-artifact", async (e, relPath) => {
  const abs = path.resolve(ROOT, relPath);
  if (!pathAllowed(abs)) return { ok: false, error: "路径不在白名单" };
  const r = await shell.openPath(fs.existsSync(abs) ? abs : path.dirname(abs));
  return r ? { ok: false, error: r } : { ok: true };
});

ipcMain.handle("open-file-dialog", async (e, options = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: options.title || "选择文件",
    properties: options.properties || ["openFile"],
    filters: options.filters || [
      { name: "素材文件", extensions: ["txt", "md", "markdown", "docx", "doc", "png", "jpg", "jpeg", "gif", "webp"] },
      { name: "所有文件", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths?.length) {
    return { ok: false, error: "未选择文件" };
  }
  return { ok: true, paths: result.filePaths };
});

// 打开外部链接（关于页的 GitHub/许可等）。只放行 http(s)，其它协议一律拒绝
ipcMain.handle("open-external", async (e, url) => {
  try {
    const u = new URL(String(url || ""));
    if (u.protocol !== "http:" && u.protocol !== "https:") {
      return { ok: false, error: "只允许打开 http/https 链接" };
    }
    await shell.openExternal(u.toString());
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String((err && err.message) || err) };
  }
});

// 应用与运行环境概览（关于弹窗的 Electron 侧信息）
ipcMain.handle("app:about", async () => ({
  version: app.getVersion(),
  electron: process.versions.electron,
  chrome: process.versions.chrome,
  node: process.versions.node,
  packaged: app.isPackaged,
  userDataDir: app.getPath("userData"),
  projectRoot: ROOT,
  apiPort: API_PORT,
}));

// 提示词模板：代理到 nf_api（白名单/备份逻辑以 Python 侧为唯一真源，此处再做一次前置校验）
const PROMPT_NAME_RE = /^stage[1-7]_.*\.md$/;

function promptNameAllowed(name) {
  if (typeof name !== "string" || !name) return false;
  if (name.includes("..") || name.includes("/") || name.includes("\\")) return false;
  return PROMPT_NAME_RE.test(name);
}

async function apiJson(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(BASE + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* 非 JSON 响应 */ }
  return { status: r.status, data };
}

ipcMain.handle("prompts:list", async () => {
  try {
    const r = await apiJson("/prompts/list");
    if (r.status !== 200) return { ok: false, error: r.data.error || "HTTP " + r.status };
    return { ok: true, items: r.data.items || [], count: r.data.count || 0 };
  } catch (err) {
    return { ok: false, error: "nf_api 不可用: " + String(err && err.message || err) };
  }
});

ipcMain.handle("prompts:get", async (e, name) => {
  if (!promptNameAllowed(name)) return { ok: false, error: "文件名不在白名单: " + String(name) };
  try {
    const r = await apiJson("/prompts/get?name=" + encodeURIComponent(name));
    if (r.status !== 200) return { ok: false, error: r.data.error || "HTTP " + r.status };
    return { ok: true, ...r.data };
  } catch (err) {
    return { ok: false, error: "nf_api 不可用: " + String(err && err.message || err) };
  }
});

ipcMain.handle("prompts:save", async (e, name, content) => {
  if (!promptNameAllowed(name)) return { ok: false, error: "文件名不在白名单: " + String(name) };
  if (typeof content !== "string") return { ok: false, error: "content 必须为字符串" };
  try {
    const r = await apiJson("/prompts/save", "POST", { name, content });
    if (r.status !== 200) return { ok: false, error: r.data.error || "HTTP " + r.status };
    return { ok: true, ...r.data };
  } catch (err) {
    return { ok: false, error: "nf_api 不可用: " + String(err && err.message || err) };
  }
});

// 用户风格笔记：代理到 nf_api（project.yaml 定向改写 + 备份逻辑以 Python 侧为唯一真源）
ipcMain.handle("style-notes:get", async () => {
  try {
    const r = await apiJson("/config/style_notes");
    if (r.status !== 200) return { ok: false, error: r.data.error || "HTTP " + r.status };
    return { ok: true, content: r.data.content || "" };
  } catch (err) {
    return { ok: false, error: "nf_api 不可用: " + String((err && err.message) || err) };
  }
});

ipcMain.handle("style-notes:save", async (e, content) => {
  if (typeof content !== "string") return { ok: false, error: "content 必须为字符串" };
  if (content.length > 4000) return { ok: false, error: "风格笔记过长（上限 4000 字）" };
  try {
    const r = await apiJson("/config/style_notes", "POST", { content });
    if (!r.data.ok) return { ok: false, error: r.data.error || r.data.message || "HTTP " + r.status };
    return { ok: true, message: r.data.message || "已保存" };
  } catch (err) {
    return { ok: false, error: "nf_api 不可用: " + String((err && err.message) || err) };
  }
});

// 自动更新 IPC
ipcMain.handle("updater:check", async () => {
  if (!autoUpdater) return { ok: false, error: "updater 未初始化" };
  try {
    const result = await autoUpdater.checkForUpdates();
    return { ok: true, result };
  } catch (e) {
    return { ok: false, error: e.message };
  }
});

ipcMain.handle("updater:quitAndInstall", () => {
  if (autoUpdater && updateDownloaded) {
    autoUpdater.quitAndInstall();
  }
});

ipcMain.handle("updater:status", () => ({
  available: updateAvailable,
  downloaded: updateDownloaded,
  initialized: autoUpdater !== null,
  dev: process.env.NODE_ENV === "development" || !app.isPackaged,
}));

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    title: "绒花墨坊",
    // Windows 用多尺寸 ICO（系统按 DPI 自动选帧，避免高分屏模糊）；其余平台用 256px PNG
    icon: path.join(__dirname, "..", "build", process.platform === "win32" ? "icon.ico" : "icon.png"),
    autoHideMenuBar: true,
    backgroundColor: "#eef0f4",
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  if (process.env.NODE_ENV === "development") {
    mainWindow.loadURL("http://localhost:5180");
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "renderer", "dist", "index.html"));
  }
  // 外链一律交给系统浏览器；窗口内导航只允许本地页面（file:// 与 dev server）
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (e, url) => {
    const okLocal = url.startsWith("file://") || url.startsWith("http://localhost:5180");
    if (!okLocal) {
      e.preventDefault();
      if (/^https?:/i.test(url)) shell.openExternal(url);
    }
  });
  mainWindow.show();
  mainWindow.focus();
  mainWindow.center();
}

app.whenReady().then(async () => {
  seedWorkspace();
  const ws = getWorkspaceDir();
  const seedMarker = path.join(ws, ".seeded");
  const configOk = fs.existsSync(path.join(ws, "config", "system.yaml"));
  console.log("[console] app ready, ROOT=", ROOT, "workspace=", ws, "seeded=", fs.existsSync(seedMarker), "configOk=", configOk);
  if (!configOk) {
    console.error("[console] FATAL: workspace missing config/system.yaml after seedWorkspace()");
    dialog.showErrorBox("启动失败", "工作区配置文件缺失，请尝试删除 %APPDATA%\\绒花墨坊\\workspace 后重新启动。");
    app.quit();
    return;
  }
  setupAutoUpdater();
  startApi();
  const ok = await waitForApi();
  console.log("[console] nf_api health:", ok ? "ok" : "timeout");
  if (!ok) console.error("[console] nf_api health check timed out (UI will show offline)");
  createWindow();
  // 启动后 3 秒检查更新（避免阻塞 UI 初始化）
  if (autoUpdater) {
    setTimeout(() => {
      autoUpdater.checkForUpdates().catch((e) => console.error("[updater] check failed:", e.message));
    }, 3000);
  }
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopApi();
  app.quit();
});

app.on("before-quit", stopApi);
process.on("exit", stopApi);
