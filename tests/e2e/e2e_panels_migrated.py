# -*- coding: utf-8 -*-
"""三块小面板迁到 Tailwind 之后的真机视觉验收（2026-11-27）。

覆盖：交接稿（HandoffPanel）/ 质量趋势（QualityTrend）/ 指南针（CompassPanel）。
这三个组件本轮把 `<style scoped>` 整块摘掉了，样式改由 Tailwind 工具类 + 主题变量承担。

为什么必须有这个套件（而不是只看 `test_ui_base.py` 的 scoped 计数）：

1. **色令牌回归是静默的**。UiButton 试点第一版把 primary 写成 `--accent` 底 + 白字，
   实测十套主题只有 2.23–3.47:1（`--accent-strong` 是 4.60–4.71:1）—— 视觉上“像个灰按钮”，
   不报错、截图也不觉得坏。这里量真实 `background-color`，与 `--accent-strong` 对得上才算过。
2. **高度不齐也是静默的**。同一行里按钮 28px、输入框 30px，几何自检（只看有没有出屏）抓不到，
   但第一眼就能看出“没对齐”。这里逐个量 bounding box。
3. **图表的 SVG 属性写 var() 有可能失效**。`fill="var(--muted)"` 在属性里合法，
   但被 Tailwind 的 `[fill:var(--muted)]` 覆盖之后是否真的生效，只能读 computed style。

用法：先 `npm run build`，再 `python tests/e2e/run_all.py --only panels`
（run_all 会自己起静态站与 mock，端口自动避让）。
"""
import json
import os
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_ux_verify import PRELOAD_SHIM_JS, MOCK_API, settle_disclaimer, shot  # noqa: E402

SITE = os.environ.get("NF_E2E_SITE") or "http://127.0.0.1:8091"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def _bg(loc):
    return loc.evaluate("el => getComputedStyle(el).backgroundColor")


def _box(loc):
    return loc.bounding_box()


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 940})
        ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
        ctx.add_init_script(PRELOAD_SHIM_JS)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        settle_disclaimer(page, require_first_run=False)

        # ---------------------------------------------------------------- 指南针
        print("\n[A] 指南针（CompassPanel · 大纲页）")
        page.locator(".tabs button:has-text('大纲')").first.click()
        page.wait_for_timeout(1500)
        cmp_panel = page.locator("div.sep-dashed", has_text="指南针").first
        cmp_panel.wait_for(state="visible", timeout=8000)
        txt = cmp_panel.inner_text()
        check("A1 面板标题与说明可见", "指南针" in txt and "终局方向" in txt)
        check("A2 终局方向是真数据（不是占位符）",
              "流散到世间各处去" in cmp_panel.locator("textarea").first.input_value())
        check("A3 活跃长线两条都在（含“埋于第N章”后缀）",
              "信任裂痕" in cmp_panel.locator("textarea").nth(1).input_value()
              and "埋于第2章" in cmp_panel.locator("textarea").nth(1).input_value())
        check("A4 跨卷未更新提示条渲染", "指南针未更新" in txt, txt[:200])

        # 保存按钮 = 这块唯一要点的动作 → primary，底色必须是 --accent-strong 而不是 --accent
        cmp_save = cmp_panel.get_by_role("button", name="保存").first
        save_bg = _bg(cmp_save)
        strong = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--accent-strong').trim()")
        check("A5 保存按钮底色 = --accent-strong（不是 --accent）",
              save_bg not in ("rgba(0, 0, 0, 0)", "transparent") and strong != "",
              "btn=%s strong=%s" % (save_bg, strong))
        accent = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()")

        def css_rgb(hex_str):
            """#rrggbb / #rgb → 'rgb(r, g, b)'，与 getComputedStyle 的返回值同形。

            为什么不在浏览器里算：`page.evaluate("(hex) => …")` 这种**字符串形式的箭头函数**
            在 Playwright 里不会被当成函数调用，返回的是字符串本身 → 后面 `hex_to_rgb(x)`
            直接 TypeError（本轮踩过）。令牌就是十六进制，在 Python 里换算完全确定，不需要往返。
            """
            h = str(hex_str).strip().lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            return "rgb(%d, %d, %d)" % (r, g, b)

        accent_rgb = css_rgb(accent)
        strong_rgb = css_rgb(strong)
        check("A6 按钮底色不等于 --accent（那是低对比度的老写法）",
              save_bg != accent_rgb,
              "btn=%s accent=%s" % (save_bg, accent_rgb))
        check("A7 按钮底色就是 --accent-strong 的真值",
              save_bg == strong_rgb, "btn=%s strong=%s" % (save_bg, strong_rgb))

        # 几何：按钮必须与输入框同高。`.mini` 就是 30px，迁移时**不改高度** ——
        # 第一版这里写成 `30±2px`，结果 size="sm"(28px) 的退化被判成通过，
        # 正是作者点名过的“同一行里三个按钮差 2px”。现在按 30±0.5 硬判。
        ta_h = _box(cmp_panel.locator("textarea").first)["height"]
        save_h = _box(cmp_save)["height"]
        check("A8 保存按钮高 30px（与输入框同档，不再有 2px 落差）",
              abs(save_h - 30) <= 0.5, "btn=%s textarea_h=%s" % (save_h, ta_h))
        num = cmp_panel.locator("input[type=number]").first
        num_w = _box(num)["width"]
        num_h = _box(num)["height"]
        check("A9 数字输入宽度 90px（没有走满整行）", abs(num_w - 90) <= 1, num_w)
        check("A10 数字输入高 30px（与按钮同一档）", abs(num_h - 30) <= 0.5, num_h)
        p_cmp = shot(page, "panels_compass")
        cmp_panel.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        page.screenshot(path=str(Path(p_cmp).parent / "panels_compass_panel.png"))

        # ---------------------------------------------------------------- 质量趋势
        print("\n[B] 质量趋势（QualityTrend · 章节页列表视图）")
        page.locator("nav.tabs button", has_text="章节").first.click()
        page.wait_for_timeout(900)
        back = page.locator("button:has-text('回到列表')")
        if back.count():
            back.first.click()
            page.wait_for_timeout(1200)
        qt = page.locator("div.rounded-\\[var\\(--r-lg\\)\\]", has_text="章节质量趋势").first
        qt.wait_for(state="visible", timeout=8000)
        qt_txt = qt.inner_text()
        check("B1 标题与均分可见", "章节质量趋势" in qt_txt and "平均" in qt_txt, qt_txt[:120])
        check("B2 低于阈值那一行渲染（含章号徽标）",
              "低于阈值" in qt_txt and "章" in qt_txt, qt_txt[-160:])
        circles = qt.locator("svg circle")
        check("B3 散点数量 = 有评分的章数（12）", circles.count() == 12, circles.count())
        # 散点描边是卡片色（视觉分隔），不是 --border
        stroke = circles.first.get_attribute("stroke")
        check("B4 散点描边走 --card（相邻点不糊在一起）",
              stroke == "var(--card)", stroke)
        # 章号文字的颜色必须真的落到 --muted 上（属性写 var() 容易被工具类盖掉）
        label_fill = qt.locator("svg text").first.evaluate(
            "el => getComputedStyle(el).fill")
        muted = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--muted').trim()")
        check("B5 图内章号字色 = --muted（fill 没被工具类盖掉）",
              label_fill == css_rgb(muted), "fill=%s muted=%s" % (label_fill, css_rgb(muted)))
        # “去审稿 / 去精修”两颗按钮同高，且与输入框同档（30px，不是 sm 的 28px）
        b1 = qt.get_by_role("button", name="去审稿").first
        b2 = qt.get_by_role("button", name="去精修").first
        h1, h2 = _box(b1)["height"], _box(b2)["height"]
        check("B6 去审稿/去精修同高且为 30px（.mini 那一档）",
              abs(h1 - h2) <= 0.5 and abs(h1 - 30) <= 0.5, "%s vs %s" % (h1, h2))
        shot(page, "panels_quality_trend")

        # ---------------------------------------------------------------- 交接稿
        print("\n[C] 交接稿（HandoffPanel · 章节页列表视图）")
        hop = page.locator("div.sep-dashed", has_text="交接稿").first
        hop.wait_for(state="visible", timeout=8000)
        check("C1 标题与说明可见",
              "交接稿" in hop.inner_text() and "零 token" in hop.inner_text())
        check("C2 未导出时明说“还没导出过”（不是空白）",
              "还没导出过" in hop.inner_text(), hop.inner_text()[:160])
        # 未导出 → 复制/打开文件必须禁用（空点一次会拿到 undefined）
        check("C3 未导出时“复制全文”禁用",
              hop.get_by_role("button", name="复制全文").first.is_disabled() is True)
        check("C4 未导出时“打开文件”禁用",
              hop.get_by_role("button", name="打开文件").first.is_disabled() is True)

        exp = hop.get_by_role("button", name="导出交接稿").first
        exp_bg = _bg(exp)
        check("C5 导出按钮是 primary（底色 = --accent-strong）",
              exp_bg == strong_rgb, "btn=%s strong=%s" % (exp_bg, strong_rgb))
        exp_h = _box(exp)["height"]
        check("C5b 导出按钮高 30px（与 .mini 同档）", abs(exp_h - 30) <= 0.5, exp_h)
        exp.click()
        page.wait_for_timeout(1500)
        hop_txt = hop.inner_text()
        check("C6 导出后显示“已写到第 N 章 · 交接给第 N+1 章”",
              "已写到第" in hop_txt and "交接给" in hop_txt, hop_txt[:200])
        check("C7 缺资料提示条渲染（problems 非空时不吞掉）",
              "缺资料" in hop_txt and "滚动摘要" in hop_txt, hop_txt[:260])
        pre = hop.locator("pre").first
        check("C8 预览块是等宽字（font-family 含 mono）",
              "mono" in pre.evaluate("el => getComputedStyle(el).fontFamily").lower(),
              pre.evaluate("el => getComputedStyle(el).fontFamily")[:80])
        check("C9 预览正文可见（第 8 章 / 下一章大纲）",
              "第 8 章" in pre.inner_text() and "下一章大纲" in pre.inner_text(),
              pre.inner_text()[:120])
        check("C10 导出后“复制全文”不再禁用",
              hop.get_by_role("button", name="复制全文").first.is_disabled() is False)
        shot(page, "panels_handoff")

        browser.close()

    check("无 console 报错（页面级异常）", not errors, errors[:3])
    print("\n截图目录：Temp/gui_verify/ux/panels_*.png")
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
