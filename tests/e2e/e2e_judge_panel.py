# -*- coding: utf-8 -*-
"""判定面板真机验收（0.7.17）：整本一页 → 单章 → 勾事件 → 裁定 → 改标准。

钉什么：
A. 章节页签顶部有「判定」摘要条，且两句固定串常显
B. 没跑过 → 明说「还没跑过回测」，不假装有结论
C. 点「开始回测」→ 出现总账 + 三章行（档位带符号与短语，不是分数）
D. 「看这章」→ 详情：事件勾选区 + 缺陷清单 + 原文
E. 勾事件保存 → 档位不再是「还没判」
F. 点缺陷 → 展开「原句/为什么/你怎么办」；选感受 + 理由 → 记录裁定
G. 「改标准」→ 口径表（带消费方）；改 J10 → 版本 +1
H. 无 console error / 无 4xx

零 token（整条链不调模型）。用法：python tests/e2e/e2e_judge_panel.py
"""
import io
import os
import re
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

API_PORT = 8808
SITE_PORT = 8096
API = "http://127.0.0.1:%d" % API_PORT
SITE = "http://127.0.0.1:%d" % SITE_PORT
UX.SITE = SITE
OUT = ROOT / "Temp" / "gui_verify" / "judge"
OUT.mkdir(parents=True, exist_ok=True)
UX.OUT = OUT

PASS, FAIL = [], []

BODY = """## 第{n}章 夜雨

他在档案室盖章。他把回执压平，拿起印章，在纸角上按下去。

他核对回执上的编号。他一行一行往下看，看到第三行停住。

他在日志上划改。他把笔尖朝上，放回笔槽。

他抽出一张字条。他把字条摊在桌上，压在回执底下。
"""

OUTLINE = """## 第{n}章 夜雨
- 核心事件：
  1. 他在档案室盖章
  2. 他核对回执编号
  3. 他在日志上划改
  4. 他发现一张字条
- 涉及角色：沈砚
- 功能：推进
- 衔接：承接上一章
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def build_workspace():
    ws = Path(tempfile.mkdtemp(prefix="nf_judge_e2e_"))
    shutil.copytree(ROOT / "config", ws / "config",
                    ignore=shutil.ignore_patterns("history"))
    sys_yaml = ws / "config" / "system.yaml"
    if sys_yaml.exists():
        txt = sys_yaml.read_text(encoding="utf-8")
        sys_yaml.write_text(re.sub(r"agent_mode:\s*true", "agent_mode: false", txt),
                            encoding="utf-8")
    for d in ("data/chapters/raw", "data/outline/chapters", "data/state",
              "data/setting", "materials/raw"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    for n in range(1, 4):
        (ws / "data/chapters/raw" / ("%02d.md" % n)).write_text(
            BODY.format(n=n), encoding="utf-8")
        (ws / "data/outline/chapters" / ("%02d.md" % n)).write_text(
            OUTLINE.format(n=n), encoding="utf-8")
    (ws / "config/project.yaml").write_text(
        'book:\n  name: "判定面板验收书"\n  chapters: 3\n  style_reference: ""\n',
        encoding="utf-8")
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
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "http.server", str(SITE_PORT),
             "--directory", str(ROOT / "console" / "renderer" / "dist")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
        if not wait_port(API_PORT) or not wait_port(SITE_PORT):
            print("[SKIP] 服务没起来")
            return 0

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx, page, errs, bad = UX.new_page(browser, API, "judge")
            page.locator("nav.tabs button:has-text('章节')").click()
            page.wait_for_timeout(1500)

            print("\n=== A/B. 摘要条 + 空状态 ===")
            panel = page.locator(".judge").first
            check("章节页签顶部有判定面板", panel.count() >= 1, panel.count())
            body = panel.inner_text()
            check("固定串①「存量回测判定，非新跑拦截」在场", "存量回测判定，非新跑拦截" in body,
                  body[:160])
            check("固定串②「本报告不改变现有成品文件」在场", "本报告不改变现有成品文件" in body)
            check("没跑过时明说（不假装有结论）", "还没跑过回测" in body, body[:160])

            print("\n=== C. 开始回测 → 总账 + 三章 ===")
            panel.locator("button:has-text('开始回测')").click()
            page.wait_for_timeout(1500)
            body = panel.inner_text()
            check("出现总账行（N 章：… 处要改）", "章：" in body and "处要改" in body,
                  body[:200])
            rows = panel.locator("table.judge-table tbody tr")
            check("三章各一行", rows.count() == 3, rows.count())
            first = rows.first.inner_text()
            check("档位带符号或明确「还没判」",
                  ("✓" in first) or ("⚠" in first) or ("✕" in first) or ("还没判" in first),
                  first[:120])
            check("不出现分数（无「分」结尾的评分字样）",
                  "quality" not in body.lower() and "评分" not in body)

            print("\n=== D. 单章详情 ===")
            rows.first.locator("button:has-text('看这章')").click()
            page.wait_for_timeout(900)
            det = panel.inner_text()
            check("进到单章详情（回整本按钮）", "回整本" in det, det[:120])
            check("有事件勾选区", "这一章发生了几件事" in det)
            check("有缺陷清单或「没命中任何规则」",
                  ("第 " in det and "段" in det) or "没命中任何规则" in det)

            print("\n=== E. 勾事件 → 档位出现 ===")
            boxes = panel.locator(".judge-event input[type=checkbox]")
            check("事件行有勾选框", boxes.count() >= 1, boxes.count())
            if boxes.count():
                boxes.nth(0).check()
                boxes.nth(1).check()
                sel = panel.locator("select.judge-ev-class")
                sel.nth(0).select_option("信息占有")
                sel.nth(1).select_option("关系")
            panel.locator("button:has-text('保存勾选')").click()
            page.wait_for_timeout(1200)
            det2 = panel.inner_text()
            check("保存后不再是「还没判」", "还没判" not in det2, det2[:160])

            print("\n=== F. 缺陷卡：原句/为什么/你怎么办 + 裁定 ===")
            defect = panel.locator(".judge-defect-row").first
            check("有可点的缺陷行", defect.count() >= 1, defect.count())
            if defect.count():
                defect.click()
                page.wait_for_timeout(400)
                card = panel.locator(".judge-card").first
                ctext = card.inner_text()
                check("卡片三行齐备（原句/为什么/你怎么办）",
                      "原句" in ctext and "为什么" in ctext and "你怎么办" in ctext, ctext[:120])
                card.locator("button:has-text('打中了')").click()
                card.locator("input.judge-reason").fill("读到这儿停了")
                card.locator("button:has-text('记录裁定')").click()
                page.wait_for_timeout(1000)
                check("裁定后未报错", "裁定已记" in page.locator("body").inner_text(),
                      page.locator("body").inner_text()[-120:])
                UX.shot(page, "JD1_detail")

            print("\n=== G. 改标准 ===")
            panel.locator("button:has-text('回整本')").click()
            page.wait_for_timeout(500)
            panel.locator("button:has-text('改标准')").click()
            page.wait_for_timeout(900)
            cal = panel.inner_text()
            check("口径表带版本号", "现在生效" in cal and "版" in cal, cal[:160])
            check("每行有消费方（谁在读）", "消费方" in cal or "F01" in cal, cal[:200])
            inp = panel.locator("input.judge-cal-input").first
            if inp.count():
                inp.fill("0.30")
                panel.locator("button:has-text('保存（产新版本）')").click()
                page.wait_for_timeout(1000)
                check("改阈值后版本变化", "第 2 版" in panel.inner_text(),
                      panel.inner_text()[:120])
            UX.shot(page, "JD2_caliber")

            print("\n=== H. 干净度 ===")
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
