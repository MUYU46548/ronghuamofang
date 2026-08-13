# -*- coding: utf-8 -*-
"""NovelForge 主调度器（orchestrator）。

职责（架构文档 v2 2.2/2.3）：
- 加载 config/system.yaml + config/project.yaml；
- 初始化 progress.json / runs.db / cost_tracker；
- 按序执行阶段 1→7，跳过已完成（断点续跑）；
- 阶段 2 审批门：未 approved 时下游不启动；
- 预算熔断：cost 状态 pause 时停止全部调度。

用法：
  python scripts/orchestrator.py               # 全流程（断点续跑）
  python scripts/orchestrator.py --from 4      # 从阶段4开始
  python scripts/orchestrator.py --stage 7     # 只跑阶段7
"""
import argparse
import sys
from pathlib import Path

import yaml

from utils.file_io import read_text
from utils.progress_manager import ProgressManager
from utils.db import RunDB
from utils.cost_tracker import CostTracker

import stage1_consolidate as s1
import stage2_outline as s2
import stage3_chapter_outline as s3
import stage4_writing as s4
import stage5_check as s5
import stage6_polish as s6
import stage7_convert as s7
import snapshot as snap

STAGES = {1: s1, 2: s2, 3: s3, 4: s4, 5: s5, 6: s6, 7: s7}


def load_config():
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    return cfg, proj


def run(from_stage=1, only_stage=None, client=None):
    cfg, proj = load_config()
    progress = ProgressManager("data/state/progress.json")
    progress.data["project"] = proj.get("book", {}).get("name", "")
    db = RunDB("logs/runs.db")
    try:
        budget = cfg.get("budget", {})
        cost = CostTracker(db, limit_yuan=budget.get("limit_yuan", 300),
                           warn_ratio=budget.get("warn_ratio", 0.7))
        run_id = db.start_run(plan_json=f"from={from_stage} only={only_stage}")

        order = [only_stage] if only_stage else range(from_stage, 8)
        for n in order:
            # 预算熔断
            state, spent = cost.status(run_id)
            if state == "pause":
                print(f"[orchestrator] 预算超限（已用 {spent:.2f} 元），熔断暂停")
                progress.data["budget"]["paused"] = True
                progress.save()
                db.finish_run(run_id, "paused")
                return 2
            # 阶段 2 审批门
            if n > 2 and progress.stage_status(2) == "done" and not progress.is_approved(2):
                print("[orchestrator] 阶段2 未获人工确认，暂停。"
                      "请审阅 data/outline/global.md 后在主会话确认（set_approved(2)）")
                db.finish_run(run_id, "waiting_approval")
                return 3
            # 断点：已完成阶段跳过
            if progress.stage_status(n) == "done" and not only_stage:
                print(f"[orchestrator] 阶段{n} 已完成，跳过")
                continue
            print(f"\n[orchestrator] ==== 开始阶段 {n} ====")
            ok, msg = STAGES[n].run_stage(cfg, proj, progress, db, cost, client=client,
                                          task_dir="data/state/tasks", run_id=run_id)
            print(f"[orchestrator] 阶段{n} 结果: {msg}")
            if ok:
                try:
                    snap.snapshot(f"stage{n}_done")  # 阶段成功 → 快照
                except Exception as e:
                    print(f"[orchestrator] 快照失败（不影响流程）: {e}")
            if not ok:
                if cfg.get("gates", {}).get("pause_on_failure", True):
                    print("[orchestrator] 阶段失败，暂停等待处理（可重跑或人工介入）")
                    db.finish_run(run_id, "failed")
                    return 1
            state, spent = cost.status(run_id)
            print(f"[orchestrator] 当前成本: {spent:.4f} 元（状态 {state}）")

        db.finish_run(run_id, "done")
        print("\n[orchestrator] 全部阶段完成 ✅")
        return 0
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="NovelForge 主调度器")
    parser.add_argument("--from", dest="from_stage", type=int, default=1, help="起始阶段")
    parser.add_argument("--stage", type=int, default=None, help="只运行指定阶段")
    args = parser.parse_args()
    sys.exit(run(args.from_stage, args.stage))


if __name__ == "__main__":
    main()
