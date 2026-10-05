# -*- coding: utf-8 -*-
# Reject / rerun normalization CLI (P1: reject gate).
"""
Replace the fragile "manual cleanup + --from N rerun" flow:
  1. Record rejection reason + timestamp (progress.json stages[N].rejected)
  2. Reset stage N + downstream status to pending
  3. Clean stage N + downstream artifact directories (history/ preserved)

Safety:
  - Auto-snapshot before rejection (history/{ts}_reject_stage{N}), recoverable

Usage:
  python scripts/reject.py --stage 6 "Over-polished, keep original style"
  python scripts/reject.py --stage 6 --reason "..." --dry-run
"""
import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from utils import agent_guard
from utils.progress_manager import ProgressManager


# Artifact directories to clean when rejecting stage N (self + downstream)
#
# ⚠️ 阶段 1 的「自己 + 下游」在字面量之后拼装（见 DOWNSTREAM_ARTIFACTS[1]）：
# 自己的产物必须清，否则打回是空操作——stage1 的增量判据
# （stage1_consolidate._pending_material_paths）读 setting.json + canon.json 判断
# 「还有哪些素材没定稿」，两者留着，重跑会判「无未定稿素材」直接空跑。
# 与 stage3 漏清 data/outline/chapters 是同一类坑（见下方 3: 的注释）。
STAGE1_OWN_ARTIFACTS = [
    "data/setting/setting.json",      # 归并产物（stage1 的 STAGE_ARTIFACTS 主项）
    "data/setting/canon.json",        # 审批冻结的定稿快照——不清则增量判据恒「无待归并」
    "data/setting/material_review.md",  # 素材体检报告（stage1 后自动产出）
    "data/setting/materials_manifest.json",
    "data/setting/merge_audit.json",
    "data/setting/vault_links.md",    # 素材→设定条目溯源（归并后自动生成）
    "data/setting/scraps_merge*.json",  # 碎片归并产物（含分批 scraps_merge_bNN.json，走 glob）
]
DOWNSTREAM_ARTIFACTS = {
    2: ["data/outline/chapters", "data/chapters/raw", "data/chapters/checked",
        "data/chapters/refined", "data/summaries", "data/merged", "output"],
    # ⚠️ stage3 **自己的产物**是 data/outline/chapters —— 原先漏了它，导致
    # "打回 stage3"是个**空操作**：目录里的旧逐章大纲一个没删，而 stage3 的断点
    # 判据那时只看 exists() → 重跑直接判"全部已存在"并 mark_stage_done。
    # 用户以为重做了，实际喂给 stage4 的还是那批被打回的大纲。
    3: ["data/outline/chapters",
        "data/chapters/raw", "data/chapters/checked", "data/chapters/refined",
        "data/summaries", "data/merged", "output"],
    # 4 也要清 summaries：rolling.md 按章累积，不清的话重跑会把新旧摘要叠在一起
    # （旧章的情节描述留在 global_summary 里，写作时被当上下文注入 → 带偏新章）。
    4: ["data/summaries",
        "data/chapters/raw", "data/chapters/checked", "data/chapters/refined",
        "data/merged", "output"],
    5: ["data/chapters/checked", "data/chapters/refined", "data/merged", "output"],
    6: ["data/chapters/refined", "data/merged", "output"],
    7: ["data/merged", "output"],
}

# 阶段 1 = 自己的产物 + 全部下游（下游清单与 stage2 逐字同源，避免两处漂移）。
# 2026-10-05 #9：stage1 陪跑闸是合法审批点（gates.material_autonomy=false，
# orchestrator.py 审批段），打回却一直被 "valid: 2-7" 挡在门外——用户实测的报错原文。
DOWNSTREAM_ARTIFACTS[1] = (
    STAGE1_OWN_ARTIFACTS
    + ["data/outline/global.md"]       # 上游作废 → 大纲本体一并清（重跑 stage2 会重出）
    + DOWNSTREAM_ARTIFACTS[2]
)

STAGE_LABEL = {
    1: "Materials", 2: "Global Outline", 3: "Chapter Outlines", 4: "Writing",
    5: "Check", 6: "Polish", 7: "Word",
}


def collect_artifacts(stage):
    """Collect artifact paths to clean when rejecting stage N."""
    paths = []
    for rel in DOWNSTREAM_ARTIFACTS.get(stage, []):
        if any(ch in rel for ch in "*?["):
            # 含通配（如 scraps_merge*.json 分批产物）→ 展开全部命中，不命中即空
            paths.extend(sorted(Path().glob(rel)))
            continue
        p = Path(rel)
        if p.exists():
            paths.append(p)
    return paths


def reject_stage(pm, stage, reason="", dry_run=False):
    """Execute rejection. Returns (ok, info_list)."""
    msgs = []

    if stage not in DOWNSTREAM_ARTIFACTS:
        return False, [f"Unsupported stage: {stage} (valid: 1-7)"]

    # Safety: snapshot before destructive ops
    if not dry_run:
        try:
            from snapshot import snapshot as make_snapshot
            snap = make_snapshot(f"reject_stage{stage}")
            msgs.append(f"Snapshot saved: {snap}")
        except Exception as e:
            msgs.append(f"Snapshot failed (continuing): {e}")

    # Record rejection reason
    if not dry_run:
        pm.set_stage(stage, "rejected",
                     rejected=reason or "(no reason)",
                     rejected_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        st = pm.data["stages"][str(stage)]
        st.pop("approved", None)
        # 人工跳过（/stage/skip）留下的标记必须一起清掉，否则 GUI 会同时显示
        # 「已跳过」和「已打回」两个矛盾徽标
        for k in ("skipped", "skipped_at", "skip_reason"):
            st.pop(k, None)
        pm.save()

    # Clean artifacts
    artifacts = collect_artifacts(stage)
    for p in artifacts:
        msgs.append(f"Clean: {p}")
        if not dry_run:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)

    # Reset downstream stages to pending
    # 上界 9（不含）与 orchestrator 的 `range(from_stage, 9)` 对齐 ——
    # 阶段 8（Markdown 分卷导出）此前被漏掉，打回后残留 done 状态，
    # 断点逻辑会让它误判"已完成"而跳过。见 progress_manager.STAGE_KEYS 的 S9 注释。
    for n in range(stage + 1, 9):
        if not dry_run:
            st = pm.data["stages"][str(n)]
            st["status"] = "pending"
            st.pop("approved", None)
            st.pop("rejected", None)
            st.pop("finished_at", None)
            st.pop("completed_chapters", None)
            st.pop("failed_chapters", None)
            for k in ("skipped", "skipped_at", "skip_reason"):
                st.pop(k, None)
        msgs.append(f"Reset: stage {n} -> pending")
    if not dry_run:
        pm.save()

    msgs.append(f"Rejected stage {stage} ({STAGE_LABEL.get(stage, '')}): {reason or '(no reason)'}")
    msgs.append(f"Next: python scripts/orchestrator.py --from {stage}")
    return True, msgs


def main():
    parser = argparse.ArgumentParser(description="NovelForge reject/rerun normalization")
    parser.add_argument("--stage", type=int, required=True,
                        help="Stage to reject (1-7; self + downstream artifacts cleaned)")
    parser.add_argument("--reason", default="", help="Rejection reason (recorded in progress.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be cleaned without executing")
    parser.add_argument("--human", action="store_true",
                        help="历史遗留声明通道（gates.agent_mode=true 下不构成人工证据，仅标准档语义）")
    args = parser.parse_args()

    # Agent 模式守卫（CLI 层，与 approve.py / HTTP 侧**同一份判据**）。
    # 打回同样是「代替用户拍板」的动作（HTTP 侧 /reject 就在禁止清单里），
    # 守卫只补 approve.py 的话，这里就还是敞着的后门。
    # agent_mode=True = 加固档（2026-10-04）：声明通道死透 + 祖先链主判据。
    # ⚠️ 必须**先于**任何写操作（progress.json 变更与产物清理都在后面）。
    # 配置读取走 CWD 项目优先（与 progress.json 同根；实现见 agent_guard）。
    if agent_guard.agent_mode_enabled(agent_guard.load_project_config()):
        is_human, evidence = agent_guard.cli_human_evidence(
            explicit_human=args.human, agent_mode=True)
        if not is_human:
            print(agent_guard.refusal_message("/reject", evidence, entry="reject.py CLI"))
            agent_guard.log_audit("/reject", method="CLI", status=403,
                                  source=agent_guard.normalize_source(
                                      os.environ.get("MOFANG_SOURCE")) or "unknown",
                                  detail="blocked: " + evidence)
            return 1
        agent_guard.log_audit("/reject", method="CLI", status=0, source="human",
                              detail="allowed: " + evidence)

    pm = ProgressManager("data/state/progress.json")
    ok, msgs = reject_stage(pm, args.stage, args.reason, dry_run=args.dry_run)
    for m in msgs:
        print(f"[reject] {m}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
