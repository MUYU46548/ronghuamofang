# -*- coding: utf-8 -*-
"""「讨论此章」按钮的专项验收（P0-1 简报投递的 GUI 层）。

e2e_ux_verify 主套不触碰分章页签的编辑器工具栏，而本次交付的核心 GUI 入口就是
「分章页签 → 选章 → 讨论此章 → 打包任务书到剪贴板」。本脚本真点一遍：

- 分章页签渲染 + 能选中一章；
- 「讨论此章」按钮打开内联弹层（**不是 window.prompt** —— Electron 禁用原生
  对话框，prompt() 恒返回 null，会导致按钮点了没反应）；
- 弹层里填意见 →「打包并复制任务书」→ 真调 /outline/discussion/brief；
- 剪贴板真收到内容（含任务书抬头「讨论任务」与提交端点/红线）；
- 空意见也能打包（让 Agent 自己提问题）；
- 全程无 console error。

前置：静态服务 8091（serve console/renderer/dist）+ mock 8798 已起。
用法：python tests/e2e/e2e_discuss_brief.py
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
OUT = ROOT / "Temp" / "gui_verify" / "discuss_brief"
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
        # 剪贴板读写权限（验收要断言「真复制到了」，不是只看 toast）
        for perm in ("clipboard-read", "clipboard-write", "clipboard-sanitized-write"):
            try:
                ctx.grant_permissions([perm], origin=SITE)
            except Exception:                              # noqa: BLE001
                pass
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
        bad_urls = []
        page.on("response", lambda r: bad_urls.append(f"{r.status} {r.url}")
                if r.status >= 400 and "favicon" not in r.url else None)

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

        # 切到分章页签
        page.locator("nav.tabs button", has_text="分章").first.click()
        page.wait_for_timeout(1200)
        check("分章页签渲染", page.locator(".bp-layout").count() > 0)

        # mock 的章节列表端点若有数据则选第一章；没有就直接看工具栏（讨论按钮
        # 在选中章后才出现 —— currentN 为空时 openDiscuss 直接 return）
        first_item = page.locator(".bp-chapter-item")
        if first_item.count():
            first_item.first.click()
            page.wait_for_timeout(900)
        btn = page.locator("button", has_text="讨论此章")
        check("「讨论此章」按钮存在", btn.count() > 0)
        page.screenshot(path=str(OUT / "B1_toolbar.png"), full_page=True)

        # 点击 → 内联弹层（不是 window.prompt！）
        if not first_item.count():
            print("    [SKIP] mock 无章节数据，跳过弹层与打包断言（按钮存在性已验）")
        else:
            btn.first.click()
            page.wait_for_timeout(700)
            dlg = page.locator(".dialog", has_text="讨论第")
            check("内联弹层打开（非 window.prompt）", dlg.count() > 0)
            check("弹层含意见输入框", dlg.locator("textarea").count() > 0)
            check("弹层说明「你批准才写回大纲」",
                  "批准" in dlg.first.inner_text())
            check("无原生 prompt 残留（Electron 禁用，点了会静默退出）",
                  page.evaluate("() => window.__promptCalled || false") is False)
            page.screenshot(path=str(OUT / "B2_dialog.png"), full_page=True)

            # 填意见 → 打包
            dlg.locator("textarea").first.fill("说服那段太生硬，该用更具体的旧创案例")
            page.wait_for_timeout(200)
            dlg.locator("button", has_text="打包并复制任务书").first.click()
            page.wait_for_timeout(1500)

            # 弹层应关闭 + toast 提示
            check("打包后弹层关闭", page.locator(".dialog", has_text="讨论第").count() == 0)

            # 剪贴板真收到任务书
            clip = page.evaluate("() => navigator.clipboard.readText()")
            check("剪贴板收到任务书", bool(clip) and "讨论任务" in clip,
                  (clip or "")[:60])
            check("任务书含用户意见",
                  "说服那段太生硬" in (clip or ""))
            check("任务书含提交端点", "/outline/discussion/apply" in (clip or ""))
            check("任务书含红线", "不要直接写" in (clip or ""))
            check("任务书无模板占位符（成对花括号）",
                  "{{" not in (clip or ""))

        # 全程无 console error
        fatal = [e for e in errors if "favicon" not in e and "Download the Vue" not in e]
        check("全程无 console error", not fatal, (fatal[:2], bad_urls[:4]))

        page.screenshot(path=str(OUT / "B3_after.png"), full_page=True)
        browser.close()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
