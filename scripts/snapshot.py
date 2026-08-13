# -*- coding: utf-8 -*-
"""项目快照（P0.6）：把关键产物复制到 history/{ts}_{label}/，保留最近 N 份。

背景：data/ 不进 Git（.gitignore 注释由 history/ 快照承担版本职责），
此前 history/ 为空、无任何快照机制。本脚本补上：
- orchestrator 每阶段完成后自动调用 snapshot(f"stage{n}_done")；
- 手动触发：python scripts/snapshot.py "开写前"；查看：--list。

用法：
  python scripts/snapshot.py "label"        # 创建快照
  python scripts/snapshot.py --list         # 列出快照
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 快照覆盖的关键产物（文件或目录；目录整棵复制）
SNAPSHOT_ITEMS = [
    "data/state/progress.json",
    "data/setting/setting.json",
    "data/setting/materials_manifest.json",
    "data/outline",
    "data/summaries/rolling.md",
    "data/chapters",
    "data/merged/book.md",
]
KEEP = 10  # 保留最近 N 份，超出删除最旧


def snapshot(label="manual", keep=KEEP, history_dir="history"):
    """创建快照并清理旧快照。返回快照目录路径。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(history_dir) / f"{ts}_{label}"
    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    for item in SNAPSHOT_ITEMS:
        src = Path(item)
        if not src.exists():
            continue
        dst = dest / item
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        elif src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            copied += 1

    # 清理旧快照（按时间戳目录名排序）
    snaps = sorted([p for p in Path(history_dir).iterdir()
                    if p.is_dir() and p.name[:8].isdigit()])
    removed = 0
    for old in snaps[:-keep]:
        shutil.rmtree(old)
        removed += 1

    print(f"[snapshot] {dest}（复制 {copied} 项；清理旧快照 {removed} 份）")
    return dest


def list_snapshots(history_dir="history"):
    h = Path(history_dir)
    if not h.exists():
        print("[snapshot] history/ 为空")
        return
    snaps = sorted([p for p in h.iterdir() if p.is_dir() and p.name[:8].isdigit()])
    for p in snaps:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        print(f"  {p.name}  ({size/1024:.0f} KB)")


def main():
    parser = argparse.ArgumentParser(description="NovelForge 项目快照")
    parser.add_argument("label", nargs="?", default=None, help="快照标签（如 stage2_done）")
    parser.add_argument("--list", action="store_true", help="列出已有快照")
    args = parser.parse_args()

    if args.list:
        list_snapshots()
        return 0
    if not args.label:
        print("用法: python scripts/snapshot.py \"label\" | --list")
        return 1
    snapshot(args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
