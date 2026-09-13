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
import os
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


def run_material_review(progress):
    """stage1 成功后自动生成素材完备度体检报告（失败不阻断，同 vault_links 模式）。

    返回 (thin_names, warn_names)，供 stage2 审批门提醒使用。
    """
    try:
        import material_review as mr
        char_results, world_items, thin_names, warn_names = \
            mr.review("data/setting/setting.json", "data/setting/normalized")
        out = "data/setting/material_review.md"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        from utils.file_io import write_text
        write_text(out, mr.format_report(char_results, world_items, thin_names, warn_names,
                                         "data/setting/setting.json", "data/setting/normalized"))
        if thin_names:
            print(f"[orchestrator] 素材体检：{len(thin_names)} 个碎片角色"
                  f"（{'、'.join(thin_names)}），报告 → {out}")
        else:
            print(f"[orchestrator] 素材体检：无碎片角色，报告 → {out}")
        return thin_names, warn_names
    except Exception as e:
        print(f"[orchestrator] 素材体检失败（不影响流程）: {e}")
        return [], []


def load_config():
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    return cfg, proj


def _warn_rewrite_conflict(cfg):
    """auto_rewrite 与 auto_refine 同时开启会改同一批章节 → 启动时明确告警。"""
    gates = cfg.get("gates", {}) or {}
    if gates.get("auto_rewrite") and gates.get("auto_refine"):
        print("[orchestrator] ⚠️ 告警：gates.auto_rewrite 与 gates.auto_refine 同时开启，"
              "两者都会改写同一批章节。")
        print("            以 auto_rewrite 为先；batch_refine 请仅处理审稿报告中的"
              "非 quality 类问题，避免重复改稿。")


def run(from_stage=1, only_stage=None, client=None):
    cfg, proj = load_config()
    _warn_rewrite_conflict(cfg)
    progress = ProgressManager("data/state/progress.json")
    # 多书隔离（P0.7 #10）：progress 记录的书名与 project.yaml 不一致时提示
    prev_book = progress.data.get("project", "")
    book_name = proj.get("book", {}).get("name", "")
    if prev_book and prev_book != book_name:
        print(f"[orchestrator] 注意：progress 记录的书名「{prev_book}」"
              f"与 project.yaml「{book_name}」不一致")
        print("             换书请用: python scripts/switch_book.py --archive "
              f"(归档「{prev_book}」) / --restore \"书名\"；继续将按 project.yaml 处理")
    progress.data["project"] = book_name
    db = RunDB("logs/runs.db")
    try:
        budget = cfg.get("budget", {})
        limit_yuan = float(os.environ.get("BUDGET_LIMIT_YUAN") or budget.get("limit_yuan", 300))
        cost = CostTracker(db, limit_yuan=limit_yuan,
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
            # 打回提示：该阶段曾被打回（reject.py），重跑前告知原因
            st_n = progress.data["stages"].get(str(n), {})
            if st_n.get("rejected"):
                print(f"[orchestrator] 阶段{n} 曾被打回"
                      f"（{st_n.get('rejected_at', '')}）：{st_n['rejected']}，正在重跑")
            # 审批门：require_approval 中已完成但未人工确认的阶段 → 暂停
            req = cfg.get("gates", {}).get("require_approval", [2])
            pending_approvals = [s for s in req
                                 if n > s and progress.stage_status(s) == "done"
                                 and not progress.is_approved(s)]
            if pending_approvals:
                for s in pending_approvals:
                    if s == 2:
                        print("[orchestrator] 阶段2 未获人工确认，暂停。"
                              "请审阅 data/outline/global.md 后在主会话确认"
                              "（approve.py --stage 2）")
                    elif s == 6:
                        print("[orchestrator] 阶段6（润色）未获人工确认，暂停。"
                              "请审阅 data/chapters/refined/ 后在主会话确认"
                              "（approve.py --stage 6）；不满意可用"
                              "reject.py --stage 6 \"意见\" 打回重跑")
                    else:
                        print(f"[orchestrator] 阶段{s} 未获人工确认，暂停。"
                              f"（approve.py --stage {s}）")
                # P1.5：审批前若设定集有碎片角色，提醒先补全（可配开关关闭）
                if 2 in pending_approvals and cfg.get("gates", {}).get(
                        "setting_refine_reminder", True):
                    try:
                        import material_review as mr
                        _, _, thin_names, _ = mr.review("data/setting/setting.json",
                                                        "data/setting/normalized")
                        if thin_names:
                            print("[orchestrator] 提醒：设定集存在碎片角色"
                                  f"（{'、'.join(thin_names)}）。"
                                  "建议先审 data/setting/material_review.md，"
                                  "并跑 python scripts/setting_refine.py 补全后再审批。")
                    except Exception as e:
                        print(f"[orchestrator] 补全提醒检查失败（不影响流程）: {e}")
                db.finish_run(run_id, "waiting_approval")
                return 3
            # 断点：已完成阶段跳过
            if progress.stage_status(n) == "done" and not only_stage:
                print(f"[orchestrator] 阶段{n} 已完成，跳过")
                continue
            print(f"\n[orchestrator] ==== 开始阶段 {n} ====")
            # 安全护栏：阶段执行前快照，执行中崩掉可恢复
            if not only_stage:
                try:
                    snap.snapshot(f"before_stage{n}")
                    print(f"[orchestrator] 阶段{n} 前快照已保存")
                except Exception as e:
                    print(f"[orchestrator] 阶段前快照失败（不影响流程）: {e}")
            ok, msg = STAGES[n].run_stage(cfg, proj, progress, db, cost, client=client,
                                          task_dir="data/state/tasks", run_id=run_id)
            print(f"[orchestrator] 阶段{n} 结果: {msg}")
            if ok:
                # 重跑成功 → 清除打回标记
                st_now = progress.data["stages"].get(str(n), {})
                if st_now.pop("rejected", None) is not None:
                    progress.save()
                try:
                    snap.snapshot(f"stage{n}_done")  # 阶段成功 → 快照
                except Exception as e:
                    print(f"[orchestrator] 快照失败（不影响流程）: {e}")
                if n == 1:
                    # P1.5：stage1 归并完成后自动生成素材体检报告（不阻断）
                    run_material_review(progress)
                if n == 4 and (cfg.get("gates", {}) or {}).get("auto_rewrite", False):
                    # 方向3 质量自评闭环：重写 quality<阈值 的章节
                    # 必须排在 review_after_stage4 审稿分支之前（F5），
                    # 使审查看到的已是重写后的章节。失败不阻断流程。
                    try:
                        import auto_rewrite as ar
                        ok_ar, msg_ar, _stats = ar.run_auto_rewrite(
                            cfg, proj, progress, db=db, cost=cost, run_id=run_id,
                            client=client, task_dir="data/state/tasks")
                        print(f"[orchestrator] 自动重写结果: {msg_ar}")
                    except Exception as e:
                        print(f"[orchestrator] 自动重写失败（不影响流程）: {e}")
                if n == 4 and cfg.get("gates", {}).get("review_after_stage4", False):
                    # P0 审稿→修稿闭环：stage4 完成后自动调用审查
                    try:
                        import chapter_review as cr
                        print("[orchestrator] stage4 完成，启动章节审查...")
                        ok_rev, msg_rev = cr.run_review(
                            scope=None,
                            report_path="data/outline/review_report.json",
                            dry_run=False,
                            client=client,
                        )
                        print(f"[orchestrator] 审查结果: {msg_rev}")
                        if ok_rev:
                            progress.set_review_report("data/outline/review_report.json")
                            print("[orchestrator] 请审阅 data/outline/review_report.md，"
                                  "然后运行: python scripts/batch_refine.py")
                            db.finish_run(run_id, "waiting_review")
                            return 3  # 复用 waiting_approval 语义（等待用户审阅）
                    except Exception as e:
                        print(f"[orchestrator] 审查失败（不影响流程）: {e}")
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
