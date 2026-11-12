# -*- coding: utf-8 -*-
"""路 C“出一份 Word”的真机目检（Playwright + headless Chromium）。

问题总账第五节：路 C = **出一份 Word，严格按模板拼接，不加任何附加物**。
这条路上有两件事只能从座位验：① 导出页那个按钮点下去真能出文件；② 出来的文件
真的过得了出口闸（`template_check.py` 15 项，零 LLM）—— 界面说“已导出”不算数。

本脚本：
  1. 打开“导出”页 → 格式选“仅 Word” → 开始导出（截确认框）
  2. 等盘上真出现 `output/<书名>_完整版.docx`（**以文件 mtime 变新为准**，不看提示语）
  3. 跑 `scripts/template_check.py --root <工作区>`（与流水线 stage7 同一套判据），要求 PASS 全绿
  4. 截图留证

前置：
  · 静态服务 127.0.0.1:8091 → `console/renderer/dist`
  · 后端 127.0.0.1:8799（`nf_api.py --root <临时工作区>`，`NF_GUI_TOKEN=devprobe`）
  · 工作区有 `config/project.yaml`（book.name / chapters / word_template）、
    `data/chapters/refined/01..NN.md`、`templates/<模板>.docx`

用法：python tests/e2e/e2e_export_word_path_c_shot.py
环境变量：NF_E2E_API / NF_E2E_WS（默认 `<repo>/Temp/e2e_path_c_ws`）
"""
import os
import subprocess
import sys
import time
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
WS = Path(os.environ.get("NF_E2E_WS", str(REPO / "Temp" / "e2e_path_c_ws")))
OUT_DIR = WS / "output"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)) if (detail and not cond) else ""))


def newest_docx():
    files = sorted(OUT_DIR.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main():
    if not (WS / "config" / "project.yaml").exists():
        print("[SKIP] 工作区没有 config/project.yaml —— 先铺好 fixture。")
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx, page, errs, bad = U.new_page(browser, API, "pathc")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)
        page.on("dialog", lambda d: d.accept())      # 导出前的确认框

        # ---- 1. 打开导出页 ----
        page.locator("nav button", has_text="导出").first.click()
        page.wait_for_timeout(1200)
        U.shot(page, "pathc_export_panel")
        sel = page.locator("select.bp-select").first
        check("导出页有格式下拉", sel.count() == 1)
        sel.select_option("docx")
        page.wait_for_timeout(200)

        before = newest_docx()
        before_mtime = before.stat().st_mtime if before else 0

        # ---- 2. 点导出并等文件真出现 ----
        page.locator("button", has_text="开始导出").first.click()
        deadline = time.time() + 90
        got = None
        while time.time() < deadline:
            page.wait_for_timeout(800)
            cur = newest_docx()
            if cur and cur.stat().st_mtime > before_mtime:
                got = cur
                break
        check("★ 点“开始导出”后盘上真多了一份 docx（mtime 变新，不看提示语）",
              got is not None, [str(x) for x in OUT_DIR.glob("*.docx")])
        U.shot(page, "pathc_exported")

        if got is None:
            return finish(browser, errs, bad)

        check("文件名是 <书名>_完整版.docx",
              got.name.endswith("_完整版.docx") and got.stat().st_size > 5000,
              (got.name, got.stat().st_size))

        # ---- 3. 出口闸：与流水线 stage7 同一套判据 ----
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "template_check.py"), "--root", str(WS)],
            cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (proc.stdout or "")[-1200:]
        print(tail)
        check("★ template_check 全绿（15 项：占位符零残留 / 章节映射 / 样式与模板逐字一致 / TOC / 分页）",
              proc.returncode == 0 and "FAIL=0" in (proc.stdout or ""), tail[-300:])

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
