// 绒花墨坊桌面控制台 — 启动抢 8765 端口的**纯逻辑**（不 require electron，node 可直测）
//
// 为什么把它拎出来（2026-11-20）：旧实现启动时**无条件**杀掉占 8765 的实例。
// 那个实例可能正是 MCP 桥拉起的 nf_api，而流水线就跑在它**自己的进程里**
// （nf_api 顶层 `from orchestrator import run`，所以 RunLock 写下的 pid 就是 nf_api 的 pid）。
// 于是：双击图标 = 把正在写的那一章连进程一起杀掉，作者看不到任何提示，
// 只会觉得"软件又抽风，稿子少了一段"。
//
// 判据只有一条：**占端口的那个进程，是不是运行锁的持有者**。
//   - 是 → 它正在写，不许静默杀，先去问人；
//   - 否 → 杀它碰不到任何在写的流水线（可能是命令行跑的，也可能是上轮残留），照旧杀。
//
// 刻意**不**在这里判 owner（pipeline / human）：那套兜底只在 Python 侧
// `utils/lock_guard.py` 一处（缺 owner 按 pipeline 读）。两个地方各兜一次
// = 同一判据两份，改一处另一处照旧生效 —— 实测会假绿。

/**
 * 能不能直接杀占端口的进程。
 * @param {{lock: (object|null), portPids: (string|number)[]}} s
 *        lock = 运行锁文件内容（null = 没有锁）
 *        portPids = netstat 里监听该端口的 PID
 * @returns {{action: "kill"|"ask", reason: string, lockPid: (string|null)}}
 *          action=ask 表示"占端口的进程自己就是持锁者"，杀它等于打断正在写的章。
 */
function decidePortTakeover(s) {
  const st = s || {};
  const pids = (st.portPids || []).map(String);
  if (!pids.length) return { action: "kill", reason: "no-occupant", lockPid: null };

  const lock = st.lock;
  if (!lock || typeof lock !== "object") {
    return { action: "kill", reason: "no-lock", lockPid: null };
  }
  // 锁文件损坏 / 老格式没有 pid：读不出"谁在写"，就不能拿它当拦人的理由。
  // （拦一下只是多问一句，但**误拦**的代价是这个进程永远起不来 —— 宁可放行。）
  const pid = lock.pid == null || lock.pid === "" ? "" : String(lock.pid);
  if (!/^\d+$/.test(pid)) {
    return { action: "kill", reason: "lock-without-pid", lockPid: null };
  }
  if (!pids.includes(pid)) {
    return { action: "kill", reason: "lock-from-other-process", lockPid: pid };
  }
  // 走到这里：占端口的进程 = 持锁进程 = 正在跑流水线的 nf_api。
  // 不查 pid 是否存活 —— 它正监听 8765，活着是既定事实，不需要再问一次。
  return { action: "ask", reason: "occupant-is-writing", lockPid: pid };
}

/**
 * 对话框里那行"谁在写"的说明（锁文件内容不可信，一律清洗 + 截断）。
 * @param {object|null} lock
 * @returns {string}
 */
function takeoverDetail(lock) {
  const l = lock && typeof lock === "object" ? lock : {};
  const clean = (v, n) => String(v == null ? "" : v).replace(/\s+/g, " ").trim().slice(0, n);
  const parts = ["PID " + (l.pid == null ? "?" : clean(l.pid, 12))];
  const since = clean(l.started_at, 40);
  if (since) parts.push("开始于 " + since);
  const cmd = clean(l.cmd, 120);
  if (cmd) parts.push("命令: " + cmd);
  return parts.join(" · ");
}

/**
 * 选了"等它写完"之后，还要不要继续等（不是无限等：到点重新问一次，
 * 避免流水线真的挂了、或锁永远不释放时把应用卡在"界面离线"里没人管）。
 * @param {{ticks:number, maxTicks?:number}} s
 * @returns {boolean}
 */
function shouldKeepWaiting(s) {
  const st = s || {};
  const max = st.maxTicks == null ? 45 : st.maxTicks;
  // 脏值（NaN）当 0 算：NaN < max 恒 false → 会立刻"到期"，变成每隔一轮弹一次框。
  const n = Number(st.ticks);
  const ticks = Number.isFinite(n) ? n : 0;
  return ticks < max;
}

module.exports = { decidePortTakeover, takeoverDetail, shouldKeepWaiting,
                   isOursProcess, decideStartupPort };

// ---------------------------------------------------------------- 2026-11-24 新增
// 作者问的一件真事：“如果用户的电脑就是有 8765 怎么办？直接永久卡死？”
// 旧答案有两处硬伤：
//   ① 判据只看“运行锁持有者”，不是它就**照杀** —— 占 8765 的可能是别人的软件，
//      杀它既无礼也可能真的弄坏别人的东西；
//   ② 杀不掉（权限 / 系统进程）时只能连续重试 3 次然后放弃 → 界面永久离线，
//      作者的话是“直接永久卡死？”—— 现象上确实一样。
// 现在：陌生程序一个都不碰，改用**下一个空闲端口**，把实际端口告诉界面。

/** 这个命令行是不是“我们自己人”（墨坊拉起的 nf_api）。 */
function isOursProcess(cmdline) {
  const s = String(cmdline == null ? "" : cmdline).toLowerCase().replace(/\\/g, "/");
  return s.indexOf("nf_api.py") >= 0;
}

/**
 * 启动时该用哪个端口。
 * @param {{defaultPort?:number, portPids?:(string|number)[], lock?:object|null,
 *          cmdlines?:Record<string,string>, candidates?:number[],
 *          takenPorts?:number[]}} s
 * @returns {{port:number, action:"use-default"|"ours"|"switch"|"keep-busy",
 *            strangers:string[], note:string}}
 *   action：
 *     use-default = 8765 没人听，直接用；
 *     ours        = 占用者是我们自己的 nf_api（或正在跑的流水线）→ 仍用 8765，
 *                   后面走“先问一句 / 等它写完”那条老路；
 *     switch      = 陌生程序占着 → **不杀**，换候选端口里第一个空闲的；
 *     keep-busy   = 候选端口全被占 → 只能留在 8765（由上层重试/提示，不再假装能修）。
 */
function decideStartupPort(s) {
  const st = s || {};
  const def = Number(st.defaultPort || 8765);
  const pids = (st.portPids || []).map(String).filter(Boolean);
  if (!pids.length) return { port: def, action: "use-default", strangers: [], note: "" };

  const cmdlines = st.cmdlines || {};
  const lock = st.lock && typeof st.lock === "object" ? st.lock : null;
  const lockPid = lock && /^\d+$/.test(String(lock.pid == null ? "" : lock.pid))
    ? String(lock.pid) : "";

  const ours = pids.filter((p) => p === lockPid || isOursProcess(cmdlines[p]));
  if (ours.length) {
    return { port: def, action: "ours", strangers: pids.filter((p) => ours.indexOf(p) < 0),
             note: "占用者是墨坊自己的进程（PID " + ours.join(",") + "）" };
  }

  const taken = (st.takenPorts || []).map(Number);
  const cands = (st.candidates || []).map(Number).filter((n) => n > 0 && n !== def);
  const free = cands.filter((p) => taken.indexOf(p) < 0)[0];
  if (free) {
    return { port: free, action: "switch", strangers: pids,
             note: "8765 被别的程序占着（PID " + pids.join(",") + "），不改动它，改用 " + free };
  }
  return { port: def, action: "keep-busy", strangers: pids,
           note: "8765 与候选端口都被占（PID " + pids.join(",") + "）" };
}

