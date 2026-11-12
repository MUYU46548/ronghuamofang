# -*- coding: utf-8 -*-
"""路 E“判断与查看”的真机目检（Playwright + headless Chromium，零 token）。

问题总账第五节：路 E = **判定结果、成本、状态各自可信**。

判据只有一条：**界面上的数字必须能在盘上找到出处**。所以本脚本先往工作区里
写死已知数据（progress.json / logs/runs.db），再打开页面，看界面念出来的
是不是那几个数 —— **对不上就是界面在编**。

  1. 成本页：`¥0.0323`（两条明细之和）/ `3,000` 输入 token / `2 次调用`
  2. 流水线页：阶段 4 显示“完成”、阶段 1 显示“失败”（照 progress.json 念）
  3. 截图留证 + console/4xx 自检

前置：
  · 静态服务 127.0.0.1:8091 → `console/renderer/dist`
  · 后端 127.0.0.1:8799（`nf_api.py --root <临时工作区>`，`NF_GUI_TOKEN=devprobe`）
  · 工作区已按 `tests/unit/test_ledger_truth.py` 的口径铺好（progress + runs.db + 两章 raw）

用法：python tests/e2e/e2e_ledgers_path_e_shot.py
环境变量：NF_E2E_API / NF_E2E_WS（默认 `<repo>/Temp/e2e_path_e_ws`）
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright —— 本脚本是真机目检，不是离线用例。")
    sys.exit(0)

import e2e_ux_verify as U                                    # noqa: E402

U.GUI_TOKEN = "devprobe"
API = os.environ.get("NF_E2E_API", "http://127.0.0.1:8799")
REPO = Path(__file__).resolve().parents[2]
WS = Path(os.environ.get("NF_E2E_WS", str(REPO / "Temp" / "e2e_path_e_ws")))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)) if (detail and not cond) else ""))


def main():
    if not (WS / "logs" / "runs.db").exists():
        print("[SKIP] 工作区没有 logs/runs.db —— 先铺好 fixture（见测试文件）。")
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx, page, errs, bad = U.new_page(browser, API, "pathe")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)

        # ---- 1. 成本页：数字必须等于库里那两个明细之和 ----
        page.locator("nav button", has_text="成本").first.click()
        page.wait_for_timeout(1600)
        body = page.inner_text("body")
        U.shot(page, "pathe_cost_page")
        check("★ 累计费用显示 ¥0.0323（= 两条明细之和，不是拍的）",
              "0.0323" in body, body[:400])
        check("★ 输入 token 显示 3,000", "3,000" in body)
        check("调用次数显示 2 次", "2 次调用" in body)

        # ---- 2. 流水线页：阶段状态照 progress.json 念 ----
        page.keyboard.press("1")
        page.wait_for_timeout(1600)
        body2 = page.inner_text("body")
        U.shot(page, "pathe_pipeline_state")
        ok4 = ("阶段 4" in body2) or ("逐章写作" in body2)
        check("流水线页有阶段 4 的行", ok4, body2[:300])
        check("★ 阶段 4 显示“完成”（progress.json 里就是 done）",
              "完成" in body2 or "已通过" in body2, body2[:400])
        check("★ 阶段 1 显示“失败”（progress.json 里就是 failed）",
              "失败" in body2, body2[:400])

        # ---- 3. 判定：报告接口与盘上记录同源 ----
        r = page.evaluate("""async () => {
          const b = window.__NF_API_BASE__ || '';
          const res = await fetch(b + '/judge/report');
          return [res.status, await res.text()];
        }""")
        check("GET /judge/report 能读到判定报告（不是 404/空）",
              r[0] == 200 and len(r[1]) > 20, r)

        check("界面无 4xx/5xx 请求", not bad, bad[:5])
        return finish(browser, errs, bad)


def finish(browser, errs, bad):
    print("\n截图目录：%s" % U.OUT)
    print("截图自检：%d 张，其中有问题 %d 张"
          % (len(U.SHOT_CHECKS), len([c for c in U.SHOT_CHECKS if not c["ok"]])))
    for c in U.SHOT_CHECKS:
        if not c["ok"]:
            print("  · %s：%s" % (c["name"], "；".join(c["notes"])))
    print("console 报错：%d 条 %s" % (len(errs), errs[:3] if errs else ""))
    print("失败请求：%d 条 %s" % (len(bad), bad[:3] if bad else ""))
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    browser.close()
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
