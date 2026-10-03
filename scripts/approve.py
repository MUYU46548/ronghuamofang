# -*- coding: utf-8 -*-
"""NovelForge 阶段审批 CLI。

安全护栏：
  - 审批前自动快照（approve_stage{N}），运行失败可回退
  - 撤销前自动快照（revoke_stage{N}），可恢复
  - **Agent 模式守卫**（2026-10-03）：gates.agent_mode=true 时，**非人工来源**
    拒绝审批执行 —— 外部 Agent 可读、可跑流水线，但不能代替用户审批。
    HTTP 层对 /approve 早有 403 守卫，但 CLI 这条路原先没有任何检查
    （Agent 照着文档跑 `approve.py --stage 2` 就能绕过），本块把后门堵上。
    人工来源的证据：交互式 TTY / MOFANG_SOURCE=gui / 显式 --human。
    详见 utils/agent_guard.py。

用法：
  python scripts/approve.py --stage 2          # 确认阶段2（整体大纲）
  python scripts/approve.py --stage 2 --revoke # 撤销确认
  python scripts/approve.py --stage 2 --human  # 声明"我是人"（无 TTY 的环境里用）
"""
import argparse
import os
import sys

import yaml

from utils import agent_guard
from utils.file_io import read_text
from utils.progress_manager import ProgressManager


def _load_system_cfg():
    """读 config/system.yaml（只看 gates.agent_mode；失败按未开启处理）。"""
    try:
        return yaml.safe_load(read_text("config/system.yaml")) or {}
    except Exception:                                       # noqa: BLE001
        return {}


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段审批（默认用于阶段2 大纲确认）")
    parser.add_argument("--stage", type=int, required=True, help="阶段号（当前审批门为 2）")
    parser.add_argument("--revoke", action="store_true", help="撤销审批")
    parser.add_argument("--human", action="store_true",
                        help="显式声明本次为人工操作（Agent 模式下无 TTY 时的逃生门）")
    args = parser.parse_args()

    # Agent 模式守卫（CLI 层）：判据与 HTTP 层同源（utils/agent_guard）。
    # 必须在**任何写操作之前**：一旦快照/改 progress.json 就晚了。
    if agent_guard.agent_mode_enabled(_load_system_cfg()):
        is_human, evidence = agent_guard.cli_human_evidence(explicit_human=args.human)
        action = "/approve?revoke" if args.revoke else "/approve"
        if not is_human:
            print(agent_guard.refusal_message(action, evidence, entry="approve.py CLI"))
            agent_guard.log_audit(action, method="CLI", status=403,
                                  source=agent_guard.normalize_source(
                                      os.environ.get("MOFANG_SOURCE")) or "unknown",
                                  detail="blocked: " + evidence)
            return 1
        agent_guard.log_audit(action, method="CLI", status=0, source="human",
                              detail="allowed: " + evidence)

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

    # A3：阶段 1 的审批 = 设定集定稿 → 冻结 canon 快照。
    # 之后 stage2/3（大纲）只读快照，改素材不再悄悄扰动已定稿的大纲；
    # 撤销审批则把快照撤掉，退回读活稿。
    if args.stage == 1:
        try:
            from utils import material_state as mstate
            if args.revoke:
                if mstate.drop_canon():
                    print("[approve] canon 快照已撤销（大纲阶段退回读活稿）")
            else:
                ok, msg = mstate.freeze_canon()
                print(f"[approve] {msg if ok else 'WARN ' + msg}")
        except Exception as e:                                # noqa: BLE001
            print(f"[approve] WARN canon 快照处理失败（不阻断审批）: {type(e).__name__}: {e}")

    if not args.revoke and status != "done":
        print(f"[approve] 注意：阶段{args.stage} 尚未完成（{status}），审批在完成后才生效")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
