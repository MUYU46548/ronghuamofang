# -*- coding: utf-8 -*-
"""本书成本账 CLI（实现与理由见 `utils/book_cost.py`）。

    python scripts/book_cost.py                        # 看当前这本书
    python scripts/book_cost.py auto                   # 只刷新机器侧读数
    python scripts/book_cost.py record --yuan 45 --note "Hermes 4.x，12 章"

为什么 Agent 侧要手填：外部 Agent（Hermes/WorkBuddy）调模型的钱记在它那边，
墨坊读不到。不填就显示“未上报”，不按字数瞎折。
"""
import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import book_cost  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="本书成本账（机器侧自动 + Agent 侧自报）")
    ap.add_argument("cmd", nargs="?", default="show", choices=["show", "auto", "record"])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--yuan", type=float, help="Agent 侧花费（元）")
    ap.add_argument("--note", default="", help="备注（用哪个 Agent、跑了几章）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()
    root = Path(args.root)

    if args.cmd == "record":
        if args.yuan is None:
            print("record 需要 --yuan（Agent 侧花费，元）")
            return 2
        rec = book_cost.upsert(root, agent_yuan=args.yuan, note=args.note, source="manual")
        print("已记账：" + book_cost.summary_line(rec))
        print("文件：" + str(root / book_cost.REL))
        return 0

    if args.cmd == "auto":
        rec = book_cost.auto_record(root)
        print("已刷新：" + book_cost.summary_line(rec))
        return 0

    data = book_cost.load(root)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not data["books"]:
        print("还没有任何记录。跑一次流水线（或 `python scripts/book_cost.py auto`）后再看。")
        return 0
    print("本书成本账（%s）" % (root / book_cost.REL))
    for name, rec in data["books"].items():
        print("  " + book_cost.summary_line(rec))
        if rec.get("agent_note"):
            print("    备注：%s" % rec["agent_note"])
        print("    合计：¥%.2f" % book_cost.total_yuan(rec))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
