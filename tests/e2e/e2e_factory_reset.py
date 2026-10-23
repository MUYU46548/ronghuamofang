# -*- coding: utf-8 -*-
"""出厂测试项目一键还原 —— 真机视觉验收（针对本功能，替代卡壳的全量脚本）。

起 scratch 工作区（带出厂标记 + 测试进度）+ nf_api --root，Playwright 真浏览器：
项目页出现还原面板 → 点「一键还原」→ armed 态变「确认还原」→ 再点 → toast 报成功
→ 磁盘验证：进度清掉、素材铺回出厂。截图存 Temp/gui_verify/factory_reset/。
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
    # system.yaml：真实工作区必有（seedWorkspace 保证）；不放的话 /models /state
    # 等通用端点会在稀疏夹具上 500 —— 那不是本功能的问题，但会污染验收信号。
    shutil.copy2(ROOT / "config" / "system.yaml", SCRATCH / "config" / "system.yaml")
    (SCRATCH / "config" / "project.yaml").write_text(
        "book:\n  name: 单测预设书\n  genre: 悬疑\n  chapters: 3\n", encoding="utf-8")
    src = SCRATCH / "examples" / "presets" / "unit-preset"
    (src / "materials" / "raw").mkdir(parents=True)
    (src / "preset.yaml").write_text(
        "id: unit-preset\nname: 单测预设书\ngenre: 悬疑\nchapters: 3\nverify:\n  min_cards: 1\n",
        encoding="utf-8")
    for i in range(1, 4):
        (src / "materials" / "raw" / ("出厂卡%d.md" % i)).write_text("# 出厂卡%d\n" % i,
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
    from utils import factory_reset as fr
    fr.write_marker(SCRATCH, "preset", "unit-preset", "单测预设书")


def main():
    build_scratch()
    SHOT.mkdir(parents=True, exist_ok=True)
    # 双因子完整复刻：后端注入主进程同款 NF_GUI_TOKEN，前端经 mofangAPI 桩取 token
    # （裸浏览器没有 Electron IPC，不注入的话 reset 会被 is_trusted_gui 正确拒 403 ——
    #  那验的是闸门不是功能；两者都要，先验闸再验功能）。
    TOKEN = "e2e-fact-reset-token"
    env = dict(os.environ, NF_GUI_TOKEN=TOKEN)
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
            page.screenshot(path=str(SHOT / "1_project_panel.png"))
            # 第一步：armed
            panel.first.click()
            page.wait_for_timeout(400)
            armed = page.locator("button", has_text="确认还原（再点一次）")
            check("点一次 → armed 态「确认还原（再点一次）」", armed.count() > 0)
            check("面板带擦除计划说明",
                  "将清掉" in (page.locator("div.art-row", has_text="出厂测试项目").first
                            .inner_text() if page.locator(
                              "div.art-row", has_text="出厂测试项目").count() else ""))
            page.screenshot(path=str(SHOT / "2_armed.png"))
            # 第二步：执行
            armed.first.click()
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            check("toast 报「已还原到出厂状态」", "已还原到出厂状态" in body)
            page.screenshot(path=str(SHOT / "3_done.png"))
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
