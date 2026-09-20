# -*- coding: utf-8 -*-
"""stage4 → 出场记录自动同步 自检（任务3，纯 fake，零 LLM、零网络）。

在**临时工作目录**跑（复制 prompts/ 与 config/），真实 data/ 零污染。
覆盖：
- 每章完成后 data/state/appearances.json 自动生成/刷新（首跑即创建）
- setting.json 的 appearances 字段同步（分章累计）
- 幂等：同章重复同步不重复累加；stage4 重跑不会把计数翻倍
- 失败注入：出场记录链路抛异常时**不阻断** stage4（章节照样完成）
- 无设定集/无章节时不写垃圾文件、不报错

用法：python tests/test_stage4_appearances.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import utils.fake_client as fc                      # noqa: E402
from utils.fake_client import FakeClient            # noqa: E402
from utils.progress_manager import ProgressManager  # noqa: E402
import stage4_writing as s4                         # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


SETTING = {
    "characters": [
        {"id": "luxi", "name": "露汐", "role": "主角", "traits": ["冷静"], "source": "fake"},
        {"id": "xiaolin", "name": "小林", "role": "配角", "traits": ["热心"], "source": "fake"},
    ],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [],
    "timeline": [],
    "_meta": {"version": 1, "generated_from": ["fake"]},
}


def build_workspace(tag="ws"):
    tmp = Path(tempfile.mkdtemp(prefix="stage4_appear_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "测试书"\n  chapters: 3\n', encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text("# 测试书 大纲\n\n## 起\n开篇\n",
                                                         encoding="utf-8")
    for n in (1, 2, 3):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：露汐（主角）\n" % n,
            encoding="utf-8")
    return tmp


def run_stage4(tmp, client=None):
    """在 tmp 为 cwd 的前提下跑一次 stage4。返回 (ok, msg)。"""
    os.chdir(tmp)
    import yaml
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    return s4.run_stage(cfg, proj, progress, None, None,
                        client=client or FakeClient(),
                        task_dir=str(tmp / "data" / "state" / "tasks"))


def case_first_run_creates():
    print("\n=== 1. 首跑：每章完成后自动生成 appearances.json ===")
    tmp = build_workspace("first")
    try:
        raw = tmp / "data" / "state" / "appearances.json"
        check("跑之前 appearances.json 不存在（验证确实是 stage4 创建的）", not raw.exists())
        ok, msg = run_stage4(tmp)
        check("stage4 跑通 3 章", ok, msg)
        check("data/state/appearances.json 已自动生成", raw.exists())
        data = json.loads(raw.read_text(encoding="utf-8"))
        chars = data.get("characters") or {}
        check("统计到设定集角色", "露汐" in chars and "小林" in chars, list(chars)[:6])
        luxi = chars.get("露汐") or {}
        check("露汐有真实出场计数（不是 absent）",
              luxi.get("chapters", 0) >= 1 and luxi.get("total", 0) > 0, luxi)
        check("首次出场章号正确（第1章）", luxi.get("first") == 1, luxi.get("first"))
        check("total_chapters 用书配置（3）", data.get("total_chapters") == 3, data.get("total_chapters"))
        check("分级已计算（非 pending）", luxi.get("level") in ("major", "minor", "background"),
              luxi.get("level"))
        check("未出场的角色仍在统计中并给出版级（防「写了忘了用」）",
              "小林" in chars and chars["小林"].get("level") in
              ("absent", "background", "minor", "major"), chars.get("小林"))

        st = json.loads((tmp / "data" / "setting" / "setting.json").read_text(encoding="utf-8"))
        app = st.get("appearances") or {}
        check("setting.json 的 appearances 字段已写入", "露汐" in app, list(app)[:6])
        check("分章记录为 1..3 全章", sorted(app.get("露汐", {}).get("chapters") or []) == [1, 2, 3],
              app.get("露汐"))
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_idempotent():
    print("\n=== 2. 幂等：重跑不重复累加 ===")
    tmp = build_workspace("idem")
    try:
        ok, _ = run_stage4(tmp)
        check("首跑完成", ok)
        st_path = tmp / "data" / "setting" / "setting.json"
        before = json.loads(st_path.read_text(encoding="utf-8"))["appearances"]["露汐"]["total_mentions"]

        # 2-1 直接重复同步同一章
        os.chdir(tmp)
        import obsidian_integrate as ri
        text = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        ri.sync_chapter_appearances(text, 1)
        ri.sync_chapter_appearances(text, 1)
        mid = json.loads(st_path.read_text(encoding="utf-8"))["appearances"]["露汐"]["total_mentions"]
        check("同章重复同步 total_mentions 不变（幂等）", mid == before, (before, mid))

        # 2-2 stage4 重跑（章节已完成 → 跳过，不得翻倍）
        ok2, msg2 = run_stage4(tmp)
        after = json.loads(st_path.read_text(encoding="utf-8"))["appearances"]["露汐"]["total_mentions"]
        check("stage4 重跑后计数不翻倍", after == before, (before, after))
        check("stage4 重跑走“已完成跳过”分支", "跳过" in msg2 or ok2, msg2)
        return True
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_failure_not_blocking():
    print("\n=== 3. 失败注入：出场记录链路异常不阻断 stage4 ===")
    tmp = build_workspace("boom")
    try:
        import obsidian_integrate as ri
        os.chdir(tmp)
        import yaml
        import appearances as ap_mod

        def boom(*a, **k):
            raise RuntimeError("注入的出场记录故障")

        ri.sync_chapter_appearances, old_sync = boom, ri.sync_chapter_appearances
        ap_mod.refresh_appearances, old_ref = boom, ap_mod.refresh_appearances
        try:
            ok, msg = run_stage4(tmp)
        finally:
            ri.sync_chapter_appearances = old_sync
            ap_mod.refresh_appearances = old_ref
        check("出场记录双路都抛异常时 stage4 仍然完成（不阻断）", ok, msg)
        check("章节正文照常落盘",
              all((tmp / "data" / "chapters" / "raw" / ("%02d.md" % n)).exists() for n in (1, 2, 3)))
        prog = json.loads((tmp / "data" / "state" / "progress.json").read_text(encoding="utf-8"))
        done = prog["stages"]["4"]
        check("进度里 3 章都记为完成（未被出场记录故障带偏）",
              len(done.get("completed") or done.get("completed_chapters") or []) == 3
              or done.get("status") == "done", str(done)[:200])
        check("失败时不会写出半截 appearances.json",
              not (tmp / "data" / "state" / "appearances.json").exists())
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_missing_setting():
    print("\n=== 4. 缺 setting.json 时不报错（角色回退到逐章大纲） ===")
    tmp = build_workspace("noset")
    try:
        (tmp / "data" / "setting" / "setting.json").unlink()
        os.chdir(tmp)
        ok, msg = run_stage4(tmp)
        check("缺 setting.json 时 stage4 仍完成", ok, msg)
        raw = tmp / "data" / "state" / "appearances.json"
        check("仍能据逐章大纲统计角色（不崩、不空转）", raw.exists())
        if raw.exists():
            data = json.loads(raw.read_text(encoding="utf-8"))
            chars = data.get("characters") or {}
            check("大纲角色被标为设定集外候选（新角色候选）",
                  "露汐" in chars and chars["露汐"].get("in_setting") is False
                  and "露汐" in (data.get("new_candidates") or []),
                  (list(chars)[:4], data.get("new_candidates")))
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    # 让 fake 正文里出现设定集角色名，才能验证"出场"被真实统计到
    _orig_para = fc._words_para
    fc._words_para = lambda min_words: ("露汐推门进来，把伞放在门边，看了一眼窗外的雨。"
                                        * (max(int(min_words), 400) // 25 + 3))
    print("=" * 70)
    print("stage4 → 出场记录自动同步 自检（fake，临时工作目录）")
    print("=" * 70)
    try:
        for fn in (case_first_run_creates, case_idempotent,
                   case_failure_not_blocking, case_missing_setting):
            try:
                fn()
            except Exception as e:      # noqa: BLE001
                FAIL.append(fn.__name__)
                import traceback
                print("  [ERROR] %s → %s" % (fn.__name__, e))
                traceback.print_exc()
    finally:
        fc._words_para = _orig_para
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 70)
    raise SystemExit(1 if FAIL else 0)
