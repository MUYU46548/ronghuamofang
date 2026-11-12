# -*- coding: utf-8 -*-
"""路 D“停下与回退”里 **GUI 那一半**的真机目检（Playwright + headless Chromium）。

“停止”的真相（半截存残稿 / 已完成章不受影响）必须真跑一次写作才看得见，本脚本**不假装验它**。
这里验的是两件能零 token 看清的事：

  1. **没任务时不给一个点了没用的“停止”**：运行条（`.runbar`，停止/写完这章就停都在里面）
     只在有 job 时出现 —— 无任务时不存在，这比“按钮在但点了说没有任务”更诚实。
  2. **回退从座位走一遍**：章节页 → 历史 → 回退到 v1 → 盘上逐字变成 v1（不看界面自述），
     列表跟着刷新。
  3. **“写完这章就停”的开关真落盘**（这条只能走接口，界面上那颗按钮在运行条里、没 job 就不可达）：
     POST `/pipeline/finish_chapter {on:true}` → GET 回读 on=true，且
     `<工作区>/data/state/finish_chapter.flag` 真的存在；关掉后两处都归零。

前置：
  · 静态服务 127.0.0.1:8091 → `console/renderer/dist`
  · 后端 127.0.0.1:8799（`nf_api.py --root <临时工作区>`，`NF_GUI_TOKEN=devprobe`）
  · 工作区 `data/chapters/refined/01.md`（当前稿）+ `data/chapters/history/ch01_v1.md`（旧稿）

用法：python tests/e2e/e2e_stop_and_restore_path_d_shot.py
环境变量：NF_E2E_API / NF_E2E_WS（默认 `<repo>/Temp/e2e_path_d_ws`）
"""
import json
import os
import sys
import urllib.request
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
WS = Path(os.environ.get("NF_E2E_WS", str(REPO / "Temp" / "e2e_path_d_ws")))
REFINED = WS / "data" / "chapters" / "refined" / "01.md"
FLAG = WS / "data" / "state" / "finish_chapter.flag"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)) if (detail and not cond) else ""))


def api(path, body=None):
    req = urllib.request.Request(
        API + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "X-Mofang-Source": "gui", "X-Mofang-Token": "devprobe"})
    with urllib.request.urlopen(req) as r:
        return r.status, json.load(r)


def main():
    if not REFINED.exists():
        print("[SKIP] 工作区没有 %s —— 先铺好 fixture。" % REFINED)
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx, page, errs, bad = U.new_page(browser, API, "pathd")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)
        page.on("dialog", lambda d: d.accept())

        # ---- 1. 没任务时：运行条不存在（停止不会出现成“点了没用”的按钮） ----
        page.keyboard.press("1")           # 流水线
        page.wait_for_timeout(1200)
        U.shot(page, "pathd_idle_pipeline")
        check("无任务时没有运行条（停止/写完这章就停都不出现）",
              page.locator(".runbar").count() == 0)
        check("页面上没有可点的“停止”按钮",
              page.locator("button", has_text="停止").count() == 0)

        # ---- 2. 回退：从座位走一遍，盘上核对 ----
        before = REFINED.read_text(encoding="utf-8")
        page.keyboard.press("2")           # 章节
        page.wait_for_timeout(1500)
        page.locator("button", has_text="历史").first.click()
        page.wait_for_timeout(1300)
        U.shot(page, "pathd_history_dialog")
        rows = page.locator("button", has_text="回退到此版")
        check("历史里有 v1 可退", rows.count() >= 1, rows.count())
        if rows.count():
            rows.first.click()
            page.wait_for_timeout(2000)
            after = REFINED.read_text(encoding="utf-8")
            check("★ 回退后盘上真的变了（v1 内容回来）",
                  after != before and "第一版" in after, after[-60:])
            U.shot(page, "pathd_after_restore")

        # ---- 3. “写完这章就停”开关：接口 + 盘上标志 ----
        st, d = api("/pipeline/finish_chapter", {"on": True})
        check("打开“写完这章就停”接口回 200",
              st == 200 and d.get("on") is True, (st, d))
        st2, d2 = api("/pipeline/finish_chapter")
        check("GET 回读也是开着的（不是只改内存）", d2.get("on") is True, d2)
        check("★ 盘上真出现 finish_chapter.flag", FLAG.exists(), str(FLAG))
        st3, d3 = api("/pipeline/finish_chapter", {"on": False})
        st4, d4 = api("/pipeline/finish_chapter")
        check("关掉后接口与盘上都归零",
              d3.get("on") is False and d4.get("on") is False and not FLAG.exists(),
              (d3, d4, FLAG.exists()))

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
