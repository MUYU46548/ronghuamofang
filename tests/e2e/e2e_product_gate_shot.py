# -*- coding: utf-8 -*-
"""产物闸面板的真机目检（Playwright + headless Chromium）。

只做一件事：打开审稿页签，看“等你点头的版本”面板在真界面里的样子，
再点一次批准，确认“面板上的按钮真能走通”。

**复用** `e2e_ux_verify` 的 preload 垫片、`new_page`、`shot`（含截图自检）——
不另造一套，否则两套垫片迟早漂移。

前置：8091 静态服务（console/renderer/dist）+ 8799 后端（`nf_api --root <有候选的工作区>`），
后端启动时设 `NF_GUI_TOKEN=devprobe`（批准是 GUI-only，要双因子）。

用法：python tests/e2e/e2e_product_gate_shot.py
"""
import io
import os
import sys
from pathlib import Path

# stdout 的 UTF-8 包装交给 e2e_ux_verify（它在 import 时已经包了一层）。
# 这里**不能**再包一次：两层 TextIOWrapper 里上层被回收时会关掉底层 buffer，
# 报 "I/O operation on closed file"（本脚本第一版就是这么挂的）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright —— 本脚本是真机目检，不是离线用例。")
    sys.exit(0)

import e2e_ux_verify as U                                    # noqa: E402

U.GUI_TOKEN = "devprobe"          # 与后端启动时的 NF_GUI_TOKEN 配对
API = os.environ.get("NF_E2E_API", "http://127.0.0.1:8799")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx, page, errs, bad = U.new_page(browser, API, "prodgate")
        # 竖栏比默认视口高：加高一点，否则栏底的徽标会被裁出画面（目检看不到 = 白跑）
        page.set_viewport_size({"width": 1440, "height": 1120})
        page.wait_for_timeout(400)
        page.keyboard.press("7")                 # 页签 7 = 审稿
        page.wait_for_timeout(1200)

        # 首启向导 / 别的抽屉会把整页罩住（实测：没填书名的项目每次必弹）。
        # 真实用户也是先关它 —— 这里按 Esc，关不掉就如实报出来，不硬点。
        if page.locator(".drawer-mask").count():
            print("有遮罩挡住（向导或抽屉），先按 Esc")
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
            print("  Esc 后遮罩数:", page.locator(".drawer-mask").count())

        title = page.locator("text=等你点头的版本")
        print("面板标题出现:", title.count())
        rows = page.locator(".prod-row")
        print("候选行数:", rows.count())
        if rows.count():
            print("  第一行文字:", " ".join(rows.first.inner_text().split())[:120])
            print("  状态标记:", page.locator(".prod-state").first.inner_text())
        U.shot(page, "product_gate_panel")

        if rows.count():
            btn = page.locator(".prod-row button.primary").first
            btn.click()
            page.wait_for_timeout(250)
            print("第一次点后按钮文字:", btn.inner_text().strip())
            btn.click()                          # 两步确认的第二下
            page.wait_for_timeout(1500)
            msg = page.locator(".prod-msg")
            print("批准后提示:", msg.first.inner_text().strip() if msg.count() else "(没有提示)")
            print("批准后状态:", page.locator(".prod-state").first.inner_text())
            U.shot(page, "product_gate_after_approve")

        print("console 错误（前5）:", errs[:5])
        print("非 2xx（前5）:", bad[:5])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
