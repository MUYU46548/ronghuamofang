# -*- coding: utf-8 -*-
"""知识库扫描面 GUI 视觉验收（自管服务，不依赖外部 8091/8798）。

验收的是**真链路**而不是静态截图：真 nf_api（--root 临时根）+ 真 vault 夹具
→ 页面读扫描面 → 勾掉一个目录 → 保存 → 界面立刻反映新排除态。

为什么单独一个脚本：e2e_ux_verify 需要外部先起服务，而这个面板的验收点是
「目录名取自用户库、勾选真的写进配置」—— 用临时根夹具能把这条链路钉死，
且不碰用户真实配置。

用法：python tests/e2e/e2e_vault_scan_shot.py
缺 playwright / 构建产物时干净 SKIP（退出码 0）。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] 未安装 playwright")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify"
OUT.mkdir(parents=True, exist_ok=True)
SITE_PORT, API_PORT = 8092, 8799
SITE = "http://127.0.0.1:%d" % SITE_PORT
API = "http://127.0.0.1:%d" % API_PORT
SHOT = OUT / "vault_scan_panel.png"

PASS, FAIL = [], []


def check(name, ok, extra=""):
    if ok:
        PASS.append(name)
        print("  [PASS] %s" % name)
    else:
        FAIL.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def port_busy(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def build_fixture_root():
    """临时项目根 + 一个**结构完全自定**的假 vault。

    刻意用与任何真实库都不同的目录名 —— 本用例的判据是「界面列出你库里的目录」，
    所以夹具里出现任何具体库的目录名都会把判据测歪。
    """
    root = Path(tempfile.mkdtemp(prefix="nf_vscan_gui_"))
    (root / "config").mkdir()
    (root / "config" / "system.yaml").write_text(
        "obsidian:\n  vault_path: ''\n  sandbox_dir: ''\n", encoding="utf-8")
    v = root / "夹具知识库"
    for d, n in (("正典区", 4), ("参考区", 3), ("草稿区", 2)):
        (v / d).mkdir(parents=True)
        for i in range(n):
            (v / d / ("词条%d.md" % i)).write_text(
                "# 词条%d\n这是一条足够长的设定正文，用来通过空骨架页判据。\n" % i,
                encoding="utf-8")
    (root / "config" / "system.local.yaml").write_text(
        "obsidian:\n  vault_path: '%s'\n  skip_dirs: ['草稿区']\n"
        % str(v).replace("\\", "/"), encoding="utf-8")
    return root


def main():
    if not (ROOT / "console" / "renderer" / "dist" / "index.html").is_file():
        print("[SKIP] console/renderer/dist 不存在（先 npm run build）。")
        return 0

    tmp_root = build_fixture_root()
    dist_dir = ROOT / "console" / "renderer" / "dist"
    dres = dist_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            f = dist_dir / u.path.lstrip("/") if u.path != "/" else (dist_dir / "index.html")
            if u.path == "/":
                f = dist_dir / "index.html"
            if not str(f.resolve()).startswith(str(dres)) or not f.is_file():
                self.send_response(404); self.end_headers(); return
            data = f.read_bytes()
            ctype = ("text/html; charset=utf-8" if f.suffix in (".html", "") else
                     ("application/javascript" if f.suffix == ".js" else
                      ("text/css" if f.suffix == ".css" else "application/octet-stream")))
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", SITE_PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    log_fh = open(OUT / "vscan_nf_api.log", "wb")
    env = {k: val for k, val in os.environ.items() if k != "NF_ROOT"}
    api_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "nf_api.py"),
         "--port", str(API_PORT), "--root", str(tmp_root)],
        stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env)

    browser = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if port_busy(API_PORT):
                break
            if api_proc.poll() is not None:
                print("[FAIL] nf_api 启动即退，日志：", OUT / "vscan_nf_api.log")
                return 1
            time.sleep(0.5)
        else:
            print("[FAIL] nf_api 30s 未就绪")
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
            ctx = browser.new_context()
            ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(API))
            page = ctx.new_page()
            page.goto(SITE, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)

            # 首启免责门：走真实放行流（不注入 localStorage 绕过）
            if page.locator(".disclaimer-mask").count():
                page.locator(".disclaimer-body").evaluate(
                    "el => { el.scrollTop = el.scrollHeight; }")
                page.wait_for_timeout(350)
                cb = page.locator(".disclaimer-check input")
                agree = page.locator(".disclaimer-dlg button:has-text('我已阅读并同意')")
                if cb.count() and agree.count():
                    cb.first.check()
                    page.wait_for_timeout(150)
                    agree.first.click()
                    page.wait_for_timeout(450)
            check("免责声明已放行（否则面板点不到）",
                  page.locator(".disclaimer-mask").count() == 0)

            # 进设置 → 风格与提示词
            page.click("text=设置")
            page.wait_for_timeout(400)
            page.click("text=风格与提示词")
            page.wait_for_timeout(1200)

            body = page.inner_text("body")
            check("面板标题渲染", "知识库扫描" in body)
            check("列出库里的目录（界面数据来自用户库本身）",
                  all(n in body for n in ("正典区", "参考区", "草稿区")),
                  "→ " + body[body.find("知识库扫描"):][:200])
            check("显示篇数与进入检索的口径", "进入正典检索" in body)

            rows = page.query_selector_all(".vault-dir-row")
            check("目录行渲染（3 个子目录）", len(rows) == 3, "→ %d 行" % len(rows))
            excluded_rows = page.query_selector_all(".vault-dir-row.is-excluded")
            check("勾选态与后端排除态一致（初始排除草稿区由用户配置决定）",
                  any("草稿区" in (r.inner_text() or "") for r in excluded_rows),
                  "→ %s" % [r.inner_text() for r in excluded_rows])

            page.screenshot(path=str(SHOT), full_page=True)
            print("  截图:", SHOT)

            # 勾掉「参考区」→ 保存 → 界面立刻反映（真链路，不是静态渲染）
            row = page.query_selector(".vault-dir-row:has-text('参考区')")
            check("找到参考区行", row is not None)
            if row:
                row.query_selector("input[type=checkbox]").click()
                page.wait_for_timeout(200)
                page.click("text=保存排除项")
                page.wait_for_timeout(1500)
                body2 = page.inner_text("body")
                rows2 = page.query_selector_all(".vault-dir-row.is-excluded")
                check("保存后界面反映新排除态（参考区 + 草稿区）",
                      len(rows2) == 2 and "参考区" in " ".join(r.inner_text() for r in rows2),
                      "→ %s" % [r.inner_text() for r in rows2])
                # inner_text 里 <b> 前后可能带换行，用正则而不是裸子串比对
                import re as _re
                check("进入检索的篇数随之减少（9 篇里 7 → 4）",
                      bool(_re.search(r"4\s*篇进入正典检索", body2)),
                      "→ " + (body2[body2.find("进入正典检索") - 30:][:80]
                              if "进入正典检索" in body2 else "(没找到该口径行)"))
                page.screenshot(path=str(OUT / "vault_scan_panel_after.png"), full_page=True)

            # 坏入参（路径穿越）后端必须 400 —— 界面只回显，不自己放行
            import urllib.request
            import urllib.error
            req = urllib.request.Request(
                API + "/config/vault_skip_dirs",
                data=json.dumps({"exclude": ["../etc"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=10)
                check("路径穿越入参被拒", False, "→ 竟然 200")
            except urllib.error.HTTPError as e:
                check("路径穿越入参被拒（400）", e.code == 400, "→ %d" % e.code)

            page.close()
            ctx.close()
    finally:
        # 每一步都单独兜底：任何一步炸掉都不许让后面的清理跳过 ——
        # 上一轮就是 browser.close() 在 event loop 已关时抛异常，
        # 导致 api_proc 没被 terminate，8797 上留了个孤儿 nf_api。
        try:
            if browser:
                browser.close()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            api_proc.terminate()
            api_proc.wait(timeout=10)
        except Exception:                                  # noqa: BLE001
            try:
                api_proc.kill()
            except Exception:                              # noqa: BLE001
                pass
        try:
            httpd.shutdown()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            log_fh.close()
        except Exception:                                  # noqa: BLE001
            pass
        shutil.rmtree(tmp_root, ignore_errors=True)

    print("\n" + "=" * 60)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 60)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
