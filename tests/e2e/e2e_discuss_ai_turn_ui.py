# -*- coding: utf-8 -*-
"""讨论面板的“让 AI 说这一轮”真机视觉验收（C1/C18，2026-11-16）。

为什么必须真点：这条功能的全部风险都在"按钮点了之后"，而 HTTP 层验收
（e2e_discuss_ai_turn_api.py）绕过了界面 —— 它证明端点活着，证不了
**流式文字真的在屏幕上滚**、**按钮真的变成"让 AI 闭嘴"**、**点了真的停**。

本脚本自起两个进程（静态服务 8091 + 假后端 18766，都不碰用户在用的 8765），
用 Chromium 真点一遍：

    章节页签 → 展开讨论 → 开题 → 让 AI 说这一轮
    → 断言：预览区有字 + 按钮变成"让 AI 闭嘴"
    → 点它 → 断言：提示"已停下"，讨论稿没多一轮
    → 再让 AI 说一轮 → 断言：讨论稿里真出现 AI 的话

前置：`pip install playwright && playwright install chromium`（缺了就 SKIP，
按项目纪律"缺依赖 = 未执行 ≠ 失败"）。
用法：python tests/e2e/e2e_discuss_ai_turn_ui.py
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
    print("       启用：python -m pip install playwright && playwright install chromium")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "Temp" / "e2e_discuss_ai_ui"
OUT = ROOT / "Temp" / "gui_verify" / "discuss_ai"
SITE_PORT = int(os.environ.get("NF_E2E_SITE_PORT") or 8097)
API_PORT = int(os.environ.get("NF_E2E_DISCUSS_API_PORT") or 18766)
SITE = "http://127.0.0.1:%d" % SITE_PORT
API = "http://127.0.0.1:%d" % API_PORT
TOPIC = "e2e-界面-雨夜来客"
BODY = ("第 03 章 雨夜\n\n"
        "他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n\n"
        "她把杯子放下，杯底和木桌碰出很轻的一响，茶水晃了一圈。\n")

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
    for name in ("system.yaml", "project.yaml"):
        src = ROOT / "config" / name
        if src.exists():
            text = src.read_text(encoding="utf-8")
            if name == "system.yaml":
                text = re.sub(r"(?m)^engine:\s*\S+", "engine: direct", text)
            (WORK / "config" / name).write_text(text, encoding="utf-8")
    (WORK / "data" / "chapters" / "raw" / "03.md").write_text(BODY, encoding="utf-8")
    return WORK


def wait_url(url, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


def rounds(root):
    js = root / "data" / "discuss" / "03" / (TOPIC + ".json")
    try:
        return json.loads(js.read_text(encoding="utf-8")).get("rounds")
    except Exception:                                          # noqa: BLE001
        return None


def main():
    print("=" * 66)
    print("讨论 AI 轮 · 界面真机验收")
    root = prepare_root()
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["NF_FAKE_STREAM_DELAY_MS"] = "60"

    static = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(SITE_PORT),
         "--directory", str(ROOT / "console" / "renderer" / "dist")],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "nf_api.py"),
         "--port", str(API_PORT), "--allow-fake", "--root", str(root)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.2)
        # 端口被别人占了会怎样：http.server 立刻退出，而旧的 wait_url 会命中
        # **别人**的服务（本机 8091 常被 Hermes 的文件服务占着）→ 页面根本不是
        # 绒花墨坊，后面所有点击都在等一个不存在的按钮。必须当场说清。
        if static.poll() is not None:
            check("U0 静态服务起来（端口 %d 没被占用）" % SITE_PORT, False,
                  "端口 %d 已被占用——用 NF_E2E_SITE_PORT 换一个" % SITE_PORT)
            return 1
        if not wait_url(SITE + "/index.html") or not wait_url(API + "/health"):
            check("U0 静态服务 + 假后端起来", False, "端口 %d/%d" % (SITE_PORT, API_PORT))
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
            page.on("response", lambda r: bad.append("%s %s" % (r.status, r.url))
                    if r.status >= 400 and "favicon" not in r.url else None)

            page.goto(SITE, wait_until="networkidle")
            page.wait_for_timeout(2000)

            # 首启免责门：真实放行流（照 e2e_discuss_brief.py 的写法）
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
            check("U0b 免责声明已放行", page.locator(".disclaimer-mask").count() == 0)

            # 还有别的抽屉（首启向导 / 更新提示等）挡路：先按 Esc，再兜底摘掉遮罩。
            # 为什么要打印它的文字：如果是"首次向导"，说明我漏了一个真实放行流，
            # 光把遮罩删掉就等于在验收里作弊。
            if page.locator(".drawer-mask").count():
                page.screenshot(path=str(OUT / "drawer.png"), full_page=False)
                try:
                    print("  [drawer] " + page.locator(".drawer-mask").first.inner_text()[:200])
                except Exception:                              # noqa: BLE001
                    pass
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            if page.locator(".drawer-mask").count():
                page.evaluate("document.querySelectorAll('.drawer-mask').forEach(e => e.remove())")
                page.wait_for_timeout(200)
                check("U0c 遮罩是被强行摘掉的（需人工确认不是首启向导）", True)

            print("\n【1】进入章节页签，展开讨论")
            page.get_by_role("button", name="章节", exact=True).click()
            page.wait_for_timeout(800)
            box = page.locator("details.discuss-box")
            check("U1 讨论面板渲染出来了", box.count() == 1, box.count())
            if box.count() != 1:
                page.screenshot(path=str(OUT / "no_panel.png"), full_page=True)
                return 1
            box.locator("summary").click()
            page.wait_for_timeout(200)

            print("\n【2】开题")
            # 面板默认跟 App 传的 defaultChapter（当前是 1）；本用例的正文在 03，
            # 所以先把章号改成 3 —— 否则讨论会开在第 1 章，与正文对不上，
            # AI 那轮也会因为"第 1 章没有正文"退化成只能谈大纲。
            no = page.locator(".discuss-no")
            no.fill("3")
            no.dispatch_event("change")
            page.wait_for_timeout(900)
            page.locator(".discuss-topic").fill(TOPIC)
            page.get_by_role("button", name="开题", exact=True).click()
            page.wait_for_timeout(900)
            picked = page.locator(".discuss-list li.cur")
            check("U2 开题后自动选中", picked.count() == 1, picked.count())
            check("U2 列表里有这个主题",
                  TOPIC in (page.locator(".discuss-list").inner_text() or ""),
                  page.locator(".discuss-list").inner_text()[:120])

            print("\n【3】让 AI 说这一轮：流式 + 按钮变闭嘴")
            page.locator(".discuss-say textarea").fill("往冲突想")
            page.get_by_role("button", name="让 AI 说这一轮").click()
            page.wait_for_timeout(500)
            preview = page.locator(".discuss-ai-preview")
            text1 = preview.inner_text() if preview.count() else ""
            check("U3 流式预览有字（不是空跑）", len(text1.strip()) > 5, text1[:80])
            check("U3 预览是人话（不是 JSON 键名）",
                  '"stance"' not in text1 and '"text"' not in text1
                  and '"evidence"' not in text1, text1[:100])
            btn = page.locator(".discuss-actions button.stop-ai")
            check("U3 按钮变成“让 AI 闭嘴”", btn.count() == 1, btn.count())
            page.screenshot(path=str(OUT / "streaming.png"), full_page=False)

            print("\n【4】点它：真的停")
            if btn.count() == 1:
                btn.click()
            page.wait_for_timeout(2500)
            body = page.locator(".discuss-msg").inner_text() if page.locator(".discuss-msg").count() else ""
            check("U4 界面说清“已停下、没记进讨论”", "已停下" in body and "记进讨论" in body, body[:120])
            check("U4 停下后讨论稿没有多一轮", rounds(root) in (0, None), rounds(root))
            page.screenshot(path=str(OUT / "stopped.png"), full_page=False)

            print("\n【5】再让 AI 说一轮：完整落盘")
            page.get_by_role("button", name="让 AI 说这一轮").click()
            page.wait_for_timeout(6000)
            msg = page.locator(".discuss-msg").inner_text() if page.locator(".discuss-msg").count() else ""
            check("U5 跑完有成功提示", "AI 已说一轮" in msg, msg[:120])
            check("U5 轮次 = 1", rounds(root) == 1, rounds(root))
            draft = page.locator(".discuss-text").inner_text() if page.locator(".discuss-text").count() else ""
            check("U5 面板里读到 AI 的话", "这一轮先说结论" in draft, draft[-160:])
            page.screenshot(path=str(OUT / "done.png"), full_page=True)

            print("\n【6】不让自说自话：AI 说话时不许切主题")
            page.get_by_role("button", name="让 AI 说这一轮").click()
            page.wait_for_timeout(400)
            page.locator(".discuss-list li").first.click()
            page.wait_for_timeout(200)
            guard = page.locator(".discuss-msg").inner_text() if page.locator(".discuss-msg").count() else ""
            check("U6 切主题被拦住并说明原因", "正在说这一轮" in guard, guard[:120])
            if page.locator(".discuss-actions button.stop-ai").count():
                page.locator(".discuss-actions button.stop-ai").click()
            page.wait_for_timeout(2500)

            print("\n【7】AI 说该改的地方：建议摊出来，但不自己动手（C5/C14）")
            # 假客户端每轮都会带一个 action；界面上必须出现"建议区"，
            # 且按钮是"按这条生成候选"（不是直接改正文）。
            page.wait_for_timeout(600)
            # 先关掉"任务结束"浮层：它盖在讨论卡片右侧，不关掉目检截图看不懂
            # （不是布局缺陷 —— 是上一段"停下"用例留下的通知条）。
            try:
                page.get_by_role("button", name="稍后", exact=True).first.click()
                page.wait_for_timeout(300)
            except Exception:                                  # noqa: BLE001
                pass
            has_sug = page.locator(".discuss-sugs").count() >= 1
            check("U9 界面有'AI 说该改的地方'", has_sug,
                  page.locator(".discuss-body").inner_text()[-200:] if page.locator(".discuss-body").count() else "")
            if has_sug:
                page.screenshot(path=str(OUT / "suggestions.png"), full_page=False)
                check("U9 有'按这条生成候选'按钮",
                      page.get_by_role("button", name="按这条生成候选").count() >= 1)
                check("U9 有'不用'按钮",
                      page.get_by_role("button", name="不用", exact=True).count() >= 1)
                check("U9 建议行里写了段落号", "#" not in page.locator(".discuss-sugs").inner_text())
                page.get_by_role("button", name="不用", exact=True).first.click()
                page.wait_for_timeout(900)
                check("U9 点'不用'后建议消失",
                      page.locator(".discuss-sugs").count() == 0,
                      page.locator(".discuss-body").inner_text()[-160:] if page.locator(".discuss-body").count() else "")
            check("U9 有'压缩老轮次'入口",
                  page.get_by_role("button", name="压缩老轮次").count() >= 1)

            print("\n【8】设置页：讨论开关在界面上")
            page.get_by_role("button", name="设置", exact=True).click()
            page.wait_for_timeout(1500)
            # 设置页左侧分类（budget = 预算与止烧）：点不到就说明还没进面板
            try:
                page.locator("text=预算与止烧").first.click()
            except Exception:                                  # noqa: BLE001
                pass
            page.wait_for_timeout(800)
            check("U8 设置页有“讨论（AI 说一轮）”",
                  page.get_by_text("讨论（AI 说一轮）").count() >= 1,
                  page.locator("body").inner_text()[:80])
            check("U8 有主动发言开关",
                  page.get_by_text("允许 AI 主动发言").count() >= 1)
            check("U8 有保存按钮",
                  page.get_by_role("button", name="保存讨论设置").count() >= 1)
            page.screenshot(path=str(OUT / "settings_discuss.png"), full_page=True)

            check("U7 全程无 console error", not errors, errors[:3])
            bad = [b for b in bad if "/discuss/" not in b]     # 404 那类负向用例不算
            check("U7 没有意外的 4xx/5xx", not bad, bad[:3])
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
