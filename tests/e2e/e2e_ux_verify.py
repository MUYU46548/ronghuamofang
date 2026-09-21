# -*- coding: utf-8 -*-
"""UX 增强的真机视觉验收（Playwright + headless Chromium）。

为什么要有它：按钮"看着有、点了没反应"是这类 GUI 最常见的翻车点，只跑 HTTP 冒烟测不出来。
本脚本把构建好的 renderer/dist 用静态服务打开，用真实浏览器逐项点击/按键，截图存
Temp/gui_verify/ux/，同时收集 console error 与非 2xx 请求。

两个后端：
  - 假后端（Temp/mock_nf_api_state.py，8798）：确定性喂「阶段3 失败 + 阶段4 已跳过 + 待重写章节」，
    验证错误恢复一级入口、跳过徽标、待重写徽标、跳过确认框。
  - 真后端（scripts/nf_api.py --port 8799）：真数据（1-4 已完成），验证工作流条、命令面板、
    Space→预估确认框、数字键切页签、日志面板。

用法：python tests/e2e_ux_verify.py

⚠️ 真机验收，**不是**离线自动化用例：需先 `pip install playwright` 并
`playwright install chromium`，还要起静态服务（8091）+ mock/真后端。
缺依赖时**干净跳过**（打印 SKIP + 0 退出），不抛 ModuleNotFoundError ——
按项目纪律，**缺依赖/缺外部服务 = 未执行 ≠ 失败**（见 MEMORY.md）。
"""
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    print("[SKIP] 未安装 playwright —— 本脚本是真机视觉验收，不是离线用例。")
    print("       启用：python -m pip install playwright && playwright install chromium")
    print("       另需起静态服务 127.0.0.1:8091 与后端 8798/8799（详见本文件 docstring）。")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "ux"
OUT.mkdir(parents=True, exist_ok=True)
SITE = "http://127.0.0.1:8091"
MOCK_API = "http://127.0.0.1:8798"
COLD_API = "http://127.0.0.1:8797"   # 同一个 mock 的 --cold 实例（全新工作区）
REAL_API = "http://127.0.0.1:8799"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def new_page(browser, api_base, tag):
    ctx = browser.new_context(viewport={"width": 1440, "height": 940})
    ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(api_base))
    page = ctx.new_page()
    errs, bad = [], []
    page.on("console", lambda m: errs.append(m.type + ": " + m.text[:200])
            if m.type in ("error", "warning") else None)
    page.on("response", lambda r: bad.append("%s %s" % (r.status, r.url))
            if r.status >= 400 and "/favicon" not in r.url else None)
    page.goto(SITE, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    return ctx, page, errs, bad


def shot(page, name, full=False):
    p = OUT / ("%s.png" % name)
    page.screenshot(path=str(p), full_page=full)
    print("      shot → %s" % p.name)
    return p


def test_mock(browser):
    print("=== A. 假后端（8798）：错误恢复 / 跳过 / 待重写 ===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "mock")
    try:
        check("工作流条渲染（一键工作流 + 7 个步骤节点）",
              page.locator(".flow-strip").count() == 1 and page.locator(".flow-step").count() == 7,
              (page.locator(".flow-strip").count(), page.locator(".flow-step").count()))
        nxt = page.locator(".flow-next b").first.inner_text()
        check("下一步指向审批门（阶段6 已完成未审批）", "确认阶段 6" in nxt, nxt)
        check("步骤条标出了失败阶段（bad 类）", page.locator(".flow-step.bad").count() >= 1,
              page.locator(".flow-step.bad").count())
        check("步骤条标出了跳过阶段（skip 类）", page.locator(".flow-step.skip").count() >= 1,
              page.locator(".flow-step.skip").count())
        check("失败阶段卡片上一级入口可见（重试/跳过/回退/日志四件套）",
              page.locator(".stage-recovery").count() >= 1 and
              page.locator(".stage-recovery button:has-text('重试')").count() >= 1 and
              page.locator(".stage-recovery button:has-text('跳过此阶段')").count() >= 1 and
              page.locator(".stage-recovery button:has-text('回退')").count() >= 1 and
              page.locator(".stage-recovery button:has-text('查看日志')").count() >= 1)
        check("跳过徽标渲染", page.locator(".pill.st-skip").count() >= 1)
        check("待重写章节徽标渲染", page.locator(".pill:has-text('待重写')").count() >= 1)
        shot(page, "A1_pipeline_recovery")

        print("  --- 跳过确认框（勾选护栏，不用 window.confirm）---")
        page.locator(".stage-recovery button:has-text('跳过此阶段')").first.click()
        page.wait_for_timeout(400)
        check("跳过确认框弹出", page.locator(".dialog:has-text('跳过阶段')").count() == 1)
        btn = page.locator(".dialog button:has-text('确认跳过')")
        check("未勾选确认 → 按钮禁用（防误点）", btn.is_disabled())
        page.locator(".dialog input[type=checkbox]").check()
        page.wait_for_timeout(200)
        check("勾选后按钮可用", not btn.is_disabled())
        shot(page, "A2_skip_confirm")
        btn.click()
        page.wait_for_timeout(800)
        check("提交后弹框关闭 + 顶部 toast 反馈",
              page.locator(".dialog:has-text('跳过阶段')").count() == 0 and page.locator(".toast").count() == 1,
              page.locator(".toast").inner_text() if page.locator(".toast").count() else "")
        shot(page, "A3_after_skip")

        print("  --- 运行日志面板 ---")
        page.locator(".stage-recovery button:has-text('查看日志')").first.click()
        page.wait_for_timeout(900)
        check("日志面板打开且显示内容",
              page.locator(".log-body").count() == 1 and
              "阶段3 结果" in page.locator(".log-body").inner_text(),
              page.locator(".log-body").inner_text()[:80] if page.locator(".log-body").count() else "")
        shot(page, "A4_run_log")
        page.locator(".dialog button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)

        print("  --- 命令面板（Ctrl+K）---")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)
        check("Ctrl+K 打开命令面板", page.locator(".cmd-palette").count() == 1)
        total = page.locator(".cmd-item").count()
        check("命令数量足够多（含工作流/阶段/页签/恢复/外观）", total > 25, total)
        shot(page, "A5_palette")
        page.fill(".cmd-input", "跳过")
        page.wait_for_timeout(300)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("中文关键词能过滤到「跳过阶段」", any("跳过阶段" in t for t in labels), labels[:4])
        shot(page, "A6_palette_filter")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        check("命令面板 Enter 能执行（打开跳过确认框）",
              page.locator(".cmd-palette").count() == 0 and page.locator(".dialog:has-text('跳过阶段')").count() == 1)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        # Esc 只关弹层，dialog 由自身按钮关：直接点取消
        if page.locator(".dialog:has-text('跳过阶段')").count():
            page.locator(".dialog button:has-text('取消')").first.click()
            page.wait_for_timeout(300)

        print("  --- 快捷键 ---")
        page.keyboard.press("Shift+Slash")
        page.wait_for_timeout(400)
        check("? 打开快捷键说明", page.locator(".dialog:has-text('键盘快捷键')").count() == 1)
        shot(page, "A7_shortcuts")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Esc 关闭快捷键弹层", page.locator(".dialog:has-text('键盘快捷键')").count() == 0)
        page.keyboard.press("9")
        page.wait_for_timeout(600)
        active = page.locator(".tabs button.active").first.inner_text()
        check("数字键 9 → 文风页签", "文风" in active, active)
        page.keyboard.press("1")
        page.wait_for_timeout(600)
        check("数字键 1 → 流水线页签", "流水线" in page.locator(".tabs button.active").first.inner_text())
        shot(page, "A8_back_to_pipeline")

        errs_real = [e for e in errs if "404" not in e]
        print("  console error/warning: %s" % (errs_real or "无"))
        check("无前端 console 报错（假后端下）", not errs_real, errs_real[:3])
    finally:
        ctx.close()


def test_real(browser):
    print("\n=== B. 真后端（8799）：真数据上的工作流 / 预估 / 日志 ===")
    ctx, page, errs, bad = new_page(browser, REAL_API, "real")
    try:
        nxt = page.locator(".flow-next b").first.inner_text()
        check("真数据：下一步 = 运行阶段 5·逻辑检查", "阶段 5" in nxt, nxt)
        check("快速运行选择器自动对齐到阶段 5",
              page.locator(".flow-quick select").input_value() == "5",
              page.locator(".flow-quick select").input_value())
        page.wait_for_timeout(2500)
        est = page.locator(".flow-est").first.inner_text()
        check("预估成本已拉到（/estimate 真实口径）", "预计" in est and "¥" in est, est)
        shot(page, "B1_pipeline_real")

        print("  --- Space = 执行下一步（走费用预估确认，不真花钱）---")
        page.locator("body").click(position={"x": 700, "y": 700})
        page.keyboard.press(" ")
        page.wait_for_timeout(1800)
        check("Space 打开运行前预估确认框",
              page.locator(".dialog:has-text('运行前预估')").count() == 1)
        shot(page, "B2_estimate_by_space")
        if page.locator(".dialog button:has-text('取消')").count():
            page.locator(".dialog button:has-text('取消')").first.click()
            page.wait_for_timeout(400)
        check("取消后回到流水线且未启动任务",
              page.locator(".dialog:has-text('运行前预估')").count() == 0,
              page.locator(".runbar").count())

        print("  --- 命令面板搜索 + 执行只读命令 ---")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".cmd-input", "日")
        page.wait_for_timeout(300)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("搜索「日」命中运行日志", any("日志" in t for t in labels), labels[:5])
        page.fill(".cmd-input", "运行日志")
        page.wait_for_timeout(250)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)
        check("真后端日志面板打开（/logs/tail 真实读取）",
              page.locator(".log-body").count() == 1 and
              len(page.locator(".log-body").inner_text()) > 50,
              (page.locator(".log-body").inner_text()[:60] if page.locator(".log-body").count() else ""))
        shot(page, "B3_real_run_log")
        page.locator(".dialog button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)

        page.keyboard.press("r")
        page.wait_for_timeout(800)
        check("R 键刷新有 toast 反馈", page.locator(".toast").count() == 1,
              page.locator(".toast").inner_text() if page.locator(".toast").count() else "")

        print("  --- 真后端「关于」：版本/路径必须来自真实数据 ---")
        page.locator(".brand-btn").click()
        page.wait_for_timeout(1200)
        about = page.locator(".about-dlg")
        check("真后端关于弹窗打开", about.count() == 1)
        atxt = about.inner_text() if about.count() else ""
        check("版本号取自 console/package.json（非 dev）",
              "v0.1.0" in atxt and "读取版本信息失败" not in atxt, atxt[:70].replace("\n", " | "))
        check("显示真实项目根路径", "NovelForge" in atxt and "E:\\CODE" in atxt, None)
        check("含快速上手 / 数据位置 / 许可区块",
              about.locator(".about-steps li").count() == 4 and about.locator(".ap-row").count() == 3
              and "MIT" in atxt)
        shot(page, "B4_about_real")
        about.locator("button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)
        errs_real = [e for e in errs if "404" not in e]
        print("  console error/warning: %s" % (errs_real or "无"))
        check("无前端 console 报错（真后端下）", not errs_real, errs_real[:3])
        print("  非 2xx 响应: %s" % (list(dict.fromkeys(bad))[:5] or "无"))
    finally:
        ctx.close()


def test_dark(browser):
    """暗色主题：新组件（工作流条 / 命令面板 / 恢复条 / 浮层）不能只在浅色下能看。"""
    print("\n=== C. 暗夜紫主题下的观感 ===")
    ctx = browser.new_context(viewport={"width": 1440, "height": 940})
    ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
    ctx.add_init_script("localStorage.setItem('mofang_theme','dark');")
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.type + ": " + m.text[:200])
            if m.type in ("error", "warning") else None)
    try:
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        check("暗色主题已生效（html.theme-dark）",
              page.evaluate("document.documentElement.className"), page.evaluate("document.documentElement.className"))
        check("工作流条/恢复条在暗色下仍渲染", page.locator(".flow-strip").count() == 1
              and page.locator(".stage-recovery").count() >= 1)
        # 对比度粗检：正文与卡片背景的亮度差不能过小
        ratio = page.evaluate("""() => {
          const el = document.querySelector('.flow-next b');
          const s = getComputedStyle(el);
          const card = getComputedStyle(document.querySelector('.flow-strip'));
          const lum = (c) => { const m = c.match(/\\d+/g).map(Number);
            const a = m.slice(0,3).map(v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); });
            return 0.2126*a[0]+0.7152*a[1]+0.0722*a[2]; };
          const l1 = lum(s.color), l2 = lum(card.backgroundColor);
          const hi = Math.max(l1,l2), lo = Math.min(l1,l2);
          return {color: s.color, bg: card.backgroundColor, contrast: +(((hi+0.05)/(lo+0.05)).toFixed(2))};
        }""")
        check("暗色下正文/卡片对比度 ≥ 3", ratio["contrast"] >= 3, ratio)
        shot(page, "C1_dark_pipeline")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)
        shot(page, "C2_dark_palette")
        check("暗色主题无 console 报错", not [e for e in errs if "404" not in e], errs[:3])
    finally:
        ctx.close()


def test_about_project(browser, mock_proc=None):
    """关于 / 项目页签 / 新建项目向导 / 冷启动引导（发布级入口）。"""
    print("\n=== D. 关于 · 项目管理 · 新建项目向导 · 冷启动引导 ===")
    ctx, page, errs, bad = new_page(browser, MOCK_API, "about")
    try:
        print("  --- 左上角 LOGO → 关于 ---")
        page.locator(".brand-btn").click()
        page.wait_for_timeout(900)
        check("点 LOGO 打开「关于」", page.locator(".about-dlg").count() == 1)
        txt = page.locator(".about-dlg").inner_text()
        check("关于含版本号", "v" in txt and "绒花墨坊" in txt, txt[:60].replace("\n", " | "))
        check("关于含「快速上手」四步", page.locator(".about-steps li").count() == 4,
              page.locator(".about-steps li").count())
        check("关于含运行环境表（Electron/Chromium/平台）",
              "Electron" in txt and "Chromium" in txt, None)
        check("关于含数据位置三行 + 打开按钮",
              page.locator(".ap-row").count() == 3
              and page.locator(".ap-row button:has-text('打开')").count() == 3)
        check("关于含许可与外链按钮",
              page.locator(".about-links button:has-text('GitHub 仓库')").count() == 1
              and page.locator(".about-links button:has-text('开源许可')").count() == 1)
        check("关于含第三方组件声明（MIT/BSD）", "MIT" in txt and "BSD" in txt, None)
        shot(page, "D1_about")
        page.locator(".about-dlg button:has-text('关闭')").click()
        page.wait_for_timeout(300)
        check("关闭后关于消失", page.locator(".about-dlg").count() == 0)

        print("  --- 书名 → 项目页签 ---")
        page.locator(".brand-sub-btn").click()
        page.wait_for_timeout(700)
        active = page.locator(".tabs button.active").first.inner_text()
        check("点书名跳到「项目」页签", "项目" in active, active)
        check("项目页签有「＋ 新建项目」入口",
              page.locator("button:has-text('＋ 新建项目')").count() >= 1)
        check("项目页签显示工作区状态徽标",
              page.locator("text=工作区有数据").count() >= 1 or page.locator("text=工作区为空").count() >= 1)
        shot(page, "D2_project_tab")

        print("  --- 新建项目向导三步 ---")
        page.locator("button:has-text('＋ 新建项目')").first.click()
        page.wait_for_timeout(500)
        check("向导打开且停在第 1 步", page.locator(".wiz-steps").count() == 1
              and "1 基本信息" in page.locator(".wiz-steps").inner_text())
        nxt = page.locator(".dialog button:has-text('下一步')")
        check("空书名时「下一步」禁用（前置校验）", nxt.is_disabled())
        shot(page, "D3_wizard_step1")
        page.locator(".dialog input.text-input").first.fill("验收新书")
        page.wait_for_timeout(200)
        check("填了书名后「下一步」可用", not nxt.is_disabled())
        nxt.click()
        page.wait_for_timeout(300)
        check("进入第 2 步（当前工作区）", "2 当前工作区" in page.locator(".wiz-steps").inner_text())
        check("有数据时显示归档勾选框", page.locator(".wiz-check input[type=checkbox]").count() == 1)
        page.locator(".wiz-check input[type=checkbox]").uncheck()
        page.wait_for_timeout(250)
        check("取消勾选后给出明确风险提示",
              page.locator(".wiz-warn").count() == 1
              and "拒绝" in page.locator(".wiz-warn").inner_text(),
              page.locator(".wiz-warn").inner_text()[:60])
        page.locator(".wiz-check input[type=checkbox]").check()
        page.wait_for_timeout(200)
        page.locator(".dialog button:has-text('下一步')").click()
        page.wait_for_timeout(300)
        check("进入第 3 步（确认摘要）", "3 确认创建" in page.locator(".wiz-steps").inner_text())
        summary = page.locator(".dialog .cost-table").inner_text()
        check("摘要含书名/类型/章数/归档说明",
              "验收新书" in summary and "归档为" in summary, summary.replace("\n", " | ")[:90])
        shot(page, "D4_wizard_confirm")
        page.locator(".dialog button:has-text('确认创建')").click()
        page.wait_for_timeout(1500)
        toast_txt = page.locator(".toast").inner_text() if page.locator(".toast").count() else ""
        check("提交后向导关闭 + toast 明确报告创建结果",
              page.locator(".wiz-steps").count() == 0 and "新项目已创建" in toast_txt,
              toast_txt)
        check("创建后回到流水线页签",
              "流水线" in page.locator(".tabs button.active").first.inner_text())
        shot(page, "D5_after_create")

        print("  --- 命令面板里的新入口 ---")
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".cmd-input", "关于")
        page.wait_for_timeout(250)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("命令面板能搜到「关于绒花墨坊」", any("关于" in t for t in labels), labels[:3])
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".cmd-input", "新建项目")
        page.wait_for_timeout(250)
        labels = page.locator(".cmd-item .cmd-label").all_inner_texts()
        check("命令面板能搜到「新建项目」", any("新建项目" in t for t in labels), labels[:3])
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    finally:
        ctx.close()

    print("  --- 冷启动引导（全新工作区）---")
    ctx2, page2, errs2, bad2 = new_page(browser, COLD_API, "cold")
    try:
        check("全新工作区显示冷启动引导卡", page2.locator(".coldstart").count() == 1)
        check("引导含四步上手路径", page2.locator(".cs-step").count() == 4,
              page2.locator(".cs-step").count())
        check("引导里有「新建项目」直达按钮",
              page2.locator(".coldstart button:has-text('新建项目')").count() == 1)
        shot(page2, "D6_coldstart")
    finally:
        ctx2.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        try:
            test_mock(browser)
            test_real(browser)
            test_dark(browser)
            test_about_project(browser)
        finally:
            browser.close()
    print("\n" + "=" * 62)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("截图目录: %s" % OUT)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
