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


def main():
    case_no_raw_hex()
    case_dark_theme_covers_viz()
    case_referenced_vars_exist()
    case_graphs_use_style_not_attr()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
