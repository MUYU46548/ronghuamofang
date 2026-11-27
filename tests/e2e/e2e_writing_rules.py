# -*- coding: utf-8 -*-
"""作品写作约束面板（M12）真机视觉验收。

断言落在 DOM 上：
  1. 文风页出现“作品写作约束”卡，textarea 里是 mock 喂的真规则（不是占位符）；
  2. 字数计数与上限可见，保存按钮在“未修改”时是禁用的；
  3. 改一个字 → 保存按钮可用 → 点击后写请求真的发出（mock 回 message）；
  4. 无 console 报错。

用法：先起 8091 静态站 + 8798 mock，再 python tests/e2e/e2e_writing_rules.py
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

# 8091 可能被别的应用占着（本机实测：文案提取器占 IPv4 8091，与本项目的静态站互不干扰地共存），
# 允许用环境变量换站点地址 —— 不给就得先杀掉别人的进程，那是不可接受的。
SITE = os.environ.get("NF_E2E_SITE") or "http://127.0.0.1:8091"

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
        settle_disclaimer(page, require_first_run=False)

        page.get_by_role("button", name="文风").first.click()
        page.wait_for_timeout(1500)

        panel = page.locator(".card", has_text="作品写作约束").first
        panel.wait_for(state="visible", timeout=8000)
        ta = panel.locator("textarea").first
        ta.wait_for(timeout=8000)
        val = ta.input_value()

        check("面板标题与说明可见", "作品写作约束" in panel.inner_text())
        check("textarea 里是盘上的真规则（不是占位符）",
              "第三人称限知" in val and "现代科技名词" in val, val[:120])
        check("字数计数可见", "6000" in panel.inner_text(), panel.inner_text()[:200])
        save_btn = panel.get_by_role("button", name="保存").first
        check("未修改时保存按钮禁用", save_btn.is_disabled() is True)

        # 按钮高度必须与 .mini 的 30px 同档。2026-11-27 实测：迁移时误用了 UiButton 的
        # size="sm"（28px），与同行输入框差 2px —— “看着就是没对齐”，但当时没有任何断言管它。
        h_save = save_btn.bounding_box()["height"]
        h_ref = panel.get_by_role("button", name="刷新").first.bounding_box()["height"]
        check("刷新/保存两颗按钮都是 30px（不是 sm 的 28px）",
              abs(h_save - 30) <= 0.5 and abs(h_ref - 30) <= 0.5,
              "保存=%s 刷新=%s" % (h_save, h_ref))
        ta_box = ta.bounding_box()
        check("textarea 宽度占满卡片（未因迁移缩窄）",
              ta_box["width"] > 500, ta_box["width"])

        ta.fill(val + "\n必须：每章至少一次具体声音")
        page.wait_for_timeout(300)
        check("修改后出现“未保存”", "未保存" in panel.inner_text())
        check("修改后保存按钮可用", save_btn.is_disabled() is False)

        save_btn.click()
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        check("保存后后端回执可见（已保存 N 字）",
              "已保存" in body and "字" in body, body[-260:])
        p1 = shot(page, "writing_rules_panel")

        browser.close()

    check("无 console 报错（页面级异常）", not errors, errors[:3])
    print("\n截图：%s" % p1)
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
