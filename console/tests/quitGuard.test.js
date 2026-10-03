// 绒花墨坊桌面控制台 — 退出守卫纯逻辑单测（node 直跑，不需 electron）
//
// 运行：node console/tests/quitGuard.test.js   （退出码 0 = 全过）
//
// 为什么单测这些：2026-10-03 的「关不掉」事故里，最贵的一步不是写代码，而是
// **判断哪条分支该放行**。把它写成纯函数 + 用例，才能在没人愿意手动关一百次窗口的前提下
// 钉住"不许把用户关在应用里"这条底线。
const {
  shouldGuardClose,
  closeAskTimedOut,
  killTreeArgv,
  shouldRetryPortProbe,
  MAX_PORT_PROBE_RETRIES,
} = require("../main/quitGuard.js");

let pass = 0;
const fails = [];
function ok(name, cond, extra) {
  if (cond) { pass += 1; console.log("  [PASS] " + name); }
  else { fails.push(name); console.log("  [FAIL] " + name + (extra ? "  → " + extra : "")); }
}

console.log("=== 1. 关窗守卫判据 ===");
ok("不忙 → 不拦（用户不该被无谓打扰）",
   shouldGuardClose({ rendererBusy: false, isQuitting: false, lastAskAt: 0, now: 1000 }) === false);
ok("忙 → 拦一次并询问",
   shouldGuardClose({ rendererBusy: true, isQuitting: false, lastAskAt: 0, now: 1000 }) === true);
ok("**已确认退出 → 绝不再拦**（否则递归弹框 = 关不掉）",
   shouldGuardClose({ rendererBusy: true, isQuitting: true, lastAskAt: 0, now: 1000 }) === false);
ok("4 秒内连点 → 合并，不重复弹",
   shouldGuardClose({ rendererBusy: true, isQuitting: false, lastAskAt: 1000, now: 3000 }) === false);
ok("超过合并窗口 → 可以再问（用户可能先点了取消）",
   shouldGuardClose({ rendererBusy: true, isQuitting: false, lastAskAt: 1000, now: 9000 }) === true);
ok("合并窗口可配",
   shouldGuardClose({ rendererBusy: true, isQuitting: false, lastAskAt: 1000, now: 3000,
                      coalesceMs: 100 }) === true);

console.log("\n=== 2. 看门狗（渲染进程不应答也必须能退） ===");
ok("刚问出去 → 不算超时",
   closeAskTimedOut({ askedAt: 1000, now: 3000 }) === false);
ok("5 秒未应答 → 判超时（主进程将强制退出）",
   closeAskTimedOut({ askedAt: 1000, now: 6000 }) === true);
ok("从没问过 → 不超时（不要凭空强退）",
   closeAskTimedOut({ askedAt: 0, now: 10 ** 9 }) === false);
ok("超时阈值可配", closeAskTimedOut({ askedAt: 0, now: 10, timeoutMs: 5 }) === false);

console.log("\n=== 3. 杀进程树（Windows 必须 /T，连孙子） ===");
const win = killTreeArgv(4321, "win32");
ok("Windows → taskkill /F /T /PID（/T 是关键，孙进程也要死）",
   win && win.argv[0] === "taskkill" && win.argv.includes("/T") && win.argv.includes("4321"),
   JSON.stringify(win));
ok("非 Windows → 交给调用方 apiProc.kill()（不猜进程组语义）",
   killTreeArgv(4321, "linux") === null);
ok("非法 pid → 不发命令（防 `taskkill /PID undefined` 乱杀）",
   killTreeArgv(null, "win32") === null && killTreeArgv("abc", "win32") === null);
ok("pid 为数字字符串也认", killTreeArgv("99", "win32") !== null);

console.log("\n=== 4. 端口探测限次（旧实现是无限递归） ===");
ok("第 1、2 次可以重试", shouldRetryPortProbe(1) === true && shouldRetryPortProbe(2) === true);
ok("到达上限后放弃（并打日志提示手动处理）",
   shouldRetryPortProbe(MAX_PORT_PROBE_RETRIES) === false);
ok("上限可配", shouldRetryPortProbe(1, 1) === false);

console.log("\n" + "=".repeat(52));
console.log("  node 单测：通过 " + pass + " / 失败 " + fails.length);
if (fails.length) { console.log("  失败项：" + fails.join(" | ")); }
console.log("=".repeat(52));
process.exit(fails.length ? 1 : 0);
