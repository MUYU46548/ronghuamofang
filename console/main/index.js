// 绒花墨坊桌面控制台 — Electron 主进程
const { app, BrowserWindow, ipcMain, shell, Menu, dialog, Notification } = require("electron");
app.disableHardwareAcceleration();
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const path = require("path");
const fs = require("fs");
const { shouldGuardClose, closeAskTimedOut, killTreeArgv,
        shouldRetryPortProbe, parseNetstatListeningPids,
        MAX_PORT_PROBE_RETRIES } = require("./quitGuard");
// 项目根显式解析（2026-10-04 Step 4c 根修）：纯函数模块，node 可直测
const { configFileFor, resolveProjectRoot, persistRoot } = require("./project-root");

// ---------------------------------------------------------------------------
// 退出守卫状态（2026-10-03：修「关不掉」）
//
// 旧实现把退出确认挂在渲染进程的 window.beforeunload 上，触发条件含
// 「有 stage 是 done 但未审批」= 审批门的长期正常状态 → 每次关窗都弹
// Chromium 原生框；该框可能跑到窗口后面、或被 Esc 吃掉 → 用户以为"关不掉"。
// 现在：渲染进程只上报 busy，**主进程**决定拦不拦，确认走应用内对话框，
// 并且永远有看门狗兜底（宁可丢掉一次确认，也不能把用户关在应用里）。
// ---------------------------------------------------------------------------
let rendererBusy = false;      // 渲染进程上报：有运行中 job / 未审批阶段
let isQuitting = false;        // 用户已确认退出或已在退出流程中
let lastCloseAskAt = 0;        // 上次询问时间（连点合并）
let closeAskTimer = null;      // 看门狗定时器
let closeAskAnswered = false;  // 渲染进程已回执（确认框已接手）→ 看门狗退场
let updateInstalling = false;  // 正在装更新（此时不许硬退出）
let confirmOnExit = true;      // 退出必确认（默认开；设置页可关，存 console-settings.json）

// ---------------------------------------------------------------- GUI 来源 token（2026-10-06 红队修复）
// 为什么要有：受保护写入端点（审批/打回/归档增删改/止烧阈值/Agent 模式）原先只认
// `X-Mofang-Source: gui` 这个**客户端自述头**，任何本机进程（curl / 外部 Agent）
// 随手加一个就能绕过全部守卫 —— 实测伪造后 /project/archive/delete 返回 200 真删成功。
// 现改为双因子：来源头 + 本 token。token 由主进程生成，只有两条出口：
//   ① spawn nf_api 时经 env `NF_GUI_TOKEN` 注入（Python 侧校验）；
//   ② 渲染层经 IPC `gui-token:get` 取得，随请求头 `X-Mofang-Token` 带上。
// 外部 Agent 走 HTTP 拿不到它（在主进程内存里），伪造来源头无用。
// 每次启动重新生成 → 会话级，不落盘、不持久化（泄漏也只影响当次会话）。
const guiToken = crypto.randomBytes(32).toString("hex");

// 退出确认设置持久化（userData/console-settings.json；损坏/缺失视为默认开）
function readConsoleSettings() {
  try {
    return JSON.parse(fs.readFileSync(
      path.join(app.getPath("userData"), "console-settings.json"), "utf8")) || {};
  } catch (e) { return {}; }
}
function writeConsoleSettings(patchObj) {
  try {
    fs.mkdirSync(app.getPath("userData"), { recursive: true });
    fs.writeFileSync(
      path.join(app.getPath("userData"), "console-settings.json"),
      JSON.stringify({ ...readConsoleSettings(), ...patchObj }, null, 2));
    return true;
  } catch (e) { console.error("[console] 写设置失败:", e && e.message); return false; }
}

function killTree(pid) {
  /** 杀整棵进程树（Windows 用 taskkill /T，连孙子一起）。返回是否失败。 */
  const spec = killTreeArgv(pid, process.platform);
  if (!spec) return false;
  try {
    spawnSync(spec.argv[0], spec.argv.slice(1), { windowsHide: true, timeout: 10000 });
    return true;
  } catch (e) {
    console.error("[console] killTree failed:", e && e.message);
    return false;
  }
}

function askRendererToClose() {
  /** 拦住关窗 → 问渲染进程（应用内确认框）。同时启动看门狗。 */
  lastCloseAskAt = Date.now();
  closeAskAnswered = false;
  if (!mainWindow || mainWindow.isDestroyed()) { forceQuit("no-window"); return; }
  try { mainWindow.webContents.send("app:close-requested"); }
  catch (e) { console.error("[console] send close-requested failed:", e && e.message); }
  if (closeAskTimer) clearTimeout(closeAskTimer);
  const askedAt = lastCloseAskAt;
  // 看门狗：渲染进程挂了/在 reload/根本没接这个通道 → 到点强制退出。
  closeAskTimer = setTimeout(() => {
    if (!isQuitting && closeAskTimedOut({ askedAt, now: Date.now(),
                                          acked: closeAskAnswered })) {
      console.error("[console] 关窗确认超时（渲染进程未应答）→ 强制退出");
      forceQuit("ask-timeout");
    }
  }, 5000);
}

function forceQuit(why) {
  /** 无条件退出：先杀整棵树，再硬退（任何异常都不许挡住退出）。 */
  console.log("[console] forceQuit:", why);
  isQuitting = true;
  if (closeAskTimer) { clearTimeout(closeAskTimer); closeAskTimer = null; }
  stopApi();
  try { app.exit(0); } catch (e) { process.exit(0); }
}

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
      // 主动回推：主动检查后若「没有新版本」，只靠 checkForUpdates() 的返回值
      // 前端拿不到结论（结果对象不可序列化），必须走事件
      if (mainWindow) {
        mainWindow.webContents.send("updater", {
          type: "no-update",
          message: `已是最新版本（v${app.getVersion()}）`,
        });
      }
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
      const msg = err.message || "";
      if (msg.includes("404") || msg.includes("no published versions")) {
        console.log("[updater] no update available (no GitHub release found)");
        // 404 = 暂无发布，不把完整 HTTP 响应体发给前端（避免用户看到巨大错误）
        if (mainWindow) {
          mainWindow.webContents.send("updater", {
            type: "no-update",
            message: "暂无更新（当前已是最新版本）",
          });
        }
      } else {
        console.error("[updater] error:", msg.slice(0, 120));
        if (mainWindow) {
          mainWindow.webContents.send("updater", {
            type: "error",
            message: msg.slice(0, 200),
          });
        }
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

// 工作区目录（可写）—— 默认值**按态分流**：
//   打包态 → %APPDATA%\绒花墨坊\workspace
//            用户可写、与安装目录解耦，升级不丢数据；便携版不放 temp
//            （每次解压路径不同 + 旧进程锁目录 = 端口冲突）
//   源码态 → 项目根 ROOT
//            **开发即用项目数据**：data/ materials/ config/ .env 全部直读，
//            改了 scripts/ 立即生效。
//            此前源码态也走 appdata，后果是「改项目代码零生效 + 界面看到的是
//            一个空壳工作区（书名待填写 / 暂无素材 / API 未配置）」，开发时
//            完全无法自测，且不报任何错（2026-09-23 排查）。
//
// ⚠️ 2026-10-04 根修（修复单 Step 4c）：默认值之上还有**显式配置**层 ——
// 项目根优先由 --project-root 启动参数 / NF_ROOT 环境变量 / 记忆配置文件决定
// （实现与优先级见 project-root.js），消除「安装版 GUI 无条件指 %APPDATA%
// 工作区 → 与 orchestrator 源码树静默分裂」（双 progress.json、审批门不可见、
// GUI 批准落空 —— 本轮试写事故 P0 病根）。解析结果连同来源
// （source=cli|env|config|default）打启动日志，**无静默回退**。
function defaultWorkspaceDir() {
  if (!isPackaged) return ROOT;
  return path.join(app.getPath("appData"), "绒花墨坊", "workspace");
}

let projectRootResolved = null;   // { root, source } —— 解析一次，进程内恒定

function resolveAndRememberProjectRoot() {
  const cliRoot = app.commandLine.hasSwitch("project-root")
    ? app.commandLine.getSwitchValue("project-root") : "";
  const envRoot = process.env.NF_ROOT || "";
  const cfgFile = configFileFor(app.getPath("appData"));
  const hit = resolveProjectRoot({
    cliRoot, envRoot, configFile: cfgFile, defaultDir: defaultWorkspaceDir(),
    log: console.log, warn: console.warn,
  });
  if (cliRoot && hit.source === "cli") {
    // 「启动参数记住项目根」：写进配置文件，下次启动不再需要参数
    if (persistRoot(cfgFile, hit.root, fs)) {
      console.log("[console] projectRoot 已记住 →", cfgFile);
    } else {
      console.warn("[console] projectRoot 记忆写入失败（本次运行仍生效）:", cfgFile);
    }
  }
  projectRootResolved = hit;
  return hit;
}

function getWorkspaceDir() {
  if (!projectRootResolved) resolveAndRememberProjectRoot();
  return projectRootResolved.root;
}

function getWorkspaceSource() {
  getWorkspaceDir();
  return projectRootResolved.source;
}

// 种子版本号。**改动 payload 内容（scripts/ 或 prompts/）时必须 +1**，
// 否则老用户永远拿不到新代码 —— 见下方 seedWorkspace() 的升级刷新逻辑。
//
// 历史：本轮（2026-09-19）之前 seedWorkspace() 在 .seeded 存在时直接 return，
// 于是「只在首次安装时播种」变成了「永远不再更新」。桌面端用户升级 App 后
// workspace 里跑的还是旧版 orchestrator/stage*/prompts，而 config/ 下的
// system.yaml 又是新的 → 新配置撞旧代码，各种诡异失败且无法从 UI 诊断。
//
// v3（2026-09-23）：payload 内 scripts/ 有实质变更 —— nf_api_domains/misc.py
//   新增 costs/rates 端点、nf_api_domains/project.py 修 Agent 模式签名、
//   utils/cost_tracker.py 增自定义定价读写。按上面的规则 +1，
//   否则已装 0.3.0 的用户升级后 ws 里跑的仍是旧后端。
//
// v4（2026-09-29，①执行单 v2.2）：payload 内 scripts/ + prompts/ 再次实质变更 ——
//   utils/validator.py 解析层容错、utils/llm_client.py 的 reasoning-JSON 捞回与
//   segment 指令移尾、stage2 锚点闸门、orchestrator 质量闸门、nf_mcp 补两把钥匙 +
//   分层指引、stage5 输入构造修复。**必须在 electron-builder 之前 bump**：
//   SEED_VERSION 会被编进 main/index.js（→ app.asar），而 payload 是 electron-builder
//   的 extraResources 在 dist 时从活的 ../scripts、../prompts 现拷的。
//   顺序反了（先 dist 再 bump）→ 装出来的包 SEED_VERSION 还是旧值，用户永远不会刷新。
// v5（2026-09-29 同日，第二批）：payload 再次变更 —— scripts/nf_api.py 的 CORS
//   预检放行 `X-Mofang-Source`（前端每次都发这个头，原先 Allow-Headers 只列了
//   Content-Type → 非 Electron 的浏览器上下文里所有请求被拦）。
//   仍遵守同一条顺序纪律：**先 bump 再 electron-builder**。
// v6（2026-09-29 同日，第三批）：payload 再变 —— `/state` 的 progress.json 改经 ROOT 解析、
//   `--root` 解析加 resolve()、`/about` 版本改「ROOT 优先 → 代码仓库兜底」、
//   `progress_manager` 损坏时不再静默降级（告警 + 留原件）。
//   仍遵守同一条顺序纪律：**先 bump 再 electron-builder**。
// v7（2026-09-29 同日，第四批）：定价批量导入上线 —— payload 新增
//   `POST /costs/rates/import`（后端 utils/cost_tracker 的解析/合并三层 + 域 handler），
//   renderer 侧定价面板加「批量导入」粘贴框。**先 bump 再 electron-builder**。
// v8（2026-09-30，第五批）：`--root` 路径纪律存量清理 —— payload 里
//   nf_api.py 的 12 处相对数据路径改经 ROOT（含 `GLOBAL`/`HISTORY_DIR` 由 _set_root 派生）、
//   nf_api_domains/outline.py 的体检路径。**先 bump 再 electron-builder**。
// v9（2026-09-30，第六批）：payload 新增 `scripts/nf_mcp_stdio_bridge.py`（MCP stdio 垫片，
//   执行单 ⑤前置步0），并在 nf_mcp.py 文件头补上垫片指引。**先 bump 再 electron-builder**。
// v10（2026-09-30，第七批）：垫片文件头里的配置示例改成 `<项目根>` 占位符（原来写的是
//   开发机绝对路径，不该入库），payload 因此再次变化。**先 bump 再 electron-builder**。
// v11（2026-09-30，同批收尾）：payload 新增 `scripts/nf_mcp_handshake_check.py`
//   （MCP 真机握手自检，用户可自己验证接入）、`scripts/nfctl.py` 的 `release-check` 子命令。
//   **先 bump 再 electron-builder**。
// v12（2026-10-01，第八批）：payload 多处变更 ——
//   · `utils/setting_schema.py` 新增 `is_locked` 公开入口（locked 判据单点复用）；
//   · `obsidian_bridge.py` 新增 `check_locked_violations`（locked 结构性违例：缺失/
//     丢锁定/改名）+ `locked` 子命令，`check_consistency` 把它从「未实现」移入「已实现」；
//   · `switch_book.py` 归档范围纳入 `config/` 与 `materials/`（换书不再丢配置与素材卡），
//     并保证归档后工作区仍有 `system.yaml` 与 `project.yaml` 骨架；
//   · `nf_api.py` 的 `/config/provider` 在切供应商后回报模型不匹配的 warning；
//   · `nfctl.py` 新增 `model-check` 子命令（换模型前的通道自检）；
//   · `cost_tracker.py` 补 LongCat 定价占位、`config/system.yaml` 预置 `longcat` provider。
//   **先 bump 再 electron-builder**。
// v13（2026-10-01，第九批）：payload 再变 ——
//   · `utils/template_loader.py`：缺模板时给可执行恢复方式 + 残留占位符告警；
//   · `proofread.py`：修 `load_template` 漏 `.md` 与占位符从未填充两处真 bug；
//   · `stage4_writing.py`：连续失败止损 + 章间预算熔断（`stage5_check` / `stage6_polish` 同步）；
//   · `nfctl.py` 的 `check` 增加提示词模板体检（清单从 `load_template()` 调用点反推）；
//   · 新增 `POST /env/set`（GUI 内写 API Key）与 `POST /prompts/restore`（模板回滚）。
// v14（2026-10-01，第十批）：payload 再变 —— 断点续跑的产物有效性判据
//   （`utils/verify_chapter.is_usable_output`：stage5 的 checked、stage6 的 refined
//   不再"文件存在就跳过"，空壳/残稿会被识别并重跑）。**先 bump 再 electron-builder**。
// v15（2026-10-01，第十一批）：第二轮系统审查的 11 处修复 ——
//   · `orchestrator.py` 后置钩子抽成 `_post_stage()`（"首次失败→重试成功"也要跑，
//     否则审稿门 review_after_stage4 会被跳过）；
//   · `stage3_chapter_outline.py` 断点判据复用 `check_chapter_outline`；
//   · `stage7_convert.py` 的 `merge_book` 逐章取优（不再只取第一个非空目录）+ 校验章数；
//   · `reject.py` 补 `data/outline/chapters`（stage3 自己的产物）与 `data/summaries`；
//   · `utils/cost_tracker.py` 修缓存 token 双重计价；
//   · `utils/verify_chapter.py` 的截断判据不再误伤「……」结尾；
//   · `snapshot.py` 恢复以"全部成功"为成功条件；
//   · `utils/progress_manager.py` 容忍"合法 JSON 但顶层不是对象"；
//   · `nf_api.py` 的 `build_state` 补顶层 `agent_mode`；
//   · `nf_mcp.py` 的 `nf_run_stage` 阶段上限改 7（与后端一致）；
//   · `nf_api.py` 的 GET 分发链加兜底（`do_GET` → `_do_GET_raw`）—— 任何 handler
//     抛异常此前会让请求**直接挂起**（socketserver 断连，无 400 无 500）；
//   · `nf_api_domains/refine.py` 两处裸相对路径改经 `api.ROOT`。
//   **先 bump 再 electron-builder**。
// v16（2026-10-06，第十二批）：payload 内 scripts/ + prompts/ 再次实质变更 ——
//   nf_mcp_stdio_bridge.py 自启动（代拉 nf_api + 项目根同链解析）、
//   prompts/agent_kickoff.md 新增（外部 Agent 启动提示词）。
//   **先 bump 再 electron-builder**。
// v17（2026-10-06，第十三批）：payload 内 scripts/ + prompts/ 再次实质变更 ——
//   · prompts/*.md 清除死字段 `model: hy3`（全项目无一处代码读它；真源是
//     config/system.yaml 的 model.*，三份副本同步）；utils/template_loader.py
//     文档同步 + 新增 tests/unit/test_prompt_frontmatter.py 防回潮；
//   · nf_api_domains/project.py 新增归档「查看/删除」三端点
//     （tree / file / delete，默认进回收站，agent 侧 403）+ nf_api 薄转发；
//   · switch_book.list_books 跳过 _trash；agent_guard 禁用名单加 /project/archive/delete。
//   **先 bump 再 electron-builder**。
// 2026-10-06：GUI 来源双因子（来源头 + 主进程 token）+ 版本号优先取 app.getVersion，
// 安装版不再显示「vdev」+ 退出必确认默认开。scripts/prompts/templates 均有实质变更。
// v19（2026-10-06，第十四批）：payload 内 prompts/agent_kickoff.md 实质变更 ——
//   新增「开发态 ≠ 客户端」三条红线（管线状态只 orchestrator 写 / 含 {{...}} 模板停下报告 /
//   声明工作态并不混用）。console/src/*（App.vue / AboutDialog.vue / style.css）再次变更 ——
//   设置页侧栏分类 + Agent 接入「让它自装」第三渠道 + 关于页清理「内部代号 NovelForge」。
//   **先 bump 再 electron-builder**。
// v20（2026-10-06，第十五批）：payload 内 scripts/ 实质变更 ——
//   · 新增 utils/interp_guard.py 解释器守卫：裸 python 缺 PyYAML 时 stderr 留痕
//     [py-guard] 自动切 venv 重跑（NF_PY_GUARD 防拉锯）；切不动 orchestrator 退 1
//     给可行动命令、nfctl check 转阻塞项「当前解释器」（--json stdout 不受污染）。
//   · orchestrator.py / nfctl.py 两入口挂守卫 + nfctl 自检新增解释器判据
//     （2026-10-06 试跑连炸五次的真根因：裸 python 缺 yaml 被误诊成 .venv 缺依赖）。
//   · tests/unit/test_interp_guard.py 20 断言（含「藏掉 yaml」反证）。
//   **先 bump 再 electron-builder**。
// v21（2026-10-07，第十六批）：payload 内 scripts/ 实质变更 ——
//   · nf_mcp_stdio_bridge.py：项目根解析补「注册表 User NF_ROOT」兜底 ——
//     长驻进程（Hermes 网关）的环境变量可能过期/缺失，与新起的 GUI 解析出
//     不同的数据根 → GUI 与 MCP 垫片双根分裂（2026-10-07 事故）。
//   · 随包新增 skills/（extraResources：技能卡随版本分发）。
//   · Electron 主进程（asar 内，不走种子）：8765 双绑接管 + 退出确认回执
//     （确认框不再被 5s 看门狗计时强退）。
// **先 bump 再 electron-builder**。
// v22（2026-10-07，第十七批，0.6.7 出厂/用户数据分治）：payload scripts 变更 ——
//   · 新增域模块 nf_api_domains/factory.py：`GET /factory/list`（出厂清单 + 样例
//     逐字节命中盘点，只读）与 `POST /factory/clean`（confirm 护栏 → 移进
//     data/books/_trash/factory__<时间戳>/，只动 materials/ 开头的路径）；
//   · 新增 utils/factory_manifest.py（sha256 / 清单读写 / 样例检测 / 清理硬边界）；
//   · agent_guard 禁用名单加 /factory/clean（只读 list 不拦，清理只许用户本人动手）；
//   · 同批 seedWorkspace 播种后写 data/state/factory_manifest.json（出厂清单）。
//   · 随包新增 examples/（extraResources → payload/examples，样例书成为比对真源）。
// **先 bump 再 electron-builder**。
const SEED_VERSION = 22;

// 只播种/刷新**代码与提示词**目录。
// 刻意不含 data/：那是用户产物（章节、设定、大纲），任何情况下都不能被覆盖。
// config/ 也不在列表里 —— 它承载用户填的书名/路径/模型选择，
// 缺文件时才补，绝不做覆盖式刷新。
const SEED_CODE_DIRS = ["scripts", "prompts", "templates"];
const SEED_CONFIG_DIR = "config";

// ---- 出厂清单（0.6.7 出厂/用户数据分治）----
// 播种把哪些文件**实际复制**进工作区，就往 data/state/factory_manifest.json 记哪些
// （相对路径 + sha256）。为什么必须记：历史上 examples/sample-book 的素材卡被试跑
// 手工搬进真实工作区，与用户自己写的卡**没有任何标记可分辨** —— 「哪些是机器发的」
// 没有账可查，GUI 的「出厂内容」面板就只能靠逐字节比对样例（那是另一条检测线，
// 见 nf_api_domains/factory.py）。本清单回答的是「这次播种动了哪些文件」。
//
// 合并语义：已记的保留，本次复制的追加/覆盖（与 Python 侧
// utils/factory_manifest.write_manifest 同一口径）；只在**真的复制过文件**时才落盘，
// 「无事可做」不写生成时间。
function listFilesUnder(base, dir, out) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) listFilesUnder(base, p, out);
    else if (ent.isFile()) out.push(path.relative(base, p));
  }
  return out;
}

function recordFactoryManifest(ws, relPaths) {
  if (!relPaths || !relPaths.length) return;
  try {
    const crypto = require("crypto");
    const file = path.join(ws, "data", "state", "factory_manifest.json");
    fs.mkdirSync(path.dirname(file), { recursive: true });
    let prev = {};
    try { prev = JSON.parse(fs.readFileSync(file, "utf8")); } catch (e) { /* 无/损坏 → 空清单 */ }
    if (!prev || typeof prev !== "object") prev = {};
    const files = Object.assign({}, (prev.files && typeof prev.files === "object") ? prev.files : {});
    for (const rel of relPaths) {
      const key = String(rel).split(path.sep).join("/");
      try {
        files[key] = crypto.createHash("sha256")
          .update(fs.readFileSync(path.join(ws, rel))).digest("hex");
      } catch (e) { /* 单个文件读不到不该毁掉整份清单 */ }
    }
    const prevVer = parseInt(prev.seed_version, 10) || 0;
    fs.writeFileSync(file, JSON.stringify({
      seed_version: Math.max(prevVer, SEED_VERSION),
      generated_at: new Date().toISOString(),
      files,
    }, null, 2) + "\n");
    console.log("[console] seedWorkspace: 出厂清单已更新（" + Object.keys(files).length
                + " 个文件，本次记录 " + relPaths.length + " 个）");
  } catch (e) {
    // 清单是**账本**，写不进去绝不能连累播种本身
    console.error("[console] factory_manifest 写入失败（不阻断播种）:", e && e.message);
  }
}

// 首启/升级：种子 payload 进 workspace + 建 data/ 目录结构
// 打包态：从 process.resourcesPath/payload 复制；源码态：从项目根目录 ROOT 复制
//
// 三种情形：
//   ① 全新安装（无 .seeded）      → 全量播种
//   ② 老版本升级（.seed-version 落后）→ 刷新代码目录（force 覆盖），保留 data/
//   ③ 已是最新版本                → 只补缺失目录（幂等，不改任何已有文件）
function seedWorkspace() {
  const ws = getWorkspaceDir();

  // 源码态：工作区 == 项目根，代码/提示词/模板本来就在那儿，无需播种。
  // 必须在此短路 —— 否则下面 payloadRoot 也等于 ROOT，cpSync(ROOT/x, ROOT/x)
  // 会「自己复制自己」，Node 直接抛 ERR_FS_CP_EINVAL。
  if (!isPackaged) {
    console.log("[console] seedWorkspace: 源码态跳过播种（workspace = 项目根）");
    return;
  }

  // 显式配置的项目根（--project-root / NF_ROOT / 记忆配置）**绝不播种**：
  // 它可能指向用户的源码仓库，payload 播种（升级时 force 覆盖）会把外部项目
  // 的 scripts/prompts 换成本次安装包的快照 —— 播种只属于默认工作区。
  if (getWorkspaceSource() !== "default") {
    console.log("[console] seedWorkspace: 项目根为显式配置（source="
                + getWorkspaceSource() + "），跳过播种（保护外部项目代码）");
    return;
  }

  // 打包态专用（源码态已在上面短路返回）
  const payloadRoot = path.join(process.resourcesPath, "payload");

  const seededMarker = path.join(ws, ".seeded");
  const versionMarker = path.join(ws, ".seed-version");
  let seededVersion = 0;
  try {
    seededVersion = parseInt(fs.readFileSync(versionMarker, "utf8").trim(), 10) || 0;
  } catch (e) { /* 文件不存在 = 版本 0 */ }

  const firstRun = !fs.existsSync(seededMarker);
  const stale = seededVersion < SEED_VERSION;

  if (!firstRun && !stale) return;   // 情形 ③：无事可做

  // 本次**实际复制**的文件（相对路径）→ 收尾统一写进出厂清单
  const seededRels = [];

  for (const d of SEED_CODE_DIRS) {
    const src = path.join(payloadRoot, d);
    const dst = path.join(ws, d);
    if (!fs.existsSync(src)) continue;
    if (firstRun || !fs.existsSync(dst)) {
      fs.cpSync(src, dst, { recursive: true });
      listFilesUnder(ws, dst, seededRels);
    } else {
      // 升级刷新：force 覆盖代码与提示词。
      // 用户若改过 prompts/ 会被覆盖 —— 这是刻意取舍：
      // 提示词属于随版本发布的资产，与旧版不兼容的提示词比"丢失自定义"危害更大。
      fs.cpSync(src, dst, { recursive: true, force: true });
      listFilesUnder(ws, dst, seededRels);
      console.log(`[console] seedWorkspace: 已刷新 ${d}（种子 v${seededVersion} → v${SEED_VERSION}）`);
    }
  }

  // config：只在**缺失**时补，绝不覆盖（承载用户的书名/路径/模型选择）
  const cfgSrc = path.join(payloadRoot, SEED_CONFIG_DIR);
  const cfgDst = path.join(ws, SEED_CONFIG_DIR);
  if (fs.existsSync(cfgSrc)) {
    if (!fs.existsSync(cfgDst)) {
      fs.cpSync(cfgSrc, cfgDst, { recursive: true });
      listFilesUnder(ws, cfgDst, seededRels);
    } else {
      // 逐文件补缺：**只在整个文件缺失时**才从 payload 复制。
      // ⚠️ 这只做到「文件粒度」，**不会**把新版本新增的 provider 并进用户已有的
      // system.yaml —— 键级合并得改写用户配置，而 yaml 序列化会丢掉全部注释、
      // 也可能覆盖用户的模型选择，风险大于收益，所以刻意不做。
      // 升级后要用新 provider，请手动把 payload 里 config/system.yaml 的对应段落
      // 贴进工作区那份；`python scripts/nfctl.py model-check <provider>` 可确认是否已到位。
      // .env 天然安全（它不在 payload 里）。
      for (const f of fs.readdirSync(cfgSrc)) {
        const s = path.join(cfgSrc, f);
        const t = path.join(cfgDst, f);
        if (fs.statSync(s).isFile() && !fs.existsSync(t)) {
          fs.copyFileSync(s, t);
          seededRels.push(path.join(SEED_CONFIG_DIR, f));
          console.log(`[console] seedWorkspace: 补充配置 ${f}`);
        }
      }
    }
  }

  // 出厂清单：把本次真正落盘的文件记进 data/state/factory_manifest.json（合并写）
  recordFactoryManifest(ws, seededRels);

  // 预建 data/ 目录结构（幂等，已有内容不受影响）
  ["data/state", "data/outline/chapters", "data/setting", "data/chapters/raw", "data/chapters/checked", "data/chapters/refined", "data/books"].forEach(d => {
    fs.mkdirSync(path.join(ws, d), { recursive: true });
  });

  fs.writeFileSync(seededMarker, "1");
  fs.writeFileSync(versionMarker, String(SEED_VERSION));
  if (stale && !firstRun) {
    console.log(`[console] seedWorkspace: 工作区已升级到种子 v${SEED_VERSION}（用户 data/ 未受影响）`);
  }
}
// 技能随版本分发（2026-10-07，对齐方寸「升级后启动自动同步」）：
// 随包 skills/worldbuilding → Hermes 技能库，内容不一致才覆盖（发布版赢），
// 运行态技能永远等于当前安装版本，Agent 手改下次启动即被复位。
// 只碰 worldbuilding/ 子树：技能库根目录还有别家的技能与清单文件，不越界。
//
// 0.6.7 扩展：除默认技能库外，同步 **%LOCALAPPDATA%\hermes\profiles\*\** 里的
// 同名子树 —— 但**只有该 profile 已经装过这张卡**（skills/worldbuilding/ronghuamofang/
// 目录存在）才同步进去。理由：绝不在没装过卡的 profile 里凭空创建目录
// （那等于替用户给别的 profile 装了没要的东西）；已装过的才需要跟版本更新。
// 默认根的同步行为与 0.6.6 完全一致；任何一步失败都不阻断启动。
function syncSkills() {
  if (!isPackaged) return;
  try {
    const src = path.join(process.resourcesPath, "payload", "skills", "worldbuilding");
    const dstBase = process.env.LOCALAPPDATA;
    if (!fs.existsSync(src) || !dstBase) return;
    const hermesRoot = path.join(dstBase, "hermes");

    // 把随包技能树同步进 dst；返回实际更新的文件数。
    const syncInto = (dst) => {
      let copied = 0;
      const walk = (rel) => {
        for (const ent of fs.readdirSync(path.join(src, rel), { withFileTypes: true })) {
          const r = rel ? path.join(rel, ent.name) : ent.name;
          if (ent.isDirectory()) { walk(r); continue; }
          const s = path.join(src, r), t = path.join(dst, r);
          let same = false;
          try {
            same = fs.existsSync(t) && fs.readFileSync(s).equals(fs.readFileSync(t));
          } catch (e) { /* 读失败按内容不同处理 */ }
          if (!same) {
            fs.mkdirSync(path.dirname(t), { recursive: true });
            fs.copyFileSync(s, t);
            copied += 1;
            console.log("[console] syncSkills: 更新 " + r);
          }
        }
      };
      walk("");
      return copied;
    };

    // 默认技能库（0.6.6 起的原行为，保持不变）
    let total = syncInto(path.join(hermesRoot, "skills", "worldbuilding"));

    // 多 profile（0.6.7）：只更新**已装过这张卡**的 profile
    try {
      const profRoot = path.join(hermesRoot, "profiles");
      if (fs.existsSync(profRoot)) {
        for (const ent of fs.readdirSync(profRoot, { withFileTypes: true })) {
          if (!ent.isDirectory()) continue;
          const dst = path.join(profRoot, ent.name, "skills", "worldbuilding");
          if (!fs.existsSync(path.join(dst, "ronghuamofang"))) {
            continue;   // 该 profile 没装过卡 → 绝不创建目录，跳过
          }
          total += syncInto(dst);
        }
      }
    } catch (e) {
      // profiles 目录读不到 / 某个 profile 损坏：默认根已同步完，不连累启动
      console.warn("[console] syncSkills: profile 遍历失败（不影响默认技能库）:",
                   e && e.message);
    }

    if (total) console.log("[console] syncSkills: 技能随版本分发完成，更新 " + total + " 个文件");
  } catch (e) {
    console.error("[console] syncSkills 失败（不阻断启动）:", e && e.message);
  }
}

const BASE = "http://127.0.0.1:" + API_PORT;

let apiProc = null;
let mainWindow = null;

function killPortOccupants(port) {
  /** 杀掉占用 port 的既有监听进程（Windows：netstat → PID → 杀整树）。返回被清 PID。 */
  if (process.platform !== "win32") return [];
  let pids = [];
  try {
    const out = require("child_process").execSync(
      "netstat -ano | findstr :" + port + " | findstr LISTENING",
      { stdio: "pipe" }).toString();
    pids = parseNetstatListeningPids(out, port);
  } catch (e) { /* netstat 没输出 = 没人占 */ }
  pids.forEach((pid) => {
    console.log("[console] port " + port + " 被既有实例占用（无本会话 GUI token）"
                + " → 杀 PID " + pid + "（树）");
    if (!killTree(parseInt(pid))) {
      try { process.kill(parseInt(pid)); } catch (e) { /* ignore */ }
    }
  });
  return pids;
}

function startApi(attempt) {
  const tryNo = attempt || 1;
  // 本进程已拉起过 → 不重复 spawn（重启走 debug:restart-api 的 stopApi 前置）。
  if (apiProc) return;
  if (!fs.existsSync(PY)) {
    console.error("[console] python not found:", PY);
    return;
  }
  const net = require("net");
  const tester = net.createServer();
  tester.once("error", (e) => {
    if (e.code === "EADDRINUSE") {
      console.warn(`[console] port ${API_PORT} in use (attempt ${tryNo}), killing old process...`);
      // Kill whatever is using our port, then retry。
      // ⚠️ 必须杀**整棵树**：旧实现 process.kill(pid) 只杀那一个 PID，
      // 上一轮遗留的 nf_api 孙进程会继续占着端口 → 下轮又撞 → 无限重试。
      try {
        require("child_process").execSync(`netstat -ano | findstr :${API_PORT} | findstr LISTENING`, { stdio: "pipe" })
          .toString().split("\n").forEach(line => {
            const pid = line.trim().split(/\s+/).pop();
            if (pid && /^\d+$/.test(pid)) {
              console.log(`[console] killing PID ${pid} (tree)`);
              if (!killTree(parseInt(pid))) {
                try { process.kill(parseInt(pid)); } catch (e) { /* ignore */ }
              }
            }
          });
      } catch (e) { /* netstat 没有输出 = 没人占，交给下一轮 */ }
      // 重试**有上限**（2026-10-03）：旧实现无条件递归，端口被一个杀不掉的进程占住时
      // 会每秒重试到天荒地老（每次还漏一个 net.Server），界面看起来就是"应用起不来"。
      if (!shouldRetryPortProbe(tryNo)) {
        console.error(`[console] port ${API_PORT} 连续 ${MAX_PORT_PROBE_RETRIES} 次被占用且无法释放`
                      + " → 放弃自动清理；界面会显示离线，"
                      + "请手动结束占用该端口的进程（或重启电脑）后重开应用。");
        try { tester.close(); } catch (e) { /* ignore */ }
        return;
      }
      setTimeout(() => {
        try { tester.close(); } catch (e) { /* ignore */ }
        startApi(tryNo + 1);
      }, 1000);
      return;
    }
    console.error("[console] port probe error:", e);
    try { tester.close(); } catch (err) { /* ignore */ }
  });
  tester.once("listening", () => {
    tester.close();
    // 双绑接管（2026-10-07）：Windows 下 SO_REUSEADDR 会让「端口空闲」探测在
    // 既有实例（MCP 垫片自启 / 上轮遗留）仍在监听时假成功 —— 不清场就双绑，
    // 新连接落进无 GUI token 的旧实例，受保护写入全 403（gui(untrusted)）。
    // spawn 前先清场：8765 只允许本进程注入了本会话 token 的实例持有。
    killPortOccupants(API_PORT);
    const ws = getWorkspaceDir();
    fs.mkdirSync(ws, { recursive: true });
    const apiScript = path.join(ws, "scripts", "nf_api.py");
    // 日志跟着工作区走：打包态 → %LOCALAPPDATA%\Temp，源码态 → 项目根\Temp
    // （源码态若还写 LOCALAPPDATA，排查时根本找不到这份日志）
    const logDir = isPackaged
      ? path.join(process.env.LOCALAPPDATA || ROOT, "Temp")
      : path.join(ROOT, "Temp");
    fs.mkdirSync(logDir, { recursive: true });
    const logPath = path.join(logDir, "nf_api_child.log");
    const out = fs.openSync(logPath, "a");
    const cmd = PY;
    const args = [apiScript, "--port", String(API_PORT), "--root", ws];
    console.log("[console] spawn nf_api:", cmd, args.join(" "));
    apiProc = spawn(cmd, args, {
      cwd: ws,
      stdio: ["ignore", out, out],
      windowsHide: true,
      // GUI 来源双因子：把会话 token 注入 nf_api 环境（Python 侧 agent_guard 读它
      // 判定「请求是否真来自本桌面端」）。漏注入 = nf_api 不认任何 GUI 来源 →
      // 受保护端点一律 403（fail-closed，宁可拒绝也不放行）。
      // 出厂样例真源（0.6.7）：打包态样例书随包住在安装目录
      // resources/payload/examples，而 nf_api 的 ROOT 被 --root 指向工作区
      // （工作区里没有 examples）→ 不注入这个变量，/factory/list 就永远
      // 「样例目录不存在」，出厂内容检测在安装版上等于失效。
      // 源码态 ROOT 就是仓库根，无需注入（注入了也无害：按存在性取）。
      env: isPackaged
        ? { ...process.env, NF_GUI_TOKEN: guiToken,
            NF_SAMPLE_ROOT: path.join(process.resourcesPath, "payload", "examples") }
        : { ...process.env, NF_GUI_TOKEN: guiToken },
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
    const pid = apiProc.pid;
    // ⚠️ 先杀**整棵树**（2026-10-03）：实测 nf_api 自己还孵着孙进程，
    // 只 `apiProc.kill()` 会让孙子成孤儿继续占 8765 与句柄 —— 那正是
    // 「退出后端口还占着 / 下次启动撞端口 / 应用像没死透」的来源。
    if (pid && !killTree(pid)) {
      try { apiProc.kill(); } catch (e) { /* ignore */ }
    }
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

// 数据/产物类路径解析：**workspace 优先，代码根（ROOT）兜底**。
//
// 为什么不能只认 ROOT：打包态 ROOT 指向安装目录里的 `resources/payload`
// （随安装包只读，且 extraResources 里**没有** data/），而 `.env`、`data/`、
// `output/` 全在 workspace（`%APPDATA%\绒花墨坊\workspace`）。
// 用 ROOT 去解析 `.env` 必然「文件不存在」—— 这正是设置页「在文件夹中显示」
// 点了没反应、关于弹窗「打开」按钮打不开产物的根因（2026-10-01 实测反馈）。
function resolveExisting(relPath) {
  const wsAbs = path.resolve(getWorkspaceDir(), relPath);
  if (fs.existsSync(wsAbs)) return wsAbs;
  const rootAbs = path.resolve(ROOT, relPath);
  if (fs.existsSync(rootAbs)) return rootAbs;
  return wsAbs;   // 都不存在 → 回 workspace，报错信息指向用户真实数据区
}

function pathAllowed(p) {
  const norm = path.resolve(p).replace(/\\/g, "/").toLowerCase();
  // 两个根都要算：源码态二者相同（去重无害），打包态分别是 workspace 与 payload。
  // ⚠️ 旧实现只按 ROOT 前缀 slice 计算相对路径 —— 打包态下 workspace 里的路径
  // 会切出一段垃圾 rel，命中不了任何白名单分支，于是**所有**指向用户数据的
  // 打开/预览请求都被判「路径不在白名单」。
  const roots = [...new Set([getWorkspaceDir(), ROOT].map(
    (r) => r.replace(/\\/g, "/").toLowerCase()))];
  let rel = null;
  for (const r of roots) {
    if (norm === r) { rel = ""; break; }
    if (norm.startsWith(r + "/")) { rel = norm.slice(r.length + 1); break; }
  }
  if (rel === null) return false;
  if (rel === "") return true;
  // 允许直接打开这几个白名单目录本身（关于弹窗的「打开目录」按钮）
  if (["data", "output", "logs", "materials", "config"].includes(rel)) return true;
  if (rel.startsWith("data/") || rel.startsWith("output/")) return true;
  if ((rel.startsWith("logs/") || rel.startsWith("materials/")) && /\.(md|log|json)$/.test(rel)) return true;
  if (rel.startsWith("config/") && /\.(yaml|yml)$/.test(rel)) return true;
  // Agent kickoff 提示词（单一事实源 prompts/agent_kickoff.md）：
  // 「接入其他 Agent」设置块要读它展示/复制。精确到这一个文件，不放开整个 prompts/。
  if (rel === "prompts/agent_kickoff.md") return true;
  // 注意：.env 含明文密钥，**刻意不放入外部打开白名单** —— 不再允许用外部编辑器
  // 直接打开（2026-09-19 审查 S7）。如需编辑密钥，请用 GUI 设置页的专用入口，
  // 或由用户自行在文件管理器中打开。
  return false;
}

ipcMain.handle("read-preview", async (e, relPath) => {
  try {
    const abs = resolveExisting(relPath);
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
  const abs = resolveExisting(relPath);
  if (!pathAllowed(abs)) return { ok: false, error: "路径不在白名单" };
  const r = await shell.openPath(fs.existsSync(abs) ? abs : path.dirname(abs));
  return r ? { ok: false, error: r } : { ok: true };
});

// 直接用系统默认程序打开文件（用户显式点击「打开」时调用）
ipcMain.handle("open-file", async (e, relPath) => {
  const abs = resolveExisting(relPath);
  if (!pathAllowed(abs)) return { ok: false, error: "路径不在白名单" };
  const r = await shell.openPath(fs.existsSync(abs) ? abs : path.dirname(abs));
  return r ? { ok: false, error: r } : { ok: true };
});

// 在文件管理器中定位 `.env`（含明文密钥：**只定位、不打开**）。
// 2026-09-19 审查 S7 决定程序不代为用外部编辑器打开密钥文件，但当时的实现
// 却把路径解析成 `ROOT/.env`（打包态 = 安装目录里的 payload，那里根本没有 .env）
// → 恒返回「文件不存在」，前端又没检查返回值、照样提示成功 —— 用户看到的是
// 「点了没反应」。现在改为解析到 workspace（后端写 .env 的地方），并把
// 失败如实体现在返回值里。本 IPC 只服务 .env，不接受任意路径。
ipcMain.handle("reveal-in-folder", async (e, relPath) => {
  try {
    if (path.basename(String(relPath || "")) !== ".env") {
      return { ok: false, error: "该操作仅支持 .env" };
    }
    const abs = resolveExisting(".env");
    const norm = abs.replace(/\\/g, "/").toLowerCase();
    const allowedRoots = [getWorkspaceDir(), ROOT].map(
      (r) => r.replace(/\\/g, "/").toLowerCase() + "/");
    if (!allowedRoots.some((r) => norm.startsWith(r))) {
      return { ok: false, error: "路径越界" };
    }
    if (!fs.existsSync(abs)) return { ok: false, error: "文件不存在: " + abs };
    shell.showItemInFolder(abs);
    return { ok: true, path: abs };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
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

// ---------------------------------------------------------------------------
// 接入其他 Agent（2026-10-06 对齐方寸「接入其他 Agent」设置页）
// 生成 MCP 配置段（按安装态/源码态算真实路径）+ 打开目标配置文件。
// 纪律：路径**全在主进程计算**，渲染进程只拿结果；open 只接受三个固定 target，
// 不接受任意路径（否则这就是一个任意文件打开后门）。
function agentConnectPaths() {
  const os = require("os");
  const localAppData = process.env.LOCALAPPDATA
    || path.join(os.homedir(), "AppData", "Local");
  const appData = process.env.APPDATA
    || path.join(os.homedir(), "AppData", "Roaming");
  // MCP 客户端 spawn 的是**stdio 进程** → 必须 console 版 python.exe
  // （源码态 PY 用的 pythonw.exe 没有 stdio，配置段里不能用它）。
  const pythonCmd = isPackaged
    ? path.join(process.resourcesPath, "runtime", "python", "python.exe")
    : path.join(ROOT, ".venv", "Scripts", "python.exe");
  const bridgePath = isPackaged
    ? path.join(process.resourcesPath, "payload", "scripts", "nf_mcp_stdio_bridge.py")
    : path.join(ROOT, "scripts", "nf_mcp_stdio_bridge.py");
  // 技能卡真源（自装指令用）：打包态指安装目录 payload（恒存在），
  // 不再让渲染层拿 project_dir 拼 —— 打包态工作区里没有 skills/，必然断路径
  // （2026-10-07 用户指出的发布态问题）。
  const skillPath = isPackaged
    ? path.join(process.resourcesPath, "payload", "skills",
                "worldbuilding", "ronghuamofang", "SKILL.md")
    : path.join(ROOT, "skills", "worldbuilding", "ronghuamofang", "SKILL.md");
  return {
    pythonCmd, bridgePath, skillPath,
    hermesConfig: path.join(localAppData, "hermes", "config.yaml"),
    claudeConfig: path.join(os.homedir(), ".claude.json"),
    clineConfig: path.join(appData, "Code", "User", "globalStorage",
      "cline.vscode-cline", "cline_mcp_settings.json"),
  };
}

ipcMain.handle("agent-connect:info", async () => {
  const p = agentConnectPaths();
  const jsonSnippet = JSON.stringify({
    mcpServers: {
      novelforge: { command: p.pythonCmd, args: [p.bridgePath] },
    },
  }, null, 2);
  const yamlSnippet = [
    "mcp_servers:",
    "  novelforge:",
    "    command: " + p.pythonCmd,
    "    args:",
    "      - " + p.bridgePath,
    "    enabled: true",
  ].join("\n");
  // MCP 8766 现状探针（垫片自启动只覆盖 8765；8766 由 nf_api 连带拉起）
  const mcpListening = await new Promise((resolve) => {
    try {
      const net = require("net");
      const sock = net.connect({ host: "127.0.0.1", port: 8766, timeout: 400 });
      sock.on("connect", () => { sock.destroy(); resolve(true); });
      sock.on("error", () => resolve(false));
      sock.on("timeout", () => { sock.destroy(); resolve(false); });
    } catch (e) { resolve(false); }
  });
  return {
    ok: true,
    packaged: isPackaged,
    version: app.getVersion(),
    pythonCmd: p.pythonCmd,
    bridgePath: p.bridgePath,
    skillPath: p.skillPath,
    hermesConfig: p.hermesConfig,
    claudeConfig: p.claudeConfig,
    clineConfig: p.clineConfig,
    jsonSnippet, yamlSnippet,
    mcpListening,
    mcpConfigured: false,   // 客户端配置在外部文件里，本进程无法可靠判读——由用户点「打开」自查
  };
});

ipcMain.handle("agent-connect:open-config", async (e, target) => {
  try {
    const p = agentConnectPaths();
    const map = { hermes: p.hermesConfig, claude: p.claudeConfig, cline: p.clineConfig };
    const abs = map[String(target || "")];
    if (!abs) return { ok: false, error: "未知目标: " + target };
    if (fs.existsSync(abs)) {
      const err = await shell.openPath(abs);
      return err ? { ok: false, error: err, path: abs }
                 : { ok: true, path: abs, existed: true };
    }
    // 文件还没有 → 打开所在目录（让用户看清位置；绝不代创建外部工具的配置）
    const dir = path.dirname(abs);
    const err2 = fs.existsSync(dir) ? await shell.openPath(dir) : "";
    return { ok: !err2, path: abs, existed: false,
             message: "配置文件尚不存在，已打开所在目录: " + dir };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

// ---------------------------------------------------------------------------
// 诊断与调试入口（2026-10-06 对齐方寸「诊断日志抽屉 / 🩺 诊断页签」）
// 纪律：全部是**固定目标**操作（自家日志文件、DevTools、自家后端进程），
// 不接受渲染进程传来的任意路径 / 任意命令 —— 否则这就是后门。
function nfApiLogPath() {
  const logDir = isPackaged
    ? path.join(process.env.LOCALAPPDATA || ROOT, "Temp")
    : path.join(ROOT, "Temp");
  return path.join(logDir, "nf_api_child.log");
}

ipcMain.handle("debug:info", async () => {
  // 后端健康与「谁在服务 8765」分开报：GUI 自己的子进程挂了但外部进程在服务
  // 时，光看 apiProc 会误判。
  let healthOk = false;
  try {
    const r = await fetch(BASE + "/health");
    healthOk = r.ok;
  } catch (e) { /* 离线 */ }
  return {
    ok: true,
    version: app.getVersion(),
    packaged: isPackaged,
    platform: process.platform,
    electron: process.versions.electron,
    node: process.versions.node,
    logPath: nfApiLogPath(),
    logExists: fs.existsSync(nfApiLogPath()),
    codeRoot: ROOT,                       // 代码根（源码仓 / 安装目录 payload）
    projectRoot: getWorkspaceDir(),       // 数据根（项目根 = 书稿所在）
    projectRootSource: getWorkspaceSource(),
    apiBase: BASE,
    apiChildPid: apiProc ? apiProc.pid : null,
    apiHealthy: healthOk,
    devtoolsOpen: mainWindow ? mainWindow.webContents.isDevToolsOpened() : false,
  };
});

// 打开后端日志文件（固定路径，唯一目标）
ipcMain.handle("debug:open-log", async () => {
  const p = nfApiLogPath();
  if (!fs.existsSync(p)) {
    return { ok: false, path: p, error: "日志文件尚不存在: " + p };
  }
  const err = await shell.openPath(p);
  return err ? { ok: false, path: p, error: err } : { ok: true, path: p };
});

// DevTools 开关（打包态没有开发菜单 —— 这就是「像样的调试入口」的开发者半边）
ipcMain.handle("debug:devtools", async () => {
  if (!mainWindow) return { ok: false, error: "窗口不存在" };
  const wc = mainWindow.webContents;
  if (wc.isDevToolsOpened()) {
    wc.closeDevTools();
    return { ok: true, open: false };
  }
  wc.openDevTools({ mode: "detach" });
  return { ok: true, open: true };
});

// 重启后端 API（杀自家子进程 → 重新 startApi → 等 /health）
ipcMain.handle("debug:restart-api", async () => {
  stopApi();
  startApi();
  const healthy = await waitForApi(20000);
  return { ok: healthy, healthy, logPath: nfApiLogPath() };
});

// 审批门系统通知（2026-10-06 修「通知权限是死按钮」）
//
// 旧实现：渲染进程 Notification.requestPermission()。它只在 permission === "default"
// 时才动作，而打包态 Electron 里权限通常已是 granted/denied → 按钮点了毫无反应，
// 且授权结果从不回显 —— 用户看到的就是一颗死按钮（而且就算点了也常常弹不出通知）。
//
// 现在：主进程 new Notification().show()，OS 级通知、**无需授权**、不存在权限弹窗。
// 方寸桌面版同款方案（fangcun/desktop/src/main/services/notifier.ts）。
// 渲染进程保留浏览器通知作为回退（浏览器预览模式下没有 mofangAPI）。
ipcMain.handle("notify:gate", async (e, opts) => {
  try {
    const o = opts || {};
    if (!Notification.isSupported()) {
      return { ok: false, error: "当前系统不支持桌面通知" };
    }
    const n = new Notification({
      title: String(o.title || "绒花墨坊"),
      body: String(o.body || ""),
      silent: false,
    });
    n.show();
    return { ok: true, mode: "system" };
  } catch (err) {
    return { ok: false, error: String((err && err.message) || err) };
  }
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
  // projectRoot = **用户数据根**（.env / data / output 所在处）；
  // 打包态它与安装目录里的 codeRoot（payload）不是同一个地方，
  // 排查「东西写到哪去了」时必须能分别看到。
  projectRoot: getWorkspaceDir(),
  codeRoot: ROOT,
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
//
// ⚠️ 返回值必须**只含可序列化字段**：`autoUpdater.checkForUpdates()` 的结果里有
// `cancellationToken`（CancellationToken 实例，带 EventEmitter）与 `downloadPromise`，
// 直接经 IPC 回传会触发 "An object could not be cloned"，invoke 直接 reject。
// 前端当时没有 try/catch → 未捕获的 rejection，`checkingUpdate` 永远停在 true，
// 按钮卡在「检查中…」并保持 disabled —— 表现就是「检查更新点了没反应 / 功能没了」。
ipcMain.handle("updater:check", async () => {
  if (!autoUpdater) {
    return { ok: false, uninitialized: true,
             error: "本机未启用自动更新（开发模式或便携版），请用「下载新版本」" };
  }
  try {
    const result = await autoUpdater.checkForUpdates();
    const info = (result && result.updateInfo) || {};
    return {
      ok: true,
      currentVersion: app.getVersion(),
      version: info.version || "",
      releaseDate: info.releaseDate || "",
    };
  } catch (e) {
    const msg = e.message || "";
    if (msg.includes("404") || msg.includes("no published versions")) {
      return { ok: false, noUpdate: true, error: "暂无更新（当前已是最新版本）" };
    }
    return { ok: false, error: msg.slice(0, 200) };
  }
});

ipcMain.handle("updater:quitAndInstall", () => {
  if (autoUpdater && updateDownloaded) {
    // 更新安装期间**关掉硬退出看门狗**：安装器要接管退出流程，
    // 5 秒后强杀 Electron 可能打断安装器启动（用户点了「重启安装」却什么都没发生）。
    updateInstalling = true;
    if (closeAskTimer) { clearTimeout(closeAskTimer); closeAskTimer = null; }
    autoUpdater.quitAndInstall();
  }
});

ipcMain.handle("updater:status", () => ({
  available: updateAvailable,
  downloaded: updateDownloaded,
  initialized: autoUpdater !== null,
  packaged: app.isPackaged,
  portable: !!process.env.PORTABLE_EXEC_DIR,
  version: app.getVersion(),
  dev: process.env.NODE_ENV === "development" || !app.isPackaged,
}));

// ---- GUI 来源 token（2026-10-06 红队修复）----
// 渲染层 api() 从这里取会话 token，随请求头带给 nf_api。
// 只发给本应用渲染进程（contextIsolation 下外部脚本摸不到 mofangAPI）。
ipcMain.handle("gui-token:get", () => guiToken);

// ---- 退出守卫 IPC（2026-10-03）----
// 渲染进程上报 busy（有运行中 job / 未审批阶段）；主进程据此决定关窗要不要拦。
ipcMain.on("app:busy", (e, busy) => { rendererBusy = !!busy; });
// ---- 退出必确认设置（2026-10-06：默认任何关窗都确认，设置页可关）----
ipcMain.handle("app:get-confirm-on-exit", () => ({ ok: true, confirmOnExit }));
ipcMain.handle("app:set-confirm-on-exit", (e, v) => {
  confirmOnExit = !!v;
  const ok = writeConsoleSettings({ confirmOnExit });
  console.log("[console] confirmOnExit ->", confirmOnExit);
  return { ok, confirmOnExit };
});

// 渲染进程回执「确认框已弹出/已接手」（2026-10-07 修「几秒后自动退出」）：
// 收到回执立刻撤看门狗 —— 确认框在场绝不计时自动退；没回执才走 5s 强退
// （渲染进程死掉时兜底，不把用户关在应用里）。
ipcMain.on("app:close-answered", () => {
  closeAskAnswered = true;
  if (closeAskTimer) { clearTimeout(closeAskTimer); closeAskTimer = null; }
});

// 用户在应用内确认框里点了「确认退出」→ 真正关窗（isQuitting=true 后 close 不再被拦）。
ipcMain.handle("app:quit-confirmed", () => {
  isQuitting = true;
  if (closeAskTimer) { clearTimeout(closeAskTimer); closeAskTimer = null; }
  console.log("[console] 用户确认退出");
  stopApi();
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
  else forceQuit("confirmed-no-window");
  return { ok: true };
});
// 用户点了「取消」→ 只记一笔，让下次关窗还能再问（不改变 busy 状态）。
ipcMain.handle("app:quit-canceled", () => {
  console.log("[console] 用户取消退出");
  if (closeAskTimer) { clearTimeout(closeAskTimer); closeAskTimer = null; }
  lastCloseAskAt = 0;
  return { ok: true };
});

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
  // ---- 调试入口快捷键（2026-10-06 对齐方寸）----
  // 打包态没有开发菜单可点，Ctrl+Shift+I / F12 是开发者的常驻通道；
  // 普通用户的入口在 设置 → 诊断与调试（日志 / 重启后端 / 诊断信息）。
  // preventDefault 后由这里唯一处理，不与 Chromium 默认快捷键叠成「按一次开两次」。
  mainWindow.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    const isToggle = input.key === "F12"
      || (input.control && input.shift && (input.key === "I" || input.key === "i"));
    if (!isToggle) return;
    event.preventDefault();
    const wc = mainWindow && mainWindow.webContents;
    if (!wc) return;
    if (wc.isDevToolsOpened()) wc.closeDevTools();
    else wc.openDevTools({ mode: "detach" });
  });
  // ---- 退出守卫（2026-10-03 修「关不掉」）----
  // 关窗时**由主进程**决定拦不拦：渲染进程只上报 busy，确认走应用内对话框。
  // 判据在 quitGuard.shouldGuardClose（纯函数，可单测）。
  mainWindow.on("close", (e) => {
    if (!shouldGuardClose({ rendererBusy, isQuitting, lastAskAt: lastCloseAskAt,
                            now: Date.now(), confirmOnExit })) {
      return;                       // 没在忙 / 已确认 / 连点合并 → 放行
    }
    e.preventDefault();
    askRendererToClose();
  });
  mainWindow.on("closed", () => { mainWindow = null; });
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
  mainWindow.maximize();
}

app.whenReady().then(async () => {
  confirmOnExit = readConsoleSettings().confirmOnExit !== false;   // 默认开
  seedWorkspace();
  syncSkills();   // 技能随版本分发（幂等：内容不同才写）
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
  isQuitting = true;
  stopApi();
  app.quit();
});

// 退出流程：先标记 isQuitting（让 close 处理器不再拦），再杀子树，
// 最后挂一个**硬退出看门狗** —— 任何环节卡住（子进程杀不掉、窗口不肯关）
// 都会在 5 秒后被 `app.exit(0)` 强制收场。宁可丢掉一次清理，也不能把用户关在应用里。
app.on("before-quit", () => {
  isQuitting = true;
  if (closeAskTimer) { clearTimeout(closeAskTimer); closeAskTimer = null; }
  stopApi();
  const t = setTimeout(() => {
    if (updateInstalling) return;   // 更新安装器接管中，不抢它的退出
    console.error("[console] 退出流程超时（5s）→ 强制 exit");
    forceQuit("before-quit-timeout");
  }, 5000);
  if (t.unref) t.unref();          // 正常退出时不要因为这个 timer 多活 5 秒
});
app.on("quit", () => { isQuitting = true; });
process.on("exit", stopApi);
