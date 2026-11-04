# -*- coding: utf-8 -*-
"""engine=self 下也要出“零 token 的确定性报告”（2026-11-04，试跑第 13 条）。

## 要治的病

作者第 13 条：“经典打不开素材体检报告，文件不存在。”

表面上这是界面挂了个死按钮（已修：产物速览按盘上事实上报）。但盘上**本来就该有**
那份报告 —— `_post_stage(1)` 会在阶段 1 成功后调 `run_material_review` 生成它。
而 `engine: self` 走的是**另一条分支** `_self_stage()`：判完完成就 return，
根本不经过 `_post_stage` → 这份报告在 self 下永远不生成。

判据要说清的是**哪类钩子**可以跳过：self 的承诺是“不调用模型”，所以
auto_rewrite / review_after_stage4 / 校对（都要模型）跳过是对的；
素材体检是纯确定性统计（读 setting.json + normalized/ 数一数），零 token，
与引擎无关，必须出。

## 判据（每条都能变红）

- R1 engine=self 跑完阶段 1 → `data/setting/material_review.md` 真的存在且非空
- R2 报告内容是真体检（有统计结论，不是空壳）
- R3 反证：把 `run_material_review` 换成空实现 → 同一条路不再生成（证明 R1 是真跑的）
- R4 要模型的钩子仍然没跑（self 的零 token 承诺没被破坏：无 review_report / auto_rewrite 产物）
- R5 接线：两个“阶段已放行”分支（产物本来就齐 / emit 说无需外部执行）都调了 `_side_reports`

用法：python tests/unit/test_self_side_reports.py
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
                           ("" if cond else "  → %s" % (detail,))))


SETTING = {
    "characters": [{"name": "阿禾", "identity": "药铺学徒，替父亲守着半间铺子",
                    "personality": "谨慎、话少、记性好", "goal": "查清父亲三年前为什么失踪",
                    "appearance": "青布短打，左手虎口有道旧疤"}],
    "world": {"locations": [{"name": "青石镇", "description": "山道尽头的旧镇，常年落雨。"}]},
    "plot_fragments": [{"summary": "阿禾发现药柜暗格是空的，灰上有指印", "status": "unused"}],
    "timeline": [{"when": "第一年春", "what": "父亲失踪"}],
}


def _w(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_ws():
    ws = Path(tempfile.mkdtemp(prefix="nf_selfrep_"))
    _w(ws / "config" / "system.yaml",
       "engine: self\nchapter:\n  target_words: [5000, 9000]\n  quality_threshold: 6\n"
       "gates:\n  require_approval: [2]\n  auto_retry: false\n  quality_gate: false\n"
       "  auto_rewrite: false\n  review_after_stage4: false\n  proofread_after_polish: false\n")
    _w(ws / "config" / "project.yaml",
       "book:\n  name: 自任务报告夹具\n  genre: 悬疑\n  chapters: 2\n  target_words: 20000\n"
       "materials:\n  dir: materials/raw\n  use_scraps: false\n")
    _w(ws / "materials" / "raw" / "01.md", "主角是药铺学徒阿禾，父亲三年前失踪。\n")
    shutil.copytree(ROOT / "prompts", ws / "prompts")
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    _w(ws / "data" / "setting" / "setting.json",
       json.dumps(SETTING, ensure_ascii=False, indent=2))
    return ws


def main():
    print("=" * 66)
    print("engine=self 也要出确定性报告")
    import orchestrator

    print("\n【R1】engine=self 跑阶段 1 → 素材体检报告必须生成")
    ws = make_ws()
    cwd = os.getcwd()
    try:
        os.chdir(ws)
        rc = orchestrator.run(from_stage=1)
        rep = Path("data/setting/material_review.md")
        check("R1 文件存在", rep.is_file(),
              "阶段 1 后 data/setting/material_review.md 没生成（self 分支漏了确定性报告）")
        body = rep.read_text(encoding="utf-8") if rep.is_file() else ""
        check("R1 非空（不是 0 字节占位）", len(body.strip()) > 50, len(body))
        check("R2 是真体检（有统计结论，不是空壳）",
              ("角色" in body or "人物" in body) and ("字" in body or "条" in body),
              body[:120])
        check("R4 要模型的钩子仍然没跑（零 token 承诺没破）",
              not Path("data/outline/review_report.json").exists()
              and not Path("data/outline/proofread_report.md").exists())
        print("      （rc=%s —— 阶段 2 会停下出任务书，属正常）" % rc)

        print("\n【R3】反证：把生成器换成空实现 → 同一条路不再产出")
        ws2 = make_ws()
        saved = orchestrator.run_material_review
        try:
            os.chdir(ws2)
            orchestrator.run_material_review = lambda progress: ([], [])
            orchestrator.run(from_stage=1)
            check("R3 空实现时不生成报告（说明 R1 是真的跑出来的）",
                  not Path("data/setting/material_review.md").exists(),
                  "换成空实现仍有文件 → R1 可能是别处产出的，判据无效")
        finally:
            orchestrator.run_material_review = saved
    finally:
        os.chdir(cwd)
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(ws2, ignore_errors=True) if "ws2" in dir() else None

    print("\n【R5】接线：两个“已放行”分支都要调")
    src = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("R5 定义了 _side_reports", "def _side_reports()" in src)
    check("R5 阶段完成判定后调用", src.count("_side_reports()") >= 3,
          src.count("_side_reports()"))   # 定义 1 次 + 调用 2 次
    check("R5 只跑零 token 的报告（n == 1 → run_material_review）",
          "if n == 1:" in src and "run_material_review(progress)" in src)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
