# -*- coding: utf-8 -*-
"""README 截图一条命令 —— docs/screenshots/*.png 的唯一生成入口。

为什么要有：README 三图（流水线主界面 / 关于页面 / 设置 · Word 模板）此前靠手动截图，
界面一改就过期（2026-10-12 欠账第一周第 2 件「截图命令化」）。本脚本自管一切：

    ./.venv/Scripts/python.exe tests/e2e/make_readme_shots.py

自管：临时夹具根（fixture 拷贝 + templates/ 播种 + 书名改「示例长篇」）、
nf_api 8795、8091 静态服务、Playwright headless Chromium
（1440x940 视口、deviceScaleFactor=2 → 2880x1880，与既有三图同尺寸）。

纪律（与 gate_stage1_smoke 同款）：

- 缺 playwright / 缺 renderer dist / 端口被占 → 干净 SKIP（退出 0 + 打印原因），
  **未执行 ≠ 失败**；
- 断言失败（选择器找不到 / 面板没渲染 / 模板没进下拉）→ 退出 1，说清哪一步；
- **只杀自己起的 PID**，不碰用户任何进程（用户可能正开着控制台）；
- 图直接写 --out（默认 docs/screenshots/），不落 Temp。

夹具为什么这样搭：fixture_project 的 progress.json 天生就是「1-4 已完成、阶段5 待运行」
—— 与既有 README 截图状态一致；书名在临时根里改，不动仓库夹具本体。
"""
import argparse
import importlib.util
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
# ⚠️ 持住被替换的旧 stdout 引用：e2e_ux_verify import 时也会重包装 stdout，
# 旧 wrapper 被 GC 会连带 close 共享 buffer → 之后 print 全炸（gate_stage1_smoke
# 2026-10-11 首跑实测踩中）。别删这两行。
_STDOUT_HOLD = [sys.stdout]

ROOT = Path(__file__).resolve().parents[2]
SITE_PORT = 8091                      # 与 e2e_ux_verify / gate_stage1_smoke 同端口（互斥不同时跑）
API_PORT = 8795                       # 本脚本专用，避开 8796(smoke)/8797(cold)/8798(mock)/8799(real)
SITE = "http://127.0.0.1:%d" % SITE_PORT
API_BASE = "http://127.0.0.1:%d" % API_PORT
DIST = ROOT / "console" / "renderer" / "dist"
LOG = ROOT / "Temp" / "gui_verify" / "readme_shots_nf_api.log"

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png",
        ".map": "application/json", ".woff2": "font/woff2"}


def skip(msg):
    print("[SKIP] %s —— 未执行 ≠ 失败。" % msg)
    return 0


def port_busy(port):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def load_e2e_module():
    """加载兄弟模块 e2e_ux_verify（PRELOAD_SHIM_JS + settle_disclaimer 的单一真源）。"""
    _STDOUT_HOLD.append(sys.stdout)
    spec = importlib.util.spec_from_file_location(
        "e2e_ux_verify", str(ROOT / "tests" / "e2e" / "e2e_ux_verify.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _STDOUT_HOLD.append(sys.stdout)
    return mod


def png_dims(path):
    d = Path(path).read_bytes()[:33]
    return struct.unpack(">II", d[16:24])


def prepare_root():
    """临时夹具根：fixture 拷贝 + 模板播种 + 书名改「示例长篇」。

    替换全部带护栏（找不到原文 = 夹具漂移 → 大声失败，不静默截出一张错图）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="nf_readme_shots_"))
    shutil.copytree(ROOT / "tests" / "e2e" / "fixture_project", tmp, dirs_exist_ok=True)

    # templates/ 播种：README 的「设置 · Word 模板」图要有真模板可选
    tpl_src = sorted((ROOT / "templates").glob("*.dotx")) + \
        sorted((ROOT / "templates").glob("*.docx"))
    if not tpl_src:
        raise RuntimeError("仓库 templates/ 里没有 .dotx/.docx —— 模板播种无源")
    (tmp / "templates").mkdir(exist_ok=True)
    for f in tpl_src:
        shutil.copy2(f, tmp / "templates" / f.name)
    tpl_name = tpl_src[0].name

    proj = tmp / "config" / "project.yaml"
    text = proj.read_text(encoding="utf-8")
    repl = [('name: "e2e 夹具书"', 'name: "示例长篇"'),
            ('genre: "测试"', 'genre: "奇幻"'),
            ('word_template: ""', 'word_template: "templates/%s"' % tpl_name)]
    for old, new in repl:
        if old not in text:
            raise RuntimeError("夹具漂移：project.yaml 找不到 %r（fixture 变了？先改本脚本）" % old)
        text = text.replace(old, new, 1)
    proj.write_text(text, encoding="utf-8")
    return tmp, tpl_name


def shot(page, path, name):
    page.screenshot(path=str(path))
    w, h = png_dims(path)
    print("  shot → %s  (%dx%d, %d bytes)" % (name, w, h, path.stat().st_size))


def run(tmp_root, out_dir, tpl_name, e2e):
    from playwright.sync_api import sync_playwright
    failures = []

    def must(cond, what):
        if not cond:
            failures.append(what)
            print("  [FAIL] %s" % what)
        else:
            print("  [ok] %s" % what)
        return cond

    # ---- 静态服务（dist + 简单 mime；路径穿越防护同 smoke）----
    real_dist = DIST.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            f = (DIST / "index.html") if u.path == "/" else (DIST / u.path.lstrip("/"))
            f = f.resolve()
            if not str(f).startswith(str(real_dist)) or not f.is_file():
                self.send_response(404)
                self.end_headers()
                return
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(f.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", SITE_PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # ---- nf_api 后端（只杀自己起的）----
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(LOG, "wb")
    env = {k: v for k, v in os.environ.items() if k != "NF_ROOT"}
    api_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "nf_api.py"),
         "--port", str(API_PORT), "--root", str(tmp_root)],
        stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env)

    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if port_busy(API_PORT):
                break
            if api_proc.poll() is not None:
                print("[FAIL] nf_api 启动即退，日志：%s" % LOG)
                return 1
            time.sleep(0.5)
        else:
            print("[FAIL] nf_api 30s 未就绪，日志：%s" % LOG)
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 940},
                                      device_scale_factor=2)
            ctx.add_init_script("window.__NF_API_BASE__ = %s;" % json.dumps(API_BASE))
            ctx.add_init_script(e2e.PRELOAD_SHIM_JS)
            page = ctx.new_page()
            errs = []
            page.on("console", lambda m: errs.append(m.type + ": " + m.text[:160])
                    if m.type == "error" else None)
            page.goto(SITE, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            e2e.settle_disclaimer(page)   # 首启强制免责声明：全新 profile 必弹，先放行

            # ---- 1) 流水线主界面 ----
            print("--- pipeline.png ---")
            page.locator(".tabs button:has-text('流水线')").click()
            page.wait_for_timeout(900)
            must(page.locator(".flow-strip").count() == 1,
                 "一键工作流条渲染（.flow-strip）")
            must(page.locator(".ps-item").count() >= 1,
                 "流水线条状态项可见（.ps-item）")
            shot(page, out_dir / "pipeline.png", "pipeline.png")

            # ---- 2) 关于页面（弹在流水线页上，与既有图同构）----
            print("--- about.png ---")
            page.locator(".brand-btn").click()
            page.wait_for_timeout(1400)
            must(page.locator(".about-dlg").count() == 1, "关于弹窗渲染（.about-dlg）")
            ver = page.locator(".about-ver").inner_text() if page.locator(".about-ver").count() else ""
            must(ver.strip() != "", "版本号可见（.about-ver → %r）" % ver.strip())
            shot(page, out_dir / "about.png", "about.png")
            close = page.locator(".about-dlg .mini.primary")
            if close.count() == 1:
                close.click()
            else:
                page.locator(".about-dlg button:has-text('关闭')").last.click()
            page.wait_for_timeout(500)
            must(page.locator(".about-dlg").count() == 0, "关于弹窗可关闭")

            # ---- 3) 设置 · Word 模板 ----
            print("--- settings-word-template.png ---")
            page.locator(".tabs button:has-text('设置')").click()
            page.wait_for_timeout(700)
            nav = page.locator(".set-nav-btn", has_text="Word 模板")
            must(nav.count() == 1, "侧边栏「Word 模板」入口存在（count=%d）" % nav.count())
            if nav.count() == 1:
                nav.click()
                page.wait_for_timeout(600)
                wpane = page.locator(".set-pane", has_text="Word 成品模板（templates/")
                must(wpane.count() == 1, "Word 模板面板渲染（.set-pane）")
                if wpane.count() == 1:
                    opts = wpane.locator("select.model-select option")
                    texts = opts.all_inner_texts() if opts.count() else []
                    must(any(tpl_name in t for t in texts),
                         "下拉列出播种模板 %s（%d 项：%s）" % (
                             tpl_name, opts.count(), " / ".join(texts)[:120]))
                shot(page, out_dir / "settings-word-template.png",
                     "settings-word-template.png")

            if errs:
                print("  [warn] console error %d 条（仅提示）：" % len(errs))
                for e in errs[:5]:
                    print("    " + e)
            browser.close()

        if failures:
            print("[FAIL] %d 项断言未过：%s" % (len(failures), "；".join(failures)))
            return 1
        print("[OK] 三张截图全部再生成 → %s" % out_dir)
        return 0
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        if api_proc.poll() is None:
            api_proc.terminate()
            try:
                api_proc.wait(timeout=5)
            except Exception:
                api_proc.kill()
        log_fh.close()


def main():
    ap = argparse.ArgumentParser(description="README 截图一条命令（docs/screenshots/*.png）")
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"),
                    help="输出目录（默认 docs/screenshots）")
    ap.add_argument("--keep-root", action="store_true", help="保留临时夹具根（排障用）")
    args = ap.parse_args()
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return skip("未装 playwright —— 启用：./.venv/Scripts/python.exe -m pip install playwright "
                    "&& playwright install chromium")
    if not (DIST / "index.html").is_file():
        return skip("console/renderer/dist 不存在 —— 先 cd console && npm run build")
    busy = [p for p in (SITE_PORT, API_PORT) if port_busy(p)]
    if busy:
        return skip("端口被占 %s（可能正开着控制台/其他 e2e）—— 不抢端口" % busy)

    e2e = load_e2e_module()
    tmp_root, tpl_name = prepare_root()
    print("[shots] 夹具根 %s（模板 %s）" % (tmp_root, tpl_name))
    try:
        return run(tmp_root, out_dir, tpl_name, e2e)
    finally:
        if args.keep_root:
            print("[shots] 临时根保留：%s" % tmp_root)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
