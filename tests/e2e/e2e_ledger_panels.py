# -*- coding: utf-8 -*-
"""新面板的真机视觉验收（2026-10-20）：七维评审账本 + 素材裁决。

为什么单独写一个：这两个面板是「后端判据 → GUI 显示」的最后一公里，
单元测试证明不了"打开页面能看到、点得动、没有 console error"。
沿用 `e2e_ux_verify.py` 的垫片与免责声明放行流（同一套，不另写一份）。

跑法：python tests/e2e/e2e_ledger_panels.py
它会自起三件东西：临时工作区 fixture + nf_api(8797, --root) + 静态服务(8092, renderer/dist)。
截图落在 Temp/gui_verify/ledger/。

⚠️ 真机验收，不是离线用例：缺 playwright 时干净 SKIP（exit 0）。
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ⚠️ 不要在这里包 sys.stdout：下面 import 的 `e2e_ux_verify` 模块级已经包过一次，
# 包两层会让前一个 wrapper 被 GC 时关闭底层 buffer（实测报 I/O operation on closed file）。
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

API_PORT = 8797
SITE_PORT = 8092
API = "http://127.0.0.1:%d" % API_PORT
SITE = "http://127.0.0.1:%d" % SITE_PORT
# UX.new_page 用的是它自己的模块级 SITE（8091）。我们另起 8092 是为了不跟
# 既有验收脚本抢端口 —— 所以必须把它的 SITE 指过来，否则页面打开的是别人家。
UX.SITE = SITE
OUT = ROOT / "Temp" / "gui_verify" / "ledger"
OUT.mkdir(parents=True, exist_ok=True)
# UX.shot 写的是它自己的 OUT（Temp/gui_verify/ux）。指过来，别让本轮截图
# 混进别人那批里 —— 也免得看到空目录以为没截图。
UX.OUT = OUT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


# ---------------------------------------------------------------- fixture
CARD_A = """# 角色：苏芷

## 基本信息
- 身份：档案室整理员
- 性格：记事极细

> 来源：素材甲
"""

CARD_B = """# 角色：苏芷

## 基本信息
- 身份：档案室的整理员（旧版笔记）
- 性格：冷静

> 来源：素材乙
"""

SETTING = {
    "characters": [
        {"id": "c001", "name": "苏芷", "role": "档案室整理员",
         "traits": ["记事极细"], "relations": {"周砚清": "她欠他一个人情"},
         "source": "materials/raw/a.md"},
        {"id": "c002", "name": "周砚清", "role": "巡夜人", "traits": ["沉默"],
         "source": "materials/raw/b.md"},
    ],
    "world": {"地理": "河口分七区，外围是滩涂。"},
    "plot_fragments": ["怀表的来历始终没有交代，链子上刻着一个旧年号"],
    "timeline": ["某年冬，封锁开始"],
}

CHAPTER = (
    "## 第1章 锈锁\n\n"
    "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n\n"
    "“你迟到了。”老者没有抬头，手指在桌沿上慢慢摩挲。\n\n"
    "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。\n\n"
    "灯芯爆了一下，屋里暗了半格。\n\n"
    "他把湿透的袖口往腕上又卷了一道。\n\n"
    "门轴响了一声，他没有回头。\n\n"
    "桌角压着一张被水浸过的纸，字迹已经散开。\n\n"
    "“明天再来。”老者把灯往桌心推了推。\n"
)


def build_workspace():
    ws = Path(tempfile.mkdtemp(prefix="nf_ledger_ui_"))
    (ws / "config").mkdir()
    for n in ("system.yaml", "project.yaml"):
        src = ROOT / "config" / n
        if not src.exists():
            src = ROOT / "config" / (n + ".example")
        shutil.copy2(src, ws / "config" / n)
    (ws / "prompts").symlink_to(ROOT / "prompts") if hasattr(Path, "symlink_to") else None
    if not (ws / "prompts").exists():
        shutil.copytree(ROOT / "prompts", ws / "prompts")
    (ws / "materials" / "raw").mkdir(parents=True)
    (ws / "materials" / "raw" / "a.md").write_text(CARD_A, encoding="utf-8")
    (ws / "materials" / "raw" / "b.md").write_text(CARD_B, encoding="utf-8")
    (ws / "data" / "setting").mkdir(parents=True)
    (ws / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (ws / "data" / "chapters" / "raw").mkdir(parents=True)
    (ws / "data" / "chapters" / "raw" / "01.md").write_text(CHAPTER, encoding="utf-8")

    # 真账本：走真实实现生成，不手搓 JSON（否则 GUI 读的结构可能不是后端真给的）
    from utils import review_ledger as rl
    dims = [
        {"dim": "plot", "score": 8, "quote": "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。", "note": "开门即入场景，因果清楚"},
        {"dim": "timeline", "score": 9, "quote": "“你迟到了。”老者没有抬头，手指在桌沿上慢慢摩挲。", "note": "无时间矛盾"},
        # 故意给一条编造的举证：GUI 必须显示「未计分」而不是 8 分
        {"dim": "space", "score": 7, "quote": "他从怀里掏出那只停在三点二十的怀表。", "note": "（这条是编的，用于验收未举证显示）"},
        {"dim": "reference", "score": 8, "quote": "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。", "note": "指代清楚"},
        {"dim": "relation", "score": 7, "quote": "“你迟到了。”老者没有抬头，手指在桌沿上慢慢摩挲。", "note": "互动符合关系"},
        {"dim": "persona", "score": 8, "quote": "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。", "note": "行为一致"},
        {"dim": "commonsense", "score": 9, "quote": "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。", "note": "无违背常识"},
    ]
    aesthetic = [
        {"q": "prose", "score": 8, "quote": "灯芯爆了一下，屋里暗了半格。", "note": "用一个光的变化写气氛"},
        {"q": "technique", "score": 7, "quote": "他把湿透的袖口往腕上又卷了一道。", "note": "动作代替情绪说明"},
        {"q": "dialogue", "score": 8, "quote": "“明天再来。”老者把灯往桌心推了推。", "note": "短句，能听出人物的性子"},
        {"q": "diction", "score": 7, "quote": "桌角压着一张被水浸过的纸，字迹已经散开。", "note": "用词克制，没有形容词堆砌"},
        {"q": "impact", "score": 8, "quote": "门轴响了一声，他没有回头。", "note": "收得干净，留了余味"},
    ]
    res = rl.check_chapter({"chapter": 1, "summary": "开篇：雨夜拜访", "dims": dims,
                            "aesthetic": aesthetic}, CHAPTER, 1)
    rl.write_ledger(ws / "data" / "review", res)
    rl.write_summary(ws / "data" / "review", [res])
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


def api_get(path):
    import urllib.request
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ws = build_workspace()
    procs = []
    print("工作区：%s" % ws)
    try:
        env = dict(os.environ)
        env["NF_ROOT"] = str(ws)
        procs.append(subprocess.Popen(
            [sys.executable, "scripts/nf_api.py", "--port", str(API_PORT), "--root", str(ws)],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "http.server", str(SITE_PORT),
             "--directory", str(ROOT / "console" / "renderer" / "dist")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
        if not wait_port(API_PORT) or not wait_port(SITE_PORT):
            print("[SKIP] 服务没起来（API=%s SITE=%s）" % (wait_port(API_PORT), wait_port(SITE_PORT)))
            return 0

        print("\n=== A. 后端契约（GUI 之前先确认数据本身对） ===")
        led = api_get("/review/ledger")
        check("/review/ledger 返回七维定义", len(led.get("dimensions") or []) == 7, led.get("dimensions"))
        rows = led.get("chapters") or []
        check("/review/ledger 有一章记录", len(rows) == 1, rows)
        check("编造举证的那一维 score 为 None（不计分）",
              (rows[0]["scores"].get("space") is None) if rows else False, rows[:1])
        check("其它维度有分数", (rows[0]["scores"].get("plot") == 8) if rows else False, rows[:1])
        one = api_get("/review/ledger?chapter=1")
        check("单章明细带举证原文", (one.get("dims") or {}).get("plot", {}).get("quote", "").startswith("他推开门"),
              (one.get("dims") or {}).get("plot"))
        cf = api_get("/setting/conflicts")
        check("/setting/conflicts 已检查", cf.get("checked") is True, cf.get("reason"))
        check("识别出「身份」字段分歧（苏芷两份素材）",
              any(c.get("field") == "身份" for c in (cf.get("review_conflicts") or [])),
              cf.get("review_conflicts"))

        print("\n=== B. 七维评审账本面板（章节页签） ===")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # UX.new_page 返回 (ctx, page, errs, bad) 且**已跑过**免责声明放行流
            # （它在内部 settle_disclaimer），这里不要重复处理。
            ctx, page, errs, bad = UX.new_page(browser, API, "ledger")
            page.locator("nav.tabs button:has-text('章节')").click()
            page.wait_for_timeout(1200)
            # 用**结构**定位，并限定 `div.card`：外层容器是 `<section class="card">`，
            # 它**祖先级**也包含面板内容，`:has-text` / `:has()` 都会同时命中两层
            # （Playwright strict mode 直接报 violation，实测踩了两次）。
            card = page.locator("div.card:has(.rl-table)")
            check("七维评审账本卡片渲染", card.count() >= 1, card.count())
            head = card.locator(".rl-table thead th").all_inner_texts() if card.count() else []
            check("表头 7 个维度 + 章 + 逻辑均分 + 审美", len(head) == 10, head)
            check("表头含「情节连贯性」", "情节连贯性" in head, head)
            check("未举证维度显示「未举证」", "未举证" in card.inner_text(), card.inner_text()[:200])
            card.locator(".rl-table tbody tr").first.click()
            page.wait_for_timeout(600)
            # 七维 7 条 + 审美五问 5 条 = 12 条明细块
            check("点章节后出现七维 + 审美共 12 条明细",
                  card.locator(".rl-dim").count() == 12,
                  card.locator(".rl-dim").count())
            check("明细里有原文引用块", card.locator(".rl-quote").count() >= 5,
                  card.locator(".rl-quote").count())
            q = card.locator(".rl-quote").first.inner_text()
            check("引用块显示的是原文句子", "推开门" in q or "迟到" in q, q[:60])
            # 审美五问（2026-10-20）：另开一块，五问各自举证
            aes_head = card.locator(".rl-aes-head")
            check("审美五问块渲染", aes_head.count() == 1)
            check("审美块显示均分", "均分" in (aes_head.inner_text() if aes_head.count() else ""),
                  aes_head.inner_text() if aes_head.count() else "")
            aes_text = card.inner_text()
            check("五问标签都在", all(k in aes_text for k in ("描写质感", "叙事手法", "对话区分度", "用词质量", "情感打动力")),
                  aes_text[-300:])
            check("审美也有原文引用（不是空口打分）",
                  card.locator(".rl-dim .rl-quote").count() >= 10,
                  card.locator(".rl-dim .rl-quote").count())
            UX.shot(page, "L1_review_ledger")

            print("\n=== C. 素材裁决面板（设定页签） ===")
            page.locator("nav.tabs button:has-text('设定')").click()
            page.wait_for_timeout(1200)
            sc = page.locator("div.card:has(.sc-summary)")
            check("素材裁决卡片渲染", sc.count() >= 1, sc.count())
            # 设定列表的摘要要能回落到 role（否则整列"（无描述）"，像设定集是空的）
            row = page.locator(".setting-row").first
            check("设定列表回落到 role/snippet 显示（不是一排「无描述」）",
                  "档案室整理员" in row.inner_text(), row.inner_text()[:80])
            body = sc.inner_text() if sc.count() else ""
            check("显示待裁决数量", "处待裁决" in body, body[:200])
            check("列出冲突条目（苏芷 / 身份）", "苏芷" in body and "身份" in body, body[:400])
            check("显示双方出处（素材甲 / 素材乙）",
                  "素材甲" in body or "素材乙" in body or "a.md" in body, body[:400])
            btn = sc.locator("button:has-text('否决该条目')").first
            check("有「否决该条目」按钮", btn.count() == 1)
            btn.click()
            page.wait_for_timeout(400)
            check("点否决后要求填原因（输入框出现）", sc.locator(".sc-input").count() == 1,
                  sc.locator(".sc-input").count())
            check("未填原因时确认按钮禁用",
                  sc.locator("button:has-text('确认否决')").is_disabled())
            UX.shot(page, "L2_setting_closure")

            real_errs = [e for e in errs if "favicon" not in e]
            check("整页没有 console error", not real_errs, real_errs[:3])
            real_bad = [b for b in bad if "/favicon" not in b]
            check("没有 4xx/5xx 请求（新端点真的通）", not real_bad, real_bad[:3])
            browser.close()
    finally:
        for pr in procs:
            try:
                pr.terminate()
            except Exception:                                    # noqa: BLE001
                pass
        time.sleep(1)
        for pr in procs:
            try:
                pr.kill()
            except Exception:                                    # noqa: BLE001
                pass
        shutil.rmtree(ws, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("  截图：%s" % OUT)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
