# -*- coding: utf-8 -*-
"""界面颜色令牌的护栏（B4 阶段 1，2026-11-07）。

## 为什么要有它

用户原话：“UI 统一问题，我都懒得骂了，天天自创”。盘下来真·自创色值并不多（30 行），
但**没有任何门禁**守着 —— 今天清完，下一轮随手写 `background: #fff8d6` 就又回去了，
而暗色主题下这类硬编码会直接“看不清”，且没有报错。

判据三条：

1. **组件里不许出现裸色值**：`.vue` 里除 `var(--x, #hex)` 的兜底与主题调色板定义外，
   不允许 `#rrggbb`。颜色只能来自 `style.css` 的主题变量。
2. **主题调色板必须成对**：`style.css` 的 `:root` 里定义的每个 `--viz-*`，
   必须在 `:root.is-dark` 里有对应覆盖 —— 否则暗色主题下图表会沿用亮色值（白底白字）。
3. **组件引用的 `--viz-*` 必须真实存在**：引用了没定义的变量，浏览器不会报错，
   只会让元素变成默认色，属于“静默白给”。

用法：python tests/unit/test_ui_tokens.py
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
                           ("  -> " + str(detail)[:260]) if detail else ""))


# 主题调色板：App.vue 里点色块的那 10 行本来就是“色值定义”，不算自创。
PALETTE_LINE = re.compile(r'color:\s*"#[0-9a-fA-F]{6}"')
# var(--warn, #b8860b) 这类兜底是允许的：变量缺失时还有个能看的颜色。
VAR_FALLBACK = re.compile(r"var\(--[a-zA-Z0-9-]+\s*,\s*#[0-9a-fA-F]{3,8}\)")
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def _components():
    return sorted(list(SRC.glob("*.vue")) + list(SRC.glob("*.css")))


def case_no_raw_hex():
    print("\n【1】组件里不许出现裸色值（style.css 只允许在 --变量定义 里写色值）")
    bad = []
    for f in _components():
        is_css = f.suffix == ".css"
        for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if PALETTE_LINE.search(line):
                continue
            if is_css and re.match(r"^\s*--[a-z0-9-]+\s*:", line):
                continue          # 主题调色板/令牌定义处：色值本来就该写在这里
            stripped = VAR_FALLBACK.sub("", line)
            # 纯白/纯黑不算“自创”：它们只用在强调色填充之上的文字（徽标/进度条），
            # 两种主题都成立；要的是“别再有品牌紫/近白底这种只对亮色主题成立的色”。
            stripped = re.sub(r"#fff{1}|#ffffff|#000000|#000\b", "", stripped)
            if HEX.search(stripped):
                bad.append("%s:%d  %s" % (f.name, no, line.strip()[:80]))
    check("零裸色值", not bad, bad[:6])


def case_dark_theme_covers_viz():
    print("\n【2】每个 --viz-* 在暗色主题里都有覆盖（否则暗色下白底白字）")
    css = (SRC / "style.css").read_text(encoding="utf-8")
    root_block = css.split(":root.is-dark", 1)[0]
    dark_block = css.split(":root.is-dark", 1)[1] if ":root.is-dark" in css else ""
    light = set(re.findall(r"(--viz-[a-z0-9-]+)\s*:", root_block))
    dark = set(re.findall(r"(--viz-[a-z0-9-]+)\s*:", dark_block))
    missing = sorted(light - dark)
    check("亮色定义 %d 个 --viz-*，暗色全覆盖" % len(light), not missing, missing)


def case_referenced_vars_exist():
    print("\n【3】组件引用的 --viz-* 必须在 style.css 里有定义（没定义 = 静默用默认色）")
    css = (SRC / "style.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    used = set()
    for f in _components():
        used |= set(re.findall(r"var\((--viz-[a-z0-9-]+)", f.read_text(encoding="utf-8")))
    missing = sorted(used - defined)
    check("引用的 --viz-* 全部有定义（%d 个）" % len(used), not missing, missing)


def case_graphs_use_style_not_attr():
    print("\n【4】图表的颜色必须走 .style()/CSS 变量，不能写死进 SVG 属性")
    bad = []
    for name in ("PlotGraph.vue", "RoleGraph.vue"):
        f = SRC / name
        for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'\.attr\("(fill|stroke)",\s*"#', line):
                bad.append("%s:%d  %s" % (name, no, line.strip()[:70]))
    check("SVG 属性里没有写死的颜色", not bad, bad[:5])


def case_content_width_single_lane():
    print("\n【5】内容区宽度只有一档（B4 阶段 3，避免“大页面窄一档、审阅台宽一档”再分叉）")
    css = (SRC / "style.css").read_text(encoding="utf-8")
    defs = re.findall(r"--content-max\s*:\s*([0-9]+)px", css)
    check("--content-max 只定义一次（当前 %s）" % (defs or "无"), len(defs) == 1, defs)
    if defs:
        check("宽度不小于 1560px（作者：不应该太窄）", int(defs[0]) >= 1560, defs[0])
    m = re.search(r"\.content\s*\{[^}]*max-width\s*:\s*([^;]+);", css, re.S)
    check(".content 的 max-width 走 var(--content-max)",
          bool(m) and "var(--content-max)" in m.group(1), m.group(1).strip() if m else "没找到 .content 规则")
    stray = []
    for f in _components():
        for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "content-wide" in line:
                stray.append("%s:%d" % (f.name, no))
    check("不再有第二档 .content-wide", not stray, stray[:5])


def _theme_blocks(css):
    """把 `:root.theme-x { ... }` 拆成 {主题id: {变量: 值}}。

    同时收 `:root, :root.theme-purple { ... }` 这种合并写法 —— 首套主题与默认值共用
    一个块，只按 `theme-(\\w+)` 抓选择器会把 purple 漏掉（L1 第一次落地就踩过）。
    """
    out = {}
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        ids = re.findall(r"theme-([\w-]+)", sel)
        if not ids:
            continue
        vars_ = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
        for i in ids:
            out.setdefault(i, {}).update(vars_)
    return out


def _rel_lum(hex_color):
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = _rel_lum(a), _rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def case_on_accent_contrast():
    """【6】每套主题的 --on-accent 压在 --accent 上必须有 4.5:1（L1，2026-11-25）。

    为什么：主按钮原来是写死白字，而十套强调色全是中低饱和，白字实测 2.2–3.5:1 ——
    “能看见但看不清”，浏览器不报错、截图也不觉得坏，只有真读起来累。
    加一套新主题时最容易漏的就是这一行，所以钉在门禁里。
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    blocks = _theme_blocks(css)
    root = re.search(r":root\s*\{([^}]*)\}", css, re.S)
    root_vars = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root.group(1))) if root else {}
    default_on = blocks.get("purple", {}).get("--on-accent") or root_vars.get("--on-accent")

    check("存在默认 --on-accent（:root.theme-purple 或 :root）", bool(default_on), default_on)

    accents = {k: v.get("--accent") for k, v in blocks.items() if v.get("--accent")}
    check("找到多套主题的 --accent（当前 %d 套）" % len(accents), len(accents) >= 10,
          sorted(accents))

    bad, rows = [], []
    for tid in sorted(accents):
        on = blocks[tid].get("--on-accent") or default_on
        if not on:
            bad.append(tid + " 无 --on-accent")
            continue
        ratio = _contrast(accents[tid], on)
        rows.append("%s=%.2f" % (tid, ratio))
        if ratio < 4.5:
            bad.append("%s 只有 %.2f:1" % (tid, ratio))
    check("每套主题 --on-accent 对 --accent 均 ≥ 4.5:1", not bad, bad or rows)

    # 主按钮 2026-11-25 定案：底用 --accent-strong、字保持白。
    # 这条单独钉住 —— 十套主题各一个值，最容易漏掉新增的那一套。
    miss, dark_bad, strong_rows = [], [], []
    for tid in sorted(accents):
        st = blocks[tid].get("--accent-strong")
        if not st:
            miss.append(tid)
            continue
        ratio = _contrast(st, "#ffffff")
        strong_rows.append("%s=%.2f" % (tid, ratio))
        if ratio < 4.5:
            dark_bad.append("%s 只有 %.2f:1" % (tid, ratio))
    check("每套主题都定义了 --accent-strong", not miss, miss)
    check("每套主题 --accent-strong 配白字 ≥ 4.5:1", not dark_bad, dark_bad or strong_rows)


def case_button_geometry_single_source():
    """【7】`.mini` 与 `.btn` 的几何必须逐字一致（2026-11-25）。

    全仓 637 处 `.mini` 不可能一次改完，所以两套名字要并存一段时间。
    并存期间最大的风险是**漂移**：谁给 `.btn` 调了 2px，同一行里就会出现
    “三个按钮高度差 2px”——不报错、截图也不明显，只有盯着看才觉得不齐。
    这条只认一个事实：两者声明的 height / padding 必须相同。
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    geo = {}
    for m in re.finditer(r"(?m)^\.(mini|btn)\s*\{([^}]*)\}", css):
        sel, body = "." + m.group(1), m.group(2)
        geo[sel] = {
            "height": (re.search(r"height:\s*([^;]+);", body) or [None, None])[1],
            "padding": (re.search(r"padding:\s*([^;]+);", body) or [None, None])[1],
        }
    if len(geo) < 2:
        check("`.mini` 与 `.btn` 都已定义", False, sorted(geo))
        return
    same = geo[".mini"] == geo[".btn"]
    check("`.mini` 与 `.btn` 的 height/padding 一致", same,
          {k: v for k, v in geo.items()})


def case_field_base_single_source():
    """【8】输入控件的几何只许由 `:where(...)` 基础层给（2026-11-25）。

    现状本来是“没有全局基础样式”，于是每个面板各写一套：padding 3/4/5/6 四种、
    圆角 7/8 两种，同一屏两个输入框差 4px。现在统一到一条 `:where()` 规则里
    （特异性 0，面板自己的类照样能覆盖宽度），其余通用类只许留宽度/布局。

    这条钉两件事：基础层必须在；四个通用类不许再自己写 padding/border。
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    check("输入控件有 :where() 基础层（特异性 0，不压面板定制）",
          re.search(r':where\(input:not\(\[type="checkbox"\]\)', css) is not None)

    bad = []
    for name in (".text-input", ".prov-input", ".rate-input", ".vault-path-input"):
        m = re.search(r"(?m)^" + re.escape(name) + r"\s*\{([^}]*)\}", css)
        if not m:
            bad.append(name + " 不存在")
            continue
        body = m.group(1)
        for prop in ("padding", "border", "border-radius", "font-size", "background"):
            if re.search(r"(?m)(^|;)\s*" + prop + r"\s*:", body):
                bad.append("%s 里还有 %s" % (name, prop))
    check("四个通用输入类只留布局，不再重复几何", not bad, bad)


def case_card_nesting():
    """【9】卡片里再套卡片时，不许画第二层框（2026-11-25）。

    实测章节页有 2 处 `.card` 套 `.card`：两层边框 + 两层阴影叠着，
    眼睛要先分辨“哪个框是容器”，这就是“看着乱但说不出哪乱”。
    规则只有一条：内层退成底色分组（无边框、无阴影），容器只画一次框。
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    m = re.search(r"(?m)^\.card \.card\s*\{([^}]*)\}", css)
    check("`.card .card` 有归零规则", m is not None)
    if not m:
        return
    body = m.group(1)
    check("内层卡片不画边框", re.search(r"border:[ ]*0", body) is not None, body.strip()[:60])
    check("内层卡片不带阴影", re.search(r"box-shadow:[ ]*none", body) is not None, body.strip()[:60])


def case_status_pill_theme_follows():
    """【10】状态徽标必须跟主题走（2026-11-25）。

    这七条原来是硬编码 rgba（`rgba(201,162,106,.22)` 之类）：换主题时底色不动，
    暗色下靠运气。现在底色一律 `color-mix(主题色, transparent)`。
    判据两条：`st-*` 里不许出现数字色值；`.badge` 与 `.pill` 的圆角/字号必须相同
    （同为 12px 徽标，长得不一样就是两套）。
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    hard = []
    for m in re.finditer(r"(?m)^(\.[^{]*)\{([^}]*)\}", css):
        first = m.group(1).strip().split(",")[0].strip()
        if not first.startswith(".st-"):
            continue                      # 只认首类名为 .st-* 的规则（.cost-stat 这类不算）
        body = m.group(2)
        if re.search(r"rgba?\([ ]*\d", body) or re.search(r"#[0-9a-fA-F]{6}", body):
            hard.append(first[:28])
    check("状态徽标里没有写死的数字色值", not hard, hard)

    def geo(name):
        m = re.search(r"(?m)^" + re.escape(name) + r"\s*\{([^}]*)\}", css)
        if not m:
            return None
        b = m.group(1)
        return (re.search(r"border-radius:[ ]*([^;]+);", b) or [None, None])[1], \
               (re.search(r"font-size:[ ]*([^;]+);", b) or [None, None])[1]

    pill, badge = geo(".pill"), geo(".badge")
    check(".pill 与 .badge 的圆角/字号一致", pill is not None and pill == badge, {".pill": pill, ".badge": badge})


def case_selected_and_toast():
    """【11】选中态不许再用强调色实心；提示条必须保留换行（2026-11-25）。

    选中语言已经定了三批才统一：侧栏（紫，作者允许保留）/ 按钮（中性）/ chips（原先是
    --accent-soft 或 --accent 实心）。现在除侧栏 .tab.active 外，`.xxx.on` 一律中性反色。
    这条钉住“不许再长回去”。

    提示条那条更朴素：say() 里一堆带换行的多行说明，`.toast` 没有 white-space:pre-line
    时会被压成一长条 —— 读不通，但不报错。
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")

    bad = []
    for m in re.finditer(r"(?m)^([^{}\n]*\.on[^{}]*)\{([^}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        if ".tab.active" in sel or "theme-swatch" in sel:
            continue
        if "background: var(--accent)" in body or "background:var(--accent)" in body:
            bad.append(sel[:48])
    check("没有 .on 规则再用强调色实心（侧栏除外）", not bad, bad)

    m = re.search(r"(?m)^\.toast\s*\{([^}]*)\}", css)
    has_preline = bool(m) and re.search(r"white-space:[ ]*pre-line", m.group(1)) is not None
    check("提示条保留换行（white-space: pre-line）", has_preline,
          (m.group(1)[:60] if m else "没找到 .toast"))


def case_wide_modal_covers_rail():
    """【12】宽屏下遮罩必须从侧栏右侧开始，且侧栏在抽屉打开时禁点（2026-11-25）。

    查出来的真 bug：`.drawer-mask` 的 `inset` 用的是 `--topbar-h:103px`（横向顶栏高度），
    但 >=900px 时顶栏其实是左侧 216px 的竖条 —— 于是抽屉开着的时候：
      · 顶部 103px 留了一条透明带，那行按钮仍然亮着、仍然能点；
      · 左侧导航在整个 .content 层叠上下文之外（z 5 vs 遮罩在 content 内），压不住它，
        点一下就能换页，换页会卸载抽屉，**绕过“未保存”确认**。
    这两条都是“不报错、截图也不明显”的静默失败，所以钉进判据。
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    check("宽屏遮罩避开侧栏（inset: 0 0 0 216px）",
          re.search(r"\.drawer-mask\s*\{[^}]*inset:[ ]*0 0 0 216px", css) is not None)
    check("抽屉打开时侧栏禁点",
          re.search(r"body:has\(\.drawer-mask\)[^{]*\.topbar[^{]*\{[^}]*pointer-events:[ ]*none",
                    css) is not None)


def main():
    case_no_raw_hex()
    case_dark_theme_covers_viz()
    case_referenced_vars_exist()
    case_graphs_use_style_not_attr()
    case_content_width_single_lane()
    case_on_accent_contrast()
    case_button_geometry_single_source()
    case_field_base_single_source()
    case_card_nesting()
    case_status_pill_theme_follows()
    case_selected_and_toast()
    case_wide_modal_covers_rail()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
