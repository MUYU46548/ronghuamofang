# -*- coding: utf-8 -*-
"""设定条目的“时期 / 演变”两栏的真机目检（Playwright + headless Chromium）。

只做三件事（问题总账第 8 条的前端那一半）：

1. 设定页签 → 点开一条带 `phases` 的条目，确认抽屉里真有“时期”“演变”两栏，
   而且**已有内容能回显**（打开就看不见 = 等于没做）；
2. 直接在“演变”里填两期、保存，再**从盘上读回 setting.json** 确认落成
   `phases: [{era, note}]`（不是只改了个 ref 就显示成功）；
3. 截图留证（抽屉局部 + 整页），并记录 console 报错 / 4xx 请求。

**复用** `e2e_ux_verify` 的 preload 垫片、`new_page`、`shot` —— 不另造一套垫片。

前置（与 `e2e_product_gate_shot.py` 同一套）：
  · 静态服务 127.0.0.1:8091 → `console/renderer/dist`
  · 后端 127.0.0.1:8799（`nf_api.py --root <临时工作区>`，启动时 `NF_GUI_TOKEN=devprobe`）
  · 该工作区 `data/setting/setting.json` 里第一条带 `phases`

用法：python tests/e2e/e2e_setting_period_shot.py
环境变量：NF_E2E_API（默认 http://127.0.0.1:8799）
         NF_E2E_SETTING（默认 `<repo>/data/setting/setting.json`，指到临时工作区的那份）
"""
import json
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
SETTING = Path(os.environ.get("NF_E2E_SETTING", str(REPO / "data" / "setting" / "setting.json")))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)) if (detail and not cond) else ""))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx, page, errs, bad = U.new_page(browser, API, "setting_period")
        page.set_viewport_size({"width": 1440, "height": 1060})
        page.wait_for_timeout(600)
        page.on("dialog", lambda d: d.accept())      # 保存时 confirm，headless 必须放行

        page.keyboard.press("4")                     # 页签 4 = 设定
        page.wait_for_timeout(1200)
        U.shot(page, "setting_period_tab")

        rows = page.locator(".setting-row")
        check("设定页签列出了条目", rows.count() >= 1, rows.count())
        if rows.count() == 0:
            print("  （没有条目，后面的目检没有意义）")
            return finish(browser, ctx, errs, bad)

        # ---- 第 1 条：带 phases 的“苏芷” ----
        rows.nth(0).click()
        page.wait_for_timeout(700)
        drawer = page.locator(".drawer")
        txt = drawer.inner_text() if drawer.count() else ""
        check("抽屉打开了", drawer.count() > 0)
        check("有“时期”一栏", "时期（可选" in txt, txt[:120])
        check("有“演变”一栏", "演变（可选" in txt, txt[:120])
        # 用 data-field 定位（占位文案会改，选择器绑在“字段名”上才稳）
        pm = drawer.locator("[data-field='phases']")
        check("演变输入框存在（占位提示写清怎么写）", pm.count() == 1, pm.count())
        if pm.count():
            shown = pm.input_value()
            check("已有 phases 回显成“时期：状态”每行一条",
                  "早期" in shown and "后期" in shown and "：" in shown, repr(shown))
        U.shot(page, "setting_period_drawer")

        # 关闭（点“关闭”按钮，不落盘）
        drawer.locator("button", has_text="关闭").first.click()
        page.wait_for_timeout(300)

        # ---- 第 2 条：现填两期 → 保存 → 盘上校验 ----
        if rows.count() < 2:
            print("  （只有一条条目，跳过写入段）")
            return finish(browser, ctx, errs, bad)
        rows.nth(1).click()
        page.wait_for_timeout(600)
        d2 = page.locator(".drawer")
        pm2 = d2.locator("[data-field='phases']")
        pm2.fill("早期：没有标识\n后期：有标识，需专用设备识别")
        d2.locator("[data-field='period']").first.fill("卷一")
        U.shot(page, "setting_period_drawer_filled")
        d2.locator("button", has_text="关闭").first.click()
        page.wait_for_timeout(400)

        before = json.loads(SETTING.read_text(encoding="utf-8"))
        page.locator("button", has_text="保存").first.click()
        page.wait_for_timeout(1500)
        after = json.loads(SETTING.read_text(encoding="utf-8"))
        ch = {c.get("name"): c for c in after.get("characters", [])}
        target = ch.get("观澜") or {}
        check("保存后盘上多了 phases（不是只改界面）",
              isinstance(target.get("phases"), list) and len(target.get("phases") or []) == 2,
              target.get("phases"))
        check("每期是 {era, note} 结构（与 schema 认的形态一致）",
              bool(target.get("phases")) and set(target["phases"][0]) >= {"era", "note"},
              target.get("phases"))
        check("时期单值也落了盘", target.get("period") == "卷一", target.get("period"))
        check("没打开的条目内容没被扰动（phases 原样、没凭空长出 period）",
              (ch.get("苏芷") or {}).get("phases")
              == [c for c in before["characters"] if c.get("name") == "苏芷"][0]["phases"]
              and "period" not in (ch.get("苏芷") or {}),
              ch.get("苏芷"))
        check("界面无 4xx/5xx 请求", not bad, bad[:5])

        return finish(browser, ctx, errs, bad)


def finish(browser, ctx, errs, bad):
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
