# -*- coding: utf-8 -*-
"""侧栏导航“挤成两列”的视觉证据（2026-11-28）。

作者原话：“左侧栏在某个版本被改坏了，挤成一团。实在不行弄个下拉会不会？
UI 这一块世界上几千万人都做过了，不是什么创新的东西，直接照抄就能赢。”

## 要证明什么

侧栏（>=900px 时 .topbar 变成左侧竖栏）**必须单列 + 滚动**，不许折成两列。

根因是 CSS 的一个组合陷阱：基础规则 `.tabs { flex-wrap: wrap }` 是给横排用的，
宽屏媒体查询里改成 `flex-direction: column` 却**没把 wrap 改成 nowrap** ——
纵向 flex 容器里 wrap 的含义是"高度不够就往右边再开一列"。
所以这个 bug **只在窗口够矮时出现**（笔记本 768 高 / 125% 缩放都中招），
在 1080p 全屏截图里看不到 —— 这正是它长期没被发现的原因。

## 判据（不靠人眼看图，机器可判）

竖栏容器里所有页签按钮的 `getBoundingClientRect().left` 必须只有**一个**不同值
（容差 2px）。两个以上不同 left = 折成多列 = 红。

## 用法

先起静态站（服务 console/renderer/dist，端口 8091）：
    cd console/renderer/dist && python -m http.server 8091
再跑：
    python tests/e2e/e2e_sidebar_nav.py
截图落 Temp/gui_verify/sidebar/。
"""
import io
import json
import sys
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "sidebar"
BASE = "http://127.0.0.1:8091/index.html"

#: 要验的视口（宽 >= 900 才走竖栏；高度分别代表全屏 / 笔记本 / 125% 缩放后的等效高度）
VIEWPORTS = [
    ("1080p", 1920, 1040),
    ("笔记本-768", 1366, 700),
    ("矮窗口-触发bug", 1280, 560),
]

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:300]) if detail else ""))


def skip(name, why):
    SKIP.append(name)
    print("  [SKIP] %s  -> %s" % (name, why))


# 放行首启许可弹窗 + 提供 window.mofangAPI 垫片（与 e2e_ux_verify 同一套做法）
PRELOAD_SHIM = """
(() => {
  try {
    localStorage.setItem('mofang_license_acked', '1');
    localStorage.setItem('mofang_disclaimer_acked', '1');
  } catch (e) {}
  window.mofangAPI = {
    apiBase: () => 'http://127.0.0.1:8091',
    notifyGate: async () => ({ ok: true }),
    openExternal: async () => ({ ok: true }),
    openArtifact: async () => ({ ok: true }),
    readPreview: async () => ({ ok: false, error: 'stub' }),
    openFileDialog: async () => ({ ok: false, canceled: true }),
    platform: 'win32',
    versions: {},
  };
})();
"""

# 量：竖栏里所有页签按钮的 left 值去重（>1 个 = 折列）
PROBE_JS = """
() => {
  const topbar = document.querySelector('.topbar');
  if (!topbar) return { error: 'no .topbar' };
  const cs = getComputedStyle(topbar);
  const placedLeft = cs.position === 'fixed' && parseFloat(cs.width) < 400;
  const btns = [...document.querySelectorAll('.topbar .tabs button')];
  const rects = btns.map(b => ({
    text: (b.textContent || '').trim().slice(0, 8),
    left: Math.round(b.getBoundingClientRect().left),
    top: Math.round(b.getBoundingClientRect().top),
    w: Math.round(b.getBoundingClientRect().width),
  }));
  const lefts = [...new Set(rects.map(r => r.left))].sort((a, b) => a - b);
  // 相邻 left 差 > 2px 才算"另起一列"（同一列不同像素取整误差不算）
  const columns = [];
  for (const L of lefts) {
    if (!columns.length || Math.abs(L - columns[columns.length - 1]) > 2) columns.push(L);
  }
  const groups = [...document.querySelectorAll('.topbar .tab-group')].map(g => {
    const cb = [...g.querySelectorAll('button')];
    const cl = [...new Set(cb.map(b => Math.round(b.getBoundingClientRect().left)))].sort((a,b)=>a-b);
    const cols = [];
    for (const L of cl) {
      if (!cols.length || Math.abs(L - cols[cols.length - 1]) > 2) cols.push(L);
    }
    return { label: (g.querySelector('.tab-group-title')||{}).textContent || '',
             buttons: cb.length, columns: cols.length };
  });
  return {
    placedLeft,
    tabbar_w: Math.round(cs.width),
    buttons: rects.length,
    columns: columns.length,
    column_lefts: columns,
    groups,
    overflow_y: getComputedStyle(document.querySelector('.topbar .tabs')).overflowY,
    flex_wrap: getComputedStyle(document.querySelector('.topbar .tabs')).flexWrap,
    scroll_h: document.querySelector('.topbar .tabs').scrollHeight,
    client_h: document.querySelector('.topbar .tabs').clientHeight,
  };
}
"""


def main():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:                                    # noqa: BLE001
        skip("整体", "playwright 不可用：%s" % e)
        print("\n未执行（缺依赖）")
        return 0
    try:
        urllib.request.urlopen(BASE, timeout=5)
    except Exception as e:                                    # noqa: BLE001
        skip("整体", "静态站 8091 不通（先 cd console/renderer/dist && python -m http.server 8091）：%s" % e)
        print("\n未执行（缺服务）")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 66)
    print("侧栏导航：必须单列 + 滚动，不许折成两列")
    print("=" * 66)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-gpu"])
        for name, w, h in VIEWPORTS:
            print("\n【%s】%dx%d" % (name, w, h))
            page = browser.new_page(viewport={"width": w, "height": h})
            page.add_init_script(PRELOAD_SHIM)
            errs = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            try:
                page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                info = page.evaluate(PROBE_JS)
                shot = OUT / ("nav_%s.png" % name)
                page.screenshot(path=str(shot))
                if info.get("error"):
                    check("%s 找到 .topbar" % name, False, info)
                    page.close()
                    continue
                if not info["placedLeft"]:
                    check("%s 处于竖栏形态（宽度>=900 时应成立）" % name,
                          False, "topbar 不是 fixed 窄栏，tabbar_w=%s" % info["tabbar_w"])
                else:
                    check("%s 竖栏形态" % name, True, "宽 %spx" % info["tabbar_w"])
                    check("%s 页签只有一列（实测 %d 列，left=%s）"
                          % (name, info["columns"], info["column_lefts"]),
                          info["columns"] == 1, info["column_lefts"])
                    for g in info["groups"]:
                        check("%s 组“%s”单列（%d 个按钮）"
                              % (name, g["label"], g["buttons"]),
                              g["columns"] <= 1, g["columns"])
                    check("%s 纵向可滚动（放不下时滚，不是折列）" % name,
                          info["overflow_y"] in ("auto", "scroll"), info["overflow_y"])
                    check("%s flex-wrap 已改成 nowrap" % name,
                          info["flex_wrap"] == "nowrap", info["flex_wrap"])
                print("    截图 %s" % shot)
                if errs:
                    print("    console error %d 条：%s" % (len(errs), errs[:2]))
            except Exception as e:                            # noqa: BLE001
                check("%s 页面可用" % name, False, str(e)[:200])
            finally:
                page.close()
        browser.close()

    print("\n" + "-" * 66)
    print("PASS %d / FAIL %d / SKIP %d" % (len(PASS), len(FAIL), len(SKIP)))
    if FAIL:
        for f in FAIL:
            print("    -", f)
        return 1
    print("→ 全部通过。截图在 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
