# -*- coding: utf-8 -*-
"""质量度量 CLI：节级字数 / 文风指标 / 口径冲突 / 轮次达标（R1/R2/R4/R5/R8）。

零 token。**只读正文**，唯一的写动作是 `--annotate`（写批注库）。
等价端点见 `scripts/nf_api_domains/metrics.py`（界面走那条）。

用法：

    python scripts/quality_metrics.py 6                  # 第 6 章：节级 + 指标 + 当前轮
    python scripts/quality_metrics.py 6 --annotate        # 把命中项变成批注
    python scripts/quality_metrics.py --book             # 全书指标 + 未达标章号
    python scripts/quality_metrics.py --round 润色        # 用指定轮次的判据表
    python scripts/quality_metrics.py --glossary          # 全书口径冲突
    python scripts/quality_metrics.py --glossary 8        # 只查第 8 章
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import glossary as gl                # noqa: E402
from utils import prose_metrics as pm           # noqa: E402
from utils import rounds as rd                  # noqa: E402
from utils import sections as sec               # noqa: E402


def _body(n, root="."):
    for layer in ("refined", "checked", "raw"):
        p = Path(root) / "data" / "chapters" / layer / ("%02d.md" % int(n))
        if p.exists():
            return p.read_text(encoding="utf-8"), layer
    return "", ""


def _outline(n, root="."):
    p = Path(root) / "data" / "outline" / "chapters" / ("%02d.md" % int(n))
    return p.read_text(encoding="utf-8") if p.exists() else ""


def show_chapter(n, round_name=None, annotate=False, as_json=False):
    text, layer = _body(n)
    if not text:
        print("第 %d 章还没有正文（raw/checked/refined 都是空的）" % n)
        return 1
    cfg = {}
    sec_rep = sec.measure_and_save(n, text, _outline(n), cfg)
    met_rep = pm.measure_and_save(n, text, cfg)
    rnd = rd.evaluate(n, round_name)

    if as_json:
        print(json.dumps({"sections": sec_rep, "metrics": met_rep, "round": rnd},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 66)
        print("第 %d 章 · %s 层 · %d 汉字" % (n, layer, met_rep["hanzi"]))
        print("-" * 66)
        if sec_rep.get("declared"):
            print("节级（%d 节）：%s" % (sec_rep.get("section_count"), sec_rep.get("why")))
            for s in sec_rep.get("sections") or []:
                flag = "OK" if s.get("ok") else "短"
                tgt = s.get("target")
                print("  节 %s｜%s  %s 汉字 / 目标 %s（%s）  [%s]"
                      % (s.get("n"), s.get("name") or "—", s.get("hanzi"),
                         tgt if tgt else "—",
                         ("%.0f%%" % (s["ratio"] * 100)) if s.get("ratio") else "—", flag))
        else:
            print("节级：这一章大纲没有节列表 —— 按整章走（不是失败）")
        print("-" * 66)
        print("文风：" + pm.summary_line(met_rep))
        g = met_rep.get("gate") or {}
        if g.get("fails"):
            for f in g["fails"]:
                print("  过线不了：%s = %s（红线 %s）" % (f["code"], f["value"], f["limit"]))
        else:
            print("  四项都在红线内")
        print("-" * 66)
        print("轮次“%s”（目标：%s）：%s"
              % (rnd.get("round"), rnd.get("goal"), "达标" if rnd.get("ok") else "未达标"))
        for c in rnd.get("checks") or []:
            print("  %s %-28s 实测 %-10s 判据 %s"
                  % ("✓" if c["ok"] else "✗", c["key"], c["actual"], c["expr"]))
        if not rnd.get("ok"):
            print("  差的项：%s" % "、".join(rnd.get("gaps") or []))

    if annotate:
        made = pm.to_annotations(n, text)
        print("-" * 66)
        print("已把 %d 处结构问题变成批注（正文没动）。到“精修 → 批注”点一条即可改。"
              % len(made))
        for m in made[:10]:
            print("  第 %d 段 [%s] %s" % (m["para_index"] + 1, m["code"], m["why"][:40]))
    return 0 if (sec_rep.get("ok") and rnd.get("ok")) else 2


def show_book(round_name=None):
    res = rd.evaluate_book(round_name)
    print("=" * 66)
    print("全书 · 轮次“%s”：%d 章达标，未达标 %s"
          % (res["round"], res["passed"], "、".join(str(x) for x in res["failed"]) or "无"))
    for r in res["chapters"]:
        if r.get("ok"):
            continue
        print("  第 %s 章：差 %s" % (r.get("chapter"), "、".join(r.get("gaps") or []) or r.get("error")))
    return 0 if res["ok"] else 2


def show_glossary(n=None):
    data = gl.load()
    if not data.get("terms") and not data.get("calendar"):
        print("还没有口径表：%s" % gl.path().as_posix())
        print("写一条试试：")
        print("  terms:")
        print("    - term: 工牌")
        print("      value: \"32→44→47\"")
        print("      anchor_chapter: 1")
        print("      status: locked")
        return 0
    if n:
        text, _layer = _body(n)
        if not text:
            print("第 %d 章还没有正文" % n)
            return 1
        rep = gl.check(text, n, data)
        print("第 %d 章口径检查：%d 处冲突" % (n, rep["count"]))
        for f in rep["findings"]:
            print("  第 %s 段 [%s] %s" % ((f.get("para_index") or 0) + 1, f.get("kind"), f.get("why")))
        return 0 if not rep["count"] else 2
    rep = gl.scan_book(None, ".", data)
    print("全书口径检查：%d 处冲突" % rep["total"])
    for term, slot in (rep.get("by_term") or {}).items():
        if slot.get("conflicts"):
            print("  ✗ " + slot.get("summary", term))
        else:
            print("  ✓ “%s”全书一致：%s" % (term, "／".join(slot["values"].keys())))
    for n_, r in sorted(rep["chapters"].items()):
        for f in r["findings"]:
            if f.get("kind") == "calendar_mix":
                print("  第 %s 章 [历法] %s" % (n_, f.get("why")))
    return 0 if not rep["total"] else 2


def main():
    ap = argparse.ArgumentParser(description="节级/文风/口径/轮次 体检（零 token）")
    ap.add_argument("chapter", nargs="?", type=int, help="章号（不给就看书）")
    ap.add_argument("--book", action="store_true", help="按当前轮次看全书")
    ap.add_argument("--round", default=None, help="用指定轮次的判据表（默认当前轮）")
    ap.add_argument("--annotate", action="store_true", help="把文风命中项变成段落批注")
    ap.add_argument("--glossary", nargs="?", const="", default=None,
                    help="口径检查：不带值查全书，带章号只查那一章")
    ap.add_argument("--json", action="store_true", help="机器可读输出（单章）")
    args = ap.parse_args()

    if args.glossary is not None:
        n = int(args.glossary) if str(args.glossary).strip() else None
        return show_glossary(n)
    if args.book or not args.chapter:
        return show_book(args.round)
    return show_chapter(args.chapter, args.round, args.annotate, args.json)


if __name__ == "__main__":
    sys.exit(main())
