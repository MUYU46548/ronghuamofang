# -*- coding: utf-8 -*-
"""规划四态状态机（丙0）CLI 工具。

用法：
  python scripts/plan_cli.py list
  python scripts/plan_cli.py propose "标题" "摘要" [--source outline_advisor]
  python scripts/plan_cli.py select <idea_id>
  python scripts/plan_cli.py write <idea_id> <ref_path>
  python scripts/plan_cli.py drop <idea_id> [--reason "原因"]
  python scripts/plan_cli.py revive <idea_id>
  python scripts/plan_cli.py get <idea_id>
  python scripts/plan_cli.py stats
  python scripts/plan_cli.py audit [--limit 50]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.plan_state import (
    propose, select, write, drop, revive,
    list_items, get, stats,
    LABELS, VALID_STATUSES,
    PLAN_AUDIT,
)


def cmd_list(args):
    items = list_items(status=args.status)
    if not items:
        print("（无提案）")
        return
    for e in items:
        print(f"[{e['id'][:12]}] {LABELS.get(e['status'], e['status'])}  {e['title']}")
        if e.get("summary"):
            print(f"    {e['summary'][:80]}")
        if e.get("ref_path"):
            print(f"    引用: {e['ref_path']}")


def cmd_propose(args):
    iid, entry = propose(args.title, args.summary, source=args.source)
    print(f"已创建: {iid}")
    print(f"状态: {LABELS.get(entry['status'])}")
    print(f"标题: {entry['title']}")


def cmd_select(args):
    ok, msg, entry = select(args.id)
    print(msg)


def cmd_write(args):
    ok, msg, entry = write(args.id, args.ref_path)
    print(msg)


def cmd_drop(args):
    ok, msg, entry = drop(args.id, reason=args.reason)
    print(msg)


def cmd_revive(args):
    ok, msg, entry = revive(args.id, reason=args.reason)
    print(msg)


def cmd_get(args):
    entry = get(args.id)
    if entry is None:
        print(f"未找到: {args.id}")
        return
    print(json.dumps(entry, ensure_ascii=False, indent=2))


def cmd_stats(args):
    s = stats()
    for status, count in s.items():
        print(f"  {LABELS.get(status, status)}: {count}")


def cmd_audit(args):
    p = Path(PLAN_AUDIT)
    if not p.exists():
        print("（无审计日志）")
        return
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    for line in lines[-args.limit:]:
        try:
            r = json.loads(line)
            print(f"{r['at']}  {r['action']:12s}  {r.get('from',''):8s} → {r['to']:8s}  by={r.get('by','')}  {r.get('idea_id','')[:12]}")
        except json.JSONDecodeError:
            print(line)


def main():
    p = argparse.ArgumentParser(description="规划四态状态机 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="列出提案")
    sp.add_argument("--status", choices=list(VALID_STATUSES), default=None, help="按状态筛选")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("propose", help="创建提案（idea 态）")
    sp.add_argument("title")
    sp.add_argument("summary", nargs="?", default="")
    sp.add_argument("--source", default="user", help="来源标注")
    sp.set_defaults(func=cmd_propose)

    sp = sub.add_parser("select", help="选中提案")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("write", help="写进正文（需提供引用路径）")
    sp.add_argument("id")
    sp.add_argument("ref_path")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("drop", help="丢弃提案")
    sp.add_argument("id")
    sp.add_argument("--reason", default="")
    sp.set_defaults(func=cmd_drop)

    sp = sub.add_parser("revive", help="复活丢弃的提案")
    sp.add_argument("id")
    sp.add_argument("--reason", default="")
    sp.set_defaults(func=cmd_revive)

    sp = sub.add_parser("get", help="查询单条")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("stats", help="统计")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("audit", help="查看审计日志")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_audit)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
