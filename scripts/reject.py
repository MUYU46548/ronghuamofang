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
import shutil
import sys
from datetime import datetime
from pathlib import Path

from utils.file_io import write_text
from utils.progress_manager import ProgressManager

# Artifact directories to clean when rejecting stage N (self + downstream)
DOWNSTREAM_ARTIFACTS = {
    2: ["data/outline/chapters", "data/chapters/raw", "data/chapters/checked",
        "data/chapters/refined", "data/merged", "output"],
    3: ["data/chapters/raw", "data/chapters/checked", "data/chapters/refined",
        "data/merged", "output"],
    4: ["data/chapters/raw", "data/chapters/checked", "data/chapters/refined",
        "data/merged", "output"],
    5: ["data/chapters/checked", "data/chapters/refined", "data/merged", "output"],
    6: ["data/chapters/refined", "data/merged", "output"],
    7: ["data/merged", "output"],
}

STAGE_LABEL = {
    1: "Materials", 2: "Global Outline", 3: "Chapter Outlines", 4: "Writing",
    5: "Check", 6: "Polish", 7: "Word",
}


def collect_artifacts(stage):
    """Collect artifact paths to clean when rejecting stage N."""
    paths = []
    for rel in DOWNSTREAM_ARTIFACTS.get(stage, []):
        p = Path(rel)
        if p.exists():
            paths.append(p)
    return paths


def reject_stage(pm, stage, reason="", dry_run=False):
    """Execute rejection. Returns (ok, info_list)."""
    msgs = []

    if stage not in DOWNSTREAM_ARTIFACTS:
        return False, [f"Unsupported stage: {stage} (valid: 2-7)"]

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
    for n in range(stage + 1, 8):
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
                        help="Stage to reject (2-7; downstream artifacts cleaned)")
    parser.add_argument("--reason", default="", help="Rejection reason (recorded in progress.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be cleaned without executing")
    args = parser.parse_args()

    pm = ProgressManager("data/state/progress.json")
    ok, msgs = reject_stage(pm, args.stage, args.reason, dry_run=args.dry_run)
    for m in msgs:
        print(f"[reject] {m}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
