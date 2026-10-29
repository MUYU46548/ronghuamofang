# -*- coding: utf-8 -*-
"""判定引擎 CLI（零 token）：回测 / 报告 / 勾事件 / 逐句裁定 / 口径。

    python scripts/judge.py run                      # 对 raw 全量回测（写 records.json）
    python scripts/judge.py report                   # 打最近一次回测的总账
    python scripts/judge.py chapter 3                # 单章缺陷单
    python scripts/judge.py destroy 3                # 三稿对照（raw→checked→refined）
    python scripts/judge.py events 3 --checked 1,2 --class 1=关系 --evidence "第 12 段"
    python scripts/judge.py sentence 3 --paragraph 5 --text "…" --feeling 打中了 --reason "…"
    python scripts/judge.py caliber                  # 看口径与版本
    python scripts/judge.py caliber --set J10=0.25   # 改口径（产新版本）
    python scripts/judge.py export                   # 导出清单（output/judge_report.md）

**零 token**：本文件不 import 任何模型客户端；断网可跑。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.interp_guard import ensure_working_python, missing_yaml_hint  # noqa: E402

if not ensure_working_python("scripts/judge.py"):
    sys.stderr.write("[judge] 启动被解释器守卫拦下：" + missing_yaml_hint() + "\n")
    raise SystemExit(1)

from utils.judge import backtest as B                       # noqa: E402
from utils.judge import caliber as C                        # noqa: E402
from utils.judge import destroy as D                        # noqa: E402
from utils.judge import records as R                        # noqa: E402
from utils.judge import rules as RU                         # noqa: E402
from utils.judge import verdict as V                        # noqa: E402


def _root(args):
    return Path(args.root).resolve() if getattr(args, "root", "") else Path(".").resolve()


def _book_name(root):
    try:
        from utils.project_config import get_project_config
        cfg = get_project_config() or {}
        return ((cfg.get("book") or {}).get("name") or "").strip()
    except Exception:                                          # noqa: BLE001
        return ""


def cmd_run(args):
    rep = B.run(_root(args), book_name=_book_name(_root(args)))
    if not rep.get("ok"):
        print("[judge] %s" % rep.get("error"))
        return 1
    s = rep["summary"]
    print("[judge] 回测完成：口径 v%s（%s） · %s"
          % (rep["caliber"]["version"], rep["caliber"]["fingerprint"], rep["generated_at"]))
    print("[judge] %d 章：%d 可以开始逐字改 · %d 要定点重写 · %d 要重写本章；全书 %d 处要改"
          % (s["total"], s["ok"], s["spot"], s["rewrite"], s["defects"]))
    print("[judge] %s / %s" % (rep["notices"][0], rep["notices"][1]))
    return 0


def cmd_report(args):
    doc = R.load(_root(args))
    run = R.latest_run(doc)
    if not run:
        print("[judge] 还没有回测记录 —— 先跑 python scripts/judge.py run")
        return 1
    s = run["summary"]
    print("最近一次回测：%s（口径 v%s）" % (run.get("at"), run.get("caliber_version")))
    print("%d 章：%d 可以开始逐字改 · %d 要定点重写 · %d 要重写本章 · %d 未判；%d 处要改"
          % (s["total"], s["ok"], s["spot"], s["rewrite"], s["undecided"], s["defects"]))
    for c in run["chapters"]:
        print("  第 %2d 章  %-12s %s（%s 处，段占比 %s）"
              % (c["chapter"], c.get("status") or "未判", c.get("verdict") or "-",
                 c.get("defect_count"), c.get("ratio")))
    return 0


def cmd_chapter(args):
    root = _root(args)
    cal_doc = C.load(root)
    ok, why = C.require_ready(cal_doc)
    if not ok:
        print("[judge] %s" % why)
        return 1
    vals = C.values(cal_doc)
    n = int(args.chapter)
    body = (root / B.RAW_DIR / ("%02d.md" % n)).read_text(encoding="utf-8")
    outline_p = root / B.OUTLINE_DIR / ("%02d.md" % n)
    outline = outline_p.read_text(encoding="utf-8") if outline_p.exists() else ""
    res = RU.analyze_chapter(body, outline, n, vals)
    author = R.get_events(root, n)
    v = V.decide(res, None, author, vals)
    print("第 %d 章：%s ｜ %s" % (n, v.get("status"), v.get("verdict") or "未判"))
    for r in v.get("reasons") or []:
        print("  依据 [%s] %s" % (r.get("rule_id"), r.get("text")))
    for d in res["defects"]:
        print("  第 %d 段「%s」（%s ×%d）%s"
              % (d["paragraph_index"], d["sentence"][:26], d["rule_name"],
                 d["count"], d["why"][:40]))
    return 0


def cmd_destroy(args):
    root = _root(args)
    n = int(args.chapter)
    body = (root / B.RAW_DIR / ("%02d.md" % n)).read_text(encoding="utf-8")
    cp = root / B.CHECKED_DIR / ("%02d.md" % n)
    rp = root / B.REFINED_DIR / ("%02d.md" % n)
    d = D.compare(body, cp.read_text(encoding="utf-8") if cp.exists() else None,
                  rp.read_text(encoding="utf-8") if rp.exists() else None,
                  C.values(C.load(root)))
    if not d.get("ok"):
        print("[judge] %s" % d.get("reason"))
        return 1
    print("第 %d 章：raw %s → checked %s → refined %s"
          % (n, d["words"]["raw"], d["words"]["checked"], d["words"]["refined"]))
    print("缩减：%s" % d.get("shrink"))
    print("判定：%s（阈值 %.0f%%）%s"
          % ("销毁" if d["destroyed"] else "未超阈值", d["threshold"] * 100,
             "；两次压缩，判定不可信" if d["compressed_twice"] else ""))
    for x in d["deleted"][:12]:
        print("  删 第 %d 段「%s」" % (x["paragraph_index"], x["sentence"][:30]))
    return 0


def cmd_events(args):
    root = _root(args)
    checked = [int(x) for x in (args.checked or "").replace("，", ",").split(",") if x.strip()]
    classes = {}
    for pair in (args.cls or []):
        if "=" in pair:
            k, v = pair.split("=", 1)
            classes[str(int(k) - 1)] = v.strip()
    ev = R.set_events(root, int(args.chapter), checked, classes, args.evidence or "")
    print("[judge] 已记：第 %s 章 %d 件事（%s）" % (args.chapter, ev["events"],
                                                   ev["chosen"] or "未分类"))
    return 0


def cmd_sentence(args):
    root = _root(args)
    ok, payload = R.set_sentence(root, int(args.chapter), int(args.paragraph),
                                 args.text, args.feeling, args.reason)
    print("[judge] %s" % ("已记裁定" if ok else payload.get("error")))
    return 0 if ok else 1


def cmd_caliber(args):
    root = _root(args)
    doc = C.load(root)
    if not doc:
        print("[judge] 口径文件读不出来")
        return 1
    if args.set:
        changes = {}
        for pair in args.set:
            if "=" not in pair:
                print("[judge] --set 要写成 J10=0.25")
                return 1
            k, v = pair.split("=", 1)
            try:
                v = float(v) if "." in v else int(v)
            except ValueError:
                pass
            changes[k.strip()] = v
        ok, d = C.update(root, changes, by="CLI")
        if not ok:
            print("[judge] %s" % d.get("error"))
            return 1
        print("[judge] %s" % (d.get("message") or "已改成 v%s" % d.get("version")))
        doc = C.load(root)
    print("现在生效：第 %s 版（%s）%s"
          % (doc["version"], doc["fingerprint"],
             "" if doc.get("calibrated") else " · 系数未实测标定"))
    for c in C.describe(doc):
        print("  %-4s %-22s %-8s %-14s ← %s"
              % (c["id"], c["name"], c["kind"], str(c["value"])[:14], c["consumer_name"]))
    bad = C.empty_criteria(doc)
    if bad:
        print("  ⚠ 空判据：%s" % "、".join(b["id"] for b in bad))
    return 0


def cmd_export(args):
    root = _root(args)
    rep = B.run(root, book_name=_book_name(root)) if args.fresh else None
    if rep is None:
        doc = R.load(root)
        run = R.latest_run(doc)
        if not run:
            print("[judge] 还没有回测记录")
            return 1
        print("[judge] 用最近一次记录导出：%s" % run.get("at"))
        return 0
    out = B.export_markdown(rep, root)
    print("[judge] 已导出：%s" % out)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="判定引擎（零 token）：回测 / 报告 / 勾选 / 裁定 / 口径")
    ap.add_argument("--root", default="", help="项目根（默认当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="全量回测（只对 raw，写 records.json）")
    sub.add_parser("report", help="最近一次回测的总账")

    p_ch = sub.add_parser("chapter", help="单章缺陷单")
    p_ch.add_argument("chapter", type=int)

    p_de = sub.add_parser("destroy", help="三稿对照（raw→checked→refined）")
    p_de.add_argument("chapter", type=int)

    p_ev = sub.add_parser("events", help="作者勾选：这一章发生了几件事")
    p_ev.add_argument("chapter", type=int)
    p_ev.add_argument("--checked", default="", help="勾中的事件序号（逗号分隔，从 1 起）")
    p_ev.add_argument("--class", dest="cls", action="append", default=[],
                      help="事件分类，如 1=关系（四类：人物处境/信息占有/关系/目标）")
    p_ev.add_argument("--evidence", default="", help="证据句（可选）")

    p_se = sub.add_parser("sentence", help="逐句裁定（打中了/没打中/拿不准 + 理由）")
    p_se.add_argument("chapter", type=int)
    p_se.add_argument("--paragraph", type=int, required=True)
    p_se.add_argument("--text", required=True)
    p_se.add_argument("--feeling", required=True, choices=["打中了", "没打中", "拿不准"])
    p_se.add_argument("--reason", required=True)

    p_ca = sub.add_parser("caliber", help="看/改口径")
    p_ca.add_argument("--set", action="append", default=[], help="如 J10=0.25")

    p_ex = sub.add_parser("export", help="导出清单到 output/judge_report.md")
    p_ex.add_argument("--fresh", action="store_true", help="先重跑一次再导出")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {
        "run": cmd_run, "report": cmd_report, "chapter": cmd_chapter,
        "destroy": cmd_destroy, "events": cmd_events, "sentence": cmd_sentence,
        "caliber": cmd_caliber, "export": cmd_export,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
