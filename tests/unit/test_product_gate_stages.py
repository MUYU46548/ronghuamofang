# -*- coding: utf-8 -*-
"""产物闸接到了哪几个阶段（阶段模块级判据，2026-11-12）。

## 它守的是什么

`test_product_gate.py` 守的是闸本身（候选 / 批准 / 合成 / stale），
`test_products_api.py` 守的是界面那一层。**这一份守的是"阶段接上了没有"**：
执行方交回候选、作者没批 → 正式路径一个字节都不写；作者批了 → 合成成功。

只测闸不测接线的话，"阶段 5 的锁是假的"这种事照样能全绿 ——
因为闸本身一直是对的，只是没人调用它。

## 判据

每阶段两条（阶段 2 / 3 / 5 / 6 各一组）：
- 候选未批 → `self_done` 返回未完成，且**正式文件不存在**
- 作者批准后 → 正式文件出现，内容与候选逐字节一致

阶段 5 另外验一句：合成之后账本仍缺 → 依然判未完成（"缺账本 = 视为没检查"
这条旧判据没有被产物闸顶掉）。

零 token、零工作区数据（全部在临时目录里造）。
用法：python tests/unit/test_product_gate_stages.py
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

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


SETTING = {
    "characters": [{"name": "阿禾", "identity": "药铺学徒", "personality": "谨慎话少",
                    "goal": "查清父亲的死因", "appearance": "青布短打"}],
    "world": {"locations": [{"name": "青石镇", "description": "山道尽头的旧镇。"}]},
    "plot_fragments": [{"summary": "阿禾发现药柜暗格", "status": "unused"}],
    "timeline": [{"when": "第一年春", "what": "父亲失踪"}],
}

OUTLINE = ("## 起\n雨夜。\n\n## 承\n暗格。\n\n## 转\n对峙。\n\n## 合\n收束。\n\n"
           "## 关键节点\n- 暗格被发现\n\n## 预计章节数\n3\n")

CH_OUTLINE = ("- 核心事件：阿禾在药柜暗格里发现一枚旧铜钥匙。\n"
              "- 涉及角色：阿禾、掌柜\n"
              "- 功能：建立悬念，交代父亲的线索\n"
              "- 小目标：找到暗格里的东西\n"
              "- 悬念钩子：灰上的指印是谁的\n")

CHAPTER = "# 第1章 暗格\n\n%s\n\n<!-- summary: 阿禾发现药柜暗格是空的。 -->\n<!-- quality: 7 -->\n" % (
    "夜雨落在青石板上，阿禾把药柜最后一格抽屉拉到底。" * 40)


def _w(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_ws():
    """最小可跑工作区：上游产物齐备，四个待测阶段各有活可干。"""
    ws = Path(tempfile.mkdtemp(prefix="nf_gate_stage_"))
    _w(ws / "config" / "system.yaml",
       "engine: self\nchapter:\n  target_words: [1000, 9000]\n  quality_threshold: 6\n"
       "gates:\n  require_approval: [2]\n  auto_retry: false\n  quality_gate: false\n")
    _w(ws / "config" / "project.yaml",
       "book:\n  name: 产物闸夹具\n  genre: 悬疑\n  chapters: 2\n  target_words: 20000\n"
       "materials:\n  dir: materials/raw\n  use_scraps: false\n")
    shutil.copytree(ROOT / "prompts", ws / "prompts")
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    _w(ws / "data" / "setting" / "setting.json",
       json.dumps(SETTING, ensure_ascii=False, indent=2))
    _w(ws / "data" / "outline" / "global.md", OUTLINE)
    for n in (1, 2):
        _w(ws / "data" / "outline" / "chapters" / ("%02d.md" % n), CH_OUTLINE)
        _w(ws / "data" / "chapters" / "raw" / ("%02d.md" % n), CHAPTER)
    return ws


def main():
    import stage2_outline as s2
    import stage3_chapter_outline as s3
    import stage5_check as s5
    import stage6_polish as s6
    from utils import product_gate as pg
    from utils.config_io import load_pipeline_config

    ws = make_ws()
    cwd = os.getcwd()
    os.chdir(ws)
    try:
        cfg, proj = load_pipeline_config()
        proj = dict(proj or {})
        proj.setdefault("book", {})["chapters"] = 2

        print("【A】阶段 2（整体大纲）：候选没批 → 正式路径不写")
        rel2 = "data/outline/global.md"
        (ws / rel2).unlink()                      # 本阶段要凭空产出：先清掉上游摆的
        task = (s2.emit_tasks(cfg, proj, None, task_dir="data/state/tasks/self")["tasks"])[0]
        check("任务书目标是候选区", task["target"] == pg.pending_rel(2, rel2), task["target"])
        ok, why = s2.self_done(cfg, proj)
        check("没候选 → 未完成", ok is False and "就绪" in why, why)
        _w(pg.pending_path(".", 2, rel2), OUTLINE)
        ok, why = s2.self_done(cfg, proj)
        check("候选在、没批 → 未完成且说清在等谁", ok is False and "批准" in why, why)
        check("正式路径没被创建", not (ws / rel2).exists())
        pg.record_approval(".", 2, rel2, by="unit-test")
        ok, why = s2.self_done(cfg, proj)
        check("批准后 → 判完成", ok is True, why)
        check("正式文件已合成且内容一致",
              (ws / rel2).read_text(encoding="utf-8") == OUTLINE)

        print("\n【B】阶段 3（分章大纲）：只批第 1 章，第 2 章不许顺带合成")
        r3 = "data/outline/chapters/%02d.md"

        def _targets(t):
            tg = t["target"]
            return tg if isinstance(tg, list) else [tg]

        (ws / "data/outline/chapters/01.md").unlink()
        task = (s3.emit_tasks(cfg, proj, None, task_dir="data/state/tasks/self")["tasks"])[0]
        check("任务书目标是候选区",
              all(str(x).startswith("data/_pending/stage3/") for x in _targets(task)),
              _targets(task))
        _w(pg.pending_path(".", 3, r3 % 1), CH_OUTLINE)
        ok, why = s3.self_done(cfg, proj)
        check("候选在、没批 → 未完成且说清在等谁", ok is False and "批准" in why, why)
        check("正式路径没被创建", not (ws / (r3 % 1)).exists())
        pg.record_approval(".", 3, r3 % 1, by="unit-test")
        ok, why = s3.self_done(cfg, proj)
        check("批准后 → 判完成", ok is True, why)
        check("第 1 章已合成", (ws / (r3 % 1)).exists())
        check("第 2 章仍是上游摆的，没被候选覆盖",
              (ws / (r3 % 2)).read_text(encoding="utf-8") == CH_OUTLINE)

        print("\n【C】阶段 5（检查稿）：候选没批 → checked/ 不写；批准后才写")
        r5 = "data/chapters/checked/%02d.md"
        task = (s5.emit_tasks(cfg, proj, None, task_dir="data/state/tasks/self")["tasks"])[0]
        check("任务书目标是候选区",
              all(str(x).startswith("data/_pending/stage5/") for x in _targets(task)),
              _targets(task))
        for n in (1, 2):
            _w(pg.pending_path(".", 5, r5 % n), CHAPTER)
        ok, why = s5.self_done(cfg, proj)
        check("候选在、没批 → 未完成且说清在等谁", ok is False and "批准" in why, why)
        check("checked/ 没被创建", not (ws / (r5 % 1)).exists())
        for n in (1, 2):
            pg.record_approval(".", 5, r5 % n, by="unit-test")
        ok, why = s5.self_done(cfg, proj)
        check("批准后检查稿已合成", (ws / (r5 % 1)).exists() and (ws / (r5 % 2)).exists())
        check("账本仍缺 → 仍判未完成（旧判据没被顶掉）",
              ok is False and "检查记录" in why, why)
        # 2026-11-12 实测踩到的洞：稿子齐了但账本没写时，emit_tasks 曾返回 done=True，
        # 阶段被直接标完成 —— "无需再检查"和"缺账本"不能同时成立。
        res5 = s5.emit_tasks(cfg, proj, None, task_dir="data/state/tasks/self")
        check("账本缺 → emit_tasks 不许说‘无需再检查’",
              not res5.get("done") and bool(res5.get("tasks")), res5.get("hint"))

        print("\n【D】阶段 6（润色稿）：候选没批 → refined/ 不写")
        r6 = "data/chapters/refined/%02d.md"
        refined_src = (ws / (r5 % 1)).read_text(encoding="utf-8")
        task = (s6.emit_tasks(cfg, proj, None, task_dir="data/state/tasks/self")["tasks"])[0]
        check("任务书目标是候选区",
              all(str(x).startswith("data/_pending/stage6/") for x in _targets(task)),
              _targets(task))
        for n in (1, 2):
            _w(pg.pending_path(".", 6, r6 % n), refined_src)
        ok, why = s6.self_done(cfg, proj)
        check("候选在、没批 → 未完成且说清在等谁", ok is False and "批准" in why, why)
        check("refined/ 没被创建", not (ws / (r6 % 1)).exists())
        for n in (1, 2):
            pg.record_approval(".", 6, r6 % n, by="unit-test")
        ok, why = s6.self_done(cfg, proj)
        check("批准后 → 判完成", ok is True, why)
        check("refined 已合成", (ws / (r6 % 1)).exists())
    finally:
        os.chdir(cwd)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
