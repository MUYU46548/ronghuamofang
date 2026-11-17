# -*- coding: utf-8 -*-
"""查成书提炼的逐章摘要（零 token、只读）。

用法：
    python scripts/book_search.py old-book --query 沈砚
    python scripts/book_search.py old-book --character 顾川
    python scripts/book_search.py old-book --chapter 3
    python scripts/book_search.py old-book            # 只列“哪些章还没摘要”
    python scripts/book_search.py old-book -q 夜巡 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.book_extract.search import search_camp            # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="成书提炼摘要检索（零 token，只读）")
    ap.add_argument("camp", help="提炼目录名（data/book_extract/ 下）或绝对路径")
    ap.add_argument("--query", "-q", default="", help="关键词（人名/地点/事件/专有名词）")
    ap.add_argument("--character", "-c", default="", help="按角色名筛（摘要的“人物”字段）")
    ap.add_argument("--chapter", "-n", type=int, default=None, help="只看第 N 章")
    ap.add_argument("--limit", "-l", type=int, default=20)
    ap.add_argument("--json", action="store_true", help="机读输出")
    a = ap.parse_args(argv)

    try:
        res = search_camp(a.camp, a.query, a.character, a.chapter, a.limit)
    except FileNotFoundError as e:
        print("[book_search] %s" % e)
        return 2

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print("[book_search] 《%s》命中 %d 章（扫描 %d 章）"
          % (res["camp"], res["matched"], res["scanned"]))
    if res.get("note"):
        print("  " + res["note"])
    for r in res["results"]:
        print("  第 %d 章 %s" % (r["seq"], r["title"]))
        for field, snip in (r.get("fields") or {}).items():
            print("    %s：%s" % (field, snip))
        if not r.get("fields"):
            who = "、".join(r.get("people") or []) or "（摘要里没有人物）"
            print("    人物：%s" % who)
    if res["missing"]:
        print("  还有 %d 章没摘要：" % len(res["missing"]))
        for m in res["missing"][:10]:
            print("    第 %s 章 %s —— %s" % (m.get("seq"), m.get("title"), m.get("why")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
