# -*- coding: utf-8 -*-
"""L2「一句话改一段」真机目检（Playwright + headless Chromium）——零 token。

判据只有一条：**点「润色」这一下，到底有没有真的发请求**。
以前它只把意见填进输入框（`@click="feedback = q.text"`），界面上看着有按钮、
点下去什么也不会发生 —— 正是用户点名的“界面有，按了没用”那一族。

怎么做到零 token：段落重写那条 POST 被本脚本**拦住并伪造 202**，任务与候选也伪造
（候选给一段真实的前后对照）。所以这里验的是**界面行为**（请求真的发出去了、
请求里带的是档位的意见、候选的字符级差异画出来了），不是模型质量。

前置（自己起，不碰用户端口）：
  · 静态服务 127.0.0.1:8093 → console/renderer/dist
  · 后端 127.0.0.1:8793 → nf_api.py --root <临时工作区> --allow-fake

用法：python tests/e2e/e2e_quick_edit_shot.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright —— 本脚本是真机目检，不是离线用例。")
    sys.exit(0)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "e2e"))
import e2e_ux_verify as U                                    # noqa: E402

PORT = 8793
SITE_PORT = 8093
API = "http://127.0.0.1:%d" % PORT
PY = sys.executable
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)) if (detail and not cond) else ""))


PARA_1 = "他把账本翻到最后一页，那一栏是空的。"
PARA_2 = "她把三句标准念了一遍，念的时候手上已经在分第一摞。"
PARA_3 = "窗外的雨声一阵紧过一阵。"


def build_ws(ws: Path) -> None:
    # 复用仓库的 config/（system.yaml 缺了 /state /models 会 500）+ 提示词目录
    import shutil
    shutil.copytree(REPO / "config", ws / "config",
                    ignore=shutil.ignore_patterns("history"), dirs_exist_ok=True)
    shutil.copytree(REPO / "prompts", ws / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"), dirs_exist_ok=True)
    (ws / "config" / "project.yaml").write_text(
        "book:\n  name: 快速改法目检\n  genre: 悬疑\n  chapters: 1\n"
        "  target_words: [4000, 7200]\n", encoding="utf-8")
    refined = ws / "data" / "chapters" / "refined"
    refined.mkdir(parents=True, exist_ok=True)
    (refined / "01.md").write_text(
        "## 第1章 目检\n\n" + PARA_1 + "\n\n" + PARA_2 + "\n\n" + PARA_3 + "\n",
        encoding="utf-8")
    (ws / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (ws / "data" / "setting" / "setting.json").write_text(
        json.dumps({"characters": [], "world": [], "plot_fragments": [], "timeline": []},
                   ensure_ascii=False), encoding="utf-8")


def wait_health(tries=80):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(API + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def main():
    dist = REPO / "console" / "renderer" / "dist"
    if not (dist / "index.html").exists():
        print("[SKIP] 没有构建产物 console/renderer/dist —— 先 `cd console && npm run build`")
        return 0

    ws = Path(tempfile.mkdtemp(prefix="nf_quickedit_ws_"))
    build_ws(ws)
    api_proc = subprocess.Popen(
        [PY, str(REPO / "scripts" / "nf_api.py"), "--root", str(ws),
         "--port", str(PORT), "--allow-fake"],
        cwd=str(ws), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ, NF_GUI_TOKEN="e2equick"))
    web_proc = subprocess.Popen([PY, "-m", "http.server", str(SITE_PORT), "--bind", "127.0.0.1"],
                                cwd=str(dist), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    try:
        if not wait_health():
            print("[FAIL] 后端没起来")
            return 1
        time.sleep(0.6)
        U.SITE = "http://127.0.0.1:%d" % SITE_PORT
        U.GUI_TOKEN = "e2equick"

        sent = {"preview": []}
        before = PARA_2
        after = "她把那三行标准念了一遍，手上已经在分第一摞。"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx, page, errs, bad = U.new_page(browser, API, "quickedit")
            page.set_viewport_size({"width": 1440, "height": 1040})
            page.wait_for_timeout(600)

            def on_route(route):
                req = route.request
                if "paragraph/preview" in req.url and req.method == "POST":
                    try:
                        sent["preview"].append(json.loads(req.post_data or "{}"))
                    except Exception:
                        sent["preview"].append({"raw": req.post_data})
                    route.fulfill(status=202, content_type="application/json",
                                  body=json.dumps({"ok": True, "job_id": "e2e-fake"}))
                    return
                if "/jobs/e2e-fake" in req.url:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"state": "ok", "result": {"ok": True}}))
                    return
                if "paragraph/pending" in req.url:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"exists": True, "rec": {
                                      "before": before, "after": after,
                                      "ok": True, "reasons": []}}, ensure_ascii=False))
                    return
                route.continue_()

            page.route(API + "/**", on_route)
            page.on("dialog", lambda d: d.accept())     # “以后不再问”确认框

            page.keyboard.press("2")                    # 页签 2 = 章节
            page.wait_for_timeout(1600)
            page.locator("button", has_text="编辑").first.click()
            page.wait_for_timeout(1400)
            check("E1 编辑器打开", page.locator(".ce-textarea").count() == 1)

            # 切到“段落批注”
            page.locator("button", has_text="段落批注").first.click()
            page.wait_for_timeout(1200)
            check("E2 段落批注面板出来了",
                  page.locator(".ce-para").count() >= 3, page.locator(".ce-para").count())

            page.locator(".ce-para").nth(1).click()     # 选第 2 段
            page.wait_for_timeout(400)
            U.shot(page, "quickedit_panel")
            check("E3 有 7 个档位",
                  page.locator(".ce-quick-btn").count() == 7,
                  page.locator(".ce-quick-btn").count())
            check("E4 “只填入”开关在界面上", page.locator(".ce-quick-toggle").count() == 1)

            before_bytes = json.dumps(sent["preview"])
            page.locator(".ce-quick-btn", has_text="润色").first.click()
            page.wait_for_timeout(250)
            check("E5 ★ 点「润色」真的发出重写请求（以前只填输入框）",
                  len(sent["preview"]) >= 1, before_bytes)
            body = sent["preview"][-1] if sent["preview"] else {}
            check("E6 请求里带的是这一段的意见",
                  "润色这一段" in str(body.get("feedback", "")), str(body)[:200])
            check("E7 请求带对章号与段号",
                  body.get("chapter") == 1 and body.get("paragraph_index") == 1, body)
            # 提交那一刻输入框里就是这条意见（作者看得见它发了什么）；
            # 候选回来后会被清空，所以这一条必须在刚点完时查。
            check("E8 点完立刻看得到发出去的意见",
                  "润色这一段" in (page.locator(".ce-feedback").input_value() or ""),
                  page.locator(".ce-feedback").input_value())

            page.wait_for_timeout(1500)
            U.shot(page, "quickedit_after_click")
            check("E9 候选面板出来了（改前/改后）",
                  page.locator(".ce-diff").count() == 1)
            check("E10 字符级差异真的标了（有被划掉的旧字）",
                  page.locator(".ce-del").count() >= 1, page.locator(".ce-del").count())
            check("E11 也标了新加的字", page.locator(".ce-ins").count() >= 1,
                  page.locator(".ce-ins").count())
            U.shot(page, "quickedit_char_diff")

            check("E12 界面无 4xx/5xx（拦截之外的请求都真打到后端）", not bad, bad[:4])
            console_err = [e for e in errs if e.startswith("error")]
            check("E13 console 无 error", not console_err, console_err[:3])
            browser.close()
    finally:
        for pr in (web_proc, api_proc):
            try:
                pr.terminate()
                pr.wait(timeout=8)
            except Exception:
                pr.kill()

    print("\n截图目录：%s" % U.OUT)
    for c in U.SHOT_CHECKS:
        if not c["ok"]:
            print("  · 截图自检有问题 %s：%s" % (c["name"], "；".join(c["notes"])))
    print("%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
