// 绒花墨坊桌面控制台 — Electron 主进程
// 职责：自动拉起 nf_api（随应用退出回收）；IPC 文件预览（白名单只读）+ 打开产物目录
const { app, BrowserWindow, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..", ".."); // NovelForge 项目根（console/ 的上级）
const PY = path.join(ROOT, ".venv", "Scripts", "python.exe");
const API_PORT = 8765;
const BASE = "http://127.0.0.1:" + API_PORT;

let apiProc = null;
let mainWindow = null;

// ---------- nf_api 生命周期 ----------
function startApi() {
  if (!fs.existsSync(PY)) {
    console.error("[console] 未找到 .venv python:", PY);
    return;
  }
  const logPath = path.join(process.env.LOCALAPPDATA || ROOT, "Temp", "nf_api_child.log");
  const out = fs.openSync(logPath, "a");
  apiProc = spawn(PY, [path.join(ROOT, "scripts", "nf_api.py"), "--port", String(API_PORT)], {
    cwd: ROOT,
    stdio: ["ignore", out, out],
    windowsHide: true,
  });
  console.log("[console] nf_api 子进程已启动 pid=", apiProc.pid, "日志→", logPath);
  apiProc.on("error", (e) => console.error("[console] nf_api spawn 失败:", e));
  apiProc.on("exit", (code) => {
    console.log("[nf_api] 退出 code=", code);
    apiProc = null;
  });
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

// ---------- IPC ----------
// 文件预览白名单：data/**、output/**、logs/*.md|log、config/*.yaml、materials/*.md（只读）
function pathAllowed(p) {
  const norm = path.resolve(p).replace(/\\/g, "/").toLowerCase();
  const root = ROOT.replace(/\\/g, "/").toLowerCase() + "/";
  if (!norm.startsWith(root)) return false;
  const rel = norm.slice(root.length);
  if (rel.startsWith("data/") || rel.startsWith("output/")) return true;
  if ((rel.startsWith("logs/") || rel.startsWith("materials/")) && /\.(md|log|json)$/.test(rel)) return true;
  if (rel.startsWith("config/") && /\.(yaml|yml)$/.test(rel)) return true;
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

// ---------- 窗口 ----------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    title: "绒花墨坊",
    autoHideMenuBar: true,
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
}

process.on("uncaughtException", (e) => {
  try {
    const p = path.join(process.env.LOCALAPPDATA || ROOT, "Temp", "nf_console_crash.log");
    fs.appendFileSync(p, new Date().toISOString() + " " + (e.stack || String(e)) + "\n");
  } catch (e2) { /* ignore */ }
  console.error("[console] uncaught:", e);
});
// 主进程就绪前的启动失败（如 package.json 损坏）也落日志，不弹 GUI 框
process.on("unhandledRejection", (e) => {
  try {
    const p = path.join(process.env.LOCALAPPDATA || ROOT, "Temp", "nf_console_crash.log");
    fs.appendFileSync(p, new Date().toISOString() + " [rejection] " + (e && (e.stack || String(e))) + "\n");
  } catch (e2) { /* ignore */ }
});

app.whenReady().then(async () => {
  console.log("[console] app ready, ROOT=", ROOT);
  startApi();
  const ok = await waitForApi();
  console.log("[console] nf_api 健康检查:", ok ? "通过" : "超时");
  if (!ok) console.error("[console] nf_api 健康检查超时（界面将显示离线状态）");
  createWindow();
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
