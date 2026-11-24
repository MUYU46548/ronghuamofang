// 绒花墨坊 — 审批门迁移判据单测（node 直跑，不需 electron）
//
// 运行：node console/tests/gateMigrate.test.js   （退出码 0 = 全过）
//
// 为什么单测这个：迁移是**改写用户配置**的动作，两种错都很贵 ——
//   改多了（用户自己选过门）→ 悄悄替作者决定确认点；
//   该改没改（还是旧出厂值 [2,6]）→ 阶段 4 那道门永远不出现，功能等于没做。
const { readRequireApproval, migrateRequireApproval } =
  require("../main/gateMigrate.js");

let pass = 0;
const fails = [];
function ok(name, cond, extra) {
  if (cond) { pass += 1; console.log("  [PASS] " + name); }
  else { fails.push(name); console.log("  [FAIL] " + name + (extra ? "  -> " + extra : "")); }
}

const OLD = [
  "gates:",
  "  require_approval:",
  "  - 2",
  "  - 6",
  "  pause_on_failure: true",
  "",
].join("\n");

console.log("=== 1. 读列表 ===");
ok("块式列表读成 [2,6]",
   JSON.stringify((readRequireApproval(OLD) || {}).nums) === "[2,6]");
ok("行内列表也认",
   JSON.stringify((readRequireApproval("gates:\n  require_approval: [2, 6]\n") || {}).nums)
   === "[2,6]");
ok("没有这个键 → null（不是空数组）", readRequireApproval("gates:\n  x: 1\n") === null);

console.log("\n=== 2. 该迁移：旧出厂值 [2,6] ===");
const r1 = migrateRequireApproval(OLD);
ok("changed=true", r1.changed === true, r1.reason);
ok("插入后是 [2,4,6]",
   JSON.stringify((readRequireApproval(r1.text) || {}).nums) === "[2,4,6]");
ok("缩进跟着原来的列表项（不破坏 yaml）",
   r1.text.split("\n").indexOf("  - 4") >= 0, JSON.stringify(r1.text.split("\n").slice(0, 6)));
ok("别的键一字不动（注释/设置都留着）",
   r1.text.includes("  pause_on_failure: true") && r1.text.includes("gates:"));

console.log("\n=== 3. 不该迁移：作者自己选过 ===");
const keep = (txt, why) => {
  const r = migrateRequireApproval(txt);
  ok(why, r.changed === false && r.text === txt, r.reason);
};
keep("gates:\n  require_approval:\n  - 2\n  - 4\n  - 6\n", "已经是 [2,4,6] → 不动");
keep("gates:\n  require_approval:\n  - 2\n", "只勾了 2（作者的选择）→ 不动");
keep("gates:\n  require_approval:\n  - 1\n  - 2\n  - 4\n  - 6\n", "加了 1（陪跑门）→ 不动");
keep("gates:\n  require_approval:\n  - 6\n", "删掉了 2 → 不动");
keep("gates:\n  material_autonomy: false\n", "整块缺失 → 不动");

console.log("\n=== 4. 行内写法也迁移 ===");
const r2 = migrateRequireApproval("gates:\n  require_approval: [2, 6]\n  x: 1\n");
ok("行内 [2,6] → [2,4,6]",
   r2.changed && /require_approval:\s*\[2, 4, 6\]/.test(r2.text), r2.text);

console.log("\n=== 5. 幂等 ===");
const again = migrateRequireApproval(r1.text);
ok("迁移过的文本再迁一次 = 不动", again.changed === false, again.reason);

console.log("");
if (fails.length) {
  console.log("gateMigrate.test: " + pass + " passed, " + fails.length + " FAILED");
  fails.forEach((f) => console.log("  - " + f));
  process.exit(1);
}
console.log("gateMigrate.test: " + pass + " passed, 0 failed");
