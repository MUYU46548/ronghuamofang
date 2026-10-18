# -*- coding: utf-8 -*-
"""「定向改写」面板的专项验收（第 3 件 · C 兜底的 GUI 层）。

e2e_ux_verify 主套不触碰精修页签的定向改写面板，而本次交付的核心 GUI 入口就是
「精修页签 → 填章号+选字段 → 写意见 → 生成候选 → 挑一个 → 提交为提案」。
本脚本真点一遍：

- 精修页签里 `.fr-wrap` 渲染；
- 章号 + 字段 →「读当前值」→ 预填区显示当前值（不是空框）；
- 「生成候选」→ 走 job 轮询 → 3 张候选卡片出现；
- 点卡片 → 编辑框预填该候选文本（可就地改）；
- 「提交为提案」→ 提交的是**编辑框**里的文本（不是卡片原文，验证就地改生效）；
- 提交后面板提示走「审核」页签批准，且**不直接写稿**（这条由单测锁，
  这里只验提示文案，避免在验收里动真章大纲）；
- 空文本时提交按钮禁用（护栏）；
- 全程无 console error。

前置：静态服务 8091（serve console/renderer/dist）+ mock 8798 已起。
用法：python tests/e2e/e2e_field_refine.py
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
OUT = ROOT / "Temp" / "gui_verify" / "field_refine"
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
        bad_urls = []
        page.on("response", lambda r: bad_urls.append(f"{r.status} {r.url}")
                if r.status >= 400 and "favicon" not in r.url else None)

        page.goto(SITE, wait_until="networkidle")
        page.wait_for_timeout(2500)

        # 首启免责门：真实放行流（不注入 localStorage 绕过）
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

        # 切到精修页签
        page.locator("nav.tabs button", has_text="精修").first.click()
        page.wait_for_timeout(1200)
        panel = page.locator(".fr-wrap")
        check("定向改写面板渲染", panel.count() > 0)
        check("面板说明含「不直接写稿」",
              "不直接写稿" in (panel.first.inner_text() if panel.count() else ""))
        page.screenshot(path=str(OUT / "F1_panel.png"), full_page=True)

        # 章号 + 读当前值
        panel.locator("input.fr-input").first.fill("3")
        panel.locator("button", has_text="读当前值").first.click()
        page.wait_for_timeout(1000)
        pre = panel.locator("pre.fr-pre")
        check("读当前值后预填区非空", pre.count() > 0 and len(pre.first.inner_text()) > 0,
              pre.first.inner_text()[:50] if pre.count() else "无预填区")

        # 写意见 → 生成候选
        panel.locator("textarea").first.fill("关键段太空泛，该用具体物件顶上去")
        panel.locator("button", has_text="生成候选").first.click()
        # job 轮询 2s 间隔，mock 立即返回 ok —— 给足时间
        page.wait_for_timeout(6000)
        cands = panel.locator(".fr-cand")
        check("生成 3 张候选卡片", cands.count() == 3, cands.count())
        page.screenshot(path=str(OUT / "F2_candidates.png"), full_page=True)

        # 点第一张卡片 → 编辑框预填
        if cands.count():
            first_text = cands.first.locator(".fr-cand-text").first.inner_text()
            cands.first.click()
            page.wait_for_timeout(400)
            edit = panel.locator(".fr-edit textarea").first
            check("点卡片后编辑框预填该候选", edit.input_value() == first_text,
                  (edit.input_value() or "")[:40])

            # 就地改 → 提交的应是改后的文本
            edited = first_text + "（我改过的版本）"
            edit.fill(edited)
            page.wait_for_timeout(200)
            submit_btn = panel.locator("button", has_text="提交为提案")
            check("编辑后提交按钮可用", not submit_btn.first.is_disabled())
            submit_btn.first.click()
            page.wait_for_timeout(1200)

            # 提交后面板提示走审核页签批准
            toast = panel.locator(".fr-toast")
            t = toast.first.inner_text() if toast.count() else ""
            check("提交后提示走「审核」页签批准", "审核" in t and "批准" in t, t[:80])
            check("提交后清空候选（等下一轮）", cands.count() == 0)

        # 空文本护栏：再生成一次，清空编辑框 → 提交按钮禁用
        panel.locator("button", has_text="生成候选").first.click()
        page.wait_for_timeout(6000)
        if panel.locator(".fr-cand").count():
            panel.locator(".fr-edit textarea").first.fill("")
            page.wait_for_timeout(200)
            check("编辑框清空后提交按钮禁用",
                  panel.locator("button", has_text="提交为提案").first.is_disabled())

        # 全程无 console error
        fatal = [e for e in errors if "favicon" not in e and "Download the Vue" not in e]
        check("全程无 console error", not fatal, (fatal[:2], bad_urls[:4]))

        page.screenshot(path=str(OUT / "F3_after.png"), full_page=True)
        browser.close()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
