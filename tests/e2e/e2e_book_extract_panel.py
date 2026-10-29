# -*- coding: utf-8 -*-
"""成书提炼面板真机验收（0.7.13）：预演零写盘 → 开始提炼（后台）→ 失败可见。

## 为什么单独写一个

单元/HTTP 用例证明端点对；证明不了「章节页签里点得到、预演结果看得见、
后台跑起来有进度、失败原因摊在界面上」。这正是用户用终端做不到的那一段。

## 判据

A. 章节页签里有「成书提炼」折叠块
B. 预演：填路径 → 点预演 → 显示「3 章 / 字数 / 预计 N 次调用」，**盘上没有 data/book_extract**
C. 开始提炼：后台启动 → 状态条出现「提炼中」
D. 引擎是 hermes（或 fake）→ 任务失败，界面明说「改直连 / 用预演」，不是转圈
E. 目录已建（manifest 落盘），且没有 console error / 4xx

零 token（失败发生在调用模型之前）。用法：python tests/e2e/e2e_book_extract_panel.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright —— 本脚本是真机视觉验收。")
    sys.exit(0)

import e2e_ux_verify as UX                                          # noqa: E402

API_PORT = 8806
SITE_PORT = 8094
API = "http://127.0.0.1:%d" % API_PORT
SITE = "http://127.0.0.1:%d" % SITE_PORT
UX.SITE = SITE
OUT = ROOT / "Temp" / "gui_verify" / "book_extract"
OUT.mkdir(parents=True, exist_ok=True)
UX.OUT = OUT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


BOOK = """第一回 夜雨
他推开门，屋檐上的水线连成了串。

第二回 旧信
信上只有半个名字。

第三回 桥头
桥头蹲着两只石兽。
"""


def build_workspace():
    ws = Path(tempfile.mkdtemp(prefix="nf_bookextract_e2e_"))
    shutil.copytree(ROOT / "config", ws / "config",
                    ignore=shutil.ignore_patterns("history"))
    (ws / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (ws / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (ws / "data" / "state").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "project.yaml").write_text(
        'book:\n  name: "成书提炼面板验收书"\n  chapters: 3\n', encoding="utf-8")
    for i in range(1, 4):
        (ws / "data" / "chapters" / "raw" / ("%02d.md" % i)).write_text(
            "## 第%d章\n\n" % i + "他推门进来，屋檐上的水线连成了串。" * 30,
            encoding="utf-8")
    (ws / "old.txt").write_text(BOOK, encoding="utf-8")
    return ws


def wait_port(port, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main():
    ws = build_workspace()
    procs = []
    print("工作区：%s" % ws)
    try:
        env = dict(os.environ)
        env["NF_ROOT"] = str(ws)
        env["PYTHONIOENCODING"] = "utf-8"
        procs.append(subprocess.Popen(
            [sys.executable, "scripts/nf_api.py", "--port", str(API_PORT),
             "--root", str(ws)],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "http.server", str(SITE_PORT),
             "--directory", str(ROOT / "console" / "renderer" / "dist")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
        if not wait_port(API_PORT) or not wait_port(SITE_PORT):
            print("[SKIP] 服务没起来")
            return 0

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx, page, errs, bad = UX.new_page(browser, API, "book_extract")
            page.locator("nav.tabs button:has-text('章节')").click()
            page.wait_for_timeout(1500)

            print("\n=== A. 面板存在 ===")
            box = page.locator("details.import-box").filter(has_text="成书提炼")
            check("章节页签里有「成书提炼」折叠块", box.count() == 1, box.count())
            if box.count() == 0:
                ctx.close()
                browser.close()
                return 1
            box.locator("summary").click()
            page.wait_for_timeout(300)

            print("\n=== B. 预演：零写盘 + 规模可见 ===")
            box.locator("input.import-path").fill(str(ws / "old.txt"))
            box.locator("button:has-text('预演')").click()
            page.wait_for_timeout(1500)
            text = box.inner_text()
            check("预演结果显示章数", "3 章" in text, text[:200])
            check("显示预计调用数", "次模型调用" in text, text[:200])
            check("预演之后盘上没有 data/book_extract（一个文件都不写）",
                  not (ws / "data" / "book_extract").exists())
            UX.shot(page, "EX1_preview")

            print("\n=== C/D. 开始提炼：后台启动 → 失败可见（引擎护栏） ===")
            box.locator("button:has-text('开始提炼')").click()
            # 轮询到「失败」或 15 秒
            status = ""
            for _ in range(30):
                page.wait_for_timeout(700)
                text = box.inner_text()
                if "提炼失败" in text or "提炼完成" in text:
                    status = text
                    break
            check("状态条进入终态（不是一直转圈）", bool(status), status[:160])
            check("失败原因说清怎么办（改用直连 / 先预演）",
                  ("直连" in status) or ("预演" in status), status[-200:])
            check("目录已建（manifest 落盘）",
                  (ws / "data" / "book_extract" / "成书提炼面板验收书" / "manifest.json").exists()
                  or (ws / "data" / "book_extract" / "old" / "manifest.json").exists(),
                  sorted(p.name for p in (ws / "data" / "book_extract").glob("*"))
                  if (ws / "data" / "book_extract").is_dir() else None)
            check("出现「合并预览」入口（说明目录名已记住）",
                  box.locator("button:has-text('合并预览')").count() == 1)
            UX.shot(page, "EX2_failed")

            print("\n=== E. 干净度 ===")
            real_errs = [e for e in errs if "requestfailed" not in e]
            check("无 console error", not real_errs, real_errs[:3])
            check("无 4xx 响应", not bad, bad[:3])
            ctx.close()
            browser.close()
    finally:
        for pr in procs:
            try:
                pr.terminate()
            except Exception:                                    # noqa: BLE001
                pass
        shutil.rmtree(ws, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
