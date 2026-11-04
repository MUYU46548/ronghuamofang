# -*- coding: utf-8 -*-
"""空素材必须停下来问，而不是凭空编设定集（2026-11-04，作者第 11 条）。

作者原话：“如果有问题，会停下来问吗？比如根本没有素材。”

## 核实结果（这条比预期好）

硬闸**早就有了**，两条引擎路径各一份、判据一致：

- `run_stage`（direct / hermes）：`stage1_consolidate.py:484` —— 无素材且无既有设定集 →
  `set_stage(1, "failed", error="materials/raw 无素材且无既有设定集")`。
- `emit_tasks`（engine: self）：扫完 manifest 与碎片后 `blocked=`
  “materials/raw 没有素材（…），也没有既有设定集：请先放素材。”
  orchestrator 收到 blocked → 阶段标 failed、退出码 1，**不出任务书**。

所以本轮要补的不是功能，是**判据**：这套“会停下来”必须有用例守着，
否则下一次重构把 blocked 改成出任务书（= 让执行方凭空写设定集），谁也不会发现。

## 判据（每条都能变红）

- N1 空素材 + 无设定集（engine: self）→ rc=1、阶段 1 `failed`、错误里点名“素材”、
  **一份任务书都不产**（“凭空编设定集”就是这个形态）
- N2 同一工作区放入素材 → rc=3 且真的出了任务书（闸门不许误杀）
- N3 空素材但有**有效**设定集 → done（复用，不拦 —— 重跑设定阶段是正常操作）

用法：python tests/unit/test_stage1_no_material.py
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
                    "personality": "谨慎、话少", "goal": "查清父亲失踪", "appearance": "青布短打"}],
    "world": {"locations": [{"name": "青石镇", "description": "山道尽头的旧镇。"}]},
    "plot_fragments": [{"summary": "阿禾发现药柜暗格", "status": "unused"}],
    "timeline": [{"when": "第一年春", "what": "父亲失踪"}],
}


def _w(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_ws(with_material=False):
    ws = Path(tempfile.mkdtemp(prefix="nf_nomat_"))
    _w(ws / "config" / "system.yaml",
       "engine: self\nchapter:\n  target_words: [5000, 9000]\n  quality_threshold: 6\n"
       "gates:\n  require_approval: [2]\n  auto_retry: false\n  quality_gate: false\n")
    _w(ws / "config" / "project.yaml",
       "book:\n  name: 空素材夹具\n  genre: 悬疑\n  chapters: 2\n  target_words: 20000\n"
       "materials:\n  dir: materials/raw\n  use_scraps: false\n")
    (ws / "materials" / "raw").mkdir(parents=True, exist_ok=True)
    if with_material:
        _w(ws / "materials" / "raw" / "01.md", "主角是药铺学徒阿禾，父亲三年前失踪。\n")
    shutil.copytree(ROOT / "prompts", ws / "prompts")
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    return ws


def task_files(ws):
    d = Path(ws) / "data" / "state" / "tasks" / "self" / "stage1"
    return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []


def main():
    print("=" * 66)
    print("空素材：必须停下来问")
    import orchestrator
    from utils.progress_manager import ProgressManager

    cwd = os.getcwd()

    print("\n【N1】空素材 + 无设定集 → failed，且不产任务书")
    ws = make_ws(with_material=False)
    try:
        os.chdir(ws)
        rc = orchestrator.run(from_stage=1)
        pg = ProgressManager(Path("data/state/progress.json"))
        s1 = pg.data["stages"]["1"]
        check("N1 退出码 = 1（失败，不是 3 等外部执行）", rc == 1, rc)
        check("N1 阶段 1 标 failed", s1.get("status") == "failed", s1)
        check("N1 错误里点名素材（人能照着做）",
              "素材" in str(s1.get("error") or ""), s1.get("error"))
        check("N1 一份任务书都没出（不许让执行方凭空写设定集）",
              task_files(ws) == [], task_files(ws))
    finally:
        os.chdir(cwd)
        shutil.rmtree(ws, ignore_errors=True)

    print("\n【N2】放进素材后 → rc=3 且真出任务书（闸门不误杀）")
    ws = make_ws(with_material=True)
    try:
        os.chdir(ws)
        rc = orchestrator.run(from_stage=1)
        pg = ProgressManager(Path("data/state/progress.json"))
        s1 = pg.data["stages"]["1"]
        check("N2 退出码 = 3（等外部执行）", rc == 3, rc)
        check("N2 阶段 1 awaiting_external", s1.get("status") == "awaiting_external", s1)
        check("N2 真的出了任务书", len(task_files(ws)) >= 1, task_files(ws))
    finally:
        os.chdir(cwd)
        shutil.rmtree(ws, ignore_errors=True)

    print("\n【N3】空素材但有有效设定集 → 直接复用（重跑设定阶段是正常操作）")
    ws = make_ws(with_material=False)
    try:
        _w(ws / "data" / "setting" / "setting.json",
           json.dumps(SETTING, ensure_ascii=False, indent=2))
        os.chdir(ws)
        rc = orchestrator.run(from_stage=1)
        pg = ProgressManager(Path("data/state/progress.json"))
        s1 = pg.data["stages"]["1"]
        check("N3 阶段 1 判完成（不拦）", s1.get("status") == "done", s1)
        check("N3 不产 stage1 任务书", task_files(ws) == [], task_files(ws))
        print("      （rc=%s —— 阶段 2 会停下出大纲任务书，属正常）" % rc)
    finally:
        os.chdir(cwd)
        shutil.rmtree(ws, ignore_errors=True)

    print("\n【N4】接线：两条引擎路径各有一份判据（防重构时丢掉）")
    s1src = (ROOT / "scripts" / "stage1_consolidate.py").read_text(encoding="utf-8")
    check("N4 run_stage 侧有“无素材且无设定集 → failed”",
          'error="materials/raw 无素材且无既有设定集"' in s1src)
    check("N4 emit_tasks 侧有 blocked=“请先放素材”",
          "blocked=" in s1src and "请先放素材" in s1src)
    orch = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("N4 orchestrator 收到 blocked → 判 failed（不是继续出任务书）",
          'if res.get("blocked")' in orch)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
