# -*- coding: utf-8 -*-
"""NovelForge 阶段审批 CLI。

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
