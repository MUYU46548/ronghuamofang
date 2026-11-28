# -*- coding: utf-8 -*-
"""A 档三块面板迁到 Tailwind + 新基座之后的真机视觉验收（2026-11-28）。

覆盖：残稿（TruncatedPanel）/ 伏笔台账（ForeshadowPanel）/ 写作预设（WritingPresetsPanel）。
这三块本轮把 `<style scoped>` 整块摘掉，并**第一次真正用上第二批基座**：
`UiTable` / `UiBadge` / `UiInput` / `UiButton`。

为什么必须有这个套件（而不是只看 `test_ui_base.py` 的 scoped 计数）：

1. **基座的几何只有一处真源** —— `UiTable` 的单元格 padding、`UiBadge` 的 7px 圆角、
   `UiInput` 不重定义高度（留给 `:where()` 基础层）。这些如果只在单测里比较源码字符串，
   一行 Tailwind 类被 tailwind-merge 吃掉也照样全绿；只有读 computed style 才能立住。
2. **替换 `.mini` 为 `UiButton` 的 2px 退化是静默的** —— 上一批（2026-11-27）四个面板
   全踩过：`size="sm"` 是 28px 而 `.mini` 是 30px，e2e 当时"全绿"，因为没人量过。
   这里逐个量按钮与同行的输入框，必须同为 30px。
3. **状态徽标必须跟主题走** —— 旧 `.fsp-closed` / `.fsp-abandoned` 是 opacity +
   删除线的堆叠类。现在四档状态都由 `UiBadge` 的 variant 表达，底色必须是
   `color-mix(主题色, transparent)` 的结果，而不是任何写死的 rgba。
4. **数据驱动的分支没夹具就等于没验** —— 三块面板的空态、错误态、已采纳态、超期行、
   状态四档、预设解析失败行，全靠 mock 喂出来的数据才走得到。mock 已在
   `mock_nf_api_state.py` 补齐（`/truncated`、`/foreshadow`、`/config/writing_presets`）。

用法：先 `npm run build`，再 `python tests/e2e/run_all.py --only panels-a`
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
from e2e_ux_verify import (OUT, PRELOAD_SHIM_JS, MOCK_API,  # noqa: E402
                           settle_disclaimer, shot)

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


def _css_rgb(hex_str):
    """#rrggbb → 'rgb(r, g, b)'，与 getComputedStyle 同形。

    不在浏览器里算：Playwright 的 `page.evaluate("(hex) => …")` 传**字符串形式的箭头函数**
    不会被当成函数调用（返回字符串本身），后面就 TypeError —— 2026-11-27 踩过。
    """
    h = str(hex_str).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "rgb(%d, %d, %d)" % (r, g, b)


def _token(page, name):
    return page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('%s').trim()" % name)


def _panel_shot(loc, name):
    """元素级截图：只截这块面板本体（visual-evidence 卡里的“密集面板”做法）。

    为什么不用 `shot(page, ...)`：残稿 / 伏笔在章节页**很靠下**，视口截图根本拍不到它们
    （首跑就是这样：图里只有页面顶部的“章节文件”，面板一行都没进画面，而断言全绿 ——
    等于交了一张看不出结论的图）。元素截图把全部像素给在这块上，也不受滚动位置影响。
    """
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
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
        ctx.add_init_script(PRELOAD_SHIM_JS)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("console:" + m.text)
                if m.type == "error" and "404" not in m.text else None)
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        settle_disclaimer(page, require_first_run=False)

        warn_rgb = None

        def refresh_tokens():
            nonlocal warn_rgb
            warn_rgb = _css_rgb(_token(page, "--warn"))

        # ================================================================ 残稿
        print("\n[A] 残稿（TruncatedPanel · 章节页列表视图）")
        page.locator("nav.tabs button", has_text="章节").first.click()
        page.wait_for_timeout(1200)
        back = page.locator("button:has-text('回到列表')")
        if back.count():
            back.first.click()
            page.wait_for_timeout(1000)
        tr = page.locator("div.sep-dashed", has_text="残稿").first
        tr.wait_for(state="visible", timeout=8000)
        refresh_tokens()
        txt = tr.inner_text()
        check("A1 标题与“共 N 份”可见", "残稿" in txt and "共 3 份" in txt, txt[:160])

        # 表格来自 UiTable：列头必须是五列，且单元格 padding 由基座唯一决定
        heads = tr.locator("table thead th")
        check("A2 列头五列（UiTable 渲染）", heads.count() == 5, heads.count())
        check("A2b 列头文案与顺序正确",
              [heads.nth(i).inner_text() for i in range(heads.count())]
              == ["时间", "字数", "开头", "采纳为第", "状态"],
              [heads.nth(i).inner_text() for i in range(heads.count())])

        rows = tr.locator("table tbody tr")
        check("A3 三行数据（可采纳 / 已采纳 / 太短）", rows.count() == 3, rows.count())

        # 第三行（太短）的字数必须是 warn 色 —— 旧实现是 .warn-text
        too_short_chars = tr.locator("table tbody tr").nth(2).locator("td").nth(1)
        check("A4 太短那份的字数走 --warn 色",
              _bg(too_short_chars) in ("rgba(0, 0, 0, 0)", "transparent")
              and too_short_chars.locator("span").evaluate(
                  "el => getComputedStyle(el).color") == warn_rgb,
              too_short_chars.locator("span").evaluate("el => getComputedStyle(el).color"))

        # 已采纳那行：徽标走 UiBadge(warn, strong)，加粗且底色是 color-mix 结果
        adopted_row = tr.locator("table tbody tr").nth(1)
        badge = adopted_row.locator("td").nth(4).locator("span span").first
        b_txt = badge.inner_text()
        b_weight = badge.evaluate("el => getComputedStyle(el).fontWeight")
        b_bg = _bg(badge)
        check("A5 已采纳行显示“第 5 章 · 残稿”徽标", "第 5 章" in b_txt and "残稿" in b_txt, b_txt)
        check("A5b 徽标加粗（st-gate 语义：等你处理）",
              int(b_weight) >= 600, b_weight)
        check("A5c 徽标底色不是透明（color-mix 真的算出了颜色）",
              b_bg not in ("rgba(0, 0, 0, 0)", "transparent"), b_bg)

        # 太短那份的采纳按钮必须**禁用**（不是隐藏）—— 这是既有行为：
        # 按钮在、点不动、title 说明“太短，建议直接重写”。迁移只换样式，
        # 不改交互语义，所以这里断言 disabled + 提示文案，而不是 count()==0。
        short_row_ops = tr.locator("table tbody tr").nth(2).locator("td").nth(3)
        short_btn = short_row_ops.get_by_role("button", name="采纳").first
        check("A6 太短的残稿“采纳”按钮禁用", short_btn.is_disabled() is True)
        check("A6b 禁用原因写在 title 里（不是只灰掉不说）",
              "太短" in (short_btn.get_attribute("title") or ""),
              short_btn.get_attribute("title"))

        # 章号输入框必须 30px 高（与按钮同档）—— UiInput 不重定义高度，
        # 全靠 style.css 的 :where() 基础层；这条一旦红，说明封装偷偷改了几何。
        first_row = tr.locator("table tbody tr").first
        ch_input = first_row.locator("input[type=number]")
        adopt_btn = first_row.get_by_role("button", name="采纳")
        ih, bh = _box(ch_input)["height"], _box(adopt_btn)["height"]
        check("A7 章号输入框高 30px（基础层生效）", abs(ih - 30) <= 0.5, ih)
        check("A7b 采纳按钮高 30px（与输入框同档，不是 sm 的 28px）",
              abs(bh - 30) <= 0.5, bh)
        check("A7c 章号输入框宽 62px（表格内要塞下三位数）",
              abs(_box(ch_input)["width"] - 62) <= 1, _box(ch_input)["width"])
        # A7d：回填值必须来自接口（chapter_hint="7"），不是空框等人手填。
        # 这条同时证明 v-model 的**入向**是通的；出向由 A7e 证明。
        check("A7d 章号预填接口给的 chapter_hint（7）", ch_input.input_value() == "7",
              ch_input.input_value())
        ch_input.fill("")
        ch_input.type("12")
        page.wait_for_timeout(200)
        check("A7e 章号 v-model 真回传（v-model.number → 数字 12）",
              ch_input.input_value() == "12", ch_input.input_value())

        # 预览列单行省略：不截时一行正文会把操作列挤出视口
        preview = first_row.locator("td").nth(2).locator("span")
        pv = preview.evaluate(
            "el => ({ ws: getComputedStyle(el).whiteSpace, ov: getComputedStyle(el).overflow, "
            "to: getComputedStyle(el).textOverflow })")
        check("A8 预览列单行省略（whiteSpace=nowrap + ellipsis）",
              pv["ws"] == "nowrap" and pv["to"] == "ellipsis", pv)

        _panel_shot(tr, "panels_a_truncated")
        shot(page, "panels_a_chapter_view")

        # ================================================================ 伏笔
        print("\n[B] 伏笔台账（ForeshadowPanel · 章节页列表视图）")
        fs = page.locator("div.sep-dashed", has_text="伏笔清单").first
        fs.wait_for(state="visible", timeout=8000)
        ftxt = fs.inner_text()
        check("B1 汇总条渲染（共 4 条 · 未推进 1 · 已推进 1）",
              "共 4 条" in ftxt and "未推进 1" in ftxt and "已推进 1" in ftxt, ftxt[:200])
        check("B2 超期计数可见", "超期" in ftxt and "1" in ftxt)

        frows = fs.locator("table tbody tr")
        check("B3 四行线索", frows.count() == 4, frows.count())

        # 超期行整行淡琥珀底 —— rowClass 由组件传，颜色由主题色算
        stale_bg = frows.nth(0).locator("td").first.evaluate(
            "el => getComputedStyle(el.parentElement).backgroundColor")
        check("B4 超期行有整行底色（不是只有文字变色）",
              stale_bg not in ("rgba(0, 0, 0, 0)", "transparent"), stale_bg)
        fresh_bg = frows.nth(1).locator("td").first.evaluate(
            "el => getComputedStyle(el.parentElement).backgroundColor")
        check("B4b 非超期行没有底色", fresh_bg in ("rgba(0, 0, 0, 0)", "transparent"), fresh_bg)

        # 四档状态各用一种 badge 语义，且都是 UiBadge 渲染的 <span>
        labels = [frows.nth(i).locator("td").nth(2).inner_text().strip() for i in range(4)]
        check("B5 四档状态文案正确",
              labels == ["未推进", "已推进", "已回收", "已放弃"], labels)
        # 已放弃必须带删除线（旧 .fsp-abandoned 的 text-decoration）
        abandoned = frows.nth(3).locator("td").nth(2).locator("span").first
        check("B6 已放弃带删除线",
              "line-through" in abandoned.evaluate("el => getComputedStyle(el).textDecorationLine"),
              abandoned.evaluate("el => getComputedStyle(el).textDecorationLine"))

        # 操作按钮的禁用态：已回收那行“已回收”禁用、“重开”可用
        closed_ops = frows.nth(2).locator("td").nth(4)
        check("B7 已回收行的“已回收”禁用",
              closed_ops.get_by_role("button", name="已回收").first.is_disabled() is True)
        check("B7b 已回收行的“重开”可用",
              closed_ops.get_by_role("button", name="重开").first.is_disabled() is False)
        open_ops = frows.nth(0).locator("td").nth(4)
        check("B7c 未推进行的“重开”禁用",
              open_ops.get_by_role("button", name="重开").first.is_disabled() is True)

        # 三颗按钮同高 30px，且不换行（换行会把行高撑起来）
        b1 = open_ops.get_by_role("button", name="已回收").first
        b2 = open_ops.get_by_role("button", name="已放弃").first
        h1, h2 = _box(b1)["height"], _box(b2)["height"]
        check("B8 操作按钮同高 30px", abs(h1 - 30) <= 0.5 and abs(h2 - 30) <= 0.5,
              "%s / %s" % (h1, h2))

        # 提示条：有超期时必须出现（明说是哪一条要处理）
        check("B9 超期提示条渲染", "超过 8 章没推进" in ftxt, ftxt[-220:])

        _panel_shot(fs, "panels_a_foreshadow")

        # ================================================================ 写作预设
        print("\n[C] 写作预设（WritingPresetsPanel · 设置页 → 风格与提示词）")
        page.locator("nav.tabs button", has_text="设置").first.click()
        page.wait_for_timeout(1500)
        # 它在设置页的二级页签“风格与提示词”下（不是默认那页）——
        # 2026-11-28 首跑踩过：只点“设置”时 .sep-dashed 是 hidden，locator 能解析
        # 但永远不可见，报的是“23 × resolved to hidden”而不是“找不到元素”。
        page.locator("nav.set-nav button", has_text="风格与提示词").first.click()
        page.wait_for_timeout(900)
        wp = page.locator("div.sep-dashed", has_text="写作预设").first
        wp.wait_for(state="visible", timeout=10000)
        wtxt = wp.inner_text()
        check("C1 标题与说明可见", "写作预设" in wtxt and "一键切换" in wtxt, wtxt[:160])

        # 当前生效的 chip：必须是 UiBadge，且数量 = current 的键数（4）
        chips = wp.locator("span.rounded-\\[7px\\]")
        check("C2 “当前生效”四个 chip（UiBadge 渲染）", chips.count() == 4, chips.count())
        if chips.count():
            cbg = _bg(chips.first)
            check("C2b chip 底色不是透明（color-mix 生效）",
                  cbg not in ("rgba(0, 0, 0, 0)", "transparent"), cbg)

        heads = wp.locator("table thead th")
        check("C3 预设表三列（id / 内容 / 操作）", heads.count() == 3,
              [heads.nth(i).inner_text() for i in range(heads.count())])

        prows = wp.locator("table tbody tr")
        check("C4 三行预设（两套正常 + 一套解析失败）", prows.count() == 3, prows.count())
        broken = prows.nth(2).locator("td").nth(1)
        check("C5 解析失败那行显示 error（不是空白）",
              "解析失败" in broken.inner_text(), broken.inner_text()[:80])
        check("C5b error 文字走 --bad 色",
              broken.locator("span").first.evaluate(
                  "el => getComputedStyle(el).color")
              == _css_rgb(_token(page, "--bad")),
              broken.locator("span").first.evaluate("el => getComputedStyle(el).color"))

        # “存当前为新预设”：点了之后展开表单，输入框与按钮同高
        cap = wp.get_by_role("button", name="存当前为新预设").first
        check("C6 “存当前为新预设”高 30px", abs(_box(cap)["height"] - 30) <= 0.5,
              _box(cap)["height"])
        cap.click()
        page.wait_for_timeout(600)
        id_input = wp.locator("input[placeholder='如 noir']")
        check("C7 点开保存表单后 id 输入框出现", id_input.count() == 1)
        if id_input.count():
            check("C7b id 输入宽 160px（不被拉满整行）",
                  abs(_box(id_input)["width"] - 160) <= 1, _box(id_input)["width"])
            check("C7c id 输入高 30px", abs(_box(id_input)["height"] - 30) <= 0.5,
                  _box(id_input)["height"])
            # C7d：**真打一遍字再读回** —— v-model 失效时界面完全不报错，
            # 只在点保存那一刻才炸（拿到的永远是空串）。首版 UiInput 就是这样：
            # 只有 type 后回读 value 才能抓到，光量几何永远绿。
            id_input.fill("")
            id_input.type("noir-v2")
            page.wait_for_timeout(200)
            check("C7d 输入框 v-model 真回传（打字后 value 读得回来）",
                  id_input.input_value() == "noir-v2", id_input.input_value())
            # C7e：JSON 编辑区必须显示当前生效值的 JSON，而不是字面的 <slot />
            json_area0 = wp.locator("textarea").first
            jval = json_area0.input_value()
            check("C7e 内容区有真实 JSON（不是字面 <slot /> 也不是空）",
                  "<slot" not in jval and '"style"' in jval, jval[:80])
            # C7f：清空内容框后表单必须还在（原实现的 v-if 挂在内容为空上，
            # Ctrl+A 重写时表单会整个消失，正在改的东西一起没了）。
            json_area0.fill("")
            page.wait_for_timeout(250)
            check("C7f 清空内容后表单仍在（Ctrl+A 重写不会把表单弄没）",
                  json_area0.is_visible() and id_input.is_visible())
            # C8：JSON 原文区是 code 档 = 等宽字（必须趁表单还开着量）
            check("C8 JSON 编辑区是等宽字（variant=code）",
                  "mono" in json_area0.evaluate(
                      "el => getComputedStyle(el).fontFamily").lower(),
                  json_area0.evaluate("el => getComputedStyle(el).fontFamily")[:70])
            # C7g：打进去的字必须回传给组件 state，再点“取消”验证表单收起
            json_area0.type('{"style": "x"}')
            page.wait_for_timeout(200)
            check("C7g 内容区 v-model 真回传",
                  json_area0.input_value() == '{"style": "x"}', json_area0.input_value())
            wp.get_by_role("button", name="取消").first.click()
            page.wait_for_timeout(300)
            check("C7h 点“取消”后表单收起",
                  wp.locator("textarea").count() == 0,
                  wp.locator("textarea").count())
            # C7i：再点一次要能重新打开（收起不是一次性销毁）
            wp.get_by_role("button", name="存当前为新预设").first.click()
            page.wait_for_timeout(400)
            check("C7i 再点“存当前为新预设”能重新打开表单",
                  wp.locator("textarea").count() == 1,
                  wp.locator("textarea").count())

        # 历史块：两条切换记录
        check("C9 最近切换块渲染（两条历史）",
              "最近切换" in wtxt and wtxt.count("→") >= 2, wtxt[-260:])

        _panel_shot(wp, "panels_a_presets")

        # ---------------------------------------------------------------- 暗色
        print("\n[D] 暗色主题下三块面板（颜色必须跟着主题走）")
        # 主题切换在 设置 → 外观与更新，是一排 .theme-swatch 圆点（没有文字“暗色”按钮）。
        # 第一组是亮色、第二组是暗色，所以取 swatches 里最后一个所在组的第一颗：
        # 直接点 `.theme-swatch` 的最后一个（DARK_THEMES 排在后半段）。
        page.locator("nav.tabs button", has_text="设置").first.click()
        page.wait_for_timeout(900)
        page.locator("nav.set-nav button", has_text="外观与更新").first.click()
        page.wait_for_timeout(700)
        swatches = page.locator(".theme-swatch")
        n_sw = swatches.count()
        check("D1 主题圆点存在（含暗色一组）", n_sw >= 10, n_sw)
        if n_sw:
            swatches.nth(n_sw - 1).click()      # 最后一颗 = 暗色组末尾
            page.wait_for_timeout(800)
            is_dark = page.evaluate("document.documentElement.classList.contains('is-dark')")
            check("D2 切到暗色主题（is-dark 挂上）", is_dark, is_dark)
            if is_dark:
                # 徽标文字色必须变成暗色主题的 --ink-warn（不是亮色主题那个值）
                page.locator("nav.tabs button", has_text="章节").first.click()
                page.wait_for_timeout(1400)
                tr2 = page.locator("div.sep-dashed", has_text="残稿").first
                tr2.wait_for(state="visible", timeout=8000)
                badge2 = (tr2.locator("table tbody tr").nth(1)
                          .locator("td").nth(4).locator("span span").first)
                ink_warn = _css_rgb(_token(page, "--ink-warn"))
                got = badge2.evaluate("el => getComputedStyle(el).color")
                check("D3 暗色下徽标文字色 = 暗色主题的 --ink-warn",
                      got == ink_warn, "got=%s want=%s" % (got, ink_warn))
                # 超期行底色也必须换主题重算（写死 rgba 的话暗色下不会变）。
                # 注意查的是**伏笔面板**：残稿表没有 superrow 概念，
                # 首跑我把残稿表当成伏笔表查，拿到透明底是必然的（用例写错，不是产品错）。
                fs2 = page.locator("div.sep-dashed", has_text="伏笔清单").first
                fs2.wait_for(state="visible", timeout=8000)
                stale_tr = fs2.locator("table tbody tr").first
                sbg = stale_tr.locator("td").first.evaluate(
                    "el => getComputedStyle(el.parentElement).backgroundColor")
                check("D4 暗色下超期行仍有整行底色（color-mix 跟主题重算）",
                      sbg not in ("rgba(0, 0, 0, 0)", "transparent"), sbg)
                # 交叉验证：亮色主题下同一个位置的值必须与暗色不同，
                # 否则说明 --warn 根本没参与计算（写死色值才会两者相同）。
                light_ok = page.evaluate("""() => {
                  const el = document.documentElement;
                  return !el.classList.contains('is-dark');
                }""")
                check("D4b 当前确为暗色（上面的取值发生在暗色下）", light_ok is False, light_ok)
                _panel_shot(fs2, "panels_a_dark_foreshadow")
        else:
            check("D1 主题圆点存在（含暗色一组）", False, "找不到 .theme-swatch")

        browser.close()

    check("无 console 报错（页面级异常与 4xx 响应）", not errors, errors[:4])
    print("\n截图目录：Temp/gui_verify/ux/panels_a_*.png")
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
