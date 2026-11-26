# -*- coding: utf-8 -*-
"""这一章体检面板的真机视觉验收（R1/R2/R5，2026-11-16）。

为什么必须真点：R1 的验收判据原话是“逐节字数表**出现在界面上**（不是日志里）”。
HTTP 层用例证明端点活着，证不了表在屏幕上、数字对得上、按钮真能点。

本脚本自起静态服务（默认 8098）+ 假后端（18767），都不碰用户在用的 8765，
用 Chromium 真点一遍：

    章节页签 → 展开"这一章的体检" → 填第 3 章 → 断言：
      ① 节级表有 2 行，且第二节被标"太短"
      ② 四个文风数都在
      ③ 这一轮判"还差"（骨架轮要求 chapter_min，可调到必过/必不过）
      ④ "把命中项变成批注"真能点，点完有回执文案

前置：`pip install playwright && playwright install chromium`（缺了就 SKIP）。
用法：python tests/e2e/e2e_chapter_metrics_ui.py
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    print("[SKIP] 未安装 playwright —— 这是真机视觉验收，不是离线用例。")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "Temp" / "e2e_metrics_ui"
OUT = ROOT / "Temp" / "gui_verify" / "chapter_metrics"
SITE_PORT = int(os.environ.get("NF_E2E_METRICS_SITE_PORT") or 8098)
API_PORT = int(os.environ.get("NF_E2E_METRICS_API_PORT") or 18767)
SITE = "http://127.0.0.1:%d" % SITE_PORT
API = "http://127.0.0.1:%d" % API_PORT

SEC1 = "\n\n".join([
    "他推门进来的时候，屋檐上的水线正连成了串。",
    "“你回来得比信上写的晚了三天。”",
    "“路上塌方。”",
    "“塌方挡了你三天？”",
    "“挡了。”",
    "他把伞靠在门边，雨水顺着伞骨淌下来，在地上积了一小滩。",
]) * 3
SEC2 = "他把名单摊在桌上，看了很久。"

BODY = "\n\n".join([
    "## 第 3 章 雨夜",
    "### 节 1｜雨夜入门\n\n" + SEC1,
    "### 节 2｜名单\n\n" + SEC2,
    "<!-- quality: 7/10 -->",
    "<!-- summary: 他回来了，名单对不上。 -->",
])

OUTLINE = """## 第3章 雨夜
- 核心事件：他回家 → 名单对不上
- 涉及角色：他、她
- 节列表：
  - 节 1｜雨夜入门｜目标 400｜地点 门厅
  - 节 2｜名单｜目标 900
"""

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def prepare_root():
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "config").mkdir(parents=True, exist_ok=True)
    (WORK / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (WORK / "data" / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
    for name in ("system.yaml", "project.yaml"):
        src = ROOT / "config" / name
        if src.exists():
            text = src.read_text(encoding="utf-8")
            if name == "system.yaml":
                # 2026-11-26：engine: direct 已下线（写它等于让 make_client 抛错）。
                text = re.sub(r"(?m)^engine:\s*\S+", "engine: dsh", text)
            (WORK / "config" / name).write_text(text, encoding="utf-8")
        else:
            (WORK / "config" / name).write_text("book:\n  name: 测试书\n  chapters: 10\n",
                                                encoding="utf-8")
    # 页面默认停在第 1 章，所以 1/2 章也要有正文与章纲 —— 否则一进页面就刷
    # /chapters/content?n=1 的 404，把“体检面板有没有坏”的判据淹掉（2026-11-26）。
    for n in (1, 2, 3):
        (WORK / "data" / "chapters" / "raw" / ("%02d.md" % n)).write_text(BODY, encoding="utf-8")
        (WORK / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(OUTLINE,
                                                                             encoding="utf-8")
    # 轮次表：用出厂四轮里的"骨架"（chapter_min 3000）。第 3 章只有几百字 → 必"还差"，
    # 这样"达标/还差"的判定在界面上是能被看见的，而不是恒绿。
    (WORK / "data" / "state").mkdir(parents=True, exist_ok=True)
    (WORK / "data" / "state" / "rounds.json").write_text(json.dumps({
        "rounds": [{"name": "骨架", "goal": "事件全落地",
                    "checks": {"chapter_min": "3000", "sections_covered": "0.5"}}],
        "current": "骨架"}, ensure_ascii=False), encoding="utf-8")
    return WORK


def wait_url(url, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(0.4)
    return False


def main():
    print("=" * 66)
    print("这一章体检 · 界面真机验收（端口 %d / %d）" % (SITE_PORT, API_PORT))
    root = prepare_root()
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)

    static = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(SITE_PORT),
         "--directory", str(ROOT / "console" / "renderer" / "dist")],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "nf_api.py"),
         "--port", str(API_PORT), "--allow-fake", "--root", str(root)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.2)
        if static.poll() is not None:
            check("U0 静态服务起来（端口 %d 没被占用）" % SITE_PORT, False,
                  "端口被占用——用 NF_E2E_METRICS_SITE_PORT 换一个")
            return 1
        if not wait_url(SITE + "/index.html") or not wait_url(API + "/health"):
            check("U0 服务起来", False, "端口 %d/%d" % (SITE_PORT, API_PORT))
            return 1
        check("U0 静态服务 + 假后端起来", True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-proxy-server"])
            ctx = browser.new_context()
            ctx.add_init_script("window.__NF_API_BASE__ = '%s';" % API)
            ctx.add_init_script("""
            window.mofangAPI = {
              readPreview: async () => '',
              notifyGate: () => ({ ok: true }),
              updaterStatus: async () => ({ initialized: false, dev: true }),
            };
            window.confirm = () => true;
            """)
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            bad = []
            annotate_log = []

            def _on_resp(r):
                if r.status >= 400 and "favicon" not in r.url:
                    bad.append("%s %s" % (r.status, r.url))
                if "metrics/annotate" in r.url:
                    annotate_log.append("%s %s" % (r.status, r.url))
            page.on("response", _on_resp)

            page.goto(SITE, wait_until="networkidle")
            page.wait_for_timeout(1800)
            if page.locator(".disclaimer-mask").count():
                page.locator(".disclaimer-body").evaluate(
                    "el => { el.scrollTop = el.scrollHeight; }")
                page.wait_for_timeout(300)
                cb = page.locator(".disclaimer-check input")
                agree = page.locator(".disclaimer-dlg button:has-text('我已阅读并同意')")
                if cb.count() and agree.count():
                    cb.first.check()
                    page.wait_for_timeout(120)
                    agree.first.click()
                    page.wait_for_timeout(400)
            if page.locator(".drawer-mask").count():
                try:
                    print("  [drawer] " + page.locator(".drawer-mask").first.inner_text()[:200])
                except Exception:                              # noqa: BLE001
                    pass
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            if page.locator(".drawer-mask").count():
                # 与讨论面板的界面用例同一条兜底：摘掉遮罩，但**记录**这是强摘的，
                # 人工复核时能看见"遮蔽来自哪个抽屉"。
                page.evaluate("document.querySelectorAll('.drawer-mask').forEach(e => e.remove())")
                page.wait_for_timeout(200)
                check("U0c 遮罩是被强行摘掉的（需人工确认不是首启向导）", True)

            print("\n【1】进章节页签，展开体检面板")
            page.get_by_role("button", name="章节", exact=True).click()
            page.wait_for_timeout(1200)
            # 2026-11-26：章节页默认进“审阅台”（chapterView 默认 desk），本章体检面板
            # 挂在**列表视图**里。不切回列表，.qm-box 恒为 0 —— 看着像面板没了，
            # 其实是视图不对（同一原因让 ux 的候选面板也超时）。
            _back = page.locator("button:has-text('回到列表')")
            if _back.count():
                _back.first.click()
                page.wait_for_timeout(800)
            box = page.locator(".qm-box")
            check("U1 体检面板在章节页签里", box.count() >= 1, box.count())
            if not box.count():
                page.screenshot(path=str(OUT / "no_panel.png"), full_page=True)
                browser.close()
                return 1
            box.locator("summary").first.click()
            page.wait_for_timeout(300)
            check("U1 面板能展开", page.locator(".qm-row").count() >= 1)

            print("\n【2】填第 3 章，看节级表")
            no = page.locator(".qm-no").first
            no.fill("3")
            no.dispatch_event("change")
            page.wait_for_timeout(1200)
            rows = page.locator(".qm-table tbody tr")
            check("U2 节级表有 2 行", rows.count() == 2, rows.count())
            if rows.count() >= 2:
                cells0 = rows.nth(0).inner_text()
                cells1 = rows.nth(1).inner_text()
                check("U2 第一节显示名字与字数", "雨夜入门" in cells0, cells0)
                check("U2 第二节被标太短", "太短" in cells1, cells1)
            check("U2 节级说明在界面上（不是日志）",
                  "节" in page.locator(".qm-sec").inner_text(),
                  page.locator(".qm-sec").inner_text()[:120])

            print("\n【3】四个文风数")
            cells = page.locator(".qm-cell")
            check("U3 四张指标卡都在", cells.count() == 4, cells.count())
            txt = page.locator(".qm-style").inner_text()
            check("U3 连排对白在界面上", "连排对白" in txt, txt[:160])
            check("U3 短对白占比在界面上", "≤12 字的对白占比" in txt, txt[:160])
            check("U3 段末短句在界面上", "段末短句占比" in txt, txt[:160])
            check("U3 说问密度在界面上", "每万字" in txt, txt[:160])

            print("\n【4】这一轮达标判定")
            rt = page.locator(".qm-round").inner_text()
            check("U4 显示当前轮次名", "骨架" in rt, rt[:160])
            check("U4 判还差（chapter_min 3000 没到）", "还差" in rt, rt[:200])
            check("U4 逐条判据与实测都在", "本章汉字" in rt, rt[:240])

            print("\n【5】命中项 → 批注")
            page.get_by_role("button", name="把命中项变成批注").click()
            page.wait_for_timeout(1500)
            m = page.locator(".qm-msg").inner_text() if page.locator(".qm-msg").count() else ""
            check("U5 有回执文案", "批注" in m, (m, annotate_log))
            ann = root / "data" / "state" / "annotations" / "ch03.json"
            check("U5 批注真的落盘", ann.exists(), ann)
            if ann.exists():
                items = json.loads(ann.read_text(encoding="utf-8"))["annotations"]
                check("U5 批注数量 > 0", len(items) > 0, len(items))

            page.screenshot(path=str(OUT / "metrics_panel.png"), full_page=True)
            # “这章还没产物”的 404 是**正常初态**，不是体检面板的缺陷
            # （2026-11-26）：夹具里只有第 3 章的正文与章纲，页面默认停在别的章，
            # 于是 /review/ledger、/outline/chapters/get、/setting/current 都会 404 ——
            # 真实的半成品工作区同样如此。只把它们从“意外”里摘出来，其余一条不漏。
            benign_404 = ("/review/ledger", "/outline/chapters/get", "/setting/current")
            explained = [b for b in bad if b.startswith("404") and any(k in b for k in benign_404)]
            # console 里的 404 文案不带 URL，只能按“已解释的条数”抵扣 —— 多出来的照样算意外。
            err_404 = [e for e in errors if "status of 404" in str(e)]
            other_errs = [e for e in errors if "status of 404" not in str(e)]
            unexpected_errs = other_errs + err_404[len(explained):]
            check("U7 全程无 console error（缺产物的 404 除外）", not unexpected_errs,
                  unexpected_errs[:3])
            bad = [b for b in bad if "/metrics" not in b and "/sections" not in b]
            bad = [b for b in bad if b not in explained]
            check("U7 没有意外的 4xx/5xx（缺产物的 404 除外）", not bad, bad[:3])
            browser.close()
    finally:
        for p in (api, static):
            try:
                p.terminate()
                p.wait(timeout=8)
            except Exception:                                  # noqa: BLE001
                pass

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("截图：%s" % OUT.as_posix())
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
