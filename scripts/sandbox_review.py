# -*- coding: utf-8 -*-
"""沙盒产物审核队列 CLI（2026-09-21）。

沙盒（`data/state/obsidian_sandbox/`，可配置到你的 Obsidian 库内目录）是
「准备粘贴进 vault」的中转区。`obsidian_bridge.write_sandbox` /
`outline_export` 等写入时会自动登记为**待审**；本 CLI 用来审它们。

用法：
  python scripts/sandbox_review.py --list                  # 全部产物 + 状态
  python scripts/sandbox_review.py --queue                 # 只看待审
  python scripts/sandbox_review.py --approve <路径>        # 通过（可粘贴进 vault）
  python scripts/sandbox_review.py --reject <路径> --note "第二节与设定冲突"
  python scripts/sandbox_review.py --orphans               # 沙盒里未登记的文件
  python scripts/sandbox_review.py --json                  # 机器可读

`<路径>` 是**相对沙盒目录**的路径（`--list` 打印出来的就是它）。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from obsidian_bridge import get_sandbox_dir          # noqa: E402
from utils import sandbox_review as srv               # noqa: E402


def _fmt(entry):
    label = srv.STATUS_LABELS.get(entry.get("status"), entry.get("status"))
    parts = [f"[{label}] {entry.get('path')}"]
    if entry.get("kind"):
        parts.append(f"类型={entry['kind']}")
    if entry.get("source"):
        parts.append(f"来源={entry['source']}")
    parts.append(f"更新={entry.get('updated_at') or entry.get('created_at') or '-'}")
    if entry.get("reviewed_at"):
        parts.append(f"审于={entry['reviewed_at']}")
    if entry.get("note"):
        parts.append(f"备注={entry['note']}")
    return "  " + "  ".join(parts)


def main():
    ap = argparse.ArgumentParser(description="沙盒产物审核队列")
    ap.add_argument("--list", action="store_true", help="列出全部产物与状态")
    ap.add_argument("--queue", action="store_true", help="只列待审")
    ap.add_argument("--approve", default=None, metavar="路径", help="标记为已通过")
    ap.add_argument("--reject", default=None, metavar="路径", help="标记为已驳回")
    ap.add_argument("--reset", default=None, metavar="路径", help="退回待审")
    ap.add_argument("--note", default="", help="审核备注（随 --approve/--reject 记录）")
    ap.add_argument("--orphans", action="store_true", help="列出沙盒中未登记的文件")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    sandbox = get_sandbox_dir()

    if args.approve or args.reject or args.reset:
        rel = args.approve or args.reject or args.reset
        status = (srv.APPROVED if args.approve
                  else srv.REJECTED if args.reject else srv.PENDING)
        ok, msg = srv.set_status(rel, status, note=args.note)
        if not ok:
            print(f"[sandbox_review] {msg}")
            return 1
        print(f"[sandbox_review] {msg}")
        if status == srv.APPROVED:
            print(f"[sandbox_review] 现在可以粘贴进 vault：{Path(sandbox) / rel}")
        return 0

    if args.orphans:
        found = srv.orphans(sandbox)
        if args.json:
            print(json.dumps({"sandbox_dir": str(sandbox), "orphans": found},
                             ensure_ascii=False))
            return 0
        print(f"[sandbox_review] 沙盒目录: {sandbox}")
        if not found:
            print("  无未登记文件 ✅")
            return 0
        print(f"  ⚠ 有 {len(found)} 个文件未登记审核状态（不会出现在审核队列里）:")
        for f in found:
            print(f"    - {f}")
        print("  这些文件是手工放进沙盒的，或换过沙盒目录。"
              "要纳入审核请重新用 write_sandbox / 导出命令写入。")
        return 0

    # 默认：待审队列
    status = None if args.list else srv.PENDING
    items = srv.list_items(status)
    st = srv.stats()
    orph = srv.orphans(sandbox)

    if args.json:
        print(json.dumps({
            "sandbox_dir": str(sandbox),
            "items": items,
            "stats": st,
            "orphans": orph,
        }, ensure_ascii=False))
        return 0

    print(f"[sandbox_review] 沙盒目录: {sandbox}")
    print(f"[sandbox_review] 状态统计: "
          f"待审 {st[srv.PENDING]} / 已通过 {st[srv.APPROVED]} / 已驳回 {st[srv.REJECTED]}"
          + (f"；另有 {len(orph)} 个未登记文件（--orphans 查看）" if orph else ""))
    if not items:
        print("  无待审产物 ✅" if status else "  尚无产物")
        return 0
    print(f"  {('全部产物' if args.list else '待审队列')}:")
    for e in items:
        print(_fmt(e))
    if not args.list:
        print("  审核：--approve <路径> / --reject <路径> --note \"原因\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
