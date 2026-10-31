# -*- coding: utf-8 -*-
"""截图自检的护栏（2026-10-31）—— 用自造长页面验 shot() 的三道判据。

## 为什么需要这条

用户看我们的全页截图说：「‘绒花墨坊’顶部栏都和原位分家了……我需要确认这是
截图的问题还是界面被改坏了。」——是截图的问题（full_page + position:sticky 的
渲染伪影），但那次是**用户发现的**。

截图是交付凭据，凭据带系统偏差比没有更糟。所以给 shot() 加了三道自检，
并用这条用例保证自检自己不会失灵。

## 为什么用自造页面，不用应用页面

应用页面太短（滚动量常只有几十像素），sticky 判据会**空转**；API 挂了页面还是空的，
根本滚不动 —— 第一版反证就是这么假绿的。所以这里写一份长 HTML 当夹具。

## 判据

- G1 正常页面 → 自检通过（不误报）
- G2 顶栏高度塌陷 → 必须报「顶栏高度异常」并降级为视口截图
- G3 滚动后再 full_page → 自动滚回顶部、图高 ≈ 整页高、截完恢复原滚动位置
- G4 台账结构可被 e2e 汇总读取

用法：python tests/e2e/test_shot_guard.py
（不在 nfctl test 范围里 —— 需要 playwright 与浏览器。）
"""
import json
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
TMP = ROOT / "Temp" / "guard_site"
TMP.mkdir(parents=True, exist_ok=True)

LONG_BODY = "<p>正文段落，用来把页面撑长。</p>" * 160
HTML = (
    '<!doctype html><html><head><meta charset="utf-8">\n'
    "<style>\n"
    "  body { margin:0; font-family:system-ui; }\n"
    "  .topbar { position:sticky; top:0; height:100px;"
    " background:rgba(230,225,245,.95); border-bottom:1px solid #ccc;"
    " display:flex; align-items:center; padding-left:20px; }\n"
    "  .filler { padding:20px; line-height:2; }\n"
    "  .broken .topbar { height:10px !important; overflow:hidden; }\n"
    "</style></head><body class=\"\">\n"
    '<div class="topbar"><b>绒花墨坊</b>（测试顶栏）</div>\n'
    '<div class="filler">' + LONG_BODY + "</div>\n"
    "</body></html>"
)
(TMP / "index.html").write_text(HTML, encoding="utf-8")

import e2e_ux_verify as UX      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def port_busy(p):
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    procs = []
    if not port_busy(8095):
        procs.append(subprocess.Popen(
            [PY, "-m", "http.server", "8095", "--bind", "127.0.0.1"],
            cwd=str(TMP), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:8095/index.html",
                                       timeout=1).read()
                break
            except Exception:
                time.sleep(0.5)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            ctx = b.new_context(viewport={"width": 1200, "height": 700})
            page = ctx.new_page()
            page.goto("http://127.0.0.1:8095/index.html", wait_until="domcontentloaded")
            page.wait_for_timeout(400)

            print("\n【G1】正常页面：自检不误报")
            UX.SHOT_CHECKS.clear()
            UX.shot(page, "_guard_normal", full=True)
            g1 = UX.SHOT_CHECKS[-1]
            check("正常页面通过自检", g1["ok"] is True, g1)

            print("\n【G2】布局真的坏了：顶栏高度塌陷（10px）")
            page.evaluate("document.body.classList.add('broken')")
            page.wait_for_timeout(250)
            geo = page.evaluate("""() => {
              const r = document.querySelector('.topbar').getBoundingClientRect();
              return {y: Math.round(r.y), h: Math.round(r.height),
                      scrollY: Math.round(window.scrollY)};
            }""")
            print("    顶栏几何：", json.dumps(geo, ensure_ascii=False))
            UX.shot(page, "_guard_broken", full=True)
            g2 = UX.SHOT_CHECKS[-1]
            check("顶栏高度异常时自检抓得住（报「顶栏高度异常」）",
                  g2["ok"] is False and any("顶栏高度异常" in n for n in g2["notes"]), g2)
            check("并降级为视口截图（宁可不截全页，也不给错图）",
                  any("降级" in n for n in g2["notes"]), g2["notes"])

            print("\n【G3】恢复顶栏 + 滚动后 full_page：自动归零且图高合理")
            page.evaluate("document.body.classList.remove('broken')")
            page.evaluate("window.scrollTo(0, 600)")
            page.wait_for_timeout(300)
            before = page.evaluate("Math.round(window.scrollY)")
            UX.shot(page, "_guard_scrolled_full", full=True)
            g3 = UX.SHOT_CHECKS[-1]
            after = page.evaluate("Math.round(window.scrollY)")
            check("截图前页面确实处于滚动状态（夹具自证）", before > 0, before)
            check("截图通过自检（已自动滚回顶部，没产生伪影）", g3["ok"] is True, g3)
            check("截图后恢复原滚动位置", after == before, (before, after))
            page_h = page.evaluate("document.documentElement.scrollHeight")
            png = ROOT / "Temp" / "gui_verify" / "ux" / "_guard_scrolled_full.png"
            with open(png, "rb") as f:
                head = f.read(33)
            img_h = struct.unpack(">I", head[20:24])[0]
            check("全页图高度 ≈ 整页高度（%d vs %d）" % (img_h, page_h),
                  abs(img_h - page_h) <= 60, (img_h, page_h))

            print("\n【G4】台账可被 e2e 汇总读取")
            check("SHOT_CHECKS 每条都带 name/ok/notes",
                  all({"name", "ok", "notes"} <= set(c) for c in UX.SHOT_CHECKS),
                  UX.SHOT_CHECKS[-1])
            ctx.close()
            b.close()
    finally:
        for pr in procs:
            try:
                pr.terminate()
            except Exception:                                # noqa: BLE001
                pass

    print("\n" + "=" * 50)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 50)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
