# -*- coding: utf-8 -*-
"""NovelForge 阶段审批 CLI。

安全护栏：
  - 审批前自动快照（approve_stage{N}），运行失败可回退
  - 撤销前自动快照（revoke_stage{N}），可恢复

用法：
  python scripts/approve.py --stage 2          # 确认阶段2（整体大纲）
  python scripts/approve.py --stage 2 --revoke # 撤销确认
"""
import argparse
import sys

from utils.progress_manager import ProgressManager


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段审批（默认用于阶段2 大纲确认）")
    parser.add_argument("--stage", type=int, required=True, help="阶段号（当前审批门为 2）")
    parser.add_argument("--revoke", action="store_true", help="撤销审批")
    args = parser.parse_args()

    # 安全护栏：审批/撤销前自动快照
    try:
        from snapshot import snapshot as make_snapshot
        action = "revoke" if args.revoke else "approve"
        snap = make_snapshot(f"{action}_stage{args.stage}")
        print(f"[approve] 快照已保存: {snap}")
    except Exception as e:
        print(f"[approve] 快照失败（继续执行）: {e}")

    pm = ProgressManager("data/state/progress.json")
    pm.set_approved(args.stage, not args.revoke)
    action = "已撤销" if args.revoke else "已确认"
    status = pm.stage_status(args.stage)
    print(f"[approve] 阶段{args.stage} {action}（当前状态: {status}）")
    if not args.revoke and status != "done":
        print(f"[approve] 注意：阶段{args.stage} 尚未完成（{status}），审批在完成后才生效")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
