# -*- coding: utf-8 -*-
"""数字格式统一（P1-10~13，2026-11-21）。

## 要治的病

界面问题表里"金额小数位 5 种混用"是同一天在一个成本页上拍到的：
`¥12.3456`（toFixed 4）、`¥0.0000`、`¥3.7500`、`¥50.0000`、
流水线页 `¥0.00 / ¥300`（2 位与 0 位并存）。同一个量四种写法，
读者会以为是四个不同的量，也没法横向比。

## 判据（一条规则，全仓收敛）

- 金额**只由 `fmtYuan()` 生成**，一律两位小数；
- 非 0 但不足一分写 `<¥0.01`（抹成 ¥0.00 是谎：真花掉的钱不能显示为没花）；
- 除"比率"外不许出现 `toFixed(3)` / `toFixed(4)`；
- 金额不许内联 `¥{{ x.toFixed(n) }}` —— 那正是精度漂移的入口。

为什么钉在源码而不是钉渲染结果：这些数由后端算、前端格式化，
渲染结果随数据变，只有"格式化入口唯一"是稳定的。

用法：python tests/unit/test_number_format.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "console" / "src"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def scan(pattern, allow=None):
    """在 console/src 的 .vue/.js 里找 pattern，返回 [(file, lineno, line)]。

    allow 是一组正则：整行命中的话跳过（比率之类的合法 3/4 位小数）。
    """
    allow = [re.compile(a) for a in (allow or [])]
    hits = []
    for f in sorted(list(SRC.glob("*.vue")) + list(SRC.glob("*.js"))):
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if pattern.search(line) and not any(a.search(line) for a in allow):
                hits.append((f.name, i, line.strip()[:110]))
    return hits


def main():
    print("=" * 66)
    print("数字格式统一（金额两位 / 精度唯一入口 / 比率豁免）")
    print("=" * 66)
    app = (SRC / "App.vue").read_text(encoding="utf-8")

    print("\n[N1] 金额格式只有一个入口")
    check("fmtYuan 用两位小数",
          re.search(r"function fmtYuan\(v\)[\s\S]{0,400}?toFixed\(2\)", app) is not None)
    check("fmtYuan 对不足一分写 <¥0.01（不抹成 0，不写四位）",
          "<¥0.01" in app and 'Math.abs(n) < 0.01' in app)
    check("fmtYuan 处理负数（-¥12.35 不是 ¥-12.35）", '"-¥"' in app)

    print("\n[N2] 没有四位小数的金额")
    h4 = scan(re.compile(r"toFixed\(4\)"))
    check("全仓无 toFixed(4)", not h4, h4[:3])

    print("\n[N3] 三位小数只留给比率")
    h3 = scan(re.compile(r"toFixed\(3\)"),
              allow=[r"drift", r"ratio", r"比例"])
    check("除比率/偏差外无 toFixed(3)", not h3, h3[:3])

    print("\n[N4] 金额不许内联 toFixed（精度漂移的入口）")
    inline = scan(re.compile(r"¥\{\{[^}]*toFixed\("))
    check("没有 ¥{{ x.toFixed(...) }}", not inline, inline[:3])
    # 组件里没有 App.vue 的 fmtYuan，写 toFixed(2) 是可以的（规则是"两位"）；
    # 这里只拦"非两位"的金额内联 —— 那才是 5 种精度混用的来源。
    money_inline = scan(re.compile(r"toFixed\([0134]\)\s*\}\}\s*元"))
    check("没有“x.toFixed(0/1/3/4) 元”式内联", not money_inline, money_inline[:3])

    print("\n[N5] 关键位置确实走 fmtYuan")
    for label, pat in (
            ("流水线累计花费", r'ps-value">{{ fmtYuan\(state\.cost\.spent_yuan\)'),
            ("成本页本次花费", r'fmtYuan\(streamingCost\.spent_yuan\)'),
            ("成本页预算剩余", r'fmtYuan\(streamingCost\.budget_left\)'),
            ("流式花费浮标", r'stream-cost">{{ fmtYuan\(streamCost\)')):
        check(label + " 用 fmtYuan", re.search(pat, app) is not None)

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
