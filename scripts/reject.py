# -*- coding: utf-8 -*-
"""打回/重跑正规化 CLI（P1 新增：reject 门）。

替代"手动清产物 + --from N 重跑"的脆弱流程：
  1. 记录打回原因与时间（progress.json 的 stages[N].rejected）
  2. 将阶段 N 及下游状态重置为 pending（断点续跑时不再跳过）
  3. 按产物映射清理阶段 N 及下游的产物目录（history/ 备份保留，可回退）

用法：
  python scripts/reject.py --stage 6 "润色过度，保留原稿风格"
  python scripts/reject.py --stage 6 --reason "..." --dry-run   # 只展示将清理的内容
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from utils.file_io import write_text
from utils.progress_manager import ProgressManager

# 打回阶段 N 时需清理的产物目录（含该阶段自身产物与下游全部）
# 注意：history/ 备份目录一律保留（可回退）；data/outline/global.md 与
#       data/setting/setting.json 是审批/设定对象，打回不删除（靠精修通道迭代）
DOWNSTREAM_ARTIFACTS = {
    # 打回 2：逐章大纲 + 章节 + 合并稿 + Word 全部重做
    2: ["data/outline/chapters", "data/chapters/raw", "data/chapters/checked",
        "data/chapters/refined", "data/merged", "output"],
    # 打回 3：章节 + 合并稿 + Word
    3: ["data/chapters/raw", "data/chapters/checked", "data/chapters/refined",
        "data/merged", "output"],
    # 打回 4：原稿 + 检查 + 润色 + 合并稿 + Word
    4: ["data/chapters/raw", "data/chapters/checked", "data/chapters/refined",
        "data/merged", "output"],
    # 打回 5：检查 + 润色 + 合并稿 + Word
    5: ["data/chapters/checked", "data/chapters/refined", "data/merged", "output"],
    # 打回 6：润色 + 合并稿 + Word（润色审阅门不满意时的标准打回）
    6: ["data/chapters/refined", "data/merged", "output"],
    # 打回 7：合并稿 + Word（重新出成品）
    7: ["data/merged", "output"],
}

STAGE_LABEL = {
    1: "素材/设定集", 2: "整体大纲", 3: "逐章大纲", 4: "写作",
    5: "检查", 6: "润色", 7: "Word 转换",
}


def collect_artifacts(stage):
    """收集阶段 N 打回时将被清理的产物路径。返回 list[Path]。"""
    paths = []
    for rel in DOWNSTREAM_ARTIFACTS.get(stage, []):
        p = Path(rel)
        if p.exists():
            paths.append(p)
    return paths


def reject_stage(pm, stage, reason="", dry_run=False):
    """执行打回。返回 (ok, 信息列表)。"""
    msgs = []

    if stage not in DOWNSTREAM_ARTIFACTS:
        return False, [f"不支持的阶段号: {stage}（支持 2-7）"]

    # 记录打回原因（阶段 N 保留 rejected 标记，供 orchestrator 提示；同时撤销审批）
    if not dry_run:
        pm.set_stage(stage, "rejected",
                     rejected=reason or "（未填原因）",
                     rejected_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        pm.data["stages"][str(stage)].pop("approved", None)
        pm.save()

    # 清理产物
    artifacts = collect_artifacts(stage)
    for p in artifacts:
        msgs.append(f"清理: {p}")
        if not dry_run:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)

    # 重置下游阶段为 pending（approved 清除）；阶段 N 保持 rejected 供提示
    for n in range(stage + 1, 8):
        if not dry_run:
            st = pm.data["stages"][str(n)]
            st["status"] = "pending"
            st.pop("approved", None)
            st.pop("rejected", None)
            st.pop("finished_at", None)
            st.pop("completed_chapters", None)
            st.pop("failed_chapters", None)
        msgs.append(f"重置: 阶段{n} → pending")
    if not dry_run:
        pm.save()

    msgs.append(f"打回完成：阶段{stage}（{STAGE_LABEL.get(stage, '')}）原因：{reason or '（未填原因）'}")
    msgs.append(f"下一步建议: python scripts/orchestrator.py --from {stage}")
    return True, msgs


def main():
    parser = argparse.ArgumentParser(description="NovelForge 打回/重跑正规化（reject 门）")
    parser.add_argument("--stage", type=int, required=True,
                        help="打回阶段号（2-7；其下游产物一并清理）")
    parser.add_argument("--reason", default="", help="打回原因（记录到 progress.json）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只展示将清理的内容，不执行")
    args = parser.parse_args()

    pm = ProgressManager("data/state/progress.json")
    ok, msgs = reject_stage(pm, args.stage, args.reason, dry_run=args.dry_run)
    for m in msgs:
        print(f"[reject] {m}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
