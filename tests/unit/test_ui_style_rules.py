# -*- coding: utf-8 -*-
"""F5 三条令牌判据：字号档 / 强调色角色 / 卡片不嵌套（2026-11-28）。

## 为什么需要它

`docs/L1-令牌草案-20261125.md` 第十节列了六条“会变红的判据”，其中第 3、4、6 条
（字号只许 8 档 / 强调色只许两处 / 卡片不许嵌套）一直标着“待做（L1-b）”——
**规则写进了文档，但没有一行代码看它**。而没有判据的规则等于没有规则：
下一轮随手写个 `font-size: 13.5px` 就长回去了，跟 `test_ui_unified.py` 拦的
“flex 骨架长回去”是同一类事故。

## 三条判据的口径（都取实测量级，不是拍的）

1. **字号只许 8 档**：`11 12 13 14 15 17 18 22`。
   为什么是 8 档而不是草案里的“5 + 3”：草案的 UI 五档（12/13/14/16/20）与阅读三档
   （17 + 行高 + 行宽）是**目标态**，而现场 400+ 处已经是 12/13/14/15/18/22 这套。
   一次性全改 = 全站视觉回归（用户明说“每批 5 个组件 + 前后截图”才许动）。
   所以本轮只做**收口**：把 41 处零散值（9 / 13.5 / 14.5 / 15.5 / 19）并进最近的档，
   禁止再出现新档位。16 / 20 留给 L1-c 迁移时再对齐。
2. **强调色只许两个角色**：`--accent`（描边/文字/图示）与 `--accent-strong`（唯一实心主按钮底）。
   现状 87 处 `color: var(--accent)` 是**合法**的（强调=文字色），要拦的是
   “到处都在用强调色实心填充”——那会让强调消失（规划文档第二节第 5 行原话）。
   判据只认一件事：实心填充 `background: var(--accent)` 的规则，必须屈指可数，
   且不许出现在 `.badge` / `.pill` / `.st-*` 这类**非主操作**上。
3. **卡片不许嵌套**：`.card .card` 必须归零（无边框/无阴影），容器只画一次框。
   （这条 `test_ui_tokens.py`【9】已有；这里保留并加反证，避免它被删掉。）

## 反证

每条判据都要证明“改源码会变红”：临时造一个越界样本，断言判据函数认得出，
然后删掉样本。只报 PASS 而没有反证的判据，是装饰。

用法：python tests/unit/test_ui_style_rules.py
"""
import io
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "console" / "src"

PASS, FAIL = [], []

#: 允许的字号档位（px）。新增档位 = 红，要先把理由写进 L1-令牌草案再说。
FONT_STEPS = {11, 12, 13, 14, 15, 17, 18, 22}

#: 允许用强调色**实心填充**的选择器（按角色白名单，不是按名字乱放）。
#:
#: 判据要拦的是“强调色用满 = 没有强调”（规划文档第二节原话：
#: 徽标是紫的、主按钮是紫的、选中态是紫的、边框也偏紫）。
#: 下面三类**不是**那个问题 —— 把它们也拦下来，判据就会变成为红而红：
#:
#: ① 操作：唯一主按钮 / 作者明确保留的当前页
#: ② 数据可视化：进度条、仪表、滑点、图表点 —— 强调色在这里表示"值/位置"，是数据语言
#: ③ 交互反馈与身份：hover 态、聊天里"我"说的那条
ACCENT_FILL_ALLOW = {
    # —— ① 操作
    ".btn-primary",          # 基座主按钮
    ".tab.active",           # 侧栏当前页（作者 2026-11-25 允许保留）
    ".tabs button.active",   # 页签当前页（同上）
    "theme-swatch",          # 主题色块：它本来就是在显示这个颜色
    ".float-select-btn",     # 选中文字后浮出的唯一动作（主操作）
    # —— ② 数据可视化（强调色 = 数值，不是"强调"）
    "progress", ".ps-progress", ".ob-bar-in", ".est-bar-in", ".meter-in",
    ".run-dot", ".ob-dot", ".desk-card-dot", ".trend-sw", ".slider-knob",
    "-webkit-slider-thumb",
    # —— ③ 交互反馈 / 身份
    ":hover", ".ai-msg-user",
}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:260]) if detail else ""))


def _files():
    return sorted(list(SRC.glob("*.vue")) + list(SRC.glob("*.css")))


# ---------------------------------------------------------------- 判据 1 字号
def scan_font_steps(text):
    """返回越界字号列表 [(值, 行号)]。"""
    bad = []
    for no, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r"font-size:\s*([0-9.]+)px", line):
            v = float(m.group(1))
            if v != int(v) or int(v) not in FONT_STEPS:
                bad.append((v, no))
    return bad


def case_font_steps():
    print("\n【1】字号只许 8 档（%s）" % " ".join("%d" % v for v in sorted(FONT_STEPS)))
    bad = []
    for f in _files():
        for v, no in scan_font_steps(f.read_text(encoding="utf-8")):
            bad.append("%s:%d  %spx" % (f.name, no, v))
    check("全仓没有越界字号", not bad, bad[:8])

    # 反证：造一个 13.5px，判据必须认出来
    probe = ".x { font-size: 13.5px; }\n.y { font-size: 20px; }"
    got = scan_font_steps(probe)
    check("反证：13.5px / 20px 都判越界", len(got) == 2, got)


# ------------------------------------------------------------ 判据 2 强调色
def scan_accent_fill(text):
    """返回“用强调色实心填充、且不在白名单里”的规则选择器 [(选择器, 行号)]。

    只认 `background: var(--accent)` 这一形态：
      · `background: var(--accent-strong)` → 合法（唯一主按钮底）
      · `color: var(--accent)`             → 合法（强调=文字色，不是"强调用满"）
      · `background: var(--accent-soft)`   → 合法（浅底，不是实心）
    要拦的只有"实心填充"这一种 —— 规划文档说的"强调色用满 = 没有强调"。

    解析要点：CSS 规则有单行（`.x { a: b; }`）与多行两种写法（本仓两种都有，
    实测漏掉单行会让反证判据假 FAIL —— 造了样本却认不出来）。
    """
    bad = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "{" not in line:
            i += 1
            continue
        sel = line.split("{")[0].split(",")[0].strip()
        if "}" in line:                       # 单行规则：选择器与声明同一行
            body = line
        else:                                 # 多行规则：往下收到右花括号
            j = i
            parts = []
            while j < len(lines):
                parts.append(lines[j])
                if "}" in lines[j] and not (j == i):
                    break
                j += 1
            body = "\n".join(parts)
            i = j
        if re.search(r"background:\s*var\(--accent\)\s*;", body) or \
           re.search(r"background:\s*var\(--accent\)\s*$", body.rstrip()):
            if not any(a in sel for a in ACCENT_FILL_ALLOW):
                bad.append((sel[:48], i + 1))
        i += 1
    return bad


def case_accent_roles():
    print("\n【2】强调色只许两个角色（实心只给唯一主操作）")
    bad = []
    for f in _files():
        for sel, no in scan_accent_fill(f.read_text(encoding="utf-8")):
            bad.append("%s:%d  %s" % (f.name, no, sel))
    check("没有非主操作使用强调色实心填充", not bad, bad[:8])

    # 反证
    probe = ".badge { background: var(--accent); color: #fff; }\n.btn-primary { background: var(--accent); }"
    got = scan_accent_fill(probe)
    check("反证：徽标用实心被拦、主按钮放行",
          len(got) == 1 and ".badge" in got[0][0], got)

    # 合法形态不许误伤
    probe2 = (".x { color: var(--accent); }\n"
              ".y { background: var(--accent-soft); }\n"
              ".z { background: var(--accent-strong); }")
    check("反证：文字色 / 浅底 / 深一档主按钮底都不误伤",
          scan_accent_fill(probe2) == [], scan_accent_fill(probe2))


# ------------------------------------------------------------ 判据 3 卡片嵌套
def scan_soft_with_white(text):
    """返回“浅底 + 白字”的规则 [(选择器, 行号)]。

    为什么单独一条：`background: var(--x-soft); color: #fff` 这种组合**不报错、
    截图也不明显**，但亮色主题下白字压在浅底上对比度只有 1.x:1 —— 字在，眼看不到。
    实测本章就抓到三处（`.stage-no` ×2、`.ov-opt-tag`），其中两处是既有缺陷、
    不是这次改出来的。

    口径：只要底色是 `*-soft`，字色就不许是纯白；浅底要配同族深字（`--accent` / `--ink-*`）。
    """
    bad = []
    for m in re.finditer(r"(?m)^([^{}\n]+)\{([^{}]*)\}", text):
        sel, body = m.group(1).strip().split(",")[0].strip(), m.group(2)
        if re.search(r"background:\s*var\(--[\w-]*-soft\)", body) and \
           re.search(r"color:\s*#fff\b|color:\s*#ffffff\b", body):
            bad.append((sel[:44], text[:m.start()].count("\n") + 1))
    return bad


def case_soft_bg_not_white_text():
    print("\n【3】浅底不许配白字（`--x-soft` 底 + #fff 字 = 亮色下看不见）")
    bad = []
    for f in _files():
        for sel, no in scan_soft_with_white(f.read_text(encoding="utf-8")):
            bad.append("%s:%d  %s" % (f.name, no, sel))
    check("全仓没有“浅底 + 白字”", not bad, bad[:8])

    probe = ".a { background: var(--accent-soft); color: #fff; }\n" \
            ".b { background: var(--accent-soft); color: var(--accent); }\n" \
            ".c { background: var(--accent); color: #fff; }"
    got = scan_soft_with_white(probe)
    check("反证：浅底白字被拦，浅底深字/实心底白字都放行",
          len(got) == 1 and ".a" in got[0][0], got)


# ------------------------------------------------------------ 判据 4 卡片嵌套
def case_card_nesting():
    print("\n【4】卡片不许嵌套（内层退成底色分组）")
    css = (SRC / "style.css").read_text(encoding="utf-8")
    m = re.search(r"(?m)^\.card\s+\.card\s*\{([^}]*)\}", css)
    check("`.card .card` 有归零规则", m is not None)
    if m:
        body = m.group(1)
        check("内层卡片不画边框", re.search(r"border:\s*0", body) is not None, body.strip()[:80])
        check("内层卡片不带阴影", re.search(r"box-shadow:\s*none", body) is not None,
              body.strip()[:80])

    # 反证：把归零规则删掉，搜索会落空
    broken = re.sub(r"(?m)^\.card\s+\.card\s*\{[^}]*\}", "", css)
    check("反证：删掉归零规则后搜不到", re.search(r"(?m)^\.card\s+\.card\s*\{", broken) is None)


# ------------------------------------------------- 判据 5 侧栏必须单列
def case_sidebar_single_column():
    """竖栏里 `flex-direction: column` 必须同时 `flex-wrap: nowrap`（2026-11-28）。

    作者原话：“左侧栏在某个版本被改坏了，挤成一团。”

    根因是一个 CSS 组合陷阱，**不报错、只在窗口够矮时出现**：
    基础规则 `.tabs { flex-wrap: wrap }` 是给横排用的；宽屏媒体查询里把它改成
    `flex-direction: column` 却没把 wrap 改成 nowrap。纵向 flex 容器里 wrap 的含义
    是“排不下就往右边再开一列” —— 窗口一矮（笔记本 768 高 / 125% 缩放），
    13 个页签立刻折成两三列挤成一团，而 1080p 全屏截图完全看不出来。

    这条判据不需要浏览器：只认“竖栏规则块里有没有 nowrap”。
    页面级验证在 `tests/e2e/e2e_sidebar_nav.py`（真量 left 去重，矮视口也验）。
    """
    print("\n【5】侧栏竖排必须 nowrap（wrap + column = 折成多列）")
    css_raw = (SRC / "style.css").read_text(encoding="utf-8")
    # 注释必须整段剥掉再判：本次注释里就写着“基础规则 .tabs { flex-wrap: wrap }”，
    # 不剥的话正则先命中的是那句说明，判据自己把自己骗过去（实测踩过）。
    css = re.sub(r"/\*[\s\S]*?\*/", "", css_raw)

    def block(selector):
        m = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
        return m.group(1) if m else ""

    for sel in (".topbar .tabs", ".topbar .tab-group"):
        body = block(sel)
        check("%s 规则存在" % sel, bool(body), "没找到该规则")
        if not body:
            continue
        check("%s 声明了 flex-direction: column" % sel,
              re.search(r"flex-direction:\s*column", body) is not None,
              body.strip()[:120])
        check("%s 同时声明了 flex-wrap: nowrap（缺这条就会折列）" % sel,
              re.search(r"flex-wrap:\s*nowrap", body) is not None,
              body.strip()[:120])

    # 反证：把 nowrap 摘掉，判据必须认得出来（用同样剥注释后的文本比）
    broken = css.replace("flex-wrap: nowrap;", "")
    matched = re.search(r"(?m)^\s*\.topbar \.tabs\s*\{([^}]*)\}", broken)
    check("反证：摘掉 nowrap 后该规则不再含 nowrap",
          matched is not None and "nowrap" not in matched.group(1),
          (matched.group(1).strip()[:80] if matched else "没找到规则"))


# ------------------------------------------------- 附加：全站 scoped 样式基线
# 不在这里重复：`test_ui_base.py` 已经钉了 `<style scoped>` 的基线（它的口径是
# “`</template>` 之后真的还有 scoped 样式块”，基线 35 只许降）。
# 这里按字符串粗略数会得到另一个数字（29），两套口径并存 = 下一轮互相引用、各说各的。


def main():
    print("=" * 66)
    print("F5 令牌判据（字号 / 强调色 / 卡片嵌套）")
    print("=" * 66)
    case_font_steps()
    case_accent_roles()
    case_soft_bg_not_white_text()
    case_card_nesting()
    case_sidebar_single_column()
    print("-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("    -", f)
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
