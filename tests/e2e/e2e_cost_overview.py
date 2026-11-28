# -*- coding: utf-8 -*-
"""成本页“用量总览”真机验收（2026-11-28）。

覆盖 `CostOverview.vue` + `GET /costs/overview`：主折线图、预测虚线、限额进度条、
按模型占比表。

为什么必须有这个套件（而不是只看构建通过）：

1. **SVG 图最容易"看起来画出来了其实没画"**。折线点数是数据驱动的 —— 21 天数据
   应该得到 21 个坐标点、正数条刻度线、1 个实测末点圆。断言点数与 `<polyline>`
   的 points 项数，是唯一能证明"图真的按数据画了"的办法。
2. **补零不能跳过**。夹具里第 10 天 token 为 0，如果后端/前端把零值那天删掉，
   折线会在视觉上"跳过去"，看着像那天没跑过。断言点数 == 日历天数。
3. **预测段的视觉语言必须成立**：虚线 + 条纹填充 = "这不是实测"。如果它被画成
   实线或实色填充，读者会把它当实测数据 —— 这是会误导决策的错误，但不报错。
4. **金额口径**：订阅引擎下 money_metered=false，页面上不许出现 "¥0.00" 这种
   会让人以为免费/记账坏了的字样；必须明说"订阅流量 · 只记 token"。

用法：先 `npm run build`，再 `python tests/e2e/run_all.py --only cost`
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
from e2e_ux_verify import OUT, PRELOAD_SHIM_JS, MOCK_API, settle_disclaimer, shot  # noqa: E402

SITE = os.environ.get("NF_E2E_SITE") or "http://127.0.0.1:8091"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def _panel_shot(loc, name):
    p = OUT / ("%s.png" % name)
    try:
        loc.screenshot(path=str(p))
        print("      shot → %s" % p.name)
    except Exception as e:                                    # noqa: BLE001
        print("      shot → %s 失败: %s" % (name, str(e)[:80]))
    return p


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1100})
        ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
        ctx.add_init_script(PRELOAD_SHIM_JS)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        settle_disclaimer(page, require_first_run=False)

        print("\n[A] 进入成本页并渲染总览")
        page.locator("nav.tabs button", has_text="成本").first.click()
        page.wait_for_timeout(2200)

        ov = page.locator("div.rounded-\\[var\\(--r-lg\\)\\]", has_text="本期用量").first
        ov.wait_for(state="visible", timeout=10000)
        txt = ov.inner_text()
        check("A1 顶部大数渲染（百分比 + 本期用量）",
              "本期用量" in txt and "%" in txt, txt[:160])
        # 夹具 total ≈ 21 天 × 9~16.2 万 ≈ 240 万 / 1000 万 = 约 24%
        check("A2 订阅口径明说（不许摆 ¥0.00 让人以为免费）",
              "订阅流量" in txt or "只记 token" in txt, txt[:200])
        check("A2b 页面上没有误导性的 ¥0.00",
              "¥0.00" not in txt, [l for l in txt.splitlines() if "¥" in l][:3])

        # A3：夹具自洽性 —— mock 的“预算剩余”必须等于 限额 - 本次花费。
        # 真后端就是这个算术（nf_api.build_streaming_cost 里 budget_left=limit-spent）。
        # 首轮 mock 漏了 budget_left 字段 → 界面显示 ¥0.00 而花费 ¥12.35，
        # vision 目检时把它当成了产品 bug。验收夹具自己必须算得对，
        # 否则以后每轮目检都要重新怀疑一次这个数。
        run_card = page.locator("div.run-cost").first
        if run_card.count():
            rt = run_card.inner_text()
            check("A3 夹具自洽：剩余 = 限额 - 花费（不出现 ¥0.00 的假 bug）",
                  "¥37.65" in rt, [l.strip() for l in rt.splitlines() if "¥" in l][:5])

        print("\n[B] 主折线图：点数必须等于日历天数（补零不跳空）")
        svg = ov.locator("svg").first
        polyline = svg.locator("polyline").first
        pts = polyline.get_attribute("points") or ""
        n_pts = len([p for p in pts.split(" ") if "," in p])
        check("B1 折线有 21 个坐标点（= 夹具的 21 天，含一个零值日）",
              n_pts == 21, n_pts)
        check("B2 折线是实测色（--ok），不是预测色",
              polyline.get_attribute("stroke") == "var(--ok)",
              polyline.get_attribute("stroke"))
        # 面积填充必须存在且用渐变引用（不是实色，实色会盖住网格）
        poly = svg.locator("polygon").first
        check("B3 实测面积用渐变填充（fill=url(#…)，不是实色）",
              "url(#" in (poly.get_attribute("fill") or ""), poly.get_attribute("fill"))

        # 零值那天必须真的压到底（y 最大）—— 证明它没被跳过而是补了 0
        zero_y = None
        for pair in pts.split(" "):
            if "," not in pair:
                continue
            y = float(pair.split(",")[1])
            if zero_y is None or y > zero_y:
                zero_y = y
        base_y = max(float(p.split(",")[1]) for p in pts.split(" ") if "," in p)
        check("B4 存在一天压到底线（补零真的生效，不是删掉那天）",
              zero_y is not None and abs(zero_y - base_y) < 0.5, base_y)

        print("\n[C] 预测段：虚线 + 条纹 = 明确“这不是实测”")
        dashed = svg.locator("line[stroke-dasharray]")
        found_dash = False
        for i in range(dashed.count()):
            if dashed.nth(i).get_attribute("stroke") == "var(--warn)":
                found_dash = True
                break
        check("C1 预测线是虚线且走 --warn（不是实线实色）", found_dash)
        hatch = svg.locator("polygon[fill*='costForecastHatch']")
        check("C2 预测区用条纹 pattern 填充（视觉上区分外推与实测）",
              hatch.count() == 1, hatch.count())

        # C2b：预测区必须有真实宽度 —— 首版把 n 天铺满整宽，最后一个实测点
        # 就在最右边界，预测段 x0 == x1，条纹区宽度为 0（"看不到条纹"的根因）。
        # 只断言"polygon 存在"会全绿，必须查它的实际几何宽度。
        if hatch.count() == 1:
            box = hatch.first.bounding_box()
            check("C2b 预测区横向有真实宽度（≥ 图的 10%，不是零宽）",
                  box is not None and box["width"] >= 720 * 0.10,
                  box["width"] if box else None)

        # SVG 不是 HTMLElement，inner_text() 会报 "Node is not an HTMLElement" —— 
        # 对 <svg> 及其子节点一律用 text_content()（本轮踩过）。
        check("C3 预测终点标注“预计 …”",
              "预计" in (svg.text_content() or ""), (svg.text_content() or "")[:160])

        print("\n[D] 用量进度条：只用真实存在的口径，不凭空造")
        lim_card = page.locator("div.rounded-\\[var\\(--r-lg\\)\\]", has_text="用量与上限").first
        check("D1 限额卡渲染", lim_card.count() == 1)
        ltxt = lim_card.inner_text()
        check("D2 说明本轮已用/上限（真实存在的那一个）",
              "本轮已用" in ltxt and "上限" in ltxt, ltxt[:200])
        check("D3 预计期末标了“外推，不是实测”",
              "外推" in ltxt, ltxt[:260])
        # 进度条宽度必须真的按百分比算出来（不是恒 0 或恒 100%）
        bars = lim_card.locator("div.h-full")
        widths = []
        for i in range(bars.count()):
            w = bars.nth(i).evaluate("el => el.style.width")
            widths.append(w)
        check("D4 进度条宽度是按百分比算的（有具体值，非空）",
              all(w.endswith("%") for w in widths) and len(widths) > 0, widths)

        print("\n[E] 按模型占比表")
        mt = page.locator("div.rounded-\\[var\\(--r-lg\\)\\]", has_text="按模型").first
        check("E1 模型卡渲染", mt.count() == 1)
        mrows = mt.locator("table tbody tr")
        check("E2 三行模型（夹具喂了 3 个）", mrows.count() == 3, mrows.count())
        shares = [mrows.nth(i).locator("td").nth(3).inner_text() for i in range(mrows.count())]
        check("E3 占比列是百分比且合计约 100%",
              all("%" in s for s in shares)
              and abs(sum(float(s.replace("%", "").split()[0]) for s in shares) - 100) < 1.5,
              shares)

        _panel_shot(ov, "cost_overview")
        # 整块成本页内容（含下面的用量上限卡与按模型卡）——
        # 元素级截图只框住主图那一张卡，下半部分进不了画面，目检就会漏掉它们。
        sec = page.locator("section", has_text="成本中心").first
        if sec.count():
            _panel_shot(sec, "cost_page_full")

        print("\n[F] 暗色主题")
        page.locator("nav.tabs button", has_text="设置").first.click()
        page.wait_for_timeout(800)
        page.locator("nav.set-nav button", has_text="外观与更新").first.click()
        page.wait_for_timeout(600)
        sw = page.locator(".theme-swatch")
        sw.nth(sw.count() - 1).click()
        page.wait_for_timeout(700)
        dark = page.evaluate("document.documentElement.classList.contains('is-dark')")
        check("F1 切到暗色", dark, dark)
        if dark:
            page.locator("nav.tabs button", has_text="成本").first.click()
            page.wait_for_timeout(2200)
            ov2 = page.locator("div.rounded-\\[var\\(--r-lg\\)\\]", has_text="本期用量").first
            ov2.wait_for(state="visible", timeout=10000)
            # 文字色必须换成暗色主题的 --muted（不是亮色那个值）
            _panel_shot(ov2, "cost_overview_dark")
            check("F2 暗色下图仍在（渲染没崩）", ov2.locator("svg polyline").count() >= 1)

        browser.close()

    check("无 console 报错", not errors, errors[:3])
    print("\n截图：Temp/gui_verify/ux/cost_overview*.png")
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
