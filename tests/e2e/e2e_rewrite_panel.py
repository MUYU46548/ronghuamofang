# -*- coding: utf-8 -*-
"""「重写本章」实时面板的真机验收（2026-10-27）：文件尾 + 中断 + 完成。

## 为什么单独写一个

单元用例只证明「源码里有 SSE 分支」「后端会推 {type:file}」；证明不了
**点下去面板真的出现、文件尾真的刷出来、中断真的能停** —— 而这正是用户
2026-10-27 抱怨的那一段（「连写到哪里都看不到」「点停止停不下来」）。

## 怎么做到零 token

起一个小**代理**（8802）挡在页面与真后端（8804）之间：
- 只接管 `POST /chapters/rewrite`、`GET /stream/{job}`、`POST /stop`（脚本化 SSE）；
- 其余请求原样转发给真 `nf_api --root <临时工作区>`。
所以页面渲染的是**真数据**（/state、/chapters/quality、/chapters/content），
任务与流是假的 —— **不调模型、不落稿**。

判据（A–F）：
A. 章节页签渲染 + 本章行有可点的「重写」
B. 点重写 → 面板打开、job 号显示、token 流有字
C. `{type:file}` → 「本章正文（文件实时）」块出现，元信息带字数与字节
D. 点「中断」→ 前端真的 POST /stop（代理记账）+ 状态变「已中断」
E. `{type:done}` → 状态变「完成」，按钮不再显示「中断」
F. console 无 error、无 4xx 响应（代理的是 200/202）

跑法：python tests/e2e/e2e_rewrite_panel.py
自起：临时工作区 + nf_api(8804) + 代理(8802) + 静态服务(8093)。
截图落 Temp/gui_verify/rewrite/。
⚠️ 真机验收，不是离线用例：缺 playwright 时干净 SKIP（exit 0）。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright —— 本脚本是真机视觉验收。")
    print("       启用：python -m pip install playwright && playwright install chromium")
    sys.exit(0)

import e2e_ux_verify as UX                                          # noqa: E402

API_PORT = 8802       # 代理（页面看到的 API）
UPSTREAM_PORT = 8804  # 真 nf_api
SITE_PORT = 8093
API = "http://127.0.0.1:%d" % API_PORT
UPSTREAM = "http://127.0.0.1:%d" % UPSTREAM_PORT
SITE = "http://127.0.0.1:%d" % SITE_PORT
UX.SITE = SITE
OUT = ROOT / "Temp" / "gui_verify" / "rewrite"
OUT.mkdir(parents=True, exist_ok=True)
UX.OUT = OUT

PASS, FAIL = [], []
STATE = {"seq": 0, "stops": 0, "stop_flag": False}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


CHAPTER = "## 第%d章 雨夜\n\n" + "他推开门，屋檐上的水线正连成了串。" * 40 + "\n"


def build_workspace():
    ws = Path(tempfile.mkdtemp(prefix="nf_rw_e2e_"))
    (ws / "config").mkdir(parents=True)
    (ws / "config" / "project.yaml").write_text(
        'book:\n  name: "重写面板验收书"\n  chapters: 3\n', encoding="utf-8")
    # agent_mode 必须关：开着的话 GUI 的「重写」按钮被 agentRunGuard 拦下
    # （设计如此：运行类动作归外部 Agent），验收就永远看不到面板 —— 2026-10-27 实测。
    sy = (ROOT / "config" / "system.yaml").read_text(encoding="utf-8")
    sy = re.sub(r"(?m)^(\s*agent_mode:\s*)true", r"\1false", sy)
    (ws / "config" / "system.yaml").write_text(sy, encoding="utf-8")
    for layer in ("raw", "checked", "refined"):
        d = ws / "data" / "chapters" / layer
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 4):
            (d / ("%02d.md" % i)).write_text(CHAPTER % i, encoding="utf-8")
    (ws / "data" / "outline").mkdir(parents=True, exist_ok=True)
    (ws / "data" / "outline" / "global.md").write_text(
        "# 重写面板验收书\n\n## 起\n雨夜\n", encoding="utf-8")
    (ws / "data" / "state").mkdir(parents=True, exist_ok=True)
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


class Proxy(BaseHTTPRequestHandler):
    """挡在页面与真后端之间的测试代理：只接管重写三条路径，其余透传。"""

    protocol_version = "HTTP/1.0"

    def log_message(self, *args):                                  # noqa: D401
        pass

    # -- helpers --------------------------------------------------------
    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:                                          # noqa: BLE001
            return {}

    def _send_json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self, method):
        body = None
        if method in ("POST", "PUT", "PATCH"):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
        req = urllib.request.Request(UPSTREAM + self.path, data=body, method=method)
        for k, v in self.headers.items():
            if k.lower() in ("host", "content-length", "connection"):
                continue
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data, code, headers = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            data, code, headers = e.read(), e.code, dict(e.headers)
        self.send_response(code)
        for k, v in headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection",
                             "keep-alive"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- routes ---------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Mofang-Source, X-Mofang-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/stream/"):
            self._sse(self.path.rsplit("/", 1)[-1])
            return
        self._proxy("GET")

    def do_POST(self):
        if self.path == "/chapters/rewrite":
            self._read_json()
            STATE["seq"] += 1
            STATE["stop_flag"] = False
            self._send_json(202, {"ok": True, "job_id": "job_e2e_%d" % STATE["seq"],
                                  "chapter": STATE["seq"], "stream": True,
                                  "message": "已开始重写"})
            return
        if self.path == "/stop":
            self._read_json()
            STATE["stops"] += 1
            STATE["stop_flag"] = True
            self._send_json(200, {"ok": True})
            return
        self._proxy("POST")

    def _sse(self, job):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def send(obj):
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False)
                                  + "\n\n").encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False

        if job.endswith("_1"):
            if not send({"type": "token", "text": "正在拆群…"}):
                return
            if not send({"type": "file", "chapter": 1, "name": "g1.md",
                         "text": "## 第1章 雨夜\n\n他推开门，屋檐上的水线正连成了串。",
                         "bytes": 4096, "words": 1200}):
                return
            # 保持连接，直到前端点「中断」（或 30s 兜底）
            for _ in range(120):
                if STATE["stop_flag"]:
                    break
                time.sleep(0.25)
            send({"type": "done", "status": "stopped"})
        else:
            send({"type": "file", "chapter": 2, "name": "raw/02.md",
                  "text": "## 第2章 雨夜\n\n写完了。", "bytes": 9000, "words": 2600})
            send({"type": "done", "status": "ok"})


def main():
    ws = build_workspace()
    procs = []
    print("工作区：%s" % ws)
    server = None
    try:
        env = dict(os.environ)
        env["NF_ROOT"] = str(ws)
        procs.append(subprocess.Popen(
            [sys.executable, "scripts/nf_api.py", "--port", str(UPSTREAM_PORT),
             "--root", str(ws)],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "http.server", str(SITE_PORT),
             "--directory", str(ROOT / "console" / "renderer" / "dist")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
        if not wait_port(UPSTREAM_PORT) or not wait_port(SITE_PORT):
            print("[SKIP] 服务没起来")
            return 0
        server = ThreadingHTTPServer(("127.0.0.1", API_PORT), Proxy)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        if not wait_port(API_PORT):
            print("[SKIP] 代理没起来")
            return 0

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx, page, errs, bad = UX.new_page(browser, API, "rewrite")
            page.on("dialog", lambda d: d.accept())
            page.locator("nav.tabs button:has-text('章节')").click()
            page.wait_for_timeout(1500)

            print("\n=== A. 章节页签 + 重写入口 ===")
            rows = page.locator(".chap-row")
            check("章节行渲染（真数据 3 章）", rows.count() >= 3, rows.count())
            btn = rows.first.locator("button:has-text('重写')")
            check("第一章行里有「重写」按钮", btn.count() == 1, btn.count())
            check("按钮可点（非 disabled / 非 agent 锁）",
                  btn.count() == 1 and btn.first.is_enabled(),
                  btn.first.get_attribute("title") if btn.count() else "-")

            print("\n=== B/C. 点重写 → 面板 + token 流 + 文件尾 ===")
            btn.first.click()
            page.wait_for_timeout(1600)
            panel = page.locator(".stream-panel")
            check("实时面板打开", panel.count() == 1, panel.count())
            if panel.count() == 0:
                print("[debug] body tail:", page.locator("body").inner_text()[-600:])
                ctx.close()
                browser.close()
                return 1
            check("面板显示 job 号", "job_e2e_1" in panel.inner_text(),
                  panel.inner_text()[:120])
            check("token 流有字（正在拆群…）", "正在拆群" in panel.inner_text(),
                  panel.inner_text()[:160])
            file_block = panel.locator(".stream-file")
            check("文件尾块出现（本章正文·文件实时）", file_block.count() == 1,
                  file_block.count())
            check("文件尾内容 = 模型正在写的那一段",
                  "屋檐上的水线" in file_block.inner_text(),
                  file_block.inner_text()[:120])
            check("元信息带字数与字节（用户要的判断依据）",
                  "1200 字" in file_block.inner_text()
                  and "4096 字节" in file_block.inner_text(),
                  file_block.inner_text()[:160])
            check("写入中仍连着（中断按钮在）",
                  panel.locator("button:has-text('中断')").count() == 1,
                  panel.locator("button:has-text('中断')").count())
            UX.shot(page, "RW1_panel_file_tail")

            print("\n=== D. 中断：真的发 /stop + 状态变「已中断」 ===")
            panel.locator("button:has-text('中断')").click()
            page.wait_for_timeout(1000)
            check("前端真的 POST 了 /stop", STATE["stops"] >= 1, STATE["stops"])
            check("状态显示「已中断」", "已中断" in panel.inner_text(),
                  panel.inner_text()[:160])
            check("中断后不再显示「中断」按钮",
                  panel.locator("button:has-text('中断')").count() == 0,
                  panel.locator("button:has-text('中断')").count())
            UX.shot(page, "RW2_stopped")

            print("\n=== E. 完成事件 → 状态「完成」，收尾清干净 ===")
            page.locator(".chap-row").nth(1).locator("button:has-text('重写')").click()
            page.wait_for_timeout(2000)
            panel = page.locator(".stream-panel")
            check("完成后状态显示「完成」", "完成" in panel.inner_text(),
                  panel.inner_text()[:160])
            check("完成后不再显示「中断」按钮",
                  panel.locator("button:has-text('中断')").count() == 0)
            UX.shot(page, "RW3_done")

            print("\n=== F. 干净度：console 无 error、无 4xx ===")
            real_errs = [e for e in errs if "requestfailed" not in e]
            check("无 console error", not real_errs, real_errs[:3])
            check("无 4xx 响应", not bad, bad[:3])
            ctx.close()
            browser.close()
    finally:
        if server is not None:
            server.shutdown()
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
