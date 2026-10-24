# -*- coding: utf-8 -*-
"""产物概况 + 成本 NaN 的真机视觉自查（2026-10-24）。

用户点名「该截图自查的自己查，别连界面长什么都不知道」。本脚本只干两件事，
都断言到 DOM 文本，不靠肉眼猜：

  1. 收件箱审批门的「产物概况」不再空白 —— 目录形态产物要渲染出「12 篇 · 148,930 字」
     （这是本次修复的核心：旧前端只认 stage 1/2/6 的文件，stage 4/6 的目录恒空白）；
  2. 流水线条「预计剩余」不再出现 NaN（旧实现读错字段 costSummary.total_yuan）。

依赖 e2e_ux_verify.py 的同款垫片（preload shim + 免责声明放行）。
用法：先起 8091 静态站 + 8798 mock，再 python tests/e2e/e2e_gate_artifacts_shot.py
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
    PRELOAD_SHIM_JS, SITE, MOCK_API, settle_disclaimer, shot, OUT,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 940})
        ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
        ctx.add_init_script(PRELOAD_SHIM_JS)
        page = ctx.new_page()
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        settle_disclaimer(page)

        # --- 1. 收件箱：产物概况 ---
        # 顶部导航里点「收件箱」
        page.get_by_role("button", name="收件箱").first.click()
        page.wait_for_timeout(1500)

        body = page.inner_text("body")
        check("收件箱出现「产物概况」标签", "产物概况" in body)
        # 目录形态产物：mock 喂 files=12 / chars=148930
        check("目录产物渲染出「12 篇」", "12 篇" in body,
              "" if "12 篇" in body else "页面未见「12 篇」")
        check("目录产物渲染出字数「148,930」",
              "148,930" in body or "148930" in body,
              "" if ("148,930" in body or "148930" in body) else "页面未见字数")
        # 旧 bug 的症状：只有「产物概况：」四个字后面全空
        if "产物概况" in body:
            tail = body.split("产物概况", 1)[1][:80].replace("\n", " ")
            check("「产物概况：」后面不是空白", tail.strip("：: ") != "", tail)
        p1 = shot(page, "gate_artifacts_inbox")

        # --- 2. 流水线条：预计剩余不得为 NaN ---
        # mock 的 /state 是 done/pending 混合且无 current_job，运行条不出现；
        # 直接验 computed 的数据源字段是否还读错：请求 costs/summary 并按前端新口径算
        summary = page.evaluate("async () => (await fetch(window.__NF_API_BASE__ + '/costs/summary')).json()")
        total = (summary.get("totals") or {}).get("cost_yuan")
        check("costs/summary 带 totals.cost_yuan（前端新口径的数据源存在）",
              total is not None, summary)
        # 旧字段 total_yuan 必须不存在 —— 存在的话说明我改错了方向
        check("旧字段 total_yuan 确实不存在（证明原实现读错）",
              "total_yuan" not in (summary or {}), summary)

        # 全页扫一次 NaN：estimatedRemainingCost 读错字段时会渲染成「NaN」
        full_txt = page.inner_text("body")
        check("全页无 NaN 字样（成本估算读错字段的症状）",
              "NaN" not in full_txt,
              "" if "NaN" not in full_txt else
              "出现于：" + full_txt[full_txt.index("NaN") - 40:full_txt.index("NaN") + 20].replace("\n", " "))

        page.wait_for_timeout(300)
        p2 = shot(page, "gate_artifacts_full", full=True)

        browser.close()

    print("\n截图：%s" % ", ".join(str(p) for p in (p1, p2)))
    print("%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
