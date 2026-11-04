# -*- coding: utf-8 -*-
"""规划四态画布视图（task-20261104-004）真机视觉验收。

断言落在 DOM + 后端状态上（不是截图好看就算）：
  1. 大纲页“规划四态”卡有两个视图按钮，默认清单；点“画布”切过去；
  2. 画布是四列（构想/已采用/已写进正文/已丢弃），每列张数与后端 stats 一致；
  3. **在画布上点“采用”** → 卡片换列、提示出现，且 mock 后端 /plan/list 里
     该条 status 真的变成 selected（证明写的是同一份状态，不是前端自己挪的）；
  4. 无 console 报错、无 4xx/5xx 请求。

用法：先起 8091 静态站 + 8798 mock（`python tests/e2e/mock_nf_api_state.py --port 8798`），
再 python tests/e2e/e2e_plan_board.py；端口可被 NF_E2E_SITE 覆盖。
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_ux_verify import PRELOAD_SHIM_JS, MOCK_API, settle_disclaimer, shot  # noqa: E402

SITE = os.environ.get("NF_E2E_SITE") or "http://127.0.0.1:8091"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def plan_state():
    """直接问 mock 后端：画布点完之后，盘上的状态是什么。"""
    with urllib.request.urlopen(MOCK_API + "/plan/list", timeout=10) as r:
        d = json.loads(r.read().decode("utf-8"))
    return {it["id"]: it["status"] for it in d.get("items") or []}, d.get("stats") or {}


def main():
    errors = []
    before, stats = plan_state()
    check("夹具：mock 后端有 5 条、四态都有",
          len(before) == 5 and stats.get("idea") == 2 and stats.get("selected") == 1
          and stats.get("written") == 1 and stats.get("dropped") == 1,
          (before, stats))
    target = "idea-a1"
    check("夹具：待采用那条在 idea 态", before.get(target) == "idea", before.get(target))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 940})
        ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(MOCK_API))
        ctx.add_init_script(PRELOAD_SHIM_JS)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        bad = []
        page.on("response", lambda r: bad.append("%s %s" % (r.status, r.url))
                if r.status >= 400 and "/favicon" not in r.url else None)
        page.goto(SITE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        settle_disclaimer(page)

        page.get_by_role("button", name="大纲").first.click()
        page.wait_for_timeout(1500)

        card = page.locator(".card", has_text="规划四态").first
        card.wait_for(state="visible", timeout=8000)

        # ---- 1. 两个视图 ----
        check("看到视图切换（清单/画布）",
              card.get_by_role("button", name="画布").count() >= 1)
        check("默认是清单视图（画布还没出来）",
              card.locator("[data-testid='plan-canvas']").count() == 0)

        card.get_by_role("button", name="画布").first.click()
        page.wait_for_timeout(600)
        canvas = card.locator("[data-testid='plan-canvas']")
        canvas.wait_for(state="visible", timeout=5000)
        check("点“画布”后画布出现", canvas.is_visible())

        # ---- 2. 四列 + 张数 ----
        cols = canvas.locator("[data-col]")
        check("画布是四列", cols.count() == 4, cols.count())
        for key, label in (("idea", "构想"), ("selected", "已采用"),
                           ("written", "已写进正文"), ("dropped", "已丢弃")):
            col = canvas.locator("[data-col='%s']" % key)
            tiles = col.locator("[data-id]")
            check("“%s”列张数 = %s" % (label, stats.get(key)),
                  tiles.count() == (stats.get(key) or 0), tiles.count())
            check("“%s”列标题可见" % label, label in col.inner_text())

        # ---- 3. 在画布上点“采用” ----
        idea_col = canvas.locator("[data-col='idea']")
        tile = idea_col.locator("[data-id='%s']" % target).first
        tile.wait_for(state="visible", timeout=5000)
        tile.locator("[data-act='select']").first.click()
        page.wait_for_timeout(1500)

        after, stats2 = plan_state()
        check("点“采用”后后端状态真的变成 selected（同一份 plan.json）",
              after.get(target) == "selected", after.get(target))
        sel_col = canvas.locator("[data-col='selected']")
        check("卡片换到“已采用”列",
              sel_col.locator("[data-id='%s']" % target).count() == 1)
        check("“构想”列少一张、“已采用”多一张",
              idea_col.locator("[data-id]").count() == (stats2.get("idea") or 0)
              and sel_col.locator("[data-id]").count() == (stats2.get("selected") or 0),
              (stats2, idea_col.locator("[data-id]").count()))
        body = page.inner_text("body")
        check("界面提示“已采用”", "已采用" in body, body[-200:])

        page.screenshot(path=str(shot(page, "plan_board_canvas")))
        check("无 console/page 报错", not errors, errors[:2])
        # 只对本面板相关的请求断言：mock 里有几个与本页无关的端点（/exports/history、
        # /ask/pending、/lock）本来就没实现，别的 e2e 也一律忽略 —— 拿它们判红是误伤。
        plan_bad = [x for x in bad if "/plan" in x]
        check("规划相关请求没有 4xx/5xx", not plan_bad, plan_bad[:3])
        browser.close()

    print("\n" + "=" * 62)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  ✗ " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
