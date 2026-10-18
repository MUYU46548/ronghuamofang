# -*- coding: utf-8 -*-
"""讨论提案队列的专项验收（P0-1 落地协议的 GUI 层）。

e2e_ux_verify 主套的 pendingGates 恒空，且不触碰审核页签的讨论队列 —— 而本次
修复的核心就是「agent 提案 → 用户在 GUI 批准 → 写回章大纲」这条链路的 GUI 侧。
本脚本用 mock（有状态：批准后队列真变化）真点一遍：

- 讨论提案块渲染在「审核」页签（与沙盒队列并存）；
- 顶部徽标计入讨论待审（不只是沙盒产物）；
- 「读提案」打开抽屉，正文含「待审提案」声明；
- 「批准并写回」走确认框 → 队列就地刷新、待审归零；
- 「驳回」必须填原因（空原因按钮禁用）；
- 全程无 console error。

前置：静态服务 8091（serve console/renderer/dist）+ mock 8798 已起。
用法：python tests/e2e/e2e_discuss_queue.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "discuss_queue"
OUT.mkdir(parents=True, exist_ok=True)
SITE = "http://127.0.0.1:8091"
API = "http://127.0.0.1:8798"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context()
        ctx.add_init_script(f"window.__NF_API_BASE__ = '{API}';")
        ctx.add_init_script("""
        window.mofangAPI = {
          readPreview: async () => '',
          notifyGate: () => ({ ok: true }),
          updaterStatus: async () => ({ initialized: false, dev: true }),
        };
        window.confirm = () => true;
        """)
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: d.accept())

        page.goto(SITE, wait_until="networkidle")
        page.wait_for_timeout(2500)

        # 首启免责门：真实放行流
        if page.locator(".disclaimer-mask").count():
            page.locator(".disclaimer-body").evaluate(
                "el => { el.scrollTop = el.scrollHeight; }")
            page.wait_for_timeout(350)
            cb = page.locator(".disclaimer-check input")
            agree = page.locator(".disclaimer-dlg button:has-text('我已阅读并同意')")
            if cb.count() and agree.count():
                cb.first.check()
                page.wait_for_timeout(150)
                agree.first.click()
                page.wait_for_timeout(450)
        check("免责声明已放行", page.locator(".disclaimer-mask").count() == 0)

        # 切到审核页签
        page.locator("nav.tabs button", has_text="审核").first.click()
        page.wait_for_timeout(1200)

        dq = page.locator(".dq-wrap")
        check("讨论提案块渲染在审核页签", dq.count() > 0)
        check("讨论提案标题含待审判定", "讨论提案" in dq.first.inner_text())
        page.screenshot(path=str(OUT / "Q1_queue.png"), full_page=True)

        # 顶部徽标：审核页签按钮应显示待审数（沙盒 0 + 讨论 1）
        badge = page.locator("nav.tabs button", has_text="审核").first.locator(".badge")
        check("徽标计入讨论待审（显示 1）",
              badge.count() > 0 and badge.first.inner_text().strip() == "1",
              badge.first.inner_text() if badge.count() else "无徽标")

        # 读提案：打开抽屉，正文含「待审提案」声明
        dq.locator("button", has_text="读提案").first.click()
        page.wait_for_timeout(900)
        drawer = page.locator(".drawer-wide")
        check("读提案抽屉打开", drawer.count() > 0)
        if drawer.count():
            txt = drawer.first.inner_text()
            check("提案正文含「待审提案」声明", "待审提案" in txt, txt[:80])
            check("提案正文含理由段", "理由" in txt)
        page.screenshot(path=str(OUT / "Q2_drawer.png"), full_page=True)

        # 关抽屉：用底部 .dialog-actions 里的「关闭」（不在顶栏下，也不受
        # drawer-wide 居中占满影响；头部的关闭按钮在视口顶被 sticky 顶栏盖住，
        # 遮罩又被 drawer-wide 居中盖掉，都没得点 —— 底部这个是可靠路径）。
        footer_close = page.locator(".drawer-wide .dialog-actions button",
                                    has_text="关闭")
        check("抽屉底部有可用关闭按钮", footer_close.count() > 0)
        footer_close.first.click()
        page.wait_for_timeout(500)
        check("抽屉可正常关闭", page.locator(".drawer-mask").count() == 0)

        # 驳回空原因：按钮应禁用
        dq.locator("button", has_text="驳回").first.click()
        page.wait_for_timeout(600)
        confirm_reject = page.locator(".dialog button", has_text="确认驳回")
        check("驳回对话框打开", confirm_reject.count() > 0)
        if confirm_reject.count():
            check("空原因时确认驳回禁用", confirm_reject.first.is_disabled())
        # 填原因后可点（先不真驳回，走批准路径）
        page.locator(".dialog textarea").first.fill("方向不对")
        page.wait_for_timeout(300)
        if confirm_reject.count():
            check("填原因后确认驳回可用", not confirm_reject.first.is_disabled())
        page.locator(".dialog button", has_text="取消").first.click()
        page.wait_for_timeout(400)

        # 批准并写回：确认框 → 队列刷新、待审归零
        dq.locator("button", has_text="批准并写回").first.click()
        page.wait_for_timeout(700)
        approve_dlg = page.locator(".dialog", has_text="批准提案并写回章大纲")
        check("批准确认框弹出并说明后果", approve_dlg.count() > 0)
        if approve_dlg.count():
            atxt = approve_dlg.first.inner_text()
            check("确认框说明会写回+有备份",
                  "追加" in atxt and "备份" in atxt, atxt[:100])
        page.screenshot(path=str(OUT / "Q3_approve_dlg.png"), full_page=True)

        page.locator(".dialog button", has_text="确认写回").first.click()
        page.wait_for_timeout(1200)
        body = dq.first.inner_text()
        check("批准后待审归零（显示没有待审提案）",
              "没有待审提案" in body, body[:120])
        check("批准后徽标消失",
              page.locator("nav.tabs button", has_text="审核").first
              .locator(".badge").count() == 0)
        page.screenshot(path=str(OUT / "Q4_after_approve.png"), full_page=True)

        # 显示全部：批准过的提案应出现在已通过列表
        dq.locator("input[type=checkbox]").first.click()
        page.wait_for_timeout(900)
        alltxt = dq.first.inner_text()
        check("显示全部后可见已通过提案", "已通过" in alltxt, alltxt[:150])

        # 全程无 console error
        fatal = [e for e in errors if "favicon" not in e and "Download the Vue" not in e]
        check("全程无 console error", not fatal, fatal[:3])

        browser.close()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
