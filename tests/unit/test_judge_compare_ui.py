# -*- coding: utf-8 -*-
"""I6 三稿对照的接线判据（2026-10-31）。

## 为什么单独一个用例

这一块**最容易做假**：三栏摆出来很好看，但
① 缺一版时显示空栏（看着像"没变化"，实际是"没生成过"）；
② 为了让界面好看，把三份正文按行对齐（段数一变就错位，看着整齐反而更误导）；
③ 顺手把 `/judge/destroy` 改成**默认**回全文（一页 1.8 万字符，拖慢每次单章详情）。

三条都很容易在"把它做得更好看"的过程中犯。这里逐条钉住。

用法：python tests/unit/test_judge_compare_ui.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PANEL = (ROOT / "console" / "src" / "JudgePanel.vue").read_text(encoding="utf-8")
CSS = (ROOT / "console" / "src" / "style.css").read_text(encoding="utf-8")
API = (ROOT / "scripts" / "nf_api_domains" / "judge.py").read_text(encoding="utf-8")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 70)
    print("I6 三稿对照：接线 / 缺版处理 / 不做假对齐")
    print("=" * 70)

    print("\n【U1】入口与取数")
    check("单章详情里有\u300c三稿对照\u300d按钮",
          "三稿对照" in PANEL and "openCompare" in PANEL)
    check("取数带 text=1（默认不带正文，省体积）",
          "/judge/destroy?n=" in PANEL and "&text=1" in PANEL)
    check("后端默认**不**回 texts（体积约定）",
          'if str((q.get("text") or ["0"])[0]).lower() in ("1", "true", "yes"):' in API)

    print("\n【U2】三列齐全，缺的那版要明说")
    for key in ("raw", "checked", "refined"):
        check("有 %s 这一列" % key, '"%s"' % key in PANEL)
    check("缺版文案：这一版不存在", "这一版不存在" in PANEL)
    check("缺版不说成\u300c没变化\u300d（这是最容易骗人的一处）",
          "是根本没生成过" in PANEL)
    check("缺版说明具体缺哪个阶段",
          "没跑过检查阶段" in PANEL and "没跑过润色阶段" in PANEL)

    print("\n【U3】不做假对齐")
    check("明确写了\u300c不做逐行对齐\u300d（对用户的前置说明）",
          "不做逐行对齐" in PANEL)
    check("三栏是各自滚动（不是同一张表按行排版）",
          ".jc-body" in CSS and "overflow-y: auto" in CSS)
    check("没有用表格做三栏（table 会把行强行对齐）",
          "jc-wrap" in PANEL and "<table" not in PANEL.split("jc-wrap")[1][:600])

    print("\n【U4】统计照旧展示")
    check("顶部显示两段缩减率（原→检 / 检→润）",
          "raw->checked" in PANEL and "checked->refined" in PANEL)
    check("越过阈值时明说\u300c疑似整章重生成\u300d",
          "疑似整章重生成" in PANEL)

    print("\n【U5】样式只吃主题变量")
    block = CSS[CSS.find(".jc-wrap"):CSS.find(".jc-wrap") + 2200] if ".jc-wrap" in CSS else ""
    check("三栏样式存在", bool(block))
    bad = re.findall(r"#[0-9a-fA-F]{3,6}\b", block)
    check("没有硬编码色值（暗色主题会瞎眼）", not bad, bad)

    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
