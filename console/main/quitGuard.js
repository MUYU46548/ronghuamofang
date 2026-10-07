// 绒花墨坊桌面控制台 — 退出守卫的**纯逻辑**（不 require electron，故可用 node 直接单测）
//
// 为什么把这些判据单独拎出来（2026-10-03）：
// 「关不掉」的根因是渲染进程挂了个 window.beforeunload 原生确认框，触发条件是
// 「有 stage 是 done 但未审批」—— 那是审批门的**长期正常状态**（可持续数天），
// 于是每次关窗都弹 Chromium 原生对话框；该框在 Windows 上会跑到窗口后面、
// 或被 Esc/取消吃掉，表现就是「点了 X 没反应、原因不明」。主进程侧当时**没有**
// 任何 close 处理，没有兜底也没有看门狗。
//
// 修法是把守卫搬到主进程 + 应用内确认框，并给三条硬边界（都在本文件里判，
// 因为它们是"能不能关掉"的直觉最容易写错的地方）：
//   1. 用户明确要退出（isQuitting）→ **绝不再拦**（否则递归弹框）；
//   2. 连点/重复请求 → 合并（coalesceMs 内不重复弹）；
//   3. 渲染进程超时不应答 → 看门狗**强制退出**（宁可丢掉一次确认，也不能把用户关在应用里）。
//
// 2026-10-07 修「几秒后自动退出」：旧实现只发问不认应答 —— 应用内确认框弹出后
// 渲染进程没有任何回执，看门狗照样 5s 到点 forceQuit，用户看到的就是
// 「框刚出来，几秒后自己退了」= 计时自动退。现在渲染进程收到 close-requested
// 即回执 `app:close-answered`（preload 自动发），主进程撤掉定时器：
// **确认框在场 = 永不计时自动退**；看门狗只剩唯一职责 —— 渲染进程彻底死掉
// （没回执）时兜底强退，不把用户关在应用里。

/**
 * 关窗时是否要拦住并询问渲染进程。
 * @param {{rendererBusy:boolean, isQuitting:boolean, lastAskAt:number, now:number, coalesceMs?:number, confirmOnExit?:boolean}} s
 * @returns {boolean} true = preventDefault 并去问渲染进程
 */
function shouldGuardClose(s) {
  const coalesceMs = s.coalesceMs == null ? 4000 : s.coalesceMs;
  if (s.isQuitting) return false;          // 已经在退出了（含用户刚确认过）
  // confirmOnExit（退出必确认，2026-10-06 用户需求）：主进程设置开关传入。
  // true → 任何关窗都拦下确认（防误触杀掉 Agent 运行）；
  // false/缺省 → 旧行为：只在忙（运行中 job / 未审批阶段）时拦。
  if (!s.confirmOnExit && !s.rendererBusy) return false;
  if (s.lastAskAt && s.now - s.lastAskAt < coalesceMs) return false; // 连点合并
  return true;
}

/**
 * 渲染进程是否已超时未应答（看门狗判据）。
 * `acked=true`（渲染进程已回执，确认框已弹出/已处理）→ 永不超时：
 * 用户读框多久都不许被计时自动退出（2026-10-07）。
 * @param {{askedAt:number, now:number, acked?:boolean, timeoutMs?:number}} s
 */
function closeAskTimedOut(s) {
  const timeoutMs = s.timeoutMs == null ? 5000 : s.timeoutMs;
  if (s.acked) return false;   // 已应答 → 看门狗退场（框在场绝不计时自动退）
  if (!s.askedAt) return false;
  return s.now - s.askedAt >= timeoutMs;
}

/**
 * 从 `netstat -ano` 输出解析「监听指定端口」的 PID 列表（去重、保序）。
 *
 * 为什么是纯函数：双绑（Windows + SO_REUSEADDR）场景下「8765 还有谁在听」
 * 是接管判据的唯一事实源，必须能被 node 单测钉住 —— 尤其是
 * `:87650` 不等于 `:8765`、IPv6 `[::]:8765` 也要认、非 LISTENING 行不收。
 * @param {string} output netstat -ano 文本
 * @param {number|string} port 端口号
 * @returns {string[]} PID 列表（字符串，保序去重）
 */
function parseNetstatListeningPids(output, port) {
  const suffix = ":" + String(port);
  const pids = [];
  const seen = new Set();
  // 按换行拆（用 fromCharCode 写换行，避免源码字面转义被写坏；行尾 
 // CR 由 trim 兜掉）
  for (const line of String(output || "").split(String.fromCharCode(10))) {
    if (!/LISTENING/i.test(line)) continue;
    const cols = line.trim().split(/\s+/);
    if (cols.length < 4) continue;
    if (!(cols[1] || "").endsWith(suffix)) continue;   // :87650 撞不上 :8765
    const pid = cols[cols.length - 1];
    if (!/^\d+$/.test(pid)) continue;
    if (!seen.has(pid)) { seen.add(pid); pids.push(pid); }
  }
  return pids;
}

/**
 * 杀**整棵**进程树。返回 {argv, shell}：Windows 用 taskkill /T（连孙子），
 * 其余平台交给调用方 apiProc.kill()。
 *
 * 为什么必须 /T：实测 nf_api 自己还孵着孙进程（一个 python 子进程），
 * 旧实现只 `apiProc.kill()` → 孙进程成孤儿继续占着 8765 与句柄，
 * 下次启动走「端口被占 → 杀进程 → 重试」那条路，反复几次就像"应用半死不活"。
 */
function killTreeArgv(pid, platform) {
  if (!pid || !/^\d+$/.test(String(pid))) return null;
  if (platform === "win32") {
    return { argv: ["taskkill", "/F", "/T", "/PID", String(pid)], shell: false };
  }
  return null;   // POSIX：调用方 apiProc.kill() 即可（进程组语义另说，不在此猜）
}

/** 端口探测的最多重试次数（防「杀进程→重试」无限递归）。 */
const MAX_PORT_PROBE_RETRIES = 3;

/** 该不该再来一次端口探测。 */
function shouldRetryPortProbe(attempt, maxRetries) {
  const max = maxRetries == null ? MAX_PORT_PROBE_RETRIES : maxRetries;
  return attempt < max;
}

module.exports = {
  shouldGuardClose,
  closeAskTimedOut,
  killTreeArgv,
  shouldRetryPortProbe,
  parseNetstatListeningPids,
  MAX_PORT_PROBE_RETRIES,
};
