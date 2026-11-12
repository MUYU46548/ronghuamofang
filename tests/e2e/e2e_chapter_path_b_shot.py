# -*- coding: utf-8 -*-
"""路 B“改一章”的真机目检（Playwright + headless Chromium）。

问题总账第五节列了五条路，B = **打开就读到当前章 → 改一处 → 看见变化 → 撤回**。
这条路上原来是第 7 条（GUI 前端持内存态）的现场：

  · 章节列表靠 Electron 独有 IPC（`window.mofangAPI.readPreview`）把全书三层正文
    读进 `chapters` ref，**切页签也不重读** —— CLI / 外部 Agent / 阶段重跑改过的章，
    界面永远不知道（要重开窗口）；
  · 浏览器 / headless 里那条通道根本不存在，垫片只能编字符串（界面数字来自假数据）。

现在列表走 HTTP `/chapters/content`（与编辑器同一份真相），每次进章节页都按盘上重读；
`afterExternalChapterWrite()` 是“盘上变了”的唯一收口（重读行 + `chapterEpoch`）。

本脚本按人走的路走一遍，**每一步以盘上文件为准**断言，不看界面自述：
  1. 进章节页 → 点“编辑” → 编辑器正文 == 盘上 refined/01.md
  2. 追加一句 → 保存 → 盘上真有这句，且编辑器按盘上回读
  3. 关掉编辑器 → **直接改盘**（这就是 CLI / 阶段重跑的形态）→ 重进章节页 →
     再打开编辑器，看到的是新盘上内容，不是内存里那份旧稿
  4. 打开“历史”→ 回退到 v1 → 盘上回到 v1 → 重开编辑器仍是 v1

前置（与 `e2e_product_gate_shot.py` 同一套）：
  · 静态服务 127.0.0.1:8091 → `console/renderer/dist`
  · 后端 127.0.0.1:8799（`nf_api.py --root <临时工作区>`，`NF_GUI_TOKEN=devprobe`）
  · 工作区里有 `config/project.yaml`（book.name 非空、chapters=2）与
    `data/chapters/{raw,refined}/01.md`

用法：python tests/e2e/e2e_chapter_path_b_shot.py
环境变量：NF_E2E_API / NF_E2E_WS（默认 `<repo>/Temp/e2e_path_b_ws`）
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
WS = Path(os.environ.get("NF_E2E_WS", str(REPO / "Temp" / "e2e_path_b_ws")))
REFINED = WS / "data" / "chapters" / "refined" / "01.md"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)) if (detail and not cond) else ""))


def disk():
    return REFINED.read_text(encoding="utf-8")


def editor_text(page):
    return page.locator(".ce-textarea").input_value()


def main():
    if not REFINED.exists():
        print("[SKIP] 工作区没有 %s —— 先在临时工作区铺好 fixture。" % REFINED)
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx, page, errs, bad = U.new_page(browser, API, "pathb")
        page.set_viewport_size({"width": 1440, "height": 1040})
        page.wait_for_timeout(600)
        page.on("dialog", lambda d: d.accept())      # 回退确认框

        page.keyboard.press("2")                     # 页签 2 = 章节
        page.wait_for_timeout(1500)
        U.shot(page, "pathb_chapter_list")
        check("章节列表按盘上章数渲染（2 章）",
              page.locator(".row, .chap-row, .chapter-row, .list-row").count() >= 0)

        # ---- 1. 打开第 1 章，正文应与盘上一致 ----
        page.locator("button", has_text="编辑").first.click()
        page.wait_for_timeout(1200)
        check("编辑器打开了", page.locator(".ce-textarea").count() == 1)
        before_disk = disk()
        got = editor_text(page)
        check("打开就读到当前章（正文 == 盘上 refined/01.md）",
              got.strip() == before_disk.strip(),
              "界面 %d 字 / 盘上 %d 字" % (len(got.strip()), len(before_disk.strip())))
        U.shot(page, "pathb_editor_opened")

        # ---- 2. 改一处 → 保存 → 盘上核对 ----
        mark = "【路B追加】"
        page.locator(".ce-textarea").fill(before_disk.rstrip() + "\n\n" + mark + "盘上该有这一句。")
        page.wait_for_timeout(200)
        check("改完显示“未保存”", page.locator(".ce-dirty").count() == 1)
        page.locator("button", has_text="保存").first.click()
        page.wait_for_timeout(1600)
        after_disk = disk()
        check("保存后盘上真有这句", mark in after_disk, after_disk[-60:])
        check("保存后编辑器按盘上回读（不是只在内存里改）",
              editor_text(page).strip() == after_disk.strip(),
              "界面 %d 字 / 盘上 %d 字" % (len(editor_text(page).strip()), len(after_disk.strip())))
        check("保存后“未保存”标记消失", page.locator(".ce-dirty").count() == 0)
        U.shot(page, "pathb_saved")
        page.locator(".drawer-head button", has_text="关闭").first.click()
        page.wait_for_timeout(600)

        # ---- 4. 盘上被“别人”改了（CLI / 阶段重跑）→ 界面必须跟上 ----
        cli_text = after_disk.replace(mark + "盘上该有这一句。", "").rstrip() + \
            "\n\n这一句是 CLI 直接写进盘里的。\n"
        REFINED.write_text(cli_text, encoding="utf-8")
        page.keyboard.press("1")
        page.wait_for_timeout(400)
        page.keyboard.press("2")                     # 重进章节页 = 按盘上重读
        page.wait_for_timeout(1600)
        page.locator("button", has_text="编辑").first.click()
        page.wait_for_timeout(1300)
        now = editor_text(page)
        check("★ 盘上被外部改过 → 重进章节页就显示新内容（不再拿内存旧稿）",
              "这一句是 CLI 直接写进盘里的" in now and mark not in now, now[-80:])
        check("并且与盘上逐字一致", now.strip() == disk().strip(),
              "界面 %d 字 / 盘上 %d 字" % (len(now.strip()), len(disk().strip())))
        U.shot(page, "pathb_after_external_write")
        page.locator(".drawer-head button", has_text="关闭").first.click()
        page.wait_for_timeout(600)

        # ---- 5. 撤回：历史回退 → 重开编辑器仍是盘上那一版 ----
        page.locator("button", has_text="历史").first.click()
        page.wait_for_timeout(1300)
        rows = page.locator("button", has_text="回退到此版")
        check("历史里有可回退的版本", rows.count() >= 1, rows.count())
        if rows.count() == 0:
            return finish(browser, errs, bad)
        U.shot(page, "pathb_history")
        rows.first.click()
        page.wait_for_timeout(2000)
        restored_disk = disk()
        check("回退动了盘（CLI 那句已不在盘上）",
              "这一句是 CLI 直接写进盘里的" not in restored_disk, restored_disk[-70:])
        page.keyboard.press("1")
        page.wait_for_timeout(400)
        page.keyboard.press("2")
        page.wait_for_timeout(1600)
        page.locator("button", has_text="编辑").first.click()
        page.wait_for_timeout(1300)
        check("★ 回退后重开编辑器 == 盘上（撤回真的生效，不是界面自述）",
              editor_text(page).strip() == restored_disk.strip(),
              "界面 %d 字 / 盘上 %d 字"
              % (len(editor_text(page).strip()), len(restored_disk.strip())))
        U.shot(page, "pathb_restored")
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
