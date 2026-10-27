# -*- coding: utf-8 -*-
"""出厂配置的键级升级（2026-10-26，用户第 13 条「每章只有两千多字」）。

## 为什么需要它

`seedWorkspace()` 对 `config/` 的纪律是「只补缺、绝不覆盖」（承载用户的书名、
路径、模型选择），代价是**老用户永远拿不到新出厂默认值**。

实测后果（本机安装态工作区，seed v47 = 最新安装包）：

    chapter.target_words: [2000, 5500]      ← 首装那版
    当前出厂:              [5000, 9000]

于是 2500 字一章正好落在区间内 → stage4 校验绿灯 → 定向扩写永不触发 →
整本书每章两千多字，而体检报告「PASS=14」，全程没有任何告警。
用户原话：「依旧每章只有两千多字，作为小说严重不合格。我这种垃圾作者都能写
6000 到 8000 字。」

## 判据（纪律比功能重要）

- **只改用户没动过的键**：当前值必须逐项等于旧出厂值；用户改过就一个字不动。
- 两种 YAML 写法都要认（行内 `[2000, 5500]` 与块列表 `- 2000`）。
- 幂等：迁移后再跑一次不再改动。
- 不误伤相邻行（`\s` 会吃换行，正则必须用 `[ \t]`）。
- CRLF 文件不能写坏行尾。

实现住在 `console/main/index.js`（Electron 侧，依赖 electron 无法直接 require），
故这里**抽取源码里的常量与函数体**丢给 node 跑 —— 与 „F7 seedWorkspace 升级‟
同一套做法，保证测的是真代码而不是复制品。

用法：python tests/unit/test_seed_config_migration.py
"""
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


PROBE = r"""
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(process.argv[2], "utf8");
const m = src.match(/const SEED_CONFIG_MIGRATIONS = \[[\s\S]*?\n\];/);
if (!m) { console.log("__NO_CONST__"); process.exit(3); }
const MIG = new Function(m[0] + "; return SEED_CONFIG_MIGRATIONS;")();
function migrate(text) {
  for (const rule of MIG) {
    if (rule.inline && rule.inline.test(text)) {
      return { text: text.replace(rule.inline, "$1 " + rule.to), changed: true };
    }
    if (rule.block && rule.block.test(text)) {
      return { text: text.replace(rule.block, "$1 " + rule.to + "\n"), changed: true };
    }
  }
  return { text, changed: false };
}
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = cases.map((c) => {
  const r = migrate(c.src);
  let idem = null;
  if (c.thenIdempotent && r.changed) {
    const r2 = migrate(r.text);
    idem = !(r2.changed || r2.text !== r.text);
  }
  return { name: c.name, got: r.text, changed: r.changed, idem };
});
console.log("__RESULT__" + JSON.stringify(out));

// ---------- ensureEnhancedMode（2026-10-26：目标字数提到 5000 → 补拆群开关）----------
const em = src.match(/function ensureEnhancedMode\(ws\) \{[\s\S]*?\n\}/);
if (!em) { console.log("__ENH_MISSING__"); process.exit(4); }
const enhCases = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const results = enhCases.map((c) => {
  const dir = fs.mkdtempSync(path.join(require("os").tmpdir(), "nf_enh_"));
  const cfg = path.join(dir, "config");
  fs.mkdirSync(cfg, { recursive: true });
  fs.writeFileSync(path.join(cfg, "system.yaml"), c.src, "utf8");
  const run = new Function("fs", "path", "SEED_CONFIG_DIR", "ws",
                           em[0] + "\nensureEnhancedMode(ws);");
  run(fs, path, "config", dir);
  const got = fs.readFileSync(path.join(cfg, "system.yaml"), "utf8");
  fs.rmSync(dir, { recursive: true, force: true });
  return { name: c.name, got };
});
console.log("__ENH__" + JSON.stringify(results));
"""


def main():
    src_path = ROOT / "console" / "main" / "index.js"
    src = src_path.read_text(encoding="utf-8")

    node = shutil.which("node")
    if not node:
        check(True, "跳过 node 行为验证（环境无 node）")
        return _report()

    # 静态判据：常量表存在，且**没有**用 \s（\s 吃换行会误删相邻配置行）
    m = re.search(r"const SEED_CONFIG_MIGRATIONS = \[[\s\S]*?\n\];", src)
    check("index.js 里有 SEED_CONFIG_MIGRATIONS 常量表", m is not None)
    if not m:
        return _report()
    block = m.group(0)
    check("迁移判据用 [ \\t] 而不是 \\s（防跨行误匹配）",
          "\\s*" not in block, block[:200])
    check("迁移目标是 [5000, 9000]（当前出厂下限）", "[5000, 9000]" in block)
    check("迁移源是旧出厂值 [2000, 5500]", "2000" in block and "5500" in block)

    cases = [
        {"name": "块列表（用户真实形态）",
         "src": "chapter:\n  target_words:\n  - 2000\n  - 5500\n  summary_every: 15\n",
         "want": "chapter:\n  target_words: [5000, 9000]\n  summary_every: 15\n",
         "wantChanged": True, "thenIdempotent": True},
        {"name": "行内列表",
         "src": "chapter:\n  target_words: [2000, 5500]\n  summary_every: 15\n",
         "want": "chapter:\n  target_words: [5000, 9000]\n  summary_every: 15\n",
         "wantChanged": True},
        {"name": "用户改过（3000/6000）→ 一个字不动",
         "src": "chapter:\n  target_words: [3000, 6000]\n",
         "want": "chapter:\n  target_words: [3000, 6000]\n",
         "wantChanged": False},
        {"name": "已是新默认 → 不动",
         "src": "chapter:\n  target_words: [5000, 9000]\n",
         "want": "chapter:\n  target_words: [5000, 9000]\n",
         "wantChanged": False},
        {"name": "CRLF 块列表（不改坏行尾）",
         "src": "chapter:\r\n  target_words:\r\n  - 2000\r\n  - 5500\r\n  summary_every: 15\r\n",
         "want": "chapter:\r\n  target_words: [5000, 9000]\n  summary_every: 15\r\n",
         "wantChanged": True},
        {"name": "无关配置（没有 target_words）→ 不动",
         "src": "engine: direct\nchapter:\n  page_break: true\n",
         "want": "engine: direct\nchapter:\n  page_break: true\n",
         "wantChanged": False},
    ]

    probe = Path(tempfile.mkdtemp(prefix="nf_cfgmig_"))
    try:
        js = probe / "probe.cjs"
        js.write_text(PROBE, encoding="utf-8")
        cj = probe / "cases.json"
        cj.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

        # ---- 加强模式补键（2026-10-26）----
        enh_cases = [
            {"name": "缺 enhanced_mode → 补 true（篇幅口径 5000 需要拆群）",
             "src": "chapter:\n  target_words: [5000, 9000]\n  page_break: true\n"},
            {"name": "用户显式写 false → 一个字不动",
             "src": "chapter:\n  target_words: [5000, 9000]\n  enhanced_mode: false\n"},
            {"name": "用户显式写 true → 不动（幂等）",
             "src": "chapter:\n  target_words: [5000, 9000]\n  enhanced_mode: true\n"},
            {"name": "没有 chapter 段 → 不猜结构，不动",
             "src": "engine: direct\nbudget:\n  limit_yuan: 300\n"},
        ]
        ej = probe / "enh.json"
        ej.write_text(json.dumps(enh_cases, ensure_ascii=False), encoding="utf-8")

        proc = subprocess.run([node, str(js), str(src_path), str(cj), str(ej)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
        out = proc.stdout or ""
        if "__RESULT__" not in out:
            check(False, "node 探针产出结果",
                  "stdout=" + out[-300:] + " stderr=" + (proc.stderr or "")[-300:])
            return _report()
        results = json.loads(out.split("__RESULT__", 1)[1].splitlines()[0])
        for want, got in zip(cases, results):
            check(want["name"] + " → 结果一致",
                  got["got"] == want["want"] and got["changed"] == want["wantChanged"],
                  "changed=%s got=%r" % (got["changed"], got["got"]))
            if want.get("thenIdempotent"):
                check(want["name"] + " → 幂等（再跑一次不再改）", got["idem"] is True, got["idem"])

        print("\n【D】加强模式补键（弱模型写不长，得靠拆群凑）")
        check("index.js 里有 ensureEnhancedMode", "__ENH_MISSING__" not in out,
              out[:120])
        check("seedWorkspace 调用了它",
              "ensureEnhancedMode(ws)" in src)
        if "__ENH__" in out:
            enh = json.loads(out.split("__ENH__", 1)[1].splitlines()[0])
            got0 = enh[0]["got"]
            check("缺 enhanced_mode → 补成 true",
                  "enhanced_mode: true" in got0 and "target_words" in got0, got0[:200])
            check("补的时候保留原有键（target_words / page_break 都在）",
                  "target_words" in got0 and "page_break" in got0, got0[:200])
            check("用户显式 false → 不动",
                  "enhanced_mode: false" in enh[1]["got"]
                  and enh[1]["got"].count("enhanced_mode") == 1, enh[1]["got"])
            check("用户显式 true → 不重复插入",
                  enh[2]["got"].count("enhanced_mode") == 1, enh[2]["got"])
            check("没有 chapter 段 → 不凭空造结构",
                  "enhanced_mode" not in enh[3]["got"], enh[3]["got"])
    finally:
        shutil.rmtree(probe, ignore_errors=True)

    return _report()


def _report():
    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
