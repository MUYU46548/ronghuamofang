# -*- coding: utf-8 -*-
"""stage 1 审批门 GUI 冒烟（2026-10-11 工单 P0-1 / P0-2 验收）。

## 覆盖什么

工单前两条的验收口径 —— 现有 e2e_ux_verify **测不到**（它的夹具 1-4 阶段全部
已审批，pendingGates 恒空，门卡片这条路径从未被浏览器点过）：

  P0-1  loadGateArtifacts 的 stage 分支：stage 1 门必须显示
        setting.json + material_review.md（旧 bug：错抓 polish_report.md → 0 行）
  P0-2  gate-files 的 stage 1 预览按钮 ×2：点开要真能看到内容
        （文件桥是本冒烟自备的 /readfile 只读端点，e2e 主脚本明确不加这层）
  附带  stage 2 门不串台（本场景只有 stage1 一张门卡）

## 怎么跑

    python tests/e2e/gate_stage1_smoke.py

自管一切：临时夹具根（fixture 拷贝 + 改写 progress）、nf_api 8796、8091 静态服务
（带 /readfile 白名单文件桥）、Playwright 截图到 Temp/gui_verify/ux/。
缺 playwright / 端口被占 → 干净 SKIP（退出 0，打印原因），不是失败。
**结束只杀自己起的 PID**，不碰用户任何进程。
"""
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
# ⚠️ 持住被替换的旧 stdout 引用：e2e_ux_verify 模块 import 时也会重包装 stdout，
# 若旧 wrapper 被 GC，它会连带 close 共享的底层 buffer → 之后所有 print 报
# "I/O operation on closed file"（本脚本 2026-10-11 首跑实测踩中）。
_STDOUT_HOLD = [sys.stdout]


def _hold_stdout():
    _STDOUT_HOLD.append(sys.stdout)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Temp" / "gui_verify" / "ux"
OUT.mkdir(parents=True, exist_ok=True)
SITE = "http://127.0.0.1:8091"          # 与 e2e_ux_verify.SITE 同端口（互斥不同时跑）
API_BASE = "http://127.0.0.1:8796"       # 本冒烟专用，避开 e2e 的 8797/8798/8799
API_PORT = 8796

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


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
    _hold_stdout()   # e2e 也会重包装 stdout —— 先持住当前（避免 buffer 被 GC 关闭）
    spec = importlib.util.spec_from_file_location(
        "e2e_ux_verify", str(ROOT / "tests" / "e2e" / "e2e_ux_verify.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    # ---- 前置：依赖与端口 ----
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[SKIP] 未装 playwright —— 视觉冒烟跳过（未执行 ≠ 失败）。")
        return 0
    if not (ROOT / "console" / "renderer" / "dist" / "index.html").is_file():
        print("[SKIP] console/renderer/dist 不存在（先 npm run build）。")
        return 0
    busy = [p for p in (8091, API_PORT) if port_busy(p)]
    if busy:
        print("[SKIP] 端口被占 %s（可能正开着控制台/e2e）—— 不抢端口。" % busy)
        return 0

    e2e = load_e2e_module()

    # ---- 夹具根：fixture 拷贝 + 改写成「stage1 完成待审批」 ----
    tmp_root = Path(tempfile.mkdtemp(prefix="nf_gate1_smoke_"))
    fixture = ROOT / "tests" / "e2e" / "fixture_project"
    shutil.copytree(fixture, tmp_root, dirs_exist_ok=True)

    prog_path = tmp_root / "data" / "state" / "progress.json"
    prog = json.loads(prog_path.read_text(encoding="utf-8"))
    prog["current_stage"] = 1
    prog["stages"] = {"1": {"status": "done", "approved": None}}
    for n in range(2, 8):
        prog["stages"][str(n)] = {"status": "pending"}
    prog_path.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")

    # 审批对象两个文件 + 诱饵（旧 bug 抓的就是诱饵）
    setting_text = "{\n  \"characters\": [],\n  \"world\": {},\n  \"marker_setting_json\": true,\n  \"note\": \"gate1 smoke\"\n}"
    review_text = "# 素材体检报告\n\n- 维度1: PASS\n- marker_review_md: true\n"
    decoy_text = "# 润色体检报告（stage6 对象，stage1 门不该出现它）\n"
    (tmp_root / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (tmp_root / "data" / "outline").mkdir(parents=True, exist_ok=True)
    (tmp_root / "data" / "setting" / "setting.json").write_text(setting_text, encoding="utf-8")
    (tmp_root / "data" / "setting" / "material_review.md").write_text(review_text, encoding="utf-8")
    (tmp_root / "data" / "outline" / "polish_report.md").write_text(decoy_text, encoding="utf-8")
    exp_setting_lines = len(setting_text.split("\n"))     # 与前端 split("\n") 同口径
    exp_review_lines = len(review_text.split("\n"))

    # ---- 静态服务：dist + /readfile 白名单文件桥 ----
    dist_dir = ROOT / "console" / "renderer" / "dist"
    real_root = tmp_root.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                          # 静默
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/readfile":
                rel = unquote((parse_qs(u.query).get("path") or [""])[0])
                target = (tmp_root / rel).resolve()
                if not str(target).startswith(str(real_root)) or not target.is_file():
                    self.send_response(404); self.end_headers(); return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            # 静态文件（带简单 mime）
            f = (dist_dir / u.path.lstrip("/")).resolve() if u.path != "/" else (dist_dir / "index.html")
            if u.path == "/":
                f = dist_dir / "index.html"
            if not str(f).startswith(str(dist_dir.resolve())) or not f.is_file():
                self.send_response(404); self.end_headers(); return
            data = f.read_bytes()
            ctype = "text/html; charset=utf-8" if f.suffix in (".html", "") else (
                "application/javascript" if f.suffix == ".js" else (
                    "text/css" if f.suffix == ".css" else "application/octet-stream"))
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 8091), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # ---- nf_api 后端（只杀自己起的） ----
    log_fh = open(OUT / "gate1_smoke_nf_api.log", "wb")
    env = {k: v for k, v in os.environ.items() if k != "NF_ROOT"}
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
                print("[FAIL] nf_api 启动即退，日志：", OUT / "gate1_smoke_nf_api.log")
                return 1
            time.sleep(0.5)
        else:
            print("[FAIL] nf_api 30s 未就绪")
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx, page, errs, bad = e2e.new_page(browser, API_BASE, "gate1")
            # 覆盖 readPreview → 真文件桥（new_page 已注入 e2e 垫片，其 readPreview 恒
            # {ok:false}；必须 reload 才能让覆盖对已加载页面生效）
            ctx.add_init_script("""
window.mofangAPI.readPreview = async (p) => {
  try {
    const r = await fetch('/readfile?path=' + encodeURIComponent(p));
    if (!r.ok) return { ok: false, error: 'smoke bridge: HTTP ' + r.status };
    const t = await r.text();
    return { ok: true, size: t.length, content: t };
  } catch (e) { return { ok: false, error: String(e) }; }
};
""")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            # settle 已由 e2e.new_page 完成（重复调会因弹窗已关再 FAIL 一次）
            page.keyboard.press("0")      # 0 = 收件箱（TAB_ORDER），门卡片只在该页签渲染
            page.wait_for_timeout(600)

            print("【1】stage 1 门卡片与产物概况（P0-1）")
            try:
                page.wait_for_selector(".gate-card", timeout=15000)
            except Exception:                                # noqa: BLE001
                check("门卡片出现", False, "15s 未见 .gate-card")
                raise SystemExit(_finish(browser, page, errs, bad, tmp_root))
            cards = page.locator(".gate-card")
            check("只有一张门卡", cards.count() == 1, cards.count())
            stage_no = page.locator(".gate-card .stage-no").first.inner_text().strip()
            check("门卡是 stage 1", stage_no == "1", stage_no)

            page.wait_for_selector(".gate-artifacts", timeout=8000)
            art_text = page.locator(".gate-artifacts").first.inner_text()
            art_flat = " ".join(art_text.split())
            check("产物概况含 setting.json", "setting.json" in art_text, art_text)
            check("产物概况含 material_review.md", "material_review.md" in art_text, art_text)
            check("产物概况不含 polish_report.md（旧 bug 诱饵）",
                  "polish_report" not in art_text, art_text)
            check("setting.json 行数正确（非 0 行）",
                  "setting.json (%d 行)" % exp_setting_lines in art_flat, art_flat)
            check("material_review.md 行数正确（非 0 行）",
                  "material_review.md (%d 行)" % exp_review_lines in art_flat, art_flat)
            e2e.shot(page, "gate1_smoke_artifacts")

            print("【2】stage 1 预览按钮 ×2 与真内容预览（P0-2）")
            b1 = page.locator(".gate-files button", has_text="设定集原文")
            b2 = page.locator(".gate-files button", has_text="素材体检报告")
            check("「设定集原文」按钮存在", b1.count() == 1, b1.count())
            check("「素材体检报告」按钮存在", b2.count() == 1, b2.count())
            b1.click()
            page.wait_for_selector(".drawer-mask", timeout=8000)
            title = page.locator(".drawer-mask b").first.inner_text()
            check("抽屉标题=setting.json", "setting.json" in title, title)
            body = page.locator(".preview-body").first.inner_text()
            check("抽屉显示真实内容（marker_setting_json）", "marker_setting_json" in body, body[:120])
            e2e.shot(page, "gate1_smoke_drawer")

            print("【3】素材体检报告按钮同样可达")
            page.locator(".drawer-mask button", has_text="关闭").click()
            page.wait_for_timeout(400)
            b2.click()
            page.wait_for_selector(".drawer-mask", timeout=8000)
            body2 = page.locator(".preview-body").first.inner_text()
            check("第二颗按钮显示真实内容（marker_review_md）", "marker_review_md" in body2, body2[:120])
            e2e.shot(page, "gate1_smoke_drawer2")

        return _finish(browser, page, errs, bad, tmp_root)
    finally:
        if browser:
            try:
                browser.close()
            except Exception:                                # noqa: BLE001
                pass
        try:
            api_proc.terminate()
            api_proc.wait(timeout=8)
        except Exception:                                    # noqa: BLE001
            try:
                api_proc.kill()
            except Exception:                                # noqa: BLE001
                pass
        try:
            log_fh.close()
        except OSError:
            pass
        httpd.shutdown()
        shutil.rmtree(tmp_root, ignore_errors=True)


def _finish(browser, page, errs, bad, tmp_root):
    if page:
        perr = [e for e in errs if e.startswith("pageerror")]
        if perr:
            print("  pageerror:", perr[:3])
        other = [e for e in errs if not e.startswith("pageerror")][:6]
        if other:
            print("  console:", other)
        if bad:
            print("  http>=400:", bad[:6])
    print("\n  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   FAIL:", f)
    try:
        shutil.rmtree(tmp_root, ignore_errors=True)
    except Exception:                                        # noqa: BLE001
        pass
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
