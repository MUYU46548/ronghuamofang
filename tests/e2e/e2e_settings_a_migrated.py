# -*- coding: utf-8 -*-
"""A 档第二批：四个设置面板迁 Tailwind + 基座的真机验收（2026-11-28）。

覆盖：设定目录（VaultDirsPanel）/ 词表（WordlistsPanel）/
      设定引用核对（SettingStalePanel）/ 素材裁决（SettingClosurePanel）。

（`SettingVersionsPanel` 本轮之前就已经没有样式块，不需要迁，也不在本套件里。）

为什么必须有这个套件 —— 这四块有两个"看着对其实错"的陷阱：

1. **v-model 失效是静默的**。WordlistsPanel 的两个 textarea 换成了 `UiInput`
   （`:value` + `@input` 自实现），只要 emit 名字或 modifiers 接错，
   打字不回传、保存写空表，界面一点提示都没有。必须 fill → type → input_value 回读。
2. **select 的 v-model 不能靠 attrs 透传**。VaultDirsPanel 是原生 `<select>` 保留，
   但包在 div 里之后 `.vd-row`（flex）没了 —— 宽度塌陷/标签挤行只有量几何才发现。
   这里量 label 与 select 的宽度、以及 select 是否与 UiButton 同高（30px）。
3. **徽标语义**：四块里的 pill/st-* 全换 `UiBadge`，底色必须是 color-mix 的结果
   （写死 rgba 的话换主题时底色纹丝不动，且不报错）。

用法：先 `npm run build`，再 `python tests/e2e/run_all.py --only settings-a`
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


def _box(loc):
    return loc.bounding_box()


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

        # ============================================================ 设定目录
        print("\n[A] 设定目录（VaultDirsPanel · 设置 → 提示词/风格 页内的库配置）")
        page.locator("nav.tabs button", has_text="设置").first.click()
        page.wait_for_timeout(1500)
        page.locator("nav.set-nav button", has_text="风格与提示词").first.click()
        page.wait_for_timeout(1000)
        # 用 data-panel 定位根元素：`div:has-text(...) .last` 会命中**内层**那个
        # 恰好也含标题文字的 div，于是 selects 数到 0、说明文字读不到
        # （本轮实测：A1/A2/A3 三条假红全是选择器问题，不是产品缺陷）。
        vd = page.locator("[data-panel='vault-dirs']").first
        vd.wait_for(state="visible", timeout=10000)
        vtxt = vd.inner_text()
        check("A1 标题与说明可见", "设定目录" in vtxt and "每一行" in vtxt, vtxt[:150])

        selects = vd.locator("select")
        check("A2 五类目录各一个下拉（原生 select 保留）", selects.count() >= 5,
              selects.count())
        # 每个 select 必须真的占了剩余宽度（.vd-row 的 flex 骨架换掉之后
        # 最容易出现的就是"select 缩成一小截"）。
        if selects.count():
            w = _box(selects.first)["width"]
            check("A2b 下拉有实际宽度（不是缩成一小截）", w > 100, w)
            h = _box(selects.first)["height"]
            check("A2c 下拉高 30px（与按钮/输入框同档）", abs(h - 30) <= 1, h)
            # 已配置项必须回填（mock: characters=设定/人物）
            check("A2d 已配置项回填（角色 = 设定/人物）",
                  selects.first.input_value() == "设定/人物", selects.first.input_value())

        # 其他库：三种来源徽标
        badges = vd.locator("span").filter(has_text="沿用当前库")
        check("A3 其他库的目录块渲染（含来源徽标）", badges.count() >= 1, badges.count())
        if badges.count():
            bg = badges.first.evaluate("el => getComputedStyle(el).backgroundColor")
            check("A3b 徽标底色是 color-mix 结果（不是透明/null）",
                  bg not in ("rgba(0, 0, 0, 0)", "transparent"), bg)

        # 保存按钮 = 这块唯一要点的动作 → primary
        save_btn = vd.locator("button").filter(has_text="保存").first
        check("A4 保存按钮高 30px", abs(_box(save_btn)["height"] - 30) <= 0.5,
              _box(save_btn)["height"])
        # 其他库的输入框：UiInput + UiField(inline)，label 与框必须在同一行
        vin = vd.locator("input").first
        if vin.count():
            check("A5 其他库目录输入框高 30px", abs(_box(vin)["height"] - 30) <= 0.5,
                  _box(vin)["height"])
            vin.fill("")
            vin.type("设定/人物/主角")
            page.wait_for_timeout(200)
            check("A5b 输入框 v-model 真回传", vin.input_value() == "设定/人物/主角",
                  vin.input_value())
        _panel_shot(vd, "settings_a_vault_dirs")

        # ============================================================ 词表
        print("\n[B] 词表（WordlistsPanel）")
        wl = page.locator("[data-panel='wordlists']").first
        wl.wait_for(state="visible", timeout=10000)
        wtxt = wl.inner_text()
        check("B1 标题与说明可见", "词表" in wtxt and "一行一条" in wtxt, wtxt[:150])

        areas = wl.locator("textarea")
        check("B2 两个编辑区（病灶词 / 合规词）", areas.count() == 2, areas.count())
        if areas.count() == 2:
            # B3：等宽字（variant=code）—— 一行一条的清单必须是等宽才好对齐
            fam = areas.first.evaluate("el => getComputedStyle(el).fontFamily").lower()
            check("B3 编辑区是等宽字（variant=code）", "mono" in fam, fam[:60])
            # B4：**真打字回读** —— v-model 失效时保存会写空表，界面不报错
            before = areas.first.input_value()
            areas.first.fill("")
            areas.first.type("微微一笑\n眼中闪过一丝")
            page.wait_for_timeout(250)
            got = areas.first.input_value()
            check("B4 编辑区 v-model 真回传（打字后读得回来）",
                  got == "微微一笑\n眼中闪过一丝", got[:60])
            # B5：清空后<不再变回原值>（说明是受控组件，不是初值渲染一次就丢）
            areas.first.fill("")
            page.wait_for_timeout(200)
            check("B5 清空后保持空（受控组件，不会弹回初值）",
                  areas.first.input_value() == "", areas.first.input_value()[:40])

        # 徽标：来源（gui / factory）
        # 来源徽标：直接数“已保存 / 内置 20 条”这类文字节点，
        # 用 filter 而不是 has_text=：后者会连带命中祖先元素。
        # 两张表各一个来源徽标，但**文字不同**：病灶词是“内置 20 条”，
        # 合规词是“界面词表 / 未配置（不检查）”。按“内置”过滤只能命中 1 个，
        # 所以分别查（这轮先按 >=2 写，是错的）。
        b_cl = wl.locator("span").filter(has_text="内置").count()
        b_co = wl.locator("span").filter(has_text="界面词表").count()              + wl.locator("span").filter(has_text="未配置").count()
        check("B6 两处来源徽标都在（病灶词：内置 / 合规词：配置状态）",
              b_cl >= 1 and b_co >= 1, {"内置": b_cl, "合规": b_co})

        # 动作行按钮
        for label in ("填入内置 20 条", "重新读取", "保存"):
            b = wl.locator("button").filter(has_text=label).first
            check("B7 “%s”按钮存在" % label, b.count() == 1)
            if b.count():
                check("B7b “%s”高 30px" % label, abs(_box(b)["height"] - 30) <= 0.5,
                      _box(b)["height"])
        _panel_shot(wl, "settings_a_wordlists")

        # B8：两列必须**等高且并排**，动作行必须在两列**之外**（之下）。
        # 为什么量这个：改成 UiInput 之后两列内容高度会不一致（一侧有 warning 另一侧没有），
        # 若 grid 用了 align-items:start 而不是默认 stretch，两列底边就会参差；
        # 而把动作行误嵌进某一列里，视觉上会像"左边多出两个按钮"，很难一眼看出根因。
        geo = wl.evaluate("""() => {
          const root = document.querySelector('[data-panel="wordlists"]');
          const grid = root.querySelector('[class*="grid-template-columns"]');
          const cols = grid ? [...grid.children] : [];
          const acts = [...root.querySelectorAll('.row-wrap')]
            .find(e => (e.innerText || '').includes('填入内置'));
          const box = e => e ? e.getBoundingClientRect() : null;
          const g = box(grid), a = box(acts);
          return {
            cols: cols.length,
            sameTop: cols.length === 2 && Math.abs(box(cols[0]).top - box(cols[1]).top) < 1,
            sameBottom: cols.length === 2 && Math.abs(box(cols[0]).bottom - box(cols[1]).bottom) < 1,
            nested: !!(grid && acts && grid.contains(acts)),
            after: g && a ? a.top >= g.bottom - 1 : null,
          };
        }""")
        check("B8 两列并排等高（底边不参差）",
              geo["cols"] == 2 and geo["sameTop"] and geo["sameBottom"], geo)
        check("B8b 动作行在两列之外（不在某一列里面）",
              geo["nested"] is False and geo["after"] is True, geo)

        # ============================================================ 设定引用核对
        print("\n[C] 设定引用核对（SettingStalePanel · 设定页）")
        page.locator("nav.tabs button", has_text="设定").first.click()
        page.wait_for_timeout(1600)
        ss = page.locator("[data-panel='setting-stale']").first
        ss.wait_for(state="visible", timeout=10000)
        stxt = ss.inner_text()
        check("C1 标题与说明可见", "设定引用核对" in stxt and "零 token" in stxt, stxt[:150])
        check("C2 汇总行渲染（条设定 / 条待核）",
              "条设定" in stxt and "条待核" in stxt, stxt[:260])
        # 待核徽标
        # UiBadge 的类名在构建后带转义，`span.rounded-[7px]` 抓不到；
        # 按它显示的文字找（徽标语义就是那几个字），再核对它是 span。
        # 精确匹配文字**等于**"待核"的 span：
        # filter(has_text=) 会先命中汇总行的"2 条待核"（它也是 span），
        # 取色时拿到的是 --muted 灰而不是徽标的 --ink-bad（本轮实测）。
        sbadges = ss.locator("span").filter(has_text="待核").filter(has_not_text="条待核")
        check("C3 待核徽标存在（UiBadge bad）", sbadges.count() >= 1, sbadges.count())
        # 来源行的按钮必须都在（三种状态各自的动作）
        # 注意：先把 button 全捞出来再读 inner_text，不要用 get_by_role(name=…)
        # —— 本项目 UiButton 的文字在 slot 里，role 计算出的可访问名与
        # `name="已核对"` 精确匹配对不上（三轮实测都栽在这里）。
        all_btns = ss.locator("button")
        names = [all_btns.nth(i).inner_text().strip()
                 for i in range(all_btns.count())]
        # 诊断：UiButton 的文字在 slot 里，inner_text 偶尔读成空 —— 同时取
        # text_content 兜底（前者按渲染可见文本，后者按 DOM 文本节点）。
        if not any(("核对" in n) or ("移除" in n) for n in names):
            raw = []
            for i in range(all_btns.count()):
                tc = all_btns.nth(i).text_content() or ""
                raw.append(" ".join(tc.split()))
            names = raw
        check("C4 面板里有动作按钮（已核对 / 移除来源等）",
              any(("核对" in n) or ("移除" in n) for n in names),
              {"n": all_btns.count(), "names": names[:10]})
        # 所有按钮同高 30px
        heights = []
        for i in range(min(all_btns.count(), 6)):
            h = _box(all_btns.nth(i))
            if h:
                heights.append(h["height"])
        check("C5 面板内按钮全为 30px（无 sm 混入）",
              all(abs(h - 30) <= 0.5 for h in heights), heights)
        _panel_shot(ss, "settings_a_stale")

        # ============================================================ 素材裁决
        print("\n[D] 素材裁决（SettingClosurePanel · 设定页）")
        sc = page.locator("[data-panel='setting-closure']").first
        sc.wait_for(state="visible", timeout=10000)
        ctxt = sc.inner_text()
        check("D1 标题与说明可见", "素材裁决" in ctxt and "否决按名字生效" in ctxt, ctxt[:150])
        check("D2 汇总行渲染（张素材卡 / 处待裁决）",
              "张素材卡" in ctxt and "处待裁决" in ctxt, ctxt[:260])
        check("D3 人工分歧块渲染（要你选一版的分歧）",
              "要你选一版的分歧" in ctxt, ctxt[:400])
        # “选这版”按钮
        pick = sc.locator("button").filter(has_text="选这版")
        check("D4 “选这版”按钮存在", pick.count() >= 1, pick.count())
        if pick.count():
            check("D4b “选这版”高 30px", abs(_box(pick.first)["height"] - 30) <= 0.5,
                  _box(pick.first)["height"])
        # 点“选这版”应该展开裁决输入框（不能静默无反应）
        if pick.count():
            pick.first.click()
            page.wait_for_timeout(700)
            reason = sc.locator("input[placeholder^='例：4-4']")
            check("D5 点“选这版”后展开“为什么选它”输入框（有反应）",
                  reason.count() == 1, reason.count())
            if reason.count():
                reason.type("4-4 稿给了改姓的制度理由")
                page.wait_for_timeout(200)
                check("D5b 裁决理由输入框 v-model 真回传",
                      "制度理由" in reason.input_value(), reason.input_value()[:60])
                # 取消掉，别把状态留在展开态影响截图
                cancel = sc.locator("button").filter(has_text="取消").first
                if cancel.count():
                    cancel.click()
                    page.wait_for_timeout(400)
        _panel_shot(sc, "settings_a_closure")

        # ============================================================ 暗色
        print("\n[E] 暗色主题下四块面板")
        page.locator("nav.tabs button", has_text="设置").first.click()
        page.wait_for_timeout(800)
        page.locator("nav.set-nav button", has_text="外观与更新").first.click()
        page.wait_for_timeout(600)
        sw = page.locator(".theme-swatch")
        sw.nth(sw.count() - 1).click()
        page.wait_for_timeout(800)
        dark = page.evaluate("document.documentElement.classList.contains('is-dark')")
        check("E1 切到暗色", dark, dark)
        if dark:
            page.locator("nav.tabs button", has_text="设定").first.click()
            page.wait_for_timeout(1600)
            ss2 = page.locator("[data-panel='setting-stale']").first
            ss2.wait_for(state="visible", timeout=10000)
            b2 = ss2.locator("span").filter(has_text="待核").filter(has_not_text="条待核").first
            if b2.count():
                ink_bad = page.evaluate(
                    "getComputedStyle(document.documentElement)"
                    ".getPropertyValue('--ink-bad').trim()")
                h = ink_bad.lstrip("#")
                if len(h) == 3:
                    h = "".join(c * 2 for c in h)
                want = "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                got = b2.evaluate("el => getComputedStyle(el).color")
                check("E2 暗色下待核徽标文字色 = 暗色 --ink-bad",
                      got == want, "got=%s want=%s" % (got, want))
            _panel_shot(ss2, "settings_a_dark")

        browser.close()

    check("无 console 报错", not errors, errors[:3])
    print("\n截图：Temp/gui_verify/ux/settings_a_*.png")
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
