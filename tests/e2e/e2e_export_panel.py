# -*- coding: utf-8 -*-
"""导出面板专项验收（2026-10-17 复盘修复的真机点击层）。

e2e_ux_verify 主套不触碰导出页签，而本次修复恰是导出面板的**分流逻辑**：
「双格式/仅 Word」曾是假选项（只出 Markdown）、frontmatter 勾选框是摆设、
book_name 显式 null 造出 None_markdown 目录。本脚本逐个选项真点一次，
用 mock 后端（tests/e2e/mock_nf_api_state.py，会回显收到的参数）断言：

- 格式=Markdown+Word → 先调 /export/markdown 再调 /export/word，两个请求都发出；
- 格式=仅 Markdown   → 只调 /export/markdown；
- 格式=仅 Word       → 只调 /export/word；
- frontmatter 勾选   → /export/markdown 的 include_frontmatter 真透传；
- 请求 body 里**不出现** book_name: null（None_markdown 根因）。

前置：静态服务 8091（serve console/renderer/dist）+ mock 8798 已起。
用法：python tests/e2e/e2e_export_panel.py
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "export_panel"
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
        # API 基址注入 mock（不打扰用户实例）；preload 垫片放行确认框
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
        bad_urls = []
        page.on("response", lambda r: bad_urls.append(f"{r.status} {r.url}")
                if r.status >= 400 and "favicon" not in r.url else None)

        page.goto(SITE, wait_until="networkidle")
        page.wait_for_timeout(2500)

        # 首启免责门：走真实放行流（滚到底 → 勾选 → 同意）——不注入 localStorage 绕过
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
        check("免责声明已放行（无残留遮罩）", page.locator(".disclaimer-mask").count() == 0)

        # 切到导出页签
        page.locator("nav.tabs button", has_text="导出").first.click()
        page.wait_for_timeout(800)
        check("导出面板渲染", page.locator("section.card", has_text="导出").count() > 0)
        page.screenshot(path=str(OUT / "E1_export_panel.png"), full_page=True)

        panel = page.locator("section.card", has_text="多平台导出").first
        fmt_select = panel.locator("select.bp-select").first
        exported = panel.locator("button", has_text="开始导出").first

        def requests_seen():
            """从 mock 回显 toast + 网络层双通道收集。"""
            return page.evaluate("() => window.__export_hits || []")

        # 用 route 拦截记录真实请求
        hits = []
        def rec(route, request):
            try:
                body = json.loads(request.post_data or "{}")
            except Exception:
                body = {}
            hits.append((request.url.split("127.0.0.1:8798")[-1], body))
            route.continue_()
        ctx.unroute("**/export/**")
        ctx.route("**/export/**", rec)

        def run_case(label, fmt, fm_before=None):
            hits.clear()
            fmt_select.select_option(fmt)
            if fm_before is not None:
                cb = panel.locator("input[type=checkbox]").first
                if cb.is_checked() != fm_before:
                    cb.click()
            page.wait_for_timeout(300)
            exported.click()
            page.wait_for_timeout(1500)
            msg = panel.locator(".meta", has_text="导出").first
            toast_text = msg.inner_text() if msg.count() else ""
            paths = [h[0] for h in hits]
            print(f"    [{label}] requests={paths} toast={toast_text[:80]!r}")
            return paths, dict(hits) if hits else {}, toast_text

        # case 1: 双格式 + frontmatter 开
        paths, bodies, toast = run_case("both+fm", "both", fm_before=True)
        check("双格式：两个端点都被调用",
              "/export/markdown" in paths and "/export/word" in paths, paths)
        md_body = next((b for u, b in hits if u == "/export/markdown"), {})
        check("frontmatter 勾选透传 include_frontmatter=true",
              md_body.get("include_frontmatter") is True, md_body)
        check("请求 body 无 book_name: null（None_markdown 根因）",
              all(b.get("book_name", "absent") is not None for _, b in hits),
              [b.get("book_name", "absent") for _, b in hits])

        # case 2: 仅 Markdown + frontmatter 关
        paths2, _, toast2 = run_case("md", "md", fm_before=False)
        check("仅 Markdown：不调 /export/word",
              "/export/markdown" in paths2 and "/export/word" not in paths2, paths2)
        md_body2 = next((b for u, b in hits if u == "/export/markdown"), {})
        check("frontmatter 取消后透传 false",
              md_body2.get("include_frontmatter") is False, md_body2)

        # case 3: 仅 Word
        paths3, _, toast3 = run_case("docx", "docx")
        check("仅 Word：不调 /export/markdown",
              "/export/word" in paths3 and "/export/markdown" not in paths3, paths3)

        # 全程无 console error
        fatal = [e for e in errors if "favicon" not in e and "Download the Vue" not in e]
        check("全程无 console error", not fatal, (fatal[:2], bad_urls[:4]))

        page.screenshot(path=str(OUT / "E2_after_exports.png"), full_page=True)
        browser.close()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
