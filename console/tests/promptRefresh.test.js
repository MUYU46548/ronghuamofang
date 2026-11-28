// 绒花墨坊 — 提示词升级判据单测（node 直跑，不需 electron）
//
// 运行：node console/tests/promptRefresh.test.js   （退出码 0 = 全过）
//
// 为什么单测这个：升级逻辑错了两种都贵 ——
//   ① 该刷没刷（0.9.15 实测的真 bug）→ 用户装了新版，跑的仍是旧提示词，
//      而界面上一句提示都没有（"升级不覆盖工作区提示词"这条缺陷就是这么来的）；
//   ② 刷多了（把用户手改的覆盖掉）→ 用户的创作资产消失，且无备份。
const { decideRefresh, isUserEditablePrompt } = require("../main/promptRefresh.js");

let pass = 0;
const fails = [];
function ok(name, cond, extra) {
  if (cond) { pass += 1; console.log("  [PASS] " + name); }
  else { fails.push(name); console.log("  [FAIL] " + name + (extra ? "  -> " + extra : "")); }
}

const A = "aaa111";
const B = "bbb222";
const C = "ccc333";

console.log("=== 1. 出厂副本当参照：本次真 bug 的判据 ===");
// 盘上 = 上一版官方原文（与出厂副本一致），清单里没有记录（真实现场）
let v = decideRefresh({
  rel: "prompts/stage4_writing.md", curSha: A, recordedSha: undefined, factorySha: A,
});
ok("清单无记录 + 与出厂副本一致 → 跟随新版", v.follow === true, v.reason);
ok("理由写明是出厂副本匹配", v.reason === "factory-copy-match", v.reason);

console.log("\n=== 2. 用户手改过：一个字都不许动 ===");
v = decideRefresh({
  rel: "prompts/stage4_writing.md", curSha: C, recordedSha: undefined, factorySha: A,
});
ok("与出厂副本不一致 → 不跟随", v.follow === false, v.reason);
ok("理由写明用户改过", v.reason === "user-edited", v.reason);

v = decideRefresh({
  rel: "prompts/stage4_writing.md", curSha: C, recordedSha: A, factorySha: A,
});
ok("清单记录也不符 → 不跟随（双路都不符）", v.follow === false, v.reason);

console.log("\n=== 3. 清单匹配优先（老工作区有清单时的原路径） ===");
v = decideRefresh({
  rel: "prompts/stage4_writing.md", curSha: B, recordedSha: B, factorySha: A,
});
ok("清单 sha == 盘上 → 跟随新版（即使出厂副本不同）", v.follow === true, v.reason);
ok("理由写明清单匹配", v.reason === "manifest-match", v.reason);

console.log("\n=== 4. 边界 ===");
v = decideRefresh({ rel: "prompts/x.md", curSha: null, recordedSha: A, factorySha: A });
ok("盘上读不出 sha → 不跟随（宁可少更新）", v.follow === false, v.reason);

v = decideRefresh({ rel: "prompts/factory/stage4_writing.md", curSha: A,
                    recordedSha: undefined, factorySha: A });
ok("factory/ 自己不参与出厂副本判据（它随版本 force 刷新）",
   v.follow === false, v.reason);

v = decideRefresh({ rel: "scripts/nfctl.py", curSha: A, recordedSha: undefined, factorySha: A });
ok("非 prompts/ 路径不享受这条豁免（scripts 是代码，走 force 刷新）",
   v.follow === false, v.reason);

console.log("\n=== 5. isUserEditablePrompt 的界 ===");
ok("顶层提示词 = true", isUserEditablePrompt("prompts/stage4_writing.md") === true);
ok("factory 副本 = false", isUserEditablePrompt("prompts/factory/stage4_writing.md") === false);
ok("style 子目录仍算顶层用户资产（提示词创作资产）",
   isUserEditablePrompt("prompts/style/letter_style.md") === true);
ok("空/undefined = false", isUserEditablePrompt(undefined) === false
   && isUserEditablePrompt("") === false);
ok("scripts = false", isUserEditablePrompt("scripts/nfctl.py") === false);

console.log("\n" + "=".repeat(60));
console.log("  通过 " + pass + " / 失败 " + fails.length);
if (fails.length) { fails.forEach((f) => console.log("    - " + f)); process.exit(1); }
console.log("  → 全部通过。");
