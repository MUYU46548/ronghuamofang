# -*- coding: utf-8 -*-
"""设定引用核对面板（M10）的真机视觉验收。

断言全部落在 DOM 文本 / 按钮上，不靠肉眼猜：

  1. 面板渲染出 mock 喂的两条待核（周砚清 / 青石站）与一条新引用（停了的怀表）；
  2. 章已不存在的来源说明是“第 7 章已不存在”，并给“移除来源”按钮；
  3. “扫描章节并记来源”先出预演（会新增几条），点“确认写入”后新引用消失；
  4. “已核对”之后该条从待核清单消失（摘要降到 1 条待核）；
  5. 全程无 console 报错（前端崩了截图再漂亮也是假的）。

依赖 e2e_ux_verify.py 的同款垫片（preload shim + 免责声明放行）。
用法：先起 8091 静态站 + 8798 mock，再 python tests/e2e/e2e_setting_stale.py
"""
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_ux_verify import (  # noqa: E402
    PRELOAD_SHIM_JS, SITE, MOCK_API, settle_disclaimer, shot,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


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
        settle_disclaimer(page)

        page.get_by_role("button", name="设定").first.click()
        page.wait_for_timeout(1600)

        panel = page.locator(".card", has_text="设定引用核对").first
        panel.wait_for(state="visible", timeout=8000)
        panel.get_by_text("条待核").first.wait_for(timeout=8000)
        text = panel.inner_text()

        check("面板标题与说明可见", "设定引用核对" in text and "零 token" in text, text[:120])
        check("渲染出待核条目“周砚清”", "周砚清" in text)
        check("渲染出待核条目“青石站”", "青石站" in text)
        check("渲染出新引用“停了的怀表”", "停了的怀表" in text)
        check("新引用写清哪一章、尚未登记", "第 5 章里有引用，尚未登记" in text, text[:400])
        check("章已不存在的来源如实说明", "第 7 章已不存在" in text, text[:400])
        check("缺失来源给“移除来源”按钮",
              panel.get_by_role("button", name="移除来源").count() >= 1)
        check("摘要显示 2 条待核", "2 条待核" in text.replace("\n", " "), text[:200])
        p1 = shot(page, "setting_stale_panel")

        # ---- 扫描：先预演，再确认写入 ----
        panel.get_by_role("button", name="扫描章节并记来源").click()
        page.wait_for_timeout(900)
        text = panel.inner_text()
        check("扫描先出预演（不直接写盘）", "预演：会新增" in text and "1" in text, text[:400])
        check("预演阶段出现“确认写入”按钮",
              panel.get_by_role("button", name="确认写入").count() == 1)
        p2 = shot(page, "setting_stale_preview")

        panel.get_by_role("button", name="确认写入").click()
        page.wait_for_timeout(1200)
        text = panel.inner_text()
        check("确认后预演条消失", "预演：会新增" not in text, text[:300])
        check("确认后新引用“停了的怀表”不再出现", "停了的怀表" not in text, text[:400])

        # ---- 逐条核对 ----
        panel.get_by_role("button", name="已核对", exact=True).first.click()
        page.wait_for_timeout(1200)
        text = panel.inner_text()
        check("核对后“周砚清”从待核清单消失", "周砚清" not in text, text[:400])
        check("摘要降到 1 条待核", "1 条待核" in text.replace("\n", " "), text[:200])
        check("另一条待核（青石站）保留 —— 逐条核对，不连带", "青石站" in text)
        p3 = shot(page, "setting_stale_after_review")

        browser.close()

    check("无 console 报错（页面级异常）", not errors, errors[:3])
    print("\n截图：%s" % ", ".join(str(p) for p in (p1, p2, p3)))
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
