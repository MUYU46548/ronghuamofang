// 绒花墨坊 — 审批门的一次性迁移（纯函数，node 可直测）
//
// 为什么需要它（2026-11-24）：作者把阶段 4（正文写完的审稿门）升级成“需要签字的门”，
// 出厂配置改成了 require_approval: [2, 4, 6]。但工作区的 config/system.yaml **不会被升级覆盖**
// （seedWorkspace 刻意只补缺失文件：键级合并要重写用户配置，会丢注释、可能改掉模型选择）。
// 于是老用户（包括作者这台）永远看不到阶段 4 那道门 —— 功能做了，等于没做。
//
// 这里只做**一件事**：当工作区里的 require_approval 仍然是**上一版出厂值** [2, 6] 时，
// 把 4 插进去。用户自己改过（例如加了 1、删了 6、只留 2）就**一个字都不动** ——
// 那是作者的选择，不是漏配。

const OLD_FACTORY = [2, 6];

/** 从 yaml 文本里取出 require_approval 的整数列表（取不到返回 null）。 */
function readRequireApproval(text) {
  const lines = String(text || "").split(/\r?\n/);
  const i = lines.findIndex((l) => /^require_approval:\s*$/.test(l.trim()) ||
                                   /^require_approval:\s*\[/.test(l.trim()));
  if (i < 0) return null;
  const head = lines[i].trim();
  const inline = head.match(/^require_approval:\s*\[(.*)\]/);
  if (inline) {
    const nums = inline[1].split(",").map((s) => parseInt(s.trim(), 10))
      .filter((n) => !Number.isNaN(n));
    return { style: "inline", nums, start: i, end: i };
  }
  const nums = [];
  let j = i + 1;
  for (; j < lines.length; j += 1) {
    const t = lines[j].trim();
    if (!t) continue;                      // 空行不算结束（yaml 允许）
    const m = t.match(/^-\s*(\d+)$/);
    if (!m) break;                         // 不是列表项 = 块结束
    nums.push(parseInt(m[1], 10));
  }
  return { style: "block", nums, start: i, end: j };
}

/**
 * 迁移文本。
 * @param {string} text 工作区 config/system.yaml 的内容
 * @returns {{changed:boolean, text:string, reason:string}}
 *   changed=false 时 text 原样返回。
 */
function migrateRequireApproval(text) {
  const src = String(text == null ? "" : text);
  const got = readRequireApproval(src);
  if (!got) return { changed: false, text: src, reason: "no-require_approval" };
  const same = got.nums.length === OLD_FACTORY.length &&
    got.nums.every((n, k) => n === OLD_FACTORY[k]);
  if (!same) return { changed: false, text: src, reason: "user-modified" };
  if (got.nums.indexOf(4) >= 0) return { changed: false, text: src, reason: "already-has-4" };

  const lines = src.split(/\r?\n/);
  const nl = src.indexOf("\r\n") >= 0 ? "\r\n" : "\n";
  if (got.style === "inline") {
    lines[got.start] = lines[got.start].replace(/\[(.*)\]/, "[2, 4, 6]");
  } else {
    // 插在 "- 2" 之后（保持 2、4、6 的顺序），缩进跟着原有列表项；
    // 不重排、不重写别的键。
    const indent = (lines[got.start + 1] || "  - 2").match(/^(\s*)/)[1];
    lines.splice(got.start + 2, 0, indent + "- 4");
  }
  return { changed: true, text: lines.join(nl), reason: "upgraded" };
}

module.exports = { readRequireApproval, migrateRequireApproval };
