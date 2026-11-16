# -*- coding: utf-8 -*-
"""外派回执 CLI（R6）：执行方生成回执，主控复检（以脚本为准）。

    # 执行方（子任务）写完产物后：
    python scripts/agent_receipt.py --emit <run_id> --outputs data/chapters/raw/03.md \
        --checks verify_chapter.txt --note "补写了第 3 节"

    # 主控 / 作者核这一趟到底算不算完成：
    python scripts/agent_receipt.py --verify <run_id>
    python scripts/agent_receipt.py --verify <run_id> --expect data/chapters/raw/03.md
    python scripts/agent_receipt.py --list            # 最近派发的复检结果一览

规矩只有一条：**模型自述与脚本实测不一致时，系统站在脚本一边。**
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import agent_receipt as ar           # noqa: E402


def _read_checks(paths):
    out = []
    for p in paths or []:
        try:
            text = Path(p).read_text(encoding="utf-8")
        except OSError as e:
            print("体检输出读不到：%s（%s）" % (p, e))
            continue
        out.append({"name": Path(p).stem, "output": text.strip()})
    return out


def _print_verify(res):
    ok = res.get("ok")
    print("=" * 66)
    print("run %s：%s" % (res.get("run_id"), "通过" if ok else "不通过（%s）" % res.get("verdict")))
    if not res.get("receipt_present"):
        print("  没有回执 —— 这一趟没有可核对的东西，不算完成。")
        return 2
    for o in res.get("outputs") or []:
        print("  %s  %s 汉字  sha %s…"
              % (o.get("path"), o.get("hanzi"),
                 str(o.get("sha256") or "")[:12] or "（盘上没有）"))
    for m in res.get("mismatches") or []:
        print("  ✗ %s：%s" % (m.get("path") or m.get("field"), m.get("why")))
    if ok:
        print("  自报与实测一致（判据来源：脚本复算）")
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(description="外派回执：生成 / 复检")
    ap.add_argument("--emit", metavar="RUN_ID", help="生成回执（执行方用）")
    ap.add_argument("--outputs", nargs="*", default=[], help="产物路径（相对项目根）")
    ap.add_argument("--checks", nargs="*", default=[], help="体检脚本输出文件（原始输出）")
    ap.add_argument("--note", default="", help="一句话说明")
    ap.add_argument("--verify", metavar="RUN_ID", help="复检（主控用）")
    ap.add_argument("--expect", nargs="*", default=[], help="额外要求存在的产物")
    ap.add_argument("--list", action="store_true", help="最近派发的复检结果")
    ap.add_argument("--json", action="store_true", help="机器可读")
    args = ap.parse_args()

    if args.emit:
        p = ar.build_receipt(args.emit, args.outputs, _read_checks(args.checks),
                             notes=args.note)
        print("回执已写：%s" % p.as_posix())
        return 0
    if args.verify:
        res = ar.verify(args.verify, expect_outputs=args.expect)
        if args.json:
            import json
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return _print_verify(res)
    if args.list:
        import nf_agent_dispatch as disp
        runs = disp.list_agent_runs(30)
        bad = 0
        for r in runs:
            rid = r.get("run_id")
            if not rid:
                continue
            res = ar.verify(rid)
            bad += 0 if res.get("ok") else 1
            print("%s  %-28s  %s" % (rid, str(r.get("title") or "")[:28],
                                     "ok" if res.get("ok") else res.get("verdict")))
        print("-" * 66)
        print("共 %d 趟，%d 趟不可信（没有回执 / 自报与实测不符）" % (len(runs), bad))
        return 0 if not bad else 2
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
