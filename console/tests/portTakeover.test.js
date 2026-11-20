// 绒花墨坊桌面控制台 — 启动抢端口判据单测（node 直跑，不需 electron）
//
// 运行：node console/tests/portTakeover.test.js   （退出码 0 = 全过）
//
// 为什么单测这个：这条判据错了有两种后果，都很贵 ——
//   假"杀"（本该拦住却杀了）→ 正在写的那一章连进程一起消失，作者只看到稿子少了；
//   假"拦"（本该杀掉却拦住）→ 窗口起来了、界面永远离线，等于软件打不开。
// 而这两个方向在界面上长得一模一样（都没有报错）。只能靠用例钉住。
const { decidePortTakeover, takeoverDetail, shouldKeepWaiting } =
  require("../main/portTakeover.js");

let pass = 0;
const fails = [];
function ok(name, cond, extra) {
  if (cond) { pass += 1; console.log("  [PASS] " + name); }
  else { fails.push(name); console.log("  [FAIL] " + name + (extra ? "  -> " + extra : "")); }
}

console.log("=== 1. 该杀：杀它碰不到任何在写的流水线 ===");
ok("端口没人占 → kill（没东西可杀，也不必问）",
   decidePortTakeover({ lock: null, portPids: [] }).action === "kill");
ok("没人占且 pid 列表是空的数组 → kill",
   decidePortTakeover({ portPids: [] }).reason === "no-occupant");
ok("没有锁文件 → kill（没有流水线在跑）",
   decidePortTakeover({ lock: null, portPids: ["100"] }).reason === "no-lock");
ok("锁是字符串（读坏了）→ kill，不当成持锁者",
   decidePortTakeover({ lock: "garbage", portPids: ["100"] }).action === "kill");
ok("锁文件损坏（无 pid）→ kill（读不出谁在写，不能拿它当拦人的理由）",
   decidePortTakeover({ lock: { corrupt: true, pid: null }, portPids: ["100"] }).reason ===
   "lock-without-pid");
ok("锁的 pid 非数字 → kill",
   decidePortTakeover({ lock: { pid: "abc" }, portPids: ["100"] }).reason === "lock-without-pid");
ok("锁属于**别的**进程（命令行跑的流水线）→ kill（杀 nf_api 不影响它）",
   decidePortTakeover({ lock: { pid: 999 }, portPids: ["100"] }).reason ===
   "lock-from-other-process");

console.log("\n=== 2. 该拦：占端口的进程自己就是持锁者 ===");
const r1 = decidePortTakeover({ lock: { pid: 4321 }, portPids: ["4321"] });
ok("锁 pid = 占端口的 pid → ask（它正在写）", r1.action === "ask", r1.action);
ok("reason 说得清是哪一种（便于排障）", r1.reason === "occupant-is-writing", r1.reason);
ok("锁 pid 是数字、netstat pid 是字符串 → 照样认（类型不同不算不同人）",
   decidePortTakeover({ lock: { pid: 4321 }, portPids: ["4321", "77"] }).action === "ask");
ok("多个占端口 PID 时，命中任意一个就算",
   decidePortTakeover({ lock: { pid: 77 }, portPids: ["4321", "77"] }).action === "ask");
ok("ask 时回传 lockPid，供对话框/日志说明是谁",
   decidePortTakeover({ lock: { pid: 4321 }, portPids: ["4321"] }).lockPid === "4321");
ok("owner=human 也拦（人接管后流水线可能正在写最后一段，一样会丢）",
   decidePortTakeover({ lock: { pid: 4321, owner: "human" }, portPids: ["4321"] }).action === "ask");
ok("缺 owner 也拦（读锁时不做 owner 兜底，兜底只在 Python 侧一处）",
   decidePortTakeover({ lock: { pid: 4321 }, portPids: ["4321"] }).action === "ask");

console.log("\n=== 3. 对话框那句话 ===");
ok("带 pid 与开始时间、命令",
   takeoverDetail({ pid: 4321, started_at: "2026-11-20T10:00:00", cmd: "nf_api.py --port 8765" })
     === "PID 4321 · 开始于 2026-11-20T10:00:00 · 命令: nf_api.py --port 8765",
   takeoverDetail({ pid: 4321, started_at: "2026-11-20T10:00:00", cmd: "nf_api.py --port 8765" }));
ok("锁内容空 → 不炸，也不编",
   takeoverDetail(null) === "PID ?", takeoverDetail(null));
ok("换行被压成空格（不能把对话框撑成一片空白）",
   takeoverDetail({ pid: 1, cmd: "a\n\nb\tc" }) === "PID 1 · 命令: a b c",
   takeoverDetail({ pid: 1, cmd: "a\n\nb\tc" }));
ok("超长的 cmd 被截断（锁文件是外部可写的，不能当可信文本直接铺满界面）",
   takeoverDetail({ pid: 1, cmd: "x".repeat(500) }).length <= 140);

console.log("\n=== 4. 等多久 ===");
ok("刚开始等 → 继续等", shouldKeepWaiting({ ticks: 0 }) === true);
ok("未到上限 → 继续等", shouldKeepWaiting({ ticks: 44, maxTicks: 45 }) === true);
ok("到上限 → 不再等（回去重新问人，别把应用卡死在离线态）",
   shouldKeepWaiting({ ticks: 45, maxTicks: 45 }) === false);
ok("上限可配", shouldKeepWaiting({ ticks: 3, maxTicks: 3 }) === false);
ok("ticks 缺失 → 按 0 算（继续等）", shouldKeepWaiting({}) === true);
ok("ticks 是脏值 → 当 0 算，不抛", shouldKeepWaiting({ ticks: "abc" }) === true);

console.log("");
if (fails.length) {
  console.log("portTakeover.test: " + pass + " passed, " + fails.length + " FAILED");
  fails.forEach(f => console.log("  - " + f));
  process.exit(1);
}
console.log("portTakeover.test: " + pass + " passed, 0 failed");
