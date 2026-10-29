# -*- coding: utf-8 -*-
"""样例稿（范文）使用声明真机验收（0.7.16）：没同意不写盘 → 同意才保存 → 撤回可见。

## 钉什么（用户原话：客户端里放样例稿的地方，免责声明写了没）

A. 文风页签里有「范文正文」框，且明确标着「保存前需确认样稿使用声明」
B. 填正文点保存 → 先弹声明框（不弹 = 静默同意，FAIL），**默认焦点在「我不同意」**
C. 点「我不同意」→ 弹层关闭、盘上一个字节都没写
D. 再点保存 → 点「我同意并保存」→ 文件落盘 + 同意记录落盘 + 状态转「已同意」
E. 刷新页面 → 仍显示已同意（同意记录在后端，不是 localStorage 假象）
F. 无 console error / 无 4xx

零 token：整条链不调模型。用法：python tests/e2e/e2e_sample_consent.py
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

API_PORT = 8807
SITE_PORT = 8095
API = "http://127.0.0.1:%d" % API_PORT
SITE = "http://127.0.0.1:%d" % SITE_PORT
UX.SITE = SITE
OUT = ROOT / "Temp" / "gui_verify" / "sample_consent"
OUT.mkdir(parents=True, exist_ok=True)
UX.OUT = OUT

PASS, FAIL = [], []

SAMPLE = (
    "他把门带上，屋里只剩炉子上的一线白汽。\n\n"
    "「你回来得比信上写的早。」她说这话时没抬头，手里的锥子照旧扎进鞋底。\n\n"
    "他站着没动，靴子上的雪化成水，沿着砖缝往低处走。过了很久才说：「路上没停。」\n\n"
    "她没有问为什么。锥子穿过去，麻线抽紧，发出很轻的一声。窗外的风把整条街的招牌都吹得响，"
    "只有这一间铺子像是没听见。\n\n"
    "「锅里还有半碗。」她终于抬头，看了他一眼，又低下去，「凉了你自己热。」\n"
) * 3


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def build_workspace():
    ws = Path(tempfile.mkdtemp(prefix="nf_sample_e2e_"))
    shutil.copytree(ROOT / "config", ws / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("data/state", "data/setting", "materials/raw"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    (ws / "config" / "project.yaml").write_text(
        'book:\n  name: "样例稿声明验收书"\n  chapters: 3\n  style_reference: ""\n',
        encoding="utf-8")
    # 文风面板挂载时会自动分析（未配置范文则回落到「第 N 章」）——
    # 没有章节文件就会刷一串 400，把「无 4xx」判据变成噪音。这里给三章正经正文。
    for i in range(1, 4):
        (ws / "data" / "chapters" / "raw" / ("%02d.md" % i)).parent.mkdir(
            parents=True, exist_ok=True)
        (ws / "data" / "chapters" / "raw" / ("%02d.md" % i)).write_text(
            "## 第%d章\n\n" % i
            + "他把门带上，屋里只剩炉子上的一线白汽。她说这话时没抬头，手里的锥子照旧扎进鞋底。"
              * 20, encoding="utf-8")
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

        sample_file = ws / "materials" / "style_sample.md"
        consent_file = ws / "data" / "state" / "style_sample_consent.json"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx, page, errs, bad = UX.new_page(browser, API, "sample_consent")
            page.locator("nav.tabs button:has-text('文风')").click()
            page.wait_for_timeout(1200)

            print("\n=== A. 入口与声明提示 ===")
            box = page.locator(".card").filter(has_text="范文正文")
            check("文风页签里有「范文正文」框", box.count() >= 1, box.count())
            if box.count() == 0:
                ctx.close()
                browser.close()
                return 1
            text0 = box.first.inner_text()
            check("框上写着「保存前需确认样稿使用声明」",
                  "样稿使用声明" in text0, text0[:160])

            print("\n=== B/C. 保存先弹声明；「我不同意」= 一个字都不写 ===")
            box.first.locator("textarea.style-ref-input").fill(SAMPLE)
            box.first.locator("button:has-text('保存')").first.click()
            page.wait_for_timeout(500)
            dlg = page.locator(".dialog[aria-label='样例稿使用声明']")
            check("弹出了声明框（不是静默同意）", dlg.count() == 1, dlg.count())
            if dlg.count() != 1:
                ctx.close()
                browser.close()
                return 1
            focus_txt = page.evaluate(
                "() => (document.activeElement && document.activeElement.textContent || '').trim()")
            check("默认焦点在「我不同意」", "不同意" in focus_txt, focus_txt[:20])
            UX.shot(page, "SC1_notice")
            dlg.locator("button:has-text('我不同意')").click()
            page.wait_for_timeout(400)
            check("点不同意后弹层关闭", page.locator(".dialog[aria-label='样例稿使用声明']").count() == 0)
            check("不同意的那个字节都没落盘", not sample_file.exists())
            check("同意记录也没写", not consent_file.exists())

            print("\n=== D. 同意并保存 → 落盘 + 记录 ===")
            box.first.locator("button:has-text('保存')").first.click()
            page.wait_for_timeout(400)
            page.locator(".dialog[aria-label='样例稿使用声明'] button:has-text('我同意并保存')").click()
            page.wait_for_timeout(900)
            check("范文文件落盘", sample_file.exists())
            check("同意记录落盘（data/state/style_sample_consent.json）", consent_file.exists())
            if consent_file.exists():
                d = json.loads(consent_file.read_text(encoding="utf-8"))
                check("记录里 agreed=true 且带时间", d.get("agreed") is True and bool(d.get("at")), d)
            body = box.first.inner_text()
            check("状态转「已同意样例稿使用声明」", "已同意样例稿使用声明" in body, body[:200])
            check("字数口径回显「已生效」", "已生效" in body, body[:200])
            UX.shot(page, "SC2_saved")

            print("\n=== E. 刷新后仍是已同意（记录在后端） ===")
            page.reload()
            page.wait_for_timeout(1200)
            page.locator("nav.tabs button:has-text('文风')").click()
            page.wait_for_timeout(800)
            body2 = page.locator(".card").filter(has_text="范文正文").first.inner_text()
            check("刷新后仍显示已同意", "已同意样例稿使用声明" in body2, body2[:200])
            check("刷新后正文还在", bool(
                page.locator(".card").filter(has_text="范文正文").first
                .locator("textarea.style-ref-input").input_value().strip()))

            print("\n=== F. 干净度 ===")
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
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for n in FAIL:
        print("    -", n)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
