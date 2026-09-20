# -*- coding: utf-8 -*-
"""项目快照（P0.6）：把关键产物复制到 history/{ts}_{label}/，保留最近 N 份。

背景：data/ 不进 Git（.gitignore 注释由 history/ 快照承担版本职责），
此前 history/ 为空、无任何快照机制。本脚本补上：
- orchestrator 每阶段完成后自动调用 snapshot(f"stage{n}_done")；
- 手动触发：python scripts/snapshot.py "开写前"；查看：--list。

用法：
  python scripts/snapshot.py "label"              # 创建快照
  python scripts/snapshot.py --list               # 列出快照
  python scripts/snapshot.py --restore <ID>       # 恢复快照（先预览）
  python scripts/snapshot.py --restore <ID> --yes # 确认恢复

## 关于 --restore（2026-09-19 补齐）

此前快照**只写不读** —— 有 510 个文件的备份，却没有任何恢复入口，
「可回退」是纸面承诺。恢复路径从未被演练，真出事时只能手工拷贝。

安全设计（按危险程度递减）：
1. **默认 dry-run**：不带 `--yes` 只列出「将覆盖哪些文件、将删除哪些文件」。
2. **恢复前先给当前状态打快照**（`pre_restore`），恢复错了还能折返。
3. **删除需显式开启**：快照里没有、而当前 data/ 里有的文件，
   默认**保留**（`--delete-extra` 才删）。默认只做「覆盖 + 补齐」，
   因为多出来的文件通常无害，而误删是不可逆的。
4. **恢复范围限定**：只恢复 SNAPSHOT_ITEMS 覆盖的路径，
   绝不整棵 `data/` 还原（`data/books/` 归档、`data/state/tasks/` 等
   不属于快照范围，动了反而制造不一致）。
5. **ID 必须精确匹配**一个已存在的快照目录，禁止路径穿越。
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


def snapshot(label="manual", keep=KEEP, history_dir="history", dry_run=False):
    """创建快照并清理旧快照。返回快照目录路径。

    dry_run=True 时列出将清理的旧快照但不删除。

    安全护栏：history_dir 解析后必须含 "history" 段，防止误删其他目录。
    """
    # 路径验证：防止 history_dir 被误改为 data/ 等关键目录
    history_path = Path(history_dir).resolve()
    if "history" not in history_path.parts and history_path.name != "history":
        raise ValueError(
            f"[snapshot] 安全拦截：history_dir={history_path} 不含 'history' 段，"
            f"防止误删。快照目录必须位于 history/ 下。"
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = history_path / f"{ts}_{label}"
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
    snaps = sorted([p for p in history_path.iterdir()
                    if p.is_dir() and p.name[:8].isdigit()])
    removed = 0
    to_remove = snaps[:-keep]
    if dry_run:
        print(f"[snapshot] dry-run: 将清理 {len(to_remove)} 份旧快照（保留 {keep} 份）")
        for old in to_remove:
            size = sum(f.stat().st_size for f in old.rglob("*") if f.is_file())
            print(f"  将删除: {old.name} ({size/1204:.0f} KB)")
    else:
        for old in to_remove:
            # 二次验证：只删除符合快照命名规范的目录
            if not old.name[:8].isdigit() or "_" not in old.name:
                print(f"[snapshot] 跳过非快照目录: {old.name}")
                continue
            shutil.rmtree(old)
            removed += 1

    print(f"[snapshot] {dest}（复制 {copied} 项；清理旧快照 {removed} 份）")
    return dest


def _history_root(history_dir="history"):
    """解析并校验 history 目录（与 snapshot() 同一条安全护栏）。"""
    history_path = Path(history_dir).resolve()
    if "history" not in history_path.parts and history_path.name != "history":
        raise ValueError(
            f"[snapshot] 安全拦截：history_dir={history_path} 不含 'history' 段，"
            f"防止误删。快照目录必须位于 history/ 下。"
        )
    return history_path


def resolve_snapshot(snap_id, history_dir="history"):
    """把快照 ID（目录名）解析为绝对路径。支持唯一前缀匹配。

    返回 (Path, error_msg)。error_msg 非 None 表示失败。
    """
    try:
        root = _history_root(history_dir)
    except ValueError as e:
        return None, str(e)
    if not root.is_dir():
        return None, f"history/ 不存在: {root}"

    # 精确匹配优先
    target = root / snap_id
    if target.is_dir() and target.parent.resolve() == root:
        return target, None

    # 唯一前缀匹配（方便只敲时间戳）
    cands = [p for p in root.iterdir()
             if p.is_dir() and p.name[:8].isdigit() and p.name.startswith(snap_id)]
    if len(cands) == 1:
        return cands[0], None
    if not cands:
        return None, f"未找到快照 '{snap_id}'（用 --list 查看可用 ID）"
    names = ", ".join(sorted(p.name for p in cands)[:5])
    return None, f"'{snap_id}' 匹配到 {len(cands)} 个快照，请写更具体的前缀: {names}"


def _collect_plan(snap_dir, items=None):
    """对比快照与当前工作区，算出「覆盖/新增」「多余」两份清单。

    返回 (to_restore, extras)：
      · to_restore — 快照里有、需要写入工作区的**顶层条目**（相对路径）
      · extras     — 工作区有、快照里没有的文件（默认不动）

    ## extras 为什么要**递归**扫描（2026-09-19 修正）

    原实现只在「快照覆盖过的父目录」下扫**一层**：

        covered_parents = {str(Path(i).parent) for i in items if not Path(i).suffix}

    于是 `data/outline`、`data/chapters` 进了列表，但它们的**子目录**
    （`data/outline/chapters`、`data/chapters/raw`）没进 —— 新增的
    `data/chapters/raw/99.md` 落在子目录里，永远检测不到，
    `--delete-extra` 对「快照后新增的章节」完全失效。

    「目录型 item」的语义本来就是**整棵子树**（`shutil.copytree` 就是全量复制），
    所以 extras 也必须按同一口径递归比对，否则两者不对称。
    """
    items = items or SNAPSHOT_ITEMS
    to_restore, extras = [], []
    for item in items:
        src = snap_dir / item
        if src.exists():
            to_restore.append(item)

    def _scan(pdir, sdir):
        """递归比对目录，收集「工作区有、快照没有」的**文件**（相对路径）。"""
        if not pdir.is_dir():
            return
        for child in sorted(pdir.iterdir()):
            counterpart = sdir / child.name
            if child.is_dir():
                if counterpart.is_dir():
                    _scan(child, counterpart)
                else:
                    # 工作区多了整个目录：目录下的文件逐个列出，
                    # 让用户看到「具体会删哪些」，而不是一个笼统的目录名。
                    for f in sorted(child.rglob("*")):
                        if f.is_file():
                            extras.append(str(f).replace("\\", "/"))
            elif not counterpart.exists():
                extras.append(str(child).replace("\\", "/"))

    # 只在**目录型 item** 下扫（文件型 item 没有「多余」的概念）
    for item in items:
        if Path(item).suffix:
            continue
        _scan(Path(item), snap_dir / item)
    return to_restore, extras


def restore_snapshot(snap_id, history_dir="history", yes=False, delete_extra=False,
                     items=None):
    """从快照恢复。返回 (ok, messages)。

    yes=False（默认）= dry-run，只打印计划不做任何改动。
    delete_extra=True 才删除「快照里没有」的额外文件（默认保留）。
    """
    msgs = []
    snap_dir, err = resolve_snapshot(snap_id, history_dir)
    if err:
        return False, [err]

    to_restore, extras = _collect_plan(snap_dir, items)
    if not to_restore:
        return False, [f"快照 {snap_dir.name} 内不含任何可恢复产物（可能建成时 data/ 为空）"]

    msgs.append(f"快照: {snap_dir.name}")
    msgs.append(f"将恢复 {len(to_restore)} 个顶层条目:")
    for item in to_restore:
        p = snap_dir / item
        kind = "目录" if p.is_dir() else "文件"
        msgs.append(f"  ↓ {item}  ({kind})")
    if extras:
        verb = "将删除" if delete_extra else "将保留"
        msgs.append(f"{verb} {len(extras)} 个快照外的额外条目"
                    + ("" if delete_extra else "（加 --delete-extra 才删除）") + ":")
        for rel in extras:
            msgs.append(f"  {'✗' if delete_extra else '·'} {rel}")

    if not yes:
        msgs.append("")
        msgs.append("【dry-run】以上为预览，未做任何修改。")
        msgs.append(f"确认执行请加 --yes:  python scripts/snapshot.py "
                    f"--restore {snap_dir.name} --yes")
        return True, msgs

    # ---- 真实执行 ----
    # 护栏 1：先把当前状态存一份，恢复错了还能折返
    try:
        safety = snapshot("pre_restore", history_dir=history_dir)
        msgs.append(f"恢复前已存快照: {safety.name}")
    except Exception as e:                                  # noqa: BLE001
        msgs.append(f"⚠️ 恢复前快照失败: {e}")
        msgs.append("为避免不可逆操作，已中止恢复。请检查 history/ 可写后重试。")
        return False, msgs

    restored = 0
    for item in to_restore:
        src = snap_dir / item
        dst = Path(item)
        try:
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            restored += 1
        except Exception as e:                              # noqa: BLE001
            msgs.append(f"✗ 恢复失败 {item}: {e}")
    if delete_extra:
        for rel in extras:
            p = Path(rel)
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
                msgs.append(f"✗ 已删除 {rel}")
            except Exception as e:                          # noqa: BLE001
                msgs.append(f"删除失败 {rel}: {e}")

    msgs.append(f"✅ 恢复完成（{restored}/{len(to_restore)} 项）")
    msgs.append("提示：如状态不符预期，可再恢复恢复前快照 —— "
                f"python scripts/snapshot.py --restore {safety.name} --yes")
    return True, msgs


def list_snapshots(history_dir="history"):
    history_path = Path(history_dir).resolve()
    if "history" not in history_path.parts and history_path.name != "history":
        print(f"[snapshot] 安全拦截：history_dir={history_path} 不含 'history' 段")
        return
    h = history_path
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
    parser.add_argument("--dry-run", action="store_true", help="列出将清理的旧快照但不删除")
    parser.add_argument("--restore", metavar="ID", default=None,
                        help="从指定快照恢复（默认 dry-run 预览，加 --yes 执行）")
    parser.add_argument("--yes", action="store_true",
                        help="配合 --restore：确认执行（否则仅预览）")
    parser.add_argument("--delete-extra", action="store_true",
                        help="配合 --restore：同时删除快照外的额外文件（默认保留）")
    args = parser.parse_args()

    if args.list:
        list_snapshots()
        return 0
    if args.restore:
        ok, msgs = restore_snapshot(args.restore, yes=args.yes,
                                    delete_extra=args.delete_extra)
        for m in msgs:
            print(f"[snapshot] {m}" if m else "")
        return 0 if ok else 1
    if args.dry_run:
        snapshot(args.label or "manual", dry_run=True)
        return 0
    if not args.label:
        print("用法: python scripts/snapshot.py \"label\" | --list | --dry-run | "
              "--restore <ID> [--yes] [--delete-extra]")
        return 1
    snapshot(args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
