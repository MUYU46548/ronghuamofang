# -*- coding: utf-8 -*-
"""出厂测试项目一键还原 —— 真机视觉验收（针对本功能，替代卡壳的全量脚本）。

起 scratch 工作区（带出厂标记 + 测试进度）+ nf_api --root，Playwright 真浏览器：
项目页出现还原面板（按钮可用）→ 点「一键还原」→ 弹窗列出将清除项 → 滑条中途松手
弹回不解锁 → 拖到最右解锁 → 点「开始还原」→ toast 报成功 → 面板按钮置灰
（已是出厂状态）→ 磁盘验证：进度清掉、素材铺回出厂。
截图存 Temp/gui_verify/factory_reset/。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 无 playwright")
    sys.exit(0)

PORT = 8801
SCRATCH = Path(os.environ.get("TMPDIR", ".")) / "fact_gui_ws"
SHOT = Path(os.environ.get("TMPDIR", ".")) / "gui_verify" / "factory_reset"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:160]) if (detail and not cond) else ""))


def build_scratch():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    raw = SCRATCH / "materials" / "raw"
    raw.mkdir(parents=True)
    (SCRATCH / "config").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "system.yaml", SCRATCH / "config" / "system.yaml")
    (SCRATCH / "config" / "project.yaml").write_text(
        "book:\n  name: 雾港核心\n  genre: 悬疑\n  chapters: 3\n", encoding="utf-8")
    # 样例源（用户实际路径：随包样例书直接跑出来的项目，**没有铺设标记**，
    # 靠书名 == 样例名认身份）。样例布局：卡在 materials/ 直下（无 raw/ 层）。
    sample = SCRATCH / "_sample" / "sample-book"
    (sample / "materials").mkdir(parents=True)
    (sample / "project.yaml").write_text(
        "book:\n  name: 雾港核心\n  genre: 悬疑\n  chapters: 3\n", encoding="utf-8")
    for i in range(1, 4):
        (sample / "materials" / ("出厂卡%d.md" % i)).write_text("# 出厂卡%d\n" % i,
                                                                encoding="utf-8")
        (raw / ("测试脏卡%d.md" % i)).write_text("# 测试脏卡%d\n" % i, encoding="utf-8")
    for rel, content in (("data/chapters/raw/01.md", "## 第1章\n\n测试进度。\n"),
                         ("data/outline/global.md", "# 大纲\n"),
                         ("data/state/progress.json",
                          json.dumps({"stages": {"1": {"status": "done"}}}))):
        p = SCRATCH / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    (SCRATCH / "data" / "books" / "别的书").mkdir(parents=True)
    (SCRATCH / "data" / "books" / "别的书" / "_meta.json").write_text("{}", encoding="utf-8")
    # 刻意不写出厂标记 —— 本验收走的就是隐式样例身份（用户实况）


def main():
    build_scratch()
    SHOT.mkdir(parents=True, exist_ok=True)
    # 双因子完整复刻：后端注入主进程同款 NF_GUI_TOKEN，前端经 mofangAPI 桩取 token
    # （裸浏览器没有 Electron IPC，不注入的话 reset 会被 is_trusted_gui 正确拒 403 ——
    #  那验的是闸门不是功能；两者都要，先验闸再验功能）。
    TOKEN = "e2e-fact-reset-token"
    # NF_SAMPLE_ROOT：打包态主进程就这么注入（样例源在安装目录，工作区里没有）；
    # 夹具把样例放在 _sample/ 下，同一根注入路 —— 顺带把这条环境变量链也验了。
    env = dict(os.environ, NF_GUI_TOKEN=TOKEN,
               NF_SAMPLE_ROOT=str(SCRATCH / "_sample"))
    srv = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"),
         str(ROOT / "scripts" / "nf_api.py"), "--port", str(PORT), "--root", str(SCRATCH)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        time.sleep(3)
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=5).read()
        print("  后端就绪 :%d root=%s" % (PORT, SCRATCH))
        errs = []
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            ctx = b.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script("window.__NF_API_BASE__ = %s;"
                                % json.dumps("http://127.0.0.1:%d" % PORT))
            ctx.add_init_script(
                "window.mofangAPI = new Proxy({ getGuiToken: async () => %s },"
                " { get: (t, k) => t[k] || (async () => ({ ok: false })) });"
                % json.dumps(TOKEN))
            page = ctx.new_page()
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            bad = []
            page.on("response",
                    lambda r: bad.append("%d %s" % (r.status, r.url))
                    if r.status >= 400 else None)
            page.goto("http://127.0.0.1:8091", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            # 免责弹窗（首启真锁模态）：滚到底 → 勾选 → 同意（与用户真实路径一致）
            try:
                if page.locator(".disclaimer-mask.forced").count():
                    page.locator(".disclaimer-body").evaluate(
                        "el => { el.scrollTop = el.scrollHeight; }")
                    page.wait_for_timeout(350)
                    page.locator(".disclaimer-check input").first.check(force=True)
                    page.wait_for_timeout(150)
                    page.locator(".disclaimer-dlg button:has-text('我已阅读并同意')").first.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass
            # 进项目页签
            page.locator("button", has_text="项目").first.click()
            page.wait_for_timeout(1200)
            panel = page.locator("button", has_text="一键还原")
            check("项目页出现「一键还原」按钮（出厂项目）", panel.count() > 0)
            check("有测试进度 → 按钮可用（未置灰）",
                  panel.count() > 0 and panel.first.is_enabled())
            check("面板带擦除计划说明",
                  "将清掉" in (page.locator("div.art-row", has_text="出厂测试项目").first
                            .inner_text() if page.locator(
                              "div.art-row", has_text="出厂测试项目").count() else ""))
            page.screenshot(path=str(SHOT / "1_project_panel.png"))
            # 第一步：点开确认弹窗（0.7.0 —— 不再是同位置点两次的 armed 态）
            panel.first.click()
            page.wait_for_timeout(400)
            slider = page.locator(".dialog .slider-confirm")
            check("确认弹窗出现（列出将清除项 + 滑条）",
                  slider.count() > 0 and "将清除" in page.locator(".dialog").first.inner_text())
            start_btn = page.locator(".dialog button", has_text="开始还原")
            check("「开始还原」默认禁用（未滑条不解锁）",
                  start_btn.count() > 0 and not start_btn.first.is_enabled())
            page.screenshot(path=str(SHOT / "2_dialog.png"))
            # 第二步：滑条解锁 —— 先拖一半松手验证弹回，再拖到底解锁
            box = slider.bounding_box()
            check("滑条可定位", bool(box), box)
            knob_w, pad = 56, 3
            cy = box["y"] + box["height"] / 2
            x0 = box["x"] + pad + knob_w / 2
            max_dx = box["width"] - knob_w - pad * 2
            page.mouse.move(x0, cy)
            page.mouse.down()
            for frac in (0.3, 0.6):
                page.mouse.move(x0 + max_dx * frac, cy)
                page.wait_for_timeout(60)
            page.mouse.up()
            page.wait_for_timeout(150)
            check("中途松手 → 弹回不解锁", not start_btn.first.is_enabled())
            page.mouse.move(x0, cy)
            page.mouse.down()
            for frac in (0.3, 0.6, 0.9, 1.0):
                page.mouse.move(x0 + max_dx * frac, cy)
                page.wait_for_timeout(60)
            page.mouse.up()
            page.wait_for_timeout(150)
            check("拖到最右 → 解锁「开始还原」", start_btn.first.is_enabled())
            page.screenshot(path=str(SHOT / "3_slid.png"))
            # 第三步：执行
            start_btn.first.click()
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            check("toast 报「已还原到出厂状态」", "已还原到出厂状态" in body)
            check("还原后面板按钮置灰（已是出厂状态，2026-10-24 用户点名场景）",
                  panel.count() > 0 and panel.first.is_disabled())
            page.screenshot(path=str(SHOT / "4_done.png"))
            check("无前端 console 报错", not errs, (errs[:3] + ["|http:"] + bad[:5]))
            ctx.close()
            b.close()
        # 磁盘验证（端到端的真证据）
        from utils import factory_reset as fr
        cards = sorted(p.name for p in (SCRATCH / "materials" / "raw").glob("*.md"))
        check("磁盘：脏卡清掉、出厂 3 张铺回",
              cards == ["出厂卡1.md", "出厂卡2.md", "出厂卡3.md"], cards)
        check("磁盘：测试进度清掉",
              not (SCRATCH / "data/chapters/raw/01.md").exists()
              and not (SCRATCH / "data/state/progress.json").exists())
        check("磁盘：他书归档保留",
              (SCRATCH / "data/books/别的书/_meta.json").exists())
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            pass
        shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n==== 结果: PASS %d / FAIL %d ====" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
