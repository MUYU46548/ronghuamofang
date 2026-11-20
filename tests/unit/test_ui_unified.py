# -*- coding: utf-8 -*-
"""B4 · UI 统一判据（2026-11-14，阶段 2）。

守三件事：
  1. **组件不再各自重定义全局基础类**（`.card / .mini / .pill / .meta / .spacer / .empty /
     .card-head`）—— 这是"每页自创一套"的源头。要特殊形态就起自己的类名（例：画布空列叫
     `.col-empty`），不许覆盖全局。
  2. **全局只定义一次**：同一基础类在 style.css 里出现两次以上，说明已经漂移。
  3. **布局工具类在、且不硬编码颜色**：`.row / .row-wrap / .row-tight / .row-baseline /
     .stack / .sep-dashed / .focus-accent` 走主题变量，暗色主题下才不会看不见。

为什么要有它：2026-11-07 盘出“同一组 flex 骨架在 20 多个组件里各写一遍”，
清完一批后必须用判据锁住，否则下一个人（或下一个 AI）会照抄旧组件的写法再漂一次。

用法：python tests/unit/test_ui_unified.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "console" / "src"
BASE_CLASSES = ("card", "mini", "pill", "meta", "spacer", "empty", "card-head")
TOOLS = ("row", "row-wrap", "row-tight", "row-baseline", "stack", "sep-dashed", "focus-accent")

PASS, FAIL = [], []
NOTE = re.compile(r"/\*.*?\*/", re.S)


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if extra else ""))


def rules(css: str):
    """[(selector, body)] —— 按 } 切块，取每块最后一个 { 之前的选择器。"""
    out = []
    for chunk in NOTE.sub("", css).split("}"):
        if "{" not in chunk:
            continue
        sel, _, body = chunk.rpartition("{")
        sel = sel.strip().split("\n")[-1].strip().rstrip(",").strip()
        if sel:
            out.append((sel, body.strip()))
    return out


def scoped_style(path: Path) -> str:
    t = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"<style[^>]*>(.*?)</style>", t, re.S)
    return m.group(1) if m else ""


def case_no_redefine():
    print("\n[A] 组件不得重定义全局基础类")
    bad = []
    for f in sorted(SRC.glob("*.vue")):
        css = scoped_style(f)
        if not css:
            continue
        for sel, _body in rules(css):
            m = re.fullmatch(r"\.([a-z][a-z-]*)", sel)
            if m and m.group(1) in BASE_CLASSES:
                bad.append("%s: %s" % (f.name, sel))
    check("A1 组件里没有对基础类的重定义（要特殊形态就起自己的类名）",
          not bad, bad[:8])


def case_global_once():
    print("\n[B] 全局基础类只定义一次")
    css = (SRC / "style.css").read_text(encoding="utf-8", errors="ignore")
    counts = {}
    for sel, _body in rules(css):
        m = re.fullmatch(r"\.([a-z][a-z-]*)", sel)
        if m and m.group(1) in BASE_CLASSES:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    dups = {k: v for k, v in counts.items() if v > 1}
    check("B1 每个基础类在 style.css 里只出现一次", not dups, dups)
    check("B2 基础类都还在（没被误删）",
          all(k in counts for k in ("card", "mini", "pill", "meta", "spacer")), counts)


def case_tools():
    print("\n[C] 布局工具类：在、而且不硬编码颜色")
    css = (SRC / "style.css").read_text(encoding="utf-8", errors="ignore")
    bodies = {}
    for sel, body in rules(css):
        m = re.match(r"\.([a-z][a-z-]*)(?::[a-z-]+)?$", sel)
        if m and m.group(1) in TOOLS:
            bodies.setdefault(m.group(1), []).append(body)
    check("C1 七个工具类都在", all(t in bodies for t in TOOLS),
          [t for t in TOOLS if t not in bodies])
    hard = []
    for name, items in bodies.items():
        for b in items:
            if re.search(r"#(?:[0-9a-fA-F]{3,8})\b|rgba?\(", b):
                hard.append(name)
    check("C2 工具类不用硬编码颜色（暗色主题下会看不见）", not hard, hard)


def case_tokens_used():
    print("\n[D] 新增样式走主题变量（宽度 / 间距不许自创第二档）")
    css = (SRC / "style.css").read_text(encoding="utf-8", errors="ignore")
    check("D1 内容宽度只有一个真源 --content-max",
          css.count("--content-max:") == 1, css.count("--content-max:"))
    check("D2 没有 .content-wide 第二档", ".content-wide" not in css)


# 工具类的"值"（与 style.css 里那几条必须一致；改工具类要同时改这里，
# 所以 C1 另有一条断言盯着"工具类没被偷偷改值"）
TOOLS_DECLS = {
    "row": {"display": "flex", "align-items": "center", "gap": "8px"},
    "row-tight": {"display": "flex", "align-items": "center", "gap": "6px"},
    "row-wrap": {"display": "flex", "align-items": "center", "gap": "8px",
                 "flex-wrap": "wrap"},
    "row-baseline": {"display": "flex", "align-items": "baseline", "gap": "10px"},
    "stack": {"display": "flex", "flex-direction": "column", "gap": "4px"},
}


def _decls(body):
    d = {}
    for part in body.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        d[k.strip()] = " ".join(v.split())
    return d


def case_no_duplicate_skeleton():
    """E（2026-11-20 阶段 2 收尾）：组件里不许再手写与工具类**完全等价**的骨架。

    为什么：同一组 flex 骨架在 20 多个组件里各写一遍，是"每页自创一套"的根。
    改一个间距要翻二十个文件、必然漏几个 —— 漏的那个页面上就是错位。
    本次把 29 处精确等价的规则换成工具类（视觉零变化：声明逐字相同），
    这条断言防的是"下一个人照抄旧写法又长回来"。
    """
    print("\n[E] 重复骨架已换成工具类（且不许长回来）")
    dups = []
    for f in sorted(SRC.glob("*.vue")):
        css = scoped_style(f)
        if not css:
            continue
        for sel, body in rules(css):
            m = re.fullmatch(r"\.([a-z][a-z-]*)", sel)     # 只看单类选择器
            if not m:
                continue
            d = _decls(body)
            for name, want in TOOLS_DECLS.items():
                if d == want:
                    dups.append("%s: %s = .%s" % (f.name, sel, name))
    check("E1 没有与工具类逐字等价的单类规则（骨架只能复用 .row/.stack 那一套）",
          not dups, dups[:6])

    css = (SRC / "style.css").read_text(encoding="utf-8", errors="ignore")
    got = {}
    for sel, body in rules(css):
        m = re.fullmatch(r"\.([a-z][a-z-]*)", sel)
        if m and m.group(1) in TOOLS_DECLS:
            got[m.group(1)] = _decls(body)
    wrong = {k: got.get(k) for k, v in TOOLS_DECLS.items() if got.get(k) != v}
    check("E2 工具类的值没被改过（本表的期望值与 style.css 一致）", not wrong, wrong)

    # 反向：工具类必须真的被组件用上（只建不用 = 白建）
    used = sum(len(re.findall(r'class="[^"]*\b%s\b[^"]*"' % t, f.read_text(encoding="utf-8")))
               for f in SRC.glob("*.vue") for t in TOOLS_DECLS if t != "row")
    check("E3 工具类在模板里真被用着（不是建完就放着）", used >= 20, used)


for fn in (case_no_redefine, case_global_once, case_tools, case_tokens_used,
           case_no_duplicate_skeleton):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
